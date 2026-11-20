"""Pod runner: queue order, sync after every job, the lifetime limit, sync targets.
The experiment runner, Ollama and nvidia-smi are stubbed; nothing runs a model."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

from tbd.eval import case0, experiment, podrunner
from tbd.eval.jobrecords import compute

DEFN = {
    "experiment": "exp",
    "models": [{"model": "llama3.1"}],
    "facts": ["none"],
    "tasks": [{"id": "t1", "variants": ["canonical", "blue"]}],
    "modes": ["one-shot"],
    "samples": 1,
}


def write_defn(tmp_path, name, **changes):
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump({**DEFN, "experiment": name, **changes}))
    return path


class StubRun:
    """Stands in for run_definition: makes one file per 'job' and calls after_job."""

    def __init__(self, jobs=2, codes=None, block=None):
        self.jobs, self.codes, self.block = jobs, codes or {}, block
        self.order, self.synced_after = [], []

    def __call__(self, path, out, after_job):
        self.order.append(path.stem)
        exp_dir = out / path.stem
        for n in range(self.jobs):
            (exp_dir / "jobs" / f"job{n}").mkdir(parents=True, exist_ok=True)
            (exp_dir / "jobs" / f"job{n}" / "metrics.json").write_text('{"status": "completed"}')
            after_job()
            if self.block:
                if self.block.wait(5):
                    return podrunner.EXIT_SIGNAL
        return self.codes.get(path.stem, 0)


def run(tmp_path, argv, **kw):
    kw.setdefault("gpu", lambda: "NVIDIA RTX A6000")
    return podrunner.main([*argv, "--out", str(tmp_path / "out")], **kw)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("TBD_GPU", "TBD_HOST_ID", "RUNPOD_POD_ID", "TBD_SYNC", "TBD_SYNC_COMMAND",
                "TBD_DEFINITIONS", "TBD_ON_EXIT", "TBD_MAX_LIFETIME"):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------- the queue

def test_runs_definitions_in_the_order_given_and_syncs_each(tmp_path):
    a, b, c = (write_defn(tmp_path, n) for n in ("zeta", "alpha", "mid"))
    stub = StubRun()
    dest = tmp_path / "durable"
    assert run(tmp_path, [str(a), str(b), str(c), "--sync", str(dest)], run_def=stub) == 0
    assert stub.order == ["zeta", "alpha", "mid"]
    for name in ("zeta", "alpha", "mid"):
        assert (dest / name / "jobs" / "job1" / "metrics.json").exists()


def test_definitions_can_come_from_the_environment(tmp_path, monkeypatch):
    a, b = write_defn(tmp_path, "one"), write_defn(tmp_path, "two")
    monkeypatch.setenv("TBD_DEFINITIONS", f"{a} {b}")
    stub = StubRun()
    assert run(tmp_path, ["--no-sync"], run_def=stub) == 0
    assert stub.order == ["one", "two"]


def test_every_definition_is_checked_before_anything_runs(tmp_path, capsys):
    good, bad = write_defn(tmp_path, "good"), write_defn(tmp_path, "bad", modes=["nope"])
    stub = StubRun()
    with pytest.raises(SystemExit):
        run(tmp_path, [str(good), str(bad), "--no-sync"], run_def=stub)
    assert stub.order == []
    assert "unknown mode" in capsys.readouterr().err


def test_rejects_models_the_pod_does_not_serve(tmp_path, capsys):
    path = write_defn(tmp_path, "x", models=[{"provider": "runpod", "model": "qwen3"}])
    with pytest.raises(SystemExit):
        run(tmp_path, [str(path), "--no-sync"], run_def=StubRun())
    assert "another provider" in capsys.readouterr().err


def test_a_sync_target_or_no_sync_is_required(tmp_path, capsys):
    path = write_defn(tmp_path, "x")
    with pytest.raises(SystemExit):
        run(tmp_path, [str(path)], run_def=StubRun())
    assert "--no-sync" in capsys.readouterr().err


def test_a_failed_definition_is_reported_and_the_next_runs(tmp_path):
    a, b = write_defn(tmp_path, "a"), write_defn(tmp_path, "b")
    stub = StubRun(codes={"a": 1})
    assert run(tmp_path, [str(a), str(b), "--no-sync"], run_def=stub) == 1
    assert stub.order == ["a", "b"]


def test_interrupted_definition_stops_the_queue(tmp_path):
    a, b = write_defn(tmp_path, "a"), write_defn(tmp_path, "b")
    stub = StubRun(codes={"a": podrunner.EXIT_SIGNAL})
    assert run(tmp_path, [str(a), str(b), "--no-sync"], run_def=stub) == podrunner.EXIT_SIGNAL
    assert stub.order == ["a"]


def test_serve_ollama_starts_it_pulls_each_model_once_and_stops_it(tmp_path):
    a = write_defn(tmp_path, "a", models=[{"model": "llama3.1"}, {"model": "qwen3"}])
    b = write_defn(tmp_path, "b", models=[{"model": "qwen3"}])
    events = []

    class Server:
        def start(self):
            events.append("start")

        def stop(self):
            events.append("stop")

    def pull(models, stopped):
        events.append(("pull", models))

    def run_def(path, out, after_job):
        events.append(("run", path.stem))
        return 0

    assert run(tmp_path, [str(a), str(b), "--no-sync", "--serve-ollama"], run_def=run_def,
               pull=pull, server_factory=Server) == 0
    assert events == ["start", ("pull", ["llama3.1", "qwen3"]), ("run", "a"), ("run", "b"),
                      "stop"]


def test_a_setup_failure_still_runs_on_exit(tmp_path):
    path = write_defn(tmp_path, "a")
    calls = []

    def pull(models, stopped):
        raise subprocess.CalledProcessError(1, ["ollama", "pull"])

    class Server:
        def start(self):
            pass

        def stop(self):
            pass

    code = run(tmp_path, [str(path), "--no-sync", "--serve-ollama", "--on-exit", "stop-the-pod"],
               run_def=StubRun(), pull=pull, server_factory=Server,
               run_shell=lambda argv, **kw: calls.append(argv))
    assert code == 1
    assert calls == [["sh", "-c", "stop-the-pod"]]


def test_dry_run_runs_and_pulls_nothing(tmp_path, capsys):
    path = write_defn(tmp_path, "a")
    stub = StubRun()
    assert run(tmp_path, [str(path), "--sync", str(tmp_path / "d"), "--dry-run",
                          "--on-exit", "stop-the-pod"], run_def=stub,
               run_shell=lambda *a, **k: pytest.fail("on-exit ran")) == 0
    assert stub.order == []
    assert not (tmp_path / "d").exists()
    out = capsys.readouterr().out
    assert "llama3.1-t1-blue-none-one-shot" in out and "models: llama3.1" in out


# ---------------------------------------------------------------- sync after every job

def test_syncs_after_every_job_through_the_real_experiment_runner(tmp_path, monkeypatch):
    """run_definition wraps experiment's run_job: each job is synced as soon as it ends."""
    path = write_defn(tmp_path, "exp")
    out, dest = tmp_path / "out", tmp_path / "durable"
    seen_at_dest = []

    def fake_job(argv, stop, label=None):
        ns = case0.build_parser().parse_args(argv)
        job_id = experiment.job_id(ns.model, ns.task, ns.variant, ns.facts, ns.mode)
        # What is at the destination when this job starts: every earlier job.
        seen_at_dest.append(sorted(p.name for p in (dest / "exp" / "jobs").glob("*")))
        d = out / "exp" / "jobs" / job_id
        d.mkdir(parents=True)
        (d / "metrics.json").write_text(json.dumps({"status": "completed"}))
        return 0

    monkeypatch.setattr(experiment, "subprocess_job", fake_job)
    monkeypatch.setattr(experiment, "ollama_check", lambda models: None)
    assert run(tmp_path, [str(path), "--sync", str(dest)]) in (0, 1)
    assert seen_at_dest == [[], ["llama3.1-t1-canonical-none-one-shot"]]
    assert (dest / "exp" / "jobs" / "llama3.1-t1-blue-none-one-shot" / "metrics.json").exists()
    assert (dest / "exp" / "experiment.yaml").exists()


