"""Evaluate any running OpenAI-compatible server (vLLM, SGLang, TGI, Ollama, LM Studio, ...)."""
from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import httpx

from .base import LaunchSpec, Provider, ProgressFn, Session


def server_root(base_url: str) -> str:
    u = urlparse(base_url)
    return f"{u.scheme}://{u.netloc}"


def parse_sse_chunk(obj: dict[str, Any], state: dict[str, Any], t0: float) -> dict[str, Any] | None:
    """Translate one OpenAI streaming JSON chunk into our event dict (or None to skip)."""
    usage = obj.get("usage")
    choices = obj.get("choices") or []
    ev: dict[str, Any] | None = None
    if choices:
        ch = choices[0]
        delta = ch.get("delta") or {}
        text = delta.get("content") or ""
        # Servers started with a reasoning parser stream thinking separately; re-wrap it in
        # <think> tags so the grader treats it the same as inline reasoning.
        reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
        if reasoning:
            if not state.get("in_reasoning"):
                state["in_reasoning"] = True
                reasoning = "<think>" + reasoning
            text = reasoning + text
        elif text and state.get("in_reasoning"):
            state["in_reasoning"] = False
            text = "</think>" + text
        lp = []
        lps = ch.get("logprobs") or {}
        for item in (lps.get("content") or []):
            if item.get("logprob") is not None:
                lp.append(float(item["logprob"]))
        n = 0
        if usage and usage.get("completion_tokens") is not None:
            total = int(usage["completion_tokens"])
            n = max(0, total - state.get("seen_tokens", 0))
            state["seen_tokens"] = total
        elif text:
            n = 1
        if text or n or lp:
            ev = {"t": time.perf_counter() - t0, "text": text, "n": n or (1 if text else 0), "lp": lp}
        if ch.get("finish_reason"):
            state["finish_reason"] = ch["finish_reason"]
    if usage:
        state["usage"] = usage
    return ev


class OpenAISession(Session):
    def __init__(self, base_url: str, api_key: str | None, model: str, info: dict[str, Any], extra_body: dict[str, Any] | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.info = info
        self.extra_body = extra_body or {}
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=20.0))

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def stream(self, messages, *, max_tokens, temperature, meta=None, extra=None) -> AsyncIterator[dict[str, Any]]:
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True, "continuous_usage_stats": True},
            "logprobs": True,
            **self.extra_body,
            **(extra or {}),
        }
        if temperature == 0:
            body.setdefault("seed", 0)
        t0 = time.perf_counter()
        state: dict[str, Any] = {}
        try:
            async with self._client.stream("POST", f"{self.base_url}/chat/completions", headers=self._headers(), json=body) as r:
                if r.status_code >= 400 and body.get("logprobs"):
                    # Some servers reject logprobs/continuous usage; retry without the extras.
                    await r.aread()
                    body.pop("logprobs", None)
                    body["stream_options"] = {"include_usage": True}
                    async for ev in self._retry(body, t0, state):
                        yield ev
                    return
                if r.status_code >= 400:
                    detail = (await r.aread()).decode("utf-8", "replace")[:500]
                    yield {"error": f"HTTP {r.status_code}: {detail}"}
                    return
                async for line in r.aiter_lines():
                    ev = self._handle_line(line, state, t0)
                    if ev == "DONE":
                        break
                    if ev:
                        yield ev
        except httpx.HTTPError as e:
            yield {"error": f"{type(e).__name__}: {e}"}
            return
        yield {"done": True, "finish_reason": state.get("finish_reason"), "usage": state.get("usage") or {}, "total_t": time.perf_counter() - t0}

    async def _retry(self, body, t0, state):
        try:
            async with self._client.stream("POST", f"{self.base_url}/chat/completions", headers=self._headers(), json=body) as r:
                if r.status_code >= 400:
                    detail = (await r.aread()).decode("utf-8", "replace")[:500]
                    yield {"error": f"HTTP {r.status_code}: {detail}"}
                    return
                async for line in r.aiter_lines():
                    ev = self._handle_line(line, state, t0)
                    if ev == "DONE":
                        break
                    if ev:
                        yield ev
        except httpx.HTTPError as e:
            yield {"error": f"{type(e).__name__}: {e}"}
            return
        yield {"done": True, "finish_reason": state.get("finish_reason"), "usage": state.get("usage") or {}, "total_t": time.perf_counter() - t0}

    @staticmethod
    def _handle_line(line: str, state, t0):
        if not line or not line.startswith("data:"):
            return None
        payload = line[5:].strip()
        if payload == "[DONE]":
            return "DONE"
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return parse_sse_chunk(obj, state, t0)

    async def metrics(self) -> str | None:
        try:
            r = await self._client.get(f"{server_root(self.base_url)}/metrics", headers=self._headers(), timeout=10)
            head = r.text[:2000]
            if r.status_code == 200 and ("# HELP" in head or "# TYPE" in head):
                return r.text
        except httpx.HTTPError:
            return None
        return None

    async def close(self) -> None:
        await self._client.aclose()


class OpenAIProvider(Provider):
    name = "openai"
    label = "Custom endpoint"

    def available(self) -> tuple[bool, str]:
        return True, "Point at any running OpenAI-compatible server."

    async def start(self, spec: LaunchSpec, progress: ProgressFn) -> Session:
        ep = spec.endpoint or {}
        base_url = (ep.get("base_url") or "").strip()
        if not base_url:
            raise ValueError("Custom endpoint requires a base URL, e.g. http://localhost:8000/v1")
        base_url = base_url.rstrip("/")
        if not base_url.endswith("/v1") and "/v1" not in base_url:
            base_url += "/v1"
        model = ep.get("model") or spec.model["hf_repo"]
        progress("info", f"Connecting to {base_url} …")
        async with httpx.AsyncClient(timeout=20) as c:
            headers = {"Authorization": f"Bearer {ep['api_key']}"} if ep.get("api_key") else {}
            try:
                r = await c.get(f"{base_url}/models", headers=headers)
            except httpx.HTTPError as e:
                raise RuntimeError(f"Cannot reach {base_url}: {e}") from e
            served: list[str] = []
            max_len = None
            if r.status_code == 200:
                data = r.json().get("data", [])
                served = [d.get("id") for d in data]
                for d in data:
                    if d.get("id") == model:
                        max_len = d.get("max_model_len")
                if model not in served and served:
                    progress("warn", f"Model '{model}' not listed by server; using '{served[0]}'.")
                    model = served[0]
            elif r.status_code in (401, 403):
                raise RuntimeError(f"Endpoint rejected credentials (HTTP {r.status_code}).")
        info = {
            "provider": "openai",
            "engine": "unknown (OpenAI-compatible)",
            "engine_version": None,
            "served_model": model,
            "gpu": None,
            "gpu_count": 1,
            "max_model_len": max_len,
            "speculative_config": None,
            "speculative_known": False,
            "dtype": None,
            "cold_start_s": 0.0,
            "base_url": base_url,
        }
        extra_body: dict[str, Any] = {}
        if spec.model.get("chat_template_kwargs"):
            extra_body["chat_template_kwargs"] = spec.model["chat_template_kwargs"]
        session = OpenAISession(base_url, ep.get("api_key"), model, info, extra_body=extra_body)
        metrics = await session.metrics()
        if metrics:
            low = metrics.lower()
            info["engine"] = "vLLM" if "vllm:" in low else ("SGLang" if "sglang:" in low else "OpenAI-compatible")
            info["metrics_available"] = True
        progress("ok", f"Connected — serving '{model}'.")
        return session
