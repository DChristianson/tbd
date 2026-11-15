# Image for agentic development on tbd with beads (bd), git and Claude Code.
# See README.md in this folder for what each piece is for and why.

FROM mcr.microsoft.com/devcontainers/base:bookworm

ARG TARGETARCH

# ---- Base tools --------------------------------------------------------------
# There is deliberately no sudo inside the running container, so installs
# belong in this file.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git ca-certificates curl jq ripgrep less procps \
 && rm -rf /var/lib/apt/lists/*

# ---- tbd toolchain -----------------------------------------------------------
# Python for src/tbd + tests; a C++20 compiler and CMake for stella-core.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip \
      build-essential cmake \
 && rm -rf /var/lib/apt/lists/*

# DASM, built from source at the same version the host uses (2.20.14.1; see
# src/tbd/tools/assembler.py). The hash is of GitHub's source archive for the
# tag. To upgrade: change DASM_VERSION, download
#   https://github.com/dasm-assembler/dasm/archive/refs/tags/<ver>.tar.gz
# and paste in its `shasum -a 256`.
ARG DASM_VERSION=2.20.14.1
ARG DASM_SHA256=ec71ffd10eeaa70bf7587ee0d79a92cd3f0a017c0d6d793e37d10359ceea663a
RUN set -eu; \
    cd /tmp; \
    curl -fsSL -o dasm.tar.gz "https://github.com/dasm-assembler/dasm/archive/refs/tags/${DASM_VERSION}.tar.gz"; \
    echo "$DASM_SHA256  dasm.tar.gz" | sha256sum -c -; \
    tar -xzf dasm.tar.gz; \
    make -C "dasm-${DASM_VERSION}" build; \
    install -m 0755 "dasm-${DASM_VERSION}/bin/dasm" /usr/local/bin/dasm; \
    rm -rf /tmp/dasm*

# Rust, for the vendored racer-check (vendor/racer/; built by post-create.sh).
# rustup-init is pinned by version and hash (written here, not fetched from
# the archive's own .sha256 at build time). To upgrade: change RUSTUP_VERSION,
# download .../rustup/archive/<ver>/<arch>-unknown-linux-gnu/rustup-init for
# both arches, paste in their `sha256sum`; change RUST_VERSION to the toolchain.
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
    /tmp/rustup-init -y -q --no-modify-path --profile minimal --default-toolchain "$RUST_VERSION"; \
    rm /tmp/rustup-init; \
    chmod -R a+rX "$RUSTUP_HOME" "$CARGO_HOME"
ENV PATH="/usr/local/cargo/bin:${PATH}"

# ---- beads (bd) --------------------------------------------------------------
# Pinned release. The hashes are written here (not fetched from the release's
# own checksums.txt) and were checked against GitHub build attestations with:
#   gh attestation verify beads_<ver>_linux_<arch>.tar.gz --repo gastownhall/beads
# Built by .github/workflows/release.yml at refs/tags/v1.3.4 (commit 7ef2fd0).
# To upgrade: change BD_VERSION, re-run that check, update both hashes.
ARG BD_VERSION=1.3.4
ARG BD_SHA256_arm64=f4ee6eb3ec79bb2d7eb9d717c73efbcb6fa763cc70f726b4d055b72afdd68ad5
ARG BD_SHA256_amd64=175c26904bde3ff9168d2282ff6b56f17b17f60c22fa5acb3dacc9fb3465303a
RUN set -eu; \
    case "$TARGETARCH" in \
      arm64) sha="$BD_SHA256_arm64" ;; \
      amd64) sha="$BD_SHA256_amd64" ;; \
      *) echo "unsupported arch: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    f="beads_${BD_VERSION}_linux_${TARGETARCH}.tar.gz"; \
    curl -fsSL -o "/tmp/$f" "https://github.com/gastownhall/beads/releases/download/v${BD_VERSION}/$f"; \
    echo "$sha  /tmp/$f" | sha256sum -c -; \
    tar -xzf "/tmp/$f" -C /tmp bd; \
    install -m 0755 /tmp/bd /usr/local/bin/bd; \
    rm -f "/tmp/$f" /tmp/bd

# ---- Lock down the dev user --------------------------------------------------
# The base image gives "vscode" passwordless sudo. Remove it so nothing running
# as that user (the agent, bd, bd's repo hooks) can become root.
RUN rm -f /etc/sudoers.d/vscode \
 && mkdir -p /home/vscode/.claude \
 && chown -R vscode:vscode /home/vscode/.claude

USER vscode
# Toolchain stays root-owned in /usr/local; cargo's download cache goes here.
ENV CARGO_HOME=/home/vscode/.cargo

# ---- Claude Code -------------------------------------------------------------
# Native installer; installs to ~/.local/bin for the vscode user.
RUN curl -fsSL https://claude.ai/install.sh | bash
ENV PATH="/home/vscode/venv/bin:/home/vscode/.local/bin:${PATH}"
