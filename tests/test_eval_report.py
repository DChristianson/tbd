"""Grid report: aggregation over small synthetic attempt logs (no model or grader needed)."""

import json
from datetime import datetime, timedelta, timezone

from tbd.eval import report


def _attempt(sample, attempt_no, category, out=100, wall_ms=1000, near_miss=False, cost=None):
    return {"sample": sample, "seed": 42 + sample, "attempt_no": attempt_no,
            "done_reason": "stop",
            "grade": {"ok": category == "pass", "category": category, "near_miss": near_miss},
            "tokens": {"prompt": 50, "output": out}, "wall_ms": wall_ms, "cost_usd": cost}


def _config(model="m1", mode="one-shot", facts="none", task="t1", variant="canonical",
            samples=3, retries=0, **extra):
    return {"provider": "ollama", "model": model, "facts": facts, "task": task,
            "variant": variant, "mode": mode, "samples": samples, "temperature": 0.7,
            "seed": 42, "retries": retries, "max_tokens": 4096, "num_ctx": 8192, **extra}


def write_job(exp_dir, name, config, attempts, status="completed"):
    job = exp_dir / "jobs" / name
    job.mkdir(parents=True)
    (job / "config.json").write_text(json.dumps(config))
    (job / "metrics.json").write_text(json.dumps({"status": status}))
    (job / "attempts.jsonl").write_text("".join(json.dumps(a) + "\n" for a in attempts))
    return job


ONE_SHOT = [_attempt(0, 1, "pass", out=300, cost=0.01),
            _attempt(1, 1, "timing", near_miss=True, cost=0.02),
            _attempt(2, 1, "assemble_error")]

FEEDBACK = [  # sample 0 passes on attempt 2, sample 1 on attempt 1, sample 2 never (budget 3)
    _attempt(0, 1, "assemble_error"), _attempt(0, 2, "pass"),
    _attempt(1, 1, "pass"),
    _attempt(2, 1, "timing", near_miss=True), _attempt(2, 2, "timing"),
    _attempt(2, 3, "output_mismatch"),
]


def test_summarize_samples_tracks_first_attempt_and_first_pass():
    s = report.summarize_samples(list(reversed(FEEDBACK)))   # order in the log doesn't matter
    assert [x["sample"] for x in s] == [0, 1, 2]
    assert [x["first_category"] for x in s] == ["assemble_error", "pass", "timing"]
    assert [x["first_passed"] for x in s] == [False, True, False]
    assert [x["attempts_to_pass"] for x in s] == [2, 1, None]
    assert [x["near_miss"] for x in s] == [False, False, True]
    assert s[2]["output_tokens"] == 300 and s[2]["wall_ms"] == 3000


def test_one_shot_cell(tmp_path):
    write_job(tmp_path / "e1", "m1-one-shot", _config(), ONE_SHOT)
    [cell] = report.build_cells([report.load_job(j) for j in report.find_jobs([tmp_path / "e1"])])
    assert cell["key"] == ("m1", "none", "t1", "canonical", "one-shot")
    assert (cell["samples"], cell["pass_at_1"], cell["pass_at_budget"], cell["budget"]) == (3, 1, 1, 1)
    assert cell["first_categories"] == {"pass": 1, "timing": 1, "assemble_error": 1}
    assert cell["near_misses"] == 1
    assert cell["mean_attempts_to_pass"] == 1
    assert cell["output_tokens"] == 500 and cell["wall_ms"] == 3000
    assert abs(cell["cost_usd"] - 0.03) < 1e-9
    assert cell["flags"] == []
    assert report.grid_value(cell) == "1/3"


def test_tool_feedback_cell_pass_at_budget(tmp_path):
    write_job(tmp_path / "e1", "fb", _config(mode="tool-feedback", retries=2), FEEDBACK)
    [cell] = report.build_cells([report.load_job(j) for j in report.find_jobs([tmp_path / "e1"])])
    assert cell["budget"] == 3
    assert (cell["pass_at_1"], cell["pass_at_budget"]) == (1, 2)
    assert cell["mean_attempts_to_pass"] == 1.5
    assert cell["attempts_total"] == 6
    assert cell["cost_usd"] is None
    assert report.grid_value(cell) == "1/3 → 2/3 @3"


