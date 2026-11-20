"""Experiment runner: expansion, resume and stopping, with case0 stubbed out (no model)."""

import json
import os
import re
import signal
import threading
import time
from pathlib import Path

import pytest
import yaml

from tbd.eval import case0, experiment
from tbd.eval.experiment import DefinitionError, expand
from tbd.paths import ROOT

BASE = {
    "experiment": "exp",
    "models": [{"provider": "ollama", "model": "llama3.1"},
               {"model": "qwen2.5-coder:14b", "num_ctx": 16384}],
    "facts": ["none"],
    "tasks": [{"id": "t1", "variants": ["canonical", "blue"]}],
    "modes": ["one-shot", "tool-feedback"],
    "samples": 3, "seed": 42, "temperature": 0.7, "num_ctx": 8192, "max_tokens": 4096,
    "retries": 3, "timeout": 900,
}


def defn(**changes):
    return {**BASE, **changes}


# ---------------------------------------------------------------- expansion

def test_expands_the_cartesian_product_models_outermost():
    jobs = expand(defn())
    assert len(jobs) == 2 * 1 * 2 * 2
    assert [j.job_id for j in jobs[:4]] == [
        "llama3.1-t1-canonical-none-one-shot", "llama3.1-t1-canonical-none-tool-feedback",
        "llama3.1-t1-blue-none-one-shot", "llama3.1-t1-blue-none-tool-feedback"]
    assert jobs[4].job_id == "qwen2.5-coder_14b-t1-canonical-none-one-shot"


def test_per_model_overrides_win_over_top_level_settings():
    jobs = expand(defn())
    assert jobs[0].settings["num_ctx"] == 8192
    assert jobs[4].settings["num_ctx"] == 16384
    assert jobs[4].settings["max_tokens"] == 4096
    argv = jobs[4].argv("exp", Path("/out"))
    assert argv[argv.index("--num-ctx") + 1] == "16384"
    assert argv[-4:] == ["--experiment", "exp", "--out", "/out"]


def test_idle_timeout_reaches_case0_and_its_expected_config():
    job = expand(defn(idle_timeout=240))[0]
    argv = job.argv("exp", Path("/out"))
    assert argv[argv.index("--idle-timeout") + 1] == "240"
    assert experiment.expected_config(job)["idle_timeout_s"] == 240


def test_variants_all_is_every_folder_with_a_prompt_canonical_first():
    jobs = expand(defn(tasks=[{"id": "t1", "variants": "all"}], models=["llama3.1"],
                       modes=["one-shot"]))
    on_disk = {p.parent.name for p in (ROOT / "tasks" / "t1").glob("*/prompt.txt")}
    assert {j.variant for j in jobs} == on_disk
    assert jobs[0].variant == "canonical"
    # t2 has a NOTICE.md beside its variant folders; only folders with a prompt count
    t2 = expand(defn(tasks=["t2"], models=["llama3.1"], modes=["one-shot"]))
    assert "NOTICE.md" not in {j.variant for j in t2}


def test_example_t1_baseline_is_36_jobs():
    _, jobs = experiment.load(ROOT / "experiments.d" / "t1-baseline.yaml")
    assert len(jobs) == 36
    assert {j.model for j in jobs} == {"llama3.1", "qwen2.5-coder", "qwen2.5-coder:14b"}


@pytest.mark.parametrize("changes, message", [
    ({"temperatures": [0.2, 0.7]}, "single values"),
    ({"modes": ["agentic"]}, "unknown mode"),
    ({"facts": ["some"]}, "unknown condition"),
    ({"tasks": [{"id": "t1", "variants": ["nope"]}]}, "no task spec"),
    ({"models": [{"model": "a", "provider": "vllm"}]}, "provider"),
    ({"models": [{"model": "a", "num_ctx": 1}, {"model": "a"}]}, "share the folder"),
    ({"experiment": "../up"}, "folder name"),
    ({"models": []}, "empty"),
    ({"think": "maybe"}, "case0 rejects"),
])
def test_rejects_bad_definitions(changes, message):
    with pytest.raises(DefinitionError, match=message):
        expand(defn(**changes))


