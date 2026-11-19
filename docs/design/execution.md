# Execution: containerized, local first, cloud when needed

Reconciles the proposal "GPU-backed Agent Evaluation Infrastructure (GitHub
Actions, RunPod, Google Cloud)" (2026-11-11) with the
[experiment grid](experiment-grid.md). The proposal's core principle is kept:
**experiment definitions say what to run, never where**. The order changes:
start on the Mac, and add cloud pieces only when a grid cell needs them.

Scope: cloud is for **evaluation and training**. The finished agent still has
to run on a laptop (design doc goal).

## Two kinds of container, not one

The proposal has one "GPU worker". In this project, the work splits cleanly:

| | Worker | Inference / training |
|---|---|---|
| Runs | harness, DASM, `stella-core`, graders, discovery probes | the model server (or a fine-tuning job) |
| Needs | CPU only | GPU (or Apple Metal) |
| On the Mac | Docker container | **on the host**: Ollama or llama.cpp with Metal. Docker on macOS has no GPU access |
| In the cloud | anywhere cheap (even a CI runner) | RunPod pod, GCP VM |

The worker talks to the model over an OpenAI-compatible HTTP endpoint (Ollama,
llama.cpp server and vLLM all expose one). Moving to the cloud then means
changing the endpoint and where the worker runs, not the harness.

The worker image and the dev container share a Dockerfile base stage, so the
toolchain (DASM version, `stella-core` build) is identical in both.

## Execution contract

A **job is one grid cell**: (model endpoint + model identity, facts condition +
fact-store snapshot, task, variant, mode, k, temperature, seed, retry budget).
It's independent and repeatable, so it can run anywhere.

`python -m tbd.eval.case0` runs one job and writes this layout (the YAML
runner, below, runs many). Runs are never deleted or overwritten: if the
job folder already exists the run stops with an error; pick a new
`--experiment`.

```
experiments/{experiment_id}/          # --experiment, default case0-{UTC stamp}
    manifest.json        # written by the first job: experiment_id, created,
                         #   git_sha, toolchain {image_digest, dasm,
                         #   racer_check {path, sha256}}
    jobs/{job_id}/       # {model}-{task}-{variant}-{facts}-{mode}, ':' and '/' -> '_'
        config.json      # the expanded cell (fields below)
        model_template.txt  # Ollama's chat template (not for anthropic)
        attempts.jsonl   # one attempt record per line (experiment-grid.md)
        metrics.json     # status, pass rate, categories, wall time (below)
        console.log      # everything the job printed, stdout and stderr together
        sample-NN/attempt-N/
            request.json, response.json   # exact request body; raw reply
            response.md, thinking.md      # reply text; thinking, if any
            stream.jsonl                  # streamed chunks as received
            prog.asm, vcs.h, macro.h      # extracted program, headers beside it
            assemble.json, build/prog.bin # DASM result; the ROM if it assembled
            {check}.toml, {check}.result.json  # each racer-check run, incl.
                                          #   accepted-color re-runs (check-0x44...)
            frames.jsonl.gz               # racer-check --frames, if check.py asks
            attempt.json                  # this attempt's record
            error.txt                     # if the request failed (timeout)
            regrade-{stamp}/              # added later by tbd.eval.regrade
```

Files after `stream.jsonl` exist only as far as the attempt got: no code
means no `prog.asm`; an assemble error means no ROM or check results.

**config.json**: `provider`, `model`, `model_digest` (Ollama; null for the
API), `runtime`, `facts`, `fact_snapshot`, `task`, `variant`, `mode`,
`samples`, `temperature` and `seed` (null where the provider takes none),
`effort`, `thinking`, `retries` (0 for one-shot), `max_tokens`, `num_ctx`,
`request_timeout_s`, `prompt`, `grader`, `task_dir`, `check_files`,
`check_py`, `toolchain` (the job's own: `dasm`, `racer_check` {`path`,
`sha256`}, `image_digest`; see Toolchain below).

