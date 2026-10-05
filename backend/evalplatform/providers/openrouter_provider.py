"""OpenRouter: evaluate hundreds of hosted models through one OpenAI-compatible API.

What is different from a self-hosted server
-------------------------------------------
* **Timing is client-side** (network + gateway + provider queueing + provider batching are all included),
  so tokens/s here measures the *service you would actually get*, not raw hardware speed.
* **The engine is invisible** — no `/metrics`, no launch flags. Speculative decoding therefore cannot be
  confirmed from outside; the platform says so instead of guessing.
* **Rate limits & transient failures are normal** — 429/5xx are retried with back-off, mid-stream error chunks
  are surfaced, and free models are run with low concurrency.
* **Cost is reported by the API** (`usage.cost`) and accumulated per run.
* The serving **provider** (DeepInfra, Together, Fireworks, …) is recorded per request, because garbling
  and slowness are very often provider-specific (quantisation!).
"""
from __future__ import annotations

import difflib
import os
import re
import time
from collections import Counter
from typing import Any

import httpx

from .base import LaunchSpec, Provider, ProgressFn, Session
from .openai_provider import OpenAISession

def base_url() -> str:
    """Resolved per call so tests (and the local replay server) can point it elsewhere via EVAL_OPENROUTER_BASE_URL."""
    return os.environ.get("EVAL_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")



REFERER = "https://github.com/Cademiller23/eval_platform"
TITLE = "Coherence Lab"

# Shown first in the picker and used as the offline fallback. The live /models list is authoritative.
FEATURED: list[dict[str, Any]] = [
    {"id": "meta-llama/llama-3.1-8b-instruct", "name": "Llama 3.1 8B Instruct", "hf": "meta-llama/Llama-3.1-8B-Instruct"},
    {"id": "meta-llama/llama-3.3-70b-instruct", "name": "Llama 3.3 70B Instruct", "hf": "meta-llama/Llama-3.3-70B-Instruct"},
    {"id": "qwen/qwen-2.5-7b-instruct", "name": "Qwen2.5 7B Instruct", "hf": "Qwen/Qwen2.5-7B-Instruct"},
    {"id": "qwen/qwen-2.5-72b-instruct", "name": "Qwen2.5 72B Instruct", "hf": "Qwen/Qwen2.5-72B-Instruct"},
    {"id": "mistralai/mistral-nemo", "name": "Mistral Nemo 12B", "hf": "mistralai/Mistral-Nemo-Instruct-2407"},
    {"id": "google/gemma-2-9b-it", "name": "Gemma 2 9B", "hf": "google/gemma-2-9b-it"},
    {"id": "deepseek/deepseek-chat", "name": "DeepSeek V3", "hf": "deepseek-ai/DeepSeek-V3"},
    {"id": "deepseek/deepseek-r1-distill-llama-70b", "name": "DeepSeek R1 Distill Llama 70B", "hf": "deepseek-ai/DeepSeek-R1-Distill-Llama-70B"},
    {"id": "openai/gpt-4o-mini", "name": "GPT-4o mini", "hf": None},
    {"id": "anthropic/claude-3.5-haiku", "name": "Claude 3.5 Haiku", "hf": None},
    {"id": "google/gemini-2.0-flash-001", "name": "Gemini 2.0 Flash", "hf": None},
]

_REASONING_HINT = re.compile(r"(r1|reason|thinking|qwq|o1|o3|o4|deepseek-r)", re.I)
_cache: dict[str, Any] = {"t": 0.0, "models": None, "base": None}


def api_key(explicit: str | None = None) -> str | None:
    return (explicit or os.environ.get("OPENROUTER_API_KEY") or "").strip() or None


