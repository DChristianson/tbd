"""Case 0 eval script: code extraction and grading (no model needed)."""

from pathlib import Path

import pytest

from tbd.eval import case0
from tbd.eval.case0 import Grade, categorize, extract_code, grade_program, red_retry_color

FIXTURES = Path(__file__).parent / "fixtures"


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
    def bg(color):
        return Grade(False, "output_mismatch", failed=["background"],
                     detail=f"background: frame 2, line 40, clock 68: color ${color}; required color $42")
    assert red_retry_color(bg("44")) == 0x44
    assert red_retry_color(bg("46")) == 0x46
    assert red_retry_color(bg("84")) is None
    # Same hue, outside the accepted $42-$46 range.
    assert red_retry_color(bg("40")) is None
    assert red_retry_color(bg("48")) is None


@pytest.mark.skipif(not case0.RACER_CHECK.exists(), reason="racer-check not built")
def test_reference_grades_as_pass(tmp_path):
    asm, grade = grade_program((FIXTURES / "red_background.asm").read_text()
                               .replace('include "vendor/atari2600/', 'include "'), tmp_path)
    assert asm["ok"]
    assert grade.ok, grade.detail
    assert grade.color == 0x42


@pytest.mark.skipif(not case0.RACER_CHECK.exists(), reason="racer-check not built")
def test_other_red_is_accepted(tmp_path):
    src = (FIXTURES / "red_background.asm").read_text().replace('include "vendor/atari2600/', 'include "')
    _, grade = grade_program(src.replace("lda #$42", "lda #$46"), tmp_path)
    assert grade.ok, grade.detail
    assert grade.color == 0x46


@pytest.mark.skipif(not case0.RACER_CHECK.exists(), reason="racer-check not built")
def test_red_outside_the_range_fails(tmp_path):
    src = (FIXTURES / "red_background.asm").read_text().replace('include "vendor/atari2600/', 'include "')
    _, grade = grade_program(src.replace("lda #$42", "lda #$48"), tmp_path)
    assert not grade.ok and grade.failed == ["background"]


@pytest.mark.skipif(not case0.RACER_CHECK.exists(), reason="racer-check not built")
def test_assemble_error_is_categorized(tmp_path):
    _, grade = grade_program("  processor 6502\n  bogus #1\n", tmp_path)
    assert grade.category == "assemble_error"


@pytest.mark.skipif(not case0.RACER_CHECK.exists(), reason="racer-check not built")
def test_regrade_writes_a_new_folder_and_leaves_the_attempt_alone(tmp_path, monkeypatch):
    import json
    from tbd.eval import regrade as rg
    monkeypatch.setattr(rg, "ROOT", tmp_path)
    attempt = tmp_path / "attempt-1"
    attempt.mkdir()
    (attempt / "response.md").write_text("```asm\n.setBG, 1\n```")
    original = {"grade": {"ok": False, "category": "no_code", "detail": "", "failed": [], "color": None}}
    (attempt / "attempt.json").write_text(json.dumps(original))
    before = sorted(p.name for p in attempt.iterdir())
    r = rg.regrade(attempt)
    assert r["original_grade"]["category"] == "no_code"
    assert r["grade"]["category"] == "assemble_error"
    after = sorted(p.name for p in attempt.iterdir())
    assert [n for n in after if n not in before] == [n for n in after if n.startswith("regrade-")]
    assert json.loads((attempt / "attempt.json").read_text()) == original


def test_property_names_from_check_file():
    names = case0.property_names(case0.CHECK_TEMPLATE.read_text())
    assert names == ["frame_length", "vsync_span", "vsync", "blanking", "background"]


def test_single_failure_is_a_near_miss():
    report = {"passed": False, "unexpected": [{"power_on": "zero", "failures": [
        {"property": "vsync_span", "frame": 2, "line": 0, "clock": 200,
         "observed": "sync began here and lasted 300 clocks",
         "required": "sync from line 0, lasting 456-912 clocks"}]}]}
    names = case0.property_names(case0.CHECK_TEMPLATE.read_text())
    g = categorize(report, names)
    assert g.category == "timing" and g.near_miss
    assert g.properties == {"frame_length": "pass", "vsync_span": "fail", "vsync": "pass",
                            "blanking": "pass", "background": "pass"}
    assert g.to_dict()["near_miss"] is True


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
    names = case0.property_names(case0.CHECK_TEMPLATE.read_text())
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
    g = categorize(report, case0.property_names(case0.CHECK_TEMPLATE.read_text()))
    assert not g.ok and g.failed == ["background"] and g.category == "output_mismatch"
    assert g.near_miss and red_retry_color(g) is None
