#!/usr/bin/env bash
# Optional: builds stella-core, the headless Stella core, into
# stella-core/build-linux/ (the Mac uses build/). Nothing in the eval needs it;
# it's the reference emulator for cross-checks (tests/test_emulator.py, the
# LXA agreement test). Run by post-create.sh unless TBD_SKIP_STELLA=1; run it
# by hand any time: bash .devcontainer/build-stella.sh
set -euo pipefail

# build-linux/ lives on the bind mount and survives rebuilds; if the workspace
# path changed, its CMake cache points at the old source dir, so start fresh.
cache=stella-core/build-linux/CMakeCache.txt
if [ -f "$cache" ] && ! grep -qxF "CMAKE_HOME_DIRECTORY:INTERNAL=$PWD/stella-core" "$cache"; then
  echo "stella-core/build-linux was configured for another path; removing it."
  rm -rf stella-core/build-linux
fi
cmake -S stella-core -B stella-core/build-linux -DCMAKE_BUILD_TYPE=Debug >/dev/null
cmake --build stella-core/build-linux -j"$(nproc)"
