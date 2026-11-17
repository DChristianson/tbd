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
runner, below, will run many). Runs are never deleted or overwritten: if the
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

A "model" in the grid is weights + quantization + runtime. The same model name
on Ollama (GGUF q4) and on vLLM (fp16 or AWQ) is two different rows, and the
results can differ. Each row records runtime, quantization and a weights
hash. Using llama.cpp/GGUF on both Mac and cloud keeps rows comparable across
machines. Larger cloud-only models (e.g. a ~27B) are simply new rows.

Temperature 0 isn't bit-reproducible across GPUs either. Results are
compared as pass@k over samples, never as single outputs.

## Experiment definition

*Planned, tracked by tbd-f78.19; not built yet.*

YAML, expanded by a small script into cells. The axes are the grid's:

```yaml
experiment: { name: baseline-t1, version: 1 }
models:
  - { name: qwen3.5, runtime: ollama, quant: q4_K_M, endpoint: local }
facts: [none]
tasks: [{ id: t1, variants: [canonical, red-band] }]
modes: [one-shot, tool-feedback]
samples: 10
temperatures: [0.2]
seeds: [42]
execution: { provider: local, max_concurrent_jobs: 2, timeout_minutes: 30 }
```

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
