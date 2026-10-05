"""Evaluate any running OpenAI-compatible server (vLLM, SGLang, TGI, Ollama, LM Studio, ...).

``OpenAISession`` is also the base class of the OpenRouter session: the hooks ``_headers``,
``_request_body`` and ``_on_chunk`` exist so hosted APIs can customise requests without
re-implementing streaming, retries and error handling.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import httpx

from .base import LaunchSpec, Provider, ProgressFn, Session

RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}


def server_root(base_url: str) -> str:
    u = urlparse(base_url)
    return f"{u.scheme}://{u.netloc}"


def error_message(obj: Any) -> str:
    """Pull a readable message out of the many shapes providers use for errors."""
    if isinstance(obj, dict):
        err = obj.get("error", obj)
        if isinstance(err, dict):
            msg = err.get("message") or err.get("detail") or json.dumps(err)[:300]
            code = err.get("code")
            return f"{msg} (code {code})" if code else str(msg)
        return str(err)[:300]
    return str(obj)[:300]


def parse_sse_chunk(obj: dict[str, Any], state: dict[str, Any], t0: float) -> dict[str, Any] | None:
    """Translate one OpenAI streaming JSON chunk into our event dict (or None to skip)."""
    if obj.get("error"):  # mid-stream failure (OpenRouter and others send errors as data chunks)
        return {"error": error_message(obj)}
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
            if text:   # reasoning and answer in the same delta: close the think block before the answer starts
                state["in_reasoning"] = False
                reasoning += "</think>"
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
        # per-chunk token deltas only make sense when the server sends *continuous* usage; otherwise the
        # single final usage object (often riding on an empty-content chunk) must not become a fake N-token chunk
        if usage and state.get("continuous") and usage.get("completion_tokens") is not None:
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
    deadline_s = 900.0        # hard overall cap per request (keep-alive comments must not extend it forever)
    max_retries = 0           # retryable HTTP failures are retried only by subclasses that opt in
    wants_logprobs = True
    wants_continuous_usage = True

    def __init__(self, base_url: str, api_key: str | None, model: str, info: dict[str, Any], extra_body: dict[str, Any] | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.info = info
        self.extra_body = extra_body or {}
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=20.0))

    # ---- hooks -------------------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _request_body(self, messages, max_tokens, temperature, extra) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True, **({"continuous_usage_stats": True} if self.wants_continuous_usage else {})},
            **self.extra_body,
            **(extra or {}),
        }
        if self.wants_logprobs:
            body["logprobs"] = True
        if temperature == 0:
            body.setdefault("seed", 0)
        return body

    def _on_chunk(self, obj: dict[str, Any], state: dict[str, Any]) -> None:
        """Subclass hook: inspect raw chunk objects (provider names, cost, ...)."""

    def _redact(self, text: str) -> str:
        """Never let an exception message carry the API key (httpx echoes bad header values verbatim)."""
        t = re.sub(r"Bearer\s+\S+", "Bearer ***", text)
        if self.api_key:
            t = t.replace(self.api_key, "***")
        return t

    # ---- streaming ---------------------------------------------------------------------------
    async def stream(self, messages, *, max_tokens, temperature, meta=None, extra=None) -> AsyncIterator[dict[str, Any]]:
        body = self._request_body(messages, max_tokens, temperature, extra)
        t_start = time.perf_counter()
        attempt = 0
        while True:
            t0 = time.perf_counter()
            state: dict[str, Any] = {"continuous": self.wants_continuous_usage}
            emitted = False
            retry_after: float | None = None
            failure: str | None = None
            retry_body = False
            try:
                async with self._client.stream("POST", f"{self.base_url}/chat/completions", headers=self._headers(), json=body) as r:
                    if r.status_code >= 400:
                        raw = (await r.aread()).decode("utf-8", "replace")
                        try:
                            msg = error_message(json.loads(raw))
                        except ValueError:
                            msg = raw[:300]
                        if r.status_code == 400 and (body.get("logprobs") or "continuous_usage_stats" in body.get("stream_options", {})):
                            # Some servers reject logprobs / continuous usage; they are optional signals.
                            body.pop("logprobs", None)
                            body["stream_options"] = {"include_usage": True}
                            self.wants_logprobs = False
                            self.wants_continuous_usage = False
                            retry_body = True
                        else:
                            failure = f"HTTP {r.status_code}: {msg}"
                            if r.status_code in RETRYABLE:
                                try:
                                    retry_after = float(r.headers.get("retry-after", ""))
                                except ValueError:
                                    retry_after = None
                    else:
                        async for line in r.aiter_lines():
                            if not line or not line.startswith("data:"):
                                continue  # blank lines and ':' keep-alive comments
                            payload = line[5:].strip()
                            if payload == "[DONE]":
                                break
                            try:
                                obj = json.loads(payload)
                            except json.JSONDecodeError:
                                continue
                            if time.perf_counter() - t_start > self.deadline_s:
                                failure = f"Timed out: request exceeded {int(self.deadline_s)}s overall"
                                break
                            self._on_chunk(obj, state)
                            ev = parse_sse_chunk(obj, state, t0)
                            if ev and ev.get("error"):
                                failure = ev["error"]
                                break
                            if ev:
                                emitted = True
                                yield ev
            except (httpx.TimeoutException, httpx.TransportError) as e:
                failure = f"{type(e).__name__}: {e}"
                retry_after = retry_after or None
                retryable_exc = True
            else:
                retryable_exc = False

            if retry_body:
                continue
            if failure is None:
                yield {"done": True, "finish_reason": state.get("finish_reason"), "usage": state.get("usage") or {}, "total_t": time.perf_counter() - t0}
                return
            # an SSE error chunk ("... (code 502)") before any token is as retryable as an HTTP 502
            chunk_code = re.search(r"\(code (\d{3})\)\s*$", failure)
            retryable = retryable_exc or failure.startswith(tuple(f"HTTP {c}" for c in RETRYABLE)) or bool(chunk_code and int(chunk_code.group(1)) in RETRYABLE)
            can_retry = (not emitted) and attempt < self.max_retries and retryable
            if can_retry:
                delay = min(60.0, retry_after) if retry_after is not None else min(30.0, 1.5 * (2 ** attempt))
                attempt += 1
                await asyncio.sleep(delay)
                continue
            yield {"error": self._redact(failure)}
            return

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
