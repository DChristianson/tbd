"""Running a task's checks over one attempt (docs/design/task-spec.md).

A variant's strict check is its `check.py`'s `check(racer)` if it has one,
else its `check.toml` as is; the relaxed tier likewise (`relaxed(racer)`, else
`relaxed.check.toml` if present). Either way the result is a report in
racer-check's `--json` shape, so `grade_program` categorizes them alike.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from tbd.eval.grading import (Grade, assemble_program, categorize, parse_failures,
                              property_names, run_racer_check, with_rom)

if TYPE_CHECKING:
    from tbd.eval.tasks import TaskSpec

_PROPERTY_BLOCK = re.compile(r"(?ms)^\[\[property\]\]\n.*?(?=^\[\[|\Z)")
_COLOR_LINE = re.compile(r"(?m)^color = 0x([0-9a-fA-F]+)$")
_SHOWN_COLOR = re.compile(r"color \$([0-9a-fA-F]{2})")


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
    its result in the attempt folder."""

    def __init__(self, variant_dir: Path, workdir: Path):
        self.variant_dir = Path(variant_dir)
        self.workdir = Path(workdir)
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
        text = with_rom(text)
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


def _summary(grade: Grade) -> dict:
    return {"passed": grade.ok, "failed": grade.failed, "properties": grade.properties,
            "accepted": grade.accepted, "detail": grade.detail}


def grade_report(spec: "TaskSpec", report: dict) -> Grade:
    grade = categorize(report, report.get("properties"), spec.informational)
    grade.accepted = report.get("accepted", {})
    return grade


def grade_program(spec: "TaskSpec", code: str, workdir: Path) -> tuple[dict, Grade]:
    """Assemble one program and run the variant's checks over it. Returns
    (assemble record, grade). The relaxed tier runs when the strict check
    ran the program and failed on timing or output."""
    asm_rec, failed = assemble_program(code, workdir)
    if failed:
        return asm_rec, failed
    racer = Racer(spec.dir, workdir)
    grade = grade_report(spec, spec.check(racer))
    if grade.category in ("timing", "output_mismatch"):
        relaxed = spec.relaxed(racer)
        if relaxed is not None:
            grade.relaxed = _summary(grade_report(spec, relaxed))
    return asm_rec, grade
