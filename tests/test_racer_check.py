"""The vendored racer-check (vendor/racer/) and tbd's additions to it, using
Task 1's reference. That every task's reference passes its own check is
tests/test_tasks.py.

racer-check reads a check file whose `rom` path is relative to the check
file, so each test assembles into a temp dir and writes its check file there
rather than writing into tasks/.
"""

import json
import subprocess

import pytest

from tbd.eval.grading import with_rom
from tbd.paths import HEADERS, RACER_CHECK, TASKS
from tbd.tools.assembler import assemble

REFERENCE = TASKS / "t1" / "canonical" / "reference.asm"
CHECK = TASKS / "t1" / "canonical" / "check.toml"

pytestmark = pytest.mark.skipif(
    not RACER_CHECK.exists(),
    reason="racer-check not built — see vendor/racer/NOTICE.md",
)


def _rom(tmp_path, source: str) -> bytes:
    """Assemble `source` (which includes "vcs.h" / "macro.h") to tmp/build/prog.bin."""
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "prog.asm").write_text(source)
    result = assemble(src / "prog.asm", include_dirs=[HEADERS])
    assert result.ok, result.errors
    (tmp_path / "build").mkdir(exist_ok=True)
    (tmp_path / "build" / "prog.bin").write_bytes(result.rom)
    return result.rom


def _racer(tmp_path, source: str, check_toml: str) -> dict:
    """racer-check's report for `source` against `check_toml` (rom = build/prog.bin)."""
    _rom(tmp_path, source)
    (tmp_path / "t.check.toml").write_text(check_toml)
    proc = subprocess.run([str(RACER_CHECK), "--json", "t.check.toml"], cwd=tmp_path,
                          capture_output=True, text=True)
    assert proc.returncode in (0, 1), proc.stderr
    return json.loads(proc.stdout.splitlines()[0])


def _check(tmp_path, source: str) -> dict:
    """Against Case 0's strict check file."""
    return _racer(tmp_path, source, with_rom(CHECK.read_text()))


def test_case0_with_lxa_passes(tmp_path):
    """CLEAN_START's default expansion uses the undocumented lxa #0 ($AB).
    Undocumented opcodes are allowed (beads tbd-dpu.10), so the grader must
    run it rather than stop (tbd-818.11)."""
    source = REFERENCE.read_text()
    assert "NO_ILLEGAL_OPCODES = 1" in source
    with_lxa = source.replace("NO_ILLEGAL_OPCODES = 1", "")
    assert _rom(tmp_path, with_lxa)[2] == 0xAB, "expected lxa as CLEAN_START's third byte"
    report = _check(tmp_path, with_lxa)
    assert report["passed"], report["unexpected"][:1]


def test_lxa_agrees_with_stella(tmp_path):
    """Non-zero case: lda #$01; lxa #$5A gives A = X = ($01 | $EE) & $5A = $4A
    in racer's simulator and in Stella."""
    from tbd.paths import STELLA_CORE as STELLA

    source = REFERENCE.read_text()
    probe = "    CLEAN_START\n    lda #$01\n    .byte $AB, $5A    ; lxa #$5A\n    sta $80\n    stx $81\n"
    assert "    CLEAN_START\n" in source
    report = _racer(tmp_path, source.replace("    CLEAN_START\n", probe, 1),
        'rom = "build/prog.bin"\n[run]\nframes = 3\nskip_frames = 1\nzero = true\n'
        '[[property]]\nkind = "ram"\naddress = 0x80\nvalue = 0x4A\n'
        '[[property]]\nname = "x"\nkind = "ram"\naddress = 0x81\nvalue = 0x4A\n')
    assert report["passed"], report["unexpected"][:1]

    if not STELLA.exists():
        pytest.skip("stella_core not built")
    rom_path = tmp_path / "build" / "prog.bin"
    cmds = [{"cmd": "load_rom", "path": str(rom_path)}, {"cmd": "step_frame"},
            {"cmd": "step_frame"}, {"cmd": "get_ram"}, {"cmd": "quit"}]
    out = subprocess.run([str(STELLA)], input="\n".join(json.dumps(c) for c in cmds) + "\n",
                         capture_output=True, text=True).stdout.splitlines()
    ram = json.loads(out[3])
    values = ram.get("ram") or ram.get("bytes") or ram.get("data")
    assert values[0x00] == 0x4A and values[0x01] == 0x4A, ram


