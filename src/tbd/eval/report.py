"""Grid report: aggregate saved attempt logs into the experiment grid.

    python -m tbd.eval.report experiments/<exp> [experiments/<exp2> ...]
    python -m tbd.eval.report experiments/<exp> --html grid.html

Reads (never writes) each experiment's jobs/{job}/config.json, metrics.json,
attempts.jsonl and each attempt's sample-NN/attempt-N/response.json; a job folder can also be passed directly. Jobs are grouped
into cells keyed by (model, facts, task, variant, mode) plus the run settings
below, across all the experiments given (docs/design/experiment-grid.md,
"Metrics per cell"). Jobs that differ in any setting -- including the model
digest (a re-pulled model), fact snapshot or toolchain -- are separate cells, never pooled.

Per cell:
    pass@1        samples whose first attempt passed
    pass@budget   samples that passed within the attempt budget (1 + retries
                  for tool-feedback; the same as pass@1 for one-shot)
    first-attempt categories, near misses (first attempt)
    assembled     attempts DASM took as the model wrote them; with the syntax
                  fixer on (fix_syntax), also the attempts it took after the
                  fix, which are the ones that reached the checks
    cost to pass  mean attempts per passing sample, output tokens, wall time,
                  API cost
    timing        tok/s (output tokens over Ollama's eval_duration, both summed
                  over attempts), queue (RunPod's delay before a worker took the
                  job) and outside generation (wall time less queue less
                  Ollama's total_duration: model load, a stalled worker,
                  streaming), from each attempt's response.json
    settings      digest, fact_snapshot, num_ctx, max_tokens, temperature,
                  retries, effort, think, fix_syntax, and the toolchain that graded: dasm
                  (version), racer_check (sha256 of the binary, not its path,
                  which differs between Mac and container), image (container
                  digest); "-" where a config doesn't record it. Only the ones
                  that differ between cells are shown as columns, and a grid
                  row gets the ones that tell it apart from the same model's
                  other rows

For runs served by RunPod, a worker table gives the same timing per worker
(response.json runpod.worker_id) over all the cells, so a slow or stalled
worker stands out.

Flagged (marked † in the grid): a job whose metrics status isn't "completed",
or that has no attempts or unreadable lines. A "running" job is also called
stale when nothing will update it again (stale_reason): its heartbeat
(metrics updated_at, rewritten after every attempt) is older than the request
timeout plus a margin, its pid is gone on this host, or it predates heartbeats.

--html PATH (or --format html) writes one self-contained page (report_html):
the same tables, each grid cell linking to its samples and attempts -- grade,
DASM errors, racer-check findings, prompt, reply, thinking and prog.asm. It
also reads each attempt folder's prog.asm, assemble.json, *.result.json and
error.txt, never stream.jsonl or frames.jsonl.gz; long thinking is truncated.

Aggregation is pure functions over plain dicts (summarize_samples,
aggregate_cell, build_cells, grid_tables, detail_table, worker_table); load_job does the
reading and render_markdown / render_html the output.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

FACT_CONDITIONS = ("none", "contributed", "discovered", "both")
SETTING_KEYS = ("digest", "fact_snapshot", "num_ctx", "max_tokens", "temperature", "retries",
                "effort", "think", "fix_syntax", "dasm", "racer_check", "image")
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
    for a in attempts:
        folder = job_dir / f"sample-{a.get('sample', 0):02d}" / f"attempt-{a.get('attempt_no', 1)}"
        try:
            response = json.loads((folder / "response.json").read_text())
        except (OSError, ValueError):
            response = {}   # a timed-out request saves none
        a["timing"] = attempt_timing(a, response if isinstance(response, dict) else {})
    return {"experiment": job_dir.parent.parent.name, "job": job_dir.name, "dir": job_dir,
            "config": read_json("config.json"), "metrics": read_json("metrics.json"),
            "attempts": attempts, "bad_lines": bad_lines}


def attempt_timing(attempt: dict, response: dict) -> dict:
    """The timing of one attempt, in ms, from its record and its saved response.

    eval_ms and total_ms are Ollama's eval_duration and total_duration (None
    when the request timed out, or for a provider that doesn't report them).
    worker_id and delay_ms are RunPod's (None when RunPod didn't serve it)."""
    def ms(ns):
        return ns / 1e6 if isinstance(ns, (int, float)) else None
    runpod = response.get("runpod") or {}
    return {"output_tokens": (attempt.get("tokens") or {}).get("output") or 0,
            "wall_ms": attempt.get("wall_ms"),
            "eval_ms": ms(response.get("eval_duration")),
            "total_ms": ms(response.get("total_duration")),
            "worker_id": runpod.get("worker_id"),
            "delay_ms": runpod.get("delay_ms")}


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
           "effort": cfg.get("effort"), "think": think, "host": cfg.get("model_host"),
           # Configs from before the fixer existed ran without it.
           "fix_syntax": "on" if cfg.get("fix_syntax") else "off",
           **toolchain_settings(cfg)}
    return {k: "-" if v is None else str(v) for k, v in raw.items()}


def _short(digest) -> str | None:
    """First DIGEST_CHARS hex of a digest, without an algorithm prefix ("sha256:")."""
    if not digest:
        return None
    return str(digest).rsplit(":", 1)[-1][:DIGEST_CHARS]


def toolchain_settings(cfg: dict) -> dict:
    """The grading toolchain recorded in config.json; None for older configs.
    racer-check is identified by its hash, not its path (Mac and container differ)."""
    tc = cfg.get("toolchain") or {}
    rc = tc.get("racer_check") or {}
    return {"dasm": tc.get("dasm"), "racer_check": _short(rc.get("sha256")),
            "image": _short(tc.get("image_digest"))}


def attempt_budget(cfg: dict, attempts: list[dict]) -> int:
    if (cfg.get("mode") or "one-shot") != "tool-feedback":
        return 1
    if cfg.get("retries") is not None:
        return 1 + int(cfg["retries"])
    return max((a.get("attempt_no", 1) for a in attempts), default=1)


def _ok(attempt: dict) -> bool:
    return bool((attempt.get("grade") or {}).get("ok"))


def assembled(attempt: dict) -> tuple[bool, bool]:
    """(DASM took the program as the model wrote it, DASM took the program
    that was graded). The two differ only when the syntax fixer ran."""
    after = bool((attempt.get("assemble") or {}).get("ok"))
    fix = attempt.get("fix")
    return (bool(fix["assembled_as_written"]) if fix else after), after


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
            "assembled_as_written": sum(assembled(a)[0] for a in rows),
            "assembled_after_fix": sum(assembled(a)[1] for a in rows),
            "output_tokens": sum((a.get("tokens") or {}).get("output") or 0 for a in rows),
            "wall_ms": sum(a.get("wall_ms") or 0 for a in rows),
            "cost_usd": (sum(a["cost_usd"] for a in rows if a.get("cost_usd") is not None)
                         if any(a.get("cost_usd") is not None for a in rows) else None),
        })
    return out


def summarize_timing(timings: list[dict]) -> dict:
    """tok/s, queue and time outside generation over attempt_timing dicts.

    tok/s is the total output tokens over the total eval time, not a mean of
    per-attempt rates, so long attempts weigh more. It counts only attempts
    with an eval time. queue_ms sums RunPod's delay, and is None if no attempt
    has one. outside_ms sums wall - delay - total_duration over the attempts
    with a total_duration; a missing delay counts as 0 because the attempt
    wasn't queued on RunPod. Each *_n says how many of the n attempts a figure
    covers."""
    evals = [t for t in timings if t["eval_ms"]]
    eval_ms = sum(t["eval_ms"] for t in evals)
    queued = [t for t in timings if t["delay_ms"] is not None]
    outside = [t for t in timings if t["total_ms"] is not None and t["wall_ms"] is not None]
    return {
        "n": len(timings),
        "tok_s": sum(t["output_tokens"] for t in evals) / (eval_ms / 1000) if eval_ms else None,
        "tok_s_n": len(evals),
        "queue_ms": sum(t["delay_ms"] for t in queued) if queued else None,
        "queue_n": len(queued),
        "outside_ms": (sum(t["wall_ms"] - (t["delay_ms"] or 0) - t["total_ms"] for t in outside)
                       if outside else None),
        "outside_n": len(outside),
    }


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
    samples, budgets, planned, timings = [], set(), 0, []
    settings = job_settings(jobs[0]["config"])
    flags = []
    for job in jobs:
        samples += summarize_samples(job["attempts"])
        timings += [a.get("timing") or attempt_timing(a, {}) for a in job["attempts"]]
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
        "assembled_as_written": sum(s["assembled_as_written"] for s in samples),
        "assembled_after_fix": sum(s["assembled_after_fix"] for s in samples),
        "output_tokens": sum(s["output_tokens"] for s in samples),
        "wall_ms": sum(s["wall_ms"] for s in samples),
        "cost_usd": sum(costs) if costs else None,
        "timing": summarize_timing(timings),
        "timings": timings,
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
# "links", where present, is shaped like "rows": each entry is the index (into the
# cells the table was built from) of the cell that table cell shows, or None.

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
    index = {id(c): i for i, c in enumerate(cells)}
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
        links = [[None] + [index[id(lookup[(model, sid, f)])] if (model, sid, f) in lookup
                           else None for f in facts]
                 for model, sid in row_ids]
        how = "pass@1" if mode == "one-shot" else "pass@1 \u2192 pass@budget"
        tables.append({"title": f"{task} / {variant} / {mode}",
                       "headers": ["model"] + [f"facts: {f}" for f in facts],
                       "rows": rows, "links": links,
                       "notes": [f"Cells: {how}, as passing/samples."]})
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


def _covered(text: str, k: int, n: int) -> str:
    """text, marked with how many of the n attempts it covers when that isn't all of them."""
    return text if k == n else f"{text} ({k}/{n})"


