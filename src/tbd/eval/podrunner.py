"""Pod runner: run a queue of experiment definitions on one GPU machine, saving results as it goes.

    python -m tbd.eval.podrunner experiments.d/a.yaml experiments.d/b.yaml \\
        --sync /workspace/results --max-lifetime 6h --serve-ollama
    python -m tbd.eval.podrunner experiments.d/a.yaml --sync /tmp/r --dry-run

This is the entrypoint of the pod-runner image (docker/worker.Dockerfile, target pod-runner;
docs/design/execution.md, "Pod runner"). It runs where the model runs: Ollama
in the same container, so there is no laptop and no network hop involved.

1. Every definition is loaded and checked before anything starts. Each model
   must use the ollama provider, since the pod serves its models itself.
2. With --serve-ollama it starts `ollama serve` and waits until it answers.
   It then pulls every model the definitions name.
3. It records the machine for case0: TBD_GPU (nvidia-smi's GPU name; unset
   if there is no GPU) and TBD_HOST_ID (RUNPOD_POD_ID, else the hostname).
   case0 copies both into each job's config.json as `compute`.
4. It runs the definitions in the order given, each with tbd.eval.experiment
   as it is (parallel, gate and resume unchanged).
5. After every job, and after every definition, it copies experiments/<name>/
   to the sync target, so results survive the machine stopping. Targets:
   --sync DIR (a local or mounted directory), or --sync-command TEMPLATE (any
   copy program, e.g. `aws s3 sync {src} s3://bucket/experiments/{name}`).
   --no-sync must be given to run without one. Nothing at the target is
   deleted.
6. It stops when the queue is empty, or when --max-lifetime runs out. At
   that point the running jobs get SIGTERM and mark themselves "interrupted",
   and no further job starts. If they haven't stopped after --grace, it
   syncs and exits anyway.
7. On the way out, always, it syncs once more and then runs --on-exit (e.g. a
   command that stops the pod). Exiting alone doesn't stop a pod's billing:
   RunPod restarts the container (docs/running.md).

Settings can also come from the environment, which suits a pod template:
TBD_DEFINITIONS (space-separated paths, when none are given as arguments),
TBD_SYNC, TBD_SYNC_COMMAND, TBD_MAX_LIFETIME, TBD_ON_EXIT.

Exit status: 0 every definition finished with every job done, 1 something not
done (failed, incomplete, or a sync that failed), 124 the lifetime ran out,
130 stopped by a signal.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from tbd.eval import experiment
from tbd.paths import ROOT

DEFAULT_LIFETIME = "12h"
DEFAULT_GRACE = "10m"
EXIT_LIFETIME = 124
EXIT_SIGNAL = 130


def log(msg: str) -> None:
    print(f"[podrunner] {msg}", flush=True)


def parse_duration(text: str) -> float:
    """Seconds, from "3600", "90s", "45m" or "6h"."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*", str(text))
    if not m:
        raise ValueError(f"not a duration: {text!r} (e.g. 3600, 45m, 6h)")
    return float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]


# ---------------------------------------------------------------- the machine

def gpu_name(run=subprocess.run) -> str | None:
    """nvidia-smi's name for each GPU, joined with ", "; None if there is none."""
    try:
        r = run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    names = [line.strip() for line in r.stdout.splitlines() if line.strip()]
    return ", ".join(names) if r.returncode == 0 and names else None


def host_id(env=os.environ) -> str:
    """RunPod sets RUNPOD_POD_ID in a pod; elsewhere, the hostname."""
    return env.get("RUNPOD_POD_ID") or socket.gethostname()


# ---------------------------------------------------------------- sync targets

class LocalDirSink:
    """Copies experiments/<name>/ to DEST/<name>/: new and changed files only
    (by size and modification time), each written beside its final name and
    then renamed, so a reader never sees half a file. Deletes nothing."""

    def __init__(self, dest: Path):
        self.dest = Path(dest)

    def __str__(self):
        return f"directory {self.dest}"

    def sync(self, exp_dir: Path) -> None:
        target = self.dest / exp_dir.name
        for src in sorted(exp_dir.rglob("*")):
            dst = target / src.relative_to(exp_dir)
            if src.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
                continue
            st = src.stat()
            try:
                dt = dst.stat()
                if dt.st_size == st.st_size and int(dt.st_mtime) == int(st.st_mtime):
                    continue
            except FileNotFoundError:
                pass
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(f".{dst.name}.podrunner-tmp")
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)


