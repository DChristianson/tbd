"""Case 0 eval script: code extraction and Task 1's grading (no model needed)."""

import json
import os
import signal
import socket
import time
from datetime import datetime

import pytest

from tbd.eval import case0, tasks
from tbd.eval.checks import accepted_substitutions
from tbd.eval.checks import grade_program as _grade_program
from tbd.eval.grading import Grade, extract_code, property_names
from tbd.eval.grading import categorize as _categorize
from tbd.paths import RACER_CHECK

SPEC = tasks.load("t1", "canonical")
REFERENCE = SPEC.reference
CHECK = (SPEC.dir / "check.toml").read_text()
RED = SPEC.module.RED


def categorize(report, names=None):
    return _categorize(report, names, SPEC.informational)


def grade_program(code, workdir):
    return _grade_program(SPEC, code, workdir)


def _bg_failure(color, prop="background"):
    return {"passed": False, "unexpected": [{"power_on": "zero", "failures": [
        {"property": prop, "frame": 2, "line": 40, "clock": 68,
         "observed": f"color ${color}", "required": "color $42"}]}]}


def test_extracts_longest_asm_block():
    text = "Here:\n```\nnot code\n```\n```asm\n  processor 6502\n  lda #$42\n  sta COLUBK\n```\n"
    assert "sta COLUBK" in extract_code(text)


def test_block_without_instructions_is_still_code():
    """Judging whether it's 6502 is DASM's job, not the extractor's (qwen3.5's
    dry reply was one asm block of made-up directives, logged as no_code)."""
    text = "```asm\n.include \"vcs.h\"\n.setBG, 1\n```"
    assert extract_code(text) == '.include "vcs.h"\n.setBG, 1\n'


def test_prefers_asm_tagged_block():
    text = "```\na much longer untagged block of prose\nand more\n```\n```asm\nlda #1\n```"
    assert extract_code(text) == "lda #1\n"


def test_ignores_think_and_prose_only():
    assert extract_code("<think>```asm\nlda #1\n```</think>no code here") is None
    assert extract_code("just words") is None


def test_categorizes_structured_failures():
    report = {"passed": False, "unexpected": [{"power_on": "zero", "failures": [
        {"property": "frame_length", "frame": 2, "observed": "263 lines", "required": "262 lines"},
        {"property": "blanking", "frame": 2, "line": 3, "clock": 68,
         "observed": "color $42", "required": "no color outside lines 40-231"}]}]}
    g = categorize(report)
    assert g.category == "timing"
    assert g.failed == ["blanking", "frame_length"]
    assert "263 lines" in g.detail


def test_unknown_opcode_is_its_own_category():
    report = {"passed": False, "unexpected": [{"power_on": "zero", "failures": [],
              "error": "simulator stopped at $f003: UnknownOpcode { pc: 61442, opcode: 171 }"}]}
    assert categorize(report).category == "unsupported_opcode"


def test_another_red_gets_a_retry_but_not_another_hue():
    def subs(color):
        return accepted_substitutions(_bg_failure(color), CHECK, {"background": RED})
    assert subs("44") == {"background": 0x44}
    assert subs("46") == {"background": 0x46}
    assert subs("84") == {}
    # Same hue, outside the accepted $42-$46 range.
    assert subs("40") == {}
    assert subs("48") == {}


@pytest.mark.skipif(not RACER_CHECK.exists(), reason="racer-check not built")
def test_reference_grades_as_pass(tmp_path):
    asm, grade = grade_program(_ref(), tmp_path)
    assert asm["ok"]
    assert grade.ok, grade.detail
    assert grade.accepted == {}


@pytest.mark.skipif(not RACER_CHECK.exists(), reason="racer-check not built")
def test_other_red_is_accepted(tmp_path):
    src = _ref()
    _, grade = grade_program(src.replace("lda #$42", "lda #$46"), tmp_path)
    assert grade.ok, grade.detail
    assert grade.accepted == {"background": 0x46}


@pytest.mark.skipif(not RACER_CHECK.exists(), reason="racer-check not built")
def test_red_outside_the_range_fails(tmp_path):
    src = _ref()
    _, grade = grade_program(src.replace("lda #$42", "lda #$48"), tmp_path)
    assert not grade.ok and grade.failed == ["background"]


@pytest.mark.skipif(not RACER_CHECK.exists(), reason="racer-check not built")
def test_assemble_error_is_categorized(tmp_path):
    _, grade = grade_program("  processor 6502\n  bogus #1\n", tmp_path)
    assert grade.category == "assemble_error"


