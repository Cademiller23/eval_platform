"""OpenRouter integration, exercised end-to-end against a local replay server that serves REAL recorded answers."""
import pytest
from fastapi.testclient import TestClient

from evalplatform.devtools.replay_server import PROFILES, build_app, load_recordings
from evalplatform.providers import LaunchSpec, get_provider
from evalplatform.providers import openrouter_provider as orp
from evalplatform.runner import Runner
from evalplatform import catalog

from helpers import serve

KEY = "sk-or-test"
RECS = load_recordings()


@pytest.fixture()
def replay(monkeypatch):
    def start(**kw):
        app = build_app(RECS, speed=kw.pop("speed", 25.0), key=KEY, **kw)
        ctx = serve(app)
        url = ctx.__enter__()
        monkeypatch.setenv("EVAL_OPENROUTER_BASE_URL", url + "/api/v1")
        monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
        orp._cache.update(t=0.0, models=None, base=None)
        started.append(ctx)
        return app

    started: list = []
    yield start
    for c in started:
        c.__exit__(None, None, None)


async def run(slug, **opts):
    events = []
    o = {"provider": "openrouter", "openrouter_model": slug, "openrouter_key": KEY, **opts}
    return await Runner("t", o, events.append).run(), events


# ------------------------------------------------------------------------------------------ provider
async def test_start_validates_key_and_model(replay, monkeypatch):
    replay()
    prov = get_provider("openrouter")
    spec = lambda slug, key=KEY: LaunchSpec(model=catalog.openrouter_model(slug), endpoint={"api_key": key, "model": slug})
    logs = []
    with pytest.raises(RuntimeError, match="rejected the API key"):
        await prov.start(spec("replay/haiku", key="wrong"), lambda l, m: logs.append(m))
    with pytest.raises(ValueError, match="no model 'replay/haikuu'.*Did you mean: replay/haiku"):
        await prov.start(spec("replay/haikuu"), lambda l, m: None)
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with pytest.raises(RuntimeError, match="No OpenRouter API key"):
        await prov.start(LaunchSpec(model=catalog.openrouter_model("replay/haiku"), endpoint={"api_key": None, "model": "replay/haiku"}), lambda l, m: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    s = await prov.start(spec("replay/haiku"), lambda l, m: logs.append(m))
    assert s.info["hosted"] and s.info["provider"] == "openrouter" and s.info["pricing"]["prompt_per_m"] == 0.8
    assert any("Pricing" in m for m in logs)
    await s.close()


async def test_session_tracks_cost_provider_and_sends_expected_request(replay):
    app = replay()
    prov = get_provider("openrouter")
    s = await prov.start(LaunchSpec(model=catalog.openrouter_model("replay/sonnet"), endpoint={"api_key": KEY, "model": "replay/sonnet"}), lambda l, m: None)
    r = await s.chat([{"role": "user", "content": "Say hello in one short sentence."}], max_tokens=50)
    assert r.error is None and "Hello" in r.text and r.finish_reason == "stop"
    summ = s.run_summary()
    assert summ["cost_usd"] > 0 and summ["providers_seen"] == {"Replay-East": 1} and summ["completion_tokens"] > 0
    req = app.state.info["requests"][-1]
    assert req["headers"]["http-referer"] and req["headers"]["x-title"] == "Coherence Lab"
    assert req["body"]["usage"] == {"include": True} and "logprobs" not in req["body"]
    assert req["body"]["stream_options"] == {"include_usage": True}   # no continuous_usage_stats on hosted APIs
    await s.close()


async def test_retries_rate_limits(replay):
    app = replay(fail_every=2)
    prov = get_provider("openrouter")
    s = await prov.start(LaunchSpec(model=catalog.openrouter_model("replay/haiku"), endpoint={"api_key": KEY, "model": "replay/haiku"}), lambda l, m: None)
    for _ in range(4):
        r = await s.chat([{"role": "user", "content": "Say hello in one short sentence."}], max_tokens=30)
        assert r.error is None and r.text
    assert app.state.info["n"] >= 7   # several 429s were retried transparently
    await s.close()


async def test_midstream_error_is_surfaced_not_swallowed(replay):
    replay(midstream_error_every=1)
    prov = get_provider("openrouter")
    s = await prov.start(LaunchSpec(model=catalog.openrouter_model("replay/haiku"), endpoint={"api_key": KEY, "model": "replay/haiku"}), lambda l, m: None)
    r = await s.chat([{"role": "user", "content": "Write a long story about a dragon and a lighthouse."}], max_tokens=300)
    assert r.error and "Provider disconnected" in r.error
    await s.close()


# ------------------------------------------------------------------------------------------ full pipeline
async def test_full_run_report_for_hosted_closed_model(replay):
    replay()
    rep, events = await run("replay/haiku")
    assert rep["environment"]["hosted"] and rep["environment"]["provider"] == "openrouter"
    assert rep["verdict"]["label"] == "ready" and rep["scores"]["overall"] >= 80
    assert rep["coherency"]["severe_rate"] == 0 and rep["coherency"]["clean_ratio"] == 1
    assert len(rep["tests"]) == 41   # 38 suite tests + 3 long-generation probes
    # hosted semantics
    spec = rep["speculative"]
    assert spec["status"] == "unknown" and spec["hosted"] and "hosted API" in spec["headline"]
    assert not rep["performance"].get("roofline")
    assert rep["usage"]["cost_usd"] > 0 and rep["usage"]["providers_seen"]
    assert rep["environment"]["providers_seen"] == rep["usage"]["providers_seen"]
    plan = rep["recommendations"]["speculative"]
    assert plan["apply"] is None and plan["methods"] == [] and "closed-weights" in plan["summary"]
    assert any(r["id"] == "hosted-ok" or r["id"].startswith("route") or r["id"] == "think" for r in rep["recommendations"]["speed"])
    assert not any(r["id"] == "fp8" for r in rep["recommendations"]["speed"])
    assert rep["performance"]["decode_tps_median"] > 20


@pytest.mark.parametrize("name", sorted(PROFILES))
async def test_all_four_replayed_models_score_like_the_real_models_did(replay, name):
    replay()
    rep, _ = await run(f"replay/{name}")
    s = rep["scores"]
    assert s["coherency"] >= 95 and s["coding"] >= 95 and s["math"] >= 95 and s["general"] >= 90, s
    assert rep["verdict"]["label"] == "ready"
    assert rep["coherency"]["garble_rate"] == 0 and rep["coherency"]["special_token_leak_rate"] == 0


async def test_speed_ordering_reflects_each_models_streaming_speed(replay):
    replay()
    haiku, _ = await run("replay/haiku", quick=True)
    opus, _ = await run("replay/opus", quick=True)
    assert haiku["performance"]["decode_tps_median"] > opus["performance"]["decode_tps_median"] * 1.8
    assert opus["usage"]["cost_usd"] > haiku["usage"]["cost_usd"] * 5   # opus costs far more per token


# ------------------------------------------------------------------------------------------ stress controls
async def test_garble_stress_control_is_detected(replay):
    replay()
    base, _ = await run("replay/sonnet", quick=True)
    bad, events = await run("replay/sonnet", quick=True, stress="garble")
    assert bad["options"]["stress"] == "garble"
    assert bad["coherency"]["garble_rate"] > 0.5
    assert bad["scores"]["coherency"] < base["scores"]["coherency"] - 30
    assert bad["verdict"]["label"] == "not_ready"
    assert any("Stress control" in e.get("message", "") for e in events if e["type"] == "log")
    titles = " ".join(r["title"] for r in bad["recommendations"]["coherence"]).lower()
    assert "provider" in titles or "numerical" in titles or "corruption" in titles


async def test_loop_stress_control_is_detected(replay):
    replay()
    bad, _ = await run("replay/opus", quick=True, stress="loop")
    assert bad["coherency"]["repetition_rate"] > 0.3 or bad["coherency"]["runaway_rate"] > 0.3
    assert bad["verdict"]["label"] != "ready"


async def test_provider_failures_do_not_poison_the_scores(replay):
    replay(fail_every=3)
    rep, _ = await run("replay/sonnet", quick=True)
    assert rep["scores"]["coherency"] >= 95 and rep["verdict"]["label"] == "ready"


# ------------------------------------------------------------------------------------------ API
def test_api_openrouter_flow(replay, monkeypatch):
    replay()
    from evalplatform.api import create_app

    with TestClient(create_app()) as c:
        cfg = c.get("/api/config").json()
        assert cfg["openrouter_key_set"] and any(p["id"] == "openrouter" and p["available"] for p in cfg["providers"])
        models = c.get("/api/openrouter/models").json()
        assert models["live"] and {m["id"] for m in models["models"]} == {f"replay/{n}" for n in PROFILES}
        st = c.get("/api/openrouter/status").json()
        assert st["configured"] and st["valid"] and st["remaining"] == 10.0
        assert "sk-or" not in str(st)

        assert c.post("/api/runs", json={"provider": "openrouter"}).status_code == 422
        r = c.post("/api/runs", json={"openrouter_model": "replay/haiku", "speculative": "ngram"})
        assert r.status_code == 422 and "hosted" in r.json()["detail"].lower()
        monkeypatch.delenv("OPENROUTER_API_KEY")
        r = c.post("/api/runs", json={"openrouter_model": "replay/haiku"})
        assert r.status_code == 400 and "API key" in r.json()["detail"]

        r = c.post("/api/runs", json={"openrouter_model": "replay/haiku", "openrouter_key": KEY, "quick": True})
        assert r.status_code == 201
        rid = r.json()["run_id"]
        import time

        for _ in range(200):
            d = c.get(f"/api/runs/{rid}").json()
            if d["status"] not in ("queued", "running"):
                break
            time.sleep(0.2)
        assert d["status"] == "completed", d.get("error")
        assert d["report"]["environment"]["hosted"]
        # the key must never be stored or returned
        assert KEY not in c.get(f"/api/runs/{rid}").text and KEY not in c.get(f"/api/runs/{rid}/report.json").text
        assert KEY not in c.get("/api/runs").text
        from evalplatform.config import get_settings

        assert KEY not in (get_settings().runs_dir / f"{rid}.json").read_text()


async def test_unknown_model_fails_the_run_with_a_helpful_message(replay):
    replay()
    with pytest.raises(ValueError, match="no model"):
        await run("replay/nope")
