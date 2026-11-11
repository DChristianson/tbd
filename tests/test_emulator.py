"""Spec for Task 1's emulator contract (see docs/plan/roadmap.md).

"Stable framerate" and "red background" are both defined here as per-frame,
per-scanline observations — no image/frame-diff tool is needed for a solid
color. Driven by `stella-core`'s compiled binary
(docs/plan/stella-core-integration.md); build it per stella-core/README.md
before running these.
"""

from pathlib import Path

import pytest

from tbd.tools.assembler import assemble
from tbd.tools.emulator import run, _DEFAULT_BIN

FIXTURE = Path(__file__).parent / "fixtures" / "red_background.asm"
# TIA hue 4 ("red"), confirmed against the real NTSC palette by running the
# fixture through stella-core and reading COLUBK's shadow register back —
# not a guess.
NTSC_RED = 0x42

pytestmark = pytest.mark.skipif(
    not _DEFAULT_BIN.exists(),
    reason="stella-core binary not built yet — see stella-core/README.md",
)


@pytest.fixture
def rom():
    result = assemble(FIXTURE)
    assert result.ok
    return result.rom


def test_stable_framerate(rom):
    """Every frame takes the same number of cycles — that's what 'stable
    framerate' means at the hardware level: no jitter from varying branch
    paths in the kernel."""
    result = run(rom, num_frames=10)
    assert len(result.frames) == 10
    cycle_counts = {frame.cycle_count for frame in result.frames}
    assert len(cycle_counts) == 1


def test_background_is_solid_red_every_scanline(rom):
    """Every scanline, every frame, reads back as the red color index."""
    result = run(rom, num_frames=10)
    for frame in result.frames:
        assert frame.scanline_colors
        assert all(color == NTSC_RED for color in frame.scanline_colors)
