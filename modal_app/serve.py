"""Modal serving app for the evaluation platform.

Deploy once (the platform will also do this automatically on first use):

    modal deploy modal_app/serve.py

`ModelServer` is a *parameterised* Modal class: the platform instantiates it with the model
to evaluate (and an optional speculative-decoding config) and picks the GPU at call time via
`Cls.with_options(gpu=...)`. Each distinct parameter set gets its own autoscaled container,
which boots a vLLM OpenAI-compatible server on localhost and exposes timed streaming methods.

Environment variables read at *deploy* time:
    EVAL_MODAL_APP          app name                      (default coherence-eval-serving)
    EVAL_VLLM_VERSION       vLLM version to install       (default 0.11.0)
    EVAL_MODAL_SCALEDOWN    idle seconds before shutdown  (default 120)
    EVAL_MODAL_MAX_CONTAINERS                              (default 4)
"""
import collections
import json
import os
import subprocess
import threading
import time

import modal

APP_NAME = os.environ.get("EVAL_MODAL_APP", "coherence-eval-serving")
VLLM_VERSION = os.environ.get("EVAL_VLLM_VERSION", "0.11.0")
SCALEDOWN = int(os.environ.get("EVAL_MODAL_SCALEDOWN", "120"))
MAX_CONTAINERS = int(os.environ.get("EVAL_MODAL_MAX_CONTAINERS", "4"))
DEFAULT_GPU = os.environ.get("EVAL_MODAL_DEFAULT_GPU", "H100")

HF_CACHE_DIR = "/hf-cache"
VLLM_CACHE_DIR = "/vllm-cache"
VLLM_PORT = 8000
SERVED_NAME = "eval-model"

app = modal.App(APP_NAME)

image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .uv_pip_install(f"vllm=={VLLM_VERSION}", "huggingface_hub[hf_transfer]", "httpx")
    .env(
        {
            "HF_HUB_ENABLE_HF_TRANSFER": "1",
            "HF_HOME": HF_CACHE_DIR,
            "VLLM_CACHE_ROOT": VLLM_CACHE_DIR,
            "TOKENIZERS_PARALLELISM": "false",
            "VLLM_LOGGING_LEVEL": "INFO",
        }
    )
)

hf_cache = modal.Volume.from_name("eval-hf-cache", create_if_missing=True)
vllm_cache = modal.Volume.from_name("eval-vllm-cache", create_if_missing=True)


def _gpu_names() -> list[str]:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], text=True, timeout=20
        )
        return [line.strip() for line in out.splitlines() if line.strip()]
    except Exception:
        return []


