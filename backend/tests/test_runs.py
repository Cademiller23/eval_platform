import asyncio

import pytest
from fastapi.testclient import TestClient

from evalplatform import recommendations
from evalplatform.runner import Runner, resolve_speculative
from evalplatform import catalog


async def run(opts):
    events = []
    rep = await Runner("t", {"provider": "mock", **opts}, events.append).run()
    return rep, events


async def test_baseline_run_report_shape():
    rep, events = await run({"model_id": "llama-3.1-8b", "gpu": "A10G", "quick": True})
    assert rep["verdict"]["label"] in ("ready", "caution", "not_ready")
    assert set(rep["scores"]) >= {"overall", "coherency", "coding", "math", "general", "performance", "grade"}
    assert rep["speculative"]["status"] == "not_detected"
    assert rep["performance"]["decode_tps_median"] > 5
    assert rep["performance"]["roofline"]["efficiency"] > 0
    assert {e["type"] for e in events} >= {"phase", "log", "test", "perf", "environment"}
    spec = rep["recommendations"]["speculative"]
    assert spec["recommended"] == "eagle3" and spec["apply"]["speculative"] == "eagle3"
    assert any("vllm serve" in (s["code"] or "") for s in spec["steps"])
    assert rep["recommendations"]["speed"] and rep["recommendations"]["coherence"]


async def test_speculative_run_is_detected_and_faster():
    base, _ = await run({"model_id": "llama-3.1-8b", "gpu": "A10G", "quick": True})
    eagle, _ = await run({"model_id": "llama-3.1-8b", "gpu": "A10G", "quick": True, "speculative": "eagle3"})
    assert eagle["speculative"]["status"] == "active"
    assert eagle["speculative"]["acceptance_rate"] > 0.2
    assert eagle["performance"]["decode_tps_median"] > base["performance"]["decode_tps_median"] * 1.3
    assert "tuning" in eagle["recommendations"]["speculative"]["summary"].lower() or "running" in eagle["recommendations"]["speculative"]["summary"]


async def test_flawed_model_is_flagged_not_ready():
    rep, _ = await run({"custom_model": {"hf_repo": "acme/broken-7b"}})
    assert rep["verdict"]["label"] == "not_ready"
    assert rep["coherency"]["garble_rate"] > 0.2
    titles = " ".join(r["title"] for r in rep["recommendations"]["coherence"])
    assert "numerical" in titles.lower() or "tokenizer" in titles.lower()
    assert "stop tokens" in titles.lower() or "leaked" in titles.lower()


async def test_mtp_model_recommends_mtp():
    rep, _ = await run({"model_id": "deepseek-r1", "quick": True})
    assert rep["recommendations"]["speculative"]["recommended"] == "mtp"


def test_resolve_speculative():
    m = catalog.get_model("llama-3.1-8b")
    assert resolve_speculative(m, "auto", None) == (None, "auto")
    cfg, label = resolve_speculative(m, "ngram", None)
    assert cfg["method"] == "ngram" and label == "ngram"
    with pytest.raises(ValueError):
        resolve_speculative(m, "mtp", None)
    with pytest.raises(ValueError):
        resolve_speculative(m, "custom", "not json")
    assert resolve_speculative(m, "custom", '{"method":"ngram"}')[0] == {"method": "ngram"}
    with pytest.raises(ValueError):
        resolve_speculative(catalog.get_model("mistral-7b-v0.3"), "eagle3", None)


def test_method_ranking_prefers_ngram_for_copy_heavy_workloads():
    m = dict(catalog.get_model("mistral-7b-v0.3"))
    assert recommendations.rank_methods(m, {"coding": 0.7})[0]["method"] == "ngram"


def test_api_end_to_end():
    from evalplatform.api import create_app

    with TestClient(create_app()) as c:
        cfg = c.get("/api/config").json()
        assert any(p["id"] == "mock" and p["available"] for p in cfg["providers"])
        assert len(c.get("/api/models").json()) >= 15
        assert c.post("/api/runs", json={"model_id": "nope", "provider": "mock"}).status_code == 422
        assert c.post("/api/runs", json={"provider": "mock"}).status_code == 422
        assert c.post("/api/runs", json={"custom_model": {"hf_repo": "bad repo"}, "provider": "mock"}).status_code == 422
        assert c.post("/api/runs", json={"model_id": "qwen2.5-7b", "provider": "openai"}).status_code == 422
        rid = c.post("/api/runs", json={"model_id": "qwen2.5-7b", "provider": "mock", "quick": True, "speculative": "ngram"}).json()["run_id"]
        import time

        for _ in range(120):
            d = c.get(f"/api/runs/{rid}").json()
            if d["status"] not in ("queued", "running"):
                break
            time.sleep(0.25)
        assert d["status"] == "completed", d.get("error")
        assert d["report"]["speculative"]["status"] == "active"
        assert c.get(f"/api/runs/{rid}/report.md").text.startswith("# Evaluation report")
        assert c.get(f"/api/runs/{rid}/report.json").json()["run_id"] == rid
        assert any(r["id"] == rid for r in c.get("/api/runs").json())
        assert "data:" in c.get(f"/api/runs/{rid}/events").text
        assert c.delete(f"/api/runs/{rid}").status_code == 200
        assert c.get(f"/api/runs/{rid}").status_code == 404


def test_dotenv_loader(tmp_path, monkeypatch):
    from evalplatform.config import load_dotenv_file

    f = tmp_path / ".env"
    f.write_text("# comment\nHF_TOKEN='abc123'\nEMPTY=\nKEEP=fromfile\n")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("KEEP", "fromenv")
    load_dotenv_file(f)
    import os

    assert os.environ["HF_TOKEN"] == "abc123" and os.environ["KEEP"] == "fromenv"
    monkeypatch.delenv("HF_TOKEN", raising=False)
