"""Checks for a solid blue background."""

from tbd.eval.checks import Racer

# Reported, not graded: racer's strict 3-line VSYNC.
INFORMATIONAL = {"vsync"}

# Any of the three blues around $84 passes.
BLUE = [0x82, 0x84, 0x86]


def check(racer: Racer) -> dict:
    return racer.run("check.toml", accept={"background": BLUE})


def relaxed(racer: Racer) -> dict:
    return racer.run("relaxed.check.toml", accept={"picture": BLUE})
