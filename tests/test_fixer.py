"""Syntax fixer: each rule, programs it must leave alone, and real failed attempts
that assemble once fixed.

tests/fixtures/fixer/ holds programs models wrote that DASM rejected, copied
unchanged from saved runs (experiments/_collected/assemble-errors-20261119):

    no-processor-no-include.asm  qwen2.5-coder:14b, t1/pal: no processor line, no vcs.h
    col0-directives.asm          qwen3.6:27b, t1/blue: include in column 0, .endrepeat
    indented-label.asm           qwen3.6:27b, t1/canonical: ' Start:' indented
    indented-bare-label.asm      qwen3.6:27b, t1/canonical: ' main_loop' indented, no colon
    p-6502.asm                   qwen3.6:27b, t1/canonical: ':p 6502', dw
"""

import json
from pathlib import Path

import pytest

from tbd.eval import case0, tasks
from tbd.eval.fixer import assemble_with_fixer, fix, map_errors
from tbd.eval.grading import assemble_program
from tbd.paths import RACER_CHECK, TASKS

FIXTURES = Path(__file__).parent / "fixtures" / "fixer"
CORPUS = sorted(FIXTURES.glob("*.asm"))
REFERENCES = sorted(TASKS.glob("*/*/reference.asm"))


def lines(*body: str) -> str:
    return "\n".join(body) + "\n"


HEAD = ("    processor 6502", '    include "vcs.h"', "    org $F000")


def fixed_body(*body: str) -> tuple[list[str], list[str]]:
    """fix() over HEAD + body: (the body lines after fixing, the rules that fired)."""
    f = fix(lines(*HEAD, *body))
    return f.text.splitlines()[len(HEAD):], f.rules


# ---------------------------------------------------------------- rules

def test_column0_instruction_directive_and_macro_are_indented():
    out, rules = fixed_body("lda #0", "sta COLUBK", "include \"macro.h\"", "CLEAN_START",
                            ".include \"vcs.h\"")
    assert out == ["    lda #0", "    sta COLUBK", '    include "macro.h"', "    CLEAN_START",
                   '    .include "vcs.h"']
    assert rules == ["indent_col0"]


def test_column0_symbol_definitions_and_labels_stay():
    out, rules = fixed_body("COLOR = $42", "Start:", "Loop  lda #0", "end equ 5")
    assert out == ["COLOR = $42", "Start:", "Loop  lda #0", "end equ 5"]
    assert rules == []


def test_indented_label_with_colon_moves_to_column0():
    out, rules = fixed_body("  Start:", "    lda #0", "  Loop: dex", "    bne Loop")
    assert out == ["Start:", "    lda #0", "Loop: dex", "    bne Loop"]
    assert rules == ["dedent_label"]


def test_indented_bare_word_moves_only_when_referenced():
    out, rules = fixed_body("    main_loop", "    jmp main_loop", "    VERTICAL_BLANK")
    assert out == ["main_loop", "    jmp main_loop", "    VERTICAL_BLANK"]
    assert rules == ["dedent_label"]


def test_indented_own_macro_is_not_a_label():
    out, rules = fixed_body("    MAC WAIT", "    sta WSYNC", "    ENDM", "    WAIT",
                            "    jmp WAIT")
    assert out[3] == "    WAIT" and rules == []


def test_processor_spellings():
    for line in ("CPU 6502", ":p 6502", "    :p 6502", '.setcpu "6502"', "    .cpu 6502"):
        f = fix(lines(line, '    include "vcs.h"', "    org $F000", "    nop"))
        assert f.text.splitlines()[0] == "    processor 6502", line
        assert f.rules == ["processor_line"], line


def test_missing_processor_is_added_first():
    f = fix(lines('    include "vcs.h"', "    org $F000", "    nop"))
    assert f.text.splitlines()[:2] == ["    processor 6502", '    include "vcs.h"']
    assert f.rules == ["add_processor"]
    assert f.line_map[:2] == [None, 1]


def test_dotted_and_indented_processor_counts():
    assert fix(lines("    .processor 6502", "    nop")).rules == []
    f = fix(lines("processor 6502", "    nop"))
    assert f.text.splitlines()[0] == "    processor 6502" and f.rules == ["indent_col0"]


