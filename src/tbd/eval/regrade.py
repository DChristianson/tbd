"""Re-grade saved attempts without asking the model again.

    python -m tbd.eval.regrade experiments/<exp>/jobs/<job>/sample-00/attempt-1 [...]

Reads the saved reply (response.md) and runs the current grader over it:
extract the code, assemble, racer-check, categorize. Nothing in the attempt
folder is changed. Each re-grade goes in its own new folder,

    <attempt>/regrade-<UTC time>/
        regrade.json      original grade, new grade, grader commit (and whether
                          the tree had uncommitted changes)
        prog.asm, assemble.json, <check>.toml, <check>.result.json

so the original record stays as it was and every re-grade is kept. The task
variant comes from the job's config.json (task, variant); jobs from before
variants existed are t1/canonical.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tbd.eval import tasks
from tbd.eval.checks import grade_program
from tbd.eval.grading import Grade, extract_code
from tbd.paths import ROOT


def grader_version() -> dict:
    def git(*a):
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return {"git_sha": git("rev-parse", "HEAD"),
            "dirty": bool(git("status", "--porcelain", "--", "src", "tasks", "vendor"))}


def job_spec(attempt_dir: Path) -> tasks.TaskSpec:
    """The task variant of the job holding attempt_dir (job/sample-NN/attempt-N)."""
    config = attempt_dir.parent.parent / "config.json"
    cfg = json.loads(config.read_text()) if config.exists() else {}
    return tasks.load(cfg.get("task", "t1"), cfg.get("variant", "canonical"))


def regrade(attempt_dir: Path) -> dict:
    attempt_dir = attempt_dir.resolve()
    original = json.loads((attempt_dir / "attempt.json").read_text())
    if original["grade"]["category"] in ("timeout",):
        # A cut-off reply is graded as a timeout; re-grading its partial text
        # would be grading something the model never finished.
        new = Grade(False, "timeout", detail="not re-graded: request timed out")
        out_dir, asm_rec, code = None, None, None
    else:
        text = (attempt_dir / "response.md").read_text()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = attempt_dir / f"regrade-{stamp}"
        out_dir.mkdir()
        code = extract_code(text)
        if code is None:
            new, asm_rec = Grade(False, "no_code"), None
        else:
            asm_rec, new = grade_program(job_spec(attempt_dir), code, out_dir)
    record = {
        "attempt": str(attempt_dir.relative_to(ROOT)),
        "regraded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "grader": grader_version(),
        "original_grade": original["grade"],
        "grade": new.to_dict(),
        "assemble": asm_rec,
        "code_found": code is not None,
    }
    if out_dir:
        (out_dir / "regrade.json").write_text(json.dumps(record, indent=2))
    return record


def main(argv: list[str] | None = None) -> int:
    dirs = [Path(a) for a in (argv if argv is not None else sys.argv[1:])]
    if not dirs:
        print(__doc__)
        return 2
    for d in dirs:
        r = regrade(d)
        print(f"{r['attempt']}: {r['original_grade']['category']} -> {r['grade']['category']}"
              + (f" ({r['grade']['detail'][:160]})" if r["grade"]["detail"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
