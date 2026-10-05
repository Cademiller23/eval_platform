"""Evidence-driven recommendations: speculative decoding how-to, speed-ups, coherency fixes.

Everything is generated from (a) the catalog entry for the model and (b) what the run actually
measured, so advice cites its evidence and differs from model to model.
"""
from __future__ import annotations

import json
from typing import Any

from .knowledge import GPUS, GPU_ORDER, bytes_per_param, match_gpu, parse_gpu, roofline_tps

SERVED_NAME = "eval-model"

# ----------------------------------------------------------------------------------------
# Speculative decoding
# ----------------------------------------------------------------------------------------
METHOD_INFO = {
    "mtp": dict(title="Native multi-token prediction (MTP)", speedup="1.5–2.2×",
                pros=["Built into the checkpoint — nothing extra to download or train", "High acceptance rates because the head was trained with the model"],
                cons=["Only 1–2 speculative tokens per step", "Needs a recent vLLM/SGLang build with MTP support for this architecture"]),
    "eagle3": dict(title="EAGLE-3 draft head", speedup="2–3.5× at low batch",
                   pros=["Largest single-stream speed-up of any lossless method", "Tiny extra weights (a single decoder layer) on top of the target model"],
                   cons=["Needs a head trained for exactly this model (and chat template)", "Gain shrinks as batch size / concurrency grows"]),
    "ngram": dict(title="N-gram / prompt-lookup", speedup="1.0–2.5×",
                  pros=["Zero extra weights, works with every model, trivial to enable", "Excellent on code edits, RAG, summarisation, JSON — anything that copies from the prompt"],
                  cons=["Little or no gain on free-form generation", "Can slightly slow down open-ended chat when drafts are rarely accepted"]),
    "draft_model": dict(title="Small draft model", speedup="1.5–2.5×",
                        pros=["Works with any model that has a small sibling sharing its tokenizer"],
                        cons=["Extra GPU memory and a second forward pass per step", "Engine support varies by version — vLLM V1 only recently regained it; SGLang/TensorRT-LLM support it",
                              "Draft and target must share the exact vocabulary"]),
}


def _spec_config(method: str, model: dict[str, Any], entry: dict[str, Any] | None) -> dict[str, Any]:
    if method == "ngram":
        return {"method": "ngram", "num_speculative_tokens": 5, "prompt_lookup_max": 4, "prompt_lookup_min": 2}
    if method == "eagle3":
        return {"method": "eagle3", "model": (entry or {}).get("repo", "<eagle3-head-repo>"), "num_speculative_tokens": 3}
    if method == "mtp":
        return {"method": model.get("mtp_method") or "mtp", "num_speculative_tokens": 1}
    if method == "draft_model":
        return {"model": (entry or {}).get("repo", "<small-draft-model>"), "num_speculative_tokens": 5}
    raise ValueError(method)


def _vllm_cmd(model: dict[str, Any], cfg: dict[str, Any], max_len: int, gpu_count: int) -> str:
    parts = [f"vllm serve {model['hf_repo']}", f"--max-model-len {max_len}"]
    if gpu_count > 1:
        parts.append(f"--tensor-parallel-size {gpu_count}")
    if model.get("trust_remote_code"):
        parts.append("--trust-remote-code")
    parts.append(f"--speculative-config '{json.dumps(cfg)}'")
    return " \\\n  ".join(parts)


def _sglang_cmd(model: dict[str, Any], method: str, entry: dict[str, Any] | None, gpu_count: int) -> str | None:
    base = f"python -m sglang.launch_server --model-path {model['hf_repo']}" + (f" --tp {gpu_count}" if gpu_count > 1 else "")
    if method == "eagle3" and entry:
        return (f"{base} \\\n  --speculative-algorithm EAGLE3 --speculative-draft-model-path {entry['repo']} \\\n"
                "  --speculative-num-steps 3 --speculative-eagle-topk 1 --speculative-num-draft-tokens 4")
    if method == "mtp":
        return f"{base} \\\n  --speculative-algorithm EAGLE --speculative-num-steps 1 --speculative-eagle-topk 1 --speculative-num-draft-tokens 2"
    if method == "draft_model" and entry:
        return f"{base} \\\n  --speculative-algorithm STANDALONE --speculative-draft-model-path {entry['repo']} --speculative-num-steps 4 --speculative-eagle-topk 1 --speculative-num-draft-tokens 5"
    if method == "ngram":
        return f"{base} \\\n  --speculative-algorithm NGRAM"
    return None


