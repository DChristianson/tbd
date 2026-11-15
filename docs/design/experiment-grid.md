# Experiment Grid

Every claim this project wants to make ("the agent can do X", "discovery
helps", "the corpus is what's doing the work") is a comparison between two
cells of one grid. All cells are filled by the same harness, so cells can be
compared directly.

This also answers a worry the original design glossed over. Any open-weight
code model has almost certainly seen AtariAge posts and 2600 tutorials in
pretraining. So "it worked" doesn't show the attributed corpus did anything.
The grid makes that prior knowledge something we measure instead of a hidden
confound.

## Three groups of ingredients

| Group | What | Examples |
|---|---|---|
| **1. Infrastructure** | Compile, run, grade, log. Shared by every cell. | DASM, `stella-core`, graders, harness, attempt log |
| **2. Contributed** | Things we supply | **models** (open-weight, frozen), **facts** (clearly licensed seed), **tasks** (the 8 demo tasks + variants, with graders) |
| **3. Discovered** | Things the loop produces | **facts** (`self_discovered`, from probes), **models** (fine-tuned on the harness's own verified passing attempts) |

## Axes

| Axis | Values |
|---|---|
| **Model** | each contributed model; later, each discovered model (named by its parent + training run) |
| **Facts** | `none` · `contributed` · `discovered` · `both` |
| **Task** | T1–T8, each as `canonical` (the wording in the design doc) and one or more `variant`s |
| **Mode** | `one-shot` (one generation, graded) · `tool-feedback` (sees assembler errors and grader output, retries within a budget) · `agentic` (an agent product such as Claude Code or Copilot CLI, sandboxed: only the prompt, an empty work dir, `dasm` + headers, and a `check` command whose grader runs outside the sandbox) |

Held fixed per run and recorded: samples per cell (*k*), temperature, seed,
retry budget, model digest, fact-store snapshot.

## The grid

Rows are models, columns are fact conditions. Each cell is reported per task ×
{canonical, variant} × mode.

| | facts: none | contributed | discovered | both |
|---|---|---|---|---|
| **contributed model** | **baseline**: what the model already knows | value of the seed corpus | clean-room claim | — |
| **discovered model** | what training on its own successes bought | | | full system |

## What each comparison answers

| Compare | Question |
|---|---|
| contributed model × `none`, **canonical vs variant** | Recall or understanding? A model that passes canonical but fails variants is reciting. This is the contamination measure. |
| `none` → `contributed` | Does the clearly licensed seed corpus add anything over pretraining? |
| `contributed` → `discovered` | Can facts the agent found itself replace supplied ones? (The clean-room claim.) |
| `discovered` → `both` | Do discovered facts add to supplied ones, or just duplicate them? |
| contributed model → discovered model | Did fine-tuning on verified attempts help, at what cost to attribution? (Knowledge in weights can't be cited.) |
| `one-shot` → `tool-feedback` | How much of the success comes from the Layer 0 tools in the loop rather than from the model? |

The baseline is the reference point for all of these, so it gets filled first.
Tasks the baseline already passes (including variants) aren't where the corpus
or discovery layer can show anything. Tasks it fails are where the discovery
loop gets pointed.

## Metrics per cell

- **pass@k**: the main number.
- **Failure categories**: share of attempts by first failure. Initial set:
  `no_code` (no assemblable program extracted) · `assemble_error` ·
  `timing` (wrong frame length or unstable framerate) · `output_mismatch`
  (runs at the right rate, wrong picture) · `crash_or_timeout`. These are
  the raw material for the failure-attribution open question.
- **Cost to pass**: attempts, tokens, wall time.
- **Fact use**: which fact ids were in context and which the program
  plausibly depended on. This is what the attribution claim rests on.

## Attempt record

Every attempt appends one JSON line. The grid report is computed from these.
They're also the training data for discovered models and the input to the
discovery layer.

```
{ run_id, cell: { model, model_digest, facts, fact_snapshot, task, variant, mode },
  sample, seed, temperature, attempt_no,
  prompt, fact_ids_in_context, response, code,
  assemble: { ok, errors }, grade: { ok, category, detail },
  tokens, wall_ms }
```

## Task specs

A task is: prompt text, one or more variants, a grader (a pure function over
the emulator's `RunResult`), and a hand-written reference solution that
proves the grader can be passed. The Task 1 tests already contain the grader
logic; they get pulled out into reusable graders so pytest and the harness
share them.

What each task needs from Layer 0 grows:

- T1–T2: per-scanline `COLUBK` reads (built)
- T3: a frame/raster compare (first real use of the raster diff tool)
- T4–T6: object positions / playfield readout
- T7–T8: **joystick input injection** in the emulator protocol, and graders
  that check behavior over time. Final fidelity ("does it play right") is a
  human judgment outside the grid.

## Models

Local models run in Ollama on the host Mac (the dev container calls it at
`OLLAMA_HOST`). On the current 16 GB M2 Pro, models over ~10 GB swap badly, so
the starting rows are `qwen3.5` (7.6 GB) and `llama3.1` (4.9 GB), plus smaller
quantized coder models if added. `qwen3-coder` (18 GB) and `GLM-4.7-flash`
(19 GB) are installed but don't fit well. A hosted frontier model can be added
as a ceiling row. The laptop-only constraint applies to the finished agent,
not to evaluation.

Where cells run (the Mac first, cloud GPUs for larger models and training)
is in [execution.md](execution.md).

## Order of filling

1. Infrastructure: graders out of pytest, task spec format, model client,
   harness, attempt log, report.
2. Baseline: T1 canonical + variant × local models × `none` × both modes.
3. Specs for T2–T4, widen the baseline.
4. `contributed` column (minimal seed: `vcs.h` registers, frame timing).
5. Discovery loop on baseline failures → `discovered` and `both` columns.
6. Discovered model row.
7. T5–T8 once the emulator takes input.

Work items for all of this are tracked in beads (`bd ready`, `bd list`).