def test_a_failed_sync_is_retried_and_reported(tmp_path):
    path = write_defn(tmp_path, "a")
    attempts = []

    class Flaky:
        def sync(self, exp_dir):
            attempts.append(exp_dir.name)
            raise OSError("bucket unreachable")

    sync = podrunner.Syncer(Flaky())
    (tmp_path / "out" / "a").mkdir(parents=True)
    assert sync(tmp_path / "out" / "a") is False
    assert sync.failed == {"a": "OSError: bucket unreachable"}
    # main: a final sync that fails makes the exit status 1
    code = run(tmp_path, [str(path), "--sync-command", "false {src}"], run_def=StubRun())
    assert code == 1


# ---------------------------------------------------------------- lifetime

def test_lifetime_interrupts_the_running_definition_and_skips_the_rest(tmp_path):
    a, b = write_defn(tmp_path, "a"), write_defn(tmp_path, "b")
    interrupted = threading.Event()
    stub = StubRun(jobs=1, block=interrupted)
    exits = []
    t0 = time.monotonic()
    code = run(tmp_path, [str(a), str(b), "--sync", str(tmp_path / "d"), "--max-lifetime", "0.3",
                          "--on-exit", "stop-the-pod"],
               run_def=stub, interrupt=interrupted.set, hard_exit=exits.append,
               run_shell=lambda argv, **kw: exits.append(argv))
    assert code == podrunner.EXIT_LIFETIME
    assert time.monotonic() - t0 < 4
    assert stub.order == ["a"]
    assert (tmp_path / "d" / "a" / "jobs" / "job0" / "metrics.json").exists()
    assert exits == [["sh", "-c", "stop-the-pod"]]   # on-exit ran once; no hard exit


