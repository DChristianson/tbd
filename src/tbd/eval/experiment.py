"""Experiment runner: expand a YAML experiment definition into case0 jobs and run them.

    python -m tbd.eval.experiment experiments.d/t1-baseline.yaml --dry-run
    python -m tbd.eval.experiment experiments.d/t1-baseline.yaml
    python -m tbd.eval.experiment experiments.d/t1-baseline.yaml --only 'llama3.1-*'

The definition (docs/design/execution.md, "Experiment definition"):

    experiment: t1-baseline        # folder under --out (default experiments/)
    models:                        # each: provider (default ollama), model, and any
      - { model: llama3.1 }        #   setting below as a per-model override
      - { provider: ollama, model: "qwen2.5-coder:14b", num_ctx: 16384 }
    facts: [none]                  # fact conditions (tbd.eval.facts.CONDITIONS)
    tasks:
      - { id: t1, variants: all }  # all = every tasks/t1/<variant>/ with a prompt.txt
    modes: [one-shot, tool-feedback]
    samples: 3                     # settings: samples, seed, temperature, retries,
    seed: 42                       #   max_tokens, num_ctx, timeout, idle_timeout,
    temperature: 0.7               #   think, effort, random_seeds; unset ones take
                                   #   case0's defaults
    parallel: 1                    # jobs at once (default 1; --parallel overrides)
    gate: [t1/canonical, t1/blue]  # optional, see "Gate" below

The cartesian product models x facts x tasks/variants x modes is one case0 job
each, run in that order, so a model stays loaded across its jobs. By default
one job runs at a time (right for local Ollama). With `parallel: N` up to N
run at once, for a provider that serves requests side by side (a RunPod
endpoint with several workers); each job's output is then printed with its
job id in front.

Gate. Simpler variants come first: a model that can't make a stable frame
won't do better on harder ones. With `gate`, the listed task/variants run
first for each model, fact condition and mode. The rest of that
configuration runs only if some attempt in some sample of a gate job
assembled; if none did, those jobs are skipped as "gated". A gate job that
didn't complete leaves its configuration's other jobs not run. Each job is `python -m tbd.eval.case0 ... --experiment NAME`
in a subprocess, so all jobs land in experiments/NAME/jobs/ in case0's layout.
The runner adds experiment.yaml (the definition, byte for byte) and jobs.json
(the expanded list) beside case0's manifest.json; a later run with a changed
definition adds experiment.<stamp>.yaml / jobs.<stamp>.json instead.

Resume. Running the same definition again skips every job whose metrics.json
says "completed" (and whose config.json matches the definition). A job folder
that exists but didn't complete (interrupted, failed, a stale "running", or a
setup crash with no metrics) can't be reused: case0 never overwrites a job
folder, and continuing its samples in place would mix two runs in one record.
Those jobs are skipped and listed at the end, with what to do; nothing is
deleted or overwritten. So is a completed job whose settings differ from the
definition's.

Stopping. Ctrl-C or SIGTERM is passed to the running job, which marks itself
"interrupted" (case0); the runner then starts no further jobs.

At the end the grid report (tbd.eval.report) for the experiment is printed.
"""

from __future__ import annotations

import argparse
import fnmatch
import itertools
import json
import os
import signal
import subprocess
import sys
import threading
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from tbd.eval import case0, facts, report, tasks
from tbd.paths import ROOT, TASKS

MODES = ("one-shot", "tool-feedback")
PROVIDERS = ("ollama", "anthropic", "runpod")
# Definition key -> case0 flag. Each may be set at the top level or per model.
SETTINGS = {"samples": "--samples", "seed": "--seed", "temperature": "--temperature",
            "retries": "--retries", "max_tokens": "--max-tokens", "num_ctx": "--num-ctx",
            "timeout": "--timeout", "idle_timeout": "--idle-timeout", "endpoint": "--endpoint", "think": "--think", "effort": "--effort",
            "random_seeds": "--random-seeds"}
TOP_KEYS = {"experiment", "models", "facts", "tasks", "modes", "parallel", "gate", *SETTINGS}
MODEL_KEYS = {"provider", "model", *SETTINGS}
TASK_KEYS = {"id", "variants"}


