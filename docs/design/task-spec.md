# Task specs: adding a task or variant

**Status:** built. The layout, `check.py` and the per-variant tests are
tbd-818.3 (`src/tbd/eval/tasks.py`, `checks.py`, `tests/test_tasks.py`);
`Racer.frames()` is tbd-818.19 (racer-check `--frames`). This doc is the
layout and the checklist for adding to it, followed by what each Task 1
variant takes.

Goal: adding a task or variant should be a folder of files and nothing else.
Each variant has its own copy of its prompt and its own check files, even when
they are nearly the same as another variant's. You can read each variant
alone, and changing one can't silently change another. Only checker features
that racer-check lacks should need code.

## Layout

One folder per variant: `tasks/<task>/<variant>/`. A variant is a complete
task on its own. The grid's `task` and `variant` fields are the two folder
names.

```
tasks/t1/canonical/
    prompt.txt            the exact text sent to the model
    reference.asm         hand-written solution; proves the check is passable
    check.toml            strict tier: decides pass/fail
    relaxed.check.toml    optional: near-miss tier, run when strict fails
    check.py              optional: when a check file alone can't say it
    broken/*.asm          optional: programs that must fail the check
```

- **prompt.txt** is sent byte for byte, without its final newline. It
  includes the shared boilerplate (target, assembler, headers, reply format).
  That boilerplate is copied into every variant on purpose, so the prompt
  recorded for a run is exactly what's in the file.
- **reference.asm** builds with `dasm reference.asm -f3 -I../../../vendor/atari2600
  -obuild/reference.bin`. Its header comments describe the solution, so none
  of that wording may appear in the prompt.
- **check.toml / relaxed.check.toml** are plain racer check files
  (`vendor/racer/checker.md`) with `rom = "build/reference.bin"`. The grader
  points `rom` at the attempt's program. Racer rejects unknown keys, so
  nothing tbd-specific goes in these files.
- **check.py** is for anything a check file can't express: tolerances,
  informational properties, generated check files, or a check written in
  Python. Without it, the grader runs `check.toml` (and
  `relaxed.check.toml`, if present) as they are.

A variant whose check file says everything needs only three files:
`prompt.txt`, `reference.asm` and `check.toml`.

### check.py's interface

```python
# tasks/t1/canonical/check.py
from tbd.eval.checks import Racer

INFORMATIONAL = {"vsync"}            # recorded, not graded
RED = [0x42, 0x44, 0x46]             # the prompt says "red", not a value

def check(racer: Racer) -> dict:
    return racer.run("check.toml", accept={"background": RED})

def relaxed(racer: Racer) -> dict:   # optional: the near-miss tier
    return racer.run("relaxed.check.toml", accept={"picture": RED})
```

- `check` is required and `relaxed` is optional. Each returns a report in
  racer-check's `--json` shape (`passed`, `runs`, `unexpected[].failures[]`
  with `property`, `frame`, `line`, `clock`, `observed`, `required`).
  Categorizing, attempt records and tool feedback stay the same whether a
  report came from a check file or from Python.
- `Racer` is bound to the attempt's folder and ROM, and saves each check file
  it runs, with its result, there, as case0 does today. Its methods:
  - `run(path_or_toml, accept=None)` runs a check file from the variant
    folder, or TOML text that `check.py` built.
  - `run(..., accept={property: colors})` handles the color tolerance. If a
    property fails showing one of its accepted colors, it rewrites `color`
    in that property's block only, then re-checks once with every
    substitution applied together. This is Task 1's current $42–$46 retry,
    keyed by property name, so a variant with two colored regions gets one
    re-run, not one per combination.
  - `frames(frames=100, seeds=2)` returns the simulated output itself, for
    checks written in Python. `checks.failure`, `checks.report` and
    `checks.merge` build and combine reports in racer's shape. See (d) below.
- Optional `INFORMATIONAL` lists property names that are recorded but not
  graded.

The test suite imports every `check.py` and runs its `check` on the
reference. That's the only special handling it gets.

