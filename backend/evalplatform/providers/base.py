from __future__ import annotations

import statistics
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

ProgressFn = Callable[[str, str], None]  # (level, message)


@dataclass
class LaunchSpec:
    """Everything a provider needs to bring a model up."""

    model: dict[str, Any]                       # catalog entry
    gpu: str | None = None                      # e.g. "H100" / "A100-80GB:2"
    speculative_config: dict[str, Any] | None = None   # vLLM --speculative-config payload
    max_model_len: int = 8192
    dtype: str = "auto"
    quantization: str | None = None
    extra_args: list[str] = field(default_factory=list)
    hf_token: str | None = None
    endpoint: dict[str, Any] | None = None      # custom OpenAI-compatible endpoint
    speculative_label: str = "none"             # none | ngram | eagle3 | mtp | draft_model | custom | auto


@dataclass
class ChatResult:
    text: str = ""
    finish_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    ttft_s: float | None = None
    total_s: float = 0.0
    chunks: list[tuple[float, int]] = field(default_factory=list)  # (t, tokens_in_chunk)
    logprobs: list[float] = field(default_factory=list)
    error: str | None = None

    @property
    def decode_tps(self) -> float | None:
        """Tokens/s after the first token — the metric users feel when reading a stream."""
        if self.ttft_s is None or self.completion_tokens < 2:
            return None
        dt = self.total_s - self.ttft_s
        if dt <= 0:
            return None
        # first token arrives at ttft; the remaining (n-1) tokens arrive during dt
        first = self.chunks[0][1] if self.chunks else 1
        return max(0.0, (self.completion_tokens - first)) / dt if dt > 0 else None

    @property
    def multi_token_chunk_ratio(self) -> float:
        if not self.chunks:
            return 0.0
        return sum(1 for _, n in self.chunks if n > 1) / len(self.chunks)

    @property
    def tokens_per_chunk(self) -> float:
        if not self.chunks:
            return 1.0
        return sum(n for _, n in self.chunks) / len(self.chunks)


class Session(ABC):
    """A live, ready-to-query model."""

    info: dict[str, Any]

    @abstractmethod
    def stream(self, messages: list[dict[str, Any]], *, max_tokens: int, temperature: float,
               meta: dict[str, Any] | None = None, extra: dict[str, Any] | None = None) -> AsyncIterator[dict[str, Any]]:
        """Yield event dicts: {'t','text','n','lp'} ... then {'done':True,'finish_reason','usage'}."""

    async def chat(self, messages: list[dict[str, Any]], *, max_tokens: int = 512, temperature: float = 0.0,
                   meta: dict[str, Any] | None = None, extra: dict[str, Any] | None = None) -> ChatResult:
        return await collect(self.stream(messages, max_tokens=max_tokens, temperature=temperature, meta=meta, extra=extra))

    async def metrics(self) -> str | None:
        """Prometheus text from the engine, if reachable."""
        return None

    async def close(self) -> None:  # pragma: no cover - trivial
        return None


class Provider(ABC):
    name: str
    label: str

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """(is usable, human readable reason/hint)."""

    @abstractmethod
    async def start(self, spec: LaunchSpec, progress: ProgressFn) -> Session:
        ...


async def collect(events: AsyncIterator[dict[str, Any]]) -> ChatResult:
    res = ChatResult()
    parts: list[str] = []
    async for ev in events:
        if ev.get("error"):
            res.error = str(ev["error"])
            break
        if ev.get("done"):
            res.finish_reason = ev.get("finish_reason")
            usage = ev.get("usage") or {}
            res.prompt_tokens = int(usage.get("prompt_tokens") or res.prompt_tokens)
            if usage.get("completion_tokens"):
                res.completion_tokens = int(usage["completion_tokens"])
            res.total_s = float(ev.get("total_t") or res.total_s)
            continue
        n = int(ev.get("n") or 0)
        text = ev.get("text") or ""
        t = float(ev.get("t") or 0.0)
        if text:
            parts.append(text)
        if n or text:
            if res.ttft_s is None:
                res.ttft_s = t
            res.chunks.append((t, max(n, 1 if text else 0)))
            res.completion_tokens = max(res.completion_tokens, sum(c[1] for c in res.chunks))
        res.logprobs.extend(ev.get("lp") or [])
        res.total_s = max(res.total_s, t)
    res.text = "".join(parts)
    return res


def median(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None
