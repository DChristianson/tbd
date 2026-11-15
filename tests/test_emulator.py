"""Spec for Task 1's emulator contract (see docs/plan/roadmap.md).

"Stable framerate" and "red background" are both defined here as per-frame,
per-scanline observations — no image/frame-diff tool is needed for a solid
color. Driven by `stella-core`'s compiled binary
(docs/plan/stella-core-integration.md); build it per stella-core/README.md
before running these.
"""


import pytest

from tbd.paths import HEADERS, TASKS
from tbd.tools.assembler import assemble
from tbd.tools.emulator import run, _DEFAULT_BIN

FIXTURE = TASKS / "t1" / "red_background.asm"
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
    result = assemble(FIXTURE, include_dirs=[HEADERS])
    assert result.ok
    return result.rom


# NTSC frame layout, as in racer's check files for its sketch 0A: 262 lines,
# VSYNC for 3 of them, a 192-line picture, blanked everywhere else.
NTSC_LINES = 262
NTSC_VSYNC_LINES = 3
NTSC_PICTURE_LINES = 192
NUM_FRAMES = 10


@pytest.fixture
def frames(rom):
    result = run(rom, num_frames=NUM_FRAMES)
    assert len(result.frames) == NUM_FRAMES
    return result.frames


def test_stable_framerate(frames):
    """Every frame takes the same number of cycles — that's what 'stable
    framerate' means at the hardware level: no jitter from varying branch
    paths in the kernel."""
    assert len({frame.cycle_count for frame in frames}) == 1


def test_frame_is_ntsc_length(frames):
    """Stable is not enough: an NTSC TV expects 262 lines. A kernel that is
    steady at 263 passed the test above (this fixture once did)."""
    for frame in frames:
        assert frame.scanlines == NTSC_LINES
        assert len(frame.scanline_colors) == NTSC_LINES


def test_vsync_lasts_three_lines(frames):
    for frame in frames:
        assert sum(frame.vsync) == NTSC_VSYNC_LINES


def test_picture_is_192_contiguous_unblanked_lines(frames):
    """Everything outside the picture is blanked (VBLANK on). Sampled once
    per line, so a mid-line VBLANK change isn't seen; racer's checker checks
    every color clock."""
    for frame in frames:
        shown = [i for i, blank in enumerate(frame.vblank) if not blank]
        assert len(shown) == NTSC_PICTURE_LINES
        assert shown == list(range(shown[0], shown[0] + NTSC_PICTURE_LINES))
        assert not any(frame.vsync[i] for i in shown)


def test_background_is_solid_red_every_picture_line(frames):
    """Every unblanked line, every frame, has the red color index in COLUBK.
    (Register value, not pixels: on-screen output needs a frame buffer.)"""
    for frame in frames:
        shown = [c for c, blank in zip(frame.scanline_colors, frame.vblank) if not blank]
        assert shown
        assert all(color == NTSC_RED for color in shown)
