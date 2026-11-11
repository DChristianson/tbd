# FlatBuffers for Emulator Protocol v1 — evaluated, not adopted

A spike, prompted by "would FlatBuffers be worth switching to for the
emulator protocol." Short answer: **no, not at today's message sizes** —
and the reason is more interesting than "JSON is fine": Python's
FlatBuffers bindings are measurably *slower* than Python's JSON for the
small, scalar-heavy messages this protocol is made of, while C++'s
FlatBuffers bindings are dramatically *faster* than C++'s JSON for the
same messages. The two sides of the pipe pull in opposite directions, and
at current message sizes the Python-side cost increase outweighs the
C++-side saving. All numbers below are measured, not estimated — see
"Methodology."

## Methodology

Everything in `docs/design/flatbuffers-spike/` is real, runnable code,
not pseudocode:

- `emulator_task1.fbs` — a real schema, covering exactly the 6 request
  types Task 1's `tbd.tools.emulator.run()` actually sends (confirmed by
  grepping `emulator.py` for `cmd=`, not guessed): `load_rom`,
  `step_scanline`, `get_tia_state`, `get_cpu_state`, `get_tia_registers`,
  `quit`. Compiles cleanly with `flatc` (installed via
  `brew install flatbuffers`, Apache-2.0 — fully compatible with this
  project's MIT license, unlike the GPL-2.0 dependencies elsewhere in this
  tree) to both C++ and Python bindings, checked into `generated/`.
- `benchmark.py` — real encode/decode timing on the Python side, using
  real example payloads taken from `stella_core`'s actual output earlier
  in this project (e.g. the exact `get_cpu_state` response
  `{"pc":61440,"a":100,...}` seen in a real CLI run).
- `cpp_benchmark.cpp` — the same comparison on the C++ side, compiled
  directly against the generated header and the same `nlohmann::json`
  single header already vendored for `stella-core`.

## Results: today's message sizes (12-220 bytes)

**Python side** (what `tbd.tools.emulator` does — encode requests, decode
responses):

| message | JSON bytes | FB bytes | JSON encode | FB encode | JSON decode | FB decode |
|---|---|---|---|---|---|---|
| `load_rom` request | 54 | 68 | 1.1us | 17.5us | — | — |
| `step_scanline` request | 24 | 20 | 0.9us | 7.7us | — | — |
| `get_cpu_state` response | 81 | 64 | 1.4us | 27.0us | 1.6us | 14.0us |
| `get_tia_state` response | 78 | 52 | 1.2us | 21.1us | — | — |
| `get_tia_registers` response (64B array, naive per-element decode) | 220 | 108 | 3.6us | 20.9us | 3.2us | 136.0us |
| `get_tia_registers` response (64B array, `AsNumpy()` decode) | 220 | 108 | 3.6us | 20.9us | 3.2us | 8.2us |

**C++ side** (what `stella-core` does — decode requests, encode
responses):

| message | JSON encode | FB encode | JSON decode | FB decode |
|---|---|---|---|---|
| `get_cpu_state` response | 2.06us | **0.15us** | 1.24us | **~0us** |
| `get_tia_registers` response | 2.65us | **0.11us** | 4.29us | **0.02us** |

The asymmetry is the finding: **FlatBuffers is 13-20x faster to encode
and essentially free to decode in C++**, exactly matching its design
intent (direct struct access, no parse step, no allocation) — but **7-20x
slower to encode and, without care, 40x slower to decode in Python**,
because the generated Python bindings call one Python-level function per
field (`Start`/`Add`/`End`, or `body.Registers(i)` per array element), and
Python's `json` module is C-accelerated while that per-field call pattern
isn't. The naive-vs-`AsNumpy()` decode rows above show this directly: using
the zero-copy numpy accessor instead of a Python list comprehension drops
a 64-byte array's decode from 136us to 8.2us — still slower than JSON's
3.2us at this size, but not by 40x anymore. The lesson: FlatBuffers in
Python is only as fast as its design intends if you use the vectorized
accessors, and even then, size matters (next section).

## Results: how this scales with payload size

