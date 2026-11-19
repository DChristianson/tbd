"""Running a task's checks over one attempt (docs/design/task-spec.md).

A variant's strict check is its `check.py`'s `check(racer)` if it has one,
else its `check.toml` as is; the relaxed tier likewise (`relaxed(racer)`, else
`relaxed.check.toml` if present). Either way the result is a report in
racer-check's `--json` shape, so `grade_program` categorizes them alike.
"""

from __future__ import annotations

import gzip
import json
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from tbd.eval.grading import (Grade, assemble_program, categorize, parse_failures,
                              property_names, run_racer_check, with_rom)
from tbd.paths import RACER_CHECK

if TYPE_CHECKING:
    from tbd.eval.tasks import TaskSpec

_PROPERTY_BLOCK = re.compile(r"(?ms)^\[\[property\]\]\n.*?(?=^\[\[|\Z)")
_COLOR_LINE = re.compile(r"(?m)^color = 0x([0-9a-fA-F]+)$")
_SHOWN_COLOR = re.compile(r"color \$([0-9a-fA-F]{2})")
_RANDOM_SEEDS = re.compile(r"(?m)^random_seeds\s*=\s*\d+\s*$")


def with_random_seeds(check_toml: str, seeds: int | None) -> str:
    """The check file with its `random_seeds` set to `seeds` (None: as is).
    Grading time grows with the seed count: 100 seeds take ~8 s per check."""
    if seeds is None:
        return check_toml
    if _RANDOM_SEEDS.search(check_toml):
        return _RANDOM_SEEDS.sub(f"random_seeds = {seeds}", check_toml)
    return re.sub(r"(?m)^\[run\]\s*$", f"[run]\nrandom_seeds = {seeds}", check_toml, count=1)


def _block_name(block: str) -> str | None:
    m = re.search(r'(?m)^name\s*=\s*"([^"]+)"', block) or \
        re.search(r'(?m)^kind\s*=\s*"([^"]+)"', block)
    return m.group(1) if m else None


def _property_block(check_toml: str, prop: str) -> re.Match | None:
    for m in _PROPERTY_BLOCK.finditer(check_toml):
        if _block_name(m.group(0)) == prop:
            return m
    return None


def property_color(check_toml: str, prop: str) -> int | None:
    """The `color` of the property named `prop`, if it has one."""
    block = _property_block(check_toml, prop)
    m = _COLOR_LINE.search(block.group(0)) if block else None
    return int(m.group(1), 16) if m else None


def set_property_color(check_toml: str, prop: str, color: int) -> str:
    """The check file with `color` changed in `prop`'s block only."""
    block = _property_block(check_toml, prop)
    if block is None or not _COLOR_LINE.search(block.group(0)):
        raise KeyError(f"no property {prop!r} with a color")
    new = _COLOR_LINE.sub(f"color = 0x{color:02x}", block.group(0), count=1)
    return check_toml[:block.start()] + new + check_toml[block.end():]


def accepted_substitutions(report: dict, check_toml: str,
                           accept: dict[str, list[int]]) -> dict[str, int]:
    """{property: color} for each accepted property that failed showing
    another of its accepted colors. Independent of whatever else failed."""
    failures = parse_failures(report)
    out = {}
    for prop, colors in accept.items():
        f = failures.get(prop)
        m = _SHOWN_COLOR.search(f.get("observed") or "") if f else None
        if not m:
            continue
        shown = int(m.group(1), 16)
        if shown in colors and shown != property_color(check_toml, prop):
            out[prop] = shown
    return out