def test_job_naming_matches_case0(tmp_path, monkeypatch):
    """Run case0's own main() up to the job folder, with the model and grader stubbed."""
    monkeypatch.setattr(case0, "RACER_CHECK", Path(__file__))   # any existing file
    monkeypatch.setattr(case0, "model_digest", lambda m: "sha256:x")
    monkeypatch.setattr(case0, "model_template", lambda m: "")
    monkeypatch.setattr(case0, "toolchain", lambda: {})
    monkeypatch.setattr(case0, "run_cell", lambda *a: 0)
    for job in expand(defn()):
        assert case0.main(job.argv("exp", tmp_path)) == 0
        assert (tmp_path / "exp" / "jobs" / job.job_id / "config.json").exists()
        cfg = json.loads((tmp_path / "exp" / "jobs" / job.job_id / "config.json").read_text())
        want = experiment.expected_config(job)
        assert {k: cfg[k] for k in want} == want
    assert len(list((tmp_path / "exp" / "jobs").iterdir())) == 8


# ---------------------------------------------------------------- running

def write_job(exp_dir, job_id, status, config=None, samples_done=3):
    d = exp_dir / "jobs" / job_id
    d.mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(config or {}))
    if status:
        (d / "metrics.json").write_text(json.dumps(
            {"status": status, "samples_planned": 3, "samples_done": samples_done}))


class FakeCase0:
    """Stands in for subprocess_job: writes the job folder case0 would, with a status."""

    def __init__(self, exp_dir, statuses=None, on_call=None, assembles=lambda job_id: True):
        self.exp_dir, self.statuses, self.on_call = exp_dir, statuses or {}, on_call
        self.assembles = assembles
        self.calls, self.labels = [], []

    def __call__(self, argv, stop, label=None):
        ns = case0.build_parser().parse_args(argv)
        job_id = experiment.job_id(ns.model, ns.task, ns.variant, ns.facts, ns.mode)
        assert not (self.exp_dir / "jobs" / job_id).exists(), "would overwrite"
        self.calls.append(job_id)
        self.labels.append(label)
        status = self.statuses.get(job_id, "completed")
        write_job(self.exp_dir, job_id, status)
        (self.exp_dir / "jobs" / job_id / "attempts.jsonl").write_text(
            json.dumps({"assemble": {"ok": False}}) + "\n"
            + json.dumps({"assemble": {"ok": self.assembles(job_id)}}) + "\n")
        if self.on_call:
            self.on_call(job_id)
        return 0 if status == "completed" else -2


def setup(tmp_path, **changes):
    path = tmp_path / "def.yaml"
    path.write_text(yaml.safe_dump(defn(**changes)))
    return path, tmp_path / "out", tmp_path / "out" / "exp"


def no_preflight(models):
    return None


def test_dry_run_lists_jobs_and_runs_nothing(tmp_path, capsys):
    path, out, exp_dir = setup(tmp_path)
    write_job(exp_dir, "llama3.1-t1-canonical-none-one-shot", "completed")
    fake = FakeCase0(exp_dir)
    assert experiment.main([str(path), "--dry-run", "--out", str(out)], run_job=fake,
                           preflight=lambda m: pytest.fail("dry run checked Ollama")) == 0
    text = capsys.readouterr().out
    assert "8 jobs, 7 to run" in text
    assert re.search(r"llama3.1-t1-canonical-none-one-shot +done", text)
    assert fake.calls == []
    assert not (exp_dir / "experiment.yaml").exists()


def test_runs_all_jobs_records_definition_and_prints_report(tmp_path, capsys):
    path, out, exp_dir = setup(tmp_path)
    fake = FakeCase0(exp_dir)
    assert experiment.main([str(path), "--out", str(out)], run_job=fake,
                           preflight=no_preflight) == 0
    assert len(fake.calls) == 8
    assert (exp_dir / "experiment.yaml").read_bytes() == path.read_bytes()
    listed = json.loads((exp_dir / "jobs.json").read_text())
    assert [j["job_id"] for j in listed] == fake.calls
    assert "# Grid report" in capsys.readouterr().out


def test_only_selects_by_glob_or_substring(tmp_path):
    path, out, exp_dir = setup(tmp_path)
    fake = FakeCase0(exp_dir)
    experiment.main([str(path), "--out", str(out), "--only", "llama3.1-*", "--only", "blue"],
                    run_job=fake, preflight=no_preflight)
    assert fake.calls == ["llama3.1-t1-canonical-none-one-shot",
                          "llama3.1-t1-canonical-none-tool-feedback",
                          "llama3.1-t1-blue-none-one-shot", "llama3.1-t1-blue-none-tool-feedback",
                          "qwen2.5-coder_14b-t1-blue-none-one-shot",
                          "qwen2.5-coder_14b-t1-blue-none-tool-feedback"]


