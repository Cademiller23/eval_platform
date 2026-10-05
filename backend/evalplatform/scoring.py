"""Turn raw test results + performance numbers into scores and a ship/no-ship verdict."""
from __future__ import annotations

from typing import Any

WEIGHTS = {"coherency": 0.30, "coding": 0.20, "math": 0.20, "general": 0.15, "performance": 0.15}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def grade_letter(score: float) -> str:
    for cut, letter in ((93, "A+"), (88, "A"), (82, "A-"), (76, "B+"), (70, "B"), (64, "B-"), (58, "C+"), (52, "C"), (45, "D"), (0, "F")):
        if score >= cut:
            return letter
    return "F"


def coherency_summary(tests: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate text-health findings across *every* response the model produced."""
    n = len(tests) or 1
    kinds: dict[str, list[str]] = {}
    sev_sum = 0.0
    clean = garbled = repetitive = leak = empty = truncated = drift = runaway = 0
    for t in tests:
        h = t.get("health") or {}
        issues = h.get("issues", [])
        sev_sum += h.get("severity_score", 0.0)
        if not h.get("severe"):
            clean += 1
        garbled += bool(h.get("garbled"))
        repetitive += bool(h.get("repetitive"))
        for i in issues:
            kinds.setdefault(i["kind"], []).append(t["id"])
        ks = {i["kind"] for i in issues if i["severity"] in ("major", "critical")}
        leak += "special_token_leak" in ks
        empty += "empty_output" in ks
        drift += "language_drift" in ks
        runaway += bool(ks & {"runaway_generation", "runaway_reasoning"})
        truncated += any(i["kind"] in ("hit_token_limit", "runaway_generation") for i in issues)
    clean_ratio = clean / n
    mean_sev = sev_sum / n
    clean_score = 100 * (0.75 * clean_ratio ** 1.5 + 0.25 * (1 - mean_sev))
    lp = [t["health"]["metrics"]["mean_logprob"] for t in tests if (t.get("health") or {}).get("metrics", {}).get("mean_logprob") is not None]
    return {
        "responses": len(tests),
        "clean_ratio": round(clean_ratio, 4),
        "clean_score": round(clean_score, 1),
        "garble_rate": round(garbled / n, 4),
        "repetition_rate": round(repetitive / n, 4),
        "special_token_leak_rate": round(leak / n, 4),
        "empty_rate": round(empty / n, 4),
        "truncation_rate": round(truncated / n, 4),
        "language_drift_rate": round(drift / n, 4),
        "runaway_rate": round(runaway / n, 4),
        "severe_rate": round(1 - clean_ratio, 4),
        "mean_logprob": round(sum(lp) / len(lp), 3) if lp else None,
        "issue_kinds": {k: {"count": len(v), "tests": sorted(set(v))} for k, v in sorted(kinds.items(), key=lambda kv: -len(kv[1]))},
    }


def performance_score(perf: dict[str, Any]) -> float:
    tps = perf.get("decode_tps_median")
    if not tps:
        return 0.0
    ttft = perf.get("ttft_ms_p50")
    tps_part = _clamp(tps / 60.0)
    ttft_part = 0.5 if ttft is None else _clamp((2000 - ttft) / 1700)
    return 100 * (0.7 * tps_part + 0.3 * ttft_part)


def domain_scores(tests: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for d in ("coherency", "coding", "math", "general"):
        ts = [t for t in tests if t["domain"] == d]
        if not ts:
            continue
        out[d] = {
            "score": round(100 * sum(t["score"] for t in ts) / len(ts), 1),
            "passed": sum(1 for t in ts if t["passed"]),
            "total": len(ts),
        }
    return out


def compute_scores(tests: list[dict[str, Any]], perf: dict[str, Any]) -> tuple[dict[str, float], dict[str, Any], dict[str, Any]]:
    doms = domain_scores(tests)
    coh = coherency_summary(tests)
    coh_tests = doms.get("coherency", {}).get("score", coh["clean_score"])
    coherency = 0.6 * coh["clean_score"] + 0.4 * coh_tests
    perf_s = performance_score(perf)
    scores = {
        "coherency": round(coherency, 1),
        "coding": doms.get("coding", {}).get("score", 0.0),
        "math": doms.get("math", {}).get("score", 0.0),
        "general": doms.get("general", {}).get("score", 0.0),
        "performance": round(perf_s, 1),
    }
    total_w = sum(WEIGHTS[k] for k in scores if k in WEIGHTS and (k in doms or k in ("coherency", "performance")))
    overall = sum(scores[k] * WEIGHTS[k] for k in WEIGHTS if k in doms or k in ("coherency", "performance")) / total_w
    scores["overall"] = round(overall, 1)
    scores["grade"] = grade_letter(overall)
    return scores, doms, coh


def verdict(scores: dict[str, Any], doms: dict[str, Any], coh: dict[str, Any], perf: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    strengths: list[str] = []
    tps = perf.get("decode_tps_median") or 0.0

    if coh["severe_rate"] > 0.10:
        blockers.append(f"{coh['severe_rate']:.0%} of responses showed serious output problems (garbling, loops, leaked tokens or empty answers).")
    elif coh["severe_rate"] > 0.04:
        warnings.append(f"{coh['severe_rate']:.0%} of responses showed serious output problems.")
    if coh["garble_rate"] > 0.05:
        blockers.append(f"Garbled language detected in {coh['garble_rate']:.0%} of responses.")
    if coh["empty_rate"] > 0.05:
        blockers.append(f"{coh['empty_rate']:.0%} of responses were empty.")
    if scores["coherency"] < 50:
        blockers.append(f"Coherency score {scores['coherency']:.0f}/100 is below the 50 minimum.")
    elif scores["coherency"] < 75:
        warnings.append(f"Coherency score {scores['coherency']:.0f}/100 — below the 75 bar for unattended production use.")
    if tps and tps < 5:
        blockers.append(f"Decode speed {tps:.1f} tok/s is too slow for interactive use.")
    elif tps and tps < 15:
        warnings.append(f"Decode speed {tps:.1f} tok/s is slow for chat (target ≥ 30 tok/s per user).")
    if not tps:
        warnings.append("Decode speed could not be measured.")
    if coh["special_token_leak_rate"] > 0:
        warnings.append("Special/chat-template tokens leaked into output — check stop tokens and chat template.")
    for d in ("coding", "math", "general"):
        if d in doms:
            s = doms[d]["score"]
            if s < 40:
                warnings.append(f"Weak at {d} ({s:.0f}/100).")
            elif s >= 80:
                strengths.append(f"Strong at {d} ({s:.0f}/100).")
    if scores["coherency"] >= 85:
        strengths.append(f"Highly coherent output ({scores['coherency']:.0f}/100).")
    if tps >= 40:
        strengths.append(f"Fast decoding ({tps:.0f} tok/s).")
    if (perf.get("ttft_ms_p50") or 0) > 1500:
        warnings.append(f"Slow time-to-first-token ({perf['ttft_ms_p50']:.0f} ms median).")

    overall = scores["overall"]
    min_domain = min((d["score"] for k, d in doms.items() if k != "coherency"), default=100)
    if blockers or overall < 45:
        label, title = "not_ready", "Not ready to run"
        summary = "This model has problems that make it unsuitable to deploy as-is."
    elif overall >= 75 and scores["coherency"] >= 80 and min_domain >= 50 and tps >= 15 and not warnings:
        label, title = "ready", "Ready to run"
        summary = "Coherent, capable across domains and fast enough — safe to deploy."
    elif overall >= 75 and scores["coherency"] >= 75 and min_domain >= 45 and tps >= 15:
        label, title = "ready", "Ready to run"
        summary = "Solid overall; review the minor warnings below before relying on it."
    else:
        label, title = "caution", "Usable with caveats"
        summary = "Works, but has weaknesses you should address or accept before deploying."
    if model.get("reasoning"):
        warnings.append("Reasoning model: expect long <think> traces — budget generous max_tokens and measure end-to-end latency, not just tok/s.")
    return {"label": label, "title": title, "summary": summary, "blockers": blockers, "warnings": warnings, "strengths": strengths}
