from __future__ import annotations

from .base import ChatResult, LaunchSpec, Provider, Session
from .mock_provider import MockProvider
from .modal_provider import ModalProvider
from .openai_provider import OpenAIProvider

_PROVIDERS: dict[str, Provider] = {p.name: p for p in (ModalProvider(), OpenAIProvider(), MockProvider())}


def get_provider(name: str) -> Provider:
    try:
        return _PROVIDERS[name]
    except KeyError as e:
        raise ValueError(f"Unknown provider '{name}'. Choose one of: {', '.join(_PROVIDERS)}") from e


def list_providers() -> list[dict]:
    out = []
    for p in _PROVIDERS.values():
        ok, hint = p.available()
        out.append({"id": p.name, "label": p.label, "available": ok, "hint": hint})
    return out


__all__ = ["ChatResult", "LaunchSpec", "Provider", "Session", "get_provider", "list_providers"]
