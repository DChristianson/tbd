# Report columns

A grid report summarises many attempts in a few numbers, and each number is
built from records at a lower level. This document lists every column the
report shows, says what it means, and names the record it rolls up from, so a
reader can check a surprising number against the saved files. The code is
`src/tbd/eval/report.py`; the saved files are described in
`docs/design/execution.md`, "Execution contract".

## Levels

The report works upward through four levels. Each level is computed only from
the level below it.

| Level | What it is | Where it comes from |
|---|---|---|
| Attempt | One request to the model and the grading of its reply. | `attempt.json` in `sample-NN/attempt-N/`, and the same record as one line of the job's `attempts.jsonl`. |
| Sample | One independent try at the task: the first attempt and, in tool-feedback mode, its retries. | `summarize_samples` groups a job's attempts by `sample`. |
| Job | One case0 run: one model, fact condition, task variant and mode, with N samples. | A job folder, `experiments/<exp>/jobs/<job>/`. |
| Cell | All jobs with the same model, fact condition, task, variant and mode, and the same settings. | `aggregate_cell` pools the samples of those jobs, across every experiment given to the report. |

The job's `metrics.json` is written by case0 while the job runs. The report
doesn't use its counts: it recomputes everything from `attempts.jsonl`. It reads
`metrics.json` for two things only. The job's status and heartbeat drive the
flags. `samples_planned` is used as the planned sample count when an older
`config.json` doesn't record `samples`.

## Attempt outcome

Every attempt is graded on its own, and its record says how it went:

- **`grade.ok`**: whether the attempt passed.
- **`grade.category`**: one of `pass`, `timeout`, `truncated`, `refusal`,
  `no_code`, `assemble_error`, `unsupported_opcode`, `crash`, `timing` or
  `output_mismatch` (`CATEGORIES` in `src/tbd/eval/grading.py`). `timing` means
  a failed property is about frame timing (`frame_length`, `vsync`,
  `vsync_span`); any other failed property makes it `output_mismatch`.
- **`grade.near_miss`**: the attempt failed the strict check but passed the
  relaxed one. Only `timing` and `output_mismatch` attempts can be near misses,
  because the relaxed check runs only after the program ran and failed on
  timing or output. A variant with no relaxed check (for example
  `t1/split-lr`) never has near misses.

## Sample outcome

`summarize_samples` reduces each sample's attempts to:

- **First category**: the category of attempt 1.
- **First passed**: whether attempt 1 passed.
- **Near miss**: whether attempt 1 was a near miss.
- **Passed**: whether any attempt passed.
- **Attempts to pass**: the attempt number of the first pass.
- **Attempts, output tokens, wall time, cost**: totals over all of the sample's attempts.

The first-attempt values come only from attempt 1. A sample that fails attempt
1 and passes attempt 3 has first category `output_mismatch` (say), first passed
false, and passed true.

## Grid tables

There is one table per task, variant and mode. Rows are models, and columns
are fact conditions (`none`, `contributed`, `discovered`, `both`, then any
others). A model that ran with two different settings gets two rows, each
labelled with the settings that tell them apart.

Each cell shows `pass@1 → pass@budget @N` as passing samples over samples,
where N is the attempt budget (1 plus retries). For a one-shot cell it shows
pass@1 alone. A dagger (†) marks a cell whose jobs are flagged (see Flags).

## Cell detail table

One row per cell.

| Column | Meaning | Rolls up from |
|---|---|---|
| model, facts, task, variant, mode | The cell's key. | config.json of its jobs. |
| samples | Samples recorded. Shown as "n of planned" when fewer than planned. | Count of samples; planned is the sum of each job's `samples` in config.json, or `samples_planned` in metrics.json when config.json lacks it. |
| pass@1 | Samples whose first attempt passed, with the percentage. | Sample "first passed". |
| pass@budget | Samples that passed within the attempt budget, with the budget(s) after `@`. Equal to pass@1 for one-shot. | Sample "passed"; the budget is 1 + `retries` from config.json. |
| first-attempt categories | Share of samples by the category of their first attempt. | Sample "first category". |
| near miss | Samples whose first attempt was a near miss. | Sample "near miss". |
| attempts/pass | Mean attempt number of the first pass, over passing samples only. | Sample "attempts to pass". |
| output tokens | Output tokens, thinking included, over all attempts. | `tokens.output` per attempt. |
| wall | Sum of attempt wall times. This is not elapsed time: jobs that ran in parallel each add their own time, and job setup isn't included. | `wall_ms` per attempt. |
| cost | API cost in dollars, for providers that report it (Anthropic). "-" otherwise. | `cost_usd` per attempt. |
| settings columns | Only settings whose values differ between cells are shown as columns. The rest are listed once under the table as "Same for every cell". | config.json; see below. |
| flags | Problems with the cell's jobs. | See Flags. |

### Settings

Jobs with different settings are never pooled into one cell. The settings
compared are:

| Setting | From config.json |
|---|---|
| digest | `model_digest`, first 12 characters (Ollama only; a re-pulled model gets a new digest). |
| fact_snapshot | `fact_snapshot`, first 12 characters: a hash of the facts that were given. |
| num_ctx, max_tokens, temperature, retries, effort | The fields of the same name. |
| think | `thinking` (Anthropic) or `think` (Ollama). |
| dasm | `toolchain.dasm`, the DASM version. |
| racer_check | `toolchain.racer_check.sha256`, first 12 characters. |
| image | `toolchain.image_digest`, the container image, first 12 characters. |

Cells are also split on `model_host` (where the model was served), but it
isn't shown as a column.

## Flags

A cell's flags list each of its jobs that:

- has a `metrics.json` status other than `completed`. A `running` job is
  called stale when nothing will update it again: its heartbeat (`updated_at`)
  is older than the request timeout plus a margin, its process is gone on this
  host, or it predates heartbeats.
- has no attempts.
- has unreadable lines in `attempts.jsonl`.

## metrics.json

case0 writes `metrics.json` for each job, separately from the report. Its
counts summarise the same attempts, so they should agree with the report. The
report doesn't read the counts; it uses only the status, the heartbeat and
`samples_planned`, as described under Levels.

| Field | Meaning |
|---|---|
| status | `running`, `completed`, `interrupted` or `failed`. |
| samples_planned, samples_done | Samples asked for and finished. |
| passed, pass_rate | Samples that passed within the budget. |
| pass_at_1_first_attempt | Share of samples whose first attempt passed. |
| first_attempt_categories | Samples by first-attempt category. Until tbd-175 is fixed, a sample that passed on a later attempt is counted as `pass` here, not under its first attempt's category. |
| near_misses | Samples whose first attempt was a near miss. Same caveat as above. |
| attempts_total, wall_s | Attempts made, and the job's elapsed time. |
| pid, host, updated_at | The heartbeat, used to spot a job that stopped without saying so. |

## Planned changes

Open beads that change these columns:

- **tbd-175**: makes `metrics.json`'s first-attempt fields come from attempt 1
  only, as the report already does.
- **tbd-9q8**: adds tok/s, RunPod queue time and time outside generation per
  cell, and a table per RunPod worker.
- **tbd-ljd**: adds "assembled as written" and "assembled after fix" when the
  syntax fixer is on.
- **tbd-z38**: adds a drill-down from each cell to its attempts in the HTML report.

Update this document when one of them merges.