def rank_methods(model: dict[str, Any], copy_ratios: dict[str, float]) -> list[dict[str, Any]]:
    """Order applicable methods best-first for this model + workload."""
    avg_copy = sum(copy_ratios.values()) / len(copy_ratios) if copy_ratios else 0.0
    code_copy = copy_ratios.get("coding", 0.0)
    cands: list[tuple[float, str, dict[str, Any] | None, str]] = []
    if model.get("mtp_native"):
        cands.append((100, "mtp", None, "The checkpoint ships MTP layers trained jointly with the model, giving the best acceptance with zero extra weights."))
    for sp in model.get("speculators", []):
        m = sp["method"]
        if m == "eagle3":
            conf = 90 if sp.get("verified", True) else 82
            cands.append((conf, "eagle3", sp, "A trained EAGLE-3 head exists for this model; it gives the biggest low-batch speed-up of any lossless method."))
        elif m == "draft_model":
            cands.append((50, "draft_model", sp, f"{sp['repo']} shares this model's tokenizer and can draft for it. Engine support is the catch — verify your vLLM version supports draft models, or use SGLang."))
        elif m == "ngram":
            score = 55 + 60 * max(avg_copy, code_copy * 0.8)
            why = (f"No extra weights needed. Your outputs reuse {avg_copy:.0%} of their phrasing from the prompt on average"
                   f" ({code_copy:.0%} in coding tasks), which predicts decent prompt-lookup acceptance.")
            cands.append((score, "ngram", sp, why))
    cands.sort(key=lambda c: -c[0])
    out = []
    for i, (score, method, entry, why) in enumerate(cands):
        info = METHOD_INFO[method]
        out.append({
            "method": method, "title": info["title"], "fit": "best" if i == 0 else ("good" if score >= 58 else "ok"),
            "expected_speedup": info["speedup"], "why": why, "pros": info["pros"], "cons": info["cons"],
            "repo": (entry or {}).get("repo"), "verified": (entry or {}).get("verified", True),
            "note": (entry or {}).get("note"),
        })
    return out


def speculative_plan(model: dict[str, Any], info: dict[str, Any], spec: dict[str, Any], perf: dict[str, Any],
                     options: dict[str, Any], copy_ratios: dict[str, float]) -> dict[str, Any]:
    if info.get("hosted"):
        return _hosted_speculative_plan(model, info, spec, perf, options, copy_ratios)
    max_len = int(info.get("max_model_len") or options.get("max_model_len") or 8192)
    gpu_name, gpu_n = parse_gpu(info.get("requested_gpu") or options.get("gpu") or model.get("min_gpu"))
    gpu_n = max(gpu_n, int(info.get("gpu_count") or 1))
    methods = rank_methods(model, copy_ratios)
    entries = {sp["method"]: sp for sp in model.get("speculators", [])}
    for m in methods:
        e = entries.get(m["method"])
        cfg = _spec_config(m["method"], model, e)
        m["config"] = cfg
        m["vllm_cmd"] = _vllm_cmd(model, cfg, max_len, gpu_n)
        m["sglang_cmd"] = _sglang_cmd(model, m["method"], e, gpu_n)
        m["platform_option"] = m["method"]

    status = spec["status"]
    tps = perf.get("decode_tps_median")
    active_method = spec.get("method")
    top = methods[0] if methods else None
    steps: list[dict[str, Any]] = []

    if status in ("active", "likely") and spec.get("config"):
        ar, ml = spec.get("acceptance_rate"), spec.get("mean_accepted_length")
        summary = (f"Speculative decoding ({active_method or 'custom'}) is running"
                   + (f" with {ar:.0%} draft acceptance and {ml:.2f} tokens per step" if ar is not None and ml else "")
                   + ". Focus on tuning it and confirming it did not hurt quality.")
        k = (spec.get("config") or {}).get("num_speculative_tokens", 4)
        tune = "Acceptance is healthy — try raising num_speculative_tokens by 1–2 and keep the setting that maximises tok/s." if (ar or 0) >= 0.6 else \
               "Acceptance is low — lower num_speculative_tokens (try 2–3); wasted draft tokens cost compute." if ar is not None else \
               "No acceptance counters were visible; check /metrics for vllm:spec_decode_* to tune k."
        steps = [
            _step("Read the numbers", f"Draft acceptance: {ar:.0%}. Mean tokens/step: {ml:.2f}. Theoretical ceiling ≈ {ml:.2f}× fewer forward passes; real gain is lower because drafting has a cost." if ar is not None and ml else "Acceptance counters were not exposed by the engine.", None),
            _step("Tune the draft length", f"{tune} Currently k = {k}.", f"--speculative-config '{json.dumps({**(spec.get('config') or {}), 'num_speculative_tokens': max(1, k + (1 if (ar or 0) >= 0.6 else -1))})}'", "bash"),
            _step("Consider a stronger method", (f"{top['title']} would likely beat {active_method or 'the current method'} here: {top['why']}" if top and top["method"] != active_method else "You are already on the best-fit method for this model."), top["vllm_cmd"] if top and top["method"] != active_method else None, "bash"),
            _step("Verify quality parity", "Speculative decoding with rejection sampling is distribution-preserving, but implementation bugs and numerics can still shift outputs. Re-run this evaluation and confirm the coherency score did not drop versus the baseline run.", None),
        ]
    elif top is None:
        summary = "No ready-made speculative-decoding recipe is catalogued for this model; use n-gram speculation or train a draft head (EAGLE-3 / Medusa)."
        steps = [_step("Use n-gram speculation as a baseline", "Works with any model.", "--speculative-config '{\"method\":\"ngram\",\"num_speculative_tokens\":5,\"prompt_lookup_max\":4}'", "bash")]
    else:
        gain = top["expected_speedup"]
        baseline = f"{tps:.0f} tok/s" if tps else "your current speed"
        summary = (f"Speculative decoding is not enabled, so every token costs a full forward pass of {model['name']}. "
                   f"Enabling {top['title']} could lift decode speed from {baseline} to roughly {gain.split(' ')[0]} of that (typical, workload dependent).")
        cfg = top["config"]
        method = top["method"]
        entry = entries.get(method)
        s: list[dict[str, Any]] = []
        s.append(_step("Pick the method", f"{top['title']} — {top['why']}" + ("" if top.get("verified", True) else "  ⚠ This checkpoint is community-maintained: confirm the repo exists, matches your exact target model and license before depending on it."), None))
        s.append(_step("Check engine & hardware prerequisites",
                       f"Use vLLM ≥ 0.10 (this platform deploys {info.get('engine_version') or 'a pinned build'}) or a recent SGLang. "
                       + ("The head is small, so budget < 2 GB extra VRAM." if method == "eagle3" else
                          "No extra memory needed." if method in ("ngram", "mtp") else
                          "Budget extra VRAM for the draft model's weights and KV cache.")
                       + f" Your {gpu_name or 'GPU'} has {GPUS.get(gpu_name, {}).get('mem_gb', '?')} GB per device.", None))
        if method in ("eagle3", "draft_model") and entry:
            s.append(_step("Download the draft weights", f"Pre-fetch so the first launch is not blocked on the network:", f"huggingface-cli download {entry['repo']}", "bash"))
        s.append(_step("Launch with speculation enabled (vLLM)", "Add the speculative config to your serve command:", top["vllm_cmd"], "bash"))
        if top.get("sglang_cmd"):
            s.append(_step("…or with SGLang", "Equivalent flags on SGLang:", top["sglang_cmd"], "bash"))
        s.append(_step("Confirm it is really on",
                       "Look for a speculative-decoding line in the startup log, then watch the acceptance counters while sending traffic. Healthy acceptance is ≥ 50% for EAGLE-3/MTP and varies widely for n-gram.",
                       "curl -s localhost:8000/metrics | grep spec_decode", "bash"))
        s.append(_step("Measure the gain on this platform",
                       "Click “Try it now” on the method card above (or choose the speculative mode in the run options before selecting a model). The platform boots the same model with speculation enabled, re-runs the full suite and diffs decode tok/s and quality against this run. Keep it only if tok/s improves ≥ 1.3× and the coherency score does not fall.", None))
        s.append(_step("Tune num_speculative_tokens",
                       {"ngram": "Start at 5 and try 3–8; also tune prompt_lookup_max (3–6).",
                        "eagle3": "Start at 3 and try 2–5; deeper trees help at batch 1 but hurt at high load.",
                        "mtp": "Start at 1 (the number of MTP heads trained); only raise it if the model documents extra heads.",
                        "draft_model": "Start at 4–5 and sweep 3–8; pick the knee of the tok/s curve."}[method] + " Rule of thumb: acceptance < 40% → lower k, > 70% → raise k.", None))
        s.append(_step("Make it permanent on Modal",
                       "Pass the config as a class parameter to the serving app (or let this platform do it via the option). In your own code:",
                       f"import json, modal\nServer = modal.Cls.from_name(\"coherence-eval-serving\", \"ModelServer\").with_options(gpu=\"{info.get('requested_gpu') or gpu_name or 'H100'}\")\n"
                       f"server = Server(model=\"{model['hf_repo']}\",\n                spec_config=json.dumps({json.dumps(cfg)}))", "python"))
        s.append(_step("Production caveats",
                       "• Gains are largest at low concurrency; at high batch sizes the GPU is already compute-bound and speculation can break even or slow down.\n"
                       "• Acceptance (and speed-up) drops at high sampling temperature.\n"
                       "• Always compare quality after enabling it — re-run this evaluation and check the coherency score.\n"
                       "• Re-validate after every engine upgrade; speculative paths change quickly between releases.", None))
        steps = s

    return {
        "status": status,
        "summary": summary,
        "methods": methods,
        "steps": steps,
        "recommended": top["method"] if top else None,
        "apply": {"speculative": top["method"], "config": top["config"]} if top and status in ("not_detected", "unknown") else None,
    }


