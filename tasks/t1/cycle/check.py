"""Task 1, cycle: a solid background whose color value goes up by 2 every
frame, wrapping at 256.

Frame timing and blanking come from check.toml. The color has no racer
property kind, so it is checked here over racer-check --frames: each checked
frame is one color on every visible clock of lines 40-231 ("solid"), and each
frame's color is the previous frame's plus 2 ("steps"). The first frame's
color can be anything, so it doesn't matter whether a program steps before or
after drawing its first frame.
"""

from tbd.eval.checks import Racer, failure, merge, report

# Recorded, not graded: racer's strict, line-aligned 3-line VSYNC.
INFORMATIONAL = {"vsync"}

LINES = range(40, 232)
CLOCKS = range(68, 228)
STEP = 2


def _signal_at(line: list, clock: int):
    for start, length, signal in line:
        if start <= clock < start + length:
            return signal
    return None


def _shown(signal) -> str:
    return f"color ${signal:02x}" if isinstance(signal, int) else f"{signal or 'nothing'}"


def solid_color(frame: dict) -> tuple[int | None, dict | None]:
    """The frame's color, or None and the first clock that breaks it."""
    number, lines = frame["number"], frame["lines"]
    color = None
    for n in LINES:
        if n >= len(lines):
            return None, failure("solid", number, f"frame has {len(lines)} lines",
                                 f"one color on lines {LINES.start}-{LINES.stop - 1}", line=n)
        for c in CLOCKS:
            s = _signal_at(lines[n], c)
            if color is None:
                color = s
            if s != color or not isinstance(s, int):
                return None, failure("solid", number, _shown(s),
                                     f"{_shown(color) if isinstance(color, int) else 'one color'}, "
                                     f"the frame's color at line {LINES.start}, clock {CLOCKS.start}",
                                     line=n, clock=c)
    return color, None


def check_run(run: dict) -> list[dict]:
    """The first failure of each property in one run."""
    first: dict[str, dict] = {}
    prev = None
    for frame in run["frames"]:
        color, miss = solid_color(frame)
        if miss and "solid" not in first:
            first["solid"] = miss
        if color is not None and prev is not None and color != (prev + STEP) % 256 \
                and "steps" not in first:
            first["steps"] = failure("steps", frame["number"], f"color ${color:02x}",
                                     f"color ${(prev + STEP) % 256:02x}, the previous frame's "
                                     f"${prev:02x} plus {STEP}")
        prev = color
    return list(first.values())


def check(racer: Racer) -> dict:
    timing = racer.run("check.toml")
    runs = racer.frames(frames=100, seeds=2)
    errors = {r["power_on"]: r["error"] for r in runs if r.get("error")}
    colors = report({r["power_on"]: check_run(r) for r in runs}, ["solid", "steps"], errors)
    return merge(timing, colors)
