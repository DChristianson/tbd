"""Every task variant in tasks/<task>/<variant>/ (docs/design/task-spec.md):
its reference passes its own checks, its broken programs fail, its check
files load, and its prompt doesn't quote the reference. Adding a folder with a
prompt.txt adds it here; there is no list to update."""

import re

import pytest

from tbd.eval import tasks
from tbd.eval.checks import Racer, grade_program
from tbd.eval.grading import assemble_program, property_names, run_racer_check, with_rom
from tbd.paths import RACER_CHECK

SPECS = tasks.all_specs()
needs_racer = pytest.mark.skipif(not RACER_CHECK.exists(), reason="racer-check not built")


def by_id(specs):
    return pytest.mark.parametrize("spec", specs, ids=[s.id for s in specs])


BROKEN = [(s, p) for s in SPECS for p in s.broken]


def test_there_are_tasks():
    assert any(s.id == "t1/canonical" for s in SPECS)


@by_id(SPECS)
def test_spec_files(spec):
    assert spec.prompt.strip(), "prompt.txt is empty"
    assert spec.reference.exists()
    assert "check.toml" in spec.check_files


@by_id(SPECS)
def test_prompt_does_not_quote_the_reference(spec):
    """The reference's header comments describe the solution; none of their
    lines may appear in the prompt."""
    comments = [m.strip() for m in re.findall(r"(?m);(.*)$", spec.reference.read_text())]
    prompt = spec.prompt
    leaked = [c for c in comments if len(c) >= 20 and c in prompt]
    assert not leaked


@by_id(SPECS)
def test_informational_names_are_properties(spec):
    names = {n for f in spec.check_files for n in property_names((spec.dir / f).read_text())}
    assert spec.informational <= names


@needs_racer
@by_id(SPECS)
def test_reference_passes(spec, tmp_path):
    """Strict and relaxed tiers, through check.py when there is one, and
    every other check file in the folder at least loads."""
    asm, failed = assemble_program(spec.reference.read_text(), tmp_path)
    assert failed is None, asm["errors"]
    racer = Racer(spec.dir, tmp_path)
    for tier, report in (("strict", spec.check(racer)), ("relaxed", spec.relaxed(racer))):
        if report is not None:
            assert report["passed"], (tier, report.get("error") or report["unexpected"][:1])
            assert not report["accepted"], (tier, "reference needed an accepted color")
    for name in set(spec.check_files) - set(racer.ran):
        report = run_racer_check(tmp_path, with_rom((spec.dir / name).read_text()), f"load-{name}")
        assert "error" not in report, (name, report["error"])


@needs_racer
@pytest.mark.parametrize("spec,path", BROKEN, ids=[f"{s.id}/{p.stem}" for s, p in BROKEN])
def test_broken_program_fails(spec, path, tmp_path):
    asm, grade = grade_program(spec, path.read_text(), tmp_path)
    assert asm["ok"], asm["errors"]
    assert not grade.ok and grade.category in ("timing", "output_mismatch"), grade.detail