def test_resume_skips_completed_jobs(tmp_path):
    path, out, exp_dir = setup(tmp_path)
    first = FakeCase0(exp_dir)
    experiment.main([str(path), "--out", str(out), "--only", "llama3.1"], run_job=first,
                    preflight=no_preflight)
    again = FakeCase0(exp_dir)
    assert experiment.main([str(path), "--out", str(out)], run_job=again,
                           preflight=no_preflight) == 0
    assert set(again.calls).isdisjoint(first.calls)
    assert len(first.calls) + len(again.calls) == 8
    assert not list(exp_dir.glob("experiment.*.yaml")), "same definition: no second copy"


def test_completed_job_with_other_settings_is_a_mismatch_not_done(tmp_path, capsys):
    path, out, exp_dir = setup(tmp_path)
    job = expand(defn())[0]
    write_job(exp_dir, job.job_id, "completed",
              {**experiment.expected_config(job), "num_ctx": 32768})
    fake = FakeCase0(exp_dir)
    assert experiment.main([str(path), "--out", str(out)], run_job=fake,
                           preflight=no_preflight) == 1
    assert job.job_id not in fake.calls and len(fake.calls) == 7
    assert "mismatch: llama3.1-t1-canonical-none-one-shot (completed with other settings: " \
           "num_ctx=32768" in capsys.readouterr().out


@pytest.mark.parametrize("status, detail", [
    ("interrupted", "interrupted, 1 of 3 samples"),
    ("failed", "failed, 1 of 3 samples"),
    ("running", "running, stale: no heartbeat"),
    (None, "no metrics.json"),
])
def test_unfinished_job_folders_are_reported_and_left_alone(tmp_path, capsys, status, detail):
    path, out, exp_dir = setup(tmp_path)
    job_id = "llama3.1-t1-blue-none-one-shot"
    write_job(exp_dir, job_id, status, samples_done=1)
    before = {p: p.read_bytes() for p in (exp_dir / "jobs" / job_id).iterdir()}
    fake = FakeCase0(exp_dir)
    assert experiment.main([str(path), "--out", str(out)], run_job=fake,
                           preflight=no_preflight) == 1
    assert job_id not in fake.calls and len(fake.calls) == 7
    assert {p: p.read_bytes() for p in (exp_dir / "jobs" / job_id).iterdir()} == before
    text = capsys.readouterr().out
    assert f"incomplete: {job_id} ({detail}" in text
    assert "can't be resumed" in text


def test_changed_definition_is_saved_beside_never_over(tmp_path):
    path, out, exp_dir = setup(tmp_path)
    experiment.main([str(path), "--out", str(out), "--only", "llama3.1-t1-canonical"],
                    run_job=FakeCase0(exp_dir), preflight=no_preflight)
    original = (exp_dir / "experiment.yaml").read_bytes()
    path.write_text(yaml.safe_dump(defn(facts=["none"], samples=3, modes=["one-shot"])))
    experiment.main([str(path), "--out", str(out)], run_job=FakeCase0(exp_dir),
                    preflight=no_preflight)
    assert (exp_dir / "experiment.yaml").read_bytes() == original
    assert len(list(exp_dir.glob("experiment.*.yaml"))) == 1
    assert len(list(exp_dir.glob("jobs.*.json"))) == 1


def test_failed_job_is_reported_and_the_next_one_runs(tmp_path, capsys):
    path, out, exp_dir = setup(tmp_path)
    first = "llama3.1-t1-canonical-none-one-shot"
    fake = FakeCase0(exp_dir, statuses={first: "failed"})
    assert experiment.main([str(path), "--out", str(out)], run_job=fake,
                           preflight=no_preflight) == 1
    assert len(fake.calls) == 8
    assert f"failed: {first} (exit -2, status failed)" in capsys.readouterr().out


def test_interrupted_job_stops_the_loop(tmp_path, capsys):
    path, out, exp_dir = setup(tmp_path)
    second = "llama3.1-t1-canonical-none-tool-feedback"
    fake = FakeCase0(exp_dir, statuses={second: "interrupted"})
    assert experiment.main([str(path), "--out", str(out)], run_job=fake,
                           preflight=no_preflight) == 130
    assert fake.calls == ["llama3.1-t1-canonical-none-one-shot", second]
    text = capsys.readouterr().out
    assert "not run: llama3.1-t1-blue-none-one-shot" in text
    assert "rerun to resume" in text


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_signal_during_a_job_stops_after_it(tmp_path, signum):
    path, out, exp_dir = setup(tmp_path)
    fake = FakeCase0(exp_dir, on_call=lambda job_id: os.kill(os.getpid(), signum))
    previous = signal.getsignal(signum)
    assert experiment.main([str(path), "--out", str(out)], run_job=fake,
                           preflight=no_preflight) == 130
    assert len(fake.calls) == 1
    assert signal.getsignal(signum) == previous, "handlers restored"


