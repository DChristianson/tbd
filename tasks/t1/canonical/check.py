"""Task 1, canonical: a stable NTSC frame with a solid red background."""

from tbd.eval.checks import Racer

# Recorded, not graded: racer's strict, line-aligned 3-line VSYNC. The graded
# VSYNC property is vsync_span (a 2-4 line range).
INFORMATIONAL = {"vsync"}

# The prompt says "red", not a value: NTSC hue 4 at the three luminances
# around $42 (the TIA ignores bit 0, so $43/$45 show as $42/$44).
RED = [0x42, 0x44, 0x46]


def check(racer: Racer) -> dict:
    return racer.run("check.toml", accept={"background": RED})


def relaxed(racer: Racer) -> dict:
    return racer.run("relaxed.check.toml", accept={"picture": RED})