def _hosted_speculative_plan(model, info, spec, perf, options, copy_ratios) -> dict[str, Any]:
    slug = model.get("openrouter_slug") or model["name"]
    tps = perf.get("decode_tps_median")
    if not model.get("open_weights"):
        return {
            "status": spec["status"], "recommended": None, "apply": None, "methods": [],
            "summary": (f"{model['name']} is a closed-weights model served by its vendor. Speculative decoding, batching and quantisation "
                        "are entirely the provider's decision — there is nothing to configure or implement on your side."),
            "steps": [
                _step("What you can influence", "You cannot add speculative decoding to a closed model, but you can choose faster routing and shorter outputs:", None),
                _step("Prefer the fastest route", "OpenRouter can sort providers by throughput or latency (or use the :nitro variant of a model).",
                      f"client.chat.completions.create(\n    model=\"{slug}\",\n    messages=[...],\n    extra_body={{\"provider\": {{\"sort\": \"throughput\"}}}},\n)", "python"),
                _step("Reduce tokens", "Wall-clock time is mostly output length ÷ tok/s: lower max_tokens, ask for concise answers, and avoid reasoning variants for simple routes.", None),
            ],
        }
    # open weights: the user can self-host and *does* control speculation → reuse the full recipe, minus Modal buttons
    plan = speculative_plan(model, {**info, "hosted": False, "requested_gpu": model.get("min_gpu") or "H100", "max_model_len": 8192, "gpu_count": 1},
                            {**spec, "status": "not_detected", "config": None}, perf, {**options, "gpu": model.get("min_gpu") or "H100"}, copy_ratios)
    base = f"{tps:.0f} tok/s" if tps else "the measured speed"
    plan["status"] = spec["status"]
    plan["apply"] = None
    plan["summary"] = ("Whether the provider uses speculative decoding can't be observed through an API — some do, some don't. "
                       f"{model['name']} is open-weight, so you can control it by self-hosting: "
                       + (f"with {plan['methods'][0]['title']} a self-hosted instance could plausibly beat {base} at low concurrency." if plan["methods"] else "see the recipe below."))
    for st in plan["steps"]:
        if st["title"].startswith("Measure the gain"):
            st["body"] = ("Switch the provider to Modal in Run options, pick this model and the method above (“Try it now” on the method card), "
                          "and compare tok/s and coherency against this hosted run.")
    return plan


