"""The grid report as one self-contained HTML page, with drill-down to attempts.

    python -m tbd.eval.report experiments/<exp> --html grid.html

The page holds the tables from report.report_tables (any column a table gains
shows up here unchanged) and, for each cell, its samples and attempts: the
grade, DASM's errors (assemble.json), racer-check's findings (each
<check>.result.json), the prompt, the reply, the thinking and prog.asm. Grid
and detail cells link to their cell's section.

Everything is inline (CSS and a few lines of JS; nothing is fetched), so the
file opens offline. Model output is untrusted text and is always escaped.
Size is kept down by collapsing text into <details>, truncating long thinking
and long text in the middle, and never reading stream.jsonl, frames.jsonl.gz or
the provider's raw responses.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from tbd.eval import report

THINKING_CHARS = 4000   # head and tail kept; thinking is often 20k+ per attempt
TEXT_CHARS = 20000      # any other text field
FINDINGS_SHOWN = 20     # distinct racer-check failures shown per check file

STYLE = """
:root{--fg:#1d1d1f;--muted:#5f6368;--bg:#fff;--panel:#f5f5f7;--line:#c8c8cc;
  --pass:#1a7f37;--fail:#b3261e;--link:#0b57d0}
@media (prefers-color-scheme:dark){:root{--fg:#e8e8ea;--muted:#a0a0a8;--bg:#16161a;
  --panel:#222228;--line:#3a3a42;--pass:#5cc97a;--fail:#ff8a80;--link:#8ab4f8}}
body{font:14px/1.45 system-ui,sans-serif;color:var(--fg);background:var(--bg);
  margin:0;padding:1.5rem 16px;max-width:110rem}
a{color:var(--link)}
.wrap{overflow-x:auto;margin-bottom:.5rem}
table{border-collapse:collapse}
th,td{border:1px solid var(--line);padding:.25rem .5rem;text-align:left;vertical-align:top}
th{background:var(--panel)}
p.note,.muted{color:var(--muted);font-size:.9em}
details{margin:.25rem 0}
summary{cursor:pointer}
details.cell{border:1px solid var(--line);border-radius:6px;padding:.4rem .7rem;margin:.6rem 0}
details.cell>summary{font-weight:600}
details.sample{margin-left:1rem}
details.attempt{margin-left:1rem;border-left:3px solid var(--line);padding-left:.6rem}
details.text{margin-left:.5rem}
pre{background:var(--panel);padding:.5rem;overflow-x:auto;white-space:pre-wrap;
  word-break:break-word;font:12px/1.4 ui-monospace,Menlo,Consolas,monospace;margin:.25rem 0}
.pass{color:var(--pass);font-weight:600}.fail{color:var(--fail);font-weight:600}
:target{outline:2px solid var(--link)}
button{font:inherit;margin-right:.4rem}
"""

# Opens the <details> a link points at (and its ancestors), so a grid cell's link
# lands on an expanded section.
SCRIPT = """
function openTarget(){var id=decodeURIComponent(location.hash.slice(1));
  var el=id&&document.getElementById(id);
  for(var e=el;e;e=e.parentElement){if(e.tagName==='DETAILS')e.open=true;}
  if(el)el.scrollIntoView();}
window.addEventListener('hashchange',openTarget);
document.addEventListener('DOMContentLoaded',openTarget);
function setAll(sel,open){document.querySelectorAll(sel).forEach(function(d){d.open=open;});}
"""


def _e(text) -> str:
    return html.escape(str(text), quote=True)


def truncate(text: str, limit: int) -> str:
    """text, or its head and tail with a note saying how much was cut."""
    if len(text) <= limit:
        return text
    head = limit * 3 // 4
    tail = limit - head
    return (text[:head] + f"\n\n[... {len(text) - limit:,} characters cut ...]\n\n"
            + text[-tail:])


def _read(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return None


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def attempt_dir(job: dict, attempt: dict) -> Path | None:
    """sample-NN/attempt-N under the job folder (case0's layout), if it exists."""
    if not job.get("dir"):
        return None
    d = (Path(job["dir"]) / f"sample-{attempt.get('sample', 0):02d}"
         / f"attempt-{attempt.get('attempt_no', 1)}")
    return d if d.is_dir() else None


def attempt_files(d: Path | None) -> dict:
    """What an attempt folder adds to its attempts.jsonl record: prog.asm, the DASM
    result, racer-check's results by check file, and error.txt. Missing reads as None."""
    if d is None:
        return {"prog": None, "assemble": None, "results": {}, "error": None}
    return {"prog": _read(d / "prog.asm"),
            "assemble": _read_json(d / "assemble.json"),
            "results": {p.name[:-len(".result.json")]: _read_json(p)
                        for p in sorted(d.glob("*.result.json"))},
            "error": _read(d / "error.txt")}


def findings(result: dict | None) -> dict:
    """One racer-check result.json as {passed, error, failures, runs}: the failures
    are distinct (property, observed, required), each with the power-on states that
    showed it, so eleven runs failing the same way read as one line."""
    rep = (result or {}).get("report") or {}
    distinct: dict[tuple, list] = {}
    for run in rep.get("unexpected") or []:
        for f in run.get("failures") or []:
            key = (f.get("property"), f.get("observed"), f.get("required"),
                   f.get("frame"), f.get("line"), f.get("clock"))
            distinct.setdefault(key, []).append(run.get("power_on") or "?")
    return {"passed": rep.get("passed"), "error": rep.get("error"),
            "runs": rep.get("runs"), "runs_unexpected": rep.get("runs_unexpected"),
            "failures": [{"property": k[0], "observed": k[1], "required": k[2],
                          "where": ", ".join(f"{label} {v}" for label, v in
                                             zip(("frame", "line", "clock"), k[3:])
                                             if v is not None),
                          "power_on": v} for k, v in distinct.items()]}


# ---------------------------------------------------------------- pieces

def _text_block(label: str, text, limit: int = TEXT_CHARS, open_: bool = False) -> str:
    if not text:
        return ""
    text = str(text)
    return (f"<details class='text'{' open' if open_ else ''}><summary>{_e(label)} "
            f"<span class='muted'>({len(text):,} chars)</span></summary>"
            f"<pre>{_e(truncate(text, limit))}</pre></details>")


def _table(t: dict, anchor: str) -> str:
    links = t.get("links") or []
    out = [f"<h2 id='{_e(anchor)}'>{_e(t['title'])}</h2><div class='wrap'><table><tr>"
           + "".join(f"<th>{_e(h)}</th>" for h in t["headers"]) + "</tr>"]
    for r, row in enumerate(t["rows"]):
        cells = []
        for c, v in enumerate(row):
            i = links[r][c] if r < len(links) and c < len(links[r]) else None
            body = _e(v) if i is None else f"<a href='#cell-{i}'>{_e(v)}</a>"
            cells.append(f"<td>{body}</td>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</table></div>")
    out += [f"<p class='note'>{_e(n)}</p>" for n in t["notes"]]
    return "\n".join(out)


def _grade(attempt: dict) -> str:
    g = attempt.get("grade") or {}
    out = []
    if g.get("detail"):
        out.append(f"<pre>{_e(truncate(str(g['detail']), TEXT_CHARS))}</pre>")
    props = g.get("properties") or {}
    if props:
        out.append("<p>Properties: " + ", ".join(
            f"{_e(k)} <span class='{'pass' if v == 'pass' else 'fail'}'>{_e(v)}</span>"
            for k, v in props.items()) + "</p>")
    relaxed = g.get("relaxed")
    if isinstance(relaxed, dict):
        out.append(f"<p>Relaxed check: {'passed' if relaxed.get('passed') else 'failed'}"
                   + (f" ({_e(', '.join(relaxed.get('failed') or []))})"
                      if relaxed.get("failed") else "") + "</p>")
    return "\n".join(out)


def _assemble(asm: dict | None, attempt: dict) -> str:
    asm = asm if asm is not None else attempt.get("assemble")
    if not asm:
        return ""
    msgs = list(asm.get("errors") or []) + list(asm.get("warnings") or [])
    head = (f"<p>DASM: <span class='{'pass' if asm.get('ok') else 'fail'}'>"
            f"{'assembled' if asm.get('ok') else 'failed'}</span>"
            + (f", {asm['rom_bytes']:,} bytes" if asm.get("rom_bytes") is not None else "")
            + "</p>")
    return head + (f"<pre>{_e(truncate(chr(10).join(map(str, msgs)), TEXT_CHARS))}</pre>"
                   if msgs else "")


def _results(results: dict) -> str:
    out = []
    for name, result in results.items():
        f = findings(result)
        status = ("passed" if f["passed"] else "failed") if f["passed"] is not None else "?"
        runs = (f", {f['runs_unexpected']} of {f['runs']} runs unexpected"
                if f["runs"] is not None else "")
        out.append(f"<p>racer-check {_e(name)}: <span class='"
                   f"{'pass' if f['passed'] else 'fail'}'>{status}</span>{_e(runs)}</p>")
        if f["error"]:
            out.append(f"<pre>{_e(truncate(str(f['error']), TEXT_CHARS))}</pre>")
        if f["failures"]:
            rows = "".join(
                f"<tr><td>{_e(x['property'])}</td><td>{_e(x['where'])}</td>"
                f"<td>{_e(x['observed'])}</td><td>{_e(x['required'])}</td>"
                f"<td>{len(x['power_on'])}: {_e(', '.join(map(str, x['power_on'][:3])))}"
                f"{' ...' if len(x['power_on']) > 3 else ''}</td></tr>"
                for x in f["failures"][:FINDINGS_SHOWN])
            more = len(f["failures"]) - FINDINGS_SHOWN
            out.append("<div class='wrap'><table><tr><th>property</th><th>where</th>"
                       "<th>observed</th><th>required</th><th>power-on states</th></tr>"
                       + rows + "</table></div>"
                       + (f"<p class='muted'>{more} more not shown.</p>" if more > 0 else ""))
    return "\n".join(out)


def _category(attempt: dict) -> str:
    g = attempt.get("grade") or {}
    if g.get("ok"):
        return "<span class='pass'>pass</span>"
    cat = g.get("category") or "unknown"
    return (f"<span class='fail'>{_e(cat)}</span>"
            + (" (near miss)" if g.get("near_miss") else ""))


def _attempt(job: dict, attempt: dict, anchor: str) -> str:
    files = attempt_files(attempt_dir(job, attempt))
    tok = attempt.get("tokens") or {}
    meta = [f"{tok['output']:,} output tokens" if tok.get("output") is not None else None,
            report._wall(attempt["wall_ms"]) if attempt.get("wall_ms") else None,
            f"done: {attempt['done_reason']}" if attempt.get("done_reason") else None]
    summary = (f"attempt {_e(attempt.get('attempt_no', 1))}: {_category(attempt)}"
               f" <span class='muted'>{_e(' · '.join(m for m in meta if m))}</span>")
    prog = files["prog"] if files["prog"] is not None else attempt.get("code")
    body = [_grade(attempt), _assemble(files["assemble"], attempt), _results(files["results"]),
            _text_block("request error", files["error"]),
            _text_block("prog.asm", prog, open_=True),
            _text_block("prompt" if attempt.get("attempt_no", 1) == 1 else
                        "prompt (this turn; the earlier turns are the previous attempts)",
                        attempt.get("prompt")),
            _text_block("reply", attempt.get("response")),
            _text_block("thinking", attempt.get("thinking"), THINKING_CHARS)]
    return (f"<details class='attempt' id='{_e(anchor)}'><summary>{summary}</summary>"
            + "\n".join(b for b in body if b) + "</details>")


def _sample(job: dict, rows: list[dict], anchor: str) -> str:
    rows = sorted(rows, key=lambda a: a.get("attempt_no", 1))
    [s] = report.summarize_samples(rows)
    outcome = (f"<span class='pass'>passed at attempt {s['attempts_to_pass']}</span>"
               if s["passed"] else "<span class='fail'>not passed</span>")
    first = s["first_category"]
    summary = (f"sample {_e(s['sample'])}: first attempt {_e(first)}, {outcome} "
               f"<span class='muted'>({s['attempts']} attempt(s))</span>")
    return (f"<details class='sample' id='{_e(anchor)}'><summary>{summary}</summary>"
            + "\n".join(_attempt(job, a, f"{anchor}-a{a.get('attempt_no', 1)}") for a in rows)
            + "</details>")


def _cell(i: int, cell: dict, jobs: dict) -> str:
    n = cell["samples"]
    cats = ", ".join(f"{k} {v}" for k, v in cell["first_categories"].items()) or "-"
    summary = (f"{_e(cell['model'])} · facts {_e(cell['facts'])} · "
               f"{_e(cell['task'])} / {_e(cell['variant'])} / {_e(cell['mode'])} — "
               f"{_e(report.grid_value(cell))}"
               f" <span class='muted'>(first attempts: {_e(cats)}; {n} sample(s))</span>")
    out = [f"<details class='cell' id='cell-{i}'><summary>{summary}</summary>"]
    out += [f"<p class='fail'>{report.FLAG} {_e(f)}</p>" for f in cell["flags"]]
    for k, name in enumerate(cell["jobs"]):
        job = jobs.get(name)
        out.append(f"<h4>{_e(name)}</h4>")
        if job is None:
            continue
        by_sample: dict = {}
        for a in job["attempts"]:
            by_sample.setdefault(a.get("sample", 0), []).append(a)
        out += [_sample(job, rows, f"cell-{i}-j{k}-s{sample}")
                for sample, rows in sorted(by_sample.items(), key=lambda kv: str(kv[0]))]
    out.append("</details>")
    return "\n".join(out)


def render_page(tables: list[dict], sources: list[str], cells: list[dict],
                jobs: list[dict]) -> str:
    """The whole page. tables come from report.report_tables(cells); jobs are the
    report.load_job dicts the cells were built from."""
    by_name = {f"{j['experiment']}/{j['job']}": j for j in jobs}
    out = ["<title>Grid report: " + _e(", ".join(sources)) + "</title>",
           f"<style>{STYLE}</style>", f"<script>{SCRIPT}</script>",
           "<h1>Grid report</h1>", f"<p>Experiments: {_e(', '.join(sources))}</p>"]
    if not tables:
        out.append("<p>No jobs found.</p>")
    for n, t in enumerate(tables):
        # without the cells there are no sections to link to
        out.append(_table(t if cells else {**t, "links": []}, f"table-{n}"))
    if tables:
        flagged = (f"{report.FLAG} flagged: incomplete job; see the flags column. "
                   if report.any_flagged(tables) else "")
        out.append(f"<p class='note'>{flagged}Each grid cell links to its samples and "
                   "attempts below.</p>")
    if cells:
        out.append("<h2 id='cells'>Samples and attempts</h2>"
                   "<p><button onclick=\"setAll('details.cell,details.sample',true)\">"
                   "Expand cells and samples</button>"
                   "<button onclick=\"setAll('details',false)\">Collapse all</button></p>")
        out += [_cell(i, c, by_name) for i, c in enumerate(cells)]
    return "<!doctype html>\n<html lang='en'><head><meta charset='utf-8'>" \
           "<meta name='viewport' content='width=device-width,initial-scale=1'>" \
           + "\n".join(out[:3]) + "</head><body>\n" + "\n".join(out[3:]) \
           + "\n</body></html>\n"
