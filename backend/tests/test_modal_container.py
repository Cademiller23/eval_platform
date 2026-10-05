"""Exercise the container-side streaming code in modal_app/serve.py against a fake vLLM server."""
import importlib.util
import json
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

SERVE = Path(__file__).resolve().parents[2] / "modal_app" / "serve.py"


def load_server_cls():
    spec = importlib.util.spec_from_file_location("serve_mod", SERVE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, mod.ModelServer._get_user_cls()


@pytest.fixture()
def fake_vllm():
    app = FastAPI()
    calls = []

    @app.post("/v1/chat/completions")
    async def chat(req: Request):
        body = await req.json()
        calls.append(body)
        if body.get("logprobs") and app.state.reject_logprobs:
            return JSONResponse({"error": "logprobs not supported with speculative decoding"}, status_code=400)

        async def gen():
            done = 0
            for piece, n in (("Hello", 1), (" big world", 2), ("!", 1)):
                done += n
                chunk = {"choices": [{"delta": {"content": piece}, "finish_reason": None}], "usage": {"prompt_tokens": 4, "completion_tokens": done}}
                if body.get("logprobs"):
                    chunk["choices"][0]["logprobs"] = {"content": [{"logprob": -0.1}] * n}
                yield f"data: {json.dumps(chunk)}\n\n"
            yield f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 4, 'completion_tokens': done}})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    app.state.reject_logprobs = False
    import socket

    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    app.state.port = port
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    yield app, calls
    server.should_exit = True
    t.join(timeout=5)


def get_raw(cls):
    """Unwrap the @modal.method PartialFunction to the plain Python function."""
    return cls.__dict__["stream_chat"]._get_raw_f()


def test_stream_events_and_logprob_retry(fake_vllm):
    app, calls = fake_vllm
    mod, cls = load_server_cls()
    mod.VLLM_PORT = app.state.port
    raw = get_raw(cls)
    events = list(raw(object(), {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 8, "logprobs": True}))
    assert events[-1]["done"] and events[-1]["finish_reason"] == "stop"
    deltas = [e for e in events if "text" in e]
    assert "".join(e["text"] for e in deltas) == "Hello big world!"
    assert [e["n"] for e in deltas] == [1, 2, 1]
    assert calls[0]["model"] == "eval-model" and calls[0]["stream"] is True

    app.state.reject_logprobs = True
    calls.clear()
    events = list(raw(object(), {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 8, "logprobs": True}))
    assert not any("error" in e for e in events)
    assert len(calls) == 2 and "logprobs" not in calls[1]


FAKE_VLLM = '''#!{python}
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
args = sys.argv[1:]
port = int(args[args.index("--port") + 1])
print("INFO speculative config received:", args[args.index("--speculative-config") + 1] if "--speculative-config" in args else "none", flush=True)
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/health": body = b"ok"
        elif self.path == "/v1/models": body = json.dumps({{"data": [{{"id": "eval-model", "max_model_len": 4096}}]}}).encode()
        else: body = b"vllm:generation_tokens_total 1\\n"
        self.send_response(200); self.end_headers(); self.wfile.write(body)
HTTPServer(("127.0.0.1", port), H).serve_forever()
'''


def test_boot_builds_command_and_reports_info(tmp_path, monkeypatch):
    import socket
    import sys

    exe = tmp_path / "vllm"
    exe.write_text(FAKE_VLLM.format(python=sys.executable))
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    mod, cls = load_server_cls()
    mod.VLLM_PORT = port
    mod.hf_cache = type("V", (), {"commit": lambda self: None})()
    mod.vllm_cache = mod.hf_cache
    obj = object.__new__(cls)
    cfg = {"method": "ngram", "num_speculative_tokens": 5}
    obj.model, obj.revision, obj.spec_config = "org/some-model", "", json.dumps(cfg)
    obj.max_model_len, obj.dtype, obj.quantization = 4096, "auto", "fp8"
    obj.trust_remote_code, obj.extra_args = True, '["--enable-prefix-caching"]'
    cls.__dict__["boot"]._get_raw_f()(obj)
    try:
        info = cls.__dict__["info"]._get_raw_f()(obj)
        assert info["speculative_config"] == cfg and info["max_model_len"] == 4096
        assert info["quantization"] == "fp8" and info["metrics_available"]
        cmd = info["command"]
        assert "vllm serve org/some-model" in cmd and "--trust-remote-code" in cmd and "--quantization fp8" in cmd
        assert "--speculative-config" in cmd and "--enable-prefix-caching" in cmd and "--served-model-name eval-model" in cmd
        assert any("ngram" in l for l in info["speculative_log_lines"])
        assert "generation_tokens" in cls.__dict__["metrics"]._get_raw_f()(obj)
    finally:
        cls.__dict__["shutdown"]._get_raw_f()(obj)
