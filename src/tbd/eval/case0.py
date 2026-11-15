"""Case 0 local eval: prompt -> model -> dasm -> racer-check -> attempt log.

The first grid cell (docs/design/experiment-grid.md): Task 1, no facts, a
local Ollama model, one-shot or tool-feedback. This file is Task 1 and the
runner; the task-agnostic parts are tbd.eval.grading (extract, assemble,
racer-check, categorize) and tbd.eval.models / claude_client (the model).
Task 1's data is in tasks/t1/. The task spec format and a generic harness
come with Task 2 (beads tbd-818.3, tbd-818.5).

    python -m tbd.eval.case0 --model llama3.1 --samples 10
    python -m tbd.eval.case0 --model qwen3.5 --samples 10 --mode tool-feedback

Output, one folder per job and one per attempt. Runs are never deleted or
overwritten: an existing job folder is an error; pick a new --experiment.

    experiments/{experiment_id}/manifest.json
    experiments/{experiment_id}/jobs/{job_id}/
        config.json            the cell: model, digest, options, prompt, grader
        model_template.txt     the chat template Ollama wraps messages in
        metrics.json           status (running / completed / interrupted / failed) + results
        attempts.jsonl         one line per attempt (same as each attempt.json)
        sample-NN/attempt-N/
            request.json       exact body sent to Ollama
            response.json      Ollama's raw reply (content, thinking, timings, done_reason)
            response.md        the reply text
            prog.asm           extracted program (absent if none)
            assemble.json      DASM result
            check-0xNN.toml    check file used, per color tried
            check-0xNN.result.json  racer-check's raw JSON
            attempt.json       the attempt record
            error.txt          if the request failed
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from tbd.eval import grading
from tbd.eval.grading import (Grade, assemble_program, extract_code, property_names,
                              run_racer_check, with_rom)
from tbd.eval.models import (MAX_OUTPUT_TOKENS, REQUEST_TIMEOUT_S, RequestTimeout,
                             model_digest, model_template, ollama_chat)
from tbd.paths import RACER_CHECK, ROOT, TASKS

TASK_DIR = TASKS / "t1"
CHECK_TEMPLATE = TASK_DIR / "red_background.check.toml"
# Relaxed tier: run when the strict check fails; passing it is a near miss.
RELAXED_TEMPLATE = TASK_DIR / "red_background.relaxed.check.toml"

TASK = "Create a kernel with stable framerate that sets the screen background to red."
# Says which assembler and headers, so a wrong dialect isn't what's measured.
# Says nothing about the frame layout or which color value is red.
PROMPT = (
    f"{TASK}\n\n"
    "Target: Atari 2600, NTSC. Write 6502 assembly for the DASM assembler. "
    'The standard headers are available: include "vcs.h" and include "macro.h". '
    "Reply with the complete program in a single ```asm code block."
)

# The canonical prompt says "red", not a value: accept $42-$46 inclusive
# (NTSC hue 4, the three luminances around the reference's $42; the TIA
# ignores bit 0, so $43/$45 show as $42/$44). Variants (tbd-dpu.2) pin exact
# values.
ACCEPTED_COLORS = range(0x42, 0x47)
DEFAULT_COLOR = 0x42

# Checked and recorded, but not part of the grade: racer's strict, line-aligned
# 3-line VSYNC. The graded VSYNC property is vsync_span (a 2-4 line range).
INFORMATIONAL = {"vsync"}


# ---------------------------------------------------------------- grading

def categorize(report: dict, names: list[str] | None = None) -> Grade:
    """grading.categorize with Task 1's informational properties."""
    return grading.categorize(report, names, INFORMATIONAL)


_SHOWN_COLOR = re.compile(r"color \$([0-9a-fA-F]{2})")


def red_retry_color(grade: Grade, prop: str = "background") -> int | None:
    """If `prop` failed showing another accepted red, that red. Independent of
    whatever else failed."""
    if prop not in grade.failed:
        return None
    start = grade.detail.find(f"{prop}:")
    m = _SHOWN_COLOR.search(grade.detail[start:].split("; required")[0]) if start >= 0 else None
    if not m:
        return None
    color = int(m.group(1), 16)
    return color if color in ACCEPTED_COLORS and color != DEFAULT_COLOR else None


