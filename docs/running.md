# Running experiments

This guide says how experiments are run today, how the pod runner is meant to
run them on a RunPod GPU pod, and what isn't built yet. Each gap names the bead
that covers it. The design behind it is in `docs/design/execution.md`.

Statements about RunPod and other outside services that haven't been tried
from this project are marked *(unchecked)*.

## Worker configurations

A worker is what runs the experiment runner and the grader (DASM and
racer-check). Those only need a CPU. The model can run somewhere else, so a
configuration is three choices:

- **Where the worker runs:** a local machine (in Docker, or directly from a
  checkout), or a RunPod pod.
- **Where the model comes from:** a remote provider, Ollama on the host
  machine, or Ollama inside the worker's container.
- **What the model runs on:** CPU or GPU.

Each model in a definition names its provider: `ollama` (at the URL in
`OLLAMA_HOST`), `runpod` (a serverless endpoint) or `anthropic`. So, apart
from the one row blocked on tbd-f78.28, moving between configurations means
changing the image, its command, the environment and the definition, not the
code. Every row except the out-of-scope one has a smoke-test bead under
tbd-f78.29.

| Worker | Model from | Model runs on | Image and setting | Status | Smoke test |
|---|---|---|---|---|---|
| Local | Remote provider: RunPod serverless, or the Anthropic API | The provider's hardware | `worker`, with the provider's credentials as environment variables (`TBD_RUNPOD_ENDPOINT` and `TBD_RUNPOD_API_KEY`, or `TBD_ANTHROPIC_API_KEY`). For Anthropic, build with `--build-arg EXTRAS=test,eval`, because the default image has no Anthropic SDK. | Works from a checkout; not yet tried in the image | tbd-f78.29.1 |
| Local | Ollama on the host, outside Docker | The host's GPU, if Ollama can use it there (Metal on a Mac, NVIDIA on Linux), else its CPU | `worker`, with `OLLAMA_HOST=http://host.docker.internal:11434`. Ollama on the host must listen beyond localhost (`OLLAMA_HOST=0.0.0.0` where Ollama runs). On Linux, `host.docker.internal` also needs `--add-host=host.docker.internal:host-gateway` *(unchecked)*. | Works from a checkout; not yet tried in the image | tbd-f78.5 |
| Local | Ollama in the container | CPU | `pod-runner` | Not yet tried | tbd-f78.27 |
| Local | Ollama in the container | GPU | — | Out of scope (see below) | — |
| RunPod pod | Remote provider | A CPU pod for the worker *(unchecked)*; the provider's hardware for the model | The `worker` image with its command set to `python -m tbd.eval.podrunner` (without `--serve-ollama`) | Blocked: the pod runner accepts only `ollama` models (tbd-f78.28). Also needs a published image (tbd-f78.7). | tbd-f78.29.2 |
| RunPod pod | Ollama in the container | CPU pod *(unchecked)* | `pod-runner` | Not yet tried; needs a published image (tbd-f78.7) | tbd-f78.29.3 |
| RunPod pod | Ollama in the container | GPU pod | `pod-runner` | Not yet tried; needs a published image (tbd-f78.7) | tbd-f78.29.4 |

**The basic smoke test** for each configuration: one sample of T1 with a small
model runs to completion, and its results load in `tbd.eval.report`. The
Ollama rows use `experiments.d/t1-pod-smoke.yaml`. The remote-provider rows
need a smoke definition that sends one cheap request, which tbd-f78.29.1 will
write. The test doesn't need S3 sync or self-stop. A pod's results can stay on
its volume, and the pod is stopped by hand.

**A local worker with Ollama in the container on a GPU is out of scope,**
because it isn't available everywhere:

- Docker Desktop on a Mac can't give containers the GPU. Containers run in a
  Linux VM, and Metal exists only on macOS. Docker says so itself
  ([Docker, 2026](https://www.docker.com/blog/docker-model-runner-vllm-metal-macos/)).
  On a Mac, the GPU is reached through Ollama on the host (the second row).
- A Linux machine with an NVIDIA GPU can pass it to Docker with `--gpus all`
  *(unchecked)*.
- Podman with its libkrun provider can give Mac containers the GPU through
  Vulkan. Red Hat measured llama.cpp that way at about 77% of native Metal
  speed ([Red Hat, 2025](https://developers.redhat.com/articles/2025/06/05/how-we-improved-ai-inference-macos-podman-containers)).
  It needs a Vulkan build of Ollama and patched drivers in the image, and
  is experimental. Not tried here.

From a checkout, without Docker, the first two rows are how experiments have
been run so far. The 2026-11-19 facts grid used RunPod serverless. Its
serverless GPU workers ranged from 55 to 145 tokens per second, and went cold
when idle between attempts. The rest of this guide is mostly about the pod
runner.

## What the pod-runner image contains

`docker/worker.Dockerfile` builds two images, separate from the dev container:

- **`worker`**: Python, tbd, DASM and racer-check. No model and no GPU.
- **`pod-runner`**: the worker plus Ollama. Its entrypoint is
  `python -m tbd.eval.podrunner --serve-ollama`.

The image copies the whole checkout to `/opt/tbd`, so these are inside it:

- **`tasks/`**: every task's prompt, reference program and check files.
- **`facts/`**: the fact store.
- **`experiments.d/`**: the experiment definitions that existed when the image
  was built.

These are left out (`docker/worker.Dockerfile.dockerignore`): `experiments/`
(results), `.git`, `.beads`, editor and agent folders, `stella-core/`, and any
`.env*` file.

Model weights are not in the image. The pod runner pulls each model the
definitions name from the Ollama registry when it starts, into Ollama's model
folder. The container runs as root and the image doesn't set `OLLAMA_MODELS`,
so that folder is `/root/.ollama/models`. A large model takes a while to pull
on each new pod. To keep the weights between pods, set
`OLLAMA_MODELS=/workspace/ollama` with a RunPod network volume mounted at
`/workspace` *(unchecked)*.

## Getting a definition to the pod

**Works now:**

- **Baked into the image.** Any `experiments.d/*.yaml` present at build time.
  Name it in `TBD_DEFINITIONS` (space-separated paths) or as an argument.
- **On a mounted volume.** Put definitions on a RunPod network volume and
  point `TBD_DEFINITIONS` at their paths there.

**Not built (tbd-f78.25):** fetching a definition when the pod starts, for
example from the S3 bucket that results go to, so a new experiment doesn't
need a new image. Also not built: a partial definition that names a base
definition and changes only some settings (for example `samples` or `tasks`).
Today each definition must be complete.

## Where results go

The pod runner runs each definition with the normal experiment runner, so
results land in `/opt/tbd/experiments/<name>/` inside the pod, in the usual
layout. After every job, every definition, and on exit, it copies that folder
to a sync target. It only adds and updates files at the target, never deletes.

**Works now:**

- `--sync DIR` (or `TBD_SYNC`): copy to a directory, such as a network volume.
- `--sync-command TEMPLATE` (or `TBD_SYNC_COMMAND`): run any copy program, with
  `{src}` and `{name}` filled in. For example
  `aws s3 sync {src} s3://bucket/experiments/{name}`.
- One of these, or an explicit `--no-sync`, is required, so results can't be
  lost by forgetting the setting.

**Not built (tbd-f78.26):** an S3 client in the image. `--sync-command` works
only once one is added (aws CLI, rclone, or Python's boto3). Built-in S3
sync would also let the target be an S3-compatible service such as R2 or
MinIO, and be tested locally against MinIO.

**Not built (tbd-f78.30): a run code for each launch.** Today a rerun can
overwrite earlier results at the sync target. Locally, an experiment's folder
name comes from its definition, and it doubles as the key for resuming:
running the same definition again skips the jobs already done. A pod starts
with an empty `experiments/`, though, so it can't see what an earlier launch
synced. Launching the same definition again reruns every job under the same
names and copies over the earlier files at the target. This happened in a
local smoke rerun on 2026-11-20.

The fix: each pod-runner launch gets a run code, such as
`20261120T1930Z-a3f9c1` (the UTC time plus six random hex digits), and syncs
to `<target>/<experiment>/<run code>/`. Each job's `config.json` and the
manifest record the code, so a result can be traced to its launch. Local runs
and resuming are unchanged. Resuming a pod run from an earlier launch's
results would be a later addition.

## How the pod stops

**Works now:** the pod runner exits when the queue is empty, or when
`--max-lifetime` (default 12h, `TBD_MAX_LIFETIME`) runs out. At the limit,
running jobs get SIGTERM and mark themselves `interrupted`. Either way it syncs
once more, then runs `--on-exit` (`TBD_ON_EXIT`). Exit status:

- **0:** every job done.
- **1:** something not done, a sync failed, or setup failed (Ollama didn't
  start, or a model didn't pull). The final sync and `--on-exit` still run.
- **2:** bad arguments, a missing or invalid definition, or no sync choice.
  The pod runner stops before starting anything, and **`--on-exit` does not
  run**, so a self-stop hook wouldn't fire either.
- **124:** the lifetime ran out.
- **130:** stopped by a signal.

**Not built (tbd-f78.22):**

- **Stopping the pod itself.** The process exiting may not stop billing:
  RunPod may restart a pod's container when it exits *(unchecked)*. The
  plan is for `--on-exit` to call RunPod's API to stop or terminate the pod.
  Whether RunPod gives the pod an API key, and whether stop or terminate is
  the right call, still has to be checked.
- **An idle watchdog.** Today only the lifetime limit catches a hung run. A
  watchdog would stop the pod when no job has made progress for some minutes.
  Each job's `metrics.json` has an `updated_at` heartbeat, rewritten after
  every attempt, so a heartbeat that stops moving shows a stalled job.
- **Stopping when there's nothing to run.** A model that won't pull already
  exits with status 1 after `--on-exit`, so it will stop the pod once
  self-stop exists. A missing or invalid definition exits with status 2
  without running `--on-exit`, so that path also needs to run it.
- **An outside check.** Something off the pod, such as a scheduled job, that
  terminates pods older than a limit, in case the pod runner itself fails.

## Test the pod runner locally

**Tried on 2026-11-20, not yet passing (tbd-f78.27).** On a Mac with Docker
Desktop, the image built, the pod runner started Ollama, pulled the model, ran
the job and synced the results. But Ollama was killed loading llama3.1 in
Docker's default 7.7 GiB VM; give Docker 12 GB or more. These steps should work
with Docker Desktop on a Mac. They run Ollama on the CPU inside the container, with a
model small enough for that. The smoke definition
`experiments.d/t1-pod-smoke.yaml` runs one sample of T1, one-shot and with
no facts, with `llama3.1` (8B); it checks the plumbing, not the model.
`docker/smoke-local.sh` runs this and the other local configurations.

Check the definition without Docker, from a checkout:

```sh
python -m tbd.eval.podrunner experiments.d/t1-pod-smoke.yaml --no-sync --dry-run
```

Build the image for your machine's architecture (arm64 on Apple Silicon) and
run it, with results synced to a folder on the host:

```sh
docker build -f docker/worker.Dockerfile --target pod-runner \
  --build-arg GIT_SHA=$(git rev-parse HEAD) -t tbd-pod-runner .
mkdir -p /tmp/tbd-results
docker run --rm -v /tmp/tbd-results:/results \
  -e TBD_DEFINITIONS=experiments.d/t1-pod-smoke.yaml -e TBD_SYNC=/results \
  -e TBD_MAX_LIFETIME=30m tbd-pod-runner
```

Then read the results with the normal report:

```sh
python -m tbd.eval.report /tmp/tbd-results/t1-pod-smoke
```

The same run against MinIO, a local S3-compatible server, would test the S3
path once tbd-f78.26 exists.

On a Linux host, the synced files are owned by root, because the container runs
as root. They can be read normally, but removing them needs root.

## Run on RunPod

**Not yet done.** The steps, once the image is published (tbd-f78.7):

1. **Publish the image** for `linux/amd64`, the platform RunPod's GPUs run, to
   GitHub's container registry (GHCR) from GitHub Actions, tagged with its
   commit. Building on an Apple Silicon Mac produces arm64 unless
   `--platform linux/amd64` is given. The repository is public and the image
   holds no secrets, so it can be public too, and a pod could then pull it
   with no registry credentials *(unchecked)*.
2. **Create a pod** from a template, with:
   - the image;
   - a GPU with enough memory. For qwen3.6:27b at `num_ctx` 20480, each
     request needs its own memory cache, so a 24 GB card is expected to run
     one at a time, and more than one at once needs a 48 GB-class card
     *(estimated, not measured)*;
   - optionally, a network volume for model weights;
   - these environment variables:
     - `TBD_DEFINITIONS`, `TBD_MAX_LIFETIME`
     - `TBD_SYNC` or `TBD_SYNC_COMMAND`
     - `OLLAMA_NUM_PARALLEL`, matching the definition's `parallel`
     - `TBD_ON_EXIT`, once self-stop exists
     - the S3 credentials, as RunPod secrets *(unchecked)*
3. **Watch** the pod's log, or the results arriving in the bucket.
4. **Read the results** from the bucket with the normal report.
5. **Check the pod stopped.** Until tbd-f78.22, stop it by hand.

## Secrets

The goal is that the pod holds as little as possible, and nothing that can
do more than its job needs.

| Secret | Needed by | How to keep it minimal |
|---|---|---|
| GitHub | Only GitHub Actions, to publish the image | Actions' own `GITHUB_TOKEN`, given `packages: write` in the publish job only *(unchecked)*. The pod needs none if the image is public. |
| Hugging Face | Nobody, today | The pod runner pulls models through Ollama, from Ollama's registry. A token would only matter for pulling a gated or private model from Hugging Face *(unchecked)*. |
| S3 (or R2) | The pod, to write results | A key limited to one bucket, or one prefix, that can't delete objects or reach other buckets. Copy tools such as `aws s3 sync` and `rclone copy` list the destination to compare files, so they also need list access there; a built-in sync that only uploads could do without it. Passed to the pod as a secret environment variable, never in the image or the definition. |
| RunPod | You, to create pods; the pod, only to stop itself | Your account key stays on your machine. For self-stop, use the most restricted key RunPod offers, or one RunPod gives the pod itself if it does (to check in tbd-f78.22). |

Results are written by the pod and read on your machine, so the bucket key on
the pod shouldn't need to read results back, beyond the listing a copy tool
does.

## Open work

| Bead | What |
|---|---|
| tbd-f78.29 | Smoke-test each worker configuration (one child bead per row of the table above, except the out-of-scope one) |
| tbd-f78.28 | Pod runner with any provider; serve Ollama only when needed |
| tbd-f78.7 | Build, test and publish the image from GitHub Actions |
| tbd-f78.25 | Fetch definitions at start; partial definitions |
| tbd-f78.26 | S3 client in the image; built-in S3 sync; test against MinIO |
| tbd-f78.22 | Self-stop through RunPod's API, idle watchdog, outside check |
