"""Grid report: aggregate saved attempt logs into the experiment grid.

    python -m tbd.eval.report experiments/<exp> [experiments/<exp2> ...]
    python -m tbd.eval.report experiments/<exp> --format html --out grid.html

Reads (never writes) each experiment's jobs/{job}/config.json, metrics.json
and attempts.jsonl; a job folder can also be passed directly. Jobs are grouped
into cells keyed by (model, facts, task, variant, mode) plus the run settings
below, across all the experiments given (docs/design/experiment-grid.md,
"Metrics per cell"). Jobs that differ in any setting -- including the model
digest (a re-pulled model) or fact snapshot -- are separate cells, never pooled.

Per cell:
    pass@1        samples whose first attempt passed
    pass@budget   samples that passed within the attempt budget (1 + retries
                  for tool-feedback; the same as pass@1 for one-shot)
    first-attempt categories, near misses (first attempt)
    cost to pass  mean attempts per passing sample, output tokens, wall time,
                  API cost
    settings      digest, fact_snapshot, num_ctx, max_tokens, temperature,
                  retries, effort, think; only the ones that differ between
                  cells are shown as columns, and a grid row gets the ones that
                  tell it apart from the same model's other rows

Flagged (marked † in the grid): a job whose metrics status isn't "completed",
or that has no attempts or unreadable lines. A "running" job is also called
stale when nothing will update it again (stale_reason): its heartbeat
(metrics updated_at, rewritten after every attempt) is older than the request
timeout plus a margin, its pid is gone on this host, or it predates heartbeats.

Aggregation is pure functions over plain dicts (summarize_samples,
aggregate_cell, build_cells, grid_tables, detail_table); load_job does the
reading and render_markdown / render_html the output.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import socket
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

FACT_CONDITIONS = ("none", "contributed", "discovered", "both")
SETTING_KEYS = ("digest", "fact_snapshot", "num_ctx", "max_tokens", "temperature", "retries",
                "effort", "think")
DIGEST_CHARS = 12
FLAG = "\u2020"  # dagger
# A running job rewrites metrics.json after every attempt, and one attempt is at most
# a request (request_timeout_s) plus grading. Older configs have no timeout recorded.
DEFAULT_REQUEST_TIMEOUT_S = 600
HEARTBEAT_MARGIN_S = 300


# ---------------------------------------------------------------- reading

def load_job(job_dir: Path) -> dict:
    """One job folder as a plain dict. Missing files read as empty."""
    def read_json(name):
        p = job_dir / name
        try:
            return json.loads(p.read_text())
        except (OSError, ValueError):
            return {}

    attempts, bad_lines = [], 0
    log = job_dir / "attempts.jsonl"
    if log.exists():
        for line in log.read_text().splitlines():
            if not line.strip():
                continue
            try:
                attempts.append(json.loads(line))
            except ValueError:
                bad_lines += 1   # e.g. a half-written last line of a running job
    return {"experiment": job_dir.parent.parent.name, "job": job_dir.name,
            "config": read_json("config.json"), "metrics": read_json("metrics.json"),
            "attempts": attempts, "bad_lines": bad_lines}


def find_jobs(paths: list[Path]) -> list[Path]:
    """Job folders under each experiment folder (or the path itself if it's a job)."""
    jobs = []
    for p in paths:
        if (p / "config.json").exists() or (p / "attempts.jsonl").exists():
            jobs.append(p)
        else:
            jobs.extend(sorted(d for d in (p / "jobs").glob("*") if d.is_dir()))
    return jobs


# ---------------------------------------------------------------- aggregation

def cell_key(job: dict) -> tuple:
    """(model, facts, task, variant, mode). Older configs lack some keys.

    The grid position; build_cells also splits on job_settings."""
    cfg = job["config"]
    model = (cfg.get("model")
             or next((a.get("requested_model") for a in job["attempts"]
                      if a.get("requested_model")), None)
             or job["job"])
    return (model, cfg.get("facts") or "none", cfg.get("task") or "t1",
            cfg.get("variant") or "canonical", cfg.get("mode") or "one-shot")


def job_settings(cfg: dict) -> dict:
    """The run settings compared across cells, as display strings."""
    think = cfg.get("thinking", cfg.get("think"))
    if isinstance(think, dict):
        think = think.get("type")
    digest = cfg.get("model_digest")
    snapshot = cfg.get("fact_snapshot")
    raw = {"digest": digest[:DIGEST_CHARS] if digest else None,
           "fact_snapshot": str(snapshot)[:DIGEST_CHARS] if snapshot else None,
           "num_ctx": cfg.get("num_ctx"), "max_tokens": cfg.get("max_tokens"),
           "temperature": cfg.get("temperature"), "retries": cfg.get("retries"),
           "effort": cfg.get("effort"), "think": think}
    return {k: "-" if v is None else str(v) for k, v in raw.items()}


def attempt_budget(cfg: dict, attempts: list[dict]) -> int:
    if (cfg.get("mode") or "one-shot") != "tool-feedback":
        return 1
    if cfg.get("retries") is not None:
        return 1 + int(cfg["retries"])
    return max((a.get("attempt_no", 1) for a in attempts), default=1)


def _ok(attempt: dict) -> bool:
    return bool((attempt.get("grade") or {}).get("ok"))


def summarize_samples(attempts: list[dict]) -> list[dict]:
    """One outcome per sample: first-attempt category, passed, attempts to pass, tokens."""
    by_sample: dict = {}
    for a in attempts:
        by_sample.setdefault(a.get("sample", 0), []).append(a)
    out = []
    for sample, rows in sorted(by_sample.items()):
        rows.sort(key=lambda a: a.get("attempt_no", 1))
        first = rows[0]
        grade = first.get("grade") or {}
        passing = [a for a in rows if _ok(a)]
        out.append({
            "sample": sample,
            "first_category": "pass" if _ok(first) else (grade.get("category") or "unknown"),
            "first_passed": _ok(first) and first.get("attempt_no", 1) == 1,
            "near_miss": bool(grade.get("near_miss")),
            "passed": bool(passing),
            "attempts_to_pass": passing[0].get("attempt_no", 1) if passing else None,
            "attempts": len(rows),
            "output_tokens": sum((a.get("tokens") or {}).get("output") or 0 for a in rows),
            "wall_ms": sum(a.get("wall_ms") or 0 for a in rows),
            "cost_usd": (sum(a["cost_usd"] for a in rows if a.get("cost_usd") is not None)
                         if any(a.get("cost_usd") is not None for a in rows) else None),
        })
    return out


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except PermissionError:   # exists, owned by someone else
        return True
    except (OSError, OverflowError, ValueError, TypeError):
        return False
    return True


def _age(seconds: float) -> str:
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}h{s % 3600 // 60:02d}m"
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def stale_reason(metrics: dict, config: dict, now: datetime, host: str | None = None,
                 alive=None) -> str | None:
    """Why a "running" job can't still be running, or None if it may be.

    Pure apart from alive(pid), which is asked only about a pid recorded on
    `host` (this machine); without host and alive the pid isn't checked.
    A job whose status isn't "running" is never stale."""
    if metrics.get("status") != "running":
        return None
    updated, pid = metrics.get("updated_at"), metrics.get("pid")
    if not updated and pid is None:
        return "no heartbeat"
    if updated:
        try:
            then = datetime.fromisoformat(updated)
        except (TypeError, ValueError):
            return f"unreadable heartbeat {updated!r}"
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        age = (now - then).total_seconds()
        limit = (config.get("request_timeout_s") or DEFAULT_REQUEST_TIMEOUT_S) + HEARTBEAT_MARGIN_S
        if age > limit:
            return f"last heartbeat {_age(age)} ago"
    if (pid is not None and alive is not None and host is not None
            and metrics.get("host") == host and not alive(pid)):
        return f"pid {pid} gone"
    return None


def job_flags(job: dict, now: datetime | None = None, host: str | None = None,
              alive=pid_alive) -> list[str]:
    """Problems with one job; now and host default to the current time and machine."""
    name = f"{job['experiment']}/{job['job']}"
    flags = []
    status = job["metrics"].get("status")
    if status != "completed":
        stale = stale_reason(job["metrics"], job["config"],
                             now or datetime.now(timezone.utc),
                             host or socket.gethostname(), alive)
        if stale == "no heartbeat":
            flags.append(f"{name}: status running (no heartbeat; stale)")
        elif stale:
            flags.append(f"{name}: status running (stale: {stale})")
        else:
            flags.append(f"{name}: status {status or 'unknown'}")
    if not job["attempts"]:
        flags.append(f"{name}: no attempts")
    if job["bad_lines"]:
        flags.append(f"{name}: {job['bad_lines']} unreadable attempt line(s)")
    return flags


def aggregate_cell(key: tuple, jobs: list[dict]) -> dict:
    """All the per-cell metrics for jobs sharing one cell key and the same settings."""
    samples, budgets, planned = [], set(), 0
    settings = job_settings(jobs[0]["config"])
    flags = []
    for job in jobs:
        samples += summarize_samples(job["attempts"])
        budgets.add(attempt_budget(job["config"], job["attempts"]))
        planned += job["config"].get("samples") or job["metrics"].get("samples_planned") or 0
        assert job_settings(job["config"]) == settings, "build_cells splits on settings"
        flags += job_flags(job)
    passes = [s for s in samples if s["passed"]]
    costs = [s["cost_usd"] for s in samples if s["cost_usd"] is not None]
    model, facts, task, variant, mode = key
    return {
        "key": key, "model": model, "facts": facts, "task": task, "variant": variant,
        "mode": mode,
        "jobs": [f"{j['experiment']}/{j['job']}" for j in jobs],
        "samples": len(samples), "samples_planned": planned,
        "budget": max(budgets) if budgets else 1, "budgets": sorted(budgets),
        "pass_at_1": sum(s["first_passed"] for s in samples),
        "pass_at_budget": len(passes),
        "first_categories": dict(Counter(s["first_category"] for s in samples)
                                 .most_common()),
        "near_misses": sum(s["near_miss"] for s in samples),
        "mean_attempts_to_pass": (sum(s["attempts_to_pass"] for s in passes) / len(passes)
                                  if passes else None),
        "attempts_total": sum(s["attempts"] for s in samples),
        "output_tokens": sum(s["output_tokens"] for s in samples),
        "wall_ms": sum(s["wall_ms"] for s in samples),
        "cost_usd": sum(costs) if costs else None,
        "settings": settings,
        "flags": flags,
    }


def build_cells(jobs: list[dict]) -> list[dict]:
    grouped: dict[tuple, list] = {}
    for job in jobs:
        settings = tuple(job_settings(job["config"]).items())
        grouped.setdefault((cell_key(job), settings), []).append(job)
    return [aggregate_cell(key, v) for (key, _), v in sorted(grouped.items())]


def _settings_id(cell: dict) -> tuple:
    return tuple(cell["settings"][k] for k in SETTING_KEYS)


# ---------------------------------------------------------------- tables
# A table is {"title", "headers", "rows", "notes"}; renderers turn it into text.

def _frac(n: int, d: int) -> str:
    return f"{n}/{d}" if d else "-"


def grid_value(cell: dict) -> str:
    s = cell["samples"]
    if cell["mode"] == "one-shot" and cell["budget"] == 1:
        text = _frac(cell["pass_at_1"], s)
    else:
        text = f"{_frac(cell['pass_at_1'], s)} \u2192 {_frac(cell['pass_at_budget'], s)} @{cell['budget']}"
    return text + (FLAG if cell["flags"] else "")


def fact_columns(cells: list[dict]) -> list[str]:
    extra = sorted({c["facts"] for c in cells} - set(FACT_CONDITIONS))
    return list(FACT_CONDITIONS) + extra


def grid_tables(cells: list[dict]) -> list[dict]:
    """One table per task x variant x mode: rows = models, columns = fact conditions."""
    facts = fact_columns(cells)
    sections: dict[tuple, list] = {}
    for c in cells:
        sections.setdefault((c["task"], c["variant"], c["mode"]), []).append(c)
    tables = []
    for (task, variant, mode), group in sorted(sections.items()):
        # One row per model and settings; a model run with two settings gets two rows,
        # labelled with the settings that tell them apart.
        row_ids = sorted({(c["model"], _settings_id(c)) for c in group})
        lookup = {(c["model"], _settings_id(c), c["facts"]): c for c in group}
        rows = [[row_label(model, sid, row_ids)]
                + [grid_value(lookup[(model, sid, f)]) if (model, sid, f) in lookup
                   else "\u2014" for f in facts]
                for model, sid in row_ids]
        how = "pass@1" if mode == "one-shot" else "pass@1 \u2192 pass@budget"
        tables.append({"title": f"{task} / {variant} / {mode}",
                       "headers": ["model"] + [f"facts: {f}" for f in facts],
                       "rows": rows, "notes": [f"Cells: {how}, as passing/samples."]})
    return tables


def row_label(model: str, sid: tuple, row_ids: list[tuple]) -> str:
    """The model, plus the settings that differ from its other rows in the same table."""
    others = [s for m, s in row_ids if m == model]
    diff = [f"{k}={v}" for i, (k, v) in enumerate(zip(SETTING_KEYS, sid))
            if len({s[i] for s in others}) > 1]
    return f"{model} ({', '.join(diff)})" if diff else model


def varying_settings(cells: list[dict]) -> list[str]:
    """Settings whose values aren't the same for every cell."""
    return [k for k in SETTING_KEYS if len({c["settings"][k] for c in cells}) > 1]


def _wall(ms: int) -> str:
    s = round(ms / 1000)
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "-"


def detail_table(cells: list[dict]) -> dict:
    varying = varying_settings(cells)
    headers = (["model", "facts", "task", "variant", "mode", "samples", "pass@1",
                "pass@budget", "first-attempt categories", "near miss", "attempts/pass",
                "output tokens", "wall", "cost"] + varying + ["flags"])
    rows = []
    for c in cells:
        n = c["samples"]
        cats = ", ".join(f"{k} {v / n:.0%}" for k, v in c["first_categories"].items()) if n else "-"
        samples = str(n) if n == c["samples_planned"] else f"{n} of {c['samples_planned']}"
        rows.append([
            c["model"], c["facts"], c["task"], c["variant"], c["mode"], samples,
            _pct(c["pass_at_1"], n),
            (_pct(c["pass_at_budget"], n) + f" @{'/'.join(map(str, c['budgets']))}"
             if n else "-"),
            cats, str(c["near_misses"]),
            "-" if c["mean_attempts_to_pass"] is None else f"{c['mean_attempts_to_pass']:.2f}",
            f"{c['output_tokens']:,}", _wall(c["wall_ms"]),
            "-" if c["cost_usd"] is None else f"${c['cost_usd']:.4f}",
        ] + [c["settings"][k] for k in varying]
          + ["; ".join(c["flags"]) or ""])
    constant = [f"{k}={cells[0]['settings'][k]}" for k in SETTING_KEYS
                if k not in varying and cells]
    notes = ["first-attempt categories and near misses are over first attempts; "
             "attempts/pass is the mean attempt number of the first pass; "
             "tokens, wall and cost are totals over all attempts."]
    if constant:
        notes.append("Same for every cell: " + ", ".join(constant) + ".")
    return {"title": "Cell detail", "headers": headers, "rows": rows, "notes": notes}


def report_tables(cells: list[dict]) -> list[dict]:
    return grid_tables(cells) + [detail_table(cells)] if cells else []


# ---------------------------------------------------------------- rendering

def _md_cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(tables: list[dict], sources: list[str]) -> str:
    out = ["# Grid report", "", "Experiments: " + ", ".join(sources), ""]
    if not tables:
        return "\n".join(out + ["No jobs found.", ""])
    for t in tables:
        out += [f"## {t['title']}", "",
                "| " + " | ".join(map(_md_cell, t["headers"])) + " |",
                "|" + "---|" * len(t["headers"])]
        out += ["| " + " | ".join(map(_md_cell, r)) + " |" for r in t["rows"]]
        out += [""] + t["notes"] + [""]
    out += [f"{FLAG} flagged: incomplete job; see the flags column.", ""]
    return "\n".join(out)


def render_html(tables: list[dict], sources: list[str]) -> str:
    e = html.escape
    out = ["<!doctype html><meta charset='utf-8'><title>Grid report</title>",
           "<style>body{font-family:system-ui,sans-serif;margin:1.5rem}"
           "table{border-collapse:collapse;margin-bottom:.5rem}"
           "th,td{border:1px solid #bbb;padding:.25rem .5rem;text-align:left;"
           "vertical-align:top}th{background:#eee}p.note{color:#555;font-size:.9em}</style>",
           "<h1>Grid report</h1>", f"<p>Experiments: {e(', '.join(sources))}</p>"]
    if not tables:
        out.append("<p>No jobs found.</p>")
    for t in tables:
        out.append(f"<h2>{e(t['title'])}</h2><table><tr>"
                   + "".join(f"<th>{e(h)}</th>" for h in t["headers"]) + "</tr>")
        out += ["<tr>" + "".join(f"<td>{e(str(v))}</td>" for v in r) + "</tr>"
                for r in t["rows"]]
        out.append("</table>")
        out += [f"<p class='note'>{e(n)}</p>" for n in t["notes"]]
    out.append(f"<p class='note'>{FLAG} flagged: incomplete job; see the flags column.</p>")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("experiments", nargs="+", type=Path,
                   help="experiment folders (experiments/<exp>) or job folders")
    p.add_argument("--format", choices=("md", "html"), default="md")
    p.add_argument("--out", type=Path, default=None, help="write here instead of stdout")
    args = p.parse_args(argv)
    for d in args.experiments:
        if not d.is_dir():
            p.error(f"{d} is not a folder")
    jobs = [load_job(j) for j in find_jobs(args.experiments)]
    tables = report_tables(build_cells(jobs))
    sources = [d.name for d in args.experiments]
    text = (render_html if args.format == "html" else render_markdown)(tables, sources)
    if args.out:
        args.out.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