def test_lifetime_gives_up_on_jobs_that_ignore_the_interrupt(tmp_path):
    path = write_defn(tmp_path, "a")
    gave_up = threading.Event()
    exits = []

    def stuck(path, out, after_job):
        (out / "a").mkdir(parents=True, exist_ok=True)
        (out / "a" / "partial.txt").write_text("x")
        gave_up.wait(5)
        return podrunner.EXIT_SIGNAL

    def hard_exit(code):
        exits.append(code)
        gave_up.set()

    code = run(tmp_path, [str(path), "--sync", str(tmp_path / "d"), "--max-lifetime", "0.2",
                          "--grace", "0.2"],
               run_def=stuck, interrupt=lambda: None, hard_exit=hard_exit)
    assert exits == [podrunner.EXIT_LIFETIME]
    assert (tmp_path / "d" / "a" / "partial.txt").exists()   # synced before giving up
    assert code == podrunner.EXIT_LIFETIME


def test_example_pod_definition_is_runnable_by_the_pod():
    from tbd.paths import ROOT
    items = podrunner.plan([ROOT / "experiments.d" / "t1-facts-qwen3.6-pod.yaml"])
    assert items[0].models == ["qwen3.6:27b"]


def test_worker_image_pins_the_dev_containers_tool_versions():
    """The two Dockerfiles are separate; jobs graded in either must use the same tools."""
    import re
    from tbd.paths import ROOT

    def pins(path):
        return dict(re.findall(r"^ARG (DASM_\w+|RUSTUP_\w+|RUST_VERSION)=(\S+)$",
                               path.read_text(), re.M))

    worker = pins(ROOT / "docker" / "worker.Dockerfile")
    dev = pins(ROOT / ".devcontainer" / "Dockerfile")
    assert {"DASM_VERSION", "DASM_SHA256", "RUST_VERSION"} <= set(worker)
    assert worker == {k: dev[k] for k in worker}


def test_parse_duration():
    assert podrunner.parse_duration("90") == 90
    assert podrunner.parse_duration("45m") == 2700
    assert podrunner.parse_duration("6h") == 21600
    with pytest.raises(ValueError):
        podrunner.parse_duration("6 hours")


# ---------------------------------------------------------------- sync targets

def test_local_dir_sink_copies_new_and_changed_files_and_deletes_nothing(tmp_path):
    src = tmp_path / "out" / "exp"
    (src / "jobs" / "j").mkdir(parents=True)
    (src / "jobs" / "j" / "a.txt").write_text("one")
    sink = podrunner.LocalDirSink(tmp_path / "dest")
    sink.sync(src)
    dst = tmp_path / "dest" / "exp" / "jobs" / "j" / "a.txt"
    assert dst.read_text() == "one"

    (src / "jobs" / "j" / "a.txt").write_text("one, two")
    os.utime(src / "jobs" / "j" / "a.txt", (time.time() + 5, time.time() + 5))
    (tmp_path / "dest" / "exp" / "kept.txt").write_text("only at the destination")
    sink.sync(src)
    assert dst.read_text() == "one, two"
    assert (tmp_path / "dest" / "exp" / "kept.txt").exists()
    assert not list((tmp_path / "dest").rglob("*podrunner-tmp"))


