"""Automated verification of the evaluation suite itself against real (hosted) models.

``verify()`` evaluates several models through OpenRouter, runs decoding *stress controls* on the best one, and
asserts the properties a careful checker would look for:

  * the suite **discriminates** — strong models score clearly above weak ones, in every domain that should differ
  * the detectors **fire on real corrupted output** — garble (temperature 2) and loops (negative penalties)
  * the detectors **do not cry wolf** — high-scoring models are not flagged as garbled
  * hosted-API semantics are honest — no invented speculative-decoding verdict or GPU roofline
  * cost/provider accounting works, infrastructure errors are rare, results are reproducible (optional)

It also writes a human-review dump of every failed test (prompt, response, failed checks) so a person can judge
whether a *model* or a *grader* was wrong — the one check no script can fully replace.
"""
from __future__ import annotations

import difflib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .providers import openrouter_provider as orp
from .report_md import to_markdown
from .runner import Runner

DEFAULT_MODELS = [
    "openai/gpt-4o-mini",
    "meta-llama/llama-3.3-70b-instruct",
    "meta-llama/llama-3.1-8b-instruct",
    "meta-llama/llama-3.2-1b-instruct",
]


@dataclass
class Check:
    name: str
    status: str   # PASS | FAIL | WARN
    detail: str


@dataclass
class VerifyResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.rows) and not any(c.status == "FAIL" for c in self.checks)


def _row(slug: str, rep: dict[str, Any], label: str = "") -> dict[str, Any]:
    s, c, p = rep["scores"], rep["coherency"], rep["performance"]
    return {
        "model": slug, "label": label, "overall": s["overall"], "coherency": s["coherency"], "coding": s["coding"], "math": s["math"],
        "general": s["general"], "system": s.get("system"), "sampling": s.get("sampling"), "verdict": rep["verdict"]["label"], "tps": p.get("decode_tps_median"), "ttft_ms": p.get("ttft_ms_p50"),
        "cost": (rep.get("usage") or {}).get("cost_usd"), "garble": c["garble_rate"], "repetition": c["repetition_rate"],
        "runaway": c["runaway_rate"], "leak": c["special_token_leak_rate"], "severe": c["severe_rate"],
        "errors": sum(1 for t in rep["tests"] if t.get("error")), "tests": len(rep["tests"]), "report": rep,
    }


def review_dump(rep: dict[str, Any]) -> str:
    """Markdown listing every failed or flagged test with its evidence, for human judgement."""
    lines = [f"# Review — {rep['model']['name']}", "", "Read each item and decide: was the **model** wrong, or the **grader**?", ""]
    n = 0
    for t in sorted(rep["tests"], key=lambda x: (x["domain"], x["id"])):
        issues = [i for i in ((t.get("health") or {}).get("issues") or []) if i["severity"] != "minor"]
        if t["passed"] and not issues and not t.get("error"):
            continue
        n += 1
        convo = "\n".join(f"[{m['role']}] {m['content']}" for m in t["messages"])[-1800:] if t.get("messages") else t["prompt"][-500:]
        lines += [f"## {t['id']} — {t['name']}  ({'ERROR' if t.get('error') else 'pass' if t['passed'] else 'FAIL'}, score {t['score']:.2f})", "",
                  "**Conversation (tail):**" if t.get("messages") else "**Prompt (tail):**", "```", convo, "```", "**Response:**", "```", (t["response"] or t.get("error") or "")[:1800], "```"]
        for c in t["checks"]:
            if not c["passed"]:
                lines.append(f"- ✗ check `{c['name']}` — {c['detail']}")
        for i in issues:
            lines.append(f"- ⚠ health `{i['kind']}` ({i['severity']}) — {i['detail']}")
        lines.append("")
    if n == 0:
        lines.append("_Nothing failed or was flagged._")
    return "\n".join(lines)


async def resolve_models(requested: list[str], progress: Callable[[str], None]) -> tuple[list[str], list[str]]:
    live, is_live = await orp.fetch_models(force=True)
    if not is_live:
        raise RuntimeError("Could not fetch OpenRouter's live model list (network or base URL problem).")
    ids = [m["id"] for m in live]
    out, skipped = [], []
    for slug in requested:
        if slug in ids:
            out.append(slug)
            continue
        close = difflib.get_close_matches(slug, ids, n=1, cutoff=0.8)
        if close:
            progress(f"  ! '{slug}' not listed; using closest match '{close[0]}'")
            out.append(close[0])
        else:
            progress(f"  ! '{slug}' not available on OpenRouter — skipped")
            skipped.append(slug)
    return out, skipped


