"""Full evaluation through the custom-endpoint provider against a fake vLLM-style server."""
import asyncio
import json
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from evalplatform.runner import Runner


@pytest.fixture()
def endpoint():
    app = FastAPI()
    counters = {"drafts": 0, "accepted": 0}

    @app.get("/v1/models")
    def models():
        return {"data": [{"id": "fake-model", "max_model_len": 8192}]}

    @app.get("/metrics")
    def metrics():
        return PlainTextResponse(
            "# HELP vllm:generation_tokens_total x\n# TYPE vllm:generation_tokens_total counter\nvllm:generation_tokens_total 5\n"
            f"vllm:spec_decode_num_drafts_total{{m=\"a\"}} {counters['drafts']}\n"
            f"vllm:spec_decode_num_draft_tokens_total{{m=\"a\"}} {counters['drafts'] * 3}\n"
            f"vllm:spec_decode_num_accepted_tokens_total{{m=\"a\"}} {counters['accepted']}\n"
        )

    @app.post("/v1/chat/completions")
    async def chat(req: Request):
        body = await req.json()
        words = ("The answer is 42. " * 40).split(" ")
        limit = min(body["max_tokens"], len(words))

        async def gen():
            i = done = 0
            while i < limit:
                n = min(2, limit - i)  # two tokens per step, like a speculative engine
                text = " ".join(words[i:i + n]) + " "
                i += n
                done += n
                counters["drafts"] += 1
                counters["accepted"] += n - 1
                yield "data: " + json.dumps({"choices": [{"delta": {"content": text}, "logprobs": {"content": [{"logprob": -0.2}] * n}}],
                                              "usage": {"prompt_tokens": 10, "completion_tokens": done}}) + "\n\n"
                await asyncio.sleep(0.001)
            fin = "length" if limit < len(words) else "stop"
            yield "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": fin}], "usage": {"prompt_tokens": 10, "completion_tokens": done}}) + "\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}/v1"
    server.should_exit = True
    t.join(timeout=5)


async def test_full_run_against_openai_compatible_endpoint(endpoint):
    events = []
    opts = {"model_id": "llama-3.2-1b", "provider": "openai", "quick": True, "endpoint": {"base_url": endpoint, "model": "fake-model"}}
    report = await Runner("e2e", opts, events.append).run()
    assert report["environment"]["engine"] == "vLLM" and report["environment"]["provider"] == "openai"
    assert report["performance"]["decode_tps_median"] and report["performance"]["decode_tps_median"] > 0
    # fake server emits 2 tokens per chunk and exposes spec counters → detected from the outside
    spec = report["speculative"]
    assert spec["status"] == "active" and spec["acceptance_rate"] and spec["mean_accepted_length"] > 1.5
    assert any(e["source"] == "behavior" and e["positive"] for e in spec["evidence"])
    # the 'model' just repeats a sentence forever → graders and detectors must notice
    assert report["verdict"]["label"] == "not_ready"
    assert report["coherency"]["repetition_rate"] > 0 or report["coherency"]["runaway_rate"] > 0
    assert len(report["tests"]) == 19  # 16 quick-suite tests + 3 long greedy-generation probes
    assert sum(t["id"].startswith("coh-longgen") for t in report["tests"]) == 3
