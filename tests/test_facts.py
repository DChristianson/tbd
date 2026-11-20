"""The fact store (tbd.eval.facts, facts/README.md) and case0's --facts. No model calls."""

import json
import re
from pathlib import Path

import pytest

from tbd.eval import case0, facts, tasks
from tbd.eval.grading import Grade
from tbd.paths import TASKS

STORE = facts.load()


def _fact(fact_id="F001", text="A short fact.", source_type="official_doc",
          source_ref="vendor/atari2600/vcs.h", license="GPL-2.0-or-later", confidence=0.9):
    return {"fact_id": fact_id, "text": text, "source_type": source_type,
            "source_ref": source_ref, "license": license, "confidence": confidence}


def _toml(*records) -> str:
    out = []
    for r in records:
        out.append("[[fact]]")
        out += [f"{k} = {json.dumps(v)}" for k, v in r.items()]
        out.append("")
    return "\n".join(out)


def _store(tmp_path, *records, name="a.toml") -> Path:
    (tmp_path / name).write_text(_toml(*records))
    return tmp_path


MIXED = (_fact("F001"), _fact("F002", source_type="human_post"),
         _fact("F003", source_type="self_discovered", source_ref="probe run x"))


# ---------------------------------------------------------------- the store in the repo

def test_repo_store_loads_and_has_contributed_facts():
    assert STORE
    assert facts.select(STORE, "contributed")


def test_repo_facts_are_short_and_cited():
    for f in STORE:
        assert len(f.text) <= 300, f.fact_id
        if f.source_type != "self_discovered":
            assert f.source_ref and f.license, f.fact_id
        if not f.verified:
            assert f.confidence < 0.8, f"{f.fact_id}: unverified facts get lower confidence"


def test_repo_facts_do_not_quote_reference_solutions():
    """Reference programs are solutions; none of their comment or code lines may
    reach a prompt through a fact."""
    texts = [f.text.lower() for f in STORE]
    for ref in sorted(TASKS.glob("*/*/reference.asm")):
        for line in ref.read_text().splitlines():
            line = line.strip().lstrip(";").strip().lower()
            if len(line) >= 20:
                assert not any(line in t for t in texts), f"{ref}: {line!r}"


# ---------------------------------------------------------------- loading / validation

def test_loads_sorted_by_id(tmp_path):
    root = _store(tmp_path, _fact("F010"), _fact("F002"))
    assert [f.fact_id for f in facts.load(root)] == ["F002", "F010"]


@pytest.mark.parametrize("bad", [
    {"source_type": "forum"},
    {"confidence": 1.5},
    {"confidence": "high"},
    {"fact_id": "fact-1"},
    {"text": ""},
    {"text": "two\nlines"},
    {"license": " "},
])
def test_rejects_bad_fields(tmp_path, bad):
    with pytest.raises(facts.FactError):
        facts.load(_store(tmp_path, {**_fact(), **bad}))


def test_rejects_missing_and_unknown_fields(tmp_path):
    rec = _fact()
    del rec["license"]
    with pytest.raises(facts.FactError, match="missing"):
        facts.load(_store(tmp_path, rec))
    with pytest.raises(facts.FactError, match="unknown"):
        facts.load(_store(tmp_path, {**_fact(), "author": "x"}))


def test_rejects_duplicate_ids_across_files(tmp_path):
    _store(tmp_path, _fact("F001"), name="a.toml")
    _store(tmp_path, _fact("F001"), name="b.toml")
    with pytest.raises(facts.FactError, match="duplicate"):
        facts.load(tmp_path)


def test_unverified_source_is_flagged(tmp_path):
    [f] = facts.load(_store(tmp_path, _fact(source_ref="unverified: DASM manual")))
    assert not f.verified


# ---------------------------------------------------------------- conditions

@pytest.mark.parametrize("condition, ids", [
    ("none", []),
    ("contributed", ["F001", "F002"]),
    ("discovered", ["F003"]),
    ("both", ["F001", "F002", "F003"]),
])
def test_condition_filtering(tmp_path, condition, ids):
    store = facts.load(_store(tmp_path, *MIXED))
    assert [f.fact_id for f in facts.select(store, condition)] == ids


def test_unknown_condition():
    with pytest.raises(ValueError):
        facts.select(STORE, "all")


# ---------------------------------------------------------------- snapshot

def test_snapshot_is_stable(tmp_path):
    """Same facts, different files, order and comments: same snapshot."""
    (tmp_path / "x").mkdir()
    a = facts.select(facts.load(_store(tmp_path / "x", *MIXED)), "both")
    reordered = tmp_path / "y"
    reordered.mkdir()
    _store(reordered, MIXED[2], name="b.toml")
    (reordered / "a.toml").write_text("# a comment\n\n" + _toml(MIXED[1], MIXED[0]))
    b = facts.select(facts.load(reordered), "both")
    assert facts.snapshot(a) == facts.snapshot(b)
    assert re.fullmatch(r"[0-9a-f]{64}", facts.snapshot(a))


