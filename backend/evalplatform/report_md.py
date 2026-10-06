"""Markdown export of a finished report."""
from __future__ import annotations

from typing import Any


def _f(v: Any, nd: int = 1, suffix: str = "") -> str:
    return "—" if v is None else f"{v:.{nd}f}{suffix}"


def to_markdown(r: dict[str, Any]) -> str:
    m, s, v, p, sp, env = r["model"], r["scores"], r["verdict"], r["performance"], r["speculative"], r["environment"]
    L: list[str] = []
    L += [f"# Evaluation report — {m['name']}", "", f"`{m['hf_repo']}` · generated {r['generated_at']} · {r['duration_s']}s", ""]
    L += [f"## Verdict: {v['title']} ({s['overall']:.0f}/100, grade {s['grade']})", "", v["summary"], ""]
    for title, items in (("Blockers", v["blockers"]), ("Warnings", v["warnings"]), ("Strengths", v["strengths"])):
        if items:
            L += [f"**{title}**", ""] + [f"- {i}" for i in items] + [""]
    L += ["## Scores", "", "| Dimension | Score |", "|---|---|"]
    for k in ("coherency", "coding", "math", "general", "performance"):
        L.append(f"| {k.title()} | {s[k]:.0f} |")
    L += ["", "## Performance", "",
          f"- Decode: **{_f(p.get('decode_tps_median'))} tok/s** (runs: {', '.join(str(x) for x in p.get('decode_tps_runs', []))})",
          f"- TTFT: p50 {_f(p.get('ttft_ms_p50'), 0)} ms · p95 {_f(p.get('ttft_ms_p95'), 0)} ms · long prompt {_f(p.get('ttft_long_ms'), 0)} ms"]
    if p.get("concurrent"):
        L.append(f"- Concurrency ×{p['concurrent']['n']}: {_f(p['concurrent']['aggregate_tps'])} tok/s aggregate")
    if p.get("roofline"):
        L.append(f"- Hardware roofline: {p['roofline']['theoretical_tps']} tok/s → {p['roofline']['efficiency']:.0%} efficiency")
    L += ["", f"## Speculative decoding: {sp['headline']}", ""]
    for e in sp["evidence"]:
        L.append(f"- _{e['source']}_: {e['text']}")
    L += ["", "## Environment", "", f"- {env.get('engine')} {env.get('engine_version')} on {env.get('gpu')} ×{env.get('gpu_count')}", f"- Cold start: {env.get('cold_start_s')}s", ""]
    coh = r["coherency"]
    L += ["## Coherency", "", f"- Clean responses: {coh['clean_ratio']:.0%} · garbled {coh['garble_rate']:.0%} · repetitive {coh['repetition_rate']:.0%} · token leaks {coh['special_token_leak_rate']:.0%}", ""]
    L += _system_md(r.get("system_prompts"))
    L += _sampling_md(r.get("sampling"))
    L += ["## Tests", "", "| Domain | Test | Result | Score |", "|---|---|---|---|"]
    for t in sorted(r["tests"], key=lambda x: (x["domain"], x["id"])):
        L.append(f"| {t['domain']} | {t['name']} | {'✅' if t['passed'] else '❌'} | {t['score']:.2f} |")
    rec = r["recommendations"]
    L += ["", "## Speculative decoding — how to implement", "", rec["speculative"]["summary"], ""]
    for i, st in enumerate(rec["speculative"]["steps"], 1):
        L += [f"### {i}. {st['title']}", "", st["body"], ""]
        if st.get("code"):
            L += [f"```{st.get('lang') or ''}", st["code"], "```", ""]
    L += ["## Make it faster", ""]
    for x in rec["speed"]:
        L += [f"### {x['title']} ({x['impact']} impact)", "", f"_{x['why']}_", "", x["detail"], ""]
        if x.get("code"):
            L += ["```", x["code"], "```", ""]
    L += ["## Make it more coherent", ""]
    for x in rec["coherence"]:
        L += [f"### {x['title']} ({x['impact']})", "", f"_{x['why']}_", "", x["detail"], ""]
        if x.get("code"):
            L += ["```", x["code"], "```", ""]
    for key, title in (("system", "Harden your system prompt"), ("sampling", "Tune the sampling settings")):
        if rec.get(key):
            L += [f"## {title}", ""]
            for x in rec[key]:
                L += [f"### {x['title']} ({x['impact']})", "", f"_{x['why']}_", "", x["detail"], ""]
                if x.get("code"):
                    L += ["```", x["code"], "```", ""]
    return "\n".join(L)


def _pct(v: Any) -> str:
    return "—" if v is None else f"{v:.0%}"


def _system_md(sp: dict[str, Any] | None) -> list[str]:
    if not sp:
        return []
    m = sp["metrics"]
    L = [f"## System prompts: {sp['score']:.0f}/100", "", "| Category | Score | Passed |", "|---|---|---|"]
    for c in sp["categories"].values():
        L.append(f"| {c['label']} | {c['score']:.0f} | {c['passed']}/{c['total']} |")
    L += ["", f"- Prompt-injection attack success rate: **{_pct(m.get('injection_asr'))}** ({m.get('injection_attacks', 0)} attempts)",
          f"- Confidentiality: secrets leaked in **{len(m.get('leaked') or [])}** of {m.get('leak_attacks', 0)} extraction attempts",
          f"- Over-refusal: {', '.join(m.get('over_refusals') or []) or 'none'}"]
    if m.get("capacity") is not None:
        L.append(f"- Rule capacity: all rules followed up to **{m['capacity']}** simultaneous rules")
    if not sp["role"].get("supported", True):
        L.append("- The chat template has no system role; prompts were folded into the first user message")
    L += ["", *[f"- {f['text']}" for f in sp.get("findings", [])], ""]
    return L


def _sampling_md(sm: dict[str, Any] | None) -> list[str]:
    if not sm or sm.get("status") != "ok":
        return []
    t = sm["temperature"]
    L = [f"## Hyperparameters: {sm['score']['overall']:.0f}/100", "",
         f"Best temperature **{t['best_t']:g}** · safe ceiling **{t['cliff_t']:g}**" + (f" · degrades from {t['breaks_at']:g}" if t.get("breaks_at") is not None else "") +
         f" · {sm['requests']} requests", "", "| Temperature | Accuracy | Clean output | Diversity |", "|---|---|---|---|"]
    for p in t["points"]:
        L.append(f"| {p['t']:g} | {_pct(p.get('acc'))} | {_pct(p.get('clean'))} | {_pct(p.get('diversity'))} |")
    L += ["", "| Parameter | Status | Detail |", "|---|---|---|"]
    for c in sm["controls"]:
        L.append(f"| {c['label']} | {c['status']} | {c['detail']} |")
    L += ["", "**Recommended settings**", ""]
    for pr in sm.get("profiles", {}).values():
        L.append(f"- {pr['label']} ({pr['for']}): `{', '.join(f'{k}={v:g}' for k, v in pr['params'].items())}`")
    for c in sm["validation"].get("comparisons", []):
        L.append(f"- Held-out check, tuned vs {c['against'].replace('_', ' ')}: {c['delta']:+.0%} (95% CI {c['ci'][0]:+.0%} to {c['ci'][1]:+.0%}), {c['verdict'].replace('_', ' ')}")
    L += ["", *[f"- {f['text']}" for f in sm.get("findings", [])], ""]
    return L
