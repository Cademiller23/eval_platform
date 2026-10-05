"""Model catalog shown in the main-screen dropdown.

Every entry is a Hugging Face repo that vLLM can serve. ``speculators`` lists the
speculative-decoding options we know of for that model; entries flagged
``verified: False`` are community artifacts — double-check the repo exists and matches
your target model/tokenizer before relying on it.
"""
from __future__ import annotations

import math
import re
from typing import Any

# fmt: off
_NGRAM = {"method": "ngram", "note": "No extra weights needed. Best on code, RAG, summarisation and any output that copies from the prompt."}


def _draft(repo: str, note: str = "Smaller sibling sharing the tokenizer. Check your engine version supports draft-model speculation (vLLM V1 support landed late; SGLang/TensorRT-LLM support it)."):
    return {"method": "draft_model", "repo": repo, "note": note, "verified": True}


def _eagle3(repo: str, verified: bool = True, note: str = "Trained EAGLE-3 head on the target model's hidden states; typically the biggest low-batch speed-up."):
    return {"method": "eagle3", "repo": repo, "note": note, "verified": verified}


CATALOG: list[dict[str, Any]] = [
    # ---------------------------------------------------------------- Llama
    dict(id="llama-3.2-1b", name="Llama 3.2 1B Instruct", family="Llama", hf_repo="meta-llama/Llama-3.2-1B-Instruct",
         params_b=1.24, context=131072, min_gpu="L4", gated=True, tags=["tiny", "edge"],
         description="Meta's smallest Llama 3.2 — a good smoke-test and a common draft model.",
         speculators=[dict(_NGRAM)], mock_quality=0.30),
    dict(id="llama-3.2-3b", name="Llama 3.2 3B Instruct", family="Llama", hf_repo="meta-llama/Llama-3.2-3B-Instruct",
         params_b=3.21, context=131072, min_gpu="L4", gated=True, tags=["small"],
         description="Compact general model for on-device and low-cost serving.",
         speculators=[dict(_NGRAM), _draft("meta-llama/Llama-3.2-1B-Instruct")], mock_quality=0.45),
    dict(id="llama-3.1-8b", name="Llama 3.1 8B Instruct", family="Llama", hf_repo="meta-llama/Llama-3.1-8B-Instruct",
         params_b=8.03, context=131072, min_gpu="A10G", gated=True, tags=["popular", "general"],
         description="The workhorse open model. Mature tooling and several EAGLE heads available.",
         speculators=[_eagle3("yuhuili/EAGLE3-LLaMA3.1-Instruct-8B"), dict(_NGRAM), _draft("meta-llama/Llama-3.2-1B-Instruct")], mock_quality=0.62),
    dict(id="llama-3.3-70b", name="Llama 3.3 70B Instruct", family="Llama", hf_repo="meta-llama/Llama-3.3-70B-Instruct",
         params_b=70.6, context=131072, min_gpu="H200:2", gated=True, tags=["large", "general"],
         description="Frontier-class open dense model. Needs multi-GPU in bf16.",
         speculators=[_eagle3("yuhuili/EAGLE3-LLaMA3.3-Instruct-70B", verified=False), dict(_NGRAM), _draft("meta-llama/Llama-3.2-1B-Instruct")], mock_quality=0.88),
    # ---------------------------------------------------------------- Qwen 2.5
    dict(id="qwen2.5-7b", name="Qwen2.5 7B Instruct", family="Qwen", hf_repo="Qwen/Qwen2.5-7B-Instruct",
         params_b=7.62, context=32768, min_gpu="A10G", tags=["popular", "general", "multilingual"],
         description="Strong all-rounder at 7B with excellent math and multilingual ability.",
         speculators=[dict(_NGRAM), _draft("Qwen/Qwen2.5-0.5B-Instruct")], mock_quality=0.68),
    dict(id="qwen2.5-coder-7b", name="Qwen2.5 Coder 7B Instruct", family="Qwen", hf_repo="Qwen/Qwen2.5-Coder-7B-Instruct",
         params_b=7.62, context=32768, min_gpu="A10G", tags=["coding"],
         description="Code-specialised Qwen2.5. Expect high ngram acceptance on code.",
         speculators=[dict(_NGRAM), _draft("Qwen/Qwen2.5-Coder-0.5B-Instruct")], mock_quality=0.66, mock_bias={"coding": 0.2, "math": -0.05}),
    dict(id="qwen2.5-14b", name="Qwen2.5 14B Instruct", family="Qwen", hf_repo="Qwen/Qwen2.5-14B-Instruct",
         params_b=14.77, context=32768, min_gpu="L40S", tags=["general"],
         description="Sweet spot between quality and cost on a single 48GB GPU.",
         speculators=[dict(_NGRAM), _draft("Qwen/Qwen2.5-0.5B-Instruct")], mock_quality=0.78),
    dict(id="qwen2.5-32b", name="Qwen2.5 32B Instruct", family="Qwen", hf_repo="Qwen/Qwen2.5-32B-Instruct",
         params_b=32.76, context=32768, min_gpu="H100", tags=["large", "general"],
         description="High-quality dense 32B; fits one H100 in bf16 with a modest context.",
         speculators=[dict(_NGRAM), _draft("Qwen/Qwen2.5-0.5B-Instruct")], mock_quality=0.84),
    # ---------------------------------------------------------------- Qwen 3
    dict(id="qwen3-8b", name="Qwen3 8B", family="Qwen", hf_repo="Qwen/Qwen3-8B",
         params_b=8.19, context=40960, min_gpu="A10G", tags=["popular", "reasoning", "new"],
         description="Hybrid thinking model. Evaluated in non-thinking mode for fair latency numbers.",
         chat_template_kwargs={"enable_thinking": False},
         speculators=[_eagle3("Tengyunw/qwen3_8b_eagle3", verified=False), dict(_NGRAM), _draft("Qwen/Qwen3-0.6B")], mock_quality=0.74),
    dict(id="qwen3-30b-a3b", name="Qwen3 30B-A3B (MoE)", family="Qwen", hf_repo="Qwen/Qwen3-30B-A3B",
         params_b=30.5, active_params_b=3.3, context=40960, min_gpu="H100", tags=["moe", "fast", "new"],
         description="Mixture-of-experts: 30B weights but only ~3B active per token — very fast decode.",
         chat_template_kwargs={"enable_thinking": False},
         speculators=[_eagle3("Tengyunw/qwen3_30b_moe_eagle3", verified=False), dict(_NGRAM)], mock_quality=0.82, mock_tps_boost=2.2),
    dict(id="qwen3-32b", name="Qwen3 32B", family="Qwen", hf_repo="Qwen/Qwen3-32B",
         params_b=32.8, context=40960, min_gpu="H100", tags=["large", "reasoning", "new"],
         description="Flagship dense Qwen3 (non-thinking mode for evaluation).",
         chat_template_kwargs={"enable_thinking": False},
         speculators=[dict(_NGRAM), _draft("Qwen/Qwen3-0.6B")], mock_quality=0.86),
    # ---------------------------------------------------------------- Mistral
    dict(id="mistral-7b-v0.3", name="Mistral 7B Instruct v0.3", family="Mistral", hf_repo="mistralai/Mistral-7B-Instruct-v0.3",
         params_b=7.25, context=32768, min_gpu="A10G", tags=["classic"],
         description="The model that started it all. Good fluency, weaker math/code than newer peers.",
         speculators=[dict(_NGRAM)], mock_quality=0.52),
    dict(id="mistral-small-24b", name="Mistral Small 24B Instruct 2501", family="Mistral", hf_repo="mistralai/Mistral-Small-24B-Instruct-2501",
         params_b=23.6, context=32768, min_gpu="H100", tags=["general"],
         description="Latency-optimised 24B with strong instruction following.",
         speculators=[dict(_NGRAM)], mock_quality=0.80),
    # ---------------------------------------------------------------- Google / Microsoft
    dict(id="gemma-2-9b", name="Gemma 2 9B Instruct", family="Gemma", hf_repo="google/gemma-2-9b-it",
         params_b=9.24, context=8192, min_gpu="L40S", gated=True, tags=["general"],
         description="Google's open 9B. Short 8k context; no system role in its chat template.",
         speculators=[dict(_NGRAM), _draft("google/gemma-2-2b-it")], mock_quality=0.66),
    dict(id="gemma-3-12b", name="Gemma 3 12B Instruct", family="Gemma", hf_repo="google/gemma-3-12b-it",
         params_b=12.2, context=131072, min_gpu="L40S", gated=True, tags=["general", "new"],
         description="Multimodal-capable Gemma 3, evaluated on text only.",
         speculators=[dict(_NGRAM)], mock_quality=0.76),
    dict(id="phi-4", name="Phi-4 14B", family="Phi", hf_repo="microsoft/phi-4",
         params_b=14.7, context=16384, min_gpu="L40S", tags=["reasoning", "stem"],
         description="Microsoft's STEM-leaning 14B; excellent maths for its size.",
         speculators=[dict(_NGRAM)], mock_quality=0.78, mock_bias={"math": 0.12, "general": -0.05}),
    # ---------------------------------------------------------------- DeepSeek / reasoning
    dict(id="r1-distill-qwen-7b", name="DeepSeek-R1-Distill-Qwen 7B", family="DeepSeek", hf_repo="deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
         params_b=7.62, context=131072, min_gpu="A10G", reasoning=True, tags=["reasoning"],
         description="Reasoning distillation — emits long <think> traces before answering.",
         speculators=[dict(_NGRAM), _draft("deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B")], mock_quality=0.66, mock_bias={"math": 0.2, "general": -0.12}),
    dict(id="r1-distill-qwen-32b", name="DeepSeek-R1-Distill-Qwen 32B", family="DeepSeek", hf_repo="deepseek-ai/DeepSeek-R1-Distill-Qwen-32B",
         params_b=32.8, context=131072, min_gpu="H100", reasoning=True, tags=["reasoning", "large"],
         description="Larger reasoning distillation with strong maths and code.",
         speculators=[dict(_NGRAM), _draft("deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B")], mock_quality=0.82, mock_bias={"math": 0.15}),
    dict(id="deepseek-r1", name="DeepSeek-R1 (671B MoE)", family="DeepSeek", hf_repo="deepseek-ai/DeepSeek-R1",
         params_b=671, active_params_b=37, context=131072, min_gpu="H200:8", reasoning=True, trust_remote_code=True,
         dtype_bytes=1.0, quantization=None, tags=["moe", "reasoning", "frontier", "mtp"],
         description="Frontier reasoning MoE with native multi-token-prediction (MTP) layers.",
         mtp_native=True, mtp_method="deepseek_mtp", speculators=[dict(_NGRAM)], mock_quality=0.95, mock_tps_boost=1.0),
    dict(id="glm-4.5-air", name="GLM-4.5-Air (106B MoE)", family="GLM", hf_repo="zai-org/GLM-4.5-Air",
         params_b=106, active_params_b=12, context=131072, min_gpu="H200:2", trust_remote_code=True, tags=["moe", "mtp", "agentic"],
         description="Agent-oriented MoE with native MTP head for speculative decoding.",
         mtp_native=True, mtp_method="glm4_moe_mtp", speculators=[dict(_NGRAM)], mock_quality=0.88, mock_tps_boost=1.6),
    # ---------------------------------------------------------------- Small
    dict(id="smollm2-1.7b", name="SmolLM2 1.7B Instruct", family="SmolLM", hf_repo="HuggingFaceTB/SmolLM2-1.7B-Instruct",
         params_b=1.7, context=8192, min_gpu="L4", tags=["tiny", "edge"],
         description="Tiny open model — great for checking how the platform flags weak outputs.",
         speculators=[dict(_NGRAM)], mock_quality=0.28),
]
# fmt: on