def test_experiments_combine_and_split_into_grid(tmp_path):
    write_job(tmp_path / "e1", "a", _config(model="m1"), ONE_SHOT)
    write_job(tmp_path / "e1", "b", _config(model="m2", facts="contributed"), ONE_SHOT[:1])
    write_job(tmp_path / "e2", "c", _config(model="m1", mode="tool-feedback", retries=2), FEEDBACK)
    write_job(tmp_path / "e2", "d", _config(model="m1", variant="pal"), ONE_SHOT[:2])
    jobs = [report.load_job(j) for j in report.find_jobs([tmp_path / "e1", tmp_path / "e2"])]
    cells = report.build_cells(jobs)
    tables = report.grid_tables(cells)
    assert [t["title"] for t in tables] == ["t1 / canonical / one-shot",
                                             "t1 / canonical / tool-feedback",
                                             "t1 / pal / one-shot"]
    one_shot = tables[0]
    assert one_shot["headers"] == ["model", "facts: none", "facts: contributed",
                                   "facts: discovered", "facts: both"]
    assert one_shot["rows"] == [["m1", "1/3", "—", "—", "—"],
                                ["m2", "—", "1/1", "—", "—"]]


def test_same_cell_and_settings_across_experiments_merges(tmp_path):
    write_job(tmp_path / "e1", "m1", _config(model_digest="abc"), ONE_SHOT)
    write_job(tmp_path / "e2", "m1", _config(model_digest="abc"), ONE_SHOT[:1])
    [cell] = report.build_cells([report.load_job(j)
                                 for j in report.find_jobs([tmp_path / "e1", tmp_path / "e2"])])
    assert cell["samples"] == 4 and cell["samples_planned"] == 6
    assert cell["flags"] == []


def test_different_settings_are_separate_cells_and_rows(tmp_path):
    write_job(tmp_path / "e1", "m1", _config(), ONE_SHOT)
    write_job(tmp_path / "e2", "m1", _config(max_tokens=64000), ONE_SHOT[:1])
    write_job(tmp_path / "e2", "m2", _config(model="m2"), ONE_SHOT)
    cells = report.build_cells([report.load_job(j)
                                for j in report.find_jobs([tmp_path / "e1", tmp_path / "e2"])])
    assert [(c["model"], c["settings"]["max_tokens"], c["samples"]) for c in cells] == [
        ("m1", "4096", 3), ("m1", "64000", 1), ("m2", "4096", 3)]
    assert all(c["flags"] == [] for c in cells)
    [grid] = report.grid_tables(cells)
    assert [r[:2] for r in grid["rows"]] == [["m1 (max_tokens=4096)", "1/3"],
                                             ["m1 (max_tokens=64000)", "1/1"],
                                             ["m2", "1/3"]]


def test_repulled_model_is_a_separate_cell(tmp_path):
    write_job(tmp_path / "e1", "m1", _config(model_digest="a" * 64), ONE_SHOT)
    write_job(tmp_path / "e2", "m1", _config(model_digest="b" * 64), ONE_SHOT)
    cells = report.build_cells([report.load_job(j)
                                for j in report.find_jobs([tmp_path / "e1", tmp_path / "e2"])])
    assert [c["settings"]["digest"] for c in cells] == ["a" * 12, "b" * 12]
    [grid] = report.grid_tables(cells)
    assert [r[0] for r in grid["rows"]] == [f"m1 (digest={'a' * 12})", f"m1 (digest={'b' * 12})"]
    assert "digest" in report.detail_table(cells)["headers"]


def _toolchain(sha, path="/workspace/vendor/racer-check/racer-check"):
    return {"image_digest": None, "dasm": "2.20.14.1",
            "racer_check": {"path": path, "sha256": sha}}


def test_different_racer_check_builds_are_separate_cells(tmp_path):
    write_job(tmp_path / "e1", "m1", _config(toolchain=_toolchain("a" * 64)), ONE_SHOT)
    write_job(tmp_path / "e2", "m1", _config(toolchain=_toolchain("b" * 64)), ONE_SHOT)
    cells = report.build_cells([report.load_job(j)
                                for j in report.find_jobs([tmp_path / "e1", tmp_path / "e2"])])
    assert [c["settings"]["racer_check"] for c in cells] == ["a" * 12, "b" * 12]
    assert all(c["settings"]["dasm"] == "2.20.14.1" for c in cells)
    [grid] = report.grid_tables(cells)
    assert [r[0] for r in grid["rows"]] == [f"m1 (racer_check={'a' * 12})",
                                            f"m1 (racer_check={'b' * 12})"]
    assert "racer_check" in report.detail_table(cells)["headers"]