@app.cls(
    image=image,
    gpu=DEFAULT_GPU,
    volumes={HF_CACHE_DIR: hf_cache, VLLM_CACHE_DIR: vllm_cache},
    timeout=60 * 60,
    startup_timeout=45 * 60,
    scaledown_window=SCALEDOWN,
    max_containers=MAX_CONTAINERS,
)
@modal.concurrent(max_inputs=64)
class ModelServer:
    model: str = modal.parameter()
    revision: str = modal.parameter(default="")
    spec_config: str = modal.parameter(default="")      # JSON for vLLM --speculative-config ("" = off)
    max_model_len: int = modal.parameter(default=8192)
    dtype: str = modal.parameter(default="auto")
    quantization: str = modal.parameter(default="")
    trust_remote_code: bool = modal.parameter(default=False)
    extra_args: str = modal.parameter(default="[]")     # JSON list of extra `vllm serve` args

    # ------------------------------------------------------------------ lifecycle
    @modal.enter()
    def boot(self):
        t0 = time.time()
        gpus = _gpu_names()
        tp = max(1, len(gpus))
        cmd = [
            "vllm", "serve", self.model,
            "--host", "127.0.0.1", "--port", str(VLLM_PORT),
            "--served-model-name", SERVED_NAME,
            "--max-model-len", str(self.max_model_len),
            "--dtype", self.dtype,
            "--gpu-memory-utilization", "0.90",
            "--tensor-parallel-size", str(tp),
        ]
        if self.revision:
            cmd += ["--revision", self.revision]
        if self.quantization:
            cmd += ["--quantization", self.quantization]
        if self.trust_remote_code:
            cmd += ["--trust-remote-code"]
        if self.spec_config:
            json.loads(self.spec_config)  # fail fast on bad JSON
            cmd += ["--speculative-config", self.spec_config]
        cmd += [str(a) for a in json.loads(self.extra_args or "[]")]

        self._cmd = cmd
        self._gpus = gpus
        self._logs: collections.deque[str] = collections.deque(maxlen=500)
        print("launching:", " ".join(cmd), flush=True)
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

        def pump():
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                line = line.rstrip()
                self._logs.append(line)
                print(line, flush=True)

        threading.Thread(target=pump, daemon=True).start()

        import httpx

        deadline = time.time() + 40 * 60
        while True:
            if self._proc.poll() is not None:
                tail = "\n".join(list(self._logs)[-40:])
                raise RuntimeError(f"vLLM exited with code {self._proc.returncode} during startup.\n{tail}")
            try:
                if httpx.get(f"http://127.0.0.1:{VLLM_PORT}/health", timeout=2).status_code == 200:
                    break
            except Exception:
                pass
            if time.time() > deadline:
                self._proc.terminate()
                raise TimeoutError("vLLM did not become healthy within 40 minutes")
            time.sleep(2)

        self._load_seconds = time.time() - t0
        try:  # persist freshly downloaded weights for the next cold start
            hf_cache.commit()
            vllm_cache.commit()
        except Exception as e:  # pragma: no cover
            print("volume commit failed:", e, flush=True)

    @modal.exit()
    def shutdown(self):
        proc = getattr(self, "_proc", None)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except Exception:
                proc.kill()

    # ------------------------------------------------------------------ methods
    @modal.method()
    def info(self) -> dict:
        import httpx
        from importlib.metadata import PackageNotFoundError, version

        try:
            engine_version = version("vllm")
        except PackageNotFoundError:
            engine_version = "unknown"
        served_len = None
        try:
            data = httpx.get(f"http://127.0.0.1:{VLLM_PORT}/v1/models", timeout=10).json()["data"][0]
            served_len = data.get("max_model_len")
        except Exception:
            pass
        spec_logs = [l for l in self._logs if "speculat" in l.lower() or "eagle" in l.lower() or "mtp" in l.lower()][-10:]
        return {
            "provider": "modal",
            "engine": "vLLM",
            "engine_version": engine_version,
            "served_model": SERVED_NAME,
            "model": self.model,
            "gpu_names": self._gpus,
            "gpu_count": max(1, len(self._gpus)),
            "max_model_len": served_len or self.max_model_len,
            "dtype": self.dtype,
            "quantization": self.quantization or None,
            "speculative_config": json.loads(self.spec_config) if self.spec_config else None,
            "speculative_known": True,
            "speculative_log_lines": spec_logs,
            "cold_start_s": round(getattr(self, "_load_seconds", 0.0), 1),
            "command": " ".join(self._cmd),
            "metrics_available": True,
        }

    @modal.method()
    def metrics(self) -> str:
        import httpx

        return httpx.get(f"http://127.0.0.1:{VLLM_PORT}/metrics", timeout=10).text

    @modal.method()
    def tail_logs(self, n: int = 60) -> list[str]:
        return list(self._logs)[-n:]

    @modal.method()
    def stream_chat(self, payload: dict):
        """Stream a chat completion, yielding events timed *inside* the container so network
        latency between your machine and Modal never pollutes tokens/s or TTFT."""
        import httpx

        body = dict(payload)
        body["model"] = SERVED_NAME
        body["stream"] = True
        body.setdefault("stream_options", {"include_usage": True, "continuous_usage_stats": True})
        t0 = time.perf_counter()
        seen = 0
        usage = None
        finish = None
        try:
            for attempt in range(2):
                with httpx.stream("POST", f"http://127.0.0.1:{VLLM_PORT}/v1/chat/completions", json=body, timeout=600) as r:
                    if r.status_code >= 400:
                        r.read()
                        # Some vLLM builds reject logprobs together with speculative decoding.
                        # Logprobs are only an optional health signal, so retry without them.
                        if attempt == 0 and body.get("logprobs") and "logprob" in r.text.lower():
                            body.pop("logprobs", None)
                            continue
                        yield {"error": f"HTTP {r.status_code}: {r.text[:500]}"}
                        return
                    for line in r.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload_s = line[5:].strip()
                        if payload_s == "[DONE]":
                            break
                        obj = json.loads(payload_s)
                        if obj.get("usage"):
                            usage = obj["usage"]
                        choices = obj.get("choices") or []
                        if not choices:
                            continue
                        ch = choices[0]
                        delta = ch.get("delta") or {}
                        text = delta.get("content") or ""
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                        if reasoning:
                            text = reasoning + text
                        n = 0
                        if usage and usage.get("completion_tokens") is not None:
                            total = int(usage["completion_tokens"])
                            n = max(0, total - seen)
                            seen = total
                        elif text:
                            n = 1
                        lp = [
                            float(i["logprob"])
                            for i in ((ch.get("logprobs") or {}).get("content") or [])
                            if i.get("logprob") is not None
                        ]
                        if text or n or lp:
                            yield {"t": time.perf_counter() - t0, "text": text, "n": n or (1 if text else 0), "lp": lp}
                        if ch.get("finish_reason"):
                            finish = ch["finish_reason"]
                break
        except Exception as e:
            yield {"error": f"{type(e).__name__}: {e}"}
            return
        yield {"done": True, "finish_reason": finish, "usage": usage or {}, "total_t": time.perf_counter() - t0}
