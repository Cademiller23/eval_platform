"""A local stand-in for OpenRouter that replays *recorded real model responses*.

It speaks the parts of the OpenRouter protocol the platform uses — ``GET /api/v1/models``, ``GET /api/v1/key``,
``POST /api/v1/chat/completions`` (SSE with ``provider`` and ``usage.cost`` fields, 429 + Retry-After, mid-stream
error chunks, keep-alive comments) — so the full OpenRouter code path can be exercised offline and deterministically.

Each fake model replays the answers a real model gave to every evaluation prompt (verification/raw/<name>.md).
Stress controls are *emulated* the way real models behave: temperature >= 1.5 injects word-salad, negative frequency
penalties force repetition loops. Run it with::

    python -m evalplatform.devtools.replay_server --port 9999
    EVAL_OPENROUTER_BASE_URL=http://localhost:9999/api/v1 OPENROUTER_API_KEY=test-key python -m evalplatform
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..runner import BENCH_PROMPTS
from ..suite.tasks import build_suite
from ..verify import parse_responses

RAW_DIR = Path(__file__).resolve().parents[3] / "verification" / "raw"

# tokens/second each replay "model" streams at, and $/M-token pricing
PROFILES = {
    "haiku": dict(tps=190, p_in=0.8, p_out=4.0, provider="Replay-East"),
    "sonnet": dict(tps=95, p_in=3.0, p_out=15.0, provider="Replay-East"),
    "opus": dict(tps=55, p_in=15.0, p_out=75.0, provider="Replay-West"),
    "fable": dict(tps=75, p_in=5.0, p_out=25.0, provider="Replay-West"),
    # NOT a real model: a deterministic emulation of a weak model, derived from the opus recording, so that
    # discrimination checks ("does the suite separate strong from weak?") can run offline.
    "tiny": dict(tps=320, p_in=0.05, p_out=0.1, provider="Replay-East"),
}


def _pieces(text: str) -> list[str]:
    out: list[str] = []
    for p in re.findall(r"\s*\S+|\s+", text):
        while len(p) > 6:
            out.append(p[:5])
            p = p[5:]
        out.append(p)
    return out


def make_tiny(source: dict[str, str]) -> dict[str, str]:
    """Emulate a weak small model from a strong model's answers (deterministic)."""
    import hashlib

    out: dict[str, str] = {}
    for tid, text in source.items():
        rng = random.Random(int(hashlib.sha256(tid.encode()).hexdigest()[:12], 16))
        roll = rng.random()
        if tid.startswith("math-") and roll < 0.55:
            text = re.sub(r"(?i)answer:\s*[^\n]*$", "Answer: 7", text.strip())
        elif tid.startswith("code-") and roll < 0.6:
            text = re.sub(r"\breturn\b[^\n]*", "return None", text, count=1)
        elif tid.startswith("gen-") and roll < 0.45:
            text = "I'm not sure about that, it depends on the situation."
        elif tid in ("coh-memory", "coh-needle", "coh-stop") and roll < 0.5:
            text = "Sure! I could not find that, but let me know if there is anything else I can help you with today."
        if rng.random() < 0.12 and not tid.startswith("code-"):
            text += " " + " ".join("".join(rng.choice("bcdfghjklmnpqrstvwxz") for _ in range(rng.randint(4, 8))) for _ in range(25))
        out[tid] = text
    return out


def load_recordings(raw_dir: Path = RAW_DIR) -> dict[str, dict[str, str]]:
    return {p.stem: parse_responses(p) for p in sorted(raw_dir.glob("*.md"))}


