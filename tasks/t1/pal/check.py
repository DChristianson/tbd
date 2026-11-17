"""Checks for a solid red background on PAL."""

from tbd.eval.checks import Racer

# Reported, not graded: racer's strict 3-line VSYNC.
INFORMATIONAL = {"vsync"}

# Any of the three PAL reds around $62 passes. NTSC's $42 is brown-orange on PAL.
RED = [0x62, 0x64, 0x66]


def check(racer: Racer) -> dict:
    return racer.run("check.toml", accept={"background": RED})


def relaxed(racer: Racer) -> dict:
    return racer.run("relaxed.check.toml", accept={"picture": RED})
