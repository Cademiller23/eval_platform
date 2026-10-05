"""Orchestrates one evaluation run and emits progress events.

Phases:  provision → warmup → performance → coherency → coding → math → general → speculative → analysis
"""
from __future__ import annotations

import asyncio
import inspect
import json
import statistics
import time
from datetime import datetime, timezone
from typing import Any, Callable

from . import __version__, catalog, recommendations, scoring, speculative
from .config import get_settings, hf_token
from .knowledge import bytes_per_param, match_gpu, parse_gpu, roofline_tps
from .providers import LaunchSpec, get_provider
from .providers.base import ChatResult, Session, collect
from .suite.coherence import analyze_text
from .suite.tasks import DOMAINS, GradeCtx, Task, build_haystack, build_suite
from .suite.text import split_thinking

Emit = Callable[[dict[str, Any]], None]

PHASES = [
    ("provision", "Start model"),
    ("warmup", "Warm up"),
    ("performance", "Speed benchmark"),
    ("coherency", "Coherency & garble"),
    ("coding", "Coding"),
    ("math", "Mathematics"),
    ("general", "General purpose"),
    ("speculative", "Speculative decoding"),
    ("analysis", "Scoring & report"),
]

BENCH_PROMPTS = [
    "Explain in detail how a transformer-based language model generates text, from tokenisation to sampling.",
    "Write a long, vivid short story about a lighthouse keeper who discovers a message in a bottle.",
    "Describe the history of the printing press and its impact on science, religion and politics.",
]


# Decoding "stress controls": deliberately break the model's sampling so a checker can confirm the detectors
# fire on real model output ("garble": temperature 2 → word salad; "loop": negative penalties → repetition).
STRESS = {
    "garble": {"temperature": 2.0, "extra": {"top_p": 1.0}},
    "loop": {"temperature": 0.0, "extra": {"frequency_penalty": -2.0, "presence_penalty": -2.0, "repetition_penalty": 0.3}},
}