The 64-byte case above is close to a wash once you use `AsNumpy()`. Sizes
matter a lot here — measured by scaling the same array up to 33,600 bytes
(one NTSC frame buffer's worth, `160×210`, the size the deferred
`get_screen` command would actually need):

| array size | JSON bytes | FB bytes | JSON encode | FB encode | JSON decode | FB decode |
|---|---|---|---|---|---|---|
| 64 B | 273 | 108 | 3.7us | 21.0us | 3.2us | 8.3us |
| 1024 B | 4707 | 1068 | 37.8us | 40.0us | 33.1us | 8.2us |
| 4096 B | 18747 | 4140 | 143.8us | 96.8us | 127.6us | 8.2us |
| 33600 B (one screen) | 153543 | 33644 | 1270.1us | 662.0us | 1075.5us | **8.4us** |

Two things stand out: FlatBuffers' wire size is consistently ~4-5x smaller
(JSON spends 3-4 ASCII bytes per byte of actual data), and its decode time
is **flat regardless of size** — genuinely zero-copy, so reading a
33KB buffer costs the same ~8us as reading 64 bytes. JSON decode scales
linearly and is already 127x slower than FlatBuffers by screen-buffer
size. Encode crosses over earlier too (FlatBuffers wins from ~4KB up).

## What this means for the real round trip

Measured directly against the actual `stella_core` binary (not a
component estimate): a real `get_cpu_state` round trip over the existing
subprocess pipe — Python encode, write, OS context switch, C++ read +
decode + dispatch + encode, write, OS context switch, Python read +
decode — costs **49us end to end** (5000 real round trips, 0.245s
total). Python's own JSON encode+decode in that round trip is ~3us of
that 49us — roughly 6%. The rest is C++-side `nlohmann::json` work (small,
per the table above) and, almost certainly the dominant piece, the raw
subprocess pipe IPC itself (two syscalls, two context switches). Swapping
serialization formats cannot touch that IPC floor at all — it's not a
serialization cost.

Netting out the measured per-message deltas against that 49us baseline
for `get_cpu_state`: Python request-encode gets ~7us worse, C++
request-decode gets maybe ~1us better, C++ response-encode gets ~1.9us
better, Python response-decode gets ~12.5us worse. Net: **roughly 17us
worse per round trip, not better** — about a 35% slowdown, driven entirely
by the Python-side binding overhead, for a message this small.

## Cost, beyond raw speed

- **Framing breaks.** JSON-lines' whole convenience — one object per
  newline, human-readable — doesn't survive the switch. FlatBuffers
  buffers are binary and can contain literal newline bytes, so the
  protocol would need length-prefixed framing instead, a real (if small)
  protocol-level change on both ends.
- **Debuggability drops.** This conversation's whole debugging workflow —
  `printf '{"cmd":"load_rom",...}' | stella_core` to hand-test a command —
  stops working. You'd need a small tool just to construct/inspect
  messages by hand.
- **A codegen step enters the build.** Schema changes require re-running
  `flatc` for both languages and keeping the generated code in sync, vs.
  today's "add a field to a dict literal" on both ends with zero build
  step.
- **A new Python dependency.** `flatbuffers`, and — to get anywhere near
  competitive performance on array-shaped messages — `numpy` too. Neither
  is currently a dependency of this project.

## Recommendation

Not now. At today's message sizes (all well under 1KB, mostly under
256 bytes), plain JSON-lines is faster end-to-end in the one direction
that was actually measured to matter (Python, which does both the
request-encode and response-decode legs), and the IPC floor dominates the
real round-trip cost regardless of format. The added cost — framing,
codegen, debuggability, two new dependencies — isn't paid for by a
performance win that doesn't exist yet.

**Worth revisiting specifically when `get_screen` gets built** (still
deferred per `emulator-protocol.md` — no task needs it yet). A full frame
buffer is ~33KB, squarely in the regime where this spike's own numbers
show FlatBuffers winning decisively: ~4.5x smaller on the wire, ~2x faster
to encode, and 127x faster to decode in Python specifically because
`AsNumpy()` decode cost doesn't grow with size at all. That's a real,
measured case for adopting it — just not the small scalar messages Task 1
needed.
