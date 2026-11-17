"""Grid report: aggregation over small synthetic attempt logs (no model or grader needed)."""

import json

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


def test_incomplete_job_reported_and_flagged(tmp_path):
    job = write_job(tmp_path / "e1", "run", _config(), ONE_SHOT[:1], status="running")
    with open(job / "attempts.jsonl", "a") as f:
        f.write('{"sample": 1, "attempt_no"')   # half-written line
    write_job(tmp_path / "e1", "empty", _config(model="m2"), [], status="interrupted")
    cells = report.build_cells([report.load_job(j) for j in report.find_jobs([tmp_path / "e1"])])
    by_model = {c["model"]: c for c in cells}
    assert by_model["m1"]["samples"] == 1
    assert by_model["m1"]["flags"] == ["e1/run: status running",
                                       "e1/run: 1 unreadable attempt line(s)"]
    assert by_model["m2"]["samples"] == 0
    assert "e1/empty: no attempts" in by_model["m2"]["flags"]
    assert report.grid_value(by_model["m2"]) == "-" + report.FLAG


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
    assert "<table>" in page and "<td>1/3</td>" in page
    # the input is never touched
    assert sorted(p.name for p in (exp / "jobs" / "a").iterdir()) == [
        "attempts.jsonl", "config.json", "metrics.json"]
