"""Case 0 local eval: prompt -> model -> dasm -> racer-check -> attempt log.

One grid cell (docs/design/experiment-grid.md): a task variant, a fact
condition, a model, one-shot or tool-feedback. The task comes from
tasks/<task>/<variant>/ (tbd.eval.tasks, docs/design/task-spec.md); grading is
tbd.eval.checks and tbd.eval.grading; the model is tbd.eval.models /
claude_client. Facts (tbd.eval.facts, facts/README.md) go in the first user
message as a "Reference notes" block before the task prompt; --facts none
(the default) sends the prompt unchanged. A generic harness replaces this
runner in tbd-818.5.

    python -m tbd.eval.case0 --model llama3.1 --samples 10
    python -m tbd.eval.case0 --model qwen3.5 --task t1 --variant canonical --mode tool-feedback
    python -m tbd.eval.case0 --model qwen3.5 --facts contributed

Output, one folder per job and one per attempt. Runs are never deleted or
overwritten: an existing job folder is an error; pick a new --experiment.
The contract in full: docs/design/execution.md, "Execution contract".

    experiments/{experiment_id}/manifest.json   git SHA, toolchain (image digest, DASM, racer-check)
    experiments/{experiment_id}/jobs/{job_id}/
        config.json            the cell: model, digest, options, prompt, grader, toolchain
        model_template.txt     the chat template Ollama wraps messages in
        metrics.json           status (running / completed / interrupted / failed) + results;
                               pid, host and updated_at, rewritten after every attempt
        attempts.jsonl         one line per attempt (same as each attempt.json)
        console.log            everything the job printed (stdout and stderr together)
        sample-NN/attempt-N/
            request.json       exact body sent to Ollama
            response.json      Ollama's raw reply (content, thinking, timings, done_reason)
            response.md        the reply text
            thinking.md        the model's thinking, if any
            stream.jsonl       streamed chunks as received
            prog.asm           extracted program (absent if none)
            assemble.json      DASM result
            <check>.toml       each check file run (and any accepted-color re-run)
            <check>.result.json  racer-check's raw JSON
            attempt.json       the attempt record
            error.txt          if the request failed
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from tbd.eval import facts, tasks
from tbd.eval.checks import grade_program
from tbd.eval.grading import Grade, extract_code
from tbd.eval.jobrecords import console_log, toolchain
from tbd.eval.models import (IDLE_TIMEOUT_S, MAX_OUTPUT_TOKENS, OLLAMA, REQUEST_TIMEOUT_S,
                             RequestTimeout, model_digest, model_template, ollama_chat)
from tbd.paths import RACER_CHECK, ROOT


# ---------------------------------------------------------------- model

# A cut-off reply is often a repetition loop as long as the output cap. Echoing all of it
# back fills the context window (Ollama then silently drops the oldest tokens, prompt
# first), so the retry sees only its start. The full reply is still saved with the attempt.
TRUNCATED_ECHO_CHARS = 2000


def feedback(asm_rec: dict, grade: Grade) -> str:
    if grade.category == "truncated":
        what = ("Your reply hit the output length limit before the program was complete, "
                "so there was no complete ```asm code block to assemble.")
    elif grade.category == "refusal":
        what = "There was no program in your reply."
    elif grade.category == "no_code":
        what = "I couldn't find a complete program in a single ```asm code block."
    elif grade.category == "assemble_error":
        what = "DASM reported errors:\n" + "\n".join(asm_rec["errors"][:10])
    else:
        what = f"It assembled, but running it in an emulator failed: {grade.detail}"
    return f"{what}\n\nFix it and reply with the complete program in a single ```asm code block."


def echoed_reply(resp: dict, text: str, grade: Grade):
    """The assistant turn sent back on a retry: the reply as given, unless it was cut off."""
    if grade.category == "truncated" and len(text) > TRUNCATED_ECHO_CHARS:
        return text[:TRUNCATED_ECHO_CHARS] + "\n[... reply cut off at the output length limit]"
    return resp.get("assistant_content") or text


# ---------------------------------------------------------------- run

def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True).stdout.strip()
    except OSError:
        return ""


def run_job(args, sample: int, job_dir: Path, log, on_attempt=None) -> dict:
    """One sample. on_attempt() is called after each attempt is logged (the heartbeat)."""
    seed = args.seed + sample
    messages = [{"role": "user", "content": first_message(args)}]
    budget = 1 if args.mode == "one-shot" else 1 + args.retries
    for attempt_no in range(1, budget + 1):
        t0 = time.monotonic()
        workdir = job_dir / f"sample-{sample:02d}" / f"attempt-{attempt_no}"
        try:
            resp = chat(args, messages, seed, workdir)
        except RequestTimeout as e:
            resp = {**e.partial, "error": str(e)}
            workdir.mkdir(parents=True, exist_ok=True)
            (workdir / "error.txt").write_text(resp["error"] + "\n")
        wall_ms = int((time.monotonic() - t0) * 1000)
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
            asm_rec, grade = grade_program(args.spec, code, workdir, args.random_seeds)
        record = {
            "sample": sample, "seed": seed, "attempt_no": attempt_no,
            "prompt": messages[-1]["content"], "response": text, "thinking": thinking,
            "done_reason": resp.get("done_reason"), "code": code,
            "fact_ids_in_context": list(args.fact_ids), "assemble": asm_rec, "grade": grade.to_dict(),
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
        if on_attempt:
            on_attempt()
        if attempt_no == 1:
            first = grade.category
            first_near = grade.near_miss
        if grade.category == "timeout":
            return {"passed": False, "attempts": attempt_no, "first_category": first,
                    "near_miss": first_near}
        if grade.ok:
            return {"passed": True, "attempts": attempt_no, "first_category": None,
                    "near_miss": False}
        messages += [{"role": "assistant", "content": echoed_reply(resp, text, grade)},
                     {"role": "user", "content": feedback(asm_rec, grade)}]
    return {"passed": False, "attempts": budget, "first_category": first,
            "near_miss": first_near}


def first_message(args) -> str:
    """The task prompt, after the condition's Reference notes (none: the prompt as is).
    Retries add tool feedback only; the notes stay in the first message."""
    return facts.with_facts(args.spec.prompt, args.selected_facts)


def chat(args, messages: list[dict], seed: int, workdir: Path) -> dict:
    if args.provider == "anthropic":
        from tbd.eval.claude_client import claude_chat
        return claude_chat(args.model, messages, max_tokens=effective_max_tokens(args),
                           effort=args.effort,
                           timeout_s=args.timeout, save_to=workdir)
    if args.provider == "runpod":
        from tbd.eval.runpod_client import runpod_chat
        return runpod_chat(args.endpoint, args.model, messages, temperature=args.temperature,
                           seed=seed, think=args.think, max_tokens=effective_max_tokens(args),
                           num_ctx=args.num_ctx, timeout_s=args.timeout,
                           idle_timeout_s=args.idle_timeout, save_to=workdir)
    return ollama_chat(args.model, messages, temperature=args.temperature, seed=seed,
                       think=args.think, max_tokens=effective_max_tokens(args),
                       num_ctx=args.num_ctx, timeout_s=args.timeout,
                       idle_timeout_s=args.idle_timeout, save_to=workdir)


# Claude requests stream, so a large cap is safe and only what's used is billed;
# 4096 truncated Claude Haiku 5.5 mid-thinking. Local models keep 4096.
ANTHROPIC_MAX_TOKENS = 64000


def model_host(args) -> str | None:
    """Where the model was served: the Ollama URL, or runpod:<endpoint id>."""
    if args.provider == "runpod":
        return f"runpod:{args.endpoint}"
    if args.provider == "ollama":
        from urllib.parse import urlsplit
        u = urlsplit(OLLAMA)  # drop any credentials in the URL
        return f"{u.scheme}://{u.hostname}" + (f":{u.port}" if u.port else "")
    return None


def effective_max_tokens(args) -> int:
    if args.max_tokens:
        return args.max_tokens
    return ANTHROPIC_MAX_TOKENS if args.provider == "anthropic" else MAX_OUTPUT_TOKENS


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--provider", choices=("ollama", "anthropic", "runpod"), default="ollama",
                   help="runpod: Ollama on a RunPod serverless endpoint (--endpoint)")
    p.add_argument("--endpoint", default=None,
                   help="runpod only: the serverless endpoint id (default: TBD_RUNPOD_ENDPOINT "
                        "from the environment or .env.runpod)")
    p.add_argument("--model", required=True)
    p.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"), default="high",
                   help="anthropic only: thinking depth (claude-opus-5-5 defaults to medium)")
    p.add_argument("--task", default="t1")
    p.add_argument("--variant", default="canonical")
    p.add_argument("--samples", type=int, default=10)
    p.add_argument("--facts", choices=tuple(facts.CONDITIONS), default="none",
                   help="fact condition: which facts/ entries go in the first message")
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
    p.add_argument("--idle-timeout", type=float, default=IDLE_TIMEOUT_S,
                   help="ollama only: longest silence from the server, including the wait "
                        "for the first token while the model loads (capped at --timeout)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--random-seeds", type=int, default=None,
                   help="random power-on states each check runs, instead of the check "
                        "file's random_seeds (fewer grade faster; regrade.py can redo "
                        "saved attempts with the full count)")
    p.add_argument("--think", choices=("on", "off", "default"), default="default",
                   help="Ollama think flag for reasoning models")
    p.add_argument("--experiment", default=None)
    p.add_argument("--out", type=Path, default=ROOT / "experiments")
    return p


def main(argv: list[str] | None = None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    args.think = {"on": True, "off": False, "default": None}[args.think]

    try:
        args.spec = tasks.load(args.task, args.variant)
    except FileNotFoundError as e:
        p.error(str(e))

    try:
        args.selected_facts = facts.select(facts.load(), args.facts)
    except facts.FactError as e:
        p.error(f"fact store: {e}")
    if args.facts != "none" and not args.selected_facts:
        p.error(f"--facts {args.facts}: the fact store has no facts for that condition")
    args.fact_ids = [f.fact_id for f in args.selected_facts]

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

    if args.provider == "runpod":
        # Check the endpoint and key before any run folder exists.
        from tbd.eval.runpod_client import ENDPOINT_ENV, RunPodError, health, setting
        args.endpoint = args.endpoint or setting(ENDPOINT_ENV)
        if not args.endpoint:
            p.error(f"--provider runpod needs --endpoint, or {ENDPOINT_ENV} in the "
                    "environment or .env.runpod")
        try:
            health(args.endpoint)
        except RunPodError as e:
            p.error(f"RunPod endpoint {args.endpoint} not usable: {e}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    exp_id = args.experiment or f"case0-{stamp}"
    exp_dir = args.out / exp_id
    job_id = f"{args.model.replace(':', '_').replace('/', '_')}-{args.task}-{args.variant}-{args.facts}-{args.mode}"
    job_dir = exp_dir / "jobs" / job_id
    if job_dir.exists():
        p.error(f"{job_dir} already exists; runs are never overwritten. Use a new --experiment.")
    job_dir.mkdir(parents=True)

    anthropic_run = args.provider == "anthropic"
    runpod_run = args.provider == "runpod"
    config = {
        "provider": args.provider,
        "model": args.model,
        # The RunPod worker doesn't expose Ollama's model list, so no digest there.
        "model_digest": None if anthropic_run or runpod_run else model_digest(args.model),
        "runtime": "anthropic-api" if anthropic_run else "ollama",
        "model_host": model_host(args),
        "facts": args.facts, "fact_snapshot": facts.snapshot(args.selected_facts),
        "fact_ids": args.fact_ids, "task": args.task, "variant": args.variant,
        "mode": args.mode, "samples": args.samples,
        # Claude takes no temperature or seed; record that instead of a value we didn't send.
        "temperature": None if anthropic_run else args.temperature,
        "seed": None if anthropic_run else args.seed,
        "effort": args.effort if anthropic_run else None,
        "thinking": {"type": "adaptive", "display": "summarized"} if anthropic_run else args.think, "retries": args.retries if args.mode == "tool-feedback" else 0,
        "max_tokens": effective_max_tokens(args),
        "num_ctx": None if anthropic_run else (args.num_ctx or max(16384, effective_max_tokens(args) + 4096)),
        "request_timeout_s": args.timeout,
        "idle_timeout_s": None if anthropic_run else args.idle_timeout, "prompt": args.spec.prompt, "grader": "racer-check",
        "task_dir": str(args.spec.dir.relative_to(ROOT)),
        # None: each check file's own random_seeds.
        "random_seeds": args.random_seeds,
        "check_files": args.spec.check_files,
        "check_py": args.spec.module is not None,
        # Per job: the manifest only has the experiment's first job's toolchain.
        "toolchain": toolchain(),
    }
    manifest = exp_dir / "manifest.json"
    if not manifest.exists():
        manifest.write_text(json.dumps({"experiment_id": exp_id, "created": stamp,
                                        "git_sha": git_sha(), "toolchain": config["toolchain"]},
                                       indent=2))
    (job_dir / "config.json").write_text(json.dumps(config, indent=2))
    if args.provider == "ollama":
        (job_dir / "model_template.txt").write_text(model_template(args.model))
    with console_log(job_dir):
        return run_cell(args, exp_id, job_id, job_dir)


def run_cell(args, exp_id: str, job_id: str, job_dir: Path) -> int:
    """Run the job's samples, keeping metrics.json current."""
    print(f"{exp_id}/{job_id}: {args.samples} samples -> {job_dir}", flush=True)
    t0 = time.monotonic()
    results: list[dict] = []
    status = "running"
    write_metrics(job_dir, args, results, status, t0)
    try:
        with sigterm_as_interrupt(), open(job_dir / "attempts.jsonl", "a") as log:
            for s in range(args.samples):
                results.append(run_job(args, s, job_dir, log,
                                       lambda: write_metrics(job_dir, args, results, status, t0)))
                write_metrics(job_dir, args, results, status, t0)
        status = "completed"
    except KeyboardInterrupt:   # Ctrl-C, or SIGTERM via sigterm_as_interrupt
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


class Terminated(KeyboardInterrupt):
    """SIGTERM (e.g. a shell timeout): ends the job like Ctrl-C, as "interrupted"."""


@contextmanager
def sigterm_as_interrupt():
    """Raise Terminated on SIGTERM while the block runs; restore the old handler after.

    SIGKILL can't be caught; readers spot that job by its stale heartbeat
    (tbd.eval.report.stale_reason)."""
    def handler(signum, frame):
        raise Terminated(f"signal {signum}")
    try:
        previous = signal.signal(signal.SIGTERM, handler)
    except ValueError:   # not the main thread: leave signals alone
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


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
        "wall_s": round(time.monotonic() - t0, 1),
        # Heartbeat: a "running" job whose updated_at stops moving, or whose pid is
        # gone, was killed without the chance to say so (tbd.eval.report.stale_reason).
        "pid": os.getpid(), "host": socket.gethostname(),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    # Write then rename, so a reader (the grid report) never sees a half-written file.
    tmp = job_dir / "metrics.json.tmp"
    tmp.write_text(json.dumps(metrics, indent=2))
    os.replace(tmp, job_dir / "metrics.json")
    return metrics


if __name__ == "__main__":
    raise SystemExit(main())