def test_same_racer_check_at_another_path_merges(tmp_path):
    """Mac and container put the same binary at different paths."""
    write_job(tmp_path / "e1", "m1", _config(toolchain=_toolchain("a" * 64)), ONE_SHOT)
    write_job(tmp_path / "e2", "m1", _config(toolchain=_toolchain("a" * 64, "/Users/x/rc")),
              ONE_SHOT)
    [cell] = report.build_cells([report.load_job(j)
                                 for j in report.find_jobs([tmp_path / "e1", tmp_path / "e2"])])
    assert cell["samples"] == 6


def test_image_digest_is_shortened_without_its_prefix():
    tc = dict(_toolchain(None), image_digest="sha256:" + "c" * 64)
    s = report.job_settings({"toolchain": tc})
    assert (s["image"], s["racer_check"]) == ("c" * 12, "-")


def test_incomplete_job_reported_and_flagged(tmp_path):
    job = write_job(tmp_path / "e1", "run", _config(), ONE_SHOT[:1], status="running")
    with open(job / "attempts.jsonl", "a") as f:
        f.write('{"sample": 1, "attempt_no"')   # half-written line
    write_job(tmp_path / "e1", "empty", _config(model="m2"), [], status="interrupted")
    cells = report.build_cells([report.load_job(j) for j in report.find_jobs([tmp_path / "e1"])])
    by_model = {c["model"]: c for c in cells}
    assert by_model["m1"]["samples"] == 1
    assert by_model["m1"]["flags"] == ["e1/run: status running (no heartbeat; stale)",
                                       "e1/run: 1 unreadable attempt line(s)"]
    assert by_model["m2"]["samples"] == 0
    assert "e1/empty: no attempts" in by_model["m2"]["flags"]
    assert report.grid_value(by_model["m2"]) == "-" + report.FLAG


def test_flag_legend_only_when_something_is_flagged(tmp_path):
    legend = "flagged: incomplete job"
    write_job(tmp_path / "ok", "j", _config(), ONE_SHOT)
    clean = report.report_tables(report.build_cells(
        [report.load_job(j) for j in report.find_jobs([tmp_path / "ok"])]))
    assert not report.any_flagged(clean)
    assert legend not in report.render_markdown(clean, ["ok"])
    write_job(tmp_path / "bad", "j", _config(), ONE_SHOT, status="interrupted")
    flagged = report.report_tables(report.build_cells(
        [report.load_job(j) for j in report.find_jobs([tmp_path / "bad"])]))
    assert report.any_flagged(flagged)
    assert legend in report.render_markdown(flagged, ["bad"])


def test_old_config_missing_keys(tmp_path):
    """Early runs: no task/variant/facts/num_ctx/retries, and 'think' instead of 'thinking'."""
    old = {"model": "llama3.1", "mode": "tool-feedback", "samples": 1, "think": None}
    attempts = [{"sample": 0, "attempt_no": n, "grade": {"ok": False, "category": "no_code"}}
                for n in (1, 2)]
    write_job(tmp_path / "old", "j", old, attempts)
    [cell] = report.build_cells([report.load_job(j) for j in report.find_jobs([tmp_path / "old"])])
    assert cell["key"] == ("llama3.1", "none", "t1", "canonical", "tool-feedback")
    assert cell["budget"] == 2   # no retries recorded: the most attempts seen
    assert cell["near_misses"] == 0 and cell["output_tokens"] == 0
    assert cell["settings"]["num_ctx"] == "-"
    assert [cell["settings"][k] for k in ("dasm", "racer_check", "image")] == ["-", "-", "-"]


def test_assembled_as_written_and_after_fix(tmp_path):
    def attempt(sample, as_written, after, fixer):
        a = _attempt(sample, 1, "timing" if after else "assemble_error")
        a["assemble"] = {"ok": after}
        if fixer:
            a["fix"] = {"assembled_as_written": as_written, "assembled_after_fix": after,
                        "rules": [] if as_written or not after else ["indent_col0"]}
        return a

    write_job(tmp_path / "e", "off", _config(model="m1"),
              [attempt(0, True, True, False), attempt(1, False, False, False)])
    write_job(tmp_path / "e", "on", _config(model="m1", fix_syntax=True),
              [attempt(0, True, True, True), attempt(1, False, True, True),
               attempt(2, False, False, True)])
    write_job(tmp_path / "e", "old", _config(model="m2"), [_attempt(0, 1, "pass")])
    cells = report.build_cells([report.load_job(j) for j in report.find_jobs([tmp_path / "e"])])
    by = {(c["model"], c["settings"]["fix_syntax"]): c for c in cells}
    assert set(by) == {("m1", "off"), ("m1", "on"), ("m2", "off")}
    assert report.assembled_value(by[("m1", "off")]) == "1/2"
    assert report.assembled_value(by[("m1", "on")]) == "1/3 → 2/3"
    t = report.detail_table(cells)
    assert "assembled" in t["headers"] and "fix_syntax" in t["headers"]


