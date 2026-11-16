"""Task 1, blue: a stable NTSC frame with a solid blue background."""

from tbd.eval.checks import Racer

# Recorded, not graded: racer's strict, line-aligned 3-line VSYNC. The graded
# VSYNC property is vsync_span (a 2-4 line range).
INFORMATIONAL = {"vsync"}

# The prompt says "blue", not a value: NTSC hue 8 at the three luminances
# around $84 (Stella shows $84 as #2d32b8; the TIA ignores bit 0).
BLUE = [0x82, 0x84, 0x86]


def check(racer: Racer) -> dict:
    return racer.run("check.toml", accept={"background": BLUE})


def relaxed(racer: Racer) -> dict:
    return racer.run("relaxed.check.toml", accept={"picture": BLUE})
