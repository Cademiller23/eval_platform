"""Provider that boots models on Modal (vLLM on serverless GPUs)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, AsyncIterator

from ..config import get_settings, modal_credentials_present, modal_sdk_installed
from ..knowledge import match_gpu
from .base import LaunchSpec, Provider, ProgressFn, Session

SERVE_FILE = Path(__file__).resolve().parents[3] / "modal_app" / "serve.py"


class ModalSession(Session):
    def __init__(self, instance: Any, info: dict[str, Any]):
        self._inst = instance
        self.info = info

    async def stream(self, messages, *, max_tokens, temperature, meta=None, extra=None) -> AsyncIterator[dict[str, Any]]:
        payload: dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "logprobs": True,
            **(extra or {}),
        }
        if temperature == 0:
            payload.setdefault("seed", 0)
        try:
            async for ev in self._inst.stream_chat.remote_gen.aio(payload):
                yield ev
        except Exception as e:  # network / container failure
            yield {"error": f"{type(e).__name__}: {e}"}

    async def metrics(self) -> str | None:
        try:
            return await self._inst.metrics.remote.aio()
        except Exception:
            return None

    async def logs(self, n: int = 60) -> list[str]:
        try:
            return await self._inst.tail_logs.remote.aio(n)
        except Exception:
            return []


class ModalProvider(Provider):
    name = "modal"
    label = "Modal (GPU)"

    def available(self) -> tuple[bool, str]:
        if not modal_sdk_installed():
            return False, "Install the Modal SDK:  pip install modal"
        if not modal_credentials_present():
            return False, "Authenticate with Modal:  modal token new   (or set MODAL_TOKEN_ID / MODAL_TOKEN_SECRET)"
        return True, "Models boot on serverless GPUs via vLLM."

    # ------------------------------------------------------------------ helpers
    async def _lookup(self, progress: ProgressFn):
        import modal

        s = get_settings()
        try:
            cls = modal.Cls.from_name(s.modal_app, s.modal_cls)
            await cls.hydrate.aio()
            return cls
        except modal.exception.NotFoundError:
            progress("info", f"Serving app '{s.modal_app}' not deployed yet — deploying now (first run only, builds the vLLM image ~5-10 min)…")
            await self._deploy(progress)
            cls = modal.Cls.from_name(s.modal_app, s.modal_cls)
            await cls.hydrate.aio()
            return cls

    async def _deploy(self, progress: ProgressFn) -> None:
        if not SERVE_FILE.exists():
            raise RuntimeError(f"Cannot find {SERVE_FILE}; run `modal deploy modal_app/serve.py` manually.")
        env = {**os.environ, "EVAL_MODAL_APP": get_settings().modal_app}
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "modal", "deploy", str(SERVE_FILE),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env,
        )
        assert proc.stdout is not None
        tail: list[str] = []
        async for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                tail.append(line)
                tail[:] = tail[-30:]
                if any(k in line.lower() for k in ("building", "created", "deployed", "error", "image", "mount")):
                    progress("info", f"modal deploy: {line[:200]}")
        code = await proc.wait()
        if code != 0:
            raise RuntimeError("`modal deploy` failed:\n" + "\n".join(tail[-12:]))
        progress("ok", "Serving app deployed.")

    # ------------------------------------------------------------------ start
    async def start(self, spec: LaunchSpec, progress: ProgressFn) -> Session:
        ok, why = self.available()
        if not ok:
            raise RuntimeError(why)
        import modal

        s = get_settings()
        cls = await self._lookup(progress)
        gpu = spec.gpu or spec.model.get("min_gpu") or "H100"
        opts: dict[str, Any] = {"gpu": gpu}
        if spec.hf_token:
            opts["secrets"] = [modal.Secret.from_dict({"HF_TOKEN": spec.hf_token, "HUGGING_FACE_HUB_TOKEN": spec.hf_token})]
        elif spec.model.get("gated"):
            progress("warn", "This model is gated on Hugging Face but HF_TOKEN is not set — the download will likely fail.")
        cls = cls.with_options(**opts)

        extra_args = list(spec.extra_args)
        instance = cls(
            model=spec.model["hf_repo"],
            spec_config=json.dumps(spec.speculative_config) if spec.speculative_config else "",
            max_model_len=int(spec.max_model_len),
            dtype=spec.dtype or "auto",
            quantization=spec.quantization or "",
            trust_remote_code=bool(spec.model.get("trust_remote_code")),
            extra_args=json.dumps(extra_args),
        )

        progress("info", f"Requesting {gpu} container on Modal for {spec.model['hf_repo']} …")
        t0 = time.time()
        stop = asyncio.Event()

        async def heartbeat():
            stages = [
                (0, "Scheduling GPU container"),
                (20, "Pulling image / allocating GPU"),
                (60, "Downloading weights (cached in a Modal Volume after the first run)"),
                (180, "Loading weights, compiling CUDA graphs"),
            ]
            last = -1
            while not stop.is_set():
                el = time.time() - t0
                idx = max(i for i, (th, _) in enumerate(stages) if el >= th)
                if idx != last or int(el) % 30 == 0:
                    progress("info", f"{stages[idx][1]} … {int(el)}s elapsed")
                    last = idx
                try:
                    await asyncio.wait_for(stop.wait(), timeout=10)
                except asyncio.TimeoutError:
                    pass

        hb = asyncio.create_task(heartbeat())
        try:
            info = await asyncio.wait_for(instance.info.remote.aio(), timeout=s.provision_timeout_s)
        except asyncio.TimeoutError as e:
            raise RuntimeError(f"Timed out after {s.provision_timeout_s}s waiting for the model to start.") from e
        except Exception as e:
            raise RuntimeError(f"Modal container failed to start: {e}") from e
        finally:
            stop.set()
            hb.cancel()

        info["gpu"] = match_gpu((info.get("gpu_names") or [None])[0]) or gpu.split(":")[0]
        info["requested_gpu"] = gpu
        info["provision_s"] = round(time.time() - t0, 1)
        progress("ok", f"Model online on {info.get('gpu_names') or gpu} — vLLM {info.get('engine_version')} (boot {info.get('cold_start_s')}s).")
        return ModalSession(instance, info)
