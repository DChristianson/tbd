# Vendored Stella source — not covered by this project's root LICENSE

Source: Stella (<https://stella-emu.github.io/>,
<https://github.com/stella-emu/stella>)
**Version:** tag `7.0c`, commit `c4016ea4891cb0f90979cb35182214af4c1cb4e7` —
matches the Homebrew `stella` 7.0c already used elsewhere in this project
(see [docs/plan/stella-core-integration.md](../../../docs/plan/stella-core-integration.md)).
**License:** GPL-2.0-or-later, same as DASM's vendored include files at
`tests/fixtures/vendor/atari2600/`. This project's root `LICENSE` (MIT) does
not apply to anything under `stella-core/vendor/`.

## What was vendored, and why

Not the whole Stella source tree — the subset needed to run the emulation
core headless, with no GUI/SDL dependency. Two separate things below:
which files are *physically present* in this tree (the directories are
copied wholesale, pruned of SDL), and which of those files `CMakeLists.txt`
actually compiles — an explicit list taken directly from Stella's own
`src/os/libretro/Makefile.common` (the authoritative source for exactly
which files its libretro build needs), not a glob. A few unused files
(`PNGLibrary.*`, `ZipHandler.cxx`) are present but never compiled, which is
fine — they're inert until something lists them.

- **`src/emucore/`** — copied wholesale. This is Stella's own
  GUI-independent emulation core (CPU, TIA, RIOT, cartridge types); nothing
  in it references SDL.
- **`src/common/`** — copied wholesale, then the SDL-specific and
  art-asset files removed (`EventHandlerSDL2.*`, `FBBackendSDL2.*`,
  `FBSurfaceSDL2.*`, `SDL_lib.hxx`, `SoundSDL2.*`, `sdl_blitter/`,
  `stella_icon.hxx`, the `stella*.png` icons). Everything else in `common/`
  (settings repositories, audio queue, TV filters, etc.) has no SDL
  dependency.
- **`src/os/libretro/`** — only the headless platform glue Stella's own
  libretro port already provides: `OSystemLIBRETRO`, `FBBackendLIBRETRO`,
  `FBSurfaceLIBRETRO`, `SoundLIBRETRO`, `EventHandlerLIBRETRO`,
  `FSNodeLIBRETRO`, and `StellaLIBRETRO` (a convenience wrapper around
  `Console` construction and frame-stepping — `create()`/`setROM()`/
  `runFrame()`/`reset()` — that this project's own `shim/` calls directly).
  **Not vendored:** `libretro.cxx`/`libretro.h` (the full libretro C ABI —
  this project talks to `stella-core` over its own stdin/stdout protocol
  instead, see the plan doc), the Android `jni/` tree, and the
  Windows/Makefile build files (replaced by this project's own
  `CMakeLists.txt`).
- **`src/lib/json/`** (same path as upstream) — header-only, needed by `KeyValueRepositoryJsonFile`
  in `common/repository/`. Confirmed no other third-party vendored lib
  (`zlib`, `libpng`, `sqlite`, `httplib`, `nanojpeg`, `tinyexif`) is needed:
  Stella's own `src/os/libretro/Makefile.common` — the authoritative source
  for exactly which files its libretro build compiles — references none of
  them as compiled sources.

**Not vendored at all:** `src/debugger/` (CPU/TIA/RIOT introspection
classes). They looked like the obvious source for register/cycle access,
but `CpuDebug`/`TIADebug`/`RiotDebug` all require a real `Debugger&`, and
`Debugger.hxx` pulls in `DialogContainer`/`DebuggerDialog` — GUI-coupled.
Dragging that in just to satisfy a constructor wasn't worth it: the
same data is available directly and publicly from the plain `emucore`
objects this project's `shim/` already holds a reference to —
`System::cycles()`, `TIA::registerValue()`, `TIA::scanlinesLastFrame()` —
with one exception, below. `src/gui/`, `src/debugger/gui/`, and the other `src/os/`
platform directories (`macos/`, `unix/`, `windows/`) are not vendored.

## Deliberate patch to vendored source

`emucore/M6502.hxx`: the CPU's `PC`/`A`/`X`/`Y`/`SP` register fields are
private, with `CpuDebug`/`CartDebug` as the only friend classes — by design,
since every other consumer is meant to go through those debug wrapper
classes. This project doesn't use them (see above), so a small set of
public accessors were added instead (one of them, `getPC()`, simply
uncomments a line already present upstream but disabled). This is the one
intentional modification to vendored source in this tree; everything else
here is unmodified upstream code.