class DefinitionError(ValueError):
    """The YAML doesn't describe a runnable experiment."""


@dataclass
class Job:
    job_id: str
    provider: str
    model: str
    facts: str
    task: str
    variant: str
    mode: str
    settings: dict = field(default_factory=dict)
    gate: bool = False   # a gate job (definition's `gate`)
    after: list[str] = field(default_factory=list)   # gate job ids this one waits on

    def argv(self, experiment: str, out: Path) -> list[str]:
        """case0's command line for this job."""
        args = ["--provider", self.provider, "--model", self.model, "--task", self.task,
                "--variant", self.variant, "--facts", self.facts, "--mode", self.mode]
        for key, value in self.settings.items():
            if key == "think" and isinstance(value, bool):
                value = "on" if value else "off"
            args += [SETTINGS[key], str(value)]
        return args + ["--experiment", experiment, "--out", str(out)]


def job_id(model: str, task: str, variant: str, facts_condition: str, mode: str) -> str:
    """The folder name case0 gives the job (case0.main)."""
    return f"{model.replace(':', '_').replace('/', '_')}-{task}-{variant}-{facts_condition}-{mode}"


# ---------------------------------------------------------------- expansion

def _list(defn: dict, key: str) -> list:
    value = defn.get(key)
    if value is None:
        raise DefinitionError(f"{key}: required")
    value = value if isinstance(value, list) else [value]
    if not value:
        raise DefinitionError(f"{key}: empty")
    return value


def _check_keys(where: str, d: dict, allowed: set) -> None:
    unknown = sorted(set(d) - allowed)
    if unknown:
        raise DefinitionError(f"{where}: unknown key(s) {unknown}; allowed: {sorted(allowed)}"
                              + ("  (temperature and seed are single values: case0's job "
                                 "name doesn't include them, so two would collide)"
                                 if {"temperatures", "seeds"} & set(unknown) else ""))


def all_variants(task: str, root: Path = TASKS) -> list[str]:
    """Every tasks/<task>/<variant>/ with a prompt.txt, canonical first."""
    names = sorted(p.parent.name for p in (root / task).glob("*/prompt.txt"))
    return sorted(names, key=lambda n: n != "canonical")


def expand(defn: dict, root: Path = TASKS) -> list[Job]:
    """The definition's cartesian product, models outermost."""
    if not isinstance(defn, dict):
        raise DefinitionError("the definition must be a mapping")
    _check_keys("definition", defn, TOP_KEYS)
    name = defn.get("experiment")
    if not isinstance(name, str) or not name or "/" in name or name.startswith("."):
        raise DefinitionError("experiment: required, a folder name (no '/')")

    models = []
    for m in _list(defn, "models"):
        m = {"model": m} if isinstance(m, str) else m
        if not isinstance(m, dict) or not m.get("model"):
            raise DefinitionError(f"models: each entry needs a model name: {m!r}")
        _check_keys(f"model {m['model']}", m, MODEL_KEYS)
        provider = m.get("provider", "ollama")
        if provider not in PROVIDERS:
            raise DefinitionError(f"model {m['model']}: provider must be one of {PROVIDERS}")
        settings = {k: defn[k] for k in SETTINGS if k in defn}
        settings.update({k: m[k] for k in SETTINGS if k in m})
        models.append((provider, str(m["model"]), settings))

    conditions = [str(c) for c in _list(defn, "facts")]
    for c in conditions:
        if c not in facts.CONDITIONS:
            raise DefinitionError(f"facts: unknown condition {c!r}; one of {list(facts.CONDITIONS)}")
        if c != "none" and not facts.select(facts.load(), c):
            raise DefinitionError(f"facts: the fact store has no facts for {c!r}")

    cells = []
    for t in _list(defn, "tasks"):
        t = {"id": t, "variants": "all"} if isinstance(t, str) else t
        if not isinstance(t, dict) or not t.get("id"):
            raise DefinitionError(f"tasks: each entry needs an id: {t!r}")
        _check_keys(f"task {t['id']}", t, TASK_KEYS)
        task = str(t["id"])
        variants = t.get("variants", ["canonical"])
        variants = (all_variants(task, root) if variants == "all"
                    else [str(v) for v in (variants if isinstance(variants, list) else [variants])])
        if not variants:
            raise DefinitionError(f"task {task}: no variants under {root / task}")
        for v in variants:
            try:
                tasks.load(task, v, root)
            except FileNotFoundError as e:
                raise DefinitionError(f"task {task}: {e}") from None
            cells.append((task, v))

    modes = [str(m) for m in _list(defn, "modes")]
    for m in modes:
        if m not in MODES:
            raise DefinitionError(f"modes: unknown mode {m!r}; one of {MODES}")

    gates = gate_cells(defn, cells)
    parallel_jobs(defn)

    jobs, seen = [], {}
    for (provider, model, settings), cond, (task, variant), mode in itertools.product(
            models, conditions, cells, modes):
        job = Job(job_id(model, task, variant, cond, mode), provider, model, cond, task,
                  variant, mode, dict(settings), gate=(task, variant) in gates)
        if gates and not job.gate:
            job.after = [job_id(model, t, v, cond, mode) for t, v in gates]
        if job.job_id in seen:
            raise DefinitionError(f"two jobs would share the folder {job.job_id}; "
                                  "case0 names a job by model, task, variant, facts and mode only")
        seen[job.job_id] = job
        jobs.append(job)
    # Each model's gate jobs first (stable, so models stay outermost).
    order = {m: i for i, (_, m, _) in enumerate(models)}
    jobs.sort(key=lambda j: (order[j.model], not j.gate))
    # Fail now, not mid-experiment, on a value case0 would reject.
    parser = case0.build_parser()
    for job in jobs:
        try:
            parser.parse_args(job.argv(name, Path(".")))
        except SystemExit:
            raise DefinitionError(f"{job.job_id}: case0 rejects {job.argv(name, Path('.'))}") from None
    return jobs