# Fill defaults so every consumer can rely on the same keys.
for _m in CATALOG:
    _m.setdefault("active_params_b", _m["params_b"])
    _m.setdefault("gated", False)
    _m.setdefault("reasoning", False)
    _m.setdefault("trust_remote_code", False)
    _m.setdefault("mtp_native", False)
    _m.setdefault("chat_template_kwargs", {})
    _m.setdefault("dtype_bytes", 2.0)
    _m.setdefault("tags", [])
    _m.setdefault("mock_bias", {})
    _m.setdefault("mock_tps_boost", 1.0)
    _m.setdefault("custom", False)

_BY_ID = {m["id"]: m for m in CATALOG}
_BY_REPO = {m["hf_repo"].lower(): m for m in CATALOG}


def list_models() -> list[dict[str, Any]]:
    return CATALOG


def get_model(model_id: str) -> dict[str, Any] | None:
    return _BY_ID.get(model_id) or _BY_REPO.get(model_id.lower())


_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[bB](?![a-zA-Z])")


def custom_model(hf_repo: str, params_b: float | None = None, reasoning: bool | None = None) -> dict[str, Any]:
    """Build a catalog-like entry for an arbitrary Hugging Face repo id."""
    hf_repo = hf_repo.strip()
    known = _BY_REPO.get(hf_repo.lower())
    if known:
        return known
    if params_b is None:
        m = _SIZE_RE.search(hf_repo.split("/")[-1])
        params_b = float(m.group(1)) if m else 7.0
    name = hf_repo.split("/")[-1]
    low = hf_repo.lower()
    if reasoning is None:
        reasoning = any(k in low for k in ("r1", "reason", "thinking", "qwq", "o1"))
    family = name.split("-")[0].title()
    quality = max(0.2, min(0.92, 0.35 + 0.15 * math.log2(max(params_b, 1.0))))
    gpu = _suggest_gpu(params_b, 2.0)
    return dict(
        id=f"custom:{hf_repo}", name=name, family=family, hf_repo=hf_repo, params_b=params_b, active_params_b=params_b,
        context=32768, min_gpu=gpu, gated=False, reasoning=bool(reasoning), trust_remote_code=False, mtp_native=False,
        chat_template_kwargs={}, dtype_bytes=2.0, tags=["custom"], mock_bias={}, mock_tps_boost=1.0, mock_quality=quality,
        description="Custom Hugging Face model.", speculators=[dict(_NGRAM)], custom=True,
    )


def _suggest_gpu(params_b: float, bytes_per_param: float) -> str:
    gb = params_b * bytes_per_param * 1.25 + 2
    if gb <= 22:
        return "A10G"
    if gb <= 44:
        return "L40S"
    if gb <= 76:
        return "H100"
    if gb <= 135:
        return "H200"
    return f"H200:{min(8, math.ceil(gb / 135))}"