def test_detail_table_shows_only_varying_settings(tmp_path):
    write_job(tmp_path / "e", "a", _config(model="m1"), ONE_SHOT)
    write_job(tmp_path / "e", "b", _config(model="m2", num_ctx=32768), ONE_SHOT)
    t = report.detail_table(report.build_cells(
        [report.load_job(j) for j in report.find_jobs([tmp_path / "e"])]))
    assert "num_ctx" in t["headers"] and "max_tokens" not in t["headers"]
    assert any("max_tokens=4096" in n for n in t["notes"])


def test_cli_markdown_and_html(tmp_path, capsys):
    exp = tmp_path / "exp"
    write_job(exp, "a", _config(), ONE_SHOT)
    assert report.main([str(exp)]) == 0
    md = capsys.readouterr().out
    assert "## t1 / canonical / one-shot" in md
    assert "| m1 | 1/3 |" in md
    assert "## Cell detail" in md
    out = tmp_path / "grid.html"
    assert report.main([str(exp), "--format", "html", "--out", str(out)]) == 0
    page = out.read_text()
    assert "<table>" in page and "<a href='#cell-0'>1/3</a></td>" in page
    # the input is never touched
    assert sorted(p.name for p in (exp / "jobs" / "a").iterdir()) == [
        "attempts.jsonl", "config.json", "metrics.json"]


def test_html_page_drills_down_to_attempts_and_escapes_model_text(tmp_path):
    """A grid cell links to its section; an attempt shows prog.asm, DASM errors and
    racer-check findings from its folder; model text can't inject markup."""
    exp = tmp_path / "exp"
    hostile = "<script>alert(1)</script>"
    attempts = [dict(_attempt(0, 1, "assemble_error"),
                     prompt="Reference notes:\n[F001] indent directives\n\nWrite a kernel.",
                     response=f"```asm\n lda #0 ; {hostile}\n```",
                     thinking="t" * 50000),
                dict(_attempt(0, 2, "timing"), prompt="DASM reported errors", response="ok")]
    job = write_job(exp, "a", _config(mode="tool-feedback", retries=1, samples=1), attempts)
    a1, a2 = job / "sample-00" / "attempt-1", job / "sample-00" / "attempt-2"
    a1.mkdir(parents=True)
    a2.mkdir(parents=True)
    (a1 / "prog.asm").write_text(f" lda #0 ; {hostile}\n")
    (a1 / "assemble.json").write_text(json.dumps(
        {"ok": False, "errors": ["prog.asm (1): error: Unknown Mnemonic 'dw'."]}))
    (a2 / "prog.asm").write_text(" sta COLUBK\n")
    failure = {"property": "background", "frame": 2, "line": 40, "clock": 68,
               "observed": "color $78", "required": "color $84"}
    (a2 / "check.result.json").write_text(json.dumps({"report": {
        "passed": False, "runs": 2, "runs_unexpected": 2,
        "unexpected": [{"power_on": "zero", "failures": [failure]},
                       {"power_on": "random seed 1", "failures": [failure]}]}}))
    (a2 / "stream.jsonl").write_text("STREAM-NOT-EMBEDDED\n")

    out = tmp_path / "grid.html"
    assert report.main([str(exp), "--html", str(out)]) == 0
    page = out.read_text()
    # the grid, its cell linked to the drill-down section
    assert "<h2 id='table-0'>t1 / canonical / tool-feedback</h2>" in page
    assert "<a href='#cell-0'>0/1 → 0/1 @2</a>" in page
    assert "<details class='cell' id='cell-0'>" in page
    assert "id='cell-0-j0-s0-a1'" in page and "id='cell-0-j0-s0-a2'" in page
    # attempt contents: prog.asm, the prompt's reference notes, DASM errors, findings
    assert " sta COLUBK" in page
    assert "[F001] indent directives" in page
    assert "Unknown Mnemonic &#x27;dw&#x27;." in page
    assert "racer-check check: <span class='fail'>failed</span>, 2 of 2 runs" in page
    assert "<td>background</td><td>frame 2, line 40, clock 68</td>" in page
    assert "2: zero, random seed 1" in page   # the same failure in two runs is one row
    # untrusted text is escaped, long thinking cut, stream logs left out
    assert hostile not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "t" * 50000 not in page and "characters cut" in page
    assert "STREAM-NOT-EMBEDDED" not in page
    # self-contained: nothing fetched from elsewhere
    assert "http://" not in page and "https://" not in page and " src=" not in page


