# Build Plan — Test-First Roadmap

Working through the demo tasks in
[docs/design/atari-2600-synthesis-agent.md](../design/atari-2600-synthesis-agent.md),
one at a time, in order. Each task is measured in the
[experiment grid](../design/experiment-grid.md).

**Work items are tracked in beads** (`bd ready`, `bd list`, `bd show <id>`),
in the dev container (see [.devcontainer/README.md](../../.devcontainer/README.md)).
This file is the narrative: why the work is ordered the way it is, and what
was learned doing it.

Rule: for the task in progress, write the tests that specify its acceptance
criteria before writing any implementation.

## Course correction (2026-11-11)

Task 1 was done by writing the kernel by hand, which tested the tools, not an
agent. Carrying on that way would finish all 8 tasks with good tools and no
synthesis engine. The old rule "nothing beyond Layer 0 until a task's own
failure surfaces a real reason for it" couldn't fire, because nothing was
attempting the tasks and failing.

From here, each task contributes three things:
1. **Layer 0 capability** the task needs (as before).
2. **A task spec**: prompt, variants, grader, and a hand-written reference
   solution. The hand-written kernel is the proof the grader can be passed,
   not the deliverable.
3. **Grid rows**: models attempt the task through the harness, and the
   results land in the experiment grid.

The work is grouped the same way as the grid:
1. **Infrastructure**: compile, run, grade, log (beads epic: *Infrastructure*).
2. **Contributed**: models, facts, tasks (*Contributed inputs*, *Baseline*).
3. **Discovered**: facts and models (*Discovery loop*, *Discovered models*).

The first grid cell to fill is the baseline: local models, no facts, Task 1.
It measures how much 2600 programming the models already know from
pretraining, which every later comparison is measured against.

## Course correction (2026-11-15): racer-check grades, Stella cross-checks

The plan had `tbd.tools.emulator` (Stella, through `stella-core`) as the
emulator the grader reads, with graders written in tbd: Python checks over
per-line register reads, then a raster/frame-diff tool for Task 3 and
joystick input added to the Stella protocol for Tasks 7-8 (beads
`tbd-818.2`, `tbd-dpu.3`, `tbd-ddh.1`).

racer (a separate project, same author) already had what those were going to
be: a cycle-accurate 6502/TIA/RIOT simulator, cross-checked against Stella,
Gopher2600 and 6502.ts, and `racer-check`, which checks TOML check files of
properties per color clock (frame length, sync, blanking, colors, register
write windows, images, object positions, audio, scripted joystick input),
from a zeroed and from random power-on states. It also showed the gap in the
Stella-based tests: they passed a Case 0 reference that drew 263 lines and
never blanked (item 5 below), because they read registers once per line
instead of what's on screen.

So, decided (beads `tbd-818.9`, `tbd-818.12`):
- **The grader is `racer-check`**, vendored into
  [vendor/racer/](../../vendor/racer/NOTICE.md) (MIT) and called as a binary,
  as tbd calls `dasm`. **Task specs grade with racer check files**
  ([tasks/t1/red_background.check.toml](../../tasks/t1/red_background.check.toml)).
- **Stella stays, as an optional reference emulator** for cross-checking the
  grader: `tests/test_emulator.py`, and a test that racer's LXA agrees with
  Stella's. The eval doesn't use it, and the build skips it on request
  (`TBD_SKIP_STELLA=1`). Periodic Stella cross-checks of graded attempts:
  `tbd-818.16`.
- **tbd adds to racer where the eval needs it**, and upstreams each
  addition to racer so the vendored copy stays unmodified. So far: the
  undocumented `LXA` (`CLEAN_START` uses it, and undocumented opcodes are
  allowed), the property kinds `signal_span`, `frame_length_range` and
  `picture_block` for tolerant VSYNC and the relaxed (near-miss) tier, and
  `racer-check --frames`. All of these are upstream as of racer PR #17
  (`tbd-818.15`); see `vendor/racer/NOTICE.md`.

What this changes for later tasks: the raster/frame-diff tool (Task 3) and
joystick input (Tasks 7-8) come from racer's `image`, `object` and `[[input]]`
support instead of being built in tbd.

## Status
- [x] Task 1 — Layer 0: stable framerate, red background (tools + hand-written reference)
- [x] Task 1 — spec (prompt, strict and relaxed check files, reference) and the eval harness (`tbd.eval.case0`)
- [ ] Task 1 — baseline: Claude rows filled (Opus 5.5 5/5, Haiku 5.5 4/5 + 1 near miss, Sonnet 5.5 1/5 + 4 near misses); local rows only dry requests (beads `tbd-1nx.5`)
- [ ] Tasks 2–8 — see beads

## Task 1 — stable framerate, red background

> Create a kernel with stable framerate that sets the screen background to red.

**Specified by:** [tests/test_assembler.py](../../tests/test_assembler.py),
[tests/test_emulator.py](../../tests/test_emulator.py).

**Status: done. All 5 tests pass** (3 assembler, 2 emulator) against a real
headless Stella build — no mocking, no approximation.

