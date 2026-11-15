# stella-core

A headless, debuggable build of Stella's emulation core, talked to over a
stdin/stdout protocol by `tbd.tools.emulator` — not a Python extension, a
separate native binary. See
[docs/plan/stella-core-integration.md](../docs/plan/stella-core-integration.md)
for the full plan, rationale, and current status.

**Status:** done — Task 1 passes end to end (5/5 tests) through this. The
real stdin/stdout protocol (`stella_core.hxx/cxx`, `main.cpp`) is what
`tbd.tools.emulator.run()` actually talks to; `shim/smoketest.cpp` is kept
around as a minimal standalone sanity check, not the real shim.

## Why this is a separate top-level folder

Stella is GPL-2.0-or-later. The rest of this repository is MIT (see the root
[LICENSE](../LICENSE)). Everything under `stella-core/vendor/` is
GPL-2.0-or-later — see `vendor/stella/NOTICE.md`. `tbd.tools.emulator` in
`src/tbd/` only ever talks to this folder's compiled binary over a pipe, the
same relationship it has with the `dasm` binary — never an in-process
import — so that boundary is what keeps the rest of the repo cleanly MIT.

## Building it

```
cmake -S stella-core -B stella-core/build -DCMAKE_BUILD_TYPE=Debug
cmake --build stella-core/build -j8
```

Produces `stella-core/build/shim/stella_core` (the real binary
`tbd.tools.emulator.run()` drives) and `stella_core_smoketest` (a minimal
standalone check — loads a ROM, prints cycle/color data, exits):

```
stella-core/build/shim/stella_core_smoketest /path/to/red_background.bin 5
```

(`red_background.bin` is the assembled output of
`tasks/t1/red_background.asm` — build it with
`tbd.tools.assembler.assemble(path, include_dirs=[tbd.paths.HEADERS])`.)

## Layout

```
stella-core/
  CMakeLists.txt
  vendor/
    stella/
      NOTICE.md            # license: GPL-2.0-or-later; exact version + what's vendored and why
      src/                  # vendored mainline Stella source (emucore, common
                             # minus SDL, os/libretro minus the full libretro.h ABI)
  shim/
    smoketest.cpp          # proof of concept — loads a ROM, prints cycle/color data
    stella_core.*          # not written yet — the real API surface
    protocol.*             # not written yet — stdin/stdout command handling
    main.cpp                # not written yet — replaces smoketest.cpp
```