def _step(title: str, body: str, code: str | None, lang: str = "text") -> dict[str, Any]:
    return {"title": title, "body": body, "code": code, "lang": lang if code else None}


# ----------------------------------------------------------------------------------------
# Speed
# ----------------------------------------------------------------------------------------
def hosted_speed_recommendations(model, info, perf) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    slug = model.get("openrouter_slug") or model["name"]
    tps, ttft, ttft_long = perf.get("decode_tps_median"), perf.get("ttft_ms_p50"), perf.get("ttft_long_ms")
    provs = info.get("providers_seen") or {}
    prov_txt = ", ".join(f"{k} ×{v}" for k, v in provs.items()) or "unknown provider"
    if tps is not None and tps < 40:
        recs.append(dict(id="route-fast", title="Route to a faster provider", impact="high", effort="low",
                         why=f"Measured {tps:.0f} tok/s via {prov_txt} (includes network and provider queueing).",
                         detail="OpenRouter can pick the highest-throughput provider for you, or you can use the `:nitro` variant of a model. Throughput differs several-fold between providers of the same model.",
                         code=f"extra_body={{\"provider\": {{\"sort\": \"throughput\"}}}}   # or model=\"{slug}:nitro\""))
    if ttft is not None and ttft > 700:
        recs.append(dict(id="route-latency", title="Optimise time-to-first-token", impact="medium", effort="low",
                         why=f"Median TTFT {ttft:.0f} ms" + (f", {ttft_long:.0f} ms for a ~1.5k-token prompt." if ttft_long else "."),
                         detail="Sort providers by latency, keep long system prompts stable so providers can cache them, and stream responses so users see tokens immediately.",
                         code="extra_body={\"provider\": {\"sort\": \"latency\"}}"))
    if len(provs) > 1:
        recs.append(dict(id="pin-provider", title="Pin a provider for consistent speed and quality", impact="medium", effort="low",
                         why=f"Requests were served by {len(provs)} different providers ({prov_txt}).",
                         detail="Providers differ in quantisation, context limits and speed. For reproducible behaviour pin one and disable fallbacks.",
                         code=f"extra_body={{\"provider\": {{\"order\": [\"{next(iter(provs))}\"], \"allow_fallbacks\": False}}}}"))
    if model.get("reasoning"):
        recs.append(dict(id="think", title="Cap reasoning effort", impact="high", effort="low",
                         why="Reasoning models spend most tokens thinking, multiplying latency and cost.",
                         detail="Lower the effort (or exclude reasoning) on routes that do not need it.",
                         code="extra_body={\"reasoning\": {\"effort\": \"low\"}}"))
    if info.get("free_tier") or (info.get("pricing") or {}).get("prompt_per_m") == 0:
        recs.append(dict(id="free-limits", title="Free models are rate-limited", impact="low", effort="n/a",
                         why="Free endpoints share capacity and enforce strict request limits.",
                         detail="Expect slower, burstier speed than the paid variant; do not use free routes for latency-sensitive production.", code=None))
    if model.get("open_weights"):
        recs.append(dict(id="selfhost", title="Self-host for full control of speed", impact="medium", effort="high",
                         why="Open-weight model: you control quantisation, batching and speculative decoding yourself.",
                         detail="Evaluate it on Modal with this platform (FP8 / speculative decoding) and compare tok/s and quality against the hosted numbers.", code=None))
    if not recs:
        recs.append(dict(id="hosted-ok", title="Speed looks healthy for a hosted endpoint", impact="info", effort="n/a",
                         why=f"{tps:.0f} tok/s, TTFT {ttft or 0:.0f} ms via {prov_txt}." if tps else "Speed measured.",
                         detail="Numbers include network and provider queueing, so treat them as the service you would actually get.", code=None))
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    recs.sort(key=lambda r: order.get(r["impact"], 4))
    return recs


