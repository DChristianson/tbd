# Running experiments

This guide says how experiments are run today, how the pod runner is meant to
run them on a RunPod GPU pod, and what isn't built yet. Each gap names the bead
that covers it. The design behind it is in `docs/design/execution.md`.

Statements about RunPod and other outside services that haven't been tried
from this project are marked *(unchecked)*.

## Three ways to run

| Where the runner runs | Where the model runs | Status |
|---|---|---|
| Laptop or dev container | Ollama on the Mac | Works. Slow for large models. |
| Laptop or dev container | RunPod serverless endpoint (`provider: runpod`) | Works. Stops when the laptop stops. In the 2026-11-19 grid, workers ranged from 55 to 145 tokens per second, and went cold when idle between attempts. |
| A RunPod GPU pod | Ollama inside the same pod | The pod runner (`tbd.eval.podrunner`). Code and tests exist; the image has never been built or run on a pod. |

The first two use `python -m tbd.eval.experiment <definition.yaml>` from a
checkout (README.md, "Building"). The rest of this guide is about the third.

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

**Not yet tried end to end (tbd-f78.27).** These steps should work with Docker
Desktop on a Mac. They run Ollama on the CPU inside the container, with a
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
| tbd-f78.7 | Build, test and publish the image from GitHub Actions |
| tbd-f78.25 | Fetch definitions at start; partial definitions |
| tbd-f78.26 | S3 client in the image; built-in S3 sync; test against MinIO |
| tbd-f78.22 | Self-stop through RunPod's API, idle watchdog, outside check |
| tbd-f78.27 | Try the local test end to end, and keep it working |
