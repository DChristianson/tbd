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

The worker image and the dev container have separate Dockerfiles, but they
pin the same DASM and Rust versions, and a test checks that the pins agree.
See "Worker image" below.

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
            prog.orig.asm, assemble.orig.json  # fix_syntax on, when the fixer
                                          #   changed the program: the model's own
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
`request_timeout_s`, `prompt`, `grader`, `task_dir`, `random_seeds`,
`fix_syntax`, `check_files`, `check_py`, `toolchain` (the job's own: `dasm`, `racer_check` {`path`,
`sha256`}, `image_digest`; see Toolchain below), `model_host` (the Ollama URL
or RunPod endpoint), `compute` {`gpu`, `host_id`}. `compute` describes the
machine that served the model. case0 can't see that machine, so it copies
`TBD_GPU` and `TBD_HOST_ID` from its environment; the pod runner sets both,
and elsewhere they are null.

**metrics.json**, rewritten after every sample: `status` (`running`,
`completed`, `interrupted` on Ctrl-C, `failed` with `error` on a crash),
`samples_planned`, `samples_done`, `passed`, `pass_rate`,
`pass_at_1_first_attempt`, `first_attempt_categories`, `near_misses`,
`attempts_total`, `wall_s`. The three first-attempt fields describe each
sample's attempt 1 only. A sample that fails attempt 1 and passes on a retry
is counted under attempt 1's category in `first_attempt_categories`, does not
count toward `pass_at_1_first_attempt`, and counts in `near_misses` if attempt
1 was a near miss. Runs saved before this was fixed counted such a
sample as `pass` and dropped its near miss; their attempts.jsonl has the right
per-attempt categories, and the report recomputes from it.

**Toolchain**: `dasm` is the version DASM reports; `racer_check.sha256`
hashes the binary that graded (the vendored source is pinned by `git_sha`);
`image_digest` is read from `TBD_IMAGE_DIGEST`, which whoever starts the
container sets, since an image can't know its own digest at build time. The
dev container doesn't set it, so it's null there. An image has no `.git`,
so there `git_sha` is the `GIT_SHA` it was built with (`TBD_GIT_SHA`). Anything that can't be
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
- **Syntax fixer**: `fix_syntax: true` (case0 `--fix-syntax on`) rewrites a
  program DASM rejects toward DASM syntax and grades the result
  (tbd.eval.fixer has the rules). Without it, most attempts by small local
  models fail in DASM on slips such as code in column 0, so the checks never
  run. It is off by default and recorded in config.json, so runs with and
  without it are never mixed. Each attempt's `fix` record says whether DASM
  took the program as written and after the fix, and which rules fired; the
  model's own program stays in `prog.orig.asm`. The grid report shows both
  counts, so a comparison such as facts against none can use either.
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

## Worker image

The worker image (tbd-f78.3) is the harness without a model: Python with tbd
installed, DASM and racer-check. It needs no GPU. It is the `worker` stage of
`docker/worker.Dockerfile`, which is separate from the dev container's
`.devcontainer/Dockerfile`, so changes to one image can't break the other.
A `build` stage compiles DASM and racer-check, and the worker, on
`debian:bookworm-slim`, copies in only the two binaries. Neither Rust nor a
compiler ships in it. Both Dockerfiles pin the same DASM and Rust versions,
so a job graded in either uses the same tools. Because the pins are written
twice, `tests/test_podrunner.py` checks that they agree.

```bash
docker build -f docker/worker.Dockerfile --target worker \
    --build-arg GIT_SHA=$(git rev-parse HEAD) -t tbd-worker .
docker run --rm -e OLLAMA_HOST=http://host.docker.internal:11434 \
    -v "$PWD/experiments:/opt/tbd/experiments" \
    tbd-worker python -m tbd.eval.experiment experiments.d/t1-baseline.yaml
```

The checkout is copied to `/opt/tbd` and installed editable, because tbd finds
`tasks/`, `facts/` and `vendor/` beside its source.
`docker/worker.Dockerfile.dockerignore` keeps `experiments/`, build output,
`stella-core/` and `.env*` files out of the image. BuildKit reads it only
for this Dockerfile. The optional `stella-core` emulator isn't built, since the eval
doesn't use it. Build on the Mac with `--platform linux/amd64` for an image
that will run on a cloud GPU.

## Pod runner

Experiments run from the laptop against a RunPod serverless endpoint stop
when the laptop does. Serverless also spends much of its time outside
generation (cold starts, queueing, slow or idle workers; see tbd-f78.24). The
pod runner instead puts the model and the harness in one container on one
GPU pod. It runs a queue of experiment definitions there and copies the
results out as each job finishes, so no laptop is involved.

It is the `pod-runner` stage of `docker/worker.Dockerfile`: the worker plus Ollama,
pinned by version and hash. Its entrypoint, `python -m tbd.eval.podrunner
--serve-ollama`, does the following (the module docstring has the details):

1. It loads and checks every definition before anything starts. Each model
   must use the `ollama` provider, because the pod serves its models itself.
2. It starts `ollama serve` on 127.0.0.1 and pulls each model the
   definitions name. Models aren't baked into the image, so one image serves
   any definition.
3. It sets `TBD_GPU` (from `nvidia-smi`, or unset when there is no GPU) and
   `TBD_HOST_ID` (`RUNPOD_POD_ID`, else the hostname). case0 records both as
   `compute` in each job's config.json.