def speed_recommendations(model: dict[str, Any], info: dict[str, Any], perf: dict[str, Any], spec: dict[str, Any], options: dict[str, Any]) -> list[dict[str, Any]]:
    if info.get("hosted"):
        return hosted_speed_recommendations(model, info, perf)
    recs: list[dict[str, Any]] = []
    gpu_key = info.get("gpu") or match_gpu((info.get("gpu_names") or [None])[0])
    gpu_name, gpu_n = parse_gpu(info.get("requested_gpu") or options.get("gpu"))
    gpu_key = gpu_key or gpu_name
    gpu_n = max(gpu_n, int(info.get("gpu_count") or 1))
    tps = perf.get("decode_tps_median")
    ttft = perf.get("ttft_ms_p50")
    ttft_long = perf.get("ttft_long_ms")
    bpp = bytes_per_param(info.get("dtype") or options.get("dtype"), info.get("quantization") or options.get("quantization"))
    roof = roofline_tps(model["active_params_b"], gpu_key, gpu_n, model.get("dtype_bytes", bpp) if not info.get("quantization") else bpp)
    eff = (tps / roof) if (tps and roof) else None
    fp8_ok = bool(GPUS.get(gpu_key or "", {}).get("fp8"))
    quantized = bool(info.get("quantization")) or model.get("dtype_bytes", 2.0) < 2.0
    active = spec["status"] in ("active", "likely")

    if not active:
        recs.append(dict(id="spec", title="Enable speculative decoding", impact="high", effort="low",
                         why=f"Decode is {tps:.0f} tok/s with no speculation — every token costs a full pass over the weights." if tps else "Not enabled.",
                         detail="See the Speculative decoding tab for the exact recipe for this model. It is the largest lossless speed-up available at low concurrency.",
                         code=None))

    if roof and eff is not None:
        if eff < 0.40:
            recs.append(dict(id="roofline", title="Engine is far from the hardware roofline", impact="high", effort="medium",
                             why=f"Measured {tps:.0f} tok/s vs a ~{roof:.0f} tok/s memory-bandwidth ceiling for {model['active_params_b']:.1f}B active params on {gpu_n}× {gpu_key} ({eff:.0%} efficiency).",
                             detail="Typical causes: eager mode instead of CUDA graphs, an outdated engine build, tensor-parallel overhead on a model that fits on one GPU, or an unoptimised attention backend. Upgrade vLLM, make sure `--enforce-eager` is NOT set, and prefer fewer GPUs if the weights fit.",
                             code="vllm serve MODEL --tensor-parallel-size 1   # only shard if weights + KV cache do not fit"))
        elif eff > 0.85:
            recs.append(dict(id="roofline-ok", title="Already near the bandwidth limit", impact="low", effort="n/a",
                             why=f"{eff:.0%} of the ~{roof:.0f} tok/s memory-bandwidth ceiling.",
                             detail="Plain decoding cannot go much faster on this GPU. The only ways forward are fewer bytes per token (quantisation), more bandwidth (bigger GPU), or fewer passes per token (speculative decoding).",
                             code=None))

    if not quantized:
        if fp8_ok:
            recs.append(dict(id="fp8", title="Serve weights in FP8", impact="high", effort="low",
                             why=f"{gpu_key} has native FP8 tensor cores; bf16 weights are {model['params_b'] * 2:.0f} GB read every step.",
                             detail="FP8 halves bytes per token → ~1.4–1.8× decode speed with usually < 1% quality loss. Re-run this evaluation afterwards and compare the coherency score.",
                             code="vllm serve MODEL --quantization fp8 --kv-cache-dtype fp8"))
        else:
            recs.append(dict(id="int4", title="Use a 4-bit AWQ/GPTQ build", impact="high", effort="medium",
                             why=f"{gpu_key or 'This GPU'} lacks FP8; 4-bit weights with Marlin kernels cut weight traffic ~3.5×.",
                             detail="Expect 1.8–2.5× decode speed. Pick a well-calibrated AWQ/GPTQ checkpoint (e.g. an official `-AWQ` or `-GPTQ-Int4` variant), then validate quality with this suite — small models degrade more.",
                             code="vllm serve MODEL-AWQ --quantization awq_marlin"))

    # hardware upgrade
    if tps and gpu_key in GPU_ORDER and tps < 30:
        cur = GPUS[gpu_key]["bw_gbs"]
        better = [g for g in ("L40S", "A100-80GB", "H100", "H200", "B200") if GPUS[g]["bw_gbs"] > cur * 1.5 and GPUS[g]["mem_gb"] * gpu_n >= model["params_b"] * bpp * 1.1]
        if better:
            tgt = better[0]
            est = tps * GPUS[tgt]["bw_gbs"] / cur
            recs.append(dict(id="gpu", title=f"Move to {tgt}", impact="high" if est >= tps * 2 else "medium", effort="low",
                             why=f"Decode is memory-bandwidth bound: {gpu_key} {cur} GB/s → {tgt} {GPUS[tgt]['bw_gbs']} GB/s.",
                             detail=f"Estimated ≈ {est:.0f} tok/s on {tgt} (from {tps:.0f}). Pick it in the GPU option and re-run to confirm.",
                             code=f"# in the platform options: GPU = {tgt}"))

    if ttft is not None and (ttft > 400 or (ttft_long or 0) > 1500):
        recs.append(dict(id="ttft", title="Cut time-to-first-token", impact="medium", effort="low",
                         why=f"Median TTFT {ttft:.0f} ms" + (f", {ttft_long:.0f} ms with a ~1.5k-token prompt." if ttft_long else "."),
                         detail="Prefill is compute-bound. Keep prefix caching on (system prompts are then free), enable chunked prefill so long prompts do not stall decode, and lower --max-model-len if you do not need it.",
                         code="vllm serve MODEL --enable-prefix-caching --enable-chunked-prefill --max-num-batched-tokens 8192"))

    conc = perf.get("concurrent") or {}
    if conc.get("aggregate_tps") and tps:
        gain = conc["aggregate_tps"] / tps
        n = conc.get("n", 8)
        if gain < n * 0.45:
            recs.append(dict(id="batching", title="Throughput scales poorly with concurrency", impact="medium", effort="low",
                             why=f"{n} parallel requests gave {conc['aggregate_tps']:.0f} tok/s total = {gain:.1f}× one stream (ideal ≈ {n}×).",
                             detail="Increase the scheduler's batch capacity and make sure GPU memory is not starving the KV cache.",
                             code="vllm serve MODEL --max-num-seqs 256 --gpu-memory-utilization 0.92"))
        else:
            recs.append(dict(id="batching-ok", title="Batching scales well", impact="low", effort="n/a",
                             why=f"{n} parallel requests → {conc['aggregate_tps']:.0f} tok/s total ({gain:.1f}× one stream).",
                             detail="For throughput-oriented workloads, serve many users per GPU; speculative decoding matters less at high batch.", code=None))

    if "moe" not in model.get("tags", []) and model["params_b"] >= 24 and tps and tps < 25:
        recs.append(dict(id="moe", title="Consider a Mixture-of-Experts alternative", impact="high", effort="medium",
                         why=f"{model['name']} is dense: all {model['params_b']:.0f}B parameters are read per token.",
                         detail="MoE models such as Qwen3-30B-A3B activate ~3B params per token and decode several times faster at similar quality tiers. Evaluate one on this platform.", code=None))

    if (info.get("cold_start_s") or 0) > 90 and info.get("provider") == "modal":
        recs.append(dict(id="cold", title="Reduce cold-start latency", impact="medium", effort="low",
                         why=f"Cold start took {info.get('cold_start_s'):.0f}s (weights load + CUDA graph capture).",
                         detail="Weights are cached in a Modal Volume after the first run. For latency-sensitive traffic keep one container warm; for bursty traffic lengthen the scale-down window.",
                         code="@app.cls(gpu=\"H100\", min_containers=1, scaledown_window=600)"))

    if model.get("reasoning"):
        recs.append(dict(id="think", title="Cap or disable reasoning tokens", impact="high", effort="low",
                         why="Reasoning models spend most tokens in <think> blocks, multiplying latency per answer.",
                         detail="For latency-sensitive routes turn thinking off or budget it; keep it on for hard maths/code.",
                         code="extra_body={\"chat_template_kwargs\": {\"enable_thinking\": False}}   # Qwen3-style hybrids"))

    order = {"high": 0, "medium": 1, "low": 2}
    recs.sort(key=lambda r: order.get(r["impact"], 3))
    return recs