def build_app(recordings: dict[str, dict[str, str]] | None = None, *, speed: float = 1.0, key: str = "test-key",
              fail_every: int = 0, midstream_error_every: int = 0, slow_variant: bool = False) -> FastAPI:
    recs = dict(recordings if recordings is not None else load_recordings())
    if "opus" in recs and "tiny" not in recs:
        recs["tiny"] = make_tiny(recs["opus"])
    by_last_user = {t.messages[-1]["content"]: t.id for t in build_suite()}
    bench = {p: f"bench-{i + 1}" for i, p in enumerate(BENCH_PROMPTS)}
    app = FastAPI(title="OpenRouter replay server")
    state = {"n": 0, "mid": 0, "requests": [], "key": key}
    app.state.info = state

    def authed(request: Request) -> bool:
        return request.headers.get("authorization", "") == f"Bearer {state['key']}"

    @app.get("/api/v1/models")
    def models():
        data = []
        for name, prof in PROFILES.items():
            if name in recs:
                data.append({
                    "id": f"replay/{name}", "name": (f"Replay: {name} (EMULATED weak model)" if name == "tiny" else f"Replay: {name} (recorded real outputs)"), "context_length": 200000,
                    "description": ("EMULATED weak model (derived from opus answers) for discrimination checks." if name == "tiny" else f"Replays real {name} answers to the evaluation prompts."), "hugging_face_id": "",
                    "pricing": {"prompt": str(prof["p_in"] / 1e6), "completion": str(prof["p_out"] / 1e6)},
                    "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                    "supported_parameters": ["temperature", "top_p", "frequency_penalty", "presence_penalty", "repetition_penalty", "max_tokens"],
                })
        if slow_variant and "opus" in recs:   # a deliberately slow model, for cancel/timeout tests
            data.append({**next(d for d in data if d["id"] == "replay/opus"), "id": "replay/opus:slow", "name": "Replay: opus (SLOW — for cancel tests)"})
        return {"data": data}

    @app.get("/api/v1/key")
    def key_info(request: Request):
        if not authed(request):
            return JSONResponse({"error": {"message": "No auth credentials found", "code": 401}}, status_code=401)
        return {"data": {"label": "replay-key", "usage": 0.0, "limit": 10.0, "is_free_tier": False}}

    def pick_text(model: str, messages: list[dict[str, str]]) -> str:
        last = messages[-1]["content"]
        r = recs.get(model, {})
        if last in by_last_user and by_last_user[last] in r:
            return r[by_last_user[last]]
        for p, bid in bench.items():
            if last.startswith(p) and bid in r:
                return r[bid]
        if "Summarise the document in one sentence" in last:
            return "The document is a long list of harbour, inventory and weather notes."
        if "Say hello" in last:
            return "Hello there, nice to meet you!"
        return "I'm happy to help with that, but I need a little more detail to give a useful answer."

    def degrade(text: str, body: dict[str, Any], seed: int) -> tuple[str, str | None]:
        """Emulate what real models do under abusive sampling settings."""
        rng = random.Random(seed)
        temp = body.get("temperature") or 0
        fp = body.get("frequency_penalty") or 0
        if temp >= 1.5:
            words = text.split(" ")
            keep = max(3, len(words) // 5)
            salad = []
            for _ in range(max(40, len(words))):
                salad.append("".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(3, 11))))
            junk = " ".join(rng.choice(salad) + rng.choice(["", "ñ", "ß", "я", "漢"]) for _ in range(len(salad)))
            return " ".join(words[:keep]) + " " + junk, None
        if fp <= -1:
            sentences = re.split(r"(?<=[.!?])\s+", text.strip())
            head = " ".join(sentences[: max(1, len(sentences) // 3)])
            loop = (sentences[min(len(sentences) - 1, 1)] if len(sentences) > 1 else head)[:80]
            return head + (" " + loop) * 60, "length"
        return text, None

    @app.post("/api/v1/chat/completions")
    async def chat(request: Request):
        if not authed(request):
            return JSONResponse({"error": {"message": "Invalid API key", "code": 401}}, status_code=401)
        body = await request.json()
        state["n"] += 1
        state["requests"].append({"headers": dict(request.headers), "body": body})
        if fail_every and state["n"] % fail_every == 0:
            return JSONResponse({"error": {"message": "Rate limit exceeded", "code": 429}}, status_code=429, headers={"Retry-After": "0"})
        model = body["model"].split("/", 1)[-1].split(":")[0]
        if model not in recs:
            return JSONResponse({"error": {"message": f"No endpoints found for {body['model']}", "code": 404}}, status_code=404)
        prof = PROFILES[model]
        spd = speed * (0.04 if body["model"].endswith(":slow") else 1.0)
        text = pick_text(model, body["messages"])
        text, forced = degrade(text, body, seed=state["n"])
        pieces = _pieces(text)
        max_tokens = body.get("max_tokens") or 1024
        finish = forced or "stop"
        if len(pieces) > max_tokens:
            pieces, finish = pieces[:max_tokens], "length"
        prompt_tokens = max(8, sum(len(m["content"]) for m in body["messages"]) // 4)
        want_usage = bool((body.get("usage") or {}).get("include")) or bool((body.get("stream_options") or {}).get("include_usage"))
        inject_error = bool(midstream_error_every) and (state.__setitem__("mid", state["mid"] + 1) or state["mid"] % midstream_error_every == 0)

        async def gen():
            yield ": OPENROUTER PROCESSING\n\n"
            await asyncio.sleep(0.02 / spd)
            done = 0
            i = 0
            step = max(1, round(prof["tps"] * spd / 60))   # tokens per SSE chunk ≈ what gateways emit
            while i < len(pieces):
                n = min(step, len(pieces) - i)
                chunk = {"id": "gen-replay", "provider": prof["provider"], "model": body["model"],
                         "choices": [{"index": 0, "delta": {"role": "assistant", "content": "".join(pieces[i:i + n])}, "finish_reason": None}]}
                i += n
                done += n
                yield f"data: {json.dumps(chunk)}\n\n"
                if inject_error and done > 6:
                    yield f"data: {json.dumps({'error': {'message': 'Provider disconnected', 'code': 502}, 'choices': [{'finish_reason': 'error', 'delta': {'content': ''}}]})}\n\n"
                    return
                await asyncio.sleep(n / (prof["tps"] * spd))
            yield f"data: {json.dumps({'id': 'gen-replay', 'provider': prof['provider'], 'choices': [{'index': 0, 'delta': {'content': ''}, 'finish_reason': finish}]})}\n\n"
            if want_usage:
                cost = prompt_tokens * prof["p_in"] / 1e6 + done * prof["p_out"] / 1e6
                yield f"data: {json.dumps({'id': 'gen-replay', 'provider': prof['provider'], 'choices': [], 'usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': done, 'total_tokens': prompt_tokens + done, 'cost': cost}})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser(description="Replay real recorded model answers behind an OpenRouter-compatible API")
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--speed", type=float, default=1.0, help="multiply streaming speed (use 10 for fast demos)")
    ap.add_argument("--key", default="test-key")
    ap.add_argument("--slow-variant", action="store_true", help="also serve replay/opus:slow (for cancel tests)")
    args = ap.parse_args()
    uvicorn.run(build_app(speed=args.speed, key=args.key, slow_variant=args.slow_variant), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