class CommandSink:
    """Runs a copy program for the experiment folder. The template is split
    like a shell command line (no shell runs it); {src} becomes the folder
    and {name} the experiment name. For S3-compatible storage, e.g.
    `aws s3 sync {src} s3://BUCKET/experiments/{name}` or
    `rclone copy {src} r2:BUCKET/experiments/{name}` (neither tool is in the
    image; add the one chosen, and its credentials as pod secrets)."""

    def __init__(self, template: str):
        self.argv = shlex.split(template)
        if not self.argv or not any("{src}" in a for a in self.argv):
            raise ValueError(f"--sync-command needs {{src}} in it: {template!r}")

    def __str__(self):
        return f"command {shlex.join(self.argv)}"

    def sync(self, exp_dir: Path) -> None:
        argv = [a.format(src=str(exp_dir), name=exp_dir.name) for a in self.argv]
        subprocess.run(argv, check=True, stdin=subprocess.DEVNULL)


class NoSink:
    def __str__(self):
        return "none (--no-sync): results stay on this machine only"

    def sync(self, exp_dir: Path) -> None:
        pass


def make_sink(sync: str | None, sync_command: str | None, no_sync: bool):
    chosen = [x for x in (sync, sync_command, no_sync or None) if x]
    if len(chosen) != 1:
        raise ValueError("pick one: --sync DIR, --sync-command TEMPLATE or --no-sync")
    if no_sync:
        return NoSink()
    if sync_command:
        return CommandSink(sync_command)
    if re.match(r"^[a-z0-9]+://", sync) and not sync.startswith("file://"):
        raise ValueError(f"--sync {sync}: only a directory is built in; for object storage use "
                         "--sync-command, e.g. 'aws s3 sync {src} s3://BUCKET/experiments/{name}'")
    return LocalDirSink(Path(sync.removeprefix("file://")))


class Syncer:
    """A sink used from several threads (parallel jobs). A failed copy is logged
    and remembered, not raised: the run goes on, and the next sync retries it."""

    def __init__(self, sink):
        self.sink, self.lock = sink, threading.Lock()
        self.failed: dict[str, str] = {}   # experiment name -> last error

    def __call__(self, exp_dir: Path) -> bool:
        if not exp_dir.exists():
            return True
        with self.lock:
            try:
                self.sink.sync(exp_dir)
            except Exception as e:   # noqa: BLE001 -- any failure: keep running, retry later
                self.failed[exp_dir.name] = f"{type(e).__name__}: {e}"
                log(f"sync of {exp_dir.name} failed: {self.failed[exp_dir.name]}")
                return False
            self.failed.pop(exp_dir.name, None)
            return True


# ---------------------------------------------------------------- Ollama

class OllamaServer:
    """`ollama serve` in the background, for the life of the runner."""

    def __init__(self):
        self.proc = None

    def start(self, timeout_s: float = 120) -> None:
        from tbd.eval.models import OLLAMA
        self.proc = subprocess.Popen(["ollama", "serve"], stdin=subprocess.DEVNULL)
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=5):
                    return
            except OSError as e:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"ollama serve exited ({self.proc.returncode})") from e
                if time.monotonic() > deadline:
                    raise RuntimeError(f"Ollama at {OLLAMA} not answering after {timeout_s:.0f}s") from e
                time.sleep(1)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def pull_models(models: list[str], stopped=lambda: False) -> None:
    for model in models:
        if stopped():
            return
        log(f"pulling {model}")
        subprocess.run(["ollama", "pull", model], check=True, stdin=subprocess.DEVNULL)


# ---------------------------------------------------------------- the queue

@dataclass
class Item:
    path: Path
    name: str          # the experiment, i.e. the folder under --out
    models: list[str]  # Ollama models it uses, in order


def plan(paths: list[Path]) -> list[Item]:
    """Load and check every definition before anything runs."""
    items = []
    for path in paths:
        defn, jobs = experiment.load(path)
        other = sorted({f"{j.model} ({j.provider})" for j in jobs if j.provider != "ollama"})
        if other:
            raise experiment.DefinitionError(
                f"{path}: the pod serves its own models through Ollama; these use another "
                f"provider: {', '.join(other)}")
        items.append(Item(Path(path), defn["experiment"], list(dict.fromkeys(j.model for j in jobs))))
    return items


