"""Checks for a red left half and a blue right half."""

from tbd.eval.checks import Racer

# Reported, not graded: racer's strict 3-line VSYNC.
INFORMATIONAL = {"vsync"}

# Any of the three reds around $42, or blues around $84, passes.
RED = [0x42, 0x44, 0x46]
BLUE = [0x82, 0x84, 0x86]


def check(racer: Racer) -> dict:
    return racer.run("check.toml", accept={"left": RED, "right": BLUE})
