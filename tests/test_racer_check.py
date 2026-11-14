"""The vendored racer-check (vendor/racer/) grades Case 0's reference.

racer-check reads a check file whose `rom` path is relative to the check
file, so the check file is copied next to a freshly assembled ROM in a temp
dir rather than writing into tests/fixtures/.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tbd.tools.assembler import assemble

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
RACER_CHECK = Path(
    os.environ.get("TBD_RACER_CHECK")
    or ROOT / "vendor" / "racer" / "target" / "release" / "racer-check"
)

pytestmark = pytest.mark.skipif(
    not RACER_CHECK.exists(),
    reason="racer-check not built — see vendor/racer/NOTICE.md",
)


def _check(asm: Path, tmp_path: Path) -> dict:
    result = assemble(asm)
    assert result.ok, result.errors
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "red_background.bin").write_bytes(result.rom)
    shutil.copy(FIXTURES / "red_background.check.toml", tmp_path)
    proc = subprocess.run(
        [str(RACER_CHECK), "--json", "red_background.check.toml"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert proc.returncode in (0, 1), proc.stderr
    return json.loads(proc.stdout.splitlines()[0])


def test_case0_reference_passes_racer_check(tmp_path):
    report = _check(FIXTURES / "red_background.asm", tmp_path)
    assert report["passed"], report["unexpected"]
    assert report["runs"] == 101


def test_racer_check_rejects_a_263_line_frame(tmp_path):
    """The bug Case 0 once had: one overscan line too many."""
    bad = tmp_path / "src"
    bad.mkdir()
    shutil.copytree(FIXTURES / "vendor", bad / "vendor")
    source = (FIXTURES / "red_background.asm").read_text()
    assert "ldx #29" in source
    (bad / "red_background.asm").write_text(source.replace("ldx #29", "ldx #30"))
    report = _check(bad / "red_background.asm", tmp_path)
    assert not report["passed"]