def gate_cells(defn: dict, cells: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The definition's `gate` as (task, variant) pairs, each one of its cells."""
    gate = defn.get("gate")
    if gate is None:
        return []
    gate = gate if isinstance(gate, list) else [gate]
    out = []
    for g in gate:
        task, _, variant = str(g).partition("/")
        if (task, variant) not in cells:
            raise DefinitionError(f"gate: {g!r} is not one of the definition's task/variants "
                                  f"({', '.join(f'{t}/{v}' for t, v in cells)})")
        out.append((task, variant))
    if not out or len(out) == len(cells):
        raise DefinitionError("gate: name some, not all, of the task/variants")
    return out


def parallel_jobs(defn: dict) -> int:
    """The definition's `parallel` (default 1)."""
    n = defn.get("parallel", 1)
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise DefinitionError(f"parallel: a whole number of jobs, 1 or more: {n!r}")
    return n


def load(path: Path) -> tuple[dict, list[Job]]:
    try:
        defn = yaml.safe_load(Path(path).read_text())
    except yaml.YAMLError as e:
        raise DefinitionError(f"{path}: {e}") from None
    return defn, expand(defn)


# ---------------------------------------------------------------- job state

def expected_config(job: Job) -> dict:
    """The config.json values case0 would record for this job (the fields compared)."""
    ns = case0.build_parser().parse_args(job.argv("x", Path(".")))
    api = job.provider == "anthropic"
    max_tokens = case0.effective_max_tokens(ns)
    return {"provider": job.provider, "model": job.model, "facts": job.facts,
            "task": job.task, "variant": job.variant, "mode": job.mode,
            "samples": ns.samples,
            "temperature": None if api else ns.temperature,
            "seed": None if api else ns.seed,
            "effort": ns.effort if api else None,
            "retries": ns.retries if job.mode == "tool-feedback" else 0,
            "max_tokens": max_tokens,
            "num_ctx": None if api else (ns.num_ctx or max(16384, max_tokens + 4096)),
            "request_timeout_s": ns.timeout,
            "idle_timeout_s": None if api else ns.idle_timeout,
            "random_seeds": ns.random_seeds}


def job_state(job: Job, exp_dir: Path) -> tuple[str, str]:
    """("new" | "done" | "incomplete" | "mismatch", detail) for the job's folder."""
    job_dir = exp_dir / "jobs" / job.job_id
    if not job_dir.exists():
        return "new", ""
    loaded = report.load_job(job_dir)
    metrics, config = loaded["metrics"], loaded["config"]
    status = metrics.get("status")
    if status != "completed":
        if not metrics:
            return "incomplete", "no metrics.json (stopped during setup)"
        stale = report.stale_reason(metrics, config, datetime.now(timezone.utc))
        if status == "running":
            return "incomplete", (f"running, stale: {stale}" if stale
                                  else "running (maybe in another process)")
        return "incomplete", f"{status}, {metrics.get('samples_done', 0)} of " \
                             f"{metrics.get('samples_planned', '?')} samples"
    want = expected_config(job)
    diff = [f"{k}={config[k]!r} (definition: {v!r})" for k, v in want.items()
            if k in config and config[k] != v]
    if diff:
        return "mismatch", "completed with other settings: " + ", ".join(diff)
    return "done", ""


def assembled(job_dir: Path) -> bool:
    """Whether any attempt the job logged assembled."""
    try:
        lines = (job_dir / "attempts.jsonl").read_text().splitlines()
    except OSError:
        return False
    return any(json.loads(line).get("assemble", {}).get("ok") for line in lines if line.strip())


def gate_verdict(job: Job, exp_dir: Path) -> tuple[str, str]:
    """("open" | "closed" | "blocked", detail) for a job behind gate jobs, once
    none of them is still to run: open if one assembled in some attempt."""
    for gate_id in job.after:
        status = report.load_job(exp_dir / "jobs" / gate_id)["metrics"].get("status")
        if status != "completed":
            return "blocked", f"gate job {gate_id} {status or 'not run'}"
    if any(assembled(exp_dir / "jobs" / g) for g in job.after):
        return "open", ""
    return "closed", "no attempt of " + ", ".join(job.after) + " assembled"


# ---------------------------------------------------------------- running

class Stop:
    """SIGINT/SIGTERM: remember it and pass it on to the running jobs, once."""

    def __init__(self):
        self.signum = None   # the signal received, if any
        self.halted = None   # or why the runner stopped itself
        self.children = set()
        self.lock = threading.Lock()

    def __call__(self, signum, frame):
        first = self.signum is None
        self.signum = self.signum or signum
        name = signal.Signals(signum).name
        if first:
            print(f"\n[experiment] {name}: stopping after the running job(s)", flush=True)
            with self.lock:
                children = list(self.children)
            for child in children:
                if child.poll() is None:
                    child.send_signal(signum)
        else:
            print(f"\n[experiment] {name} again: already stopping", flush=True)

    def __bool__(self):
        return self.signum is not None or self.halted is not None

    def install(self):
        previous = {}
        for s in (signal.SIGINT, signal.SIGTERM):
            try:
                previous[s] = signal.signal(s, self)
            except ValueError:   # not the main thread
                pass
        return previous


def subprocess_job(argv: list[str], stop: Stop, label: str | None = None) -> int:
    """Run case0 in its own process session, so a terminal Ctrl-C reaches it only
    through the runner (once), and wait for it to finish. With a label (jobs
    running side by side), each line it prints is prefixed with it."""
    child = subprocess.Popen([sys.executable, "-m", "tbd.eval.case0", *argv],
                             start_new_session=True,
                             **({"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT,
                                 "text": True, "bufsize": 1} if label else {}))
    with stop.lock:
        stop.children.add(child)
    try:
        if label:
            for line in child.stdout:
                print(f"[{label}] {line}", end="", flush=True)
        return child.wait()
    finally:
        with stop.lock:
            stop.children.discard(child)


def ollama_check(models: list[str]) -> str | None:
    """Why Ollama can't run these models now (unreachable, or a model not pulled), or None."""
    from tbd.eval.models import OLLAMA, model_digest
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=30):
            pass
        missing = [m for m in models if model_digest(m) is None]
    except OSError as e:
        return f"Ollama at {OLLAMA} not reachable: {e}"
    return f"not pulled in Ollama: {', '.join(missing)}" if missing else None