def check_file(template_path: Path, color: int) -> str:
    """A Task 1 check file asking for `color`, pointed at the attempt's ROM."""
    template = template_path.read_text()
    return with_rom(re.sub(r"(?m)^color = 0x[0-9a-fA-F]+$", f"color = 0x{color:02x}", template))


def check_red(workdir: Path, template_path: Path, prefix: str, prop: str, color: int) -> Grade:
    """Run a check file at `color`. If `prop` failed showing another accepted
    red, run it again at that red and keep that run when `prop` then passes."""
    names = property_names(template_path.read_text())

    def run(c: int) -> Grade:
        report = run_racer_check(workdir, check_file(template_path, c), f"{prefix}-0x{c:02x}")
        g = categorize(report, names)
        g.color = c
        return g

    g = run(color)
    other = red_retry_color(g, prop)
    if other is not None and other != color:
        retry = run(other)
        if prop not in retry.failed:
            g = retry
    return g


def grade_program(code: str, workdir: Path) -> tuple[dict, Grade]:
    """Assemble and check one program. Returns (assemble record, grade)."""
    asm_rec, failed = assemble_program(code, workdir)
    if failed:
        return asm_rec, failed
    grade = check_red(workdir, CHECK_TEMPLATE, "check", "background", DEFAULT_COLOR)
    if not grade.ok and grade.color == DEFAULT_COLOR:
        grade.color = None  # recorded only when a red was accepted
    if grade.category in ("timing", "output_mismatch"):
        grade.relaxed = grade_relaxed(workdir, grade.color or DEFAULT_COLOR)
    return asm_rec, grade


def grade_relaxed(workdir: Path, color: int) -> dict:
    """The relaxed tier, with the same $42-$46 color handling as the strict one."""
    g = check_red(workdir, RELAXED_TEMPLATE, "relaxed", "picture", color)
    return {"passed": g.ok, "failed": g.failed, "properties": g.properties,
            "color": g.color, "detail": g.detail}


# ---------------------------------------------------------------- model

def feedback(asm_rec: dict, grade: Grade) -> str:
    if grade.category == "no_code":
        what = "I couldn't find a complete program in a single ```asm code block."
    elif grade.category == "assemble_error":
        what = "DASM reported errors:\n" + "\n".join(asm_rec["errors"][:10])
    else:
        what = f"It assembled, but running it in an emulator failed: {grade.detail}"
    return f"{what}\n\nFix it and reply with the complete program in a single ```asm code block."


# ---------------------------------------------------------------- run

def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True).stdout.strip()
    except OSError:
        return ""


def run_job(args, sample: int, job_dir: Path, log) -> dict:
    seed = args.seed + sample
    messages = [{"role": "user", "content": PROMPT}]
    budget = 1 if args.mode == "one-shot" else 1 + args.retries
    for attempt_no in range(1, budget + 1):
        t0 = time.time()
        workdir = job_dir / f"sample-{sample:02d}" / f"attempt-{attempt_no}"
        try:
            resp = chat(args, messages, seed, workdir)
        except RequestTimeout as e:
            resp = {**e.partial, "error": str(e)}
            workdir.mkdir(parents=True, exist_ok=True)
            (workdir / "error.txt").write_text(resp["error"] + "\n")
        wall_ms = int((time.time() - t0) * 1000)
        text = resp.get("message", {}).get("content", "")
        thinking = resp.get("message", {}).get("thinking", "")
        code = extract_code(text)
        empty = {"ok": False, "errors": [], "warnings": []}
        if "error" in resp:
            asm_rec, grade = empty, Grade(False, "timeout", detail=resp["error"])
            workdir.mkdir(parents=True, exist_ok=True)
        elif resp.get("done_reason") == "refusal":
            asm_rec, grade = empty, Grade(False, "refusal")
            workdir.mkdir(parents=True, exist_ok=True)
        elif code is None and resp.get("done_reason") in ("length", "max_tokens"):
            asm_rec, grade = empty, Grade(False, "truncated")
            workdir.mkdir(parents=True, exist_ok=True)
        elif code is None:
            asm_rec, grade = {"ok": False, "errors": [], "warnings": []}, Grade(False, "no_code")
            workdir.mkdir(parents=True, exist_ok=True)
        else:
            asm_rec, grade = grade_program(code, workdir)
        record = {
            "sample": sample, "seed": seed, "attempt_no": attempt_no,
            "prompt": messages[-1]["content"], "response": text, "thinking": thinking,
            "done_reason": resp.get("done_reason"), "code": code,
            "fact_ids_in_context": [], "assemble": asm_rec, "grade": grade.to_dict(),
            "tokens": {"prompt": resp.get("prompt_eval_count"), "output": resp.get("eval_count"),
                       "chunks_received": resp.get("chunks"),
                       "tokens_per_s": round(resp["eval_count"] / (resp["eval_duration"] / 1e9), 1)
                       if resp.get("eval_duration") else None},
            "wall_ms": wall_ms,
            "provider": args.provider, "requested_model": args.model,
            "served_model": resp.get("served_model", args.model),
            "fallback_ran": resp.get("fallback_ran", False), "cost_usd": resp.get("cost_usd"),
        }
        (workdir / "attempt.json").write_text(json.dumps(record, indent=2))
        log.write(json.dumps(record) + "\n")
        log.flush()
        print(f"  sample {sample} attempt {attempt_no}: {grade.category}"
              + (f" ({grade.detail[:100]})" if grade.detail else ""), flush=True)
        if attempt_no == 1:
            first = grade.category
            first_near = grade.near_miss
        if grade.category == "timeout":
            return {"passed": False, "attempts": attempt_no, "first_category": first,
                    "near_miss": first_near}
        if grade.ok:
            return {"passed": True, "attempts": attempt_no, "first_category": None,
                    "near_miss": False}
        messages += [{"role": "assistant", "content": resp.get("assistant_content") or text},
                     {"role": "user", "content": feedback(asm_rec, grade)}]
    return {"passed": False, "attempts": budget, "first_category": first,
            "near_miss": first_near}