async def verify(models: list[str], *, key: str | None = None, quick: bool = False, out_dir: str | Path | None = None,
                 stress: bool = True, stress_model: str | None = None, repeat: bool = False, require_spread: bool = False,
                 progress: Callable[[str], None] = print) -> VerifyResult:
    res = VerifyResult()
    out = Path(out_dir) if out_dir else None
    if out:
        (out / "reports").mkdir(parents=True, exist_ok=True)
        (out / "review").mkdir(parents=True, exist_ok=True)
    key = orp.api_key(key)
    if not key:
        raise RuntimeError("No OpenRouter API key (set OPENROUTER_API_KEY or pass key=).")

    async def run(slug: str, **extra: Any) -> dict[str, Any]:
        opts = {"provider": "openrouter", "openrouter_model": slug, "openrouter_key": key, "quick": quick, **extra}
        return await Runner("verify", opts, lambda ev: None).run()

    def save(slug: str, tag: str, rep: dict[str, Any]) -> None:
        if not out:
            return
        safe = slug.replace("/", "__") + (f"--{tag}" if tag else "")
        (out / "reports" / f"{safe}.json").write_text(__import__("json").dumps(rep, indent=1))
        (out / "reports" / f"{safe}.md").write_text(to_markdown(rep))
        (out / "review" / f"{safe}.md").write_text(review_dump(rep))

    slugs, res.skipped = await resolve_models(models, progress)
    baseline: dict[str, dict[str, Any]] = {}
    for slug in slugs:
        progress(f"▶ evaluating {slug} …")
        try:
            rep = await run(slug)
        except Exception as e:  # noqa: BLE001
            progress(f"  ✗ {slug} failed: {e}")
            res.checks.append(Check(f"evaluation of {slug} completes", "FAIL", str(e)[:300]))
            continue
        baseline[slug] = rep
        save(slug, "", rep)
        r = _row(slug, rep)
        res.rows.append(r)
        progress(f"  ✓ overall {r['overall']:.1f} · coherency {r['coherency']:.0f} · coding {r['coding']:.0f} · math {r['math']:.0f} · general {r['general']:.0f} "
                 f"· system {r['system'] if r['system'] is not None else 'n/a'} · hyper {r['sampling'] if r['sampling'] is not None else 'n/a'} "
                 f"· {r['tps'] or 0:.0f} tok/s · cost {('$%.4f' % r['cost']) if r['cost'] is not None else 'n/a'}")
    if not res.rows:
        res.checks.append(Check("at least one model evaluated", "FAIL", "no model completed"))
        return res

    rows = res.rows
    best = max(rows, key=lambda r: r["overall"])
    worst = min(rows, key=lambda r: r["overall"])

    # ---- infrastructure
    bad_err = [r["model"] for r in rows if r["errors"] > 0.05 * r["tests"]]
    res.checks.append(Check("requests succeed (≤5% infrastructure errors per model)", "FAIL" if bad_err else "PASS",
                            f"too many request errors for: {', '.join(bad_err)}" if bad_err else f"max errors in any run: {max(r['errors'] for r in rows)}/{rows[0]['tests']}"))

    # ---- discrimination
    if len(rows) >= 3:
        spread = best["overall"] - worst["overall"]
        if spread >= 10:
            res.checks.append(Check("suite separates strong from weak models", "PASS", f"{best['model']} {best['overall']:.1f} vs {worst['model']} {worst['overall']:.1f} (spread {spread:.1f})"))
        else:
            res.checks.append(Check("suite separates strong from weak models", "FAIL" if require_spread else "WARN",
                                    f"spread only {spread:.1f} — include a clearly weaker model (e.g. a 1B) to assess discrimination"))
        dom_bad = [d for d in ("coding", "math", "general") if best[d] + 1e-9 < worst[d]]
        res.checks.append(Check("strongest model is not beaten by the weakest in any domain", "WARN" if dom_bad else "PASS",
                                f"weakest wins in: {', '.join(dom_bad)}" if dom_bad else "ordering holds in coding, math and general"))
    else:
        res.checks.append(Check("suite separates strong from weak models", "WARN", "need ≥3 models to assess"))

    # ---- system prompts
    sysrows = [r for r in rows if r["system"] is not None]
    if len(sysrows) >= 3:
        sp = max(r["system"] for r in sysrows) - min(r["system"] for r in sysrows)
        res.checks.append(Check("system-prompt suite separates strong from weak models", "PASS" if sp >= 8 else ("FAIL" if require_spread else "WARN"),
                                f"best {max(r['system'] for r in sysrows):.0f} vs worst {min(r['system'] for r in sysrows):.0f} (spread {sp:.0f})"))
    strong_sys = [r for r in sysrows if r["overall"] >= 80]
    weak_follow = [r["model"] for r in strong_sys if r["system"] < 60]
    res.checks.append(Check("high-scoring models follow system prompts (≥ 60/100)", "WARN" if weak_follow else "PASS" if strong_sys else "WARN",
                            f"scored low on system prompts despite ≥80 overall: {', '.join(weak_follow)} — read review/*.md" if weak_follow else
                            (f"{len(strong_sys)} high-scoring model(s), lowest system score {min(r['system'] for r in strong_sys):.0f}" if strong_sys else "no model scored ≥80")))
    sys_missing = [r["model"] for r in rows if r["system"] is None]
    res.checks.append(Check("system-prompt suite ran on every model", "FAIL" if sys_missing else "PASS", f"missing for: {', '.join(sys_missing)}" if sys_missing else f"{len(rows)} model(s)"))

    # ---- hyperparameters
    smp = [(r["model"], r["report"].get("sampling") or {}) for r in rows]
    failed = [m for m, x in smp if x.get("status") != "ok"]
    res.checks.append(Check("hyperparameter sweeps complete on every model", "FAIL" if failed else "PASS",
                            f"sweeps failed or were skipped for: {', '.join(failed)}" if failed else f"{len(smp)} model(s), {sum(x['requests'] for _, x in smp)} requests in total"))
    ok_smp = [(m, x) for m, x in smp if x.get("status") == "ok"]
    if ok_smp:
        by_ctl = lambda x, i: next((c["status"] for c in x["controls"] if c["id"] == i), None)  # noqa: E731
        t_bad = [m for m, x in ok_smp if by_ctl(x, "temperature") != "honored"]
        res.checks.append(Check("temperature is honoured by every model/provider", "WARN" if t_bad else "PASS",
                                f"no observable effect for: {', '.join(t_bad)}" if t_bad else "output diversity rises with temperature everywhere"))
        mt_bad = [m for m, x in ok_smp if by_ctl(x, "max_tokens") != "honored" or by_ctl(x, "stop") not in ("honored", "rejected")]
        res.checks.append(Check("max_tokens and stop sequences are honoured", "WARN" if mt_bad else "PASS",
                                f"not honoured for: {', '.join(mt_bad)}" if mt_bad else "length caps and stop strings work"))
        broke = [m for m, x in ok_smp if (x["temperature"]["points"][-1].get("clean") is not None and x["temperature"]["points"][-1]["clean"] < 0.5)]
        top = max(x["temperature"]["points"][-1]["t"] for _, x in ok_smp)
        res.checks.append(Check("the temperature sweep exposes high-temperature breakdown on real output", "PASS" if broke else "WARN",
                                f"word salad at the top temperature for: {', '.join(broke)}" if broke else
                                (f"quick mode tops out at T = {top:g}: run the full suite to see breakdown" if top < 1.8 else "no model degraded at the highest temperature (provider may clamp it)")))
        strong_clean = [m for m, x in ok_smp if (x["temperature"]["points"][0].get("clean") or 0) < 0.9 and rows[[r["model"] for r in rows].index(m)]["overall"] >= 80]
        res.checks.append(Check("greedy decoding of strong models is not flagged as degenerate", "WARN" if strong_clean else "PASS",
                                f"flagged: {', '.join(strong_clean)}" if strong_clean else "clean at temperature 0"))

    # ---- no false alarms on strong models
    strong = [r for r in rows if r["overall"] >= 80]
    false_alarm = [r["model"] for r in strong if r["garble"] > 0.05 or r["leak"] > 0.05 or r["severe"] > 0.10]
    res.checks.append(Check("high-scoring models are not flagged as garbled / leaking", "FAIL" if false_alarm else "PASS" if strong else "WARN",
                            f"flagged despite scoring ≥80: {', '.join(false_alarm)} — read review/*.md" if false_alarm else
                            (f"{len(strong)} high-scoring model(s), worst severe-rate {max(r['severe'] for r in strong):.0%}" if strong else "no model scored ≥80")))

    # ---- hosted semantics
    hosted_bad = [r["model"] for r in rows if r["report"]["speculative"]["status"] != "unknown" or r["report"]["performance"].get("roofline")]
    res.checks.append(Check("hosted runs make no speculative-decoding or hardware claims", "FAIL" if hosted_bad else "PASS",
                            f"unexpected claims for: {', '.join(hosted_bad)}" if hosted_bad else "status 'unknown', no roofline"))
    priced = [r for r in rows if (r["report"]["environment"].get("pricing") or {}).get("completion_per_m")]
    cost_bad = [r["model"] for r in priced if not r["cost"]]
    res.checks.append(Check("cost is tracked for paid models", "FAIL" if cost_bad else "PASS" if priced else "WARN",
                            f"no cost reported for: {', '.join(cost_bad)}" if cost_bad else (f"{len(priced)} paid model(s) reported cost" if priced else "only free models")))

    # ---- stress controls on a real model
    if stress:
        target = stress_model if stress_model in baseline else best["model"]
        base = baseline[target]
        for kind in ("garble", "loop"):
            progress(f"▶ stress control '{kind}' on {target} …")
            try:
                rep = await run(target, stress=kind, quick=True)
            except Exception as e:  # noqa: BLE001
                res.checks.append(Check(f"stress '{kind}' run completes", "FAIL", str(e)[:300]))
                continue
            save(target, f"stress-{kind}", rep)
            c, b = rep["coherency"], base["coherency"]
            drop = base["scores"]["coherency"] - rep["scores"]["coherency"]
            if kind == "garble":
                fired = c["garble_rate"] >= 0.3 or drop >= 25
                detail = f"garble rate {b['garble_rate']:.0%} → {c['garble_rate']:.0%}; coherency {base['scores']['coherency']:.0f} → {rep['scores']['coherency']:.0f}"
            else:
                fired = (c["repetition_rate"] + c["runaway_rate"]) >= 0.3 or c["severe_rate"] >= 0.3 or drop >= 25
                detail = f"repetition {b['repetition_rate']:.0%} → {c['repetition_rate']:.0%}, runaway {b['runaway_rate']:.0%} → {c['runaway_rate']:.0%}; coherency {base['scores']['coherency']:.0f} → {rep['scores']['coherency']:.0f}"
            res.checks.append(Check(f"detectors fire on real '{kind}' corruption ({target})", "PASS" if fired else "FAIL", detail))
            res.checks.append(Check(f"'{kind}' stress never yields a 'ready' verdict", "FAIL" if rep["verdict"]["label"] == "ready" else "PASS", f"verdict: {rep['verdict']['title']}"))
            res.rows.append(_row(target, rep, label=f"stress:{kind}"))

    # ---- reproducibility
    if repeat:
        progress(f"▶ repeat run of {best['model']} for reproducibility …")
        rep2 = await run(best["model"], quick=quick)
        d = abs(rep2["scores"]["overall"] - best["overall"])
        res.checks.append(Check("results are reproducible across runs (|Δ overall| ≤ 8)", "PASS" if d <= 8 else "FAIL",
                                f"{best['overall']:.1f} vs {rep2['scores']['overall']:.1f}"))

    if out:
        (out / "summary.md").write_text(summary_markdown(res))
    return res