# ----------------------------------------------------------------------------------------
# Coherency / quality
# ----------------------------------------------------------------------------------------
MODEL_TIPS = {
    "Llama": "Llama 3.x needs both `<|eot_id|>` and `<|end_of_text|>` as stop tokens; vLLM reads them from generation_config.json — make sure that file exists in the checkpoint you serve.",
    "Qwen": "Qwen models occasionally drift into Chinese on English prompts; a system prompt such as \"Always answer in English\" and temperature ≤ 0.7 largely removes it. Beyond 32k tokens enable YaRN rope scaling explicitly.",
    "Gemma": "Gemma activations overflow in float16 — always serve with `--dtype bfloat16` (needs Ampere+ GPU). Gemma has no system role: fold instructions into the first user turn.",
    "DeepSeek": "R1-style models want temperature 0.6, no system prompt, and thinking tokens left enabled; strip <think> before showing answers.",
    "Mistral": "Mistral v0.3 checkpoints load best with `--tokenizer-mode mistral --config-format mistral --load-format mistral` when `params.json`/consolidated weights are present.",
    "Phi": "Phi-4 is trained on synthetic STEM data — strong at maths, weaker at open chat and strict formatting; add few-shot examples for format-sensitive tasks.",
    "SmolLM": "Sub-2B models are inherently fragile: use temperature ≤ 0.4 with a repetition penalty and keep generations short.",
    "GLM": "GLM-4.5 needs `--trust-remote-code`-free recent vLLM plus its tool/reasoning parsers; use the official chat template.",
}