@pytest.mark.parametrize("before, after", [
    ("    dw Reset", "    .word Reset"),
    ("    .dw Reset, Reset", "    .word Reset, Reset"),
    ("    db $01,$02", "    .byte $01,$02"),
    ("Table .db 1", "Table .byte 1"),
    ("    .res 4", "    ds 4"),
    ("    res 4", "    ds 4"),
    ("    .rept 3", "    repeat 3"),
    ("    .endrepeat", "    repend"),
    ("    .endr", "    repend"),
    ("* = $F000", "    org $F000"),
    ("db $01", "    .byte $01"),
])
def test_foreign_directives(before, after):
    out, rules = fixed_body(before)
    assert out == [after]
    assert "foreign_directive" in rules


def test_dasm_directives_with_a_dot_are_left_alone():
    out, rules = fixed_body("    .org $F800", "    .byte 1", "    .word 2", "    .ds 1",
                            "    .repeat 2", "    nop", "    .repend")
    assert rules == []


def test_star_comment():
    out, rules = fixed_body("* Title: a kernel", "    nop")
    assert out == ["; Title: a kernel", "    nop"] and rules == ["star_comment"]


def test_accumulator_operand():
    out, rules = fixed_body("    asl A", "    LSR a ; halve", "    rol", "    ror A", "Lbl rol a")
    assert out == ["    asl", "    LSR ; halve", "    rol", "    ror", "Lbl rol"]
    assert rules == ["accumulator_A"]


def test_include_name():
    f = fix(lines("    processor 6502", '    include "vcs.asm"', "    include MACRO.H",
                  "    nop"))
    assert f.text.splitlines()[1:3] == ['    include "vcs.h"', '    include "macro.h"']
    assert f.rules == ["include_name"]


def test_includes_added_when_needed():
    f = fix(lines("    processor 6502", "    org $F000", "    VERTICAL_SYNC", "    sta COLUBK"))
    assert f.text.splitlines()[:3] == ["    processor 6502", '    include "vcs.h"',
                                       '    include "macro.h"']
    assert f.rules == ["add_include_macro.h", "add_include_vcs.h"]


def test_no_vcs_h_when_the_program_defines_the_registers():
    f = fix(lines("    processor 6502", "COLUBK = $09", "    org $F000", "    sta COLUBK"))
    assert f.rules == []


def test_comments_and_strings_are_not_rewritten():
    out, rules = fixed_body("    nop ; asl A, dw, lda in column 0", '    .byte "db;x"')
    assert rules == []


def test_errors_map_back_to_the_models_lines():
    f = fix(lines("include \"vcs.h\"", "    org $F000", "    bogus"))
    assert f.text.splitlines()[0] == "    processor 6502"
    assert map_errors(["prog.asm (4): error: Unknown Mnemonic 'bogus'.", "--- Unresolved"],
                      f.line_map) == ["prog.asm (3): error: Unknown Mnemonic 'bogus'.",
                                      "--- Unresolved"]
    assert map_errors(["prog.asm (1): error: x"], f.line_map) == \
        ["prog.asm (line added by the fixer): error: x"]


# ---------------------------------------------------------------- what it must leave alone

@pytest.mark.parametrize("ref", REFERENCES, ids=lambda p: f"{p.parent.parent.name}/{p.parent.name}")
def test_reference_programs_come_out_unchanged(ref):
    f = fix(ref.read_text())
    assert f.rules == [] and f.text == ref.read_text()


# ---------------------------------------------------------------- real failed attempts

@pytest.mark.parametrize("path", CORPUS, ids=lambda p: p.stem)
def test_corpus_program_assembles_after_fixing(path, tmp_path):
    code = path.read_text()
    as_written, _ = assemble_program(code, tmp_path / "as-written")
    assert not as_written["ok"]
    f = fix(code)
    fixed, _ = assemble_program(f.text, tmp_path / "fixed")
    assert fixed["ok"], (f.rules, fixed["errors"])
    assert fix(f.text).rules == []   # a fixed program needs nothing more


