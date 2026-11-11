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

Output layout, taken from the proposal and used from the first harness run:

```
experiments/{experiment_id}/
    manifest.json            # experiment definition, git SHA, image digest
    jobs/{job_id}/
        config.json          # the fully expanded cell
        attempts.jsonl       # attempt records (schema in experiment-grid.md)
        metrics.json         # pass@k, categories, tokens, wall time, cost
        stdout.log, stderr.log
        artifacts/           # ROMs, emulator outputs
```

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