### Naming conventions the grader relies on

- `frame_length`, `frame_length_range`, `vsync` and `vsync_span` are
  categorized as `timing` (`grading.TIMING_PROPS`). Every other failing
  property makes the attempt `output_mismatch`. Use these names for timing
  properties in new check files.
- Property names are what attempt records and the grid report refer to. Keep
  a property's name when copying it to a new variant.

## Adding a task or variant

1. `cp -r tasks/t1/canonical tasks/<task>/<variant>` (or start from whichever
   existing variant is closest).
2. Edit `prompt.txt`.
3. Write `reference.asm`.
4. Edit `check.toml`. Remove `relaxed.check.toml` if no near-miss tier makes
   sense yet. Edit `check.py`, or delete it if the check files say
   everything.
5. Run `python -m pytest tests/test_tasks.py -k <task>-<variant>`. The test
   finds the folder on its own; there is no list to update.
6. Dry run against a model, then let the user look at the request before any
   real samples (see the beads memory about runs never being deleted):
   `python -m tbd.eval.case0 --task <task> --variant <variant> --samples 1`.

### What the test suite checks, for every `tasks/*/*/prompt.txt`

- `reference.asm` assembles.
- It passes the strict check, and the relaxed tier if there is one
  (through `check.py` if present, otherwise the check files).
- Every check file loads (racer-check exits 0 or 1, never 2).
- Every `broken/*.asm` assembles and fails the strict check.
- Names in `check.py`'s `accept` and `INFORMATIONAL` are properties that
  exist in the check files.
- The prompt shares no comment line with `reference.asm` (a cheap leak check).

### How long checking takes

Measured on the dev container with Task 1's reference:

| What | One run (100 frames) | Task 1's check (101 runs) |
|---|---|---|
| racer-check, per color clock | ~74 ms | **7.5 s** |
| stella-core, stepping frames only (no checking) | ~70 ms, plus 0.2 s startup | ~27 s |
| stella-core, COLUBK read once per scanline over JSON | ~1.2 s | ~2 min |

When only emulating, Stella is about as fast as racer's simulator. It's
slower at checking because the shim returns state as one JSON request per
scanline, and once per scanline is weaker than racer's every-clock check
(it can't see a mid-line color change, which (c) depends on). Getting
racer's per-clock coverage from Stella would need a new shim command that
returns the whole frame. Even then, Stella has no random power-on states in
the shim. So Stella stays a cross-check (tbd-818.16), not the grader.

So each variant adds about 15 s to the suite (strict plus relaxed), and six
variants take about 1.5 minutes. racer-check is single-threaded and the
container has 10 cores, so `pytest -n` takes most of that away. Speed up the
suite that way rather than trimming a variant's check files for tests.

## Code changes for tbd-818.3 (done)

1. `tbd/eval/tasks.py`: `TaskSpec` (paths plus the imported `check.py`, if
   any), `load(task, variant)`, and `all_specs()` (finds every folder that
   has a `prompt.txt`).
2. `tbd/eval/checks.py`: `Racer`, which takes over Task 1's `check_red` and
   `red_retry_color` as `run(..., accept=...)`. Rewriting a color in one
   property's block replaces case0's file-wide regex.
   `checks.grade_program(spec, code, workdir)` assembles the program, calls
   `check` (and `relaxed` on a timing or output failure), and categorizes the
   result.
3. `case0.py`: add `--task` and `--variant` (defaulting to `t1`/`canonical`).
   The prompt and check files come from the spec, `job_id` gets the variant,
   and `config.json` records the spec's files. `PROMPT`, `ACCEPTED_COLORS`
   and the check paths go away. (Renaming case0 to a generic runner belongs
   to tbd-818.5.)
4. `regrade.py`: load the spec from the job's `config.json` (`task`,
   `variant`). That file already records `t1`/`canonical`.
5. Move `tasks/t1/*` to `tasks/t1/canonical/` under the names above
   (`git mv`), and add the `check.py` shown earlier. Old runs' `config.json` still names
   `tasks/t1/red_background.check.toml`. Their saved copies of the check file
   and the git sha in the manifest still say what ran.
