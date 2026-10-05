"""Static hardware / engine knowledge used by scoring and the recommendation engine."""
from __future__ import annotations

# name -> (memory GB per GPU, memory bandwidth GB/s per GPU, relative $/hr hint)
GPUS: dict[str, dict] = {
    "T4": {"mem_gb": 16, "bw_gbs": 320, "arch": "Turing", "bf16": False, "fp8": False},
    "L4": {"mem_gb": 24, "bw_gbs": 300, "arch": "Ada", "bf16": True, "fp8": True},
    "A10G": {"mem_gb": 24, "bw_gbs": 600, "arch": "Ampere", "bf16": True, "fp8": False},
    "L40S": {"mem_gb": 48, "bw_gbs": 864, "arch": "Ada", "bf16": True, "fp8": True},
    "A100-40GB": {"mem_gb": 40, "bw_gbs": 1555, "arch": "Ampere", "bf16": True, "fp8": False},
    "A100-80GB": {"mem_gb": 80, "bw_gbs": 2039, "arch": "Ampere", "bf16": True, "fp8": False},
    "H100": {"mem_gb": 80, "bw_gbs": 3350, "arch": "Hopper", "bf16": True, "fp8": True},
    "H200": {"mem_gb": 141, "bw_gbs": 4800, "arch": "Hopper", "bf16": True, "fp8": True},
    "B200": {"mem_gb": 192, "bw_gbs": 8000, "arch": "Blackwell", "bf16": True, "fp8": True},
}

GPU_ORDER = list(GPUS.keys())


def parse_gpu(spec: str | None) -> tuple[str, int]:
    """'H100:2' -> ('H100', 2). Unknown names are returned as-is with count 1."""
    if not spec:
        return "", 1
    name, _, count = spec.partition(":")
    try:
        n = int(count) if count else 1
    except ValueError:
        n = 1
    return name.strip(), max(1, n)


def match_gpu(reported: str | None) -> str | None:
    """Map an nvidia-smi style name ('NVIDIA H100 80GB HBM3') to a GPUS key."""
    if not reported:
        return None
    r = reported.upper().replace(" ", "")
    for key in ("H200", "B200", "H100", "L40S", "A10G", "L4", "T4"):
        if key in r:
            return key
    if "A100" in r:
        return "A100-80GB" if "80" in r else "A100-40GB"
    return None


def roofline_tps(params_b_active: float, gpu_key: str | None, gpu_count: int = 1, bytes_per_param: float = 2.0) -> float | None:
    """Upper bound on single-stream decode tokens/s: memory bandwidth / bytes read per token."""
    if not gpu_key or gpu_key not in GPUS or params_b_active <= 0:
        return None
    bw = GPUS[gpu_key]["bw_gbs"] * max(1, gpu_count) * 0.85  # tensor-parallel is never perfectly linear
    bytes_per_token_gb = params_b_active * bytes_per_param
    return bw / bytes_per_token_gb


def bytes_per_param(dtype: str | None, quantization: str | None = None) -> float:
    q = (quantization or "").lower()
    if q in {"fp8", "int8", "w8a8"}:
        return 1.0
    if q in {"awq", "gptq", "int4", "w4a16", "nvfp4", "mxfp4", "fp4"}:
        return 0.55
    d = (dtype or "").lower()
    if d in {"float32", "fp32"}:
        return 4.0
    return 2.0