SPAN_CHECK = (
    'rom = "build/prog.bin"\n[run]\nframes = 10\nskip_frames = 1\nzero = true\nrandom_seeds = 5\n'
    '[[property]]\nname = "vsync_span"\nkind = "signal_span"\nsignal = "sync"\n'
    'first_line = 0\nmin_clocks = {lo}\nmax_clocks = {hi}\n'
)


def _span_check(tmp_path, source: str, lo: int, hi: int) -> dict:
    return _racer(tmp_path, source, SPAN_CHECK.format(lo=lo, hi=hi))


# Sync turned on 11 CPU cycles into line 0 and off after three WSYNCs, as in
# Claude Opus 5.5's first Case 0 attempt: 654 clocks instead of 684.
LATE_VSYNC = REFERENCE.read_text().replace(
    "    VERTICAL_SYNC\n",
    "    lda #2\n    sta VSYNC\n    sta WSYNC\n    sta WSYNC\n    sta WSYNC\n    lda #0\n    sta VSYNC\n", 1)


def test_signal_span_accepts_a_late_vsync_within_range(tmp_path):
    report = _span_check(tmp_path, LATE_VSYNC, 456, 912)
    assert report["passed"], report["unexpected"][:1]


def test_signal_span_rejects_out_of_range(tmp_path):
    report = _span_check(tmp_path, LATE_VSYNC, 684, 912)
    assert not report["passed"]
    failure = report["unexpected"][0]["failures"][0]
    assert failure["property"] == "vsync_span"
    assert "lasted" in failure["observed"]


# Sonnet 5.5's shape in case0-first-run: one vertical-blank line short and one
# overscan line short, so 260 lines with the picture on lines 39-230.
SHIFTED = REFERENCE.read_text().replace(
    "    ldx #37\n", "    ldx #36\n", 1).replace("    ldx #29\n", "    ldx #28\n", 1)

RELAXED = (
    'rom = "build/prog.bin"\n[run]\nframes = 10\nskip_frames = 1\nzero = true\nrandom_seeds = 5\n'
    '[[property]]\nkind = "frame_length_range"\nmin_lines = {lo}\nmax_lines = {hi}\n'
    '[[property]]\nkind = "picture_block"\nlines = 192\nstart_window = [{s0}, {s1}]\n'
    'clocks = [68, 228]\ncolor = 0x42\n'
)


def _relaxed(tmp_path, source, lo=260, hi=264, s0=38, s1=43) -> dict:
    return _racer(tmp_path, source, RELAXED.format(lo=lo, hi=hi, s0=s0, s1=s1))


def _failed(report):
    return sorted({f["property"] for r in report["unexpected"] for f in r["failures"]})


def test_relaxed_tier_accepts_the_shifted_frame(tmp_path):
    report = _relaxed(tmp_path, SHIFTED)
    assert report["passed"], report["unexpected"][:1]


def test_relaxed_tier_accepts_the_reference(tmp_path):
    report = _relaxed(tmp_path, REFERENCE.read_text())
    assert report["passed"], report["unexpected"][:1]


def test_frame_length_range_rejects_outside(tmp_path):
    report = _relaxed(tmp_path, SHIFTED, lo=261, hi=264)
    assert _failed(report) == ["frame_length_range"]


def test_picture_block_rejects_start_outside_window(tmp_path):
    report = _relaxed(tmp_path, SHIFTED, s0=40, s1=43)
    assert _failed(report) == ["picture_block"]
    f = report["unexpected"][0]["failures"][0]
    assert f["line"] == 39
