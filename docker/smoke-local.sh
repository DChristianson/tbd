#!/usr/bin/env bash
# Smoke-test the three local worker configurations (docs/running.md, "Worker
# configurations"). Run it on the host, from the repository root, with Docker
# available; not inside the dev container, which has no Docker.
#
#   docker/smoke-local.sh              # all three
#   docker/smoke-local.sh remote host  # some of them: remote, host, container
#
# Cells:
#   remote     worker image, model from RunPod serverless (tbd-f78.29.1).
#              Needs TBD_RUNPOD_ENDPOINT and TBD_RUNPOD_API_KEY in an env
#              file: ENV_FILE, default .env.runpod in the repository root.
#   host       worker image, model from Ollama on this machine (tbd-f78.5).
#              Needs Ollama listening beyond localhost (OLLAMA_HOST=0.0.0.0)
#              with qwen2.5-coder:1.5b pulled.
#   container  pod-runner image, Ollama inside the container on CPU (tbd-f78.27).
#
# Before its cells, each image runs a toolchain check: T1's reference program
# assembled with DASM and graded by racer-check inside the image. The small
# smoke model rarely writes a program that assembles, so this is what checks
# racer-check works in the image.
#
# Each cell writes to experiments/_smoke/<cell>/ (results) and
# experiments/_smoke/<cell>.log. Read a result with:
#   python -m tbd.eval.report experiments/_smoke/<cell>/<experiment>
set -euo pipefail

cd "$(dirname "$0")/.."
OUT="$PWD/experiments/_smoke"
SHA="$(git rev-parse HEAD)"
ENV_FILE="${ENV_FILE:-.env.runpod}"
CELLS=("${@:-remote host container}")
read -r -a CELLS <<< "${CELLS[*]}"
mkdir -p "$OUT"

build() {   # build <target> <tag>; once per run
  local target=$1 tag=$2
  echo "== building $tag (target $target)"
  docker build -q -f docker/worker.Dockerfile --target "$target" \
    --build-arg GIT_SHA="$SHA" -t "$tag" . > /dev/null
}

run_cell() {   # run_cell <name> <docker run args...>; records pass or fail
  local name=$1; shift
  mkdir -p "$OUT/$name"
  echo "== $name: running (log: experiments/_smoke/$name.log)"
  if docker run --rm "$@" > "$OUT/$name.log" 2>&1; then
    echo "== $name: exit 0"
  else
    echo "== $name: exit $? (see the log)"
  fi
}

needs_worker=0; needs_pod=0
for c in "${CELLS[@]}"; do
  case $c in
    remote|host) needs_worker=1 ;;
    container) needs_pod=1 ;;
    *) echo "unknown cell: $c (remote, host, container)" >&2; exit 2 ;;
  esac
done
[ $needs_worker = 1 ] || [ $needs_pod = 1 ] || exit 0
# pod-runner is built on worker, so building it also builds worker's layers.
[ $needs_worker = 1 ] && build worker tbd-worker
[ $needs_pod = 1 ] && build pod-runner tbd-pod-runner

toolchain() {   # toolchain <image>: DASM and racer-check grade a reference program
  local image=$1
  echo "== toolchain check in $image"
  if docker run --rm --entrypoint python "$image" -m pytest -q \
      "tests/test_tasks.py::test_reference_passes[t1/canonical]" > "$OUT/toolchain-$image.log" 2>&1; then
    echo "== toolchain check in $image: passed"
  else
    echo "== toolchain check in $image: FAILED (see experiments/_smoke/toolchain-$image.log)"
  fi
}
[ $needs_worker = 1 ] && toolchain tbd-worker
[ $needs_pod = 1 ] && toolchain tbd-pod-runner

for c in "${CELLS[@]}"; do
  case $c in
    remote)
      [ -f "$ENV_FILE" ] || { echo "remote: no env file at $ENV_FILE (set ENV_FILE)" >&2; continue; }
      run_cell remote --env-file "$ENV_FILE" \
        -v "$OUT/remote:/opt/tbd/experiments" \
        tbd-worker python -m tbd.eval.experiment experiments.d/t1-remote-smoke.yaml ;;
    host)
      run_cell host -e OLLAMA_HOST=http://host.docker.internal:11434 \
        --add-host=host.docker.internal:host-gateway \
        -v "$OUT/host:/opt/tbd/experiments" \
        tbd-worker python -m tbd.eval.experiment experiments.d/t1-pod-smoke.yaml ;;
    container)
      run_cell container -e TBD_DEFINITIONS=experiments.d/t1-pod-smoke.yaml \
        -e TBD_SYNC=/results -e TBD_MAX_LIFETIME=45m \
        -v "$OUT/container:/results" \
        tbd-pod-runner ;;
  esac
done
echo "== done; results under experiments/_smoke/"
