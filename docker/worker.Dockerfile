# syntax=docker/dockerfile:1
#
# Images that run experiments, separate from the dev container
# (.devcontainer/Dockerfile). Build from the repository root, which is the
# build context; worker.Dockerfile.dockerignore beside this file says what
# stays out of it:
#
#   docker build -f docker/worker.Dockerfile --target worker \
#       --build-arg GIT_SHA=$(git rev-parse HEAD) -t tbd-worker .
#   docker build -f docker/worker.Dockerfile --target pod-runner \
#       --build-arg GIT_SHA=$(git rev-parse HEAD) -t tbd-pod-runner .
#
# Stages:
#   build       compilers only: builds DASM and racer-check. Not shipped.
#   worker      Python + tbd, with DASM and racer-check copied in. No model, no
#               GPU, no compilers. Runs experiments against a model served
#               elsewhere.
#   pod-runner  worker + Ollama, for one GPU machine that serves the model and
#               runs a queue of experiments (tbd.eval.podrunner).
# How to run them: docs/design/execution.md ("Worker image", "Pod runner").
#
# The DASM and Rust versions match the dev container's, so a job graded here
# and one graded there use the same tools. tests/test_podrunner.py checks that
# the pins below and the ones in .devcontainer/Dockerfile agree; change both
# together.