class RunCancelled(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    vs = sorted(values)
    k = (len(vs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(vs) - 1)
    return vs[lo] + (vs[hi] - vs[lo]) * (k - lo)


def resolve_speculative(model: dict[str, Any], mode: str | None, custom_json: str | None) -> tuple[dict[str, Any] | None, str]:
    """Map the UI option to a vLLM --speculative-config dict."""
    mode = (mode or "auto").lower()
    if mode in ("auto", "none", ""):
        return None, "none" if mode == "none" else "auto"
    if mode == "custom":
        try:
            cfg = json.loads(custom_json or "")
            if not isinstance(cfg, dict):
                raise ValueError
        except ValueError as e:
            raise ValueError("Custom speculative config must be a JSON object, e.g. {\"method\":\"ngram\",\"num_speculative_tokens\":5}") from e
        return cfg, "custom"
    entries = {sp["method"]: sp for sp in model.get("speculators", [])}
    if mode == "mtp":
        if not model.get("mtp_native"):
            raise ValueError(f"{model['name']} has no native MTP layers.")
        return recommendations._spec_config("mtp", model, None), "mtp"
    if mode in ("eagle3", "draft_model", "ngram"):
        if mode not in entries:
            raise ValueError(f"No {mode} recipe is catalogued for {model['name']}. Use 'custom' to supply your own config.")
        return recommendations._spec_config(mode, model, entries[mode]), mode
    raise ValueError(f"Unknown speculative mode '{mode}'.")


def resolve_model(options: dict[str, Any]) -> dict[str, Any]:
    if options.get("openrouter_model"):
        from .providers import openrouter_provider as orp

        slug = options["openrouter_model"].strip()
        meta = next((m for m in (orp._cache["models"] or []) if m["id"] == slug), None)
        return catalog.openrouter_model(slug, meta)
    if options.get("custom_model"):
        cm = options["custom_model"]
        return catalog.custom_model(cm["hf_repo"], cm.get("params_b"), cm.get("reasoning"))
    m = catalog.get_model(options["model_id"])
    if not m:
        raise ValueError(f"Unknown model '{options['model_id']}'")
    return m


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + f"\n… [{len(s) - n} more characters]"


class Runner:
    def __init__(self, run_id: str, options: dict[str, Any], emit: Emit):
        self.run_id = run_id
        self.options = options
        self._emit = emit
        self.settings = get_settings()
        self.model = resolve_model(options)
        self.provider = get_provider(options.get("provider") or "mock")
        self.session: Session | None = None
        self.tests: list[dict[str, Any]] = []
        self.perf: dict[str, Any] = {}
        self.behaviour = {"chunks": 0, "multi": 0, "tokens": 0}
        self._last_tps_emit = 0.0

    @staticmethod
    def _endpoint(opts: dict[str, Any]) -> dict[str, Any] | None:
        if opts.get("provider") == "openrouter":
            return {"api_key": opts.get("openrouter_key"), "model": opts.get("openrouter_model")}
        return opts.get("endpoint")

    # ------------------------------------------------------------------ events
    def log(self, level: str, message: str) -> None:
        self._emit({"type": "log", "level": level, "message": message, "ts": now_iso()})

    def phase(self, pid: str, status: str, detail: str = "") -> None:
        self._emit({"type": "phase", "id": pid, "status": status, "detail": detail, "ts": now_iso()})

    def progress(self, pid: str, done: int, total: int) -> None:
        self._emit({"type": "progress", "phase": pid, "done": done, "total": total, "ephemeral": True})

    # ------------------------------------------------------------------ chat helper
    async def chat(self, messages, *, max_tokens: int, temperature: float = 0.0, meta=None, live: bool = False) -> ChatResult:
        assert self.session
        extra = {}
        if self.model.get("chat_template_kwargs"):
            extra["chat_template_kwargs"] = self.model["chat_template_kwargs"]
        stress = STRESS.get(self.options.get("stress") or "")
        if stress:
            temperature = stress["temperature"]
            extra.update(stress["extra"])
        t_start = time.perf_counter()
        state = {"tokens": 0, "t0": None}

        async def gen():
            async for ev in self.session.stream(messages, max_tokens=max_tokens, temperature=temperature, meta=meta, extra=extra or None):
                if live and not ev.get("done") and not ev.get("error"):
                    state["tokens"] += int(ev.get("n") or 0)
                    if state["t0"] is None and (ev.get("n") or ev.get("text")):
                        state["t0"] = ev.get("t", 0.0)
                        state["first_n"] = int(ev.get("n") or 1)
                    wall = time.perf_counter()
                    if state["t0"] is not None and wall - self._last_tps_emit > 0.25 and ev.get("t", 0) > state["t0"]:
                        self._last_tps_emit = wall
                        tps = (state["tokens"] - state.get("first_n", 1)) / max(1e-6, ev["t"] - state["t0"])
                        self._emit({"type": "metric", "name": "tps", "value": round(tps, 1), "ephemeral": True})
                yield ev

        return await collect(gen())

    def _max_tokens(self, task: Task, prompt_chars: int) -> int:
        mt = task.max_tokens
        if self.model.get("reasoning"):
            mt = max(mt * 6, 3072)  # reasoning models think at length even for trivial prompts
        ctx = int(self.session.info.get("max_model_len") or self.options.get("max_model_len") or 8192) if self.session else 8192
        room = ctx - int(prompt_chars / 3.2) - 64
        return max(32, min(mt, room))

    # ------------------------------------------------------------------ main
    async def run(self) -> dict[str, Any]:
        t_run = time.time()
        opts = self.options
        model = self.model
        spec_cfg, spec_label = resolve_speculative(model, opts.get("speculative"), opts.get("speculative_custom"))
        default_len = 16384 if model.get("reasoning") else 8192
        max_len = int(opts.get("max_model_len") or min(model.get("context", default_len), default_len))
        max_len = max(max_len, 4096)
        gpu = opts.get("gpu") or model.get("min_gpu")
        spec = LaunchSpec(
            model=model, gpu=gpu, speculative_config=spec_cfg, max_model_len=max_len, dtype=opts.get("dtype") or "auto",
            quantization=opts.get("quantization") or None, hf_token=hf_token(),
            endpoint=self._endpoint(opts), speculative_label=spec_label,
        )
        if self.provider.name == "openrouter" and spec_cfg:
            raise ValueError("Speculative decoding cannot be configured on a hosted API — pick Modal to test it.")
        if opts.get("stress"):
            self.log("warn", f"Stress control '{opts['stress']}' active: decoding is deliberately broken ({STRESS[opts['stress']]}). "
                             "Scores are expected to drop — this verifies that the detectors fire.")
        quick = bool(opts.get("quick"))
        suite = build_suite(self.settings.haystack_tokens, max_len, quick)
        self.log("info", f"Evaluating {model['name']} ({model['hf_repo']}) — {len(suite)} tests{' (quick mode)' if quick else ''}.")

        try:
            # ---- provision
            self.phase("provision", "running", f"{self.provider.label} · {gpu or 'default GPU'}")
            t0 = time.time()
            self.session = await self.provider.start(spec, self.log)
            info = dict(self.session.info)
            info["wall_provision_s"] = round(time.time() - t0, 1)
            self._emit({"type": "environment", "environment": _public_env(info, spec)})
            self.phase("provision", "done", f"online in {info.get('provision_s') or info['wall_provision_s']}s")

            # ---- warmup
            self.phase("warmup", "running")
            for _ in range(2):
                await self.chat([{"role": "user", "content": "Say hello in one short sentence."}], max_tokens=24)
            self.phase("warmup", "done")
            metrics_before = await self.session.metrics()

            # ---- performance
            await self.performance()

            # ---- suites
            by_domain: dict[str, list[Task]] = {d: [t for t in suite if t.domain == d] for d in DOMAINS}
            for d in DOMAINS:
                await self.run_domain(d, by_domain[d])

            failed = [t for t in self.tests if t.get("error")]
            if self.tests and len(failed) > len(self.tests) * 0.5:
                raise RuntimeError(f"{len(failed)}/{len(self.tests)} requests failed. First error: {failed[0]['error']}")

            # ---- speculative
            self.phase("speculative", "running")
            metrics_after = await self.session.metrics()
            beh = {
                "chunks": self.behaviour["chunks"],
                "multi_token_chunk_ratio": self.behaviour["multi"] / self.behaviour["chunks"] if self.behaviour["chunks"] else 0.0,
                "tokens_per_chunk": self.behaviour["tokens"] / self.behaviour["chunks"] if self.behaviour["chunks"] else 1.0,
            }
            spec_report = speculative.analyze(info, metrics_before, metrics_after, beh, model)
            self.phase("speculative", "done", spec_report["headline"])

            # ---- analysis
            self.phase("analysis", "running")
            ok_tests = [t for t in self.tests if not t.get("error")]
            scores, doms, coh = scoring.compute_scores(ok_tests, self.perf)
            verdict = scoring.verdict(scores, doms, coh, self.perf, model)
            copy_ratios: dict[str, float] = {}
            for d in DOMAINS:
                vals = [t["copy_ratio"] for t in ok_tests if t["domain"] == d and t.get("copy_ratio") is not None]
                if vals:
                    copy_ratios[d] = sum(vals) / len(vals)
            spec_report["copy_ratio_by_domain"] = {k: round(v, 3) for k, v in copy_ratios.items()}
            usage = self.session.run_summary() if self.session else {}
            if usage.get("providers_seen"):
                info["providers_seen"] = usage["providers_seen"]
            recs = recommendations.build(model, info, self.perf, spec_report, coh, doms, ok_tests, opts, copy_ratios)
            report = {
                "run_id": self.run_id,
                "generated_at": now_iso(),
                "platform_version": __version__,
                "duration_s": round(time.time() - t_run, 1),
                "model": _public_model(model),
                "options": _public_options(opts, spec_label, spec_cfg, gpu, max_len),
                "environment": _public_env(info, spec),
                "scores": scores,
                "verdict": verdict,
                "domains": doms,
                "performance": self.perf,
                "coherency": coh,
                "speculative": spec_report,
                "tests": self.tests,
                "recommendations": recs,
                "usage": usage,
            }
            self.phase("analysis", "done", f"{scores['overall']:.0f}/100 · {verdict['title']}")
            return report
        finally:
            if self.session:
                try:
                    await self.session.close()
                except Exception:  # pragma: no cover
                    pass

    # ------------------------------------------------------------------ performance
    async def performance(self) -> None:
        pid = "performance"
        self.phase(pid, "running", "decode throughput · TTFT · concurrency")
        decode_runs: list[float] = []
        bench_samples: list[ChatResult] = []
        ttfts: list[float] = []
        tokens_total = 0
        n_total = len(BENCH_PROMPTS) * 2 + 3
        done = 0
        for rep in range(2):
            for p in BENCH_PROMPTS:
                r = await self.chat([{"role": "user", "content": p}], max_tokens=256, temperature=0.0, meta={"seed": f"bench{rep}"}, live=True)
                done += 1
                self.progress(pid, done, n_total)
                if r.error:
                    self.log("warn", f"benchmark request failed: {r.error}")
                    continue
                self._absorb_behaviour(r)
                if rep == 0:
                    bench_samples.append(r)
                tokens_total += r.completion_tokens
                if r.decode_tps:
                    decode_runs.append(r.decode_tps)
                    self._emit({"type": "metric", "name": "decode_tps_run", "value": round(r.decode_tps, 1)})
                if r.ttft_s is not None:
                    ttfts.append(r.ttft_s * 1000)
        self.log("info", f"Decode throughput: {statistics.median(decode_runs):.1f} tok/s (median of {len(decode_runs)} runs)." if decode_runs else "No decode throughput could be measured.")

        # long-prompt TTFT
        long_doc = build_haystack(1500, "0000")
        r = await self.chat([{"role": "user", "content": long_doc + "\n\nSummarise the document in one sentence."}], max_tokens=24, meta={"seed": "ttft-long"})
        done += 1
        self.progress(pid, done, n_total)
        ttft_long = r.ttft_s * 1000 if (r.ttft_s is not None and not r.error) else None

        # concurrency
        n = max(2, min(8, getattr(self.session, "max_concurrency", None) or 8))
        results = await asyncio.gather(*[
            self.chat([{"role": "user", "content": BENCH_PROMPTS[i % 3] + f" (variation {i})"}], max_tokens=128, temperature=0.0, meta={"seed": f"conc{i}"})
            for i in range(n)
        ])
        done += 1
        self.progress(pid, done, n_total)
        okr = [x for x in results if not x.error and x.completion_tokens]
        conc = None
        if okr:
            span = max(x.total_s for x in okr)
            conc = {"n": n, "aggregate_tps": round(sum(x.completion_tokens for x in okr) / span, 1) if span > 0 else None, "ok": len(okr)}
        self.perf = {
            "decode_tps_median": round(statistics.median(decode_runs), 2) if decode_runs else None,
            "decode_tps_runs": [round(x, 1) for x in decode_runs],
            "decode_tps_min": round(min(decode_runs), 1) if decode_runs else None,
            "decode_tps_max": round(max(decode_runs), 1) if decode_runs else None,
            "ttft_ms_p50": round(percentile(ttfts, 0.5), 1) if ttfts else None,
            "ttft_ms_p95": round(percentile(ttfts, 0.95), 1) if ttfts else None,
            "ttft_long_ms": round(ttft_long, 1) if ttft_long else None,
            "concurrent": conc,
            "tokens_generated": tokens_total,
        }
        info = self.session.info if self.session else {}
        gpu_key = info.get("gpu") or match_gpu((info.get("gpu_names") or [None])[0])
        gpu_n = int(info.get("gpu_count") or 1)
        q = info.get("quantization")
        bpp = bytes_per_param(info.get("dtype"), q) if q else self.model.get("dtype_bytes", 2.0)
        roof = roofline_tps(self.model["active_params_b"], gpu_key, gpu_n, bpp)
        if roof and self.perf["decode_tps_median"]:
            self.perf["roofline"] = {"theoretical_tps": round(roof, 1), "efficiency": round(self.perf["decode_tps_median"] / roof, 3), "gpu": gpu_key, "gpu_count": gpu_n}
        self._emit({"type": "perf", "perf": self.perf})
        # Long greedy generations are the best degeneration probe: grade them as coherency tests too.
        for i, r in enumerate(bench_samples):
            res = self._bench_test(i, r)
            self.tests.append(res)
            self._emit({"type": "test", "test": res})
        self.phase(pid, "done", f"{self.perf['decode_tps_median'] or 0:.0f} tok/s · TTFT {self.perf['ttft_ms_p50'] or 0:.0f} ms")

    def _bench_test(self, i: int, r: ChatResult) -> dict[str, Any]:
        # finish_reason is deliberately ignored: these runs are capped at 256 tokens on purpose
        health = analyze_text(r.text, kind="prose", logprobs=r.logprobs, temperature=0.0)
        clean = not health.severe
        return {
            "id": f"coh-longgen-{i + 1}", "domain": "coherency", "name": f"Open-ended greedy generation #{i + 1}",
            "difficulty": "medium", "skill": "degeneration probe", "prompt": _truncate(BENCH_PROMPTS[i % len(BENCH_PROMPTS)], 600),
            "passed": clean, "score": round(1.0 - health.severity_score, 3) if not clean else 1.0,
            "checks": [{"name": "no garbling, loops or leaked tokens over ~256 tokens", "passed": clean,
                        "detail": "; ".join(x.detail for x in health.issues if x.severity != "minor")[:240]}],
            "response": _truncate(r.text, 6000), "thinking_chars": 0, "health": health.as_dict(),
            "copy_ratio": None,
            "metrics": {"ttft_ms": round(r.ttft_s * 1000, 1) if r.ttft_s is not None else None,
                        "decode_tps": round(r.decode_tps, 1) if r.decode_tps else None, "tokens": r.completion_tokens,
                        "prompt_tokens": r.prompt_tokens, "duration_ms": round(r.total_s * 1000, 1), "finish_reason": r.finish_reason},
        }

    def _absorb_behaviour(self, r: ChatResult) -> None:
        ch = r.chunks[1:] if len(r.chunks) > 1 else []
        self.behaviour["chunks"] += len(ch)
        self.behaviour["multi"] += sum(1 for _, n in ch if n > 1)
        self.behaviour["tokens"] += sum(n for _, n in ch)

    # ------------------------------------------------------------------ domains
    async def run_domain(self, domain: str, tasks: list[Task]) -> None:
        title = dict(PHASES)[domain]
        if not tasks:
            self.phase(domain, "skipped")
            return
        self.phase(domain, "running", f"{len(tasks)} tests")
        cap = getattr(self.session, "max_concurrency", None) or 99
        sem = asyncio.Semaphore(max(1, min(self.settings.suite_concurrency, cap)))
        done = 0
        self.progress(domain, 0, len(tasks))

        async def one(task: Task):
            nonlocal done
            async with sem:
                res = await self.run_task(task)
            self.tests.append(res)
            done += 1
            self.progress(domain, done, len(tasks))
            self._emit({"type": "test", "test": res})

        await asyncio.gather(*[one(t) for t in tasks])
        ids = {t.id for t in tasks}
        ok = [t for t in self.tests if t["id"] in ids and not t.get("error")]
        passed = sum(1 for t in ok if t["passed"])
        self.phase(domain, "done", f"{passed}/{len(tasks)} passed")
        self.log("info", f"{title}: {passed}/{len(tasks)} passed.")

    async def run_task(self, task: Task) -> dict[str, Any]:
        prompt_chars = sum(len(m["content"]) for m in task.messages)
        temp = task.temperature if task.temperature else float(self.options.get("temperature") or 0.0)
        max_tokens = self._max_tokens(task, prompt_chars)
        results: list[ChatResult] = []
        for run in range(task.runs):
            r = await self.chat(task.messages, max_tokens=max_tokens, temperature=temp, meta={"task": task, "seed": f"r{run}"})
            results.append(r)
            if r.error:
                break
        first = results[0]
        base = {"id": task.id, "domain": task.domain, "name": task.name, "difficulty": task.difficulty, "skill": task.skill,
                "prompt": _truncate(task.messages[-1]["content"], 1500)}
        if any(r.error for r in results):
            err = next(r.error for r in results if r.error)
            return {**base, "passed": False, "score": 0.0, "checks": [], "response": "", "error": err, "health": None, "metrics": {}}
        text = results[-1].text
        visible, thinking, _ = split_thinking(text)
        health = analyze_text(
            text, kind=task.text_kind, allow_repetition=task.allow_repetition, multilingual=task.multilingual,
            finish_reason=results[-1].finish_reason, expect_short=task.expect_short, logprobs=results[-1].logprobs, temperature=temp,
        )
        ctx = GradeCtx(finish_reason=results[-1].finish_reason, completion_tokens=results[-1].completion_tokens, all_texts=[r.text for r in results])
        graded = task.grader(text, ctx)
        if inspect.isawaitable(graded):
            graded = await graded
        copy = speculative.copy_ratio("\n".join(m["content"] for m in task.messages), visible) if visible else None
        r = results[-1]
        return {
            **base,
            "passed": graded.passed,
            "score": round(graded.score or 0.0, 3),
            "checks": [c.as_dict() for c in graded.checks],
            "response": _truncate(text, 6000),
            "thinking_chars": len(thinking),
            "health": health.as_dict(),
            "copy_ratio": round(copy, 3) if copy is not None else None,
            "metrics": {
                "ttft_ms": round(r.ttft_s * 1000, 1) if r.ttft_s is not None else None,
                "decode_tps": round(r.decode_tps, 1) if r.decode_tps else None,
                "tokens": r.completion_tokens,
                "prompt_tokens": r.prompt_tokens,
                "duration_ms": round(r.total_s * 1000, 1),
                "finish_reason": r.finish_reason,
            },
        }


def _public_model(m: dict[str, Any]) -> dict[str, Any]:
    keys = ["id", "name", "family", "hf_repo", "params_b", "active_params_b", "context", "reasoning", "tags", "description", "mtp_native", "custom", "gated"]
    return {k: m.get(k) for k in keys}


def _public_options(opts, spec_label, spec_cfg, gpu, max_len) -> dict[str, Any]:
    return {
        "provider": opts.get("provider"), "gpu": gpu, "speculative": spec_label, "speculative_config": spec_cfg,
        "quick": bool(opts.get("quick")), "max_model_len": max_len, "temperature": opts.get("temperature") or 0.0,
        "parent_run_id": opts.get("parent_run_id"), "stress": opts.get("stress") or None,
        "openrouter_model": opts.get("openrouter_model"),
        "endpoint": ({"base_url": opts["endpoint"].get("base_url"), "model": opts["endpoint"].get("model")} if opts.get("endpoint") else None),
    }


def _public_env(info: dict[str, Any], spec: LaunchSpec) -> dict[str, Any]:
    keys = ["provider", "engine", "engine_version", "gpu", "gpu_names", "gpu_count", "requested_gpu", "max_model_len", "dtype", "quantization",
            "speculative_config", "cold_start_s", "provision_s", "wall_provision_s", "command", "simulated", "base_url", "served_model",
            "hosted", "open_weights", "hf_id", "pricing", "free_tier", "providers_seen", "behavior_unreliable"]
    return {k: info.get(k) for k in keys if k in info}
