"""Case 0 eval script: code extraction and grading (no model needed)."""

from pathlib import Path

import pytest

from tbd.eval import case0
from tbd.eval.case0 import Grade, categorize, extract_code, grade_program, red_retry_color

FIXTURES = Path(__file__).parent / "fixtures"


def test_extracts_longest_asm_block():
    text = "Here:\n```\nnot code\n```\n```asm\n  processor 6502\n  lda #$42\n  sta COLUBK\n```\n"
    assert "sta COLUBK" in extract_code(text)


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
    assert red_retry_color(bg("84")) is None


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
def test_assemble_error_is_categorized(tmp_path):
    _, grade = grade_program("  processor 6502\n  bogus #1\n", tmp_path)
    assert grade.category == "assemble_error"