**metrics.json**, rewritten after every sample: `status` (`running`,
`completed`, `interrupted` on Ctrl-C, `failed` with `error` on a crash),
`samples_planned`, `samples_done`, `passed`, `pass_rate`,
`pass_at_1_first_attempt`, `first_attempt_categories`, `near_misses`,
`attempts_total`, `wall_s`.

**Toolchain**: `dasm` is the version DASM reports; `racer_check.sha256`
hashes the binary that graded (the vendored source is pinned by `git_sha`);
`image_digest` is read from `TBD_IMAGE_DIGEST`, which whoever starts the
container sets, since an image can't know its own digest at build time. The
dev container doesn't set it, so it's null there. Anything that can't be
determined is null, never a guess. The manifest records the toolchain of the
experiment's first job; each job's config.json records its own, so a later job
graded by a different build is visible. The report treats the toolchain as a
setting: jobs differing in DASM version, racer-check sha256 (first 12 hex; the
path is ignored, since Mac and container differ) or image digest are separate
cells. Configs from before this read as "-".

Discovery runs are **not** independent jobs: each probe changes the fact
store. They run as one sequential job that outputs a new fact-store snapshot.
Grid cells then take that snapshot as an input.

## Model identity across runtimes

A "model" in the grid is weights + quantization + runtime (decided in
tbd-f78.2). The same weights served by Ollama and by vLLM are two different
rows, because the runtime and the quantization can change the results. Each
row records its runtime, its quantization and a hash of its weights. Larger
cloud-only models (e.g. a ~27B) are simply new rows.

Within that, runs aim to be reproducible. Every request sends a seed (the
job's seed plus the sample number), and each job records the seed and
temperature it used. Where the same model runs on the Mac and in the cloud,
both sides should use GGUF at the same quantization, so the two rows can be
compared.

A fixed seed still doesn't give identical output across GPUs or runtimes.
Results are therefore compared as pass@k over samples, never as single
outputs.

There are two clients, one per kind of API:

- **Ollama (on the Mac):** `models.ollama_chat` uses Ollama's own API. That
  API is what exposes the thinking switch, the context window size, the
  weights digest and the chat template, so the Mac client stays on it.
- **OpenAI-compatible (in the cloud):** llama.cpp server and vLLM expose only
  `/v1/chat/completions`. That API has no standard way to report the weights
  hash or the chat template, so the pod records them itself when it starts
  (for example, by hashing the GGUF file).

## Experiment definition

`python -m tbd.eval.experiment DEFINITION.yaml [--dry-run] [--only PATTERN]`
(tbd-f78.19) expands a YAML definition into case0 jobs and runs them. Definitions
are tracked in `experiments.d/` (`experiments/` is output); the T1 baseline is
`experiments.d/t1-baseline.yaml`.

```yaml
experiment: t1-baseline            # the folder: experiments/t1-baseline/
models:                            # provider (ollama | anthropic, default ollama),
  - { provider: ollama, model: llama3.1 }    # model, and any setting below
  - { model: "qwen2.5-coder:14b", num_ctx: 16384 }   # as a per-model override
facts: [none]                      # fact conditions
tasks: [{ id: t1, variants: all }] # all = every tasks/t1/<variant>/ with a prompt.txt
modes: [one-shot, tool-feedback]
samples: 3                         # settings: samples, seed, temperature, retries,
seed: 42                           #   max_tokens, num_ctx, timeout, think, effort;
temperature: 0.7                   #   unset ones take case0's defaults
```

- **Expansion**: models x facts x tasks/variants x modes, models outermost (a
  local model stays loaded across its jobs), one case0 job each. Temperature
  and seed are single values, not axes: case0's job name doesn't include them,
  so two values would collide in one experiment folder (use two experiments).
  Unknown keys, tasks, variants, modes, fact conditions, and values case0
  would reject are errors before anything runs.
- **Running**: one job at a time (local Ollama), each `python -m
  tbd.eval.case0 ... --experiment NAME` in a subprocess, so every job has the
  layout above. Before each Ollama job the runner checks Ollama is reachable
  and the model is pulled, and stops if not, so a job never dies in setup
  and leaves a folder behind. `--only` picks jobs by id (a glob, or a
  substring).
- **Records**: `experiment.yaml` (the definition, byte for byte) and
  `jobs.json` (the expanded jobs with their case0 arguments) beside
  case0's `manifest.json`. Running a changed definition into the same folder
  (e.g. adding a fact condition) adds `experiment.{stamp}.yaml` and
  `jobs.{stamp}.json`; nothing is overwritten.
- **Resume**: running the definition again skips jobs whose metrics.json is
  `completed` and whose config.json matches the definition's settings. A job
  folder that exists but didn't complete (interrupted, failed, a stale
  `running`, or no metrics.json) is reported and skipped, never deleted or
  reused: case0 refuses an existing folder, and continuing its samples in
  place would mix two runs in one record. A completed job with other settings
  is reported as a mismatch. To redo such jobs, run them under a new
  experiment name. `--dry-run` shows each job's state without running any.
