# The Checker

The checker runs a ROM in the simulator and verifies properties of what it did. It is how Racer decides correctness: reference ROMs are checked against hand-written properties now, and compiled Racer programs will be checked against properties generated from their constraints. See [How the simulator, checker, and emulators relate](milestone-1.md#how-the-simulator-checker-and-emulators-relate).

## Running it

```sh
cargo run --release -p racer-checker -- reference/*.check.toml          # readable summaries
cargo run --release -p racer-checker -- --json reference/*.check.toml   # one JSON report per line
```

The command is `racer-check`. It exits with 0 if every file passed, 1 if any failed, and 2 if a file could not be checked (a missing ROM, or a mistake in the file).

## Check files

A check file is TOML, kept next to the ROM it describes and named after it (`frame-0a.check.toml`):

```toml
rom = "build/frame-0a.bin"   # relative to the check file

[run]
frames = 10                  # frames to run
skip_frames = 1              # frames not checked: the first shows power-on state (default 1)
zero = true                  # run from an all-zero power-on state (default true)
random_seeds = 100           # and from random power-on states with seeds 1-100 (default 0)

[[property]]
kind = "frame_length"
lines = 262
```

Every property is checked in every frame after the skipped ones, in every run. Frames are numbered from 1, starting at the first `VSYNC`. A check that would examine no frame fails.

**Expected failures.** A ROM that must be rejected lists the properties it must fail:

```toml
expect_failures = ["frame_length"]
```

The file then passes only if every run fails exactly those properties: not fewer, and not others.

**Names.** A property may have a `name`, used in reports and in `expect_failures`. Without one, its kind is its name.

**Some frames only.** A property with `on_frames`, a list of ranges of frames, is checked only on those frames: the frames on which a screen shows, for a property of its picture (milestone 9). Without it, every checked frame.

**Input.** A check file can give a script of what a player does, as `[[input]]` entries, each holding controls from its frame on, until the next entry (milestone 9):

```toml
[[input]]
from = 3                 # from frame 3 on
joystick0 = ["right", "fire"]
console = ["reset"]

[[input]]
from = 5                 # nothing held from frame 5 on
```

Each joystick, `joystick0` and `joystick1`, takes `up`, `down`, `left`, `right`, and `fire`, but not two opposite directions; `console` takes `reset` and `select`. What an entry does not name is released, and before the first entry nothing is held. The simulator applies each frame's input as `VSYNC` turns on, starting the frame, so a program that reads its input once a frame sees one value for the whole frame. The same script is used for every run, zero and random. A file with a script is a scripted session: the unit of a played game's test.

**Ranges** are half-open, `[start, end)`, as in the design sketches: `lines = [40, 232]` is lines 40 through 231. Lines are numbered within the frame, line 0 being the line on which `VSYNC` turns on. Color clocks are numbered within the line, 0-227; 68 is the first visible clock. A write's clock is the first clock that shows its effect ([`BOARD-CYCLE-1`](target-model.md#board-cycle-1-order-of-events-within-a-cycle)).

## Property kinds

| Kind | Parameters | Requires, in every checked frame |
|---|---|---|
| `frame_length` | `lines` | The frame is exactly `lines` lines long |
| `frame_length_range` | `min_lines`, `max_lines` | The frame is from `min_lines` to `max_lines` lines long, inclusive. (Added in tbd's vendored copy.) |
| `signal_on_lines` | `signal` (`"sync"` or `"blank"`), `first_line`, `lines` | The signal is output as one unbroken span that begins on `first_line` and lasts exactly `lines` × 228 clocks. For sync, this checks the duration of `VSYNC` wherever in the line it begins |
| `signal_span` | `signal` (`"sync"` or `"blank"`), `first_line`, `min_clocks`, `max_clocks` | The signal is output as one unbroken span that begins on `first_line`, at any clock, and lasts from `min_clocks` to `max_clocks` clocks inclusive: `signal_on_lines` with a tolerance. (Added in tbd's vendored copy.) |
| `picture_block` | `lines`, `start_window`, `clocks`, `color` | The frame's first line showing any color is in `start_window`; from it, `lines` consecutive lines show `color` on every clock in `clocks`; no other line shows any color. A correct picture sitting a line or two off. (Added in tbd's vendored copy.) |
| `blank_outside` | `lines` | No color is output on any line outside `lines` |
| `constant_output` | `lines`, `clocks`, `color` | Every clock in `clocks` of every line in `lines` shows `color` |
| `output_by_line` | `lines`, `clocks`, `start`, `step` | Every clock in `clocks` of line `lines[0] + n` shows `(start + step × n) mod 256` |
| `output_colors` | `lines`, `clocks`, `colors` | Every clock in `clocks` of line `lines[0] + n` shows `colors[n]`. `colors` has one color for each line in `lines` |
| `write_window` | `chip` (`"tia"` or `"riot"`), `register`, `lines`, `clocks` | On every line in `lines`, `register` is written at least once, and every write to it takes effect at a clock in `clocks` |
| `write_window` | `chip`, `register`, `lines`, `windows` (a list of ranges, in place of `clocks`) | On every line in `lines`, `register` is written exactly once in each window, in order, and nowhere else: for a register written more than once on a line, such as a playfield register rewritten for the right half. A property gives `clocks` or `windows`, not both |
| `image` | `lines`, `clocks`, `rows`; and `behind`, the names of object properties drawn in front of it | Over `lines` by `clocks`, each of `rows`, `{ lines = n, runs = [[color, length], ...] }`, covers the next `n` lines, and each of its runs the next `length` clocks of every one of them, in `color`. The rows' lines must add up to `lines`, and each row's runs to `clocks`; a file whose do not is rejected when it is loaded. With `behind`, a clock covered on the same frame by a set bit of a named object, showing that object's color, is not checked |
| `object` | `lines`, `rows`, `color`, `positions`; `shown` and `frames`, together, for an object that may flicker; and `behind`, the names of object properties drawn in front of it | On frame `n`, the object is at `[x, y] = positions[n - 1]`: its row `r` is drawn on line `lines[0] + y + r`, its bits from visible clock `x`, bit 7 leftmost, and every set bit shows `color`; rows outside `lines`, and bits past the last visible clock, are not checked ([decision 0010](decisions/0010-checking-runtime-dependent-output.md)). With `shown` and `frames`, `1 <= shown <= frames`, the object need be drawn in full on only `shown` of every `frames` consecutive checked frames; a frame on which it is not counts as hidden, and a failure names the first run of frames that hides it too often. Fewer checked frames than `frames` is a failure. With `behind`, a bit covered on the same frame by a set bit of a named object, where the picture shows that object's color, is not checked; a name that is not another object property in the file is rejected when it is loaded |

| `audio_registers` | `channel` (0 or 1), `settings` | At the end of frame `n`, the channel's `AUDC`, `AUDF`, and `AUDV`, as last written, are `settings[n - 1]`, a list `[audc, audf, audv]`. A sound starting, changing, and stopping is its registers changing; a register never written fails |
| `audio_pitch` | `channel`, `frames`, `hz`, `cents`, `clock_hz` | Over `frames`, the channel's output on every audio clock repeats with a period that, at `clock_hz` audio clocks a second, is within `cents` of `hz`. Checked once, on the last of `frames`; silence, or an output that does not repeat, fails. The channel's setting must not change within `frames` |
| `ram` | `address` (`0x80`-`0xFF`), `value` or `values`, optional `line` | On frame `n`, at the start of `line`, or at the frame's end without one, the byte holds `values[n - 1]`, or `value` on every checked frame (milestone 9, task 13). For probes and reference ROMs: a compiled program's check file does not read its RAM (decision 0010, amended) |
| `state` | `register`, `value` or `values`, optional `line` | The same, for a register or counter: the TIA's write registers as last written (`COLUBK`, `AUDC0`, ...), its read registers (`CXM0P` to `CXPPMM`, `INPT4`, `INPT5`), each audio channel's counters (`AUDIO0_DIVIDER`, `AUDIO0_PULSE`, `AUDIO0_NOISE`, and channel 1's), and the RIOT's `INTIM`, `SWCHA`, and `SWCHB` as a read would see them |

A channel is checked by its registers on every frame, and its pitch over a stretch where its setting is held, rather than sample by sample: where its waveform starts depends on when its divider last counted, which differs between emulators after an `RSYNC` (decision 0002, choice 17).

An object that may flicker, as a compiled program's check file states one drawn on 1 frame in 2 (milestone 7):

```toml
[[property]]
name = "rock"
kind = "object"
lines = [40, 232]
rows = [0x3c, 0x7e, 0xdf, 0xff, 0xfb, 0xff, 0x7e, 0x3c]
color = 0x1e
positions = [[120, 91], [119, 91], ...]   # one for each frame
shown = 1
frames = 2
behind = ["ufo"]
```

Without `behind`, the rock would count as hidden on a frame where the UFO, drawn in front of it as the program asks, covers one of its bits.

Its failure names the frames and the first miss in them:

```
rock failed: frame 50, line 94, clock 117: shown on 0 of frames 50-51, hidden on frames 50, 51; on frame 50, color $00; required the object shown on at least 1 of every 2 consecutive frames, shown meaning color $1e (row 4, bit 7, of the object at visible clock 49, picture line 50) (in 3 frames)
```

An image, as the reference ROMs for sketch 3 state theirs:

```toml
[[property]]
name = "image"
kind = "image"
lines = [40, 232]
clocks = [68, 228]
rows = [
    { lines = 16, runs = [[0x00, 160]] },
    { lines = 16, runs = [[0x00, 12], [0x4e, 16], [0x00, 132]] },
    # ...
]
```

Registers are given by name (`"COLUBK"`, `"TIM64T"`) or by number: the address modulo 64 for the TIA, modulo 32 for the RIOT.

## Reports

For each file, the checker reports whether it passed and, if not, the first run whose results were unexpected, with each failing property's first failure:

```
reference/broken-late-write.check.toml: FAILED (1 of 1 runs unexpected)
  first unexpected run: zero
    colubk_in_hblank failed: frame 2, line 40, clock 96: COLUBK write took effect at color clock 96; required before 68 (in 9 frames)
```

A failure names the property; the frame, and the line and clock where they apply; what was observed; what the property requires; and in how many frames it failed. With `--json`, the same information is given as fields (`property`, `frame`, `line`, `clock`, `observed`, `required`, `frames_failed`), for tools and agents.
