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
    return "\n".join(L)
