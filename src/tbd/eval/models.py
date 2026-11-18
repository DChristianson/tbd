"""Local models through Ollama: a streamed chat request that keeps partial
output on disk, plus the model's identity (digest, chat template).

claude_client.claude_chat has the same contract for the Anthropic API.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
MAX_OUTPUT_TOKENS = 4096   # includes a reasoning model's thinking
REQUEST_TIMEOUT_S = 600
# Longest silence allowed from Ollama. The first byte only arrives once the
# model is loaded, and Ollama may first have to finish with and unload another
# model, so this must cover a cold load and a wait in Ollama's queue.
IDLE_TIMEOUT_S = 300


class RequestTimeout(Exception):
    """The request ran past its wall-clock deadline. Carries what arrived."""

    def __init__(self, message: str, partial: dict):
        super().__init__(message)
        self.partial = partial


def ollama_chat(model: str, messages: list[dict], *, temperature: float, seed: int,
                think: bool | None, max_tokens: int = MAX_OUTPUT_TOKENS,
                num_ctx: int | None = None, timeout_s: float = REQUEST_TIMEOUT_S,
                idle_timeout_s: float = IDLE_TIMEOUT_S, save_to: Path | None = None) -> dict:
    """Streamed chat request. Every chunk is appended to stream.jsonl as it
    arrives, and response.md / thinking.md grow with it, so a timeout or an
    interruption keeps everything generated so far. `timeout_s` is a total
    wall-clock deadline for the whole request. `idle_timeout_s` bounds a silent
    server: the wait for any one read, including the wait for the first token
    while the model loads."""
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
    # Monotonic, not wall-clock: the dev container's wall clock can be stepped
    # forward by many minutes (e.g. after the Mac sleeps), which would end a
    # request that has only run for seconds.
    deadline = time.monotonic() + timeout_s

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
        # The socket timeout bounds a silent server; the loop enforces the deadline.
        socket_timeout = max(1.0, min(timeout_s, idle_timeout_s))
        with urllib.request.urlopen(req, timeout=socket_timeout) as r:
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
                if time.monotonic() > deadline:
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
