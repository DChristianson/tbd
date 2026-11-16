"""Task-agnostic grading: find the program in a reply, assemble it, run a
racer-check check file over it, and turn racer-check's report into a Grade.

What counts as passing is the task's business: its check files and check.py
(tbd.eval.tasks, tbd.eval.checks).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from tbd.paths import HEADERS, RACER_CHECK
from tbd.tools.assembler import assemble

CATEGORIES = ("pass", "timeout", "truncated", "refusal", "no_code", "assemble_error",
              "unsupported_opcode", "crash", "timing", "output_mismatch")
TIMING_PROPS = {"frame_length", "vsync", "vsync_span"}
HEADER_FILES = ("vcs.h", "macro.h")
# Where the assembled program goes in an attempt folder; check files are
# pointed at it (racer-check resolves `rom` against the check file's folder).
PROG_ROM = "build/prog.bin"


# ---------------------------------------------------------------- extraction

_FENCE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
_ASM_TAGS = {"asm", "assembly", "dasm", "6502", "s", "nasm", "ca65"}


def extract_code(response: str) -> str | None:
    """The program in the reply: the longest fenced block tagged as assembly,
    else the longest fenced block of any kind, else None. Whether it is valid
    6502 is left to DASM."""
    response = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL)
    blocks = [(tag.strip().lower(), body) for tag, body in _FENCE.findall(response)]
    tagged = [b for t, b in blocks if t in _ASM_TAGS]
    pool = tagged or [b for _, b in blocks]
    return max(pool, key=len) if pool else None


# ---------------------------------------------------------------- grades

@dataclass
class Grade:
    ok: bool
    category: str
    detail: str = ""
    failed: list[str] = field(default_factory=list)
    # {property: color} where an accepted color other than the check file's
    # was substituted (checks.Racer.run's `accept`).
    accepted: dict[str, int] = field(default_factory=dict)
    # Every property in the check file -> "pass" / "fail", once the program ran.
    properties: dict[str, str] = field(default_factory=dict)
    # Failed properties that don't count toward the grade (informational).
    informational_failed: list[str] = field(default_factory=list)
    # The relaxed tier's result, when the strict check failed after running:
    # {"passed", "failed", "properties", "accepted", "detail"}.
    relaxed: dict | None = None

    @property
    def near_miss(self) -> bool:
        """Failed the strict check, passed the relaxed tier."""
        return not self.ok and bool(self.relaxed and self.relaxed["passed"])

    def to_dict(self) -> dict:
        return {"ok": self.ok, "category": self.category, "detail": self.detail,
                "failed": self.failed, "informational_failed": self.informational_failed,
                "accepted": self.accepted, "properties": self.properties,
                "near_miss": self.near_miss, "relaxed": self.relaxed}


def parse_failures(report: dict) -> dict[str, dict]:
    """{property name: its first failure}, over every unexpected run."""
    out: dict[str, dict] = {}
    for run in report.get("unexpected", []):
        for f in run.get("failures", []):
            out.setdefault(f["property"], f)
    return out


def describe(f: dict) -> str:
    where = ", ".join(f"{k} {f[k]}" for k in ("frame", "line", "clock") if k in f)
    return f"{f['property']}: {where}: {f.get('observed')}; required {f.get('required')}"


_PROPERTY_BLOCK = re.compile(r"(?ms)^\[\[property\]\]\n(.*?)(?=^\[\[|\Z)")


def property_names(check_toml: str) -> list[str]:
    """Each property's name (its `name`, else its `kind`), in file order."""
    names = []
    for block in _PROPERTY_BLOCK.findall(check_toml):
        m = re.search(r'(?m)^name\s*=\s*"([^"]+)"', block) or \
            re.search(r'(?m)^kind\s*=\s*"([^"]+)"', block)
        if m:
            names.append(m.group(1))
    return names