def record_definition(exp_dir: Path, source: Path, jobs: list[Job]) -> None:
    """experiment.yaml + jobs.json beside the manifest. Never overwritten: a changed
    definition is saved again under a timestamp."""
    exp_dir.mkdir(parents=True, exist_ok=True)
    text = source.read_bytes()
    listing = json.dumps([{**asdict(j), "argv": j.argv(exp_dir.name, exp_dir.parent)}
                          for j in jobs], indent=2) + "\n"
    if (exp_dir / "experiment.yaml").exists():
        if (exp_dir / "experiment.yaml").read_bytes() == text:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        yaml_path, jobs_path = exp_dir / f"experiment.{stamp}.yaml", exp_dir / f"jobs.{stamp}.json"
        print(f"[experiment] the definition differs from {exp_dir / 'experiment.yaml'}; "
              f"saved as {yaml_path.name}", flush=True)
    else:
        yaml_path, jobs_path = exp_dir / "experiment.yaml", exp_dir / "jobs.json"
    yaml_path.write_bytes(text)
    jobs_path.write_text(listing)


def select(jobs: list[Job], patterns: list[str] | None) -> list[Job]:
    """Jobs whose id matches any pattern (a glob; without wildcards, a substring)."""
    if not patterns:
        return jobs
    globs = [p if any(c in p for c in "*?[") else f"*{p}*" for p in patterns]
    return [j for j in jobs if any(fnmatch.fnmatchcase(j.job_id, g) for g in globs)]


