"""Job records besides results: toolchain identity in manifest.json, console.log (no model calls)."""

import json
import shutil
import sys

import pytest

from tbd.eval import case0, jobrecords
from tbd.paths import RACER_CHECK


def test_toolchain_records_null_rather_than_guessing(monkeypatch, tmp_path):
    monkeypatch.delenv("TBD_IMAGE_DIGEST", raising=False)
    monkeypatch.setattr(jobrecords, "DASM_BIN", "no-such-dasm")
    monkeypatch.setattr(jobrecords, "RACER_CHECK", tmp_path / "missing")
    assert jobrecords.toolchain() == {
        "image_digest": None, "dasm": None,
        "racer_check": {"path": str(tmp_path / "missing"), "sha256": None}}


def test_toolchain_reads_image_digest_and_hashes_racer_check(monkeypatch, tmp_path):
    monkeypatch.setenv("TBD_IMAGE_DIGEST", "sha256:abc")
    binary = tmp_path / "racer-check"
    binary.write_bytes(b"hello")
    monkeypatch.setattr(jobrecords, "RACER_CHECK", binary)
    tc = jobrecords.toolchain()
    assert tc["image_digest"] == "sha256:abc"
    assert tc["racer_check"]["sha256"] == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824")


@pytest.mark.skipif(shutil.which("dasm") is None, reason="dasm not installed")
def test_dasm_version():
    v = jobrecords.dasm_version()
    assert v and v[0].isdigit() and "." in v


def test_console_log_tees_and_logs_tracebacks(tmp_path, capsys):
    with pytest.raises(RuntimeError):
        with jobrecords.console_log(tmp_path):
            print("to stdout")
            print("to stderr", file=sys.stderr)
            raise RuntimeError("boom")
    log = (tmp_path / "console.log").read_text()
    assert "to stdout\nto stderr\n" in log
    assert "RuntimeError: boom" in log
    out = capsys.readouterr()
    assert out.out == "to stdout\n" and out.err == "to stderr\n"


@pytest.mark.skipif(not RACER_CHECK.exists(), reason="racer-check not built")
def test_job_writes_manifest_toolchain_and_console_log(tmp_path, monkeypatch):
    reply = {"message": {"content": "no program here"}, "done_reason": "stop"}
    monkeypatch.setattr(case0, "chat", lambda *a, **k: reply)
    monkeypatch.setattr(case0, "model_digest", lambda m: "digest")
    monkeypatch.setattr(case0, "model_template", lambda m: "template")
    case0.main(["--model", "fake", "--samples", "1", "--experiment", "e", "--out", str(tmp_path)])
    exp = tmp_path / "e"
    manifest = json.loads((exp / "manifest.json").read_text())
    assert set(manifest["toolchain"]) == {"image_digest", "dasm", "racer_check"}
    assert manifest["toolchain"]["racer_check"]["sha256"]
    job = exp / "jobs" / "fake-t1-canonical-none-one-shot"
    assert json.loads((job / "config.json").read_text())["toolchain"] == manifest["toolchain"]
    log = (job / "console.log").read_text()
    assert "sample 0 attempt 1: no_code" in log
    assert '"status": "completed"' in log
    assert json.loads((job / "metrics.json").read_text())["status"] == "completed"
