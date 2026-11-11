Our own C++ code goes here — not vendored, ours to write and maintain:

- `headless_osystem.*` — a null video/audio `OSystem` so Stella's `Console`
  constructs and runs with no SDL/display dependency.
- `stella_core.*` — the API surface described in the plan doc
  (`load_rom`, `step_frame`/`step_scanline`/`step_instruction`,
  `read_memory`/`write_memory` over the full address space, `get_cpu_state`,
  `get_tia_state`, `get_screen`, `get_ram`/`set_ram`), built on Stella's own
  `CpuDebug`/`TIADebug`/`RiotDebug`/`CartDebug`.
- `protocol.*` — the stdin/stdout command/response handling
  `tbd.tools.emulator` talks to as a subprocess.
- `main.cpp` — the binary's entry point.

Not yet written. See
[docs/plan/stella-core-integration.md](../../docs/plan/stella-core-integration.md).