def timing_columns(t: dict) -> list[str]:
    """tok/s, queue and outside generation from summarize_timing, as display strings."""
    tok_s = "-" if t["tok_s"] is None else _covered(f"{t['tok_s']:.1f}", t["tok_s_n"], t["n"])
    queue = "-" if t["queue_ms"] is None else _covered(_wall(t["queue_ms"]), t["queue_n"], t["n"])
    outside = ("-" if t["outside_ms"] is None
               else _covered(_wall(t["outside_ms"]), t["outside_n"], t["n"]))
    return [tok_s, queue, outside]


TIMING_NOTE = ("tok/s is output tokens over Ollama's eval time, both summed over attempts. "
               "queue is RunPod's delay before a worker took the job. outside gen is wall "
               "time less queue less Ollama's total_duration: model loading, a stalled "
               "worker, streaming. A figure marked (k/n) covers k of n attempts; a timed-out "
               "request records no Ollama durations.")


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "-"


def detail_table(cells: list[dict]) -> dict:
    varying = varying_settings(cells)
    headers = (["model", "facts", "task", "variant", "mode", "samples", "pass@1",
                "pass@budget", "first-attempt categories", "near miss", "assembled",
                "attempts/pass",
                "output tokens", "wall", "cost", "tok/s", "queue", "outside gen"]
               + varying + ["flags"])
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
            cats, str(c["near_misses"]), assembled_value(c),
            "-" if c["mean_attempts_to_pass"] is None else f"{c['mean_attempts_to_pass']:.2f}",
            f"{c['output_tokens']:,}", _wall(c["wall_ms"]),
            "-" if c["cost_usd"] is None else f"${c['cost_usd']:.4f}",
        ] + timing_columns(c["timing"]) + [c["settings"][k] for k in varying]
          + ["; ".join(c["flags"]) or ""])
    constant = [f"{k}={cells[0]['settings'][k]}" for k in SETTING_KEYS
                if k not in varying and cells]
    notes = ["assembled counts all attempts: as written, then \u2192 after the syntax fixer "
             "where fix_syntax is on; "
             "first-attempt categories and near misses are over first attempts; "
             "attempts/pass is the mean attempt number of the first pass; "
             "tokens, wall and cost are totals over all attempts.", TIMING_NOTE]
    if constant:
        notes.append("Same for every cell: " + ", ".join(constant) + ".")
    links = [[i] + [None] * (len(headers) - 1) for i in range(len(cells))]
    return {"title": "Cell detail", "headers": headers, "rows": rows, "links": links,
            "notes": notes}


