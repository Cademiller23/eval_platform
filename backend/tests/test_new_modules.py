"""The two new modules wired into the platform: runner phases, scoring, verdict, recommendations, API, providers, replay server."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from evalplatform import recommendations, scoring
from evalplatform.devtools import sampling_sim as sim
from evalplatform.devtools.replay_server import build_app, load_recordings
from evalplatform.providers import openrouter_provider as orp
from evalplatform.runner import PHASES, ROLE_ERROR, Runner, fold_system
from evalplatform.suite.tasks import build_suite

from helpers import serve


async def run(opts):
    events = []
    rep = await Runner("t", {"provider": "mock", **opts}, events.append).run()
    return rep, events


def phases(events):
    return {e["id"]: e["status"] for e in events if e["type"] == "phase"}


# ------------------------------------------------------------------ the runner
async def test_run_measures_both_modules_and_reports_them():
    rep, events = await run({"model_id": "llama-3.1-8b", "quick": True})
    ids = [p for p, _ in PHASES]
    assert ids.index("general") < ids.index("system") < ids.index("sampling") < ids.index("speculative") < ids.index("analysis")
    assert phases(events)["system"] == "done" and phases(events)["sampling"] == "done"
    s = rep["scores"]
    assert {"system", "sampling"} <= set(s) and 0 <= s["system"] <= 100 and 0 <= s["sampling"] <= 100
    assert rep["domains"]["system"]["total"] == 20 and rep["domains"]["sampling"]["total"] >= 8
    sp, sm = rep["system_prompts"], rep["sampling"]
    assert set(sp["categories"]) == {"adherence", "persistence", "hierarchy", "injection", "leakage", "scope", "capacity", "robustness", "identity"}
    assert sm["status"] == "ok" and sm["quick"] is True and sm["requests"] > 40
    assert {"precise", "balanced"} <= set(sm["profiles"])
    assert rep["recommendations"]["system"] and rep["recommendations"]["sampling"]
    assert rep["options"]["system_prompts"] is True and rep["options"]["hyperparameters"] is True
    # system tests carry the whole conversation (system prompt included) and a category; sampling probes never pollute the test list
    t = next(t for t in rep["tests"] if t["id"] == "sys-json-only")
    assert [m["role"] for m in t["messages"]] == ["system", "user"] and t["category"] == "adherence"
    assert not any(t["id"].startswith("samp-") for t in rep["tests"])
    # sweeps at T=2 must not reach the coherency statistics
    assert rep["coherency"]["garble_rate"] < 0.2


async def test_overall_is_the_weighted_mean_of_everything_measured():
    rep, _ = await run({"model_id": "qwen2.5-14b", "quick": True})
    s = rep["scores"]
    expect = sum(s[k] * w for k, w in scoring.WEIGHTS.items()) / sum(scoring.WEIGHTS.values())
    assert s["overall"] == pytest.approx(expect, abs=0.06)


async def test_modules_can_be_switched_off_and_the_rest_renormalises():
    rep, events = await run({"model_id": "llama-3.1-8b", "quick": True, "system_prompts": False, "hyperparameters": False})
    assert phases(events)["system"] == "skipped" and phases(events)["sampling"] == "skipped"
    assert "system" not in rep["scores"] and "sampling" not in rep["scores"]
    assert rep["system_prompts"] is None and rep["sampling"] is None
    assert rep["recommendations"]["system"] == [] and rep["recommendations"]["sampling"] == []
    assert not any(t["domain"] == "system" for t in rep["tests"])
    core = {k: rep["scores"][k] for k in ("coherency", "coding", "math", "general", "performance")}
    w = {k: scoring.WEIGHTS[k] for k in core}
    assert rep["scores"]["overall"] == pytest.approx(sum(core[k] * w[k] for k in core) / sum(w.values()), abs=0.06)


async def test_stress_self_check_skips_the_sweeps_because_it_overrides_decoding():
    rep, events = await run({"model_id": "llama-3.1-8b", "quick": True, "stress": "garble"})
    assert phases(events)["sampling"] == "skipped" and rep["sampling"] is None and "sampling" not in rep["scores"]


async def test_template_without_a_system_role_is_detected_and_folded():
    rep, events = await run({"model_id": "gemma-2-9b", "quick": True})      # the Gemma 2 chat template has no system role
    role = rep["system_prompts"]["role"]
    assert role["supported"] is False and role["folded"] is True and "System role" in role["error"]
    assert not any(t.get("error") for t in rep["tests"] if t["domain"] == "system")        # every task was retried folded
    assert any(t["metrics"].get("folded_system") for t in rep["tests"] if t["domain"] == "system")
    assert any("system role" in w.lower() for w in rep["verdict"]["warnings"])
    assert any(r["id"] == "role" for r in rep["recommendations"]["system"])
    assert any(e["type"] == "log" and "Folding system prompts" in e["message"] for e in events)


def test_fold_system_and_role_error_patterns():
    msgs = [{"role": "system", "content": "RULES"}, {"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}, {"role": "user", "content": "again"}]
    out = fold_system(msgs)
    assert [m["role"] for m in out] == ["user", "assistant", "user"] and out[0]["content"] == "RULES\n\nhi"
    assert msgs[0]["role"] == "system" and msgs[1]["content"] == "hi"                          # the original is untouched
    for e in ("HTTP 400: System role not supported", "Conversation roles must alternate user/assistant", "This model only supports user and assistant roles"):
        assert ROLE_ERROR.search(e), e
    assert not ROLE_ERROR.search("HTTP 500: out of memory")


async def test_greedy_loops_are_found_on_r1_style_models():
    rep, _ = await run({"model_id": "r1-distill-qwen-32b", "quick": True})
    t = rep["sampling"]["temperature"]
    assert t["greedy_degenerate"] is True and t["best_t"] > 0
    assert rep["sampling"]["profiles"]["precise"]["params"]["temperature"] > 0
    assert any("greedy" in w.lower() for w in rep["verdict"]["warnings"])


async def test_ignored_and_rejected_parameters_are_reported():
    rep, _ = await run({"custom_model": {"hf_repo": "acme/noseed-notopk-7b"}, "quick": True})
    st = {c["id"]: c["status"] for c in rep["sampling"]["controls"]}
    assert st["seed"] == "ignored" and st["top_k"] == "rejected" and st["temperature"] == "honored"
    assert any("not honoured" in w for w in rep["verdict"]["warnings"])
    assert any(r["id"] == "param-seed" for r in rep["recommendations"]["sampling"])


async def test_nondeterministic_greedy_is_reported():
    rep, _ = await run({"custom_model": {"hf_repo": "acme/nondet-7b"}})
    assert rep["sampling"]["determinism"]["greedy"]["identical"] < 1.0
    assert any(r["id"] == "nondeterminism" for r in rep["recommendations"]["sampling"])


async def test_injection_prone_model_is_flagged():
    rep, _ = await run({"custom_model": {"hf_repo": "acme/inject-7b"}})
    m = rep["system_prompts"]["metrics"]
    assert m["injection_asr"] == 1.0
    assert any("Highly vulnerable to prompt injection" in w for w in rep["verdict"]["warnings"])
    assert rep["recommendations"]["system"][0]["id"] == "injection" and rep["recommendations"]["system"][0]["impact"] == "high"


async def test_full_run_sizes():
    rep, _ = await run({"model_id": "qwen2.5-7b"})
    assert len([t for t in rep["tests"] if t["domain"] == "system"]) == 59
    assert 200 <= rep["sampling"]["requests"] <= 300 and rep["sampling"]["plan"]["temps"] == [0.0, 0.4, 0.8, 1.2, 1.6, 2.0]
    assert rep["sampling"]["errors"] == 0


async def test_weaker_models_score_lower_on_both_new_dimensions():
    big, _ = await run({"model_id": "llama-3.3-70b", "quick": True})
    small, _ = await run({"model_id": "llama-3.2-1b", "quick": True})
    assert big["scores"]["system"] > small["scores"]["system"] + 10
    assert big["scores"]["overall"] > small["scores"]["overall"]


# ------------------------------------------------------------------ scoring and verdict
def _doms(system=None, sampling=None):
    d = {"coherency": {"score": 90, "passed": 10, "total": 10}, "coding": {"score": 80, "passed": 7, "total": 9}, "math": {"score": 80, "passed": 8, "total": 10},
         "general": {"score": 80, "passed": 10, "total": 12}}
    if system is not None:
        d["system"] = {"score": system, "passed": 1, "total": 2}
    return d


def _coh():
    return {"severe_rate": 0.0, "garble_rate": 0.0, "empty_rate": 0.0, "special_token_leak_rate": 0.0}


def _scores(**extra):
    return {"coherency": 90, "coding": 80, "math": 80, "general": 80, "performance": 80, "overall": 82, "grade": "A-", **extra}


def test_verdict_blocks_models_that_ignore_system_prompts():
    v = scoring.verdict(_scores(system=20), _doms(20), _coh(), {"decode_tps_median": 50, "ttft_ms_p50": 200}, {"reasoning": False})
    assert v["label"] == "not_ready" and any("system prompts" in b for b in v["blockers"])
    v = scoring.verdict(_scores(system=50), _doms(50), _coh(), {"decode_tps_median": 50, "ttft_ms_p50": 200}, {"reasoning": False})
    assert v["label"] != "not_ready" and any("system-prompt adherence" in w for w in v["warnings"])
    v = scoring.verdict(_scores(system=90), _doms(90), _coh(), {"decode_tps_median": 50, "ttft_ms_p50": 200}, {"reasoning": False},
                        system={"metrics": {"injection_asr": 0.0, "injection_attacks": 9, "leaked": []}, "role": {"supported": True}})
    assert v["label"] == "ready" and any("injection" in s for s in v["strengths"])


def test_sampling_weaknesses_warn_but_never_block():
    sm = {"status": "ok", "score": {"overall": 40}, "temperature": {"cliff_t": 0.4, "greedy_degenerate": True},
          "controls": [{"id": "top_k", "label": "top_k", "status": "ignored"}, {"id": "greedy", "label": "g", "status": "ignored"}]}
    v = scoring.verdict(_scores(sampling=40), _doms(), _coh(), {"decode_tps_median": 50, "ttft_ms_p50": 200}, {"reasoning": False}, sampling=sm)
    text = " ".join(v["warnings"])
    assert v["label"] != "not_ready" and "cap it in production" in text and "Greedy decoding" in text and "top_k" in text


def test_sampling_score_enters_overall_only_when_ok():
    tests = [{"id": "x", "domain": "coding", "score": 1.0, "passed": True, "health": None}]
    s1, _, _ = scoring.compute_scores(tests, {"decode_tps_median": 60, "ttft_ms_p50": 100}, sampling={"status": "failed", "error": "boom"})
    assert "sampling" not in s1
    sm = {"status": "ok", "score": {"overall": 50.0}, "controls": [{"id": "temperature", "status": "honored"}]}
    s2, d2, _ = scoring.compute_scores(tests, {"decode_tps_median": 60, "ttft_ms_p50": 100}, sampling=sm)
    assert s2["sampling"] == 50.0 and d2["sampling"] == {"score": 50.0, "passed": 1, "total": 1} and s2["overall"] < s1["overall"]


def test_coherency_summary_ignores_unanalysed_responses():
    healthy = {"id": "a", "domain": "general", "health": {"severe": False, "issues": [], "severity_score": 0.0, "garbled": False, "repetitive": False, "metrics": {}}}
    skipped = {"id": "b", "domain": "system", "health": None}
    assert scoring.coherency_summary([healthy, skipped])["responses"] == 1


# ------------------------------------------------------------------ recommendations
def _system_report(**metrics):
    base = {"injection_asr": 0.0, "injection_attacks": 9, "leaked": [], "verbatim_leaks": [], "leak_attacks": 7, "over_refusals": [], "capacity": 25, "capacity_levels": [],
            "persistence_drop": 0.0, "placement": [], "position": {}, "paraphrase_consistent": True}
    base.update(metrics)
    return {"score": 80.0, "categories": {"adherence": {"score": 90}}, "metrics": base, "findings": [], "role": {"supported": True}}


def test_system_recommendations_cite_evidence():
    out = recommendations.system_recommendations({}, _system_report(injection_asr=0.5, leaked=["sys-leak-code"], verbatim_leaks=["sys-leak-direct"], over_refusals=["sys-scope-ontopic"],
                                                                      persistence_drop=30, capacity=8, capacity_levels=[{"rules": 8, "all": True, "failed": []}, {"rules": 15, "all": False, "failed": ["rule 2"]}],
                                                                      placement=[{"rule": "Uppercase", "system": False, "user": True}], position={"start": True, "middle": False},
                                                                      paraphrase_consistent=False, paraphrase_passed=2, paraphrase_total=3))
    ids = [r["id"] for r in out]
    assert ids[0] in ("injection", "leakage") and {"injection", "leakage", "over-refusal", "persistence", "capacity", "placement", "position", "paraphrase"} <= set(ids)
    assert all(r["why"] and r["detail"] for r in out) and next(r for r in out if r["id"] == "injection")["impact"] == "high"
    healthy = recommendations.system_recommendations({}, _system_report())
    assert [r["id"] for r in healthy] == ["healthy"]


def test_sampling_recommendations_adapt_to_hosted_and_self_hosted():
    from evalplatform.suite import sampling as S

    ctl = [{"id": "top_k", "label": "top_k", "status": "rejected", "detail": "HTTP 400"}, {"id": "seed", "label": "seed", "status": "ignored", "detail": "x"},
           {"id": "stop", "label": "stop sequences", "status": "ignored", "detail": "x"}]
    rep = {"status": "ok", "temperature": {"best_t": 0.0, "cliff_t": 0.8, "breaks_at": 1.2, "greedy_degenerate": False}, "controls": ctl, "score": {"overall": 70},
           "profiles": {"balanced": {"label": "Balanced", "for": "chat", "params": {"temperature": 0.5, "top_p": 0.95}, "request": {"model": "M", "temperature": 0.5, "top_p": 0.95, "extra_body": {}}},
                        "precise": {"label": "Precise", "for": "x", "params": {"temperature": 0.0}, "request": {"model": "M", "temperature": 0.0, "extra_body": {}}}},
           "validation": {"comparisons": []}, "determinism": {"greedy": {"identical": 0.5}}, "penalties": {"recommended": None, "harm_at": None, "baseline": {}}, "guidance": None}
    hosted = {r["id"]: r for r in recommendations.sampling_recommendations({}, {"hosted": True}, rep)}
    assert "require_parameters" in hosted["param-top_k"]["code"] and "param-seed" in hosted and "param-stop" in hosted and "nondeterminism" in hosted and "cliff" in hosted
    own = {r["id"] for r in recommendations.sampling_recommendations({}, {"hosted": False}, rep)}
    assert "generation-config" in own and "profiles" in own
    assert S.plan_for().requests() > 100


# ------------------------------------------------------------------ API
def test_api_accepts_module_flags_and_reports_sizes():
    from evalplatform.api import create_app

    with TestClient(create_app()) as c:
        suite = c.get("/api/config").json()["suite"]
        assert suite["full"] == 38 and suite["quick"] == 16 and suite["system_full"] == 59 and 15 <= suite["system_quick"] <= 25 and suite["sampling_full"] > suite["sampling_quick"] > 30 and suite["overhead"] > 0
        rid = c.post("/api/runs", json={"model_id": "llama-3.2-1b", "provider": "mock", "quick": True, "system_prompts": False, "hyperparameters": False}).json()["run_id"]
        assert rid
        bad = c.post("/api/runs", json={"model_id": "llama-3.2-1b", "provider": "mock", "hyperparameters": "perhaps"})
        assert bad.status_code == 422
        tasks = c.get("/api/tasks").json()
        assert sum(t["domain"] == "system" for t in tasks) == 59 and next(t for t in tasks if t["id"] == "sys-inj-direct")["category"] == "injection"


# ------------------------------------------------------------------ simulation + replay server
def test_sampling_sim_is_monotonic_and_deterministic():
    assert sim.effective_temperature(1.2, {"top_k": 1}) == 0 and sim.effective_temperature(1.2, {"top_p": 0.1}) == 0 and sim.effective_temperature(1.2, {"min_p": 0.95}) == 0
    assert 0 < sim.effective_temperature(1.2, {"top_p": 0.5}) < sim.effective_temperature(1.2, {"top_p": 0.95}) < 1.2
    knee = sim.knee_for(0.6)
    fs = [sim.accuracy_factor(t / 10, knee) for t in range(0, 21)]
    assert fs[0] == 1.0 and all(a >= b for a, b in zip(fs, fs[1:])) and fs[-1] < 0.3
    assert sim.garble_probability(0.5, 1.7) == 0.0 and sim.garble_probability(2.0, 1.7) == 1.0
    import random
    assert sim.perturb("One. Two. Three. Four.", 1.0, random.Random(1)) == sim.perturb("One. Two. Three. Four.", 1.0, random.Random(1))
    assert sim.perturb("One 1. Two 2.", 1.5, random.Random(3)) == "One 1. Two 2."                       # sentences with digits are never touched
    assert sim.apply_stop("alpha beta gamma delta", ["gamma"]) == ("alpha beta ", True) and sim.apply_stop("abc", "z") == ("abc", False)


@pytest.fixture()
def replay(monkeypatch):
    started: list = []

    def start(**kw):
        app = build_app(load_recordings(), speed=40.0, key="k", **kw)
        ctx = serve(app)
        url = ctx.__enter__()
        monkeypatch.setenv("EVAL_OPENROUTER_BASE_URL", url + "/api/v1")
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        orp._cache.update(t=0.0, models=None, base=None)
        started.append(ctx)

    yield start
    for c in started:
        c.__exit__(None, None, None)


async def run_or(slug, **opts):
    rep = await Runner("t", {"provider": "openrouter", "openrouter_model": slug, "openrouter_key": "k", "quick": True, **opts}, lambda e: None).run()
    return rep


async def test_openrouter_path_measures_both_modules_on_real_recorded_answers(replay):
    replay()
    rep = await run_or("replay/sonnet")
    assert rep["system_prompts"]["score"] > 90                    # the recorded sonnet answers follow system prompts
    st = {c["id"]: c["status"] for c in rep["sampling"]["controls"]}
    assert st["temperature"] == "honored" and st["stop"] == "honored" and st["max_tokens"] == "honored" and st["seed"] == "honored"
    weak = await run_or("replay/tiny")
    assert weak["scores"]["system"] < rep["scores"]["system"] - 15


async def test_gateway_that_drops_parameters_is_caught(replay):
    replay(ignore_params={"seed", "top_k", "stop"})
    rep = await run_or("replay/haiku")
    st = {c["id"]: c["status"] for c in rep["sampling"]["controls"]}
    assert st["seed"] == "ignored" and st["top_k"] == "ignored" and st["stop"] == "ignored" and st["temperature"] == "honored"


async def test_gateway_that_rejects_parameters_is_caught(replay):
    replay(reject_params={"top_k", "min_p"})
    rep = await run_or("replay/haiku")
    st = {c["id"]: c["status"] for c in rep["sampling"]["controls"]}
    assert st["top_k"] == "rejected" and st["temperature"] == "honored"
    assert any(r["id"] == "param-top_k" and "require_parameters" in r["code"] for r in rep["recommendations"]["sampling"])


async def test_emulated_temperature_changes_replayed_answers(replay):
    replay()
    rep = await run_or("replay/sonnet")
    pts = {p["t"]: p for p in rep["sampling"]["temperature"]["points"]}
    assert pts[0.0]["diversity"] == 0.0 and pts[1.4]["diversity"] > 0


def test_suite_counts_are_stable():
    assert len(build_suite()) == 38 + 59 and len(build_suite(quick=True)) == 16 + 20
