"""Case 0 local eval: prompt -> model -> dasm -> racer-check -> attempt log.

The first grid cell (docs/design/experiment-grid.md): Task 1, no facts, a
local Ollama model, one-shot or tool-feedback. Deliberately one task and one
script; the spec format, harness and report get pulled out of this when
Task 2 arrives (beads tbd-818.3, tbd-818.5).

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
import os
import re
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from tbd.tools.assembler import assemble

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures"
HEADERS = FIXTURES / "vendor" / "atari2600"
CHECK_TEMPLATE = FIXTURES / "red_background.check.toml"
RACER_CHECK = Path(
    os.environ.get("TBD_RACER_CHECK")
    or ROOT / "vendor" / "racer" / "target" / "release" / "racer-check"
)
OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")

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

CATEGORIES = ("pass", "timeout", "truncated", "refusal", "no_code", "assemble_error",
              "unsupported_opcode", "crash", "timing", "output_mismatch")
MAX_OUTPUT_TOKENS = 4096   # includes a reasoning model's thinking
REQUEST_TIMEOUT_S = 600
TIMING_PROPS = {"frame_length", "vsync", "vsync_span"}
# Checked and recorded, but not part of the grade: racer's strict, line-aligned
# 3-line VSYNC. The graded VSYNC property is vsync_span (a 2-4 line range).
INFORMATIONAL = {"vsync"}


# ---------------------------------------------------------------- extraction

_FENCE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
_ASM_TAGS = {"asm", "assembly", "dasm", "6502", "s", "nasm", "ca65"}


def extract_code(response: str) -> str | None:
    """The program in the reply: the longest fenced block tagged as assembly,
    else the longest fenced block of any kind, else None. Whether it is valid
    6502 is left to DASM."""
    response = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL)
    blocks = [(tag.strip().lower(), body) for tag, body in _FENCE.findall(response)]
    tagged = [b for t, b in blocks if t in _ASM_TAGS]
    pool = tagged or [b for _, b in blocks]
    return max(pool, key=len) if pool else None


# ---------------------------------------------------------------- grading

@dataclass
class Grade:
    ok: bool
    category: str
    detail: str = ""
    failed: list[str] = field(default_factory=list)
    color: int | None = None
    # Every property in the check file -> "pass" / "fail", once the program ran.
    properties: dict[str, str] = field(default_factory=dict)
    # Failed properties that don't count toward the grade (INFORMATIONAL).
    informational_failed: list[str] = field(default_factory=list)

    @property
    def near_miss(self) -> bool:
        """Assembled, ran, and failed exactly one graded property."""
        return not self.ok and len(self.failed) == 1 and bool(self.properties)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "category": self.category, "detail": self.detail,
                "failed": self.failed, "informational_failed": self.informational_failed,
                "color": self.color, "properties": self.properties,
                "near_miss": self.near_miss}


def parse_failures(report: dict) -> dict[str, dict]:
    """{property name: its first failure}, over every unexpected run."""
    out: dict[str, dict] = {}
    for run in report.get("unexpected", []):
        for f in run.get("failures", []):
            out.setdefault(f["property"], f)
    return out


def describe(f: dict) -> str:
    where = ", ".join(f"{k} {f[k]}" for k in ("frame", "line", "clock") if k in f)
    return f"{f['property']}: {where}: {f.get('observed')}; required {f.get('required')}"


_PROPERTY_BLOCK = re.compile(r"(?ms)^\[\[property\]\]\n(.*?)(?=^\[\[|\Z)")


def property_names(check_toml: str) -> list[str]:
    """Each property's name (its `name`, else its `kind`), in file order."""
    names = []
    for block in _PROPERTY_BLOCK.findall(check_toml):
        m = re.search(r'(?m)^name\s*=\s*"([^"]+)"', block) or \
            re.search(r'(?m)^kind\s*=\s*"([^"]+)"', block)
        if m:
            names.append(m.group(1))
    return names


def categorize(report: dict, names: list[str] | None = None) -> Grade:
    grade = _categorize(report)
    if grade.category not in ("pass", "timing", "output_mismatch"):
        return grade
    if names:
        grade.properties = {n: ("fail" if n in grade.failed else "pass") for n in names}
    info = [n for n in grade.failed if n in INFORMATIONAL]
    if info:
        grade.informational_failed = info
        grade.failed = [n for n in grade.failed if n not in INFORMATIONAL]
        details = [d for d in grade.detail.split("; ") if not any(d.startswith(f"{n}:") for n in info)]
        if not grade.failed:
            grade.ok, grade.category = True, "pass"
            grade.detail = "passed; informational: " + "; ".join(
                d for d in grade.detail.split("; ") if any(d.startswith(f"{n}:") for n in info))
        else:
            grade.category = "timing" if TIMING_PROPS & set(grade.failed) else "output_mismatch"
            grade.detail = "; ".join(details)
    return grade


