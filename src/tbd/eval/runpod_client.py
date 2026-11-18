"""Ollama on a RunPod serverless endpoint: the same contract as models.ollama_chat
(chunks saved as they arrive, partial output kept on a timeout), over RunPod's
job API instead of Ollama's HTTP API.

The endpoint runs an Ollama worker that takes {"input": {"messages", "stream",
"options", "think"}} at /run and streams Ollama's own /api/chat JSON lines
through /stream/{job id}. The worker serves the one model it was configured
with (OLLAMA_MODEL on the endpoint); the model each chunk names is checked
against the requested one.

Settings: TBD_RUNPOD_API_KEY and TBD_RUNPOD_ENDPOINT (the endpoint id), each from
the environment, else from the gitignored .env.runpod at the repo root. Both are
per account, so neither belongs in a tracked experiment definition. The key is
never written to a run folder; the endpoint id is recorded as the job's host.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from tbd.eval.models import IDLE_TIMEOUT_S, MAX_OUTPUT_TOKENS, REQUEST_TIMEOUT_S, RequestTimeout
from tbd.paths import ROOT

KEY_ENV = "TBD_RUNPOD_API_KEY"
ENDPOINT_ENV = "TBD_RUNPOD_ENDPOINT"
ENV_FILE = ROOT / ".env.runpod"
API = "https://api.runpod.ai/v2"
POLL_S = 1.0
HTTP_TIMEOUT_S = 60
HTTP_RETRIES = 5        # transient errors (429, 5xx, network) on one call


class RunPodError(Exception):
    """The endpoint can't be used, or a job failed on RunPod."""


def setting(name: str) -> str | None:
    """`name` from the environment, else from .env.runpod (NAME=value lines)."""
    value = os.environ.get(name)
    if not value and ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            key, sep, val = line.strip().partition("=")
            if sep and key.strip() == name:
                value = val.strip().strip("'\"")
    return value or None


def api_key() -> str:
    key = setting(KEY_ENV)
    if not key:
        raise RunPodError(f"no RunPod API key: set {KEY_ENV} or put it in {ENV_FILE.name}")
    return key


def _call(method: str, url: str, body: dict | None = None) -> dict:
    """One API call, retried on transient errors."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"}
    for attempt in range(HTTP_RETRIES):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RunPodError(f"RunPod refused the API key ({e.code})") from e
            if e.code != 429 and e.code < 500 or attempt == HTTP_RETRIES - 1:
                raise RunPodError(f"{method} {url}: HTTP {e.code}: {e.read()[:300]!r}") from e
        except (TimeoutError, OSError) as e:
            if attempt == HTTP_RETRIES - 1:
                raise RunPodError(f"{method} {url}: {type(e).__name__}: {e}") from e
        time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def health(endpoint: str) -> dict:
    """The endpoint's worker and job counts. Free: starts no job."""
    return _call("GET", f"{API}/{endpoint}/health")