def normalize(m: dict[str, Any]) -> dict[str, Any]:
    pricing = m.get("pricing") or {}

    def per_million(v: Any) -> float | None:
        try:
            return round(float(v) * 1e6, 4)
        except (TypeError, ValueError):
            return None

    arch = m.get("architecture") or {}
    slug = m["id"]
    p_in, p_out = per_million(pricing.get("prompt")), per_million(pricing.get("completion"))
    params = m.get("supported_parameters") or []
    return {
        "id": slug,
        "name": m.get("name") or slug,
        "vendor": slug.split("/")[0],
        "context_length": m.get("context_length") or (m.get("top_provider") or {}).get("context_length"),
        "prompt_per_m": p_in,
        "completion_per_m": p_out,
        "free": slug.endswith(":free") or (p_in == 0 and p_out == 0),
        "open_weights": bool(m.get("hugging_face_id")),
        "hf_id": m.get("hugging_face_id") or None,
        "reasoning": "reasoning" in params or bool(_REASONING_HINT.search(slug)),
        "text_out": "text" in (arch.get("output_modalities") or ["text"]),
        "description": (m.get("description") or "")[:240],
    }


async def fetch_models(force: bool = False, client: httpx.AsyncClient | None = None) -> tuple[list[dict[str, Any]], bool]:
    """(models, live). The public listing needs no key. Falls back to FEATURED when offline."""
    if not force and _cache["models"] and _cache.get("base") == base_url() and time.time() - _cache["t"] < 600:
        return _cache["models"], True
    own = client is None
    client = client or httpx.AsyncClient(timeout=20)
    try:
        r = await client.get(f"{base_url()}/models", headers={"HTTP-Referer": REFERER, "X-Title": TITLE})
        r.raise_for_status()
        models = [normalize(m) for m in r.json().get("data", []) if m.get("id")]
        models = [m for m in models if m["text_out"]]
        models.sort(key=lambda m: (m["vendor"], m["name"].lower()))
        _cache.update(t=time.time(), models=models, base=base_url())
        return models, True
    except (httpx.HTTPError, ValueError, KeyError):
        fallback = [normalize({"id": f["id"], "name": f["name"], "hugging_face_id": f["hf"]}) for f in FEATURED]
        return fallback, False
    finally:
        if own:
            await client.aclose()


class OpenRouterSession(OpenAISession):
    max_retries = 4
    wants_logprobs = False            # many upstream providers do not support logprobs; it is optional anyway
    wants_continuous_usage = False

    def __init__(self, api_key_: str, model: str, info: dict[str, Any], extra_body: dict[str, Any] | None = None, max_concurrency: int = 6):
        super().__init__(base_url(), api_key_, model, info, extra_body)
        self.max_concurrency = max_concurrency
        self.cost_usd = 0.0
        self.cost_seen = False
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.providers: Counter[str] = Counter()

    def _headers(self) -> dict[str, str]:
        h = super()._headers()
        h["HTTP-Referer"] = REFERER
        h["X-Title"] = TITLE
        return h

    def _request_body(self, messages, max_tokens, temperature, extra):
        body = super()._request_body(messages, max_tokens, temperature, extra)
        body["usage"] = {"include": True}      # ask OpenRouter to return cost with the final usage chunk
        return body

    def _on_chunk(self, obj, state):
        prov = obj.get("provider")
        if prov and not state.get("provider_seen"):
            state["provider_seen"] = True
            self.providers[str(prov)] += 1
        usage = obj.get("usage")
        if usage and not state.get("usage_counted"):
            state["usage_counted"] = True
            self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
            self.completion_tokens += int(usage.get("completion_tokens") or 0)
            cost = usage.get("cost")
            if cost is not None:
                try:
                    self.cost_usd += float(cost)
                    self.cost_seen = True
                except (TypeError, ValueError):
                    pass

    async def metrics(self) -> str | None:   # hosted: nothing to scrape
        return None

    def run_summary(self) -> dict[str, Any]:
        return {
            "cost_usd": round(self.cost_usd, 6) if self.cost_seen else None,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "providers_seen": dict(self.providers),
        }


