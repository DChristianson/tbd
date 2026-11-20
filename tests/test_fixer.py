"""Syntax fixer: golden tests over input programs, plus what an attempt keeps.

Golden tests. Each tests/fixtures/fixer/cases/<name>.asm is fixed and compared
with tests/fixtures/fixer/expected/<name>.asm; a difference fails with a
line-by-line diff. expected/rules.txt lists the rules that fired for each case.
Reading a case beside its expected file shows what the fixer does, and a change
to the fixer shows up as a change to these files. After a deliberate change,
regenerate them and review the diff:

    UPDATE_GOLDEN=1 python -m pytest tests/test_fixer.py

Cases named model-*.asm are programs models wrote that DASM rejected, copied
unchanged from saved runs (experiments/_collected/assemble-errors-20261119):

    model-no-processor-no-include  qwen2.5-coder:14b, t1/pal: no processor line, no vcs.h
    model-col0-directives          qwen3.6:27b, t1/blue: include in column 0, .endrepeat
    model-indented-label           qwen3.6:27b, t1/canonical: ' Start:' indented
    model-indented-bare-label      qwen3.6:27b, t1/canonical: ' main_loop' indented, no colon
    model-p-6502                   qwen3.6:27b, t1/canonical: ':p 6502', dw

They must also assemble once fixed. The other cases are small programs, one
per rule, including ones the fixer must leave alone.
"""

import difflib
import json
import os
from pathlib import Path

import pytest

from tbd.eval import case0
from tbd.eval.fixer import assemble_with_fixer, fix, map_errors
from tbd.eval.grading import assemble_program
from tbd.paths import RACER_CHECK, TASKS

FIXER = Path(__file__).parent / "fixtures" / "fixer"
CASES = sorted((FIXER / "cases").glob("*.asm"))
EXPECTED = FIXER / "expected"
RULES = EXPECTED / "rules.txt"
MODEL_CASES = [p for p in CASES if p.stem.startswith("model-")]
REFERENCES = sorted(TASKS.glob("*/*/reference.asm"))
UPDATE = bool(os.environ.get("UPDATE_GOLDEN"))


def lines(*body: str) -> str:
    return "\n".join(body) + "\n"


def read_rules() -> dict[str, list[str]]:
    """expected/rules.txt: `<case>: rule, rule` per line (nothing after the colon: none)."""
    out = {}
    for line in RULES.read_text().splitlines():
        name, _, rules = line.partition(":")
        out[name.strip()] = [r.strip() for r in rules.split(",") if r.strip()]
    return out


# ---------------------------------------------------------------- golden

@pytest.mark.parametrize("case", CASES, ids=lambda p: p.stem)
def test_golden(case):
    got = fix(case.read_text())
    expected = EXPECTED / case.name
    if UPDATE:
        expected.write_text(got.text)
        pytest.skip("golden file rewritten")
    want = expected.read_text()
    diff = "".join(difflib.unified_diff(want.splitlines(keepends=True),
                                        got.text.splitlines(keepends=True),
                                        f"expected/{case.name}", f"fix(cases/{case.name})"))
    assert got.text == want, f"fixer output differs from its golden file:\n{diff}"
    assert got.rules == read_rules().get(case.stem), f"rules for {case.stem}"
    assert fix(got.text).rules == [], "a fixed program needs nothing more"


def test_rules_file_lists_every_case():
    if UPDATE:
        RULES.write_text("".join(f"{p.stem}: {', '.join(fix(p.read_text()).rules)}".rstrip() + "\n"
                                 for p in CASES))
        pytest.skip("rules.txt rewritten")
    assert sorted(read_rules()) == [p.stem for p in CASES]
    assert sorted(p.name for p in EXPECTED.glob("*.asm")) == [p.name for p in CASES]


@pytest.mark.parametrize("ref", REFERENCES, ids=lambda p: f"{p.parent.parent.name}/{p.parent.name}")
def test_reference_programs_come_out_unchanged(ref):
    f = fix(ref.read_text())
    assert f.rules == [] and f.text == ref.read_text()


@pytest.mark.parametrize("case", MODEL_CASES, ids=lambda p: p.stem)
def test_model_program_assembles_only_after_fixing(case, tmp_path):
    as_written, _ = assemble_program(case.read_text(), tmp_path / "as-written")
    assert not as_written["ok"]
    fixed, _ = assemble_program((EXPECTED / case.name).read_text(), tmp_path / "fixed")
    assert fixed["ok"], fixed["errors"]


# ---------------------------------------------------------------- error lines

def test_errors_map_back_to_the_models_lines():
    f = fix(lines("include \"vcs.h\"", "    org $F000", "    bogus"))
    assert f.line_map[:2] == [None, 1]   # processor line added, then the model's line 1
    assert map_errors(["prog.asm (4): error: Unknown Mnemonic 'bogus'.", "--- Unresolved"],
                      f.line_map) == ["prog.asm (3): error: Unknown Mnemonic 'bogus'.",
                                      "--- Unresolved"]
    assert map_errors(["prog.asm (1): error: x"], f.line_map) == \
        ["prog.asm (line added by the fixer): error: x"]


# ---------------------------------------------------------------- what an attempt keeps

def test_program_that_assembles_is_not_touched(tmp_path):
    code = (TASKS / "t1" / "canonical" / "reference.asm").read_text()
    asm, failed, rec = assemble_with_fixer(code, tmp_path)
    assert asm["ok"] and failed is None
    assert rec == {"assembled_as_written": True, "assembled_after_fix": True, "rules": []}
    assert not (tmp_path / "prog.orig.asm").exists()


def test_fixed_attempt_keeps_the_original(tmp_path):
    code = (FIXER / "cases" / "model-p-6502.asm").read_text()
    asm, failed, rec = assemble_with_fixer(code, tmp_path)
    assert asm["ok"] and failed is None
    assert rec["assembled_as_written"] is False and rec["assembled_after_fix"] is True
    assert rec["rules"] == read_rules()["model-p-6502"]
    assert rec["errors_as_written"]
    assert (tmp_path / "prog.orig.asm").read_text() == code
    assert (tmp_path / "prog.asm").read_text() == (EXPECTED / "model-p-6502.asm").read_text()
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
    code = (FIXER / "cases" / "model-p-6502.asm").read_text()

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
