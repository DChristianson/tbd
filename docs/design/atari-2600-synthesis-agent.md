# Atari 2600 Game Synthesis Agent — High-Level Design

## Goal
Synthesize playable Atari 2600 games from natural-language gameplay prompts, using only
public-domain/clearly-licensed sources, running entirely on a laptop (no hosted APIs at
inference time), with per-fact source attribution and a self-improving discovery loop.

## Demo tasks

Create a kernel with stable framerate that sets the screen background to red.
Create a kernel that changes the screen background every scan line.
Create a kernel that changes the screen color every scanline, producing a grid.
Create a kernel to position players, missiles and the ball on the playfield.
Create a kernel for a 2 player game scoreboard.
Create a kernel for 6 character scoreboard.
Create a pong clone.
Create a game where both players move around the screen and shoot at each other.

## Architecture

### Layer 0 — Compute Tools (deterministic, no attribution needed)
Ground-truth engines the agent calls rather than a model it trains to imitate.
- **Assembler**: DASM or ca65, compiles 6502/TIA source to a ROM.
- **Cycle-accurate emulator**: Stella (with debugger/trace hooks), returns cycle counts,
  register state, and rendered frames per instruction.
- **Frame/raster diff tool**: compares emulator video output against an expected raster
  pattern for canonical test scenes (color/position of playfield, sprites, timing).

These are called as tools, not modeled in weights — the same principle as pushing
computation down to a query engine rather than reimplementing it in application logic.

### Layer 1 — Retrieval Tool: Attributed Corpus Index
A frozen small local LLM (e.g., Qwen2.5-Coder / Qwen3 / Gemma 3, 4-bit quantized,
1B–14B params, run via Ollama/llama.cpp) plus a searchable index of facts. Every entry
carries provenance:

```
{ fact_id, text, source_type: "official_doc" | "human_post" | "self_discovered",
  source_ref, license, confidence }
```

- `official_doc` — public-domain Atari/6502 reference docs, Stella source/docs.
- `human_post` — public forum posts with clear reuse license; retained with
  per-post/per-author attribution (fractional credit).
- `self_discovered` — facts produced by the clean-room discovery layer (below).

Retrieval calls are restrictable by `source_type` / author / license, enabling controlled
ablations (e.g., synthesize using only `self_discovered` facts, or excluding all
`human_post` facts, and compare gameplay fidelity).

### Loop: Game Synthesis Engine ↔ Clean-Room Discovery Layer
1. **Synthesis engine** attempts a spec, using Layer 0 tools + Layer 1 retrieval.
2. Result is checked against ground truth (cycle trace, frame diff) and, for final
   fidelity, human judgment (including correct "racing the beam" timing behavior).
3. On failure, the engine emits a structured failure claim (expected vs. observed,
   and — only when the cause is a genuine knowledge gap rather than a code or spec
   bug — a hypothesis about the missing fact).
4. **Discovery layer** turns that hypothesis into an isolated probe program, runs it
   against Layer 0, confirms reproducibility, and writes a new `self_discovered` fact
   back into the Layer 1 corpus.
5. Synthesis retries with the new fact available.

This closes the loop: failures directly seed new corpus knowledge rather than being
discarded after a retry.

## Required Components
- [ ] Local model runtime (Ollama / llama.cpp) + a small open-weight code model
- [ ] DASM or ca65 toolchain, scriptable from the agent
- [ ] Stella emulator with debugger/trace access, scriptable
- [ ] Frame/raster comparison utility
- [ ] Corpus store + retrieval index with provenance schema and source-type filtering
- [ ] Curated set of officially public-domain / clearly licensed docs (initial seed)
- [ ] Failure-attribution logic (knowledge gap vs. code bug vs. spec misunderstanding)
- [ ] Probe-generation / bisection procedure to turn a vague failure into an isolatable experiment
- [ ] Dedup/plateau tracking so probes don't re-derive existing facts
- [ ] Human-fidelity evaluation harness for final judging

## Open Questions
- Failure attribution: how does the synthesis engine reliably tell "missing fact" apart
  from "bad code" or "wrong spec understanding"?
- Probe design: what turns a vague symptom ("colors wrong on odd frames") into a
  concrete, bisectable experiment?
- Licensing verification workflow for `human_post` sources (many forums default to
  all-rights-reserved absent an explicit license).
- Reactive-only discovery vs. adding speculative/adjacent probing — worth an ablation
  once the reactive loop is stable.

## Suggested Reading / Resources
- **Stella** emulator (source + debugger docs) — ground truth for Layer 0/1.
- **DASM** and **ca65 (cc65 suite)** — assemblers.
- *Racing the Beam* — Montfort & Bogost — the canonical explanation of Atari 2600
  "beam racing" timing constraints.
- **AtariAge** development forums and wiki — human-sourced tricks (verify license per post).
- **Voyager** (Wang et al., 2023) — open-ended agent that accumulates a growing library
  of self-discovered, reusable skills; closely analogous architecture.
- **Reflexion** (Shinn et al., 2023) — turning failed attempts into structured
  self-critique that conditions the next attempt.
- Robot-scientist literature (King et al., "Adam"/"Eve") — automated hypothesis
  generation + experimentation as a general pattern for the discovery layer.
- Local model tooling: **Ollama**, **llama.cpp**, **Unsloth** (for optional later
  fine-tuning of tool-calling behavior, not fact storage).