class OpenRouterProvider(Provider):
    name = "openrouter"
    label = "OpenRouter"

    def available(self) -> tuple[bool, str]:
        if api_key():
            return True, "Evaluate any hosted model via OpenRouter (key found in OPENROUTER_API_KEY)."
        return False, "Set OPENROUTER_API_KEY (or paste a key in Run options) to evaluate hosted models."

    async def start(self, spec: LaunchSpec, progress: ProgressFn) -> Session:
        ep = spec.endpoint or {}
        key = api_key(ep.get("api_key"))
        if not key:
            raise RuntimeError("No OpenRouter API key. Set OPENROUTER_API_KEY or enter one in Run options.")
        slug = (ep.get("model") or "").strip()
        if not slug:
            raise ValueError("Choose an OpenRouter model.")
        progress("info", f"Checking OpenRouter key and model '{slug}' …")
        async with httpx.AsyncClient(timeout=25) as c:
            headers = {"Authorization": f"Bearer {key}", "HTTP-Referer": REFERER, "X-Title": TITLE}
            key_info: dict[str, Any] = {}
            try:
                r = await c.get(f"{base_url()}/key", headers=headers)
            except httpx.HTTPError as e:
                raise RuntimeError(f"Cannot reach OpenRouter ({base_url()}): {e}") from e
            if r.status_code in (401, 403):
                raise RuntimeError("OpenRouter rejected the API key (HTTP %d). Check OPENROUTER_API_KEY." % r.status_code)
            if r.status_code == 200:
                key_info = (r.json() or {}).get("data") or {}
                limit, used = key_info.get("limit"), key_info.get("usage")
                if limit is not None and used is not None and limit - used <= 0:
                    raise RuntimeError("This OpenRouter key has no credit left.")
            models, live = await fetch_models(client=c)
        meta = next((m for m in models if m["id"] == slug), None)
        if live and meta is None:
            close = difflib.get_close_matches(slug, [m["id"] for m in models], n=4, cutoff=0.5)
            raise ValueError(f"OpenRouter has no model '{slug}'." + (f" Did you mean: {', '.join(close)}?" if close else ""))
        meta = meta or {"id": slug, "name": slug, "open_weights": False, "free": slug.endswith(":free"), "context_length": None}

        # Fill what we learned about the model back into the shared model dict.
        spec.model.update({
            "name": meta["name"], "context": meta.get("context_length") or spec.model.get("context") or 8192,
            "open_weights": meta["open_weights"], "reasoning": bool(meta.get("reasoning") or spec.model.get("reasoning")),
        })
        free = bool(meta.get("free") or key_info.get("is_free_tier"))
        cap = int(os.environ.get("EVAL_OPENROUTER_CONCURRENCY", "2" if meta.get("free") else "6"))
        if meta.get("free"):
            progress("warn", "Free models are heavily rate-limited — running with low concurrency; expect retries and slower tests.")
        if meta.get("prompt_per_m") is not None:
            progress("info", f"Pricing: ${meta['prompt_per_m']}/M input · ${meta['completion_per_m']}/M output tokens (a full run uses ≈ 60k tokens).")

        extra_body: dict[str, Any] = {}
        if spec.model.get("chat_template_kwargs"):
            extra_body["chat_template_kwargs"] = spec.model["chat_template_kwargs"]
        info = {
            "provider": "openrouter",
            "engine": "OpenRouter (hosted API)",
            "engine_version": None,
            "served_model": slug,
            "hosted": True,
            "gpu": None,
            "gpu_count": 1,
            "max_model_len": meta.get("context_length"),
            "speculative_config": None,
            "speculative_known": False,
            "metrics_available": False,
            "behavior_unreliable": True,
            "dtype": None,
            "cold_start_s": 0.0,
            "base_url": base_url(),
            "open_weights": meta["open_weights"],
            "hf_id": meta.get("hf_id"),
            "pricing": {"prompt_per_m": meta.get("prompt_per_m"), "completion_per_m": meta.get("completion_per_m")},
            "free_tier": free,
            "key_label": key_info.get("label"),
            "live_model_list": live,
        }
        session = OpenRouterSession(key, slug, info, extra_body, max_concurrency=cap)
        progress("ok", f"OpenRouter ready — {meta['name']} (context {meta.get('context_length') or '?'} tokens).")
        return session
