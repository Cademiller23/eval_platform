"""Detect and quantify speculative decoding from three independent signals.

1. **Config**   — what the engine was launched with (known for Modal/demo, unknown for custom endpoints)
2. **Metrics**  — Prometheus counters (``vllm:spec_decode_*`` / ``sglang:spec_accept_*``) diffed across the run
3. **Behaviour**— speculative engines emit several tokens per step, so streams arrive in multi-token
                  bursts; a plain autoregressive engine emits exactly one token per chunk.
"""
from __future__ import annotations

import re
from typing import Any

_LINE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+([-+0-9.eE]+|NaN|Inf|\+Inf)\s*$")


def _sum_metric(rows: list[tuple[str, float]], *names: str) -> float | None:
    total, found = 0.0, False
    for n, v in rows:
        base = n.replace("_total", "")
        if any(base.endswith(x) or n.endswith(x) for x in names):
            total += v
            found = True
    return total if found else None


def parse_prom(text: str | None) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    if not text:
        return rows
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line.strip())
        if not m:
            continue
        try:
            rows.append((m.group(1), float(m.group(3))))
        except ValueError:
            continue
    return rows


def spec_counters(text: str | None) -> dict[str, float] | None:
    """Extract speculative-decoding counters; None when the engine exposes none."""
    rows = parse_prom(text)
    if not rows:
        return None
    drafts = _sum_metric(rows, "spec_decode_num_drafts")
    draft_tokens = _sum_metric(rows, "spec_decode_num_draft_tokens")
    accepted = _sum_metric(rows, "spec_decode_num_accepted_tokens")
    out: dict[str, float] = {}
    if drafts is not None:
        out["drafts"] = drafts
    if draft_tokens is not None:
        out["draft_tokens"] = draft_tokens
    if accepted is not None:
        out["accepted"] = accepted
    # SGLang exposes gauges instead of counters
    for n, v in rows:
        if n.endswith("spec_accept_length"):
            out["sglang_accept_length"] = v
        if n.endswith("spec_accept_rate"):
            out["sglang_accept_rate"] = v
    return out or None


def analyze(info: dict[str, Any], before: str | None, after: str | None, behaviour: dict[str, float], model: dict[str, Any]) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    cfg = info.get("speculative_config")
    cfg_known = bool(info.get("speculative_known"))
    method = (cfg or {}).get("method") if cfg else None
    if cfg and not method and cfg.get("model"):
        method = "draft_model"

    # ---- config
    if cfg:
        evidence.append({"source": "config", "positive": True, "text": f"Engine launched with speculative config {cfg}."})
    elif cfg_known:
        evidence.append({"source": "config", "positive": False, "text": "Engine was launched without a speculative-decoding config."})
    else:
        evidence.append({"source": "config", "positive": None, "text": "Server launch configuration is not visible for this endpoint."})
    for line in info.get("speculative_log_lines") or []:
        evidence.append({"source": "logs", "positive": True, "text": line[:200]})

    # ---- metrics
    a, b = spec_counters(before), spec_counters(after)
    drafts = draft_tokens = accepted = None
    accept_rate = mean_len = None
    metrics_positive = None
    if b is None:
        evidence.append({"source": "metrics", "positive": False if info.get("metrics_available") else None,
                         "text": "No speculative-decoding counters in /metrics." if info.get("metrics_available") else "Engine metrics endpoint is not reachable."})
    else:
        if "sglang_accept_length" in b:
            mean_len = b["sglang_accept_length"]
            accept_rate = b.get("sglang_accept_rate")
            metrics_positive = mean_len > 1.05
            evidence.append({"source": "metrics", "positive": metrics_positive, "text": f"SGLang reports mean accept length {mean_len:.2f}."})
        else:
            base = a or {}
            drafts = b.get("drafts", 0) - base.get("drafts", 0)
            draft_tokens = b.get("draft_tokens", 0) - base.get("draft_tokens", 0)
            accepted = b.get("accepted", 0) - base.get("accepted", 0)
            if drafts and drafts > 0:
                mean_len = 1 + accepted / drafts
                if draft_tokens:
                    accept_rate = accepted / draft_tokens
                metrics_positive = True
                evidence.append({"source": "metrics", "positive": True,
                                 "text": f"{int(drafts)} draft rounds, {int(draft_tokens)} drafted / {int(accepted)} accepted tokens "
                                         f"→ {accept_rate:.0%} acceptance, {mean_len:.2f} tokens per step."})
            else:
                metrics_positive = False
                evidence.append({"source": "metrics", "positive": False, "text": "Speculative counters exist but did not advance during the run (speculation configured yet unused)."})

    # ---- behaviour
    ratio = behaviour.get("multi_token_chunk_ratio", 0.0)
    tpc = behaviour.get("tokens_per_chunk", 1.0)
    behaviour_positive = None
    if behaviour.get("chunks", 0) >= 30:
        behaviour_positive = ratio > 0.12 and tpc > 1.12
        evidence.append({"source": "behavior", "positive": behaviour_positive,
                         "text": f"{ratio:.0%} of stream chunks carried more than one token ({tpc:.2f} tokens/chunk on average)"
                                 + (" — bursty arrival typical of draft-and-verify decoding." if behaviour_positive else " — steady one-token-per-step arrival of plain autoregressive decoding.")})

    # ---- verdict
    if metrics_positive:
        status, conf = "active", 0.97
    elif cfg and metrics_positive is None and behaviour_positive:
        status, conf = "active", 0.85
    elif cfg and metrics_positive is False:
        status, conf = "likely", 0.5      # configured, but no speculative activity observed
    elif cfg:
        status, conf = "likely", 0.7
    elif behaviour_positive:
        status, conf = "likely", 0.65
    elif cfg_known or metrics_positive is False:
        status, conf = "not_detected", 0.93 if cfg_known else 0.75
    else:
        status, conf = "unknown", 0.3

    if status == "likely" and cfg and metrics_positive is False:
        headline = "Speculative decoding is configured but showed no activity"
    else:
        headline = {
            "active": "Speculative decoding is active",
            "likely": "Speculative decoding is likely active",
            "not_detected": "Speculative decoding is not in use",
            "unknown": "Could not determine whether speculative decoding is used",
        }[status]

    eligible = []
    if model.get("mtp_native"):
        eligible.append({"method": "mtp", "repo": None, "note": "Model ships native multi-token-prediction layers.", "verified": True})
    eligible += list(model.get("speculators") or [])

    return {
        "status": status,
        "headline": headline,
        "confidence": conf,
        "method": method,
        "config": cfg,
        "evidence": evidence,
        "drafts": drafts,
        "draft_tokens": draft_tokens,
        "accepted_tokens": accepted,
        "acceptance_rate": accept_rate,
        "mean_accepted_length": mean_len,
        "multi_token_chunk_ratio": ratio,
        "tokens_per_chunk": tpc,
        "eligible": eligible,
        "native_mtp": bool(model.get("mtp_native")),
    }


def copy_ratio(prompt: str, response: str, n: int = 3) -> float:
    """Share of response word n-grams that already appear in the prompt.

    A cheap predictor of how well *prompt-lookup (n-gram)* speculation will do: the draft is just
    'copy what followed this phrase earlier in the context'.
    """
    pw = re.findall(r"\w+", prompt.lower())
    rw = re.findall(r"\w+", response.lower())
    if len(rw) < n + 3:
        return 0.0
    grams = {tuple(pw[i:i + n]) for i in range(len(pw) - n + 1)}
    hits = sum(1 for i in range(len(rw) - n + 1) if tuple(rw[i:i + n]) in grams)
    return hits / (len(rw) - n + 1)