class Racer:
    """Runs check files over one attempt's ROM, saving each check file and
    its result in the attempt folder. With `random_seeds`, every check file
    runs that many random power-on states instead of its own count."""

    def __init__(self, variant_dir: Path, workdir: Path, random_seeds: int | None = None):
        self.variant_dir = Path(variant_dir)
        self.workdir = Path(workdir)
        self.random_seeds = random_seeds
        self.ran: list[str] = []  # check files run, by name in the variant folder

    def _save_name(self, stem: str) -> str:
        """A name not yet used in the attempt folder, for the saved files."""
        name, n = stem, 1
        while (self.workdir / f"{name}.toml").exists():
            n += 1
            name = f"{stem}-{n}"
        return name

    def run(self, check: str, accept: dict[str, list[int]] | None = None) -> dict:
        """racer-check's report for `check`: a check file in the variant
        folder, or check-file text. With `accept`, a property that fails
        showing one of its accepted colors is re-checked at that color (all
        such properties in one re-run); the re-run's report is kept when
        every substituted property then passes, and its `accepted` key says
        which colors were substituted.

        The report also gets `properties`: every property name in the file."""
        if "\n" in check:
            text, stem = check, "check"
        else:
            text, stem = (self.variant_dir / check).read_text(), check.removesuffix(".toml")
            self.ran.append(check)
        text = with_random_seeds(with_rom(text), self.random_seeds)
        for prop in accept or {}:
            if property_color(text, prop) is None:
                raise KeyError(f"{stem}: accept names {prop!r}, not a property with a color")
        report = run_racer_check(self.workdir, text, self._save_name(stem))
        report["properties"] = property_names(text)
        report["accepted"] = {}
        subs = accepted_substitutions(report, text, accept or {})
        if subs:
            retry_text = text
            for prop, color in subs.items():
                retry_text = set_property_color(retry_text, prop, color)
            suffix = "-".join(f"{p}-0x{c:02x}" for p, c in subs.items())
            retry = run_racer_check(self.workdir, retry_text, self._save_name(f"{stem}-{suffix}"))
            if not set(subs) & set(parse_failures(retry)):
                retry["properties"] = report["properties"]
                retry["accepted"] = subs
                report = retry
        return report


    def frames(self, frames: int = 100, seeds: int = 2, skip_frames: int = 1) -> list[dict]:
        """The attempt's output, simulated as a check file would run it: the
        zero power-on state and random `seeds`, each a dict with `power_on`,
        `frame_lengths`, `frames` (after `skip_frames`: `{number, lines}`,
        each line runs `[start, length, signal]`, signal "sync", "blank" or
        a color) and `error` if the simulator stopped. Saved, gzipped, as
        frames.jsonl.gz (racer-check --frames)."""
        name = self._save_name("frames")
        toml = with_rom(f'rom = ""\n[run]\nframes = {frames}\nskip_frames = {skip_frames}\n'
                        f'zero = true\nrandom_seeds = {seeds}\n')
        (self.workdir / f"{name}.toml").write_text(toml)
        proc = subprocess.run([str(RACER_CHECK), "--frames", f"{name}.toml"], cwd=self.workdir,
                              capture_output=True, text=True, timeout=300)
        with gzip.open(self.workdir / f"{name}.jsonl.gz", "wt") as f:
            f.write(proc.stdout)
        runs = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        if proc.returncode != 0 or not runs:
            raise RuntimeError(f"racer-check --frames failed: {(proc.stderr or proc.stdout)[:500]}")
        return runs


def failure(prop: str, frame: int | None, observed: str, required: str,
            line: int | None = None, clock: int | None = None) -> dict:
    """One failure, in racer-check's --json shape."""
    f = {"property": prop, "observed": observed, "required": required}
    for k, v in (("frame", frame), ("line", line), ("clock", clock)):
        if v is not None:
            f[k] = v
    return f


def report(runs: dict[str, list[dict]], properties: list[str],
           errors: dict[str, str] | None = None) -> dict:
    """A report in racer-check's --json shape from Python checks: `runs` maps
    each power-on label to its failures (first failure per property)."""
    errors = errors or {}
    unexpected = []
    for label, failures in runs.items():
        if failures or label in errors:
            r = {"power_on": label, "failures": failures}
            if label in errors:
                r["error"] = errors[label]
            unexpected.append(r)
    return {"passed": not unexpected, "runs": len(runs), "unexpected": unexpected,
            "properties": list(properties), "accepted": {}}


def merge(*reports: dict) -> dict:
    """Several reports over the same program as one: passed if all passed,
    unexpected runs joined by power-on label, properties and accepted
    colors combined."""
    out = {"passed": all(r.get("passed") for r in reports), "runs": max(r.get("runs", 0) for r in reports),
           "unexpected": [], "properties": [], "accepted": {}}
    by_label: dict[str, dict] = {}
    for r in reports:
        if r.get("error"):
            out.setdefault("error", r["error"])
        for run in r.get("unexpected", []):
            dst = by_label.setdefault(run["power_on"], {"power_on": run["power_on"], "failures": []})
            dst["failures"] += run.get("failures", [])
            if run.get("error"):
                dst.setdefault("error", run["error"])
        out["properties"] += [p for p in r.get("properties", []) if p not in out["properties"]]
        out["accepted"].update(r.get("accepted", {}))
    out["unexpected"] = list(by_label.values())
    return out


def _summary(grade: Grade) -> dict:
    return {"passed": grade.ok, "failed": grade.failed, "properties": grade.properties,
            "accepted": grade.accepted, "detail": grade.detail}


def grade_report(spec: "TaskSpec", report: dict) -> Grade:
    grade = categorize(report, report.get("properties"), spec.informational)
    grade.accepted = report.get("accepted", {})
    return grade


def grade_program(spec: "TaskSpec", code: str, workdir: Path,
                  random_seeds: int | None = None) -> tuple[dict, Grade]:
    """Assemble one program and run the variant's checks over it. Returns
    (assemble record, grade). The relaxed tier runs when the strict check
    ran the program and failed on timing or output. `random_seeds` overrides
    the check files' count (Racer)."""
    asm_rec, failed = assemble_program(code, workdir)
    if failed:
        return asm_rec, failed
    racer = Racer(spec.dir, workdir, random_seeds)
    grade = grade_report(spec, spec.check(racer))
    if grade.category in ("timing", "output_mismatch"):
        relaxed = spec.relaxed(racer)
        if relaxed is not None:
            grade.relaxed = _summary(grade_report(spec, relaxed))
    return asm_rec, grade