def run(jobs: list[Job], exp_dir: Path, *, dry_run: bool = False, run_job=subprocess_job,
        preflight=ollama_check, stop: Stop | None = None, parallel: int = 1) -> dict:
    """Run the jobs not yet done, in order, up to `parallel` at once; a job behind
    gate jobs waits for them (gate_verdict). Returns {state: [(job_id, detail)]},
    where state is done (skipped), ran, failed, incomplete, mismatch, gated or
    not run (stopped, or its gate job didn't complete)."""
    stop = stop if stop is not None else Stop()
    outcome: dict[str, list] = {}
    states = [(job, *job_state(job, exp_dir)) for job in jobs]
    todo = [job for job, state, _ in states if state == "new"]
    width = max((len(j.job_id) for j in jobs), default=0)
    print(f"[experiment] {exp_dir}: {len(jobs)} jobs, {len(todo)} to run"
          + (f", up to {parallel} at once" if parallel > 1 else ""), flush=True)
    for job, state, detail in states:
        mark = "run" if state == "new" else state
        if state == "new" and job.after:
            mark += " if a gate job assembles"
        print(f"  {job.job_id:<{width}}  {mark}" + (f"  ({detail})" if detail else ""))
        if state != "new":
            outcome.setdefault(state, []).append((job.job_id, detail))
    if dry_run or not todo:
        return outcome

    ollama = sorted({j.model for j in todo if j.provider == "ollama"})
    if ollama and (problem := preflight(ollama)):
        print(f"[experiment] not starting: {problem}", flush=True)
        outcome["not run"] = [(j.job_id, problem) for j in todo]
        return outcome

    pending, running, started = list(todo), {}, 0

    def next_job() -> Job | None:
        """The first pending job that can start now; settles gated ones on the way."""
        busy = {j.job_id for j in pending} | {j.job_id for j in running.values()}
        for job in list(pending):
            if any(g in busy for g in job.after):
                continue
            pending.remove(job)
            verdict, detail = gate_verdict(job, exp_dir) if job.after else ("open", "")
            if verdict == "open":
                return job
            state = "gated" if verdict == "closed" else "not run"
            print(f"[experiment] {state}: {job.job_id} ({detail})", flush=True)
            outcome.setdefault(state, []).append((job.job_id, detail))
            busy.discard(job.job_id)
        return None

    def finish(job: Job, code: int) -> None:
        status = report.load_job(exp_dir / "jobs" / job.job_id)["metrics"].get("status")
        if status == "completed" and code == 0:
            outcome.setdefault("ran", []).append((job.job_id, ""))
            if parallel > 1:
                print(f"[experiment] finished: {job.job_id}", flush=True)
        elif status == "interrupted" or stop:
            outcome.setdefault("incomplete", []).append((job.job_id, "interrupted"))
            stop.halted = stop.halted or "a job was interrupted"
        else:
            outcome.setdefault("failed", []).append(
                (job.job_id, f"exit {code}, status {status or 'none (no metrics.json)'}"))

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        while True:
            while len(running) < parallel and not stop and (job := next_job()) is not None:
                # Check Ollama before case0 makes the job folder: a job that dies in
                # setup leaves a folder that can't be rerun under this experiment.
                if job.provider == "ollama" and (problem := preflight([job.model])):
                    print(f"[experiment] stopping: {problem}", flush=True)
                    stop.halted = problem
                    outcome.setdefault("not run", []).append((job.job_id, problem))
                    break
                started += 1
                print(f"\n[experiment] job {started}/{len(todo)}: {job.job_id}", flush=True)
                argv = job.argv(exp_dir.name, exp_dir.parent)
                running[pool.submit(run_job, argv, stop, **(
                    {"label": job.job_id} if parallel > 1 else {}))] = job
            if not running:
                break
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                finish(running.pop(future), future.result())
    for job in pending:
        outcome.setdefault("not run", []).append((job.job_id, stop.halted or "stopped"))
    return outcome