def test_command_sink_fills_in_src_and_name_without_a_shell(tmp_path):
    src = tmp_path / "out" / "my exp"
    src.mkdir(parents=True)
    (src / "f.txt").write_text("x")
    dest = tmp_path / "dest"
    dest.mkdir()
    sink = podrunner.CommandSink(f"cp -R {{src}} '{dest}/{{name}}'")
    sink.sync(src)
    assert (dest / "my exp" / "f.txt").read_text() == "x"


@pytest.mark.parametrize("args, message", [
    (("s3://bucket/x", None, False), "--sync-command"),
    (("/d", "cp {src} x", False), "pick one"),
    ((None, None, False), "pick one"),
])
def test_make_sink_rejects(args, message):
    with pytest.raises(ValueError, match=message):
        podrunner.make_sink(*args)


def test_command_sink_needs_src():
    with pytest.raises(ValueError, match="src"):
        podrunner.CommandSink("aws s3 sync . s3://b")


# ---------------------------------------------------------------- the machine

def test_gpu_name_from_nvidia_smi_or_none():
    def smi(out, code=0):
        return lambda *a, **k: subprocess.CompletedProcess(a, code, stdout=out, stderr="")

    assert podrunner.gpu_name(smi("NVIDIA RTX A6000\n")) == "NVIDIA RTX A6000"
    assert podrunner.gpu_name(smi("NVIDIA L4\nNVIDIA L4\n")) == "NVIDIA L4, NVIDIA L4"
    assert podrunner.gpu_name(smi("", 9)) is None

    def missing(*a, **k):
        raise FileNotFoundError("nvidia-smi")

    assert podrunner.gpu_name(missing) is None


def test_runner_records_gpu_and_pod_id_for_case0(tmp_path, monkeypatch):
    path = write_defn(tmp_path, "a")
    monkeypatch.setenv("RUNPOD_POD_ID", "abc123")
    seen = []
    run(tmp_path, [str(path), "--no-sync"],
        run_def=lambda *a: seen.append(compute()) or 0, gpu=lambda: "NVIDIA RTX A6000")
    assert seen == [{"gpu": "NVIDIA RTX A6000", "host_id": "abc123"}]


def test_no_gpu_is_recorded_as_null(tmp_path):
    path = write_defn(tmp_path, "a")
    seen = []
    run(tmp_path, [str(path), "--no-sync"], run_def=lambda *a: seen.append(compute()) or 0,
        gpu=lambda: None)
    assert seen[0]["gpu"] is None and seen[0]["host_id"]


def test_case0_writes_compute_into_config(tmp_path, monkeypatch):
    monkeypatch.setattr(case0, "RACER_CHECK", Path(__file__))
    monkeypatch.setattr(case0, "model_digest", lambda m: "sha256:x")
    monkeypatch.setattr(case0, "model_template", lambda m: "")
    monkeypatch.setattr(case0, "toolchain", lambda: {})
    monkeypatch.setattr(case0, "run_cell", lambda *a: 0)
    monkeypatch.setenv("TBD_GPU", "NVIDIA L40S")
    monkeypatch.setenv("TBD_HOST_ID", "pod-1")
    assert case0.main(["--model", "llama3.1", "--samples", "1", "--experiment", "e",
                       "--out", str(tmp_path)]) == 0
    cfg = json.loads(next(tmp_path.glob("e/jobs/*/config.json")).read_text())
    assert cfg["compute"] == {"gpu": "NVIDIA L40S", "host_id": "pod-1"}


def test_git_sha_falls_back_to_the_image_build_arg(monkeypatch, tmp_path):
    monkeypatch.setattr(case0, "ROOT", tmp_path)   # not a git checkout
    monkeypatch.setenv("TBD_GIT_SHA", "deadbeef")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    assert case0.git_sha() == "deadbeef"


def test_lifetime_with_the_real_signal_skips_the_rest(tmp_path):
    """The default interrupt is SIGTERM to the runner itself; between jobs the
    runner's own handler takes it, so the process survives and stops the queue."""
    a, b = write_defn(tmp_path, "a"), write_defn(tmp_path, "b")
    order = []

    def slow(path, out, after_job):
        order.append(path.stem)
        time.sleep(0.6)
        return 0

    code = run(tmp_path, [str(a), str(b), "--no-sync", "--max-lifetime", "0.2"], run_def=slow)
    assert code == podrunner.EXIT_LIFETIME
    assert order == ["a"]
