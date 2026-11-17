"""Checks for a background color that goes up by 2 on every scanline, with a
red bar on the right."""

from tbd.eval.checks import Racer

# Reported, not graded: racer's strict 3-line VSYNC, and where COLUBK is written.
INFORMATIONAL = {"vsync", "colubk_switch"}

# Any of the three reds around $46 passes.
RED = [0x42, 0x44, 0x46]


def check(racer: Racer) -> dict:
    return racer.run("check.toml", accept={"bar": RED})