# ==== build: DASM and racer-check =============================================
FROM debian:bookworm-slim AS build
ARG TARGETARCH

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl build-essential \
 && rm -rf /var/lib/apt/lists/*

# DASM from source. The hash is of GitHub's source archive for the tag.
ARG DASM_VERSION=2.20.14.1
ARG DASM_SHA256=ec71ffd10eeaa70bf7587ee0d79a92cd3f0a017c0d6d793e37d10359ceea663a
RUN set -eu; \
    cd /tmp; \
    curl -fsSL -o dasm.tar.gz "https://github.com/dasm-assembler/dasm/archive/refs/tags/${DASM_VERSION}.tar.gz"; \
    echo "$DASM_SHA256  dasm.tar.gz" | sha256sum -c -; \
    tar -xzf dasm.tar.gz; \
    make -C "dasm-${DASM_VERSION}" build; \
    install -m 0755 "dasm-${DASM_VERSION}/bin/dasm" /usr/local/bin/dasm

# Rust, pinned by rustup-init version and hash, only to build racer-check.
ARG RUSTUP_VERSION=1.29.3
ARG RUSTUP_SHA256_arm64=9f608c142e0e38a78957d0661ba1dfaf0b6fb37184cfaa4b9a0aa57af6184282
ARG RUSTUP_SHA256_amd64=b1c8cb14e30889e7e2999e6b69e62d9e9b9ffc0f4df8180564d306d9680e6c55
ARG RUST_VERSION=1.100.0
ENV RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo
RUN set -eu; \
    case "$TARGETARCH" in \
      arm64) arch=aarch64; sha="$RUSTUP_SHA256_arm64" ;; \
      amd64) arch=x86_64;  sha="$RUSTUP_SHA256_amd64" ;; \
      *) echo "unsupported arch: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/rustup-init "https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/${arch}-unknown-linux-gnu/rustup-init"; \
    echo "$sha  /tmp/rustup-init" | sha256sum -c -; \
    chmod +x /tmp/rustup-init; \
    /tmp/rustup-init -y -q --no-modify-path --profile minimal --default-toolchain "$RUST_VERSION"
ENV PATH="/usr/local/cargo/bin:${PATH}"

# racer-check, the grader, from the vendored source at its Cargo.lock versions.
COPY vendor/racer /src/racer
RUN cargo build -q --release --locked --manifest-path /src/racer/Cargo.toml \
          --target-dir /src/target \
 && install -m 0755 /src/target/release/racer-check /usr/local/bin/racer-check


# ==== worker: the harness, no model ===========================================
FROM debian:bookworm-slim AS worker

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates python3 python3-venv \
 && rm -rf /var/lib/apt/lists/*

COPY --from=build /usr/local/bin/dasm /usr/local/bin/racer-check /usr/local/bin/
ENV TBD_RACER_CHECK=/usr/local/bin/racer-check

# The commit the image was built from. The image has no .git, so case0 records
# this as the experiment's git_sha. Unset, git_sha is "".
ARG GIT_SHA=
ENV TBD_GIT_SHA=${GIT_SHA}

RUN useradd --create-home --uid 1000 tbd

WORKDIR /opt/tbd
# The checkout, minus what worker.Dockerfile.dockerignore leaves out. tbd is
# installed editable because it finds tasks/, facts/ and vendor/ beside its
# source (tbd.paths). The optional stella-core emulator isn't built: the eval
# doesn't use it, and its tests skip without it.
COPY --chown=tbd:tbd . /opt/tbd

# EXTRAS=test,eval adds the Anthropic SDK, for runs against Claude.
ARG EXTRAS=test
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install -q --no-cache-dir -e "/opt/tbd[${EXTRAS}]" \
 && chown tbd:tbd /opt/tbd
# WORKDIR made /opt/tbd as root, and COPY --chown only changes what it copies,
# so the chown above gives tbd the folder itself. Without it, tbd couldn't
# create experiments/ (left out of the image) or pytest's cache there.
ENV PATH="/opt/venv/bin:${PATH}"

USER tbd
CMD ["python", "-m", "tbd.eval.experiment", "--help"]


# ==== pod-runner: worker + Ollama, for one GPU machine ========================
# Serves the model in the same container and runs a queue of experiment
# definitions with tbd.eval.podrunner. Models are pulled when it starts (the
# definitions name them), not baked in.
FROM worker AS pod-runner
ARG TARGETARCH

# It runs as root. GPU pods (RunPod's included) mount volumes such as
# /workspace owned by root and expect the container to be root, and the
# machine runs nothing else. Ollama listens on 127.0.0.1 only: it has no
# authentication, so its port must never be exposed.
USER root

# Ollama, pinned by version and hash. The hashes are the release's own
# sha256sum.txt entries, and they match the digests GitHub lists for the
# assets. To upgrade: change OLLAMA_VERSION and paste in both lines for
#   ollama-linux-{amd64,arm64}.tar.zst
# from https://github.com/ollama/ollama/releases/download/v<ver>/sha256sum.txt
ARG OLLAMA_VERSION=0.40.13
ARG OLLAMA_SHA256_amd64=c9490e0f520be21271d969e6703f012268b1d39f8b70dd58854e5bfef7a75bb6
ARG OLLAMA_SHA256_arm64=913d9a28da6da86d551ce4ca531457767bb14d511cbc1d5ca93e9b3b8b9a8666
RUN set -eu; \
    case "$TARGETARCH" in \
      arm64) sha="$OLLAMA_SHA256_arm64" ;; \
      amd64) sha="$OLLAMA_SHA256_amd64" ;; \
      *) echo "unsupported arch: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl zstd; \
    f="ollama-linux-${TARGETARCH}.tar.zst"; \
    curl -fsSL -o "/tmp/$f" "https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}/$f"; \
    echo "$sha  /tmp/$f" | sha256sum -c -; \
    tar --zstd -xf "/tmp/$f" -C /usr/local; \
    rm -f "/tmp/$f"; \
    apt-get purge -y curl; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/*; \
    ollama --version 2>&1 | grep -q "${OLLAMA_VERSION}"

# OLLAMA_HOST is read by both Ollama (where to listen) and tbd (where to call).
# Keep the model loaded between jobs rather than unloading it after 5 idle
# minutes. OLLAMA_NUM_PARALLEL (concurrent requests per model) is left to the
# pod's settings, because it depends on the GPU's memory (execution.md).
# The NVIDIA variables make the host's driver and nvidia-smi visible.
ENV OLLAMA_HOST=http://127.0.0.1:11434 \
    OLLAMA_KEEP_ALIVE=-1 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

ENTRYPOINT ["python", "-m", "tbd.eval.podrunner", "--serve-ollama"]