def chat(args, messages: list[dict], seed: int, workdir: Path) -> dict:
    if args.provider == "anthropic":
        from tbd.eval.claude_client import claude_chat
        return claude_chat(args.model, messages, max_tokens=effective_max_tokens(args),
                           effort=args.effort,
                           timeout_s=args.timeout, save_to=workdir)
    return ollama_chat(args.model, messages, temperature=args.temperature, seed=seed,
                       think=args.think, max_tokens=effective_max_tokens(args),
                       num_ctx=args.num_ctx, timeout_s=args.timeout, save_to=workdir)


# Claude requests stream, so a large cap is safe and only what's used is billed;
# 4096 truncated Claude Haiku 5.5 mid-thinking. Local models keep 4096.
ANTHROPIC_MAX_TOKENS = 64000


def effective_max_tokens(args) -> int:
    if args.max_tokens:
        return args.max_tokens
    return ANTHROPIC_MAX_TOKENS if args.provider == "anthropic" else MAX_OUTPUT_TOKENS


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--provider", choices=("ollama", "anthropic"), default="ollama")
    p.add_argument("--model", required=True)
    p.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"), default="high",
                   help="anthropic only: thinking depth (claude-opus-5-5 defaults to medium)")
    p.add_argument("--samples", type=int, default=10)
    p.add_argument("--mode", choices=("one-shot", "tool-feedback"), default="one-shot")
    p.add_argument("--retries", type=int, default=3, help="tool-feedback retries after the first")
    p.add_argument("--temperature", type=float, default=0.7)
    p.add_argument("--max-tokens", type=int, default=None,
                   help=f"output cap, including thinking (default: {ANTHROPIC_MAX_TOKENS} for "
                        f"anthropic, {MAX_OUTPUT_TOKENS} for ollama)")
    p.add_argument("--num-ctx", type=int, default=None,
                   help="context window (default: max(16384, max-tokens + 4096))")
    p.add_argument("--timeout", type=float, default=REQUEST_TIMEOUT_S,
                   help="wall-clock seconds per request; output so far is kept")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--think", choices=("on", "off", "default"), default="default",
                   help="Ollama think flag for reasoning models")
    p.add_argument("--experiment", default=None)
    p.add_argument("--out", type=Path, default=ROOT / "experiments")
    return p