def summary_markdown(res: VerifyResult) -> str:
    L = ["# Suite verification summary", "", "| Model | Run | Overall | Coherency | Coding | Math | General | System | Hyper | Verdict | tok/s | Cost |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    f = lambda v: "—" if v is None else f"{v:.0f}"  # noqa: E731
    for r in res.rows:
        L.append(f"| {r['model']} | {r['label'] or 'baseline'} | {r['overall']:.1f} | {r['coherency']:.0f} | {r['coding']:.0f} | {r['math']:.0f} | {r['general']:.0f} | {f(r['system'])} | {f(r['sampling'])} | {r['verdict']} | "
                 f"{(r['tps'] or 0):.0f} | {('$%.4f' % r['cost']) if r['cost'] is not None else '—'} |")
    L += ["", "| Check | Result | Detail |", "|---|---|---|"]
    icon = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "WARN": "⚠️ WARN"}
    for c in res.checks:
        L.append(f"| {c.name} | {icon[c.status]} | {c.detail} |")
    if res.skipped:
        L += ["", f"Skipped (not on OpenRouter): {', '.join(res.skipped)}"]
    L += ["", f"**Overall: {'PASS' if res.ok else 'FAIL'}**", "", "_Human step: open review/*.md and confirm each flagged item was the model's fault, not the grader's._"]
    return "\n".join(L)
