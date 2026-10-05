"""ModalSession against a fake Modal class instance (no network / credentials)."""
from types import SimpleNamespace

from evalplatform.providers.modal_provider import ModalProvider, ModalSession


class _Aio:
    def __init__(self, fn):
        self.aio = fn


def fake_instance():
    async def stream_chat(payload):
        assert payload["messages"] and payload["logprobs"] is True and payload["seed"] == 0
        yield {"t": 0.05, "text": "Hi", "n": 1, "lp": [-0.1]}
        yield {"t": 0.07, "text": " there", "n": 2, "lp": [-0.1, -0.2]}
        yield {"done": True, "finish_reason": "stop", "usage": {"prompt_tokens": 3, "completion_tokens": 3}, "total_t": 0.07}

    async def metrics():
        return "vllm:generation_tokens_total 3\n"

    return SimpleNamespace(stream_chat=SimpleNamespace(remote_gen=_Aio(stream_chat)), metrics=SimpleNamespace(remote=_Aio(metrics)))


async def test_session_collects_events():
    s = ModalSession(fake_instance(), {})
    r = await s.chat([{"role": "user", "content": "x"}], max_tokens=10)
    assert r.text == "Hi there" and r.completion_tokens == 3 and r.ttft_s == 0.05
    assert r.multi_token_chunk_ratio == 0.5
    assert "generation_tokens" in await s.metrics()


def test_unavailable_without_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    ok, why = ModalProvider().available()
    assert not ok and "modal token" in why.lower()