def test_stop_forwards_the_first_signal_to_the_running_job():
    sent = []

    class Child:
        def poll(self):
            return None

        def send_signal(self, s):
            sent.append(s)

    stop = experiment.Stop()
    stop.children = {Child(), Child()}
    stop(signal.SIGINT, None)
    stop(signal.SIGINT, None)
    assert sent == [signal.SIGINT, signal.SIGINT] and stop


def test_ollama_problem_stops_before_making_a_job_folder(tmp_path, capsys):
    path, out, exp_dir = setup(tmp_path)
    fake = FakeCase0(exp_dir)
    assert experiment.main([str(path), "--out", str(out)], run_job=fake,
                           preflight=lambda models: "not pulled in Ollama: llama3.1") == 1
    assert fake.calls == [] and not (exp_dir / "jobs").exists()
    # and mid-run: Ollama goes away after the first job
    checks = iter([None, None, "Ollama not reachable"])
    fake = FakeCase0(exp_dir)
    experiment.main([str(path), "--out", str(out)], run_job=fake,
                    preflight=lambda models: next(checks, "Ollama not reachable"))
    assert len(fake.calls) == 1


# ---------------------------------------------------------------- random seeds

def test_random_seeds_reaches_case0_and_its_expected_config():
    job = expand(defn(random_seeds=10))[0]
    argv = job.argv("exp", Path("/out"))
    assert argv[argv.index("--random-seeds") + 1] == "10"
    assert experiment.expected_config(job)["random_seeds"] == 10
    assert experiment.expected_config(expand(defn())[0])["random_seeds"] is None


# ---------------------------------------------------------------- syntax fixer

def test_fix_syntax_reaches_case0_as_on_off_and_its_expected_config():
    job = expand(defn(fix_syntax=True))[0]
    argv = job.argv("exp", Path("/out"))
    assert argv[argv.index("--fix-syntax") + 1] == "on"
    assert experiment.expected_config(job)["fix_syntax"] is True
    assert experiment.expected_config(expand(defn())[0])["fix_syntax"] is False


def test_job_recorded_before_the_fixer_counts_as_fixer_off(tmp_path):
    """A config.json without fix_syntax ran without the fixer."""
    exp_dir = tmp_path / "exp"
    job = expand(defn())[0]
    old = {k: v for k, v in experiment.expected_config(job).items() if k != "fix_syntax"}
    write_job(exp_dir, job.job_id, "completed", old)
    assert experiment.job_state(job, exp_dir) == ("done", "")
    fixed = expand(defn(fix_syntax=True))[0]
    state, detail = experiment.job_state(fixed, exp_dir)
    assert state == "mismatch" and "fix_syntax=False (definition: True)" in detail


# ---------------------------------------------------------------- gate

GATED = {"tasks": [{"id": "t1", "variants": ["canonical", "blue", "split-lr"]}],
         "gate": ["t1/canonical", "t1/blue"], "models": ["llama3.1"],
         "facts": ["none", "contributed"], "modes": ["tool-feedback"]}


def test_gate_jobs_come_first_and_the_rest_wait_on_their_configuration():
    jobs = expand(defn(**GATED))
    assert [j.job_id for j in jobs] == [
        "llama3.1-t1-canonical-none-tool-feedback", "llama3.1-t1-blue-none-tool-feedback",
        "llama3.1-t1-canonical-contributed-tool-feedback",
        "llama3.1-t1-blue-contributed-tool-feedback",
        "llama3.1-t1-split-lr-none-tool-feedback",
        "llama3.1-t1-split-lr-contributed-tool-feedback"]
    assert jobs[4].after == ["llama3.1-t1-canonical-none-tool-feedback",
                             "llama3.1-t1-blue-none-tool-feedback"]
    assert all(j.gate and not j.after for j in jobs[:4])


@pytest.mark.parametrize("gate, message", [
    (["t1/pal"], "not one of"), (["t1/canonical", "t1/blue", "t1/split-lr"], "not all"),
    ([], "not all")])
