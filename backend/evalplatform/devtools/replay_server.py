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
from ..suite.sampling import PROBES
from ..suite.tasks import build_suite
from ..verify import parse_responses
from . import sampling_sim as sim

RAW_DIR = Path(__file__).resolve().parents[3] / "verification" / "raw"

# tokens/second each replay "model" streams at, and $/M-token pricing
# ``quality`` only shapes how the *emulated* decoding settings behave (where accuracy starts to fall, where word salad starts).
PROFILES = {
    "haiku": dict(tps=190, ttft=0.38, p_in=0.8, p_out=4.0, provider="Replay-East", quality=0.80),
    "sonnet": dict(tps=95, ttft=0.62, p_in=3.0, p_out=15.0, provider="Replay-East", quality=0.88),
    "opus": dict(tps=55, ttft=1.05, p_in=15.0, p_out=75.0, provider="Replay-West", quality=0.92),
    "fable": dict(tps=75, ttft=0.8, p_in=5.0, p_out=25.0, provider="Replay-West", quality=0.90),
    # NOT a real model: a deterministic emulation of a weak model, derived from the opus recording, so that
    # discrimination checks ("does the suite separate strong from weak?") can run offline.
    "tiny": dict(tps=320, ttft=0.22, p_in=0.05, p_out=0.1, provider="Replay-East", quality=0.30),
}
SAMPLING_PARAMS = {"temperature", "top_p", "top_k", "min_p", "seed", "frequency_penalty", "presence_penalty", "repetition_penalty", "stop"}


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

    from ..suite.system_prompts import build_system_tasks

    sys_tasks = {t.id: t for t in build_system_tasks()}
    p_fail = {"adherence": 0.45, "persistence": 0.5, "hierarchy": 0.4, "injection": 0.7, "leakage": 0.7, "scope": 0.4, "capacity": 0.6, "robustness": 0.45, "identity": 0.3}
    out: dict[str, str] = {}
    for tid, text in source.items():
        rng = random.Random(int(hashlib.sha256(tid.encode()).hexdigest()[:12], 16))
        roll = rng.random()
        if tid in sys_tasks:
            st = sys_tasks[tid]
            if st.fails and roll < p_fail.get(st.category, 0.5):
                out[tid] = st.fails[0]               # a weak model breaks the rule, falls for the injection, leaks the secret ...
                continue
            out[tid] = text
            continue
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
              fail_every: int = 0, midstream_error_every: int = 0, slow_variant: bool = False,
              ignore_params: set[str] | None = None, reject_params: set[str] | None = None) -> FastAPI:
    recs = dict(recordings if recordings is not None else load_recordings())
    if "opus" in recs and "tiny" not in recs:
        recs["tiny"] = make_tiny(recs["opus"])
    by_last_user = {t.messages[-1]["content"]: t.id for t in build_suite()}
    by_last_user.update({p.prompt: p.id for p in PROBES.values()})
    ignored = set(ignore_params or ())          # emulate endpoints that silently drop parameters
    rejected = set(reject_params or ())         # ... and endpoints that answer 400 for them
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
                    "id": f"replay/{name}", "name": ("Tiny (emulated weak model)" if name == "tiny" else f"{name.capitalize()} (recorded answers)"), "context_length": 200000,
                    "description": ("An emulated weak model (derived from the Opus answers) for discrimination checks." if name == "tiny" else f"{name.capitalize()}'s actual answers to every evaluation prompt, replayed."), "hugging_face_id": "",
                    "pricing": {"prompt": str(prof["p_in"] / 1e6), "completion": str(prof["p_out"] / 1e6)},
                    "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                    "supported_parameters": ["temperature", "top_p", "frequency_penalty", "presence_penalty", "repetition_penalty", "max_tokens"],
                })
        if slow_variant and "opus" in recs:   # a deliberately slow model, for cancel/timeout tests
            data.append({**next(d for d in data if d["id"] == "replay/opus"), "id": "replay/opus:slow", "name": "Opus (slow variant, for cancel tests)"})
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

    def degrade(text: str, body: dict[str, Any], seed: int, model: str = "opus", prompt: str = "") -> tuple[str, str | None]:
        """Emulate what real models do under different decoding settings: variation with temperature, narrowing with truncation
        samplers, repeatability with a seed, word salad when far too hot, loops under negative penalties."""
        params = {k: v for k, v in body.items() if k in SAMPLING_PARAMS and k not in ignored}
        q = PROFILES.get(model, {}).get("quality", 0.8)
        temp = float(params.get("temperature") or 0.0)
        t_eff = sim.effective_temperature(temp, params)
        fp = float(params.get("frequency_penalty") or 0)
        seeded = params.get("seed") if t_eff > 0 else None
        rng = random.Random(sim.stable_int(model, prompt, round(t_eff, 3), seeded if seeded is not None else f"{seed}|{random.random()}", params.get("frequency_penalty"), params.get("repetition_penalty")))
        if rng.random() < sim.garble_probability(t_eff, sim.salad_at(q)):
            words = text.split(" ")
            keep = max(3, len(words) // 5)
            return " ".join(words[:keep]) + " " + sim.salad(rng, max(40, len(words))), None
        if fp <= -1:
            sentences = re.split(r"(?<=[.!?])\s+", text.strip())
            head = " ".join(sentences[: max(1, len(sentences) // 3)])
            loop = (sentences[min(len(sentences) - 1, 1)] if len(sentences) > 1 else head)[:80]
            return head + (" " + loop) * 60, "length"
        if t_eff > 0:
            # accuracy decays past the model's knee: swap the final numeric answer for a wrong one with the matching probability
            p_wrong = 1.0 - sim.accuracy_factor(t_eff, sim.knee_for(q)) + sim.penalty_harm(params)
            if "Answer:" in text and rng.random() < p_wrong:
                text = re.sub(r"(Answer:\s*)[-\d.,/%$ ]+", lambda m: m.group(1) + "7777", text, count=1)
            text = sim.perturb(text, t_eff, rng)
        if sim.penalty_strength(params) != (0.0, 1.0):
            text = sim.penalised_repetition(text, params)
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
        bad = sorted(k for k in rejected if k in body)
        if bad:
            return JSONResponse({"error": {"message": f"Unsupported parameter: {bad[0]}", "code": 400}}, status_code=400)
        prof = PROFILES[model]
        spd = speed * (0.04 if body["model"].endswith(":slow") else 1.0)
        text = pick_text(model, body["messages"])
        text, forced = degrade(text, body, seed=state["n"], model=model, prompt=body["messages"][-1]["content"])
        text, stopped = sim.apply_stop(text, body["stop"]) if body.get("stop") and "stop" not in ignored else (text, False)
        if stopped:
            forced = "stop"
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
            # queueing + prefill: the model's typical time to first token (scaled by --speed), with a little jitter
            await asyncio.sleep(prof["ttft"] * (0.85 + 0.3 * random.random()) / spd)
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
    ap.add_argument("--ignore-params", default="", help="comma list of sampling parameters to silently drop, e.g. seed,top_k (emulates a gateway)")
    ap.add_argument("--reject-params", default="", help="comma list of sampling parameters answered with HTTP 400")
    args = ap.parse_args()
    split = lambda v: {x.strip() for x in v.split(",") if x.strip()}  # noqa: E731
    uvicorn.run(build_app(speed=args.speed, key=args.key, slow_variant=args.slow_variant, ignore_params=split(args.ignore_params), reject_params=split(args.reject_params)),
                host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