ISSUE_ADVICE = {
    "special_token_leak": dict(
        title="Fix leaked chat-template / stop tokens", impact="high",
        detail="Special tokens appearing in text mean generation did not stop at the model's end-of-turn token or the wrong template is applied. Serve the *Instruct* variant, make sure generation_config.json lists every EOS id, or pass stop token ids explicitly.",
        code="# per request\nextra_body={\"stop_token_ids\": [128009, 128001]}   # example: Llama 3 <|eot_id|>, <|end_of_text|>\n# or at server start\nvllm serve MODEL --generation-config auto"),
    "repetition": dict(
        title="Stop degenerate repetition loops", impact="high",
        detail="Greedy decoding on long outputs can lock into loops. Use light sampling plus a repetition/frequency penalty, and verify the model is not served beyond its trained context length.",
        code="client.chat.completions.create(..., temperature=0.6, top_p=0.9, frequency_penalty=0.2,\n    extra_body={\"repetition_penalty\": 1.05})"),
    "gibberish_words": dict(
        title="Investigate numerical / tokenizer corruption", impact="high",
        detail="Implausible words and random characters point to a numerical problem or tokenizer mismatch rather than a 'weak' model. Work through: (1) serve in bfloat16 instead of float16, (2) drop aggressive quantisation, (3) confirm the tokenizer is the one shipped with the checkpoint, (4) test with `--enforce-eager` to rule out a CUDA-graph or kernel bug, (5) try another attention backend, (6) reduce --max-model-len to the trained context.",
        code="vllm serve MODEL --dtype bfloat16 --enforce-eager   # diagnostic run\nVLLM_ATTENTION_BACKEND=FLASH_ATTN vllm serve MODEL"),
    "replacement_chars": dict(
        title="Fix broken UTF-8 decoding", impact="medium",
        detail="Replacement characters (�) come from splitting multi-byte characters across tokens at the client, or from corrupted logits. Decode streamed bytes incrementally, and if it also happens on non-streaming calls treat it as a numerics problem.",
        code="decoder = codecs.getincrementaldecoder('utf-8')()\ntext = decoder.decode(chunk_bytes)"),
    "language_drift": dict(
        title="Pin the response language", impact="medium",
        detail="Add an explicit language instruction and keep temperature low; stray non-Latin characters usually appear when sampling picks rare multilingual tokens.",
        code="messages=[{\"role\": \"system\", \"content\": \"Always respond in English.\"}, ...]\ntemperature=0.3"),
    "runaway_generation": dict(
        title="Make the model stop when it is done", impact="high",
        detail="Short-answer prompts ran to the token limit. This is almost always a missing EOS/stop configuration or serving a *base* model with a chat template it was never trained on. Check the stop tokens and that you are using the instruct checkpoint.",
        code=None),
    "runaway_reasoning": dict(
        title="Give the reasoning model a bigger (or smaller) budget", impact="high",
        detail="A <think> block never closed. Raise max_tokens substantially for reasoning models, cap thinking with a budget, or disable thinking for simple routes.",
        code="client.chat.completions.create(..., max_tokens=8192)\n# Qwen3-style hybrid: extra_body={\"chat_template_kwargs\": {\"enable_thinking\": False}}"),
    "empty_output": dict(
        title="Empty responses", impact="high",
        detail="The model emitted an end-of-sequence immediately. Verify the chat template is applied (use /v1/chat/completions, not /v1/completions) and that the prompt is not being truncated by --max-model-len.",
        code=None),
    "hit_token_limit": dict(
        title="Responses are cut off", impact="low",
        detail="Increase max_tokens, or ask for shorter answers.", code=None),
}
_ALIASES = {"tail_loop": "repetition", "line_loop": "repetition", "compressible_loop": "repetition", "low_word_coverage": "gibberish_words",
            "random_chars": "gibberish_words", "symbol_soup": "gibberish_words", "low_confidence": "gibberish_words", "mojibake": "replacement_chars",
            "control_chars": "gibberish_words"}


HOSTED_OVERRIDES = {
    "special_token_leak": dict(
        detail="Special tokens in the text mean a provider is mis-applying the chat template or stop tokens. This is a provider bug, not the model — route to a different provider and report it.",
        code="extra_body={\"provider\": {\"order\": [\"Together\", \"Fireworks\"], \"allow_fallbacks\": True, \"ignore\": [\"<offending provider>\"]}}"),
    "gibberish_words": dict(
        detail="Garbled language through a hosted API is very often one provider serving an aggressively quantised or buggy build. Require high-precision providers, lower the temperature, and compare providers one by one.",
        code="extra_body={\"provider\": {\"quantizations\": [\"bf16\", \"fp16\", \"fp8\"], \"require_parameters\": True}}\ntemperature=0.3"),
    "runaway_generation": dict(
        detail="Short-answer prompts ran to the token limit: a provider is not honouring the model's end-of-turn token, or you are calling a base model. Pin another provider or pass explicit stop sequences.",
        code="stop=[\"<|eot_id|>\", \"<|im_end|>\"]   # model-specific end-of-turn markers"),
    "empty_output": dict(
        detail="Empty completions from a hosted model usually mean a provider error, a content filter, or a too-small max_tokens budget when the model reasons first. Retry on another provider and raise max_tokens.",
        code="extra_body={\"provider\": {\"allow_fallbacks\": True}}\nmax_tokens=2048"),
}


