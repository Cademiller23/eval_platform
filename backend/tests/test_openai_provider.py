"""Run the OpenAI-compatible provider against an in-process fake vLLM server."""
import asyncio
import json

import httpx
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, StreamingResponse

from evalplatform.providers.openai_provider import OpenAISession


def fake_server(multi_token: bool) -> FastAPI:
    app = FastAPI()
    state = {"tokens": 0}

    @app.get("/v1/models")
    def models():
        return {"data": [{"id": "fake", "max_model_len": 4096}]}

    @app.get("/metrics")
    def metrics():
        extra = 'vllm:spec_decode_num_drafts_total{x="1"} 5\nvllm:spec_decode_num_accepted_tokens_total{x="1"} 5\n' if multi_token else ""
        return PlainTextResponse("# HELP vllm:foo x\n# TYPE vllm:foo counter\nvllm:generation_tokens_total 1\n" + extra)

    @app.post("/v1/chat/completions")
    async def chat(body: dict):
        assert body["stream"] is True

        async def gen():
            words = ["Hello", " there", " from", " the", " fake", " server", "."]
            done = 0
            i = 0
            while i < len(words):
                n = 2 if (multi_token and i % 2 == 0 and i + 1 < len(words)) else 1
                text = "".join(words[i:i + n])
                i += n
                done += n
                chunk = {"choices": [{"delta": {"content": text}, "logprobs": {"content": [{"logprob": -0.2}] * n}, "finish_reason": None}],
                         "usage": {"prompt_tokens": 5, "completion_tokens": done}}
                yield f"data: {json.dumps(chunk)}\n\n"
                await asyncio.sleep(0.01)
            yield f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 5, 'completion_tokens': done}})}\n\n"
            yield f"data: {json.dumps({'choices': [], 'usage': {'prompt_tokens': 5, 'completion_tokens': done}})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


def session_for(app: FastAPI) -> OpenAISession:
    s = OpenAISession("http://fake/v1", None, "fake", {})
    s._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), timeout=30)
    return s


async def test_streaming_parse_and_timing():
    s = session_for(fake_server(False))
    r = await s.chat([{"role": "user", "content": "hi"}], max_tokens=20)
    assert r.error is None
    assert r.text == "Hello there from the fake server."
    assert r.finish_reason == "stop" and r.completion_tokens == 7 and r.prompt_tokens == 5
    assert r.multi_token_chunk_ratio == 0.0 and r.ttft_s is not None and r.decode_tps and r.decode_tps > 0
    assert len(r.logprobs) == 7


async def test_multi_token_chunks_and_metrics():
    s = session_for(fake_server(True))
    r = await s.chat([{"role": "user", "content": "hi"}], max_tokens=20)
    assert r.multi_token_chunk_ratio > 0.3 and r.completion_tokens == 7
    assert "spec_decode_num_drafts" in (await s.metrics())


async def test_http_error_surfaces():
    app = FastAPI()

    @app.post("/v1/chat/completions")
    def chat():
        return PlainTextResponse("boom", status_code=500)

    s = session_for(app)
    r = await s.chat([{"role": "user", "content": "hi"}], max_tokens=5)
    assert r.error and "500" in r.error
