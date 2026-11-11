"""Stella wrapper — Layer 0.

Drives `stella-core`'s compiled binary as a subprocess speaking Emulator
Protocol v1 (docs/design/emulator-protocol.md) — a JSON-lines command set
that's backend-agnostic in principle, even though `stella-core` is the
only implementation so far. See docs/plan/stella-core-integration.md for
why a subprocess rather than an in-process extension.

Confirmed by hand against the built binary:
  - Cycle counts and TIA register reads are real, not inferred — `run()`
    gets `COLUBK` from `get_tia_registers` (TIA's shadow copy of its
    write-only registers; a real bus `read_memory` would return open-bus
    garbage for a write-only address, which is itself hardware-accurate
    but useless for "what color is this").
  - Stepping scanline-by-scanline (rather than whole frames via
    `step_frame`) is what makes real per-scanline colors possible. A frame
    boundary is detected by watching the live `scanlines` counter in
    `get_tia_state` wrap back to a small number — confirmed this happens
    reliably once the kernel reaches steady state.
  - The ROM's first frame (through `CLEAN_START`/reset) is a startup
    transient with a different cycle count than every steady-state frame
    after it — confirmed via stella-core/shim/smoketest.cpp. `run()` skips
    it: `num_frames` means steady-state frames, not raw TIA frames
    including power-on.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# TIA write-register address for COLUBK — same value as vcs.h's COLUBK
# equate (0009), vendored at tests/fixtures/vendor/atari2600/vcs.h.
_COLUBK_ADDR = 0x09

# TBD_STELLA_CORE overrides the binary location. The dev container sets it to
# a separate build dir (stella-core/build-linux/) so a Linux build never
# overwrites the host's macOS build in the shared stella-core/build/.
_DEFAULT_BIN = Path(
    os.environ.get("TBD_STELLA_CORE")
    or Path(__file__).resolve().parents[3] / "stella-core" / "build" / "shim" / "stella_core"
)


@dataclass
class Frame:
    cycle_count: int
    scanline_colors: list[int]  # COLUBK value per scanline, across the whole
    # frame (not just the visible region — see docs/plan/stella-core-integration.md)


@dataclass
class RunResult:
    frames: list[Frame] = field(default_factory=list)


class _StellaCoreProcess:
    """One `stella_core` subprocess, one loaded ROM, line-at-a-time JSON."""

    def __init__(self, binary: Path):
        self._proc = subprocess.Popen(
            [str(binary)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def send(self, **command) -> dict:
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._proc.stdin.write(json.dumps(command) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            stderr = self._proc.stderr.read() if self._proc.stderr else ""
            raise RuntimeError(f"stella_core exited unexpectedly; stderr: {stderr}")
        response = json.loads(line)
        if not response.get("ok"):
            raise RuntimeError(f"stella_core command {command} failed: {response.get('error')}")
        return response

    def close(self):
        try:
            self.send(cmd="quit")
        except Exception:
            pass
        if self._proc.stdin:
            self._proc.stdin.close()
        self._proc.wait(timeout=5)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def run(rom: bytes, *, num_frames: int, binary: Path | None = None) -> RunResult:
    """Run `rom` under headless Stella for `num_frames` steady-state frames.

    Each `Frame.scanline_colors` is `COLUBK`'s value sampled once per
    scanline, for every scanline Stella actually drew that frame (NTSC is
    ~262-263 depending on the kernel's own VSYNC timing, not a fixed 192 —
    see the module docstring).
    """
    binary = binary or _DEFAULT_BIN
    if not binary.exists():
        raise RuntimeError(
            f"'{binary}' not found — build it first, see stella-core/README.md"
        )

    with tempfile.TemporaryDirectory() as tmp:
        rom_path = Path(tmp) / "rom.bin"
        rom_path.write_bytes(rom)

        with _StellaCoreProcess(binary) as core:
            core.send(cmd="load_rom", path=str(rom_path))

            frames: list[Frame] = []
            current_colors: list[int] = []
            # Discard two wraps before trusting steady state, not one: the
            # first wrap only ends the power-on/CLEAN_START transient: the
            # kernel's *first* real pass still starts with a few scanlines
            # of VSYNC before its own `sta COLUBK` executes, during which
            # the register holds whatever was there before — 0x00 on that
            # first real pass specifically, since nothing had written it
            # yet. From the second real pass on, COLUBK already holds the
            # previous frame's value throughout that same gap, so no
            # startup blip — confirmed by hand, this was a real (if
            # one-off) test failure before this comment existed.
            warmup_wraps_needed = 2
            wraps_seen = 0
            started = False
            prev_scanlines: int | None = None
            boundary_cycles: int | None = None

            # Safety valve: a kernel that never produces a clean scanline
            # wrap would otherwise spin forever.
            max_steps = (num_frames + 3) * 400

            for _ in range(max_steps):
                core.send(cmd="step_scanline")
                tia = core.send(cmd="get_tia_state")
                cur_scanlines = tia["scanlines"]

                wrapped = prev_scanlines is not None and cur_scanlines < prev_scanlines
                if wrapped:
                    wraps_seen += 1
                    cycles_now = core.send(cmd="get_cpu_state")["cycles"]
                    if started:
                        frames.append(
                            Frame(
                                cycle_count=cycles_now - boundary_cycles,
                                scanline_colors=current_colors,
                            )
                        )
                        if len(frames) == num_frames:
                            break
                    if wraps_seen >= warmup_wraps_needed:
                        started = True
                    current_colors = []
                    boundary_cycles = cycles_now

                if started:
                    colubk = core.send(cmd="get_tia_registers")["registers"][_COLUBK_ADDR]
                    current_colors.append(colubk)

                prev_scanlines = cur_scanlines

            if len(frames) < num_frames:
                raise RuntimeError(
                    f"only collected {len(frames)}/{num_frames} frames — "
                    "kernel may not be producing clean scanline boundaries"
                )

    return RunResult(frames=frames)