def coherence_recommendations(model: dict[str, Any], info: dict[str, Any], coh: dict[str, Any], doms: dict[str, Any], tests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    seen: set[str] = set()
    by_id = {t["id"]: t for t in tests}

    for kind, rec in coh["issue_kinds"].items():
        key = _ALIASES.get(kind, kind)
        if key in seen:
            continue
        advice = ISSUE_ADVICE.get(key)
        if not isinstance(advice, dict):
            continue
        if info.get("hosted") and key in HOSTED_OVERRIDES:
            advice = {**advice, **HOSTED_OVERRIDES[key]}
        # count only major/critical (or all for minor-only kinds)
        related = [k for k, v in coh["issue_kinds"].items() if _ALIASES.get(k, k) == key]
        ids = sorted({tid for k in related for tid in coh["issue_kinds"][k]["tests"]})
        sev_major = any(i["severity"] in ("major", "critical") and _ALIASES.get(i["kind"], i["kind"]) == key
                        for tid in ids for i in (by_id[tid].get("health") or {}).get("issues", []))
        if not sev_major and advice["impact"] != "low" and kind not in ("replacement_chars",):
            impact = "low"
        else:
            impact = advice["impact"]
        seen.add(key)
        recs.append(dict(id=key, title=advice["title"], impact=impact, detail=advice["detail"], code=advice["code"],
                         why=f"Seen in {len(ids)} of {coh['responses']} responses ({', '.join(ids[:5])}{'…' if len(ids) > 5 else ''}).",
                         severity="major" if sev_major else "minor"))

    t_by = {t["id"]: t for t in tests}
    if t_by.get("coh-needle") and not t_by["coh-needle"]["passed"]:
        recs.append(dict(id="longctx", title="Long-context recall failed", impact="high", severity="major",
                         why="The buried 4-digit code was not retrieved from a long document.",
                         detail="Check the model's native context length and that --max-model-len does not exceed it (enable YaRN/rope scaling only when documented). For production, chunk documents and retrieve (RAG) instead of stuffing the whole context.",
                         code="vllm serve MODEL --max-model-len 8192   # stay within the trained window"))
    if t_by.get("coh-stability") and not t_by["coh-stability"]["passed"]:
        recs.append(dict(id="stability", title="Outputs are not repeatable at temperature 0", impact="low", severity="minor",
                         why="Two identical greedy requests produced different text.",
                         detail="Floating-point non-associativity with dynamic batching changes logits slightly. Pass a seed, or enable batch-invariant kernels if exact reproducibility matters.",
                         code="VLLM_BATCH_INVARIANT=1 vllm serve MODEL   # newer vLLM builds"))
    if t_by.get("coh-multilingual") and t_by["coh-multilingual"]["score"] < 0.75:
        recs.append(dict(id="multilingual", title="Weak multilingual / non-Latin output", impact="medium", severity="minor",
                         why="Some of French/German/Japanese/Hindi greetings were wrong.",
                         detail="Smaller or English-centric models struggle with non-Latin scripts. Use a multilingual family (Qwen, Gemma, Llama 3.1+) at ≥ 7B if you serve non-English users.", code=None))

    dom_advice = {
        "math": ("Improve mathematical accuracy", "Sample several chain-of-thought answers and take the majority (self-consistency), keep temperature around 0.6, ask for step-by-step reasoning with a fixed final-answer line, or offload arithmetic to a code tool. A maths-strong model (Phi-4, Qwen2.5/3, R1-distill) may also be a better fit.",
                 "answers = [ask(prompt, temperature=0.6) for _ in range(5)]\nfinal = Counter(extract(a) for a in answers).most_common(1)[0][0]"),
        "coding": ("Improve code generation", "Use temperature ≤ 0.2, include example tests or signatures in the prompt, extract fenced code programmatically and run it, and feed failures back for one repair round. A code-specialised checkpoint (Qwen2.5-Coder) is often a big upgrade.",
                   None),
        "general": ("Improve instruction & format following", "Constrain output with structured generation (JSON schema / regex) instead of trusting prompts, give a one-shot example, and put hard constraints at the end of the prompt.",
                    "client.chat.completions.create(..., response_format={\"type\": \"json_schema\", \"json_schema\": {...}})"),
    }
    for d, (title, detail, code) in dom_advice.items():
        if d in doms and doms[d]["score"] < 70:
            failed = [t["id"] for t in tests if t["domain"] == d and not t["passed"]]
            recs.append(dict(id=f"domain-{d}", title=title, impact="high" if doms[d]["score"] < 45 else "medium", severity="major" if doms[d]["score"] < 45 else "minor",
                             why=f"{d.title()} score {doms[d]['score']:.0f}/100 — failed: {', '.join(failed[:6])}{'…' if len(failed) > 6 else ''}.",
                             detail=detail, code=code))

    if info.get("hosted") and info.get("providers_seen") and coh["severe_rate"] > 0.04:
        provs = ", ".join(f"{k} ×{v}" for k, v in info["providers_seen"].items())
        recs.append(dict(id="provider-attribution", title="Check which provider served the bad responses", impact="medium", severity="minor",
                         why=f"Providers seen: {provs}.", detail="Re-run with a single provider pinned (provider.order + allow_fallbacks=false) to find out whether the problem is the model or one provider's deployment.",
                         code=None))
    if (not info.get("hosted")) and (info.get("dtype") in ("float16", "half") or (info.get("gpu") == "T4" and coh["garble_rate"] > 0)):
        recs.append(dict(id="dtype", title="Prefer bfloat16", impact="high", severity="major",
                         why=f"Serving dtype is {info.get('dtype')}.", detail="float16 has a much smaller dynamic range and overflows in several architectures, producing NaNs/garbage. Use bfloat16 on Ampere or newer.", code="vllm serve MODEL --dtype bfloat16"))

    tip = MODEL_TIPS.get(model.get("family", ""))
    if tip:
        recs.append(dict(id="model-tip", title=f"{model['family']}-specific notes", impact="info", severity="info", why=f"Known quirks of the {model['family']} family.", detail=tip, code=None))

    if not [r for r in recs if r["impact"] in ("high", "medium")]:
        recs.insert(0, dict(id="healthy", title="No coherency problems found", impact="info", severity="info",
                            why=f"{coh['responses']} responses analysed: {coh['clean_ratio']:.0%} clean.",
                            detail="Keep this suite in CI and re-run it after every engine, quantisation or model-version change — coherency regressions are silent otherwise.", code=None))
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    recs.sort(key=lambda r: order.get(r["impact"], 4))
    return recs


def build(model, info, perf, spec, coh, doms, tests, options, copy_ratios) -> dict[str, Any]:
    return {
        "speculative": speculative_plan(model, info, spec, perf, options, copy_ratios),
        "speed": speed_recommendations(model, info, perf, spec, options),
        "coherence": coherence_recommendations(model, info, coh, doms, tests),
    }
