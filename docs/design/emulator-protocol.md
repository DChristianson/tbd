# Emulator Protocol v1

A backend-agnostic interface for driving an Atari 2600 emulator from Python.
`stella-core` is the first (and so far only) implementation; this doc is
the contract, not Stella's implementation of it — see
[stella-core-integration.md](../plan/stella-core-integration.md) for that.

## Why this is its own doc

Almost nothing in the command set below is actually Stella-specific — it's
Atari-2600-hardware-specific (one 6507 CPU, one TIA, one RIOT), which any
faithful emulator of this console needs to expose the same way. The one
thing that *is* implementation-specific is the transport and the compiled
binary. Separating the two means a second backend (a different emulator,
built the same way) could speak this exact protocol, and
`tbd.tools.emulator` wouldn't need to know which one it's talking to —
just which binary path to launch.

## Transport

A subprocess, talked to over its stdin/stdout — not an in-process
extension (see stella-core-integration.md's "Architecture decision" for
why: it keeps a GPL-licensed emulator core at a process boundary rather
than raising an in-process-linking license question, and it's the same
shape every other Layer-0 tool in this project already uses).

**Framing:** one JSON object per line, each direction. A request is a line
on the backend's stdin; the matching response is the next line on its
stdout, in order — no concurrent/pipelined requests, no request IDs. A
backend must write *only* protocol responses to stdout; anything else it
might normally log needs to go to stderr or be suppressed (`stella-core`
had to fix exactly this — see "File open/read error" in
stella-core-integration.md).

## Conventions

- Every response is a JSON object with `"ok": true` or `"ok": false`.
- `"ok": false` responses carry an `"error"` string describing what went
  wrong. They are not fatal — the backend keeps running and accepting
  further commands.
- A request's keys beyond `"cmd"` are that command's arguments.
- Numeric fields are plain JSON numbers (no hex strings, no base64) —
  addresses and register/memory values are small enough that this isn't a
  size concern (see the FlatBuffers cost/savings note below, where it
  becomes one).
- `cycles` is the CPU's cycle counter since the ROM was loaded (or since
  the last `reset`), unsigned, no defined wraparound behavior a caller
  should rely on.

## Commands

| Command | Args | Response (beyond `ok`) | Hardware concept |
|---|---|---|---|
| `load_rom` | `path` | `error` on failure | Load a cartridge image. **Must accept any valid image — no catalog/allow-list, and must never hard-exit the process on an unrecognized one** (a real constraint this protocol exists partly to state explicitly — see ALE's catalog gate in roadmap.md for the cautionary example). |
| `reset` | — | — | Reset the system to power-on state. |
| `step_frame` | `n` (default 1) | — | Advance by `n` whole frames. |
| `step_scanline` | `n` (default 1) | — | Advance by `n` scanlines. The finer-grained sibling of `step_frame` — this is what makes real per-scanline observation possible. |
| `read_memory` | `addr` (0-65535) | `value` | Read one byte from the full `$0000`-`$FFFF` address space, out-of-band (doesn't perturb the emulated system's own state/timing). Hardware-accurate: a write-only address (e.g. a TIA color register) returns open-bus garbage here, *not* the last value written to it — see `get_tia_registers` for that. |
| `write_memory` | `addr`, `value` | — | Write one byte, same address space, same out-of-band semantics. |
| `get_ram` | — | `ram`: 128 ints | The CPU-visible general-purpose RAM (RIOT RAM on the 2600). |
| `set_ram` | `addr` (0-127), `value` | — | Write one byte of that RAM. |
| `get_cpu_state` | — | `pc`, `a`, `x`, `y`, `sp`, `cycles` | The 6507's registers and cycle count — the ground-truth signal for "did this kernel's timing change." |
| `get_tia_state` | — | `scanlines` (live, in-progress count), `scanlines_last_frame`, `frame_number` | Where the video chip is right now. `scanlines` is what a caller stepping scanline-by-scanline (rather than via `step_frame`) watches wrap back to a small number to detect a frame boundary. |
| `get_tia_registers` | — | `registers`: 64 ints | The video chip's own shadow copy of its write-only registers (color, position, etc.) — the thing `read_memory` can't give you for these addresses, by design. |
| `quit` | — | — | Backend exits after responding. |

**Deferred, not implemented by any backend yet** (add when a task actually
needs them, not before): `get_screen` (full rendered frame, raw palette
indices), `step_instruction` (single 6507 instruction).

## What a second backend would need

- The same framing and conventions above.
- Every non-deferred command in the table, with the same argument/response
  shapes.
- The same hard requirement on `load_rom`: no catalog gate, no hard exit.
- Nothing else — no shared code, no shared build system. A second backend
  is free to be implemented however it wants internally, as long as it
  speaks this protocol on stdin/stdout.

## Version history

- **v1** (this version): the command set `stella-core` implements as of
  Task 1 going green. Not yet exercised by a second backend — see the
  "cost and savings" note in
  [emulator-protocol-flatbuffers.md](emulator-protocol-flatbuffers.md) for
  a serious alternative transport considered and not (yet) adopted.
