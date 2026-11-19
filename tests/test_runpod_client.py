"""The RunPod client against a fake RunPod job API on a local HTTP server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tbd.eval import runpod_client
from tbd.eval.models import RequestTimeout
from tbd.eval.runpod_client import RunPodError, runpod_chat


def line(content, done=False, model="qwen2.5-coder:14b", **extra):
    return json.dumps({"model": model, "message": {"role": "assistant", "content": content},
                       "done": done, **extra})


@pytest.fixture
def fake_runpod(monkeypatch):
    """Serves /run, /stream, /status, /cancel. `polls` is a list of /stream replies
    given in order; the last one repeats."""
    state = {"polls": [], "seen": 0, "cancelled": [], "runs": [], "auth": [], "status": {}}

    class Handler(BaseHTTPRequestHandler):
        def reply(self, obj):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(obj).encode())

        def do_POST(self):
            state["auth"].append(self.headers["Authorization"])
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"] or 0)) or b"{}")
            if self.path.endswith("/run"):
                state["runs"].append(body)
                self.reply({"id": "job-1", "status": "IN_QUEUE"})
            elif "/cancel/" in self.path:
                state["cancelled"].append(self.path.rsplit("/", 1)[1])
                self.reply({"id": "job-1", "status": "CANCELLED"})

        def do_GET(self):
            if "/stream/" in self.path:
                polls = state["polls"]
                self.reply(polls[min(state["seen"], len(polls) - 1)])
                state["seen"] += 1
            elif "/status/" in self.path:
                self.reply({"id": "job-1", "status": "COMPLETED", "delayTime": 1200,
                            "executionTime": 3400, "workerId": "w1", "error": "boom",
                            **state["status"]})

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(runpod_client, "API", f"http://127.0.0.1:{server.server_port}/v2")
    monkeypatch.setattr(runpod_client, "POLL_S", 0.01)
    monkeypatch.setenv(runpod_client.KEY_ENV, "test-key")
    yield state
    server.shutdown()


def chat(tmp_path=None, **kw):
    return runpod_chat("ep", "qwen2.5-coder:14b", [{"role": "user", "content": "hi"}],
                       temperature=0.7, seed=42, think=None,
                       save_to=tmp_path, **{"timeout_s": 30, "idle_timeout_s": 5, **kw})


def test_streams_chunks_to_disk_and_records_the_runpod_job(fake_runpod, tmp_path):
    fake_runpod["polls"] = [
        {"status": "IN_QUEUE", "stream": []},
        {"status": "IN_PROGRESS", "stream": [{"output": line("Hel")}]},
        {"status": "COMPLETED", "stream": [
            {"output": line("lo") + "\n" + line("", done=True, done_reason="stop", eval_count=2)}]},
    ]
    resp = chat(tmp_path)
    assert resp["message"]["content"] == "Hello"
    assert resp["done_reason"] == "stop" and resp["chunks"] == 3
    assert resp["runpod"] == {"job_id": "job-1", "worker_id": "w1", "delay_ms": 1200,
                              "execution_ms": 3400}
    assert (tmp_path / "response.md").read_text() == "Hello"
    assert len((tmp_path / "stream.jsonl").read_text().splitlines()) == 3
    # the request carries the experiment's settings, and the key never reaches disk
    sent = fake_runpod["runs"][0]["input"]
    assert sent["stream"] is True
    assert sent["options"] == {"temperature": 0.7, "seed": 42, "num_ctx": 16384,
                               "num_predict": 4096}
    assert fake_runpod["auth"][0] == "Bearer test-key"
    assert "test-key" not in "".join(p.read_text() for p in tmp_path.iterdir())


def test_silence_past_the_idle_bound_cancels_the_job_and_keeps_partial_output(fake_runpod, tmp_path):
    fake_runpod["polls"] = [{"status": "IN_PROGRESS", "stream": [{"output": line("par")}]},
                            {"status": "IN_PROGRESS", "stream": []}]
    with pytest.raises(RequestTimeout) as e:
        chat(tmp_path, idle_timeout_s=0.2)
    assert e.value.partial["message"]["content"] == "par"
    assert fake_runpod["cancelled"] == ["job-1"]
    assert (tmp_path / "response.md").read_text() == "par"


def test_a_different_served_model_is_an_error(fake_runpod):
    fake_runpod["polls"] = [{"status": "IN_PROGRESS",
                             "stream": [{"output": line("x", model="llama3.1:latest")}]}]
    with pytest.raises(RunPodError, match="serves llama3.1"):
        chat()
    assert fake_runpod["cancelled"] == ["job-1"]


def test_a_reply_missing_from_the_stream_is_read_from_the_job_status(fake_runpod, tmp_path):
    # RunPod can report COMPLETED before /stream has handed out every chunk; the
    # job's own output still has them, until RunPod discards it.
    fake_runpod["polls"] = [{"status": "COMPLETED", "stream": []}]
    fake_runpod["status"] = {"output": [{"output": line("Hel")},
                                        {"output": line("lo") + "\n" + line("", done=True)}]}
    resp = chat(tmp_path)
    assert resp["message"]["content"] == "Hello" and resp["chunks"] == 3
    assert (tmp_path / "response.md").read_text() == "Hello"
    assert len((tmp_path / "stream.jsonl").read_text().splitlines()) == 3
    assert json.loads((tmp_path / "runpod_status.json").read_text())["status"] == "COMPLETED"


def test_a_completed_job_with_no_reply_anywhere_is_an_error_and_keeps_the_status(fake_runpod,
                                                                                    tmp_path):
    fake_runpod["polls"] = [{"status": "COMPLETED", "stream": []}]
    with pytest.raises(RunPodError, match="without a final chunk"):
        chat(tmp_path)
    assert (tmp_path / "runpod_status.json").exists()


def test_a_failed_job_is_an_error(fake_runpod):
    fake_runpod["polls"] = [{"status": "FAILED", "stream": []}]
    with pytest.raises(RunPodError, match="FAILED: boom"):
        chat()


def test_key_from_the_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv(runpod_client.KEY_ENV, raising=False)
    env = tmp_path / ".env.runpod"
    monkeypatch.setattr(runpod_client, "ENV_FILE", env)
    with pytest.raises(RunPodError, match="no RunPod API key"):
        runpod_client.api_key()
    env.write_text("# comment\nTBD_RUNPOD_API_KEY='abc123'\nTBD_RUNPOD_ENDPOINT=ep1\n")
    assert runpod_client.api_key() == "abc123"
    monkeypatch.delenv(runpod_client.ENDPOINT_ENV, raising=False)
    assert runpod_client.setting(runpod_client.ENDPOINT_ENV) == "ep1"
    monkeypatch.setenv(runpod_client.ENDPOINT_ENV, "from-env")
    assert runpod_client.setting(runpod_client.ENDPOINT_ENV) == "from-env"