@pytest.mark.skipif(not RACER_CHECK.exists(), reason="racer-check not built")
def test_regrade_writes_a_new_folder_and_leaves_the_attempt_alone(tmp_path, monkeypatch):
    import json
    from tbd.eval import regrade as rg
    monkeypatch.setattr(rg, "ROOT", tmp_path)
    attempt = tmp_path / "attempt-1"
    attempt.mkdir()
    (attempt / "response.md").write_text("```asm\n.setBG, 1\n```")
    original = {"grade": {"ok": False, "category": "no_code", "detail": "", "failed": [], "accepted": {}}}
    (attempt / "attempt.json").write_text(json.dumps(original))
    before = sorted(p.name for p in attempt.iterdir())
    r = rg.regrade(attempt)
    assert r["original_grade"]["category"] == "no_code"
    assert r["grade"]["category"] == "assemble_error"
    after = sorted(p.name for p in attempt.iterdir())
    assert [n for n in after if n not in before] == [n for n in after if n.startswith("regrade-")]
    assert json.loads((attempt / "attempt.json").read_text()) == original


def test_property_names_from_check_file():
    names = property_names(CHECK)
    assert names == ["frame_length", "vsync_span", "vsync", "blanking", "background"]


def test_properties_listed_for_every_check_file_property():
    report = {"passed": False, "unexpected": [{"power_on": "zero", "failures": [
        {"property": "vsync_span", "frame": 2, "line": 0, "clock": 200,
         "observed": "sync began here and lasted 300 clocks",
         "required": "sync from line 0, lasting 456-912 clocks"}]}]}
    g = categorize(report, property_names(CHECK))
    assert g.category == "timing"
    assert g.properties == {"frame_length": "pass", "vsync_span": "fail", "vsync": "pass",
                            "blanking": "pass", "background": "pass"}
    assert not g.near_miss  # decided by the relaxed tier, which categorize doesn't run


def test_crash_is_not_a_near_miss():
    report = {"passed": False, "unexpected": [{"power_on": "zero", "failures": [],
              "error": "simulator stopped at $f003: UnknownOpcode { pc: 61442, opcode: 171 }"}]}
    g = categorize(report, ["frame_length"])
    assert not g.near_miss and g.properties == {}


def test_strict_vsync_is_informational():
    report = {"passed": False, "unexpected": [{"power_on": "zero", "failures": [
        {"property": "vsync", "frame": 2, "line": 0, "clock": 45,
         "observed": "sync began here and lasted 654 clocks",
         "required": "sync from line 0 for exactly 3 lines (684 clocks)"}]}]}
    names = property_names(CHECK)
    g = categorize(report, names)
    assert g.ok and g.category == "pass"
    assert g.informational_failed == ["vsync"] and g.failed == []
    assert g.properties["vsync"] == "fail" and g.properties["vsync_span"] == "pass"
    assert not g.near_miss


def test_informational_failure_does_not_hide_a_graded_one():
    report = {"passed": False, "unexpected": [{"power_on": "zero", "failures": [
        {"property": "vsync", "frame": 2, "observed": "x", "required": "y"},
        {"property": "background", "frame": 2, "line": 40, "clock": 68,
         "observed": "color $84", "required": "color $42"}]}]}
    g = categorize(report, property_names(CHECK))
    assert not g.ok and g.failed == ["background"] and g.category == "output_mismatch"
    assert accepted_substitutions(report, CHECK, {"background": RED}) == {}


def test_color_retry_happens_alongside_other_failures():
    """A $44 background is accepted even when the frame is also wrong; the
    color check is independent of the other properties."""
    report = {"passed": False, "unexpected": [{"power_on": "zero", "failures": [
        {"property": "background", "frame": 2, "line": 40, "clock": 68,
         "observed": "color $44", "required": "color $42"},
        {"property": "blanking", "frame": 2, "line": 39, "clock": 68,
         "observed": "color $44", "required": "no color"},
        {"property": "frame_length", "frame": 2, "observed": "260 lines",
         "required": "262 lines"}]}]}
    assert accepted_substitutions(report, CHECK, {"background": RED}) == {"background": 0x44}


def test_anthropic_default_max_tokens(monkeypatch):
    def fake_main_args(argv):
        p = case0.build_parser()
        return p.parse_args(argv)
    args = fake_main_args(["--provider", "anthropic", "--model", "claude-haiku-5-5"])
    assert case0.effective_max_tokens(args) == 64000
    args = fake_main_args(["--model", "llama3.1"])
    assert case0.effective_max_tokens(args) == 4096
    args = fake_main_args(["--provider", "anthropic", "--model", "x", "--max-tokens", "1000"])
    assert case0.effective_max_tokens(args) == 1000


def _ref():
    return REFERENCE.read_text()


# One vertical-blank and one overscan line short: 260 lines, picture on 39-230.
def _shifted(src):
    return src.replace("    ldx #37\n", "    ldx #36\n", 1).replace("    ldx #29\n", "    ldx #28\n", 1)


@pytest.mark.skipif(not RACER_CHECK.exists(), reason="racer-check not built")
def test_shifted_frame_is_a_near_miss(tmp_path):
    _, g = grade_program(_shifted(_ref()).replace("lda #$42", "lda #$44"), tmp_path)
    assert not g.ok
    assert set(g.failed) == {"frame_length", "blanking", "background"}
    assert g.relaxed["passed"] and g.relaxed["accepted"] == {"picture": 0x44}
    assert g.near_miss and g.to_dict()["near_miss"]


