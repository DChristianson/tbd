# `stella-core` — a headless, debuggable Stella integration

Plan for the thing `tbd.tools.emulator.run()` will actually drive. Written
after two dead ends, recorded in [roadmap.md](roadmap.md):

- The installed `stella` GUI binary has no headless/scriptable CLI — its
  debugger prompt and script output are routed through the SDL window, not
  process stdin/stdout.
- ALE (Arcade Learning Environment) is Stella's core decoupled from the GUI
  and genuinely headless, but its public `loadROM()` rejects any ROM whose
  MD5 *or* filename doesn't match one of ~100 cataloged commercial games
  (`std::exit(1)`) — confirmed from ALE's own source, `Roms.cpp`'s
  `buildRomRLWrapper()`. There's a filename-based bypass (name a custom ROM
  after a cataloged game and the match succeeds without any MD5 check), but
  it's moot: it only attaches that game's reward/lives logic, and ALE's
  published Python bindings don't expose CPU cycles or TIA registers at all
  regardless — the actual, bypass-independent reason this didn't fit.

This plan is for building our own headless integration, at the same
architectural layer a libretro core sits at (frontend-agnostic, frame-step
driven, no GUI), directly against mainline Stella source — not the stale
`stella2014-libretro` fork, which lags badly behind the Stella 7.0c this
project actually uses.

**Status: done.** Task 1 passes end to end against this — 5/5 tests, real
binary, no mocking. See "What actually happened" below for two things that
changed from the original plan while building it, and "Confirmed working"
further down for the finished result.

## Goal

An API shaped like ALE's (familiar verbs: load a ROM, step, read the
screen/RAM) but with the one capability ALE's public interface doesn't
offer and this project actually needs: real debugging access — CPU
registers, cycle counts, TIA register readback, and memory reads/writes
over the *full* address space, not just ALE's 128-byte RIOT-RAM window.

## What actually happened, building this

Two things in this plan turned out differently once building it hit real
constraints:

- **Stella already ships its own headless platform layer.** `src/os/libretro/`
  in mainline Stella (not the stale third-party `stella2014-libretro` fork —
  this is Stella's *own* current libretro port, maintained alongside the GUI
  build) provides `OSystemLIBRETRO` (null persistence, no SDL),
  `FBBackendLIBRETRO`/`SoundLIBRETRO`/`EventHandlerLIBRETRO` (minimal
  stubs — "most of the functionality is not used, since libretro has its
  own rendering system"), and `StellaLIBRETRO`, a wrapper class with exactly
  the lifecycle we need: `setROM(path, data, size)`, `create()`,
  `runFrame()`, `reset()`, direct `osystem()` access. No catalog/MD5 check
  anywhere in that path. So "write a `HeadlessOSystem` from scratch" became
  "use Stella's own, and write our own thin `main.cpp`/protocol instead of
  the full `libretro.h` C ABI (`libretro.cxx`) it also ships" — much less
  work, and it's upstream-maintained rather than ours to keep correct.
- **Not using `debugger/` after all.** The original plan was
  `CpuDebug`/`TIADebug`/`RiotDebug` for register/cycle access. They turned
  out to require a real `Debugger&` in their constructor, and
  `Debugger.hxx` pulls in `DialogContainer`/`DebuggerDialog` — GUI-coupled.
  Not worth dragging in just to satisfy a constructor: the same data is
  available directly and *publicly* from the plain `emucore` objects
  `StellaLIBRETRO::osystem().console()` already exposes —
  `System::cycles()`, `TIA::registerValue()`, `TIA::scanlinesLastFrame()`.
  One exception: `M6502`'s `PC`/`A`/`X`/`Y`/`SP` are private with
  `CpuDebug`/`CartDebug` as the only friends, so `M6502.hxx` got a small,
  explicitly-documented patch adding public accessors — see
  `vendor/stella/NOTICE.md`.

## Architecture decision: separate process, not an in-process extension

ALE binds its core into the Python process directly (nanobind). I'd do this
differently here:

**`stella-core` is a standalone native binary, talked to over stdin/stdout**
(a line-oriented or JSON-lines protocol), the same shape `tbd.tools.assembler`
already uses to drive `dasm`. `tbd.tools.emulator.run()` launches it as a
subprocess, sends commands, parses structured responses.

Why, over a Python extension module:
- **License boundary.** Stella is GPL-2.0-or-later; this project's own code
  is MIT. A subprocess communicating over a pipe is uncontroversially fine
  under GPL. An in-process import of a GPL-linked Python extension raises a
  real question — whether the combination counts as a "linked" derivative
  work needing the whole process to be GPL-compatible — that a process
  boundary sidesteps entirely rather than requiring a legal judgment call.
- **Consistency.** Every Layer-0 tool so far (`assemble()`) is a subprocess
  wrapper. Keeping `run()` the same shape means one pattern to reason about,
  and no per-Python-version/per-platform C-extension build matrix — just one
  native binary.
- **Cost:** slower round-trips than an in-process call. Not a concern yet —
  nothing in the roadmap needs tight per-instruction stepping at Python-call
  speed.

"Similar API to ALE" below means similar verbs and semantics, not the same
binding technology.

## Directory layout

A separate top-level folder, sibling to `src/`, `tests/`, `docs/` — kept
apart because it's a different license (GPL-2.0-or-later, inherited from
Stella) from the rest of this repo (MIT), same reasoning as
`vendor/atari2600/`, just a bigger instance of it:

```
stella-core/
  CMakeLists.txt
  README.md
  vendor/
    README.md
    stella/               # vendored mainline Stella source — emucore, common
                           # (minus SDL files), os/libretro (minus the full
                           # libretro.h ABI). License and exact version:
                           # vendor/stella/NOTICE.md (the only NOTICE here).
  shim/
    stella_core.*         # our API surface over StellaLIBRETRO + System/TIA
                           #   direct access
    main.cpp              # the stella_core binary: JSON-lines commands on
                           #   stdin/stdout (Emulator Protocol v1)
    smoketest.cpp         # the original proof of concept, still built
```

`tbd.tools.emulator` stays in `src/tbd/`, MIT-licensed, and only ever talks
to `stella-core`'s compiled binary over a pipe — same relationship it has
with the `dasm` binary today.

## Command surface (built)

Mirroring ALE's shape where it's actually useful, dropping the parts that
are RL-specific and don't apply here (actions, rewards, lives, game modes),
adding the debugging access that's the whole point of this. Full request/
response shapes are documented at the top of `shim/main.cpp`.

| Command | ALE equivalent | Notes |
|---|---|---|
| `load_rom` | `loadROM()` | **No catalog/MD5 gate**, and never hard-exits on a bad ROM — returns an error response instead. |
| `reset` | `reset_game()` | |
| `step_frame [n]` | (`act()` advances one frame as a side effect) | Via `StellaLIBRETRO::runFrame()`. |
| `step_scanline [n]` | — | New. One `TIA::updateScanline()` call — this is what makes real per-scanline sampling possible; `tbd.tools.emulator.run()` uses this, not `step_frame`, so it can read `COLUBK` between every scanline. |
| `read_memory <addr>` / `write_memory <addr> <val>` | — | New. Full `0x0000`–`0xFFFF` space via `System::peekOob`/`pokeOob` (the `Oob` — out-of-band — variants: debug inspection, not part of the emulated system's own activity). **Note:** a real bus peek of a write-only TIA address like `COLUBK` returns hardware-accurate open-bus garbage, *not* the last-written value — confirmed; that's what `get_tia_registers` is for. |
| `get_ram` / `set_ram <addr> <val>` | `getRAM()`/`setRAM()` | The 128-byte RIOT window, via `M6532`. |
| `get_cpu_state` | — | New. PC, A, X, Y, SP, and `System::cycles()` — the literal "stable framerate" signal — read directly from `M6502`/`System`, not `CpuDebug` (see above). |
| `get_tia_state` | — | New. The *live* in-progress `TIA::scanlines()` (how `run()` detects a frame boundary while stepping scanline-by-scanline) plus `scanlinesLastFrame()` and our own frame counter. |
| `get_tia_registers` | — | New. All 64 of `TIA::registerValue()` — the shadow copy of its write-only registers. This is how `COLUBK` actually gets read. |

**Deferred, not needed yet:** `get_screen`, `step_instruction`. Add them
when a task actually needs them, per the roadmap's own rule.

## Confirmed working — Task 1 passes end to end

`tests/test_assembler.py` and `tests/test_emulator.py`: 5/5 pass, against
the real binary, no mocking. The two emulator tests ("stable framerate",
"red background every scanline") are driven by `tbd.tools.emulator.run()`
calling the protocol above.

One real subtlety the tests themselves caught: a kernel's first full pass
still spends a few scanlines in `VERTICAL_SYNC` before its own
`sta COLUBK` executes, during which the register holds whatever was there
before — stale `0x00`, specifically on that first pass, since nothing had
written it yet. `run()` discards two warm-up frames (the power-on/
`CLEAN_START` transient *and* that first real pass) before trusting steady
state, not one — see the comment above `warmup_wraps_needed` in
`emulator.py` for how this was actually found (a failing test, not
foresight).

One implementation-level gotcha, for whoever touches `shim/main.cpp` next:
**Stella writes stray diagnostics straight to stdout** during console
setup (confirmed: a "File open/read error" from a properties-file lookup
that doesn't matter here, since this project uses Noop repositories) —
which is fatal for a line-oriented protocol sharing that stream. Fixed by
muting real fd 1 (`dup2` to `/dev/null`) for the duration of each command
and restoring it before writing our own JSON response — see `StdoutMute`
in `main.cpp`.

## Resolved along the way

- **Stella version:** pinned to tag `7.0c`, commit
  `c4016ea4891cb0f90979cb35182214af4c1cb4e7` — matches the installed
  Homebrew `stella` used elsewhere in this project.
- **Submodule vs. vendored snapshot:** snapshot, as planned. Exact commit
  recorded in `vendor/stella/NOTICE.md`.
- **Exact file set:** Stella's own `src/os/libretro/Makefile.common` is the
  authoritative list of what its libretro build compiles — used directly
  rather than guessed, plus 4 files that list's own glob (hyphenated
  `frame-manager/` paths) made easy to miss on a first pass. Full accounting
  in `vendor/stella/NOTICE.md`.
- **Protocol format:** JSON-lines, one object per line each direction — via
  the vendored `nlohmann::json` (already present for Stella's own settings
  code, MIT-licensed, no extra dependency).
- **Build trigger:** manual for now — `cmake -S stella-core -B
  stella-core/build && cmake --build stella-core/build`, documented in
  `stella-core/README.md`. `tests/test_emulator.py` skips if the binary
  isn't there yet, same pattern as before.

## Order of work

1. ✅ Vendor the Stella source subset into `stella-core/vendor/stella/`.
2. ✅ Headless console construction — Stella's own
   `StellaLIBRETRO`/`OSystemLIBRETRO`, not a from-scratch `OSystem`.
3. ✅ The real command surface over stdin/stdout (`stella_core.hxx/cxx`,
   `main.cpp`) — `smoketest.cpp` is kept around as a minimal standalone
   sanity check, but isn't what `run()` talks to.
4. ✅ `tbd.tools.emulator.run()` — replaces the old `NotImplementedError`
   stub; `Frame`/`RunResult` now carry real cycle counts and real
   per-scanline `COLUBK` reads.
5. ✅ `skipif` guards removed from `tests/test_emulator.py` — all 5 tests
   pass.
6. ✅ `roadmap.md` updated to point here.

Task 1 is done. Next per the roadmap: Task 2 (per-scanline background color
changes), not detailed yet — written when it's actually started.