def summary(outcome: dict, exp_dir: Path, definition: Path) -> str:
    lines = ["", "# Experiment run", ""]
    for state in ("ran", "done", "gated", "failed", "incomplete", "mismatch", "not run"):
        for job_id, detail in outcome.get(state, []):
            lines.append(f"- {state}: {job_id}" + (f" ({detail})" if detail else ""))
    stuck = outcome.get("failed", []) + outcome.get("incomplete", []) + outcome.get("mismatch", [])
    if stuck:
        lines += ["", f"{len(stuck)} job(s) can't be resumed in {exp_dir.name}: their folders "
                  "exist and runs are never overwritten. A \"running\" one may still be "
                  "running elsewhere. To redo them, run them under a new experiment "
                  "name, e.g. a copy of the definition restricted with --only."]
    if outcome.get("not run"):
        lines += ["", f"Not run yet; rerun to resume: python -m tbd.eval.experiment {definition}"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, run_job=subprocess_job, preflight=ollama_check) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("definition", type=Path, help="experiment YAML (e.g. experiments.d/*.yaml)")
    p.add_argument("--dry-run", action="store_true",
                   help="list the jobs and which would be skipped; run nothing")
    p.add_argument("--only", action="append", metavar="PATTERN",
                   help="run only job ids matching this glob (or substring); repeatable")
    p.add_argument("--parallel", type=int, metavar="N", default=None,
                   help="run up to N jobs at once (default: the definition's parallel, or 1)")
    p.add_argument("--out", type=Path, default=ROOT / "experiments")
    args = p.parse_args(argv)
    if args.parallel is not None and args.parallel < 1:
        p.error("--parallel: 1 or more")
    try:
        defn, jobs = load(args.definition)
    except (OSError, DefinitionError) as e:
        p.error(str(e))
    exp_dir = args.out / defn["experiment"]
    chosen = select(jobs, args.only)
    if not chosen:
        p.error(f"--only {args.only}: no job matches")

    parallel = args.parallel or parallel_jobs(defn)

    if args.dry_run:
        run(chosen, exp_dir, dry_run=True, parallel=parallel)
        return 0

    stop = Stop()
    previous = stop.install()
    try:
        record_definition(exp_dir, args.definition, jobs)
        outcome = run(chosen, exp_dir, run_job=run_job, preflight=preflight, stop=stop,
                      parallel=parallel)
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)
    sys.stdout.write(summary(outcome, exp_dir, args.definition))
    loaded = [report.load_job(j) for j in report.find_jobs([exp_dir])]
    sys.stdout.write("\n" + report.render_markdown(
        report.report_tables(report.build_cells(loaded)), [exp_dir.name]))
    if stop.signum is not None or stop.halted == "a job was interrupted":
        return 130
    return 1 if any(outcome.get(s) for s in ("failed", "incomplete", "mismatch", "not run")) else 0


if __name__ == "__main__":
    raise SystemExit(main())