def runpod_chat(endpoint: str, model: str, messages: list[dict], *, temperature: float,
                seed: int, think: bool | None, max_tokens: int = MAX_OUTPUT_TOKENS,
                num_ctx: int | None = None, timeout_s: float = REQUEST_TIMEOUT_S,
                idle_timeout_s: float = IDLE_TIMEOUT_S, save_to: Path | None = None) -> dict:
    """Streamed chat request through a RunPod job. `timeout_s` is a total deadline;
    `idle_timeout_s` bounds the time without a new chunk, which includes waiting in
    RunPod's queue and a cold worker loading the model. A job that runs out of
    time is cancelled on RunPod."""
    num_ctx = num_ctx or max(16384, max_tokens + 4096)
    inp = {"messages": messages, "stream": True,
           "options": {"temperature": temperature, "seed": seed, "num_ctx": num_ctx,
                       "num_predict": max_tokens}}
    if think is not None:
        inp["think"] = think
    if save_to:
        save_to.mkdir(parents=True, exist_ok=True)
        (save_to / "request.json").write_text(json.dumps({"endpoint": endpoint, "input": inp},
                                                         indent=2))
    content, thinking, chunks, final = [], [], 0, None
    # Monotonic: see models.ollama_chat.
    start = time.monotonic()
    deadline = start + timeout_s
    last_chunk = start
    job_id = _call("POST", f"{API}/{endpoint}/run", {"input": inp})["id"]
    if save_to:
        (save_to / "runpod_job.txt").write_text(job_id + "\n")

    def partial() -> dict:
        return {"message": {"role": "assistant", "content": "".join(content),
                            "thinking": "".join(thinking)},
                "chunks": chunks, "done": False, "runpod_job_id": job_id}

    def flush():
        if save_to:
            (save_to / "response.md").write_text("".join(content))
            if thinking:
                (save_to / "thinking.md").write_text("".join(thinking))

    def give_up(why: str):
        try:
            _call("POST", f"{API}/{endpoint}/cancel/{job_id}")
        except RunPodError:
            pass  # the timeout is what gets recorded
        flush()
        raise RequestTimeout(why, partial())

    stream_log = open(save_to / "stream.jsonl", "a") if save_to else None
    try:
        status = None
        while final is None:
            now = time.monotonic()
            if now > deadline:
                give_up(f"deadline of {timeout_s:g}s passed after {chunks} chunks")
            if now - last_chunk > idle_timeout_s:
                give_up(f"no output for {idle_timeout_s:g}s (RunPod status {status}) "
                        f"after {chunks} chunks")
            try:
                r = _call("GET", f"{API}/{endpoint}/stream/{job_id}")
            except RunPodError as e:
                give_up(f"polling RunPod failed: {e}")
            status = r.get("status")
            for item in r.get("stream", []):
                out = item.get("output")
                lines = out.splitlines() if isinstance(out, str) else [json.dumps(out)]
                for line in filter(None, (ln.strip() for ln in lines)):
                    if stream_log:
                        stream_log.write(line + "\n")
                        stream_log.flush()
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise RunPodError(f"Ollama on RunPod: {chunk['error']}")
                    served = chunk.get("model")
                    if served and served.split(":latest")[0] != model.split(":latest")[0]:
                        _call("POST", f"{API}/{endpoint}/cancel/{job_id}")
                        raise RunPodError(f"endpoint {endpoint} serves {served}, not {model}")
                    chunks += 1
                    last_chunk = time.monotonic()
                    msg = chunk.get("message", {})
                    content.append(msg.get("content", ""))
                    thinking.append(msg.get("thinking", "") or "")
                    if chunk.get("done"):
                        final = chunk
            flush()
            if final is None and status in ("FAILED", "CANCELLED", "TIMED_OUT"):
                detail = _call("GET", f"{API}/{endpoint}/status/{job_id}")
                raise RunPodError(f"RunPod job {job_id} {status}: "
                                  f"{str(detail.get('error') or detail)[:500]}")
            if final is None and status == "COMPLETED":
                raise RunPodError(f"RunPod job {job_id} completed without a final chunk")
            if final is None:
                time.sleep(POLL_S)
    except KeyboardInterrupt:
        try:
            _call("POST", f"{API}/{endpoint}/cancel/{job_id}")
        finally:
            raise
    finally:
        if stream_log:
            stream_log.close()
    flush()
    info = _call("GET", f"{API}/{endpoint}/status/{job_id}")
    resp = {**final, "message": partial()["message"], "chunks": chunks,
            "served_model": final.get("model", model),
            "runpod": {"job_id": job_id, "worker_id": info.get("workerId"),
                       "delay_ms": info.get("delayTime"),
                       "execution_ms": info.get("executionTime")}}
    if save_to:
        (save_to / "response.json").write_text(json.dumps(resp, indent=2))
    return resp
