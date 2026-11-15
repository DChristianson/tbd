"""Claude client plumbing against a fake stream (no network, no cost)."""

import json
from types import SimpleNamespace as NS

import pytest

anthropic = pytest.importorskip("anthropic")
from tbd.eval import claude_client  # noqa: E402
from tbd.eval.case0 import RequestTimeout  # noqa: E402


class Ev(NS):
    def model_dump(self, mode=None):
        return {"type": self.type}


class FakeStream:
    def __init__(self, events, final):
        self.events, self.final = events, final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self.events)

    def get_final_message(self):
        return self.final


def fake_client(events, final, seen):
    class Client:
        def __init__(self, **kw):
            pass

        def with_options(self, **kw):
            return self

        @property
        def beta(self):
            def stream(**body):
                seen.update(body)
                return FakeStream(events, final)
            return NS(messages=NS(stream=stream))
    return Client


def block(d):
    return NS(model_dump=lambda mode=None: d)


def test_streams_to_disk_and_normalizes(tmp_path, monkeypatch):
    events = [
        Ev(type="content_block_delta", delta=NS(type="thinking_delta", thinking="hmm ")),
        Ev(type="content_block_delta", delta=NS(type="text_delta", text="```asm\nlda #1\n")),
        Ev(type="content_block_delta", delta=NS(type="text_delta", text="```")),
    ]
    final = NS(stop_reason="end_turn", model="claude-opus-5-5",
               usage=NS(input_tokens=100, output_tokens=1000, iterations=None),
               content=[block({"type": "text", "text": "x"})],
               model_dump=lambda mode=None: {"id": "msg_1"})
    seen = {}
    monkeypatch.setattr(anthropic, "Anthropic", fake_client(events, final, seen))
    r = claude_client.claude_chat("claude-opus-5-5", [{"role": "user", "content": "hi"}],
                                  max_tokens=64000, effort="high", timeout_s=60,
                                  save_to=tmp_path, timeout_error=RequestTimeout)
    assert "temperature" not in seen and "system" not in seen
    assert seen["messages"] == [{"role": "user", "content": "hi"}]
    assert r["message"]["content"] == "```asm\nlda #1\n```"
    assert r["message"]["thinking"] == "hmm "
    assert r["done_reason"] == "end_turn" and r["eval_count"] == 1000
    assert r["cost_usd"] == pytest.approx(100 / 1e6 * 4 + 1000 / 1e6 * 20)
    assert r["served_model"] == "claude-opus-5-5" and not r["fallback_ran"]
    for f in ("request.json", "response.json", "response.md", "thinking.md", "stream.jsonl"):
        assert (tmp_path / f).exists(), f
    assert len((tmp_path / "stream.jsonl").read_text().splitlines()) == 3
    assert json.loads((tmp_path / "request.json").read_text())["output_config"] == {"effort": "high"}


def test_deadline_keeps_partial_output(tmp_path, monkeypatch):
    events = [Ev(type="content_block_delta", delta=NS(type="text_delta", text="part"))]
    monkeypatch.setattr(anthropic, "Anthropic", fake_client(events, None, {}))
    with pytest.raises(RequestTimeout) as e:
        claude_client.claude_chat("claude-opus-5-5", [{"role": "user", "content": "hi"}],
                                  max_tokens=10, effort="high", timeout_s=-1,
                                  save_to=tmp_path, timeout_error=RequestTimeout)
    assert e.value.partial["message"]["content"] == "part"
    assert (tmp_path / "response.md").read_text() == "part"
