"""Spec for Task 1's assembler contract (see docs/plan/roadmap.md):
`tbd.tools.assembler.assemble()` turns `tasks/t1/canonical/reference.asm` into a
ROM, and reports a bad source as errors rather than a ROM.
"""


import pytest

from tbd.paths import HEADERS, TASKS
from tbd.tools.assembler import assemble

FIXTURE = TASKS / "t1" / "canonical" / "reference.asm"

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


@pytest.mark.parametrize("org, after_vectors, size", [
    ("$F000", "", 4096),                      # org $F000 ... vectors at $FFFA: exactly 4K
    ("$F000", "Extra\n    rti\n    rti\n", 4098),   # code after the vectors runs past $FFFF
    ("$F800", "", 2048),                      # a later first ORG makes a smaller image
])
def test_rom_runs_from_the_first_org_to_the_last_byte(tmp_path, org, after_vectors, size):
    """DASM's raw output (-f3) holds every byte from the first ORG to the last
    one assembled; nothing pads it to a cartridge size. Backs fact F018."""
    src = tmp_path / "layout.asm"
    src.write_text(f"    processor 6502\n    org {org}\nReset\n    jmp Reset\n"
                   f"    org $FFFA\n    .word Reset\n    .word Reset\n    .word Reset\n"
                   f"{after_vectors}")
    result = assemble(src)
    assert result.ok, result.errors
    assert len(result.rom) == size