6. `tests/test_tasks.py`: the parametrized checks above. Task 1's reference
   test in `test_racer_check.py` and the 263-line case move into
   `tasks/t1/canonical/broken/`.
7. `README.md`, `stella-core/README.md` and the tests' fixture paths point at
   `tasks/t1/canonical/`.

The attempt record's `grade.color` becomes `grade.accepted`, a map of
property name to the substituted value, because there can be more than one.

## Looking ahead: the Task 1 variants

All five use checker features that already exist except (d). The NTSC
values come from Stella's palette
(`stella-core/vendor/stella/src/common/PaletteHandler.cxx`).

| | Variant | Check file | Code | Hard part |
|---|---|---|---|---|
| a | `blue`: solid non-red | canonical with a different `color` | none | none; choose the color |
| b | `split-tb`: top / bottom | two `constant_output` blocks | none | no relaxed tier yet |
| c | `split-lr`: left / right | two `constant_output` blocks by clock | none | none (score-mode playfield) |
| d | `cycle`: changes each frame | `check.py` over racer's frames | racer: frame export | the sequence the prompt gives |
| e | `pal`: red, PAL | 312 lines, PAL reds | none | choose the PAL frame layout |

### a. Non-red solid background (`t1/blue`)

The prompt is canonical with "blue" in place of "red". Copy the canonical
check files, change `color = 0x42` to `0x84` in `background` and `picture`,
and in `check.py` change `RED` to `BLUE = [0x82, 0x84, 0x86]`
(NTSC hue 8; Stella shows $84 as #2d32b8). The reference is canonical with a
different `lda`. No code.

Decision: which color. Blue is also common in tutorials. A color whose name
covers fewer hues (green is $Cx and partly $Bx/$Dx) makes the `accept`
range harder to defend. Blue, hue 8, is the cleanest choice.

### b. Two colors, top and bottom (`t1/split-tb`)

Prompt: "...sets the background to red on the top half of the screen and
blue on the bottom half." The strict check is canonical with `background`
split in two:

```toml
[[property]]
name = "top"
kind = "constant_output"
lines = [40, 136]
clocks = [68, 228]
color = 0x42

[[property]]
name = "bottom"
kind = "constant_output"
lines = [136, 232]
clocks = [68, 228]
color = 0x84
```

`check.py` passes `accept={"top": RED, "bottom": BLUE}`. The reference writes COLUBK once more
after line 96 of the picture. No code.

There's no relaxed tier yet: `picture_block` takes one color. A near-miss
tier would need a racer `picture_block` that takes a list of
`{lines, color}` bands. Postpone that until baseline runs show shifted-frame
near misses on this variant.

Decision: does the strict check pin the boundary at exactly line 136? "Half"
of a 192-line picture is exact, so pinning it is defensible, and leaving
lines 135–137 unchecked would tolerate an off-by-one. Start exact.

### c. Two colors, left and right (`t1/split-lr`)

Prompt: "...red on the left half of the screen and blue on the right half."
Use two `constant_output` blocks over lines [40, 232]: left
`clocks = [68, 148]` and right `clocks = [148, 228]`.

The reference uses playfield score mode, with no mid-line timing at all.
Set `PF0`/`PF1`/`PF2` to all ones and `CTRLPF` bit 1 (score mode), so the
left half of the playfield is drawn in `COLUP0` (red) and the right half in
`COLUP1` (blue). The right half starts at exactly clock 148 (68 + 20
playfield bits × 4 clocks), so the boundary can be pinned exactly. The
kernel is canonical's, plus four register writes before the picture. No
code.

Decision: whether to leave a few clocks around 148 unchecked. A model that
instead rewrites COLUBK mid-line can only land a write every third clock
(one CPU cycle), so it may not be able to switch at exactly 148. The check
sees only output, so any method is allowed. The question is whether being
off by up to 2 clocks should still pass. Start exact. If baseline runs show
COLUBK solutions missing 148 by a clock or two, add a relaxed tier with the
gap: left `[68, 146]`, right `[151, 228]`.

No relaxed tier yet, for the same reason as (b).

### d. Background color changes every frame (`t1/cycle`)

No property kind checks a color that changes from frame to frame.
`constant_output` takes one color for every frame, and `state` reads the
COLUBK register, not what's on screen. So (d) is the first variant with a
`check.py` that does its own checking:

- **The frame timing still comes from a check file.** `check.toml` keeps
  canonical's `frame_length`, `vsync_span` and `blanking`. `check.py` runs it
  through `racer.run` and merges the report with its own.
- **The color part is written in Python** over the frames racer simulated:
  each checked frame is solid inside [40, 232] × [68, 228], and each frame's
  color differs from the one before by the step the prompt gives. Python
  makes the rules easy to adjust. "Any start, fixed step" takes care of
  phase: whether the program increments before or after drawing its first
  frame is a detail the prompt doesn't settle, and it shouldn't fail an
  attempt. A looser "different color every frame" is also a one-line
  change, if we'd rather measure that.
- **What this needs from racer: a frame export.** The checker already builds
  each frame as a `VideoFrame` (every line's signals, clock by clock), and
  every property is checked against it. Add a `racer-check --frames` option
  (or a small `racer-frames` binary next to it) that writes those frames for
  the zero run and chosen seeds. Write each line as runs, `[[clock, color],
  ...]`, because a solid frame is a few runs per line, not 228 values.
  `Racer.frames(seeds=..., frames=...)` runs it and parses the output. It's
  a vendored-racer change like `signal_span`: Rust, a test, an entry in
  `vendor/racer/NOTICE.md`, and an upstream bead next to tbd-818.15.
- Python checks report failures in racer's shape (property name, frame,
  line, clock, observed, required). Tool feedback then reads the same for
  either kind of check.
- **The prompt still has to give the rule:** "...a solid background whose
  color changes every frame: each frame's COLUBK value is the previous
  frame's plus 2, wrapping at 256."
- Don't export all 101 runs: a run is 100 frames × 262 lines, and a few
  seeds are enough for this property. Timing and blanking still run over all
  101 in the check file.

This export is also what Tasks 7–8 will need, for behavior checked over
time. Done here, (d) is where we find out whether Python checks are cheap
enough to keep using.

The no-Rust fallback is a `check.py` that generates check files. Write 99
`constant_output` blocks, one per frame (`on_frames = [[n, n+1]]`), for each
phase the sequence could start in, and pass if either phase passes. It needs
no racer change. But it can only test sequences fixed in advance, and each
phase is another 7.5 s run.

### e. Stable red frame, PAL (`t1/pal`)

The simulator has no NTSC/PAL mode, and it doesn't need one: check files
compare color values, and `frame_length` takes any number of lines.
Everything is in the prompt and the check file.

- Prompt: canonical, with "Target: Atari 2600, PAL."
- Frame: 312 lines. VSYNC 3, vertical blank 45, picture 228 on lines
  [48, 276], overscan 36. Strict `frame_length = 312`, the same
  `vsync_span`, and `blanking` and `background` over [48, 276].
- Color: PAL red is hue 6. Stella shows $62 as #890821, $64 as #a73d4d and
  $66 as #c26472, so `check.py` has `RED = [0x62, 0x64, 0x66]`. NTSC's $42
  shows as brown-orange (#802700) on PAL, so a model that reuses the NTSC
  value fails. That's part of what this variant measures.
- Relaxed tier: `frame_length_range` 310–314 and `picture_block`
  `lines = 228`, `start_window = [46, 51]`.
- `broken/ntsc.asm`: the canonical reference, which must fail.

Decision: whether 3/45/228/36 is the only layout the strict check accepts.
PAL picture heights vary more in practice than NTSC's 192 (228 and 242 are
both common). If baseline runs show sound 242-line kernels failing, widen
the relaxed tier (`picture_block` with a range of line counts) rather than
the strict one.
