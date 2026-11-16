"""Checks for a solid red background."""

from tbd.eval.checks import Racer

# Reported, not graded: racer's strict 3-line VSYNC.
INFORMATIONAL = {"vsync"}

# Any of the three reds around $42 passes.
RED = [0x42, 0x44, 0x46]


def check(racer: Racer) -> dict:
    return racer.run("check.toml", accept={"background": RED})


def relaxed(racer: Racer) -> dict:
    return racer.run("relaxed.check.toml", accept={"picture": RED})