# ---------------------------------------------------------------- staleness

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


def _running(age_s, **extra):
    return {"status": "running", "pid": 4242, "host": "box",
            "updated_at": (NOW - timedelta(seconds=age_s)).isoformat(timespec="seconds"),
            **extra}


def test_fresh_running_job_is_not_stale():
    assert report.stale_reason(_running(60), {"request_timeout_s": 900}, NOW) is None
    assert report.stale_reason(_running(60), {}, NOW, "box", lambda pid: True) is None


def test_old_heartbeat_is_stale():
    reason = report.stale_reason(_running(3 * 3600), {"request_timeout_s": 900}, NOW)
    assert reason == "last heartbeat 3h00m ago"
    # the threshold is the request timeout plus a margin
    limit = 900 + report.HEARTBEAT_MARGIN_S
    assert report.stale_reason(_running(limit - 1), {"request_timeout_s": 900}, NOW) is None
    assert report.stale_reason(_running(limit + 1), {"request_timeout_s": 900}, NOW)
    # no timeout in the config: the default
    default = report.DEFAULT_REQUEST_TIMEOUT_S + report.HEARTBEAT_MARGIN_S
    assert report.stale_reason(_running(default - 1), {}, NOW) is None
    assert report.stale_reason(_running(default + 1), {}, NOW)


def test_gone_pid_on_this_host_is_stale():
    assert report.stale_reason(_running(5), {}, NOW, "box", lambda pid: False) == "pid 4242 gone"
    # another host's pid can't be checked from here
    assert report.stale_reason(_running(5), {}, NOW, "other", lambda pid: False) is None


def test_missing_heartbeat_is_stale():
    assert report.stale_reason({"status": "running"}, {}, NOW) == "no heartbeat"


def test_finished_jobs_are_never_stale():
    for status in ("completed", "interrupted", "failed"):
        assert report.stale_reason({"status": status}, {}, NOW) is None
        assert report.stale_reason({**_running(10 ** 6), "status": status}, {}, NOW) is None


def test_report_flags_stale_and_fresh_running_jobs(tmp_path):
    for name, metrics in [("old", _running(7200)), ("fresh", _running(30)),
                          ("dead", _running(30, pid=999)), ("legacy", {"status": "running"})]:
        job = write_job(tmp_path / "e", name, _config(model=name, request_timeout_s=900),
                        ONE_SHOT[:1])
        (job / "metrics.json").write_text(json.dumps(metrics))
    jobs = [report.load_job(j) for j in report.find_jobs([tmp_path / "e"])]
    flags = {j["job"]: report.job_flags(j, NOW, "box", lambda pid: pid != 999) for j in jobs}
    assert flags == {"old": ["e/old: status running (stale: last heartbeat 2h00m ago)"],
                     "fresh": ["e/fresh: status running"],
                     "dead": ["e/dead: status running (stale: pid 999 gone)"],
                     "legacy": ["e/legacy: status running (no heartbeat; stale)"]}


# ---------------------------------------------------------------- timing

def _response(eval_s, total_s, worker=None, delay_ms=None):
    """A saved response.json: Ollama's durations in ns, plus RunPod's job info if given."""
    resp = {"done": True, "eval_duration": int(eval_s * 1e9),
            "total_duration": int(total_s * 1e9)}
    if worker:
        resp["runpod"] = {"job_id": "j", "worker_id": worker, "delay_ms": delay_ms,
                          "execution_ms": 1}
    return resp


def write_responses(job, responses):
    """responses: {(sample, attempt_no): response}. An attempt left out saved none."""
    for (sample, attempt_no), resp in responses.items():
        d = job / f"sample-{sample:02d}" / f"attempt-{attempt_no}"
        d.mkdir(parents=True)
        (d / "response.json").write_text(json.dumps(resp))


