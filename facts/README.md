# Fact store (v0)

Facts are short pieces of general Atari 2600 / 6502 / DASM knowledge that can
be put in a model's context. The grid's **facts** axis
(`docs/design/experiment-grid.md`) measures whether they help. The schema is
the Layer 1 provenance schema from `docs/design/atari-2600-synthesis-agent.md`.

## Format

Every `facts/*.toml` file holds `[[fact]]` tables, one per fact, and nothing
else. All six fields are required, and no others are allowed:

```toml
[[fact]]
fact_id = "F007"            # F + digits, unique across all files, never reused
text = "One or two sentences of platform knowledge."
source_type = "official_doc"  # official_doc | human_post | self_discovered
source_ref = "where it can be checked (a file path, a named document)"
license = "the source's license"
confidence = 0.9            # 0..1
```

`src/tbd/eval/facts.py` loads and validates the store. To see what a condition
selects, its snapshot hash and the block a run sends, run
`python -m tbd.eval.facts contributed`.

## Conditions

| `--facts` | source types |
|---|---|
| `none` (default) | no facts; the prompt is sent unchanged |
| `contributed` | `official_doc` + `human_post` |
| `discovered` | `self_discovered` |
| `both` | all |

v0 delivery: every fact in the condition goes in the first user message, as a
`Reference notes:` block of `[F001] ...` lines before the task prompt. Tool
feedback on retries carries no facts. A run records the condition
(`config.json` `facts`), the ids (`fact_ids`, and `fact_ids_in_context` on every
attempt) and `fact_snapshot`: a sha256 over the selected facts' full records.
Editing any field of a selected fact changes the snapshot, so the grid report
never pools runs made with different facts.

## Rules

- **Provenance is the point.** A contributed fact (`official_doc`,
  `human_post`) needs a `source_ref` and `license` someone can check: a file
  in this repo (`vendor/atari2600/vcs.h`, the vendored Stella source under
  `stella-core/vendor/stella/`) or a public document named precisely. Never
  invent URLs, page numbers or quotes.
- **Unverified facts** may be added with `source_ref = "unverified: <what
  would back it>"`, `license = "unverified"` and a confidence below 0.8 (a
  test enforces the confidence). Replace them with a real source when one is
  found.
- **No solution code.** Facts are general platform knowledge. Nothing may be
  copied from `tasks/*/*/reference.asm` or its comments; those are solutions
  and must never reach a prompt. A test checks that no reference line appears
  in a fact.
- **Short.** One or two sentences, one line.
- **Edits change results.** Changing a fact changes the snapshot of every
  condition that includes it. Don't renumber; add a new id for a new fact.
- `self_discovered` facts are written by the discovery loop (tbd-dug.3), with
  the probe that confirmed them as `source_ref`.
