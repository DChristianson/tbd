"""Claude through the Anthropic Messages API, for tbd.eval (beads tbd-1nx.6).

Same contract as case0.ollama_chat: streamed, every event appended to
stream.jsonl as it arrives, response.md / thinking.md growing with it, a
wall-clock deadline that keeps the partial output, and a returned dict using
the same keys the attempt record reads (prompt_eval_count, eval_count,
done_reason, message.content / message.thinking).

What differs from a local model, and is recorded rather than hidden:
- No temperature or seed: current Claude models reject sampling parameters,
  so samples are independent draws and can't be regenerated from a seed.
- Thinking is always on for claude-opus-5-5 (adaptive); depth is set with
  `effort`. We ask for the summarized display so the thinking is saved.
- Server-side refusal fallback is on ("default" routing). If a fallback ever
  serves an answer, `served_model` and `fallback_ran` say so, because the
  grid row is the requested model.

Credentials: TBD_ANTHROPIC_API_KEY if set, else the SDK's usual sources
(ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, an `ant auth login` profile). The
separate name exists because Claude Code in the dev container also reads
ANTHROPIC_API_KEY, and setting that would move Claude Code onto the key too.
Nothing is stored here or written to the run folder.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
KEY_ENV = "TBD_ANTHROPIC_API_KEY"


def make_client(anthropic, **kw):
    key = os.environ.get(KEY_ENV)
    return anthropic.Anthropic(api_key=key, **kw) if key else anthropic.Anthropic(**kw)

# $ per million tokens (input, output), Anthropic first-party API, from the
# claude-api skill's model table cached 2026-11-03. Thinking is billed as
# output and is included in usage.output_tokens.
PRICES = {
    "claude-fable-5-2": (8.00, 40.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    price = PRICES.get(model)
    if price is None:
        return None
    return round(input_tokens / 1e6 * price[0] + output_tokens / 1e6 * price[1], 6)


def claude_chat(model: str, messages: list[dict], *, max_tokens: int, effort: str,
                timeout_s: float, save_to: Path | None, timeout_error) -> dict:
    """One streamed request. `timeout_error(message, partial)` builds the
    exception raised at the deadline (case0.RequestTimeout)."""
    import anthropic  # optional dependency: pip install -e '.[eval]'

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": effort},
        "fallbacks": "default",
        "betas": [FALLBACK_BETA],
    }
    if save_to:
        save_to.mkdir(parents=True, exist_ok=True)
        (save_to / "request.json").write_text(json.dumps(body, indent=2))

    content, thinking, events = [], [], 0
    deadline = time.time() + timeout_s

    def partial() -> dict:
        return {"message": {"role": "assistant", "content": "".join(content),
                            "thinking": "".join(thinking)},
                "chunks": events, "done": False}

    def flush():
        if save_to:
            (save_to / "response.md").write_text("".join(content))
            if thinking:
                (save_to / "thinking.md").write_text("".join(thinking))

    client = make_client(anthropic, max_retries=2)
    stream_log = open(save_to / "stream.jsonl", "a") if save_to else None
    try:
        with client.with_options(timeout=timeout_s).beta.messages.stream(**body) as stream:
            for event in stream:
                if stream_log:
                    stream_log.write(json.dumps(event.model_dump(mode="json")) + "\n")
                    stream_log.flush()
                events += 1
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        content.append(event.delta.text)
                    elif event.delta.type == "thinking_delta":
                        thinking.append(event.delta.thinking)
                if events % 20 == 0:
                    flush()
                if time.time() > deadline:
                    flush()
                    raise timeout_error(f"deadline of {timeout_s:g}s passed after {events} events",
                                        partial())
            final = stream.get_final_message()
    except (anthropic.APITimeoutError, anthropic.APIConnectionError) as e:
        flush()
        raise timeout_error(f"{type(e).__name__}: {e} after {events} events", partial()) from e
    finally:
        if stream_log:
            stream_log.close()
    flush()

    usage = final.usage
    fallback_ran = any(getattr(it, "type", None) == "fallback_message"
                       for it in (getattr(usage, "iterations", None) or []))
    resp = {
        "message": partial()["message"],
        "done": True,
        "done_reason": final.stop_reason,
        "prompt_eval_count": usage.input_tokens,
        "eval_count": usage.output_tokens,
        "requested_model": model,
        "served_model": final.model,
        "fallback_ran": fallback_ran,
        "cost_usd": cost_usd(final.model, usage.input_tokens, usage.output_tokens),
        "chunks": events,
        # Passed back unchanged on a retry (tool-feedback mode): thinking
        # blocks must not be edited out of the history.
        "assistant_content": [b.model_dump(mode="json") for b in final.content],
        "final_message": final.model_dump(mode="json"),
    }
    if save_to:
        (save_to / "response.json").write_text(json.dumps(resp, indent=2))
    return resp
