"""Assembler wrapper (DASM) — Layer 0.

Task 1 scope only (see docs/plan/roadmap.md): assemble a source file into ROM
bytes, or report why it failed.

Confirmed by hand against the installed `dasm` 2.20.14.1:
  - `-f3` gives a raw binary with no origin/header bytes.
  - `-R` deletes the output file on a failed assembly, so "no ROM" is just
    "the file doesn't exist" rather than a stale/empty one.
  - All diagnostics (errors and warnings) go to stdout, one per line, in the
    default (MS-style) format: `<file> (<line>): error|warning: <message>`.
  - Exit code is 0 on success, nonzero on failure.
  - `include`/`incbin` paths are resolved relative to the *process's* cwd,
    not the source file's directory — so we run dasm with cwd set to the
    source file's own directory, which is what every fixture here assumes.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

DASM_BIN = "dasm"

# e.g. "red_background.asm (37): error: Unknown Mnemonic '$FFFA'."
_DIAGNOSTIC_RE = re.compile(r"^.+\(\d+\):\s+(error|warning):\s+.*$")


@dataclass
class AssembleResult:
    rom: bytes | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.rom) and not self.errors


def assemble(source_path: Path) -> AssembleResult:
    """Assemble a DASM source file into an Atari 2600 ROM."""
    # Resolve to absolute first: we run dasm with cwd set to the source
    # file's own directory (include/incbin paths resolve against the
    # process cwd, not the source file's location — confirmed by hand), and
    # a relative source_path would be silently re-resolved against that new
    # cwd instead of the caller's original one.
    source_path = Path(source_path).resolve()
    if shutil.which(DASM_BIN) is None:
        raise RuntimeError(f"'{DASM_BIN}' not found on PATH")

    with tempfile.TemporaryDirectory() as tmp:
        rom_path = Path(tmp) / "out.bin"
        proc = subprocess.run(
            [DASM_BIN, str(source_path), "-f3", f"-o{rom_path}", "-R"],
            capture_output=True,
            text=True,
            cwd=source_path.parent,
        )

        errors: list[str] = []
        warnings: list[str] = []
        for line in proc.stdout.splitlines():
            match = _DIAGNOSTIC_RE.match(line.strip())
            if match:
                bucket = errors if match.group(1) == "error" else warnings
                bucket.append(line.strip())

        rom = rom_path.read_bytes() if rom_path.exists() else None

        if not rom and not errors:
            # Exit code 0 with no "error:"-formatted line but still no ROM
            # bytes — e.g. dasm printed a plain "Warning: Unable to open"
            # for the source itself. Don't silently report success.
            errors.append(proc.stdout.strip() or f"dasm exited {proc.returncode}")

        return AssembleResult(rom=rom, errors=errors, warnings=warnings)