def _categorize(report: dict) -> Grade:
    if report.get("passed"):
        return Grade(True, "pass")
    errors = [r["error"] for r in report.get("unexpected", []) if r.get("error")]
    if report.get("error"):
        errors.insert(0, report["error"])
    if errors:
        # Undocumented opcodes are allowed (tbd-dpu.10). racer's simulator runs
        # LXA (tbd-818.11); any other one it can't run is a grader gap, not a
        # model failure.
        category = "unsupported_opcode" if "UnknownOpcode" in errors[0] else "crash"
        return Grade(False, category, detail=errors[0])
    failures = parse_failures(report)
    names = sorted(failures)
    category = "timing" if TIMING_PROPS & set(names) else "output_mismatch"
    return Grade(False, category, detail="; ".join(describe(failures[n]) for n in names),
                 failed=names)


_SHOWN_COLOR = re.compile(r"color \$([0-9a-fA-F]{2})")


def red_retry_color(grade: Grade) -> int | None:
    """If only the background failed, and it showed another red, that red."""
    if grade.failed != ["background"]:
        return None
    m = _SHOWN_COLOR.search(grade.detail.split("; required")[0])
    if not m:
        return None
    color = int(m.group(1), 16)
    return color if color in ACCEPTED_COLORS and color != DEFAULT_COLOR else None