def assembled_value(cell: dict) -> str:
    """Attempts DASM took as written, and after the fix where the fixer was on."""
    text = _frac(cell["assembled_as_written"], cell["attempts_total"])
    if cell["settings"].get("fix_syntax") == "on":
        text += f" \u2192 {_frac(cell['assembled_after_fix'], cell['attempts_total'])}"
    return text


def worker_table(cells: list[dict]) -> dict | None:
    """Timing per RunPod worker over all the cells' attempts; None if no attempt names one."""
    workers: dict[str, list] = {}
    for c in cells:
        for t in c["timings"]:
            if t["worker_id"]:
                workers.setdefault(t["worker_id"], []).append(t)
    if not workers:
        return None
    rows = []
    for worker, timings in sorted(workers.items()):
        t = summarize_timing(timings)
        per = "-" if t["outside_ms"] is None else _wall(t["outside_ms"] / t["outside_n"])
        rows.append([worker, str(t["n"])] + timing_columns(t) + [per])
    return {"title": "RunPod workers",
            "headers": ["worker", "attempts", "tok/s", "queue", "outside gen",
                        "outside gen / attempt"],
            "rows": rows,
            "notes": ["Attempts whose saved response names the worker (runpod.worker_id), "
                      "over all cells. A timed-out request names none, so it isn't counted.",
                      TIMING_NOTE]}


