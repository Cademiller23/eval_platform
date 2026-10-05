"""Regression tests for defects found by the independent review (each one was reproduced before being fixed)."""
import time

import pytest
from fastapi.testclient import TestClient

from evalplatform.providers import LaunchSpec, get_provider
from evalplatform.providers import openrouter_provider as orp
from evalplatform.providers.openai_provider import OpenAISession, parse_sse_chunk
from evalplatform.suite.coherence import analyze_text
from evalplatform.suite.text import extract_final_number
from evalplatform import catalog


# ---- 2: "Answer: x = 12" must return 12, not a number from the working that follows
@pytest.mark.parametrize("text,expected", [
    ("Answer: n = 12. Check: 12*13/2 = 78.", 12), ("Answer: x = 4 (since 4 + 3 = 7)", 4), ("Final answer: $P = \\frac{1}{6}$", 1 / 6),
    ("The answer is approximately 0.17, i.e. about 1 in 6.", 0.17), ("Answer: 240.\n\nLet me know if you want another answer!", 240),
    ("Answer: \\frac{1}{6}", 1 / 6), ("**Answer:** 240", 240), ("so the sum is 78\nAnswer: x = 12\nCheck: 12*13/2 = 78", 12),
])
def test_final_number_extraction(text, expected):
    assert extract_final_number(text) == pytest.approx(expected)


# ---- 5: reasoning and answer in the same delta must not leave the answer inside <think>
def test_reasoning_and_content_in_same_delta():
    st: dict = {}
    t0 = time.perf_counter()
    chunks = [{"choices": [{"delta": {"reasoning": "think a"}}]}, {"choices": [{"delta": {"reasoning": " b", "content": "Answer"}}]}, {"choices": [{"delta": {"content": " 42"}}]}]
    text = "".join((parse_sse_chunk(c, st, t0) or {}).get("text", "") for c in chunks)
    assert text == "<think>think a b</think>Answer 42"
    from evalplatform.suite.text import split_thinking

    assert split_thinking(text)[0] == "Answer 42"


# ---- 8: a final usage object riding on an empty chunk must not become a fake N-token chunk
def test_final_usage_is_not_a_fake_chunk_without_continuous_usage():
    st = {"continuous": False}
    t0 = time.perf_counter()
    evs = [parse_sse_chunk(c, st, t0) for c in [{"choices": [{"delta": {"content": "a"}}]}, {"choices": [{"delta": {"content": ""}, "finish_reason": "stop"}], "usage": {"completion_tokens": 5}}]]
    assert evs[0]["n"] == 1 and evs[1] is None and st["usage"]["completion_tokens"] == 5
    st = {"continuous": True}
    ev = parse_sse_chunk({"choices": [{"delta": {"content": "ab"}}], "usage": {"completion_tokens": 3}}, st, t0)
    assert ev["n"] == 3   # servers that DO send continuous usage keep per-chunk token deltas


# ---- 6: hex addresses, git SHAs and model ids are normal in technical prose
@pytest.mark.parametrize("text", [
    "The crash happened at address 0x7ffd5e8c and again at 0x7ffd5e9a in the same frame, which points to a stale pointer.",
    "Revert commit 3f2a9c1b7e and cherry-pick 9b8d7e6f5a onto the release branch before tagging.",
    "Use the model gpt4o20240806 for that.",
    "Pin the image to sha256:3f2a9c1b7e5d4c8aa1b2c3d4e5f60718 and redeploy.",
])
def test_technical_identifiers_are_not_garble(text):
    assert not analyze_text(text).severe
    assert not analyze_text(text, kind="short").severe


def test_real_soup_is_still_caught():
    soup = "q z%wbÅ8j1f207k5zfÅg%q@Ãp#ff6j404vpggk%j1ggqk2Å8xj@6fwx7fä%07bpw#g�x5k%gjdÃÅ 4w0Å0d408qzw2w6j"
    assert analyze_text("Buenos días\n" + soup, kind="short", multilingual=True).garbled


# ---- 1: key hygiene
async def test_malformed_key_is_rejected_and_never_echoed(monkeypatch):
    with pytest.raises(ValueError, match="whitespace"):
        orp.api_key("sk-or-v1-SECRET\nPART2")
    sess = OpenAISession("http://x/v1", "sk-or-v1-SECRET", "m", {})
    msg = sess._redact("Illegal header value b'Bearer sk-or-v1-SECRET' and sk-or-v1-SECRET again")
    assert "SECRET" not in msg and "Bearer ***" in msg


def test_api_rejects_multiline_key_with_422(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from evalplatform.api import create_app

    with TestClient(create_app()) as c:
        r = c.post("/api/runs", json={"openrouter_model": "a/b", "openrouter_key": "sk-or-v1-AAA\nBBB"})
        assert r.status_code == 422 and "AAA" not in r.text


# ---- 3: credit uses limit_remaining, tolerant of resetting limits
def test_remaining_credit_semantics():
    assert orp.remaining_credit({"limit": 10, "usage": 25, "limit_remaining": 4.0, "limit_reset": "monthly"}) == 4.0
    assert orp.remaining_credit({"limit": 10, "usage": 25, "limit_reset": "monthly"}) is None      # lifetime usage says nothing
    assert orp.remaining_credit({"limit": 10, "usage": 3}) == 7
    assert orp.remaining_credit({"limit": None, "usage": 3}) is None


# ---- 4: DNS rebinding / cross-site
def test_foreign_host_and_cross_origin_are_blocked():
    from evalplatform.api import create_app

    with TestClient(create_app()) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/health", headers={"host": "evil.example.com"}).status_code == 400          # rebinding
        r = c.post("/api/runs", json={"model_id": "llama-3.2-1b", "provider": "mock"}, headers={"origin": "https://evil.example.com"})
        assert r.status_code == 403
        ok = c.post("/api/runs", json={"model_id": "llama-3.2-1b", "provider": "mock", "quick": True}, headers={"origin": "http://testserver"})
        assert ok.status_code == 201


# ---- 7: routing variants are accepted when their base model exists
async def test_variant_slug_resolves_to_base_model(monkeypatch):
    from helpers import serve
    from evalplatform.devtools.replay_server import build_app, load_recordings

    with serve(build_app(load_recordings(), speed=25, key="k")) as url:
        monkeypatch.setenv("EVAL_OPENROUTER_BASE_URL", url + "/api/v1")
        orp._cache.update(t=0.0, models=None, base=None)
        logs = []
        s = await get_provider("openrouter").start(
            LaunchSpec(model=catalog.openrouter_model("replay/haiku:nitro"), endpoint={"api_key": "k", "model": "replay/haiku:nitro"}), lambda lv, m: logs.append(m))
        assert any("routing variant" in m for m in logs)
        r = await s.chat([{"role": "user", "content": "Say hello in one short sentence."}], max_tokens=20)
        assert r.error is None and r.text
        await s.close()
