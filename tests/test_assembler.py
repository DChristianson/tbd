"""Spec for Task 1's assembler contract (see docs/plan/roadmap.md):
`tbd.tools.assembler.assemble()` turns `tasks/t1/red_background.asm` into a
ROM, and reports a bad source as errors rather than a ROM.
"""


import pytest

from tbd.paths import HEADERS, TASKS
from tbd.tools.assembler import assemble

FIXTURE = TASKS / "t1" / "red_background.asm"

pytestmark = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason="fixture not written yet (Task 1, pre-implementation)",
)


def test_assembles_without_errors():
    """A correct, hand-written source assembles clean: no errors, ROM bytes out."""
    result = assemble(FIXTURE, include_dirs=[HEADERS])
    assert result.ok
    assert result.errors == []
    assert result.rom
    assert len(result.rom) > 0


def test_rom_is_a_standard_cartridge_size():
    """DASM should produce a ROM at one of the real Atari 2600 cart sizes."""
    result = assemble(FIXTURE, include_dirs=[HEADERS])
    assert len(result.rom) in (2048, 4096)


def test_bad_source_reports_errors_not_a_rom(tmp_path):
    """A source with a real assembly error fails with `.ok is False` and a
    non-empty `errors` list — this is what a future failure-attribution step
    would use to tell a code bug apart from a knowledge gap, but that's out
    of scope for Task 1; this just specifies the assembler's own contract."""
    broken = tmp_path / "broken.asm"
    broken.write_text("        processor 6502\n        this is not an opcode\n")
    result = assemble(broken)
    assert not result.ok
    assert result.errors
    assert result.rom is None