def report_tables(cells: list[dict]) -> list[dict]:
    if not cells:
        return []
    workers = worker_table(cells)
    return grid_tables(cells) + [detail_table(cells)] + ([workers] if workers else [])


# ---------------------------------------------------------------- rendering

def _md_cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def any_flagged(tables: list[dict]) -> bool:
    """Whether any table cell carries the flag mark, so the legend is worth printing."""
    return any(FLAG in str(cell) for t in tables for row in t["rows"] for cell in row)


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
    if any_flagged(tables):
        out += [f"{FLAG} flagged: incomplete job; see the flags column.", ""]
    return "\n".join(out)


def render_html(tables: list[dict], sources: list[str], cells: list[dict] | None = None,
                jobs: list[dict] | None = None) -> str:
    """One self-contained page: the tables and, given the cells and jobs they were
    built from, each cell's samples and attempts (see report_html)."""
    from tbd.eval import report_html
    return report_html.render_page(tables, sources, cells or [], jobs or [])


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("experiments", nargs="+", type=Path,
                   help="experiment folders (experiments/<exp>) or job folders")
    p.add_argument("--format", choices=("md", "html"), default="md")
    p.add_argument("--out", type=Path, default=None, help="write here instead of stdout")
    p.add_argument("--html", type=Path, default=None, metavar="PATH",
                   help="write the HTML page, with drill-down to attempts, to PATH "
                        "(the same as --format html --out PATH)")
    args = p.parse_args(argv)
    if args.html:
        args.format, args.out = "html", args.html
    for d in args.experiments:
        if not d.is_dir():
            p.error(f"{d} is not a folder")
    jobs = [load_job(j) for j in find_jobs(args.experiments)]
    cells = build_cells(jobs)
    tables = report_tables(cells)
    sources = [d.name for d in args.experiments]
    text = (render_html(tables, sources, cells, jobs) if args.format == "html"
            else render_markdown(tables, sources))
    if args.out:
        args.out.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