def test_corpus_rules():
    rules = {p.stem: fix(p.read_text()).rules for p in CORPUS}
    assert rules == {
        "col0-directives": ["add_processor", "foreign_directive", "indent_col0"],
        "indented-bare-label": ["dedent_label"],
        "indented-label": ["dedent_label"],
        "no-processor-no-include": ["add_include_vcs.h", "add_processor"],
        "p-6502": ["foreign_directive", "processor_line"],
    }


# ---------------------------------------------------------------- what an attempt keeps

def test_program_that_assembles_is_not_touched(tmp_path):
    code = (TASKS / "t1" / "canonical" / "reference.asm").read_text()
    asm, failed, rec = assemble_with_fixer(code, tmp_path)
    assert asm["ok"] and failed is None
    assert rec == {"assembled_as_written": True, "assembled_after_fix": True, "rules": []}
    assert not (tmp_path / "prog.orig.asm").exists()


def test_fixed_attempt_keeps_the_original(tmp_path):
    code = (FIXTURES / "p-6502.asm").read_text()
    asm, failed, rec = assemble_with_fixer(code, tmp_path)
    assert asm["ok"] and failed is None
    assert rec["assembled_as_written"] is False and rec["assembled_after_fix"] is True
    assert rec["rules"] == ["foreign_directive", "processor_line"]
    assert rec["errors_as_written"]
    assert (tmp_path / "prog.orig.asm").read_text() == code
    assert (tmp_path / "prog.asm").read_text() == fix(code).text
    assert json.loads((tmp_path / "assemble.orig.json").read_text())["ok"] is False
    assert json.loads((tmp_path / "assemble.json").read_text())["ok"] is True
    assert (tmp_path / "build" / "prog.bin").exists()


def test_fix_that_still_fails_reports_the_models_line_numbers(tmp_path):
    code = lines("include \"vcs.h\"", "    org $F000", "    bogus #1")
    asm, failed, rec = assemble_with_fixer(code, tmp_path)
    assert failed.category == "assemble_error"
    assert rec["assembled_as_written"] is False and rec["assembled_after_fix"] is False
    assert asm["errors"][0].startswith("prog.asm (3): error: Unknown Mnemonic 'bogus'")
    assert "prog.asm (3)" in failed.detail


def test_unfixable_program_is_left_as_written(tmp_path):
    code = lines("    processor 6502", "    org $F000", "    bogus #1")
    asm, failed, rec = assemble_with_fixer(code, tmp_path)
    assert failed is not None and rec["rules"] == []
    assert not (tmp_path / "prog.orig.asm").exists()


@pytest.mark.skipif(not RACER_CHECK.exists(), reason="racer-check not built")
@pytest.mark.parametrize("setting", ["on", "off"])
def test_case0_records_the_fixer(tmp_path, monkeypatch, setting):
    code = (FIXTURES / "p-6502.asm").read_text()

    def fake_chat(args, messages, seed, workdir):
        workdir.mkdir(parents=True, exist_ok=True)
        return {"message": {"content": f"```asm\n{code}```"}, "done_reason": "stop"}

    monkeypatch.setattr(case0, "chat", fake_chat)
    monkeypatch.setattr(case0, "model_digest", lambda m: "digest")
    monkeypatch.setattr(case0, "model_template", lambda m: "")
    case0.main(["--model", "m", "--samples", "1", "--random-seeds", "1", "--fix-syntax", setting,
                "--experiment", "e", "--out", str(tmp_path)])
    [job] = (tmp_path / "e" / "jobs").iterdir()
    assert json.loads((job / "config.json").read_text())["fix_syntax"] is (setting == "on")
    attempt = json.loads((job / "sample-00" / "attempt-1" / "attempt.json").read_text())
    if setting == "off":
        assert attempt["fix"] is None and attempt["grade"]["category"] == "assemble_error"
    else:
        assert attempt["fix"]["assembled_as_written"] is False
        assert attempt["fix"]["assembled_after_fix"] is True
        assert attempt["assemble"]["ok"] and attempt["grade"]["category"] != "assemble_error"
        assert (job / "sample-00" / "attempt-1" / "prog.orig.asm").read_text() == code