4. It runs the definitions in the order given with `tbd.eval.experiment`, so
   parallel, gate and resume behave as they do on the laptop.
5. After each job, and after each definition, it copies
   `experiments/<name>/` to the sync target. It never deletes anything at the
   target.
6. It stops when the queue is empty or the maximum lifetime runs out. At the
   lifetime limit the running jobs get SIGTERM and mark themselves
   `interrupted`. If they are still running after a grace period, the runner
   syncs and exits anyway.
7. Whatever the reason for stopping, it syncs once more and then runs the
   `--on-exit` command.

An example definition for the pod is `experiments.d/t1-facts-qwen3.6-pod.yaml`.
A queue can be checked locally without starting anything:

```bash
python -m tbd.eval.podrunner experiments.d/t1-facts-qwen3.6-pod.yaml --no-sync --dry-run
```

### Launching it

These steps haven't been run yet. The image has never been built or started
on a pod.

1. Build for amd64 and push the image to a registry the pod can pull from
   (GitHub Actions publishing is tbd-f78.7):
   `docker build -f docker/worker.Dockerfile --platform linux/amd64 --target pod-runner --build-arg GIT_SHA=$(git rev-parse HEAD) -t ghcr.io/OWNER/tbd-pod-runner:SHA .`
2. Create a RunPod pod (or a pod template) with that image. Its container
   start command holds the runner's arguments, e.g.
   `experiments.d/t1-facts-qwen3.6-pod.yaml --sync /workspace/results --max-lifetime 6h`.
   The same settings can instead come from environment variables:
   `TBD_DEFINITIONS`, `TBD_SYNC` or `TBD_SYNC_COMMAND`, `TBD_MAX_LIFETIME`
   and `TBD_ON_EXIT`.
3. Set `OLLAMA_NUM_PARALLEL` to match the definition's `parallel` (see "GPU
   size" below). Optionally set `OLLAMA_MODELS=/workspace/ollama`, so that a
   network volume keeps the pulled weights for the next pod.
4. Don't expose port 11434. Ollama has no authentication.
5. Read progress in the pod's logs. Read results at the sync target.

Definitions in the image are the ones in `experiments.d/` when it was built.
A new definition means a new image, or a definition file put on a mounted
volume and named by its path.

### Stopping the pod itself

The runner exits when it is done. Exiting alone probably doesn't stop the
bill, though. A RunPod pod's container is restarted when its process exits
(to verify). The restarted runner would then pull the models again and find
nothing left to run, so the pod would keep billing while doing nothing
useful.

The design is that `--on-exit` stops the pod through RunPod's API, using the
pod's own id:

```bash
--on-exit 'curl -fsS -X POST -H "Authorization: Bearer $RUNPOD_API_KEY" \
    https://rest.runpod.io/v1/pods/$RUNPOD_POD_ID/stop'
```

This is not built or tested here. Three things need checking before it is
relied on, and tbd-f78.22 owns that spike:

- Whether RunPod puts a pod-scoped `RUNPOD_API_KEY` in the pod, or one has to
  be added as a RunPod secret. The key should be the narrowest scope that
  can stop the pod, and it must never be in the image.
- Stop or terminate. A stopped pod still bills for its volume disk.
  Termination bills nothing more, but it deletes the pod's own volume. Only
  terminate when the results are synced somewhere off the pod.
- A watchdog for a runner that hangs or crashes before its on-exit command
  runs. One option is a second timer in the start command that stops the pod
  regardless, a little after `--max-lifetime`.

### What it costs to leave running

A pod bills by the hour from start until it is stopped, whether it is
generating or idle. RunPod's pricing page (fetched 2026-11-19) lists 48 GB
cards (RTX A6000, A40) at about $0.33-0.69/h, an L40S (48 GB) at
$0.79-1.09/h, and 24 GB cards (RTX 3090, 4090) at $0.22-0.89/h. The lower
figure is Community Cloud and the higher is Secure Cloud. A forgotten 48 GB
pod therefore costs roughly $8-17 a day, plus volume storage. That is why
`--max-lifetime` defaults to 12 h and the on-exit stop matters. For
comparison, tbd-f78.24 estimates the generation for the whole qwen3.6:27b T1
facts grid at 1.1-1.5 h on one pod.

### Open decisions

- **Storage for results.** `--sync DIR` is built. It covers a RunPod network
  volume mounted at `/workspace` (this survives termination, but it's tied
  to one region) and any other mounted directory. `--sync-command` runs any
  copy program, so S3-compatible storage (an S3 or R2 bucket) works with
  e.g. `'aws s3 sync {src} s3://BUCKET/experiments/{name}'` or `rclone`.
  Neither tool is in the image, so adding one is part of choosing it, and
  its keys come from RunPod secrets. A results branch in git is the third
  option from the bead. It isn't built, because it needs push credentials
  on the pod.
- **GPU size.** qwen3.6:27b at `num_ctx` 20480 needs its own KV cache for each
  concurrent request. With parallel > 1 (`OLLAMA_NUM_PARALLEL` and the
  definition's `parallel`), a 48 GB-class card is needed. A 24 GB card runs
  one request at a time. The choice trades the hourly rate against wall
  time, and the rates above should be compared with what serverless costs
  for the same grid.
- **Ollama version.** The image pins Ollama 0.40.13. Rows are only comparable
  with the Mac's runs if the runtime and weights match (tbd-f78.2). Each pod
  job records its model digest, which can be compared with the Mac's.
  Serverless jobs record no digest.

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