- **Parallel**: `parallel: N` (or `--parallel N`) runs up to N jobs at once.
  It suits a RunPod endpoint with N workers. It doesn't suit a single local
  Ollama, which would only queue them. Each job's output is then printed with
  its job id in front.
- **Gate**: `gate: [t1/canonical, t1/blue]` runs the listed task/variants
  first for each model, fact condition and mode. The configuration's other
  jobs run only if an attempt in some gate job assembled; otherwise they are
  reported as `gated`. A model that can't produce a stable frame gains
  nothing from the harder variants, so running them would only spend time.
- **Random seeds**: `random_seeds: N` makes every check run N random power-on
  states instead of the count in its check file, and config.json records it.
  Grading time grows with the count: 100 seeds take about 8 s per check file.
  On RunPod, a grade longer than the endpoint's idle window lets the worker
  scale down, so the next attempt waits for a cold start.
- **Stopping**: Ctrl-C or SIGTERM is passed once to each running job, which
  marks itself `interrupted`; no further job starts. A failed job is reported
  and the next one runs. Exit status: 0 all done, 1 some job not done, 130
  stopped.
- At the end it prints the run summary and the grid report for the
  experiment.

Not built: the proposal's `execution.provider`. Jobs always run from this
machine; only where the model is served changes.

The proposal's `agent.configurations` axis (single / multi / planner-worker)
isn't in the grid yet. Adding it multiplies every other axis, so it waits until
the single-agent baseline exists.

## Providers, in the order they get built

1. **Local**: the worker container on the Mac, calling host Ollama. Results
   go to `experiments/` on disk.
2. **RunPod**: a pod running llama.cpp server or vLLM for models over ~10 GB,
   with the worker running beside it or locally. Needs the worker image
   published to a registry.
3. **Cloud training**: fine-tuning for the discovered-model row. Unsloth
   targets CUDA, so this likely means a cloud GPU (the Mac alternative is
   MLX-LM).
4. **GCP**: when managed security or quota matter. Workload Identity
   Federation, no long-lived keys.

The `ComputeProvider` interface (`submit` / `status` / `collect_results` /
`cancel`) is extracted when the second provider arrives, from two working
implementations, not designed up front.

## GitHub Actions

- **Build and publish the worker image**: tests, build, tag with git SHA,
  record the digest. Actions pinned to commit SHAs.
- **Evaluation dispatch**: manual `workflow_dispatch` only at first. Not on
  pull requests from forks; no secrets reach untrusted PRs, and nothing
  spends GPU money without a person starting it.
- Long jobs own their own lifecycle; Actions dispatches and reports.

## Deferred until needed

The proposal's controller with a PostgreSQL queue, S3 artifact store and
dashboard. Until running jobs or comparing results is actually painful,
`experiments/` on disk (synced to object storage when cloud runs start) plus
the grid report is enough.

## Cost

Record per job: wall time, tokens, tokens/s, GPU type and $/hour where
applicable. Report **cost per passing attempt**, as the proposal says: a
cheaper model that needs more attempts can cost more.
