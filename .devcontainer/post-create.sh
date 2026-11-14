#!/usr/bin/env bash
# Runs once, inside the container, right after it is created.
set -euo pipefail

echo "bd:     $(bd version 2>/dev/null | head -1)"
echo "claude: $(claude --version 2>/dev/null || echo 'not found')"
echo "git:    $(git --version)"
echo "dasm:   $(dasm 2>&1 | head -1)"
echo "python: $(python3 --version)"
echo "rust:   $(rustc --version)"

# Record the metrics opt-out in bd's user config too (the env vars already
# disable it; this keeps `bd metrics status` honest if they are ever unset).
bd metrics off >/dev/null 2>&1 || true

# Python env lives outside the shared folder: the Mac's .venv/ holds macOS
# binaries and must not be touched from here.
if [ ! -x /home/vscode/venv/bin/python ]; then
  python3 -m venv /home/vscode/venv
fi
/home/vscode/venv/bin/pip install -q -e '.[test]'

# Linux build of stella-core into build-linux/ (the Mac uses build/).
# build-linux/ lives on the bind mount and survives rebuilds; if the workspace
# path changed, its CMake cache points at the old source dir, so start fresh.
cache=stella-core/build-linux/CMakeCache.txt
if [ -f "$cache" ] && ! grep -qxF "CMAKE_HOME_DIRECTORY:INTERNAL=$PWD/stella-core" "$cache"; then
  echo "stella-core/build-linux was configured for another path; removing it."
  rm -rf stella-core/build-linux
fi
cmake -S stella-core -B stella-core/build-linux -DCMAKE_BUILD_TYPE=Debug >/dev/null
cmake --build stella-core/build-linux -j"$(nproc)"

# racer-check (vendored, vendor/racer/) into target-linux/ (the Mac uses target/).
cargo build -q --release --manifest-path vendor/racer/Cargo.toml --target-dir vendor/racer/target-linux

# Claude Code integration comes from `bd init` (init-project.sh): it writes a
# project-level .claude/settings.json hook that runs `bd prime` at session start.

if [ ! -d .beads ]; then
  echo
  echo "This project has no beads database yet. To start one, run:"
  echo "  bash .devcontainer/init-project.sh"
fi

echo
echo "Ready. Run the tests with:  pytest -q"
