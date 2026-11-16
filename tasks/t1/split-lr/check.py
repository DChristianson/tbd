"""Task 1, split-lr: red left half, blue right half."""

from tbd.eval.checks import Racer

# Recorded, not graded: racer's strict, line-aligned 3-line VSYNC.
INFORMATIONAL = {"vsync"}

# The prompt names colors, not values: NTSC hue 4 (red) and hue 8 (blue),
# each at the three luminances around the reference's value.
RED = [0x42, 0x44, 0x46]
BLUE = [0x82, 0x84, 0x86]


def check(racer: Racer) -> dict:
    return racer.run("check.toml", accept={"left": RED, "right": BLUE})