@pytest.mark.parametrize("edit", [{"text": "A short fact!"}, {"source_ref": "elsewhere"},
                                  {"confidence": 0.5}])
def test_snapshot_changes_on_edit(tmp_path, edit):
    before = facts.snapshot(facts.load(_store(tmp_path, *MIXED)))
    after = facts.snapshot(facts.load(_store(tmp_path, {**MIXED[0], **edit}, *MIXED[1:])))
    assert before != after


def test_snapshot_covers_only_the_selection(tmp_path):
    store = facts.load(_store(tmp_path, *MIXED))
    contributed = facts.snapshot(facts.select(store, "contributed"))
    edited = facts.load(_store(tmp_path, *MIXED[:2], {**MIXED[2], "text": "changed"}))
    assert facts.snapshot(facts.select(edited, "contributed")) == contributed
    assert facts.snapshot([]) is None


# ---------------------------------------------------------------- rendering

def test_render_tags_each_fact(tmp_path):
    store = facts.load(_store(tmp_path, *MIXED))
    block = facts.render(facts.select(store, "contributed"))
    assert block.splitlines() == ["Reference notes:", "[F001] A short fact.", "[F002] A short fact."]
    assert facts.with_facts("Do the task.", facts.select(store, "contributed")) == \
        block + "\n\nDo the task."


def test_no_facts_leaves_the_prompt_unchanged():
    spec = tasks.load("t1", "canonical")
    args = case0.build_parser().parse_args(["--model", "m"])
    assert args.facts == "none"
    args.spec, args.selected_facts = spec, facts.select(STORE, "none")
    assert case0.first_message(args) == spec.prompt


# ---------------------------------------------------------------- a facts run

def _fake_run(tmp_path, monkeypatch, condition):
    seen = []

    def fake_chat(args, messages, seed, workdir):
        seen.append([dict(m) for m in messages])
        workdir.mkdir(parents=True, exist_ok=True)
        return {"message": {"content": "```asm\n  processor 6502\n```"}, "done_reason": "stop"}

    def fake_grade(spec, code, workdir, random_seeds=None, fix_syntax=False):
        return {"ok": True, "errors": [], "warnings": []}, Grade(False, "timing", detail="x"), None

    monkeypatch.setattr(case0, "chat", fake_chat)
    monkeypatch.setattr(case0, "grade_attempt", fake_grade)
    monkeypatch.setattr(case0, "model_digest", lambda m: "digest")
    monkeypatch.setattr(case0, "model_template", lambda m: "")
    monkeypatch.setattr(case0, "RACER_CHECK", tmp_path)  # exists
    case0.main(["--model", "m", "--samples", "1", "--mode", "tool-feedback", "--retries", "1",
                "--facts", condition, "--experiment", "e", "--out", str(tmp_path)])
    [job] = (tmp_path / "e" / "jobs").iterdir()
    config = json.loads((job / "config.json").read_text())
    attempts = [json.loads(line) for line in (job / "attempts.jsonl").read_text().splitlines()]
    return job, config, attempts, seen


def test_contributed_run_records_ids_and_snapshot(tmp_path, monkeypatch):
    job, config, attempts, seen = _fake_run(tmp_path, monkeypatch, "contributed")
    selected = facts.select(STORE, "contributed")
    ids = [f.fact_id for f in selected]
    assert job.name == "m-t1-canonical-contributed-tool-feedback"
    assert config["facts"] == "contributed"
    assert config["fact_snapshot"] == facts.snapshot(selected)
    assert config["fact_ids"] == ids
    assert len(attempts) == 2
    assert all(a["fact_ids_in_context"] == ids for a in attempts)
    first = seen[0][0]["content"]
    assert first.startswith("Reference notes:\n[F001] ")
    assert first.endswith("\n\n" + tasks.load("t1", "canonical").prompt)
    assert attempts[0]["prompt"] == first
    # The retry's feedback carries no facts; the notes stay in the first message only.
    assert "Reference notes" not in seen[1][-1]["content"]
    assert seen[1][0]["content"] == first


def test_none_run_is_unchanged(tmp_path, monkeypatch):
    job, config, attempts, seen = _fake_run(tmp_path, monkeypatch, "none")
    assert job.name == "m-t1-canonical-none-tool-feedback"
    assert config["facts"] == "none" and config["fact_snapshot"] is None
    assert seen[0][0]["content"] == tasks.load("t1", "canonical").prompt
    assert all(a["fact_ids_in_context"] == [] for a in attempts)