def categorize(report: dict, names: list[str] | None = None,
               informational: frozenset[str] | set[str] = frozenset()) -> Grade:
    """racer-check's report as a Grade. Properties in `informational` are
    checked and recorded but don't count toward the grade."""
    grade = _categorize(report)
    if grade.category not in ("pass", "timing", "output_mismatch"):
        return grade
    if names:
        grade.properties = {n: ("fail" if n in grade.failed else "pass") for n in names}
    info = [n for n in grade.failed if n in informational]
    if info:
        grade.informational_failed = info
        grade.failed = [n for n in grade.failed if n not in informational]
        details = [d for d in grade.detail.split("; ") if not any(d.startswith(f"{n}:") for n in info)]
        if not grade.failed:
            grade.ok, grade.category = True, "pass"
            grade.detail = "passed; informational: " + "; ".join(
                d for d in grade.detail.split("; ") if any(d.startswith(f"{n}:") for n in info))
        else:
            grade.category = "timing" if TIMING_PROPS & set(grade.failed) else "output_mismatch"
            grade.detail = "; ".join(details)
    return grade


def _categorize(report: dict) -> Grade:
    if report.get("passed"):
        return Grade(True, "pass")
    errors = [r["error"] for r in report.get("unexpected", []) if r.get("error")]
    if report.get("error"):
        errors.insert(0, report["error"])
    if errors:
        # Undocumented opcodes are allowed (tbd-dpu.10). racer's simulator runs
        # LXA (tbd-818.11); any other one it can't run is a grader gap, not a
        # model failure.
        category = "unsupported_opcode" if "UnknownOpcode" in errors[0] else "crash"
        return Grade(False, category, detail=errors[0])
    failures = parse_failures(report)
    names = sorted(failures)
    category = "timing" if TIMING_PROPS & set(names) else "output_mismatch"
    return Grade(False, category, detail="; ".join(describe(failures[n]) for n in names),
                 failed=names)


# ---------------------------------------------------------------- running

def assemble_program(code: str, workdir: Path) -> tuple[dict, Grade | None]:
    """Write prog.asm (with the standard headers beside it) into workdir,
    assemble it, and save assemble.json and build/prog.bin. Returns (assemble
    record, an assemble_error Grade or None if it assembled)."""
    workdir.mkdir(parents=True, exist_ok=True)
    for h in HEADER_FILES:
        shutil.copy(HEADERS / h, workdir / h)
    src = workdir / "prog.asm"
    src.write_text(code)
    asm = assemble(src)
    # Errors name the file by absolute path; the model only needs "prog.asm".
    strip = [str(src.resolve()), str(src)]
    def short(msgs):
        out = []
        for m in msgs[:20]:
            for prefix in strip:
                m = m.replace(prefix, "prog.asm")
            out.append(m)
        return out
    asm_rec = {"ok": asm.ok, "errors": short(asm.errors), "warnings": short(asm.warnings)}
    (workdir / "assemble.json").write_text(json.dumps(
        {**asm_rec, "rom_bytes": len(asm.rom) if asm.rom else 0,
         "errors_raw": asm.errors, "warnings_raw": asm.warnings}, indent=2))
    if not asm.ok:
        return asm_rec, Grade(False, "assemble_error", detail="; ".join(asm_rec["errors"][:5]))
    (workdir / PROG_ROM).parent.mkdir(exist_ok=True)
    (workdir / PROG_ROM).write_bytes(asm.rom)
    return asm_rec, None


def with_rom(check_toml: str, rom: str = PROG_ROM) -> str:
    """The check file with its `rom` line pointed at `rom`."""
    return re.sub(r'(?m)^rom = "[^"]*"$', f'rom = "{rom}"', check_toml)


def run_racer_check(workdir: Path, check_toml: str, name: str) -> dict:
    """Save check_toml as {name}.toml in workdir, run racer-check on it there,
    save {name}.result.json, and return racer-check's report."""
    (workdir / f"{name}.toml").write_text(check_toml)
    proc = subprocess.run([str(RACER_CHECK), "--json", f"{name}.toml"], cwd=workdir,
                          capture_output=True, text=True, timeout=300)
    raw = {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    if proc.returncode == 2 or not proc.stdout.strip():
        report = {"passed": False, "error": (proc.stderr or proc.stdout).strip()}
    else:
        report = json.loads(proc.stdout.splitlines()[0])
    (workdir / f"{name}.result.json").write_text(json.dumps({"report": report, "raw": raw}, indent=2))
    return report