@pytest.mark.skipif(not RACER_CHECK.exists(), reason="racer-check not built")
def test_shifted_frame_in_the_wrong_color_is_not(tmp_path):
    _, g = grade_program(_shifted(_ref()).replace("lda #$42", "lda #$84"), tmp_path)
    assert not g.ok and not g.relaxed["passed"] and not g.near_miss
    assert "picture" in g.relaxed["failed"]


@pytest.mark.skipif(not RACER_CHECK.exists(), reason="racer-check not built")
def test_a_pass_runs_no_relaxed_tier(tmp_path):
    _, g = grade_program(_ref(), tmp_path)
    assert g.ok and g.relaxed is None and not g.near_miss


def test_truncated_feedback_does_not_claim_it_assembled():
    msg = case0.feedback({"ok": False, "errors": [], "warnings": []}, Grade(False, "truncated"))
    assert "length limit" in msg
    assert "assembled" not in msg


def test_refusal_feedback_does_not_claim_it_assembled():
    msg = case0.feedback({"ok": False, "errors": [], "warnings": []}, Grade(False, "refusal"))
    assert "assembled" not in msg


def test_truncated_reply_is_cut_before_it_is_echoed():
    loop = "```asm\n" + "    sta WSYNC  ; Wait for horizontal blank\n" * 2000
    echoed = case0.echoed_reply({}, loop, Grade(False, "truncated"))
    assert len(echoed) < case0.TRUNCATED_ECHO_CHARS + 100
    assert echoed.startswith(loop[:100])
    assert echoed.endswith("cut off at the output length limit]")


def test_other_replies_are_echoed_whole():
    text = "x" * (case0.TRUNCATED_ECHO_CHARS * 2)
    assert case0.echoed_reply({}, text, Grade(False, "assemble_error")) == text
    blocks = [{"type": "text", "text": "hi"}]
    assert case0.echoed_reply({"assistant_content": blocks}, "hi", Grade(False, "no_code")) is blocks


# ---------------------------------------------------------------- job lifecycle

def _lifecycle(tmp_path, monkeypatch, run_job):
    """case0.main's argv and job folder, with the model, grader and run_job stubbed."""
    monkeypatch.setattr(case0, "RACER_CHECK", tmp_path)          # exists
    monkeypatch.setattr(case0, "model_digest", lambda m: "d" * 64)
    monkeypatch.setattr(case0, "model_template", lambda m: "")
    monkeypatch.setattr(case0, "run_job", run_job)
    argv = ["--model", "stub", "--samples", "2", "--out", str(tmp_path / "x"),
            "--experiment", "e", "--timeout", "30"]
    return argv, tmp_path / "x" / "e" / "jobs" / "stub-t1-canonical-none-one-shot"


def _metrics(job_dir):
    return json.loads((job_dir / "metrics.json").read_text())


def test_sigterm_ends_the_job_as_interrupted(tmp_path, monkeypatch):
    before = signal.getsignal(signal.SIGTERM)

    def run_job(args, sample, job_dir, log, on_attempt=None):
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(5)   # the handler raises before this returns
        raise AssertionError("SIGTERM should have ended the job")

    argv, job_dir = _lifecycle(tmp_path, monkeypatch, run_job)
    with pytest.raises(case0.Terminated):
        case0.main(argv)
    m = _metrics(job_dir)
    assert m["status"] == "interrupted" and m["samples_done"] == 0
    assert signal.getsignal(signal.SIGTERM) is before   # handler restored


def test_metrics_heartbeat_after_every_attempt(tmp_path, monkeypatch):
    seen = []

    def run_job(args, sample, job_dir, log, on_attempt=None):
        for _ in range(2):
            time.sleep(0.1)
            on_attempt()
            seen.append(_metrics(job_dir))
        return {"passed": True, "attempts": 2, "first_category": "timing", "near_miss": False}

    argv, job_dir = _lifecycle(tmp_path, monkeypatch, run_job)
    assert case0.main(argv) == 0
    assert len(seen) == 4
    for m in seen:
        assert m["status"] == "running"
        assert m["pid"] == os.getpid() and m["host"] == socket.gethostname()
        assert datetime.fromisoformat(m["updated_at"]).utcoffset().total_seconds() == 0
    # written within a sample, before its result is in, and the clock moves
    assert seen[0]["samples_done"] == seen[1]["samples_done"] == 0
    assert seen[1]["wall_s"] > seen[0]["wall_s"]
    final = _metrics(job_dir)
    assert final["status"] == "completed" and final["samples_done"] == 2
    assert final["passed"] == 2 and final["attempts_total"] == 4
    assert {"pid", "host", "updated_at"} <= final.keys()