def main(argv: list[str] | None = None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    args.think = {"on": True, "off": False, "default": None}[args.think]

    if not RACER_CHECK.exists():
        p.error(f"racer-check not found at {RACER_CHECK}; see vendor/racer/NOTICE.md")

    if args.provider == "anthropic":
        # The SDK only notices missing credentials when it sends a request, so
        # make one free call (model lookup) before any run folder exists.
        try:
            import anthropic
        except ImportError:
            p.error("the anthropic SDK isn't installed: pip install -e '.[eval]'")
        try:
            from tbd.eval.claude_client import make_client
            make_client(anthropic).models.retrieve(args.model)
        except (anthropic.AnthropicError, TypeError) as e:
            p.error(f"Anthropic API not usable for {args.model}: {type(e).__name__}: {e}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    exp_id = args.experiment or f"case0-{stamp}"
    exp_dir = args.out / exp_id
    job_id = f"{args.model.replace(':', '_').replace('/', '_')}-t1-canonical-none-{args.mode}"
    job_dir = exp_dir / "jobs" / job_id
    if job_dir.exists():
        p.error(f"{job_dir} already exists; runs are never overwritten. Use a new --experiment.")
    job_dir.mkdir(parents=True)

    anthropic_run = args.provider == "anthropic"
    config = {
        "provider": args.provider,
        "model": args.model,
        "model_digest": None if anthropic_run else model_digest(args.model),
        "runtime": "anthropic-api" if anthropic_run else "ollama",
        "facts": "none", "fact_snapshot": None, "task": "t1", "variant": "canonical",
        "mode": args.mode, "samples": args.samples,
        # Claude takes no temperature or seed; record that instead of a value we didn't send.
        "temperature": None if anthropic_run else args.temperature,
        "seed": None if anthropic_run else args.seed,
        "effort": args.effort if anthropic_run else None,
        "thinking": {"type": "adaptive", "display": "summarized"} if anthropic_run else args.think, "retries": args.retries if args.mode == "tool-feedback" else 0,
        "max_tokens": effective_max_tokens(args),
        "num_ctx": None if anthropic_run else (args.num_ctx or max(16384, effective_max_tokens(args) + 4096)),
        "request_timeout_s": args.timeout, "prompt": PROMPT, "grader": "racer-check",
        "check_template": str(CHECK_TEMPLATE.relative_to(ROOT)),
        "accepted_colors": [f"0x{c:02x}" for c in ACCEPTED_COLORS],
    }
    manifest = exp_dir / "manifest.json"
    if not manifest.exists():
        manifest.write_text(json.dumps({"experiment_id": exp_id, "created": stamp,
                                        "git_sha": git_sha()}, indent=2))
    (job_dir / "config.json").write_text(json.dumps(config, indent=2))
    if not anthropic_run:
        (job_dir / "model_template.txt").write_text(model_template(args.model))

    print(f"{exp_id}/{job_id}: {args.samples} samples -> {job_dir}", flush=True)
    t0 = time.time()
    results: list[dict] = []
    status = "running"
    write_metrics(job_dir, args, results, status, t0)
    try:
        with open(job_dir / "attempts.jsonl", "a") as log:
            for s in range(args.samples):
                results.append(run_job(args, s, job_dir, log))
                write_metrics(job_dir, args, results, status, t0)
        status = "completed"
    except KeyboardInterrupt:
        status = "interrupted"
        raise
    except BaseException as e:
        status = "failed"
        error = f"{type(e).__name__}: {e}"
        raise
    finally:
        metrics = write_metrics(job_dir, args, results, status, t0, locals().get("error"))
        print(json.dumps(metrics, indent=2))
    return 0


def write_metrics(job_dir: Path, args, results: list[dict], status: str, t0: float,
                  error: str | None = None) -> dict:
    done = len(results)
    passed = sum(r["passed"] for r in results)
    firsts: dict[str, int] = {}
    for r in results:
        key = "pass" if r["passed"] and r["attempts"] == 1 else (r["first_category"] or "pass")
        firsts[key] = firsts.get(key, 0) + 1
    metrics = {
        "status": status, "error": error, "samples_planned": args.samples, "samples_done": done,
        "passed": passed, "pass_rate": passed / done if done else None,
        "pass_at_1_first_attempt": (sum(r["passed"] and r["attempts"] == 1 for r in results) / done
                                    if done else None),
        "first_attempt_categories": firsts,
        "near_misses": sum(bool(r.get("near_miss")) for r in results),
        "attempts_total": sum(r["attempts"] for r in results),
        "wall_s": round(time.time() - t0, 1),
    }
    (job_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    return metrics


if __name__ == "__main__":
    raise SystemExit(main())
