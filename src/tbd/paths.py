"""Where things live in the checkout. One place, so the eval and the tests agree.

Built tools default to the Mac build folders; the dev container points the
environment variables at its own (see .devcontainer/devcontainer.json).
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Task definitions: prompt-adjacent data, reference solution, check files.
TASKS = ROOT / "tasks"

# The fact store: facts/*.toml (facts/README.md).
FACTS = ROOT / "facts"

# DASM's vcs.h / macro.h (GPL-2.0-or-later; see its NOTICE.md).
HEADERS = ROOT / "vendor" / "atari2600"

# The grader (vendor/racer/NOTICE.md).
RACER_CHECK = Path(
    os.environ.get("TBD_RACER_CHECK")
    or ROOT / "vendor" / "racer" / "target" / "release" / "racer-check"
)

# Optional reference emulator (stella-core/README.md).
STELLA_CORE = Path(
    os.environ.get("TBD_STELLA_CORE")
    or ROOT / "stella-core" / "build" / "shim" / "stella_core"
)

# batari Basic, the BASIC-to-2600 compiler (.devcontainer/build-batari.sh).
BATARI = Path(
    os.environ.get("TBD_BATARI")
    or ROOT / "build" / "batari-basic"
)