Scope for this task: `tbd.tools.assembler` (assemble source -> ROM bytes) and
`tbd.tools.emulator` (run a ROM for N frames, read back per-frame cycle count
and background color per scanline). Nothing else — no raster diff, no corpus,
no retrieval, no synthesis loop. Comparing "is this solid red" only needs a
per-scanline color read, not an image-diff tool.

Order of work from here:
1. ✅ Author the fixture (`red_background.asm`) — by hand, not generated.
   Vendored `vcs.h`/`macro.h` from the local DASM install into
   `tests/fixtures/vendor/atari2600/` (now `vendor/atari2600/`) to make it assemble (GPL-2.0-or-later,
   see the `NOTICE.md` there — distinct from this repo's MIT license).
2. ✅ Implement `assemble()` against DASM — `test_assembler.py` passes.
3. ⏸️ Implement `run()` against an emulator — paused, still deciding *what*
   `run()` actually drives. Research so far, so it isn't re-derived:
   - **Stella's own GUI binary** (Homebrew `stella`): confirmed by hand it
     has no headless/scriptable CLI. `-debug` + a `<rom>.script` + `saveSes`
     looked promising from the docs, but in practice the debugger prompt and
     its script output are routed through the SDL window, not through
     process stdin/stdout — tried it, got an open window and no session
     file, had to kill the process.
   - **ALE (Arcade Learning Environment)**: this is Stella's core decoupled
     from the GUI, headless, with real memory access
     (`getRAM()`/`setRAM()`, and internally `RomUtils::readMappedRam` over
     the *full* address space, not just the 128-byte RL-RAM window). Looked
     like the answer. `loadROM()` requires the ROM's MD5 *or* filename to
     match one of its ~100 cataloged commercial games — confirmed from
     ALE's own source, `Roms.cpp`'s `buildRomRLWrapper()` — and calls
     `std::exit(1)` (a hard process exit, not a catchable exception) when
     neither matches. There *is* a filename-based bypass (naming a custom
     ROM after a cataloged game gets it past the check — the match is
     MD5-or-filename, not MD5-only), but it's moot: it only attaches that
     cataloged game's reward/lives logic, and separately, ALE's published
     Python bindings don't expose CPU cycles or TIA registers at all
     regardless of which `RomSettings` got attached — that's the actual,
     bypass-independent reason this didn't fit. Its underlying `emucore` is
     still the right shape; using it means building against that layer
     directly instead of `ALEInterface`, which is a real build/integration
     task, not a `pip install`.
   - **Decided and built:** our own headless integration directly against
     mainline Stella 7.0c source (not the stale `stella2014-libretro` fork,
     not ALE's `ALEInterface`), reusing Stella's *own* libretro platform
     layer (`src/os/libretro/`) rather than writing a headless `OSystem`
     from scratch. Lives in its own top-level `stella-core/` folder
     (GPL-2.0-or-later, separate from this repo's MIT license), talked to
     as a subprocess over a JSON-lines protocol — same shape as
     `assemble()`'s relationship to `dasm`. Full writeup, including two
     things that changed from the original plan while building it (reusing
     Stella's libretro layer instead of a from-scratch `OSystem`; reading
     `System`/`TIA`/`M6502` directly instead of the GUI-coupled `Debugger`
     wrapper classes):
     [stella-core-integration.md](stella-core-integration.md).
   - One real subtlety the test suite caught: a kernel's first full pass
     still starts with a few scanlines of VSYNC before its own
     `sta COLUBK` executes, during which the register holds whatever was
     there before — stale `0x00` on that very first pass specifically.
     `tbd.tools.emulator.run()` discards two warm-up frames (the power-on
     transient *and* that first real pass), not one, before trusting
     steady state.
4. ✅ `skipif` guards removed — both fixture and `run()` exist and pass.
5. ✅ (2026-11-14) Fixture fixed, tests strengthened. Checked with racer's
   checker, the original fixture drew **263** lines and never blanked:
   `VERTICAL_SYNC`'s first `WSYNC` ends the last overscan line, so 30
   overscan `WSYNC`s made one line too many, and `VBLANK` was turned off
   once and never back on. The tests only checked that frames were *equal*
   length and that `COLUBK` held red, so both passed. They now also check
   262 lines, 3 lines of VSYNC, and 192 contiguous unblanked lines, all red.
   The fixture also defines `NO_ILLEGAL_OPCODES` (racer's simulator rejected
   `CLEAN_START`'s `lxa` at the time; it runs it since 2026-11-15). Its racer
   check file is `tasks/t1/red_background.check.toml` and passes 101 of
   101 runs.
6. ✅ (2026-11-15) Grading moved to `racer-check` (course correction above).
   The Stella tests in `tests/test_emulator.py` stay as a cross-check.

## Later tasks

Not detailed here. Each gets its own section when it starts; work items are
in beads. Layer 0 needs per task are listed in
[experiment-grid.md](../design/experiment-grid.md#task-specs).

2. Per-scanline background color changes
3. Color grid (graded with racer's `image` / `output_colors` properties)
4. Position players/missiles/ball on playfield
5. 2-player scoreboard
6. 6-character scoreboard
7. Pong clone (scripted joystick input through racer check files' `[[input]]`)
8. Two-player movement + shooting