def run_definition(path: Path, out: Path, after_job) -> int:
    """tbd.eval.experiment on one definition, with after_job() after every job."""
    def job(argv, stop, label=None):
        try:
            return experiment.subprocess_job(argv, stop, label=label)
        finally:
            after_job()
    return experiment.main([str(path), "--out", str(out)], run_job=job,
                           preflight=experiment.ollama_check)


class Lifetime:
    """Interrupts the run when the time is up, and gives up waiting for it
    `grace` seconds later."""

    def __init__(self, seconds: float, grace: float, interrupt, give_up):
        self.seconds, self.grace = seconds, grace
        self.interrupt, self.give_up = interrupt, give_up
        self.expired = threading.Event()
        self.timers: list[threading.Timer] = []

    def start(self) -> None:
        self._after(self.seconds, self._expire)

    def _after(self, seconds, fn):
        t = threading.Timer(seconds, fn)
        t.daemon = True
        t.start()
        self.timers.append(t)

    def _expire(self):
        log(f"maximum lifetime ({self.seconds:.0f}s) reached: stopping the running jobs")
        self.expired.set()
        self._after(self.grace, self.give_up)
        self.interrupt()

    def cancel(self) -> None:
        for t in self.timers:
            t.cancel()


def run_queue(items: list[Item], out: Path, sync: Syncer, run_def, stopped) -> list[tuple]:
    """Each definition in order, syncing after each of its jobs and after it.
    Returns [(item, exit code or None if not run)]."""
    results = []
    for item in items:
        if stopped():
            results.append((item, None))
            continue
        log(f"definition {item.path} -> {out / item.name}")
        exp_dir = out / item.name
        try:
            code = run_def(item.path, out, lambda d=exp_dir: sync(d))
        except SystemExit as e:   # argparse errors inside experiment.main
            code = e.code if isinstance(e.code, int) else 2
        sync(exp_dir)
        log(f"definition {item.path}: exit {code}")
        results.append((item, code))
        if code == EXIT_SIGNAL:
            break
    for item in items[len(results):]:
        results.append((item, None))
    return results


class Finish:
    """The last steps, exactly once, whichever way the runner ends: sync every
    experiment, stop Ollama, run --on-exit."""

    def __init__(self, items, out, sync, server, on_exit, run_shell=subprocess.run):
        self.items, self.out, self.sync, self.server = items, out, sync, server
        self.on_exit, self.run_shell = on_exit, run_shell
        self.lock, self.done = threading.Lock(), False

    def __call__(self) -> bool:
        """True if the final sync of every experiment succeeded."""
        with self.lock:
            if self.done:
                return True
            self.done = True
            ok = all([self.sync(self.out / item.name) for item in self.items])
            if self.server:
                self.server.stop()
            if self.on_exit:
                log(f"on exit: {self.on_exit}")
                try:
                    self.run_shell(["sh", "-c", self.on_exit], stdin=subprocess.DEVNULL)
                except OSError as e:
                    log(f"on-exit command failed: {e}")
            return ok


def build_parser() -> argparse.ArgumentParser:
    env = os.environ.get
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("definitions", nargs="*", type=Path,
                   help="experiment YAML files, run in this order (default: TBD_DEFINITIONS)")
    p.add_argument("--out", type=Path, default=ROOT / "experiments")
    p.add_argument("--sync", default=env("TBD_SYNC"), metavar="DIR",
                   help="copy each experiment folder to DIR/<name>/ as jobs finish")
    p.add_argument("--sync-command", default=env("TBD_SYNC_COMMAND"), metavar="TEMPLATE",
                   help="or run this copy command, with {src} and {name} filled in")
    p.add_argument("--no-sync", action="store_true", help="run with no sync target")
    p.add_argument("--max-lifetime", default=env("TBD_MAX_LIFETIME", DEFAULT_LIFETIME),
                   help=f"stop after this long, e.g. 90m or 6h (default {DEFAULT_LIFETIME})")
    p.add_argument("--grace", default=DEFAULT_GRACE,
                   help="after the lifetime, how long to wait for interrupted jobs")
    p.add_argument("--on-exit", default=env("TBD_ON_EXIT"), metavar="COMMAND",
                   help="shell command run last, always (e.g. one that stops the pod)")
    p.add_argument("--serve-ollama", action="store_true",
                   help="start `ollama serve` here and pull the definitions' models")
    p.add_argument("--dry-run", action="store_true",
                   help="check the definitions and show the plan; start, pull and run nothing")
    return p