def run_racer_check(workdir: Path, color: int) -> dict:
    template = CHECK_TEMPLATE.read_text()
    check = re.sub(r"(?m)^color = 0x[0-9a-fA-F]+$", f"color = 0x{color:02x}", template)
    check = check.replace('rom = "build/red_background.bin"', 'rom = "build/prog.bin"')
    name = f"check-0x{color:02x}"
    (workdir / f"{name}.toml").write_text(check)
    proc = subprocess.run([str(RACER_CHECK), "--json", f"{name}.toml"], cwd=workdir,
                          capture_output=True, text=True, timeout=300)
    raw = {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    if proc.returncode == 2 or not proc.stdout.strip():
        report = {"passed": False, "error": (proc.stderr or proc.stdout).strip()}
    else:
        report = json.loads(proc.stdout.splitlines()[0])
    (workdir / f"{name}.result.json").write_text(json.dumps({"report": report, "raw": raw}, indent=2))
    return report


def grade_program(code: str, workdir: Path) -> tuple[dict, Grade]:
    """Assemble and check one program. Returns (assemble record, grade)."""
    workdir.mkdir(parents=True, exist_ok=True)
    for h in ("vcs.h", "macro.h"):
        shutil.copy(HEADERS / h, workdir / h)
    src = workdir / "prog.asm"
    src.write_text(code)
    asm = assemble(src)
    # Errors name the file by absolute path; the model only needs "prog.asm".
    strip = [str(src.resolve()), str(src)]
    def short(msgs):
        out = []
        for m in msgs[:20]:
            for prefix in strip:
                m = m.replace(prefix, "prog.asm")
            out.append(m)
        return out
    asm_rec = {"ok": asm.ok, "errors": short(asm.errors), "warnings": short(asm.warnings)}
    (workdir / "assemble.json").write_text(json.dumps(
        {**asm_rec, "rom_bytes": len(asm.rom) if asm.rom else 0,
         "errors_raw": asm.errors, "warnings_raw": asm.warnings}, indent=2))
    if not asm.ok:
        return asm_rec, Grade(False, "assemble_error", detail="; ".join(asm_rec["errors"][:5]))
    (workdir / "build").mkdir(exist_ok=True)
    (workdir / "build" / "prog.bin").write_bytes(asm.rom)
    names = property_names(CHECK_TEMPLATE.read_text())
    grade = categorize(run_racer_check(workdir, DEFAULT_COLOR), names)
    if grade.ok:
        grade.color = DEFAULT_COLOR
        return asm_rec, grade
    other = red_retry_color(grade)
    if other is not None:
        regrade = categorize(run_racer_check(workdir, other), names)
        if regrade.ok:
            regrade.color = other
            return asm_rec, regrade
    return asm_rec, grade


# ---------------------------------------------------------------- model

class RequestTimeout(Exception):
    """The request ran past its wall-clock deadline. Carries what arrived."""

    def __init__(self, message: str, partial: dict):
        super().__init__(message)
        self.partial = partial


def ollama_chat(model: str, messages: list[dict], *, temperature: float, seed: int,
                think: bool | None, max_tokens: int = MAX_OUTPUT_TOKENS,
                num_ctx: int | None = None, timeout_s: float = REQUEST_TIMEOUT_S,
                save_to: Path | None = None) -> dict:
    """Streamed chat request. Every chunk is appended to stream.jsonl as it
    arrives, and response.md / thinking.md grow with it, so a timeout or an
    interruption keeps everything generated so far. `timeout_s` is a total
    wall-clock deadline for the whole request."""
    # The context window must hold the conversation plus the whole output.
    num_ctx = num_ctx or max(16384, max_tokens + 4096)
    body = {"model": model, "messages": messages, "stream": True,
            "options": {"temperature": temperature, "seed": seed, "num_ctx": num_ctx,
                        "num_predict": max_tokens}}
    if think is not None:
        body["think"] = think
    if save_to:
        save_to.mkdir(parents=True, exist_ok=True)
        (save_to / "request.json").write_text(json.dumps(body, indent=2))
    content, thinking, chunks, final = [], [], 0, None
    deadline = time.time() + timeout_s

    def partial() -> dict:
        return {"message": {"role": "assistant", "content": "".join(content),
                            "thinking": "".join(thinking)},
                "chunks": chunks, "done": False}

    def flush():
        if save_to:
            (save_to / "response.md").write_text("".join(content))
            if thinking:
                (save_to / "thinking.md").write_text("".join(thinking))

    req = urllib.request.Request(f"{OLLAMA}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    stream_log = open(save_to / "stream.jsonl", "a") if save_to else None
    try:
        # Socket timeout bounds a silent server; the loop enforces the deadline.
        with urllib.request.urlopen(req, timeout=max(1.0, min(timeout_s, 60))) as r:
            for line in r:
                if stream_log:
                    stream_log.write(line.decode())
                    stream_log.flush()
                chunk = json.loads(line)
                chunks += 1
                msg = chunk.get("message", {})
                content.append(msg.get("content", ""))
                thinking.append(msg.get("thinking", "") or "")
                if chunks % 20 == 0:
                    flush()
                if chunk.get("done"):
                    final = chunk
                    break
                if time.time() > deadline:
                    raise RequestTimeout(f"deadline of {timeout_s:g}s passed after {chunks} chunks",
                                         partial())
    except (TimeoutError, OSError) as e:  # socket timeout, connection errors
        flush()
        raise RequestTimeout(f"{type(e).__name__}: {e} after {chunks} chunks", partial()) from e
    except RequestTimeout:
        flush()
        raise
    finally:
        if stream_log:
            stream_log.close()
    flush()
    resp = {**final, "message": partial()["message"], "chunks": chunks}
    if save_to:
        (save_to / "response.json").write_text(json.dumps(resp, indent=2))
    return resp


def model_template(model: str) -> str:
    """The chat template Ollama wraps the messages in (and any default system prompt)."""
    req = urllib.request.Request(f"{OLLAMA}/api/show", data=json.dumps({"model": model}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        info = json.load(r)
    return f"# TEMPLATE\n{info.get('template', '')}\n\n# SYSTEM (model default)\n{info.get('system', '')}\n"


def model_digest(model: str) -> str | None:
    with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=30) as r:
        for m in json.load(r)["models"]:
            if m["name"] == model or m["name"] == f"{model}:latest":
                return m["digest"]
    return None


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
        return claude_chat(args.model, messages, max_tokens=args.max_tokens, effort=args.effort,
                           timeout_s=args.timeout, save_to=workdir, timeout_error=RequestTimeout)
    return ollama_chat(args.model, messages, temperature=args.temperature, seed=seed,
                       think=args.think, max_tokens=args.max_tokens,
                       num_ctx=args.num_ctx, timeout_s=args.timeout, save_to=workdir)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--provider", choices=("ollama", "anthropic"), default="ollama")
    p.add_argument("--model", required=True)
    p.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"), default="high",
                   help="anthropic only: thinking depth (claude-opus-5-5 defaults to medium)")
    p.add_argument("--samples", type=int, default=10)
    p.add_argument("--mode", choices=("one-shot", "tool-feedback"), default="one-shot")
    p.add_argument("--retries", type=int, default=3, help="tool-feedback retries after the first")
    p.add_argument("--temperature", type=float, default=0.7)
    p.add_argument("--max-tokens", type=int, default=MAX_OUTPUT_TOKENS,
                   help="output cap, including a reasoning model's thinking")
    p.add_argument("--num-ctx", type=int, default=None,
                   help="context window (default: max(16384, max-tokens + 4096))")
    p.add_argument("--timeout", type=float, default=REQUEST_TIMEOUT_S,
                   help="wall-clock seconds per request; output so far is kept")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--think", choices=("on", "off", "default"), default="default",
                   help="Ollama think flag for reasoning models")
    p.add_argument("--experiment", default=None)
    p.add_argument("--out", type=Path, default=ROOT / "experiments")
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
        "max_tokens": args.max_tokens,
        "num_ctx": None if anthropic_run else (args.num_ctx or max(16384, args.max_tokens + 4096)),
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
