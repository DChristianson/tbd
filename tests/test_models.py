"""The Ollama client's timeouts, against a local HTTP server that stays silent
before it replies (as Ollama does while it loads a model)."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tbd.eval import models
from tbd.eval.models import RequestTimeout, ollama_chat


@pytest.fixture
def silent_ollama(monkeypatch):
    """An /api/chat server that waits `delay` seconds, then streams one chunk."""
    state = {"delay": 0.0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            time.sleep(state["delay"])
            self.send_response(200)
            self.end_headers()
            done = {"message": {"content": "ok"}, "done": True, "done_reason": "stop"}
            self.wfile.write(json.dumps(done).encode() + b"\n")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(models, "OLLAMA", f"http://127.0.0.1:{server.server_port}")
    yield state
    server.shutdown()


def chat(**kw):
    return ollama_chat("m", [{"role": "user", "content": "hi"}], temperature=0.7,
                       seed=42, think=None, **kw)


def test_waits_through_a_silent_model_load_shorter_than_the_idle_timeout(silent_ollama):
    silent_ollama["delay"] = 1.5
    resp = chat(timeout_s=30, idle_timeout_s=5)
    assert resp["message"]["content"] == "ok"


def test_silence_longer_than_the_idle_timeout_is_a_timeout(silent_ollama):
    silent_ollama["delay"] = 3
    start = time.time()
    with pytest.raises(RequestTimeout) as e:
        chat(timeout_s=30, idle_timeout_s=1)
    assert time.time() - start < 2.5
    assert e.value.partial["chunks"] == 0


def test_idle_timeout_is_capped_at_the_request_timeout(silent_ollama):
    silent_ollama["delay"] = 3
    start = time.time()
    with pytest.raises(RequestTimeout):
        chat(timeout_s=1, idle_timeout_s=60)
    assert time.time() - start < 2.5