def main(argv: list[str] | None = None, *, run_def=run_definition, pull=pull_models,
         server_factory=OllamaServer, gpu=gpu_name, interrupt=None, run_shell=subprocess.run,
         hard_exit=os._exit) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    paths = args.definitions or [Path(x) for x in os.environ.get("TBD_DEFINITIONS", "").split()]
    if not paths:
        p.error("no experiment definitions (arguments or TBD_DEFINITIONS)")
    try:
        lifetime_s, grace_s = parse_duration(args.max_lifetime), parse_duration(args.grace)
        sink = make_sink(args.sync, args.sync_command, args.no_sync)
        items = plan(paths)
    except (ValueError, OSError) as e:   # DefinitionError is a ValueError
        p.error(str(e))

    models = list(dict.fromkeys(m for item in items for m in item.models))
    log(f"{len(items)} definition(s): {', '.join(str(i.path) for i in items)}")
    log(f"models: {', '.join(models)}")
    log(f"sync: {sink}")
    log(f"maximum lifetime: {lifetime_s:.0f}s; on exit: {args.on_exit or '(nothing)'}")
    if args.dry_run:
        for item in items:
            experiment.main([str(item.path), "--out", str(args.out), "--dry-run"])
        return 0

    started = time.monotonic()
    stop = {"signum": None}

    def on_signal(signum, frame):
        if stop["signum"] is None:
            log(f"{signal.Signals(signum).name}: no further definition will start")
        stop["signum"] = stop["signum"] or signum

    previous = {s: signal.signal(s, on_signal) for s in (signal.SIGINT, signal.SIGTERM)}
    sync = Syncer(sink)
    server = server_factory() if args.serve_ollama else None
    finish = Finish(items, args.out, sync, server, args.on_exit, run_shell)

    def give_up():
        log(f"jobs still running {grace_s:.0f}s after the lifetime ran out: exiting anyway")
        finish()
        hard_exit(EXIT_LIFETIME)

    lifetime = Lifetime(lifetime_s, grace_s,
                        interrupt or (lambda: os.kill(os.getpid(), signal.SIGTERM)), give_up)
    lifetime.start()
    results: list[tuple] = []
    try:
        if server:
            server.start()
            pull(models, stopped=lambda: stop["signum"] is not None)
        os.environ["TBD_HOST_ID"] = os.environ.get("TBD_HOST_ID") or host_id()
        if not os.environ.get("TBD_GPU"):
            name = gpu()
            if name:
                os.environ["TBD_GPU"] = name
            else:
                os.environ.pop("TBD_GPU", None)
        log(f"gpu: {os.environ.get('TBD_GPU')}; host: {os.environ['TBD_HOST_ID']}")
        results = run_queue(items, args.out, sync, run_def,
                            stopped=lambda: stop["signum"] is not None or lifetime.expired.is_set())
    except Exception as e:   # noqa: BLE001 -- a setup failure still runs Finish (stops the pod)
        log(f"error: {type(e).__name__}: {e}")
    finally:
        lifetime.cancel()
        synced = finish()
        for s, handler in previous.items():
            signal.signal(s, handler)

    hours = (time.monotonic() - started) / 3600
    log(f"done after {hours:.2f} h")
    for item, code in results:
        log(f"  {item.path}: " + ("not run" if code is None else f"exit {code}"))
    for name, error in sync.failed.items():
        log(f"  sync of {name} failed: {error}")
    if lifetime.expired.is_set():
        return EXIT_LIFETIME
    if stop["signum"] is not None or any(code == EXIT_SIGNAL for _, code in results):
        return EXIT_SIGNAL
    ok = results and len(results) == len(items) and all(code == 0 for _, code in results)
    return 0 if ok and synced else 1


if __name__ == "__main__":
    raise SystemExit(main())