def test_rejects_bad_gates(gate, message):
    with pytest.raises(DefinitionError, match=message):
        expand(defn(**{**GATED, "gate": gate}))


@pytest.mark.parametrize("parallel", [0, -1, "2", True])
def test_rejects_bad_parallel(parallel):
    with pytest.raises(DefinitionError, match="parallel"):
        expand(defn(parallel=parallel))


def test_configuration_whose_gate_jobs_never_assembled_is_gated(tmp_path, capsys):
    path, out, exp_dir = setup(tmp_path, **GATED)
    # Facts "none": nothing assembles. Facts "contributed": blue assembles once.
    fake = FakeCase0(exp_dir, assembles=lambda job_id: job_id.endswith(
        "blue-contributed-tool-feedback"))
    assert experiment.main([str(path), "--out", str(out)], run_job=fake,
                           preflight=no_preflight) == 0
    assert "llama3.1-t1-split-lr-none-tool-feedback" not in fake.calls
    assert fake.calls[-1] == "llama3.1-t1-split-lr-contributed-tool-feedback"
    assert not (exp_dir / "jobs" / "llama3.1-t1-split-lr-none-tool-feedback").exists()
    text = capsys.readouterr().out
    assert "gated: llama3.1-t1-split-lr-none-tool-feedback (no attempt of " in text


def test_gate_is_reread_from_disk_on_resume(tmp_path):
    path, out, exp_dir = setup(tmp_path, **GATED)
    experiment.main([str(path), "--out", str(out), "--only", "canonical", "--only", "blue"],
                    run_job=FakeCase0(exp_dir, assembles=lambda j: "-none-" in j),
                    preflight=no_preflight)
    again = FakeCase0(exp_dir)
    experiment.main([str(path), "--out", str(out)], run_job=again, preflight=no_preflight)
    assert again.calls == ["llama3.1-t1-split-lr-none-tool-feedback"]


def test_gate_job_that_failed_leaves_its_configuration_not_run(tmp_path, capsys):
    path, out, exp_dir = setup(tmp_path, **GATED)
    failed = "llama3.1-t1-blue-none-tool-feedback"
    fake = FakeCase0(exp_dir, statuses={failed: "failed"})
    assert experiment.main([str(path), "--out", str(out)], run_job=fake,
                           preflight=no_preflight) == 1
    assert "llama3.1-t1-split-lr-none-tool-feedback" not in fake.calls
    assert "llama3.1-t1-split-lr-contributed-tool-feedback" in fake.calls
    assert (f"not run: llama3.1-t1-split-lr-none-tool-feedback (gate job {failed} failed)"
            in capsys.readouterr().out)


def test_dry_run_marks_jobs_behind_a_gate(tmp_path, capsys):
    path, out, exp_dir = setup(tmp_path, **GATED)
    experiment.main([str(path), "--dry-run", "--out", str(out)], run_job=FakeCase0(exp_dir))
    assert re.search(r"split-lr-none-tool-feedback +run if a gate job assembles",
                     capsys.readouterr().out)


# ---------------------------------------------------------------- parallel

def test_parallel_runs_jobs_side_by_side_and_labels_them(tmp_path):
    path, out, exp_dir = setup(tmp_path, parallel=3)
    lock, live, peak = threading.Lock(), [0], [0]

    def slow(job_id):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.05)
        with lock:
            live[0] -= 1

    fake = FakeCase0(exp_dir, on_call=slow)
    assert experiment.main([str(path), "--out", str(out)], run_job=fake,
                           preflight=no_preflight) == 0
    assert len(fake.calls) == 8 and peak[0] == 3
    assert sorted(fake.labels) == sorted(fake.calls)
    # --parallel overrides the definition
    (tmp_path / "b").mkdir()
    path2, out2, exp2 = setup(tmp_path / "b", parallel=3)
    one = FakeCase0(exp2)
    experiment.main([str(path2), "--out", str(out2), "--parallel", "1"], run_job=one,
                    preflight=no_preflight)
    assert one.labels == [None] * 8


def test_parallel_gate_holds_back_only_its_own_configuration(tmp_path):
    path, out, exp_dir = setup(tmp_path, parallel=4, **GATED)
    order = []
    fake = FakeCase0(exp_dir, on_call=order.append)
    assert experiment.main([str(path), "--out", str(out)], run_job=fake,
                           preflight=no_preflight) == 0
    assert set(order[:4]) == {j.job_id for j in expand(defn(**GATED)) if j.gate}
    assert len(order) == 6