def _timing(out, wall_s, eval_s, total_s):
    return report.attempt_timing({"tokens": {"output": out}, "wall_ms": wall_s * 1000},
                                 _response(eval_s, total_s))


def test_tok_s_is_tokens_over_eval_time_not_a_mean_of_rates():
    # 1000 tokens in 10 s (100 tok/s) and 10 tokens in 10 s (1 tok/s): the mean of
    # the rates would be 50.5; the cell's rate is 1010 / 20.
    t = report.summarize_timing([_timing(1000, 12, 10, 11), _timing(10, 12, 10, 11)])
    assert t["tok_s"] == 1010 / 20


def test_runpod_cell_timing(tmp_path):
    attempts = [_attempt(0, 1, "timing", out=1000, wall_ms=20_000),
                _attempt(1, 1, "timing", out=100, wall_ms=5_000)]
    job = write_job(tmp_path / "e1", "a", _config(samples=2), attempts)
    write_responses(job, {(0, 1): _response(10, 12, "w1", 500),
                          (1, 1): _response(1, 2, "w2", 1_000)})
    [cell] = report.build_cells([report.load_job(job)])
    t = cell["timing"]
    assert t["tok_s"] == 1100 / 11
    assert t["queue_ms"] == 1_500
    assert t["outside_ms"] == (20 - 0.5 - 12) * 1000 + (5 - 1 - 2) * 1000
    detail = report.detail_table([cell])
    row = detail["rows"][0]
    assert [row[detail["headers"].index(h)] for h in ("tok/s", "queue", "outside gen")] == \
        ["100.0", "2s", "10s"]


def test_timing_without_runpod_data_or_ollama_durations(tmp_path):
    # Local Ollama: durations but no RunPod data, so no queue and outside is wall - total.
    # The timed-out attempt saved no response.json, so it has no durations at all.
    attempts = [_attempt(0, 1, "pass", out=600, wall_ms=8_000),
                _attempt(1, 1, "timeout", out=0, wall_ms=600_000)]
    job = write_job(tmp_path / "e1", "a", _config(samples=2), attempts)
    write_responses(job, {(0, 1): _response(6, 7)})
    [cell] = report.build_cells([report.load_job(job)])
    t = cell["timing"]
    assert (t["tok_s"], t["queue_ms"], t["outside_ms"]) == (100, None, 1_000)
    assert (t["n"], t["tok_s_n"], t["outside_n"]) == (2, 1, 1)
    assert report.timing_columns(t) == ["100.0 (1/2)", "-", "1s (1/2)"]
    assert report.worker_table([cell]) is None
    assert report.report_tables([cell])[-1]["title"] == "Cell detail"


def test_attempts_without_responses_show_dashes(tmp_path):
    job = write_job(tmp_path / "e1", "a", _config(), ONE_SHOT)
    [cell] = report.build_cells([report.load_job(job)])
    assert report.timing_columns(cell["timing"]) == ["-", "-", "-"]


def test_worker_table_groups_attempts_across_cells(tmp_path):
    a = write_job(tmp_path / "e1", "a", _config(facts="none", samples=2),
                  [_attempt(0, 1, "timing", out=550, wall_ms=12_000),
                   _attempt(1, 1, "timing", out=1450, wall_ms=11_000)])
    write_responses(a, {(0, 1): _response(10, 11, "slow", 100),
                        (1, 1): _response(10, 10.5, "fast", 500)})
    b = write_job(tmp_path / "e1", "b", _config(facts="contributed", samples=2),
                  [_attempt(0, 1, "timing", out=550, wall_ms=511_000),
                   _attempt(1, 1, "timeout", out=0, wall_ms=600_000)])
    write_responses(b, {(0, 1): _response(10, 11, "slow", 1_000)})   # sample 1 timed out
    cells = report.build_cells([report.load_job(a), report.load_job(b)])
    t = report.worker_table(cells)
    assert t["headers"] == ["worker", "attempts", "tok/s", "queue", "outside gen",
                            "outside gen / attempt"]
    # slow: (12 - 0.1 - 11) + (511 - 1 - 11) = 499.9 s over 2 attempts
    assert t["rows"] == [["fast", "1", "145.0", "0s", "0s", "0s"],
                         ["slow", "2", "55.0", "1s", "8m20s", "4m10s"]]
    assert report.report_tables(cells)[-1]["title"] == "RunPod workers"
