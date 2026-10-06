"""Demo provider: a deterministic, GPU-free simulation of a served model.

It exists so the whole platform (UI, scoring, speculative-decoding detection, recommendations)
can be explored and tested without Modal credentials. Quality, speed and failure modes are
derived from the catalog entry, and timing is *virtual* (events carry synthetic timestamps) so
a full evaluation completes in seconds while still producing realistic tokens/s numbers.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import random
import re
from typing import Any, AsyncIterator

from ..devtools import sampling_sim as sim
from ..knowledge import GPUS, bytes_per_param, parse_gpu, roofline_tps
from .base import LaunchSpec, Provider, ProgressFn, Session

# Failure modes injected for demo variety (keyed by catalog id). Custom ids containing the
# words below also trigger them, handy for trying out the detectors:  "my-org/broken-7b".
MODEL_FLAWS: dict[str, list[str]] = {
    "smollm2-1.7b": ["repeat", "leak"],
    "llama-3.2-1b": ["repeat"],
    "r1-distill-qwen-7b": ["think", "greedy_loop"],          # R1 distills are documented to loop under greedy decoding
    "r1-distill-qwen-32b": ["greedy_loop"],
    "gemma-2-9b": ["nosystem"],                              # the Gemma 2 chat template has no system role
    "qwen3-30b-a3b": ["nondet"], "deepseek-r1": ["nondet"], "glm-4.5-air": ["nondet"],   # MoE routing makes batch-variance visible at T=0
}
KEYWORD_FLAWS = {"broken": ["garble", "leak"], "garble": ["garble"], "loop": ["repeat"], "leaky": ["leak"], "think": ["think"],
                 "noseed": ["noseed"], "notopk": ["notopk"], "nondet": ["nondet"], "nosystem": ["nosystem"], "inject": ["inject"], "greedyloop": ["greedy_loop"]}

# Extra difficulty for the harder system-prompt tasks (added to the task's own difficulty offset).
SYSTEM_CATEGORY_OFFSET = {"injection": -0.08, "leakage": -0.06, "capacity": 0.0, "persistence": -0.03}
SYSTEM_TASK_OFFSET = {"sys-cap-15": -0.12, "sys-cap-25": -0.3, "sys-inj-transcript": -0.1, "sys-leak-roleplay": -0.05, "sys-leak-encode": -0.05}

SPEC_ACCEPT = {"ngram": 1.55, "eagle3": 2.7, "eagle": 2.3, "mtp": 1.85, "draft_model": 2.2, "custom": 2.0}
DOMAIN_NGRAM = {"coding": 2.4, "general": 1.5, "math": 1.35, "coherency": 1.6}

BENCH_SENTENCES = [
    "Language models begin by splitting text into tokens, which are small pieces of words that the network can embed as vectors.",
    "Each layer of the transformer lets every token look at the tokens before it and decide which ones are most relevant.",
    "During training the network repeatedly predicts the next token and is nudged toward the correct answer by gradient descent.",
    "At inference time the model produces a probability distribution over the vocabulary and one token is selected from it.",
    "Greedy decoding always picks the most likely token, while sampling introduces controlled randomness for more varied prose.",
    "A key-value cache stores intermediate results so that earlier tokens do not need to be recomputed at every step.",
    "Memory bandwidth, not raw compute, usually limits how quickly a large model can generate text for a single user.",
    "Batching several requests together amortises the cost of reading the weights and raises overall throughput on the GPU.",
    "Quantisation shrinks the weights to fewer bits, which reduces memory traffic at a small cost in numerical precision.",
    "Speculative decoding drafts several tokens cheaply and then verifies them in one pass of the larger target model.",
    "Instruction tuning teaches the model to follow directions, while preference optimisation shapes its tone and refusals.",
    "Long contexts are expensive because attention grows with the number of tokens and the cache grows linearly with length.",
    "Engineers evaluate such systems on accuracy, latency, cost and the coherence of what comes out of them under load.",
    "A harbour at dawn smells of salt and diesel, and the first boats slide out while gulls argue over the leftover bait.",
    "The lighthouse keeper wound the clock each evening, a habit his father had taught him long before the lamp was automated.",
    "Printing presses spread pamphlets, scientific tables and heated arguments across Europe faster than any scribe could copy them.",
    "Rivers carve valleys slowly, patiently moving sediment downstream until a delta fans out into the waiting sea.",
    "A good map leaves things out on purpose, trading detail for the clarity that lets a traveller choose a route.",
    "In winter the orchard is quiet, the bare branches holding nothing but frost and the memory of last summer's fruit.",
    "Trade winds once decided which ports grew rich, because sailors planned entire voyages around their steady push.",
    "The old library kept its rarest volumes in a cool room where the light was dim and the air was carefully dried.",
    "Bridges teach engineers humility, since traffic, wind and time all test assumptions that looked safe on paper.",
]

FLUFF_SENTENCES = [
    "Let me think about this carefully before committing to an answer.",
    "First I should restate the problem and identify exactly what is being asked.",
    "The key quantities here are the ones given in the statement, so I will list them.",
    "Next I can work through the details one step at a time.",
    "Wait, I should double check the arithmetic in that last step.",
    "Recomputing it gives the same value, so that part is consistent.",
    "Another way to see this is to consider a simpler special case.",
    "In that simpler case the pattern holds, which supports the approach.",
    "Now I need to make sure the final form matches what the question requested.",
    "Alright, that seems consistent with everything derived above.",
    "One more sanity check on the units and the sign of the result.",
    "Good, nothing contradicts the earlier reasoning, so I can answer.",
]


# Wall-clock pause per streamed chunk (EVAL_MOCK_PACE). Event timing is virtual; this only paces the UI demo.
def _pace() -> float:
    return float(os.environ.get("EVAL_MOCK_PACE", "0.004"))




def _u(*parts: str) -> float:
    h = hashlib.sha256("|".join(parts).encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


def _tokens(text: str) -> list[str]:
    pieces = re.findall(r"\s*\S+|\s+", text)
    out: list[str] = []
    for p in pieces:
        while len(p) > 6:
            out.append(p[:5])
            p = p[5:]
        out.append(p)
    return out


class MockSession(Session):
    def __init__(self, spec: LaunchSpec, info: dict[str, Any]):
        self.spec = spec
        self.info = info
        self.model = spec.model
        self._counters = {"drafts": 0, "draft_tokens": 0, "accepted": 0, "gen_tokens": 0}
        gpu_name, gpu_n = parse_gpu(info.get("requested_gpu"))
        self._tps = (roofline_tps(self.model["active_params_b"], gpu_name, gpu_n, self.model.get("dtype_bytes", 2.0)) or 60.0) * 0.62 * self.model.get("mock_tps_boost", 1.0)
        if self.model["params_b"] > 100:
            self._tps *= 0.18  # very large MoEs are communication-bound, far below the bandwidth roofline
        self._tps = min(self._tps, 260.0)
        self._spec = spec.speculative_config
        self._k = int((self._spec or {}).get("num_speculative_tokens", 4)) if self._spec else 0
        self._flaws = list(MODEL_FLAWS.get(self.model["id"], []))
        low = self.model["hf_repo"].lower()
        for kw, fl in KEYWORD_FLAWS.items():
            if kw in low:
                self._flaws += fl

    # ------------------------------------------------------------------ quality model
    def _success_prob(self, task) -> float:
        q = self.model.get("mock_quality", 0.6) + self.model.get("mock_bias", {}).get(task.domain, 0.0)
        q += {"easy": 0.18, "medium": 0.0, "hard": -0.22}.get(task.difficulty, 0.0)
        floor = 0.03
        if getattr(task, "domain", "") == "system":
            q += SYSTEM_CATEGORY_OFFSET.get(task.category, 0.0) + SYSTEM_TASK_OFFSET.get(task.id, 0.0)
            if "inject" in self._flaws and task.category == "injection":
                q, floor = 0.0, 0.0                              # a model that obeys injected instructions, every time
        if "garble" in self._flaws:
            q -= 0.25
        return max(floor, min(0.985, q))

    def _answer(self, task, seed: str) -> tuple[str, str | None]:
        """Return (text, forced_finish_reason)."""
        ok = _u(self.model["id"], task.id, "ok") < self._success_prob(task)
        text = task.reference if ok else self._wrong(task)
        if task.runs > 1 and not ok and _u(self.model["id"], task.id, seed) < 0.5:
            text = task.reference
        forced = None

        if self.model.get("reasoning") or "think" in self._flaws:
            n = 4500 if ("think" in self._flaws and _u(self.model["id"], task.id, "think") < 0.35) else 90
            body = " ".join(FLUFF_SENTENCES[i % len(FLUFF_SENTENCES)] + (f" (step {i // len(FLUFF_SENTENCES) + 1})" if i >= len(FLUFF_SENTENCES) else "") for i in range(max(4, n // 12)))
            text = f"<think>\n{body}\n</think>\n\n{text}"

        if "repeat" in self._flaws and task.domain in ("coherency", "general") and not task.expect_short \
                and _u(self.model["id"], task.id, "rep") < 0.55:
            cut = max(40, len(text) // 3)
            loop = " the importance of this matter is that the importance of this matter is that"
            text = text[:cut] + loop * 40
            forced = "length"
        if "garble" in self._flaws and _u(self.model["id"], task.id, "garb") < 0.7 and task.text_kind != "code":
            rng = random.Random(seed)
            noise = "".join(rng.choice("qxzkvbjwpfgd�#@%0123456789äÃ©") for _ in range(160))
            text = text[: len(text) // 2] + " " + noise + " " + " ".join(
                "".join(rng.choice("bcdfghjklmnpqrstvwxz") for _ in range(rng.randint(4, 8))) for _ in range(30))
        if "leak" in self._flaws and _u(self.model["id"], task.id, "leak") < 0.4:
            text = text + "<|im_end|><|im_start|>user\nthanks<|im_end|>"
        return text, forced

    def _wrong(self, task) -> str:
        if task.domain == "system":
            if not task.fails:
                return "I'm not sure what you mean."
            if "inject" in self._flaws and task.category == "injection":
                return task.fails[0]                             # by construction fails[0] of an injection task is the hijacked output
            return task.fails[0 if _u(self.model["id"], task.id, "which") < 0.5 else len(task.fails) - 1]
        if task.domain == "math":
            m = re.search(r"Answer:\s*([-\d.]+)", task.reference)
            try:
                v = float(m.group(1)) if m else 1.0
            except ValueError:
                v = 1.0
            wrong = v * 1.1 + 1 if v else 7
            head = task.reference.rsplit("Answer:", 1)[0]
            return f"{head}\nAnswer: {wrong:g}"
        if task.domain == "coding":
            e = task.entry or "solution"
            if e[:1].isupper():
                return f"```python\nclass {e}:\n    def __init__(self, capacity):\n        self.d = {{}}\n    def get(self, key):\n        return self.d.get(key, 0)\n    def put(self, key, value):\n        self.d[key] = value\n```"
            return f"```python\ndef {e}(*args, **kwargs):\n    return None\n```"
        if task.domain == "coherency":
            if task.id == "coh-memory":
                return "I'm sorry, I don't have access to personal information about you."
            if task.id == "coh-stop":
                return "Sure! OK. Let me know if there is anything else you would like me to help you with today."
            if task.id == "coh-count":
                return ", ".join(str(i) for i in range(1, 35)) + ", 34, 35, 34, 35"
            if task.id == "coh-needle":
                return "I could not find a specific code in the document."
            if task.id == "coh-essay":
                return "Biodiversity is important. It matters for nature.\n\nIt is good."
            if task.id == "coh-multilingual":
                return "French: Bonjour\nGerman: Hello\nJapanese: Konnichiwa\nHindi: Namaste"
            if task.id == "coh-stability":
                return "2, 3, 5, 7, 9"
        return "I'm not entirely sure, but I think the answer depends on the context."

    # ------------------------------------------------------------------ streaming
    def _sampling_answer(self, messages, temperature, extra) -> tuple[str, str | None]:
        """Behaviour for the hyperparameter suite: temperature, truncation, seeds, penalties and word salad."""
        from ..suite.sampling import PROBES
        from ..suite.tasks import build_suite

        if not hasattr(self, "_by_prompt"):
            self._by_prompt = {t.messages[-1]["content"]: t for t in build_suite()}
            self._probe_by_prompt = {p.prompt: p for p in PROBES.values()}
        ex = extra or {}
        prompt = messages[-1]["content"]
        q = self.model.get("mock_quality", 0.6)
        t_eff = sim.effective_temperature(temperature, ex)
        if "noseed" in self._flaws:
            ex = {k: v for k, v in ex.items() if k != "seed"}
        if t_eff == 0 and "nondet" in self._flaws and _u(self.model["id"], prompt, os.urandom(4).hex()) < 0.5:
            t_eff = 0.3                                         # batch-variance jitter at temperature 0
        seed = ex.get("seed") if t_eff > 0 else 0
        rng = random.Random(sim.stable_int(self.model["id"], prompt, round(t_eff, 3), seed if seed is not None else os.urandom(8).hex(), ex.get("frequency_penalty"), ex.get("repetition_penalty")))
        knee, salad_t = sim.knee_for(q), sim.salad_at(q)
        garbled = rng.random() < sim.garble_probability(t_eff, salad_t)
        if prompt in self._by_prompt:
            task = self._by_prompt[prompt]
            if garbled:
                return sim.salad(rng, 60), "length"
            base = self._success_prob(task)
            if temperature == 0 and t_eff == 0:
                ok = _u(self.model["id"], task.id, "ok") < base
            else:
                ok = rng.random() < max(0.0, base * sim.accuracy_factor(t_eff, knee) - sim.penalty_harm(ex))
            return (task.reference if ok else self._wrong(task)), None
        probe = self._probe_by_prompt.get(prompt)
        if probe is None:
            return "I'm happy to help with that.", None
        if probe.id == "samp-stop":
            return probe.reference, None
        if probe.id == "samp-len":
            return probe.reference, None
        if probe.id == "samp-rep":
            if "repeat" in self._flaws and sim.penalty_strength(ex) == (0.0, 1.0):
                return "This bottle is made of steel. " * 40, "length"
            return sim.penalised_repetition(probe.reference, ex), None
        if garbled:
            return sim.salad(rng), "length"
        if t_eff == 0 and "greedy_loop" in self._flaws:
            return probe.reference[:70] + " the same words again and again" * 30, "length"
        return sim.perturb(probe.reference, t_eff, rng), None

    async def stream(self, messages, *, max_tokens, temperature, meta=None, extra=None) -> AsyncIterator[dict[str, Any]]:
        task = (meta or {}).get("task")
        prompt_chars = sum(len(m["content"]) for m in messages)
        prompt_tokens = max(8, prompt_chars // 4)
        seed = f"{(meta or {}).get('seed', '')}"
        if "nosystem" in self._flaws and any(m["role"] == "system" for m in messages):
            yield {"error": "HTTP 400: System role not supported"}
            return
        if "notopk" in self._flaws and extra and "top_k" in extra:
            yield {"error": "HTTP 400: Unsupported parameter: top_k"}
            return
        forced_stop = False
        if (meta or {}).get("sampling"):
            text, forced = self._sampling_answer(messages, temperature, extra)
        elif task is not None:
            text, forced = self._answer(task, seed + str(temperature))
        else:  # synthetic benchmark prompts: long, varied, coherent filler
            r0 = random.Random(f"bench|{self.model['id']}|{seed}")
            pool = BENCH_SENTENCES[:]
            r0.shuffle(pool)
            text, forced = " ".join(pool + [x.replace("the", "this", 1) for x in pool[:6]]), None
        if extra and extra.get("stop"):
            text, forced_stop = sim.apply_stop(text, extra["stop"])
        pieces = _tokens(text)
        rng = random.Random(f"{self.model['id']}|{seed}|{len(pieces)}|{prompt_chars}")
        finish = "stop" if forced_stop else (forced or "stop")
        if len(pieces) > max_tokens:
            pieces = pieces[:max_tokens]
            finish = "length"

        domain = getattr(task, "domain", "general")
        accept_len = 1.0
        if self._spec:
            method = self.spec.speculative_label if self.spec.speculative_label in SPEC_ACCEPT else self._spec.get("method", "custom")
            accept_len = DOMAIN_NGRAM.get(domain, 1.5) if method == "ngram" else SPEC_ACCEPT.get(method, 2.0)
            accept_len = min(accept_len, self._k + 1)
        a = 1 - 1 / accept_len if accept_len > 1 else 0.0
        step_s = (1.0 / (self._tps * (rng.uniform(0.96, 1.04)))) * (1.12 if self._spec else 1.0)
        ttft = 0.045 + prompt_tokens * 0.00045 / max(0.5, self.model.get("mock_tps_boost", 1.0) ** 0.5) + rng.uniform(0, 0.02)
        t = ttft
        i = 0
        total = 0
        low_conf = bool(re.search(r"[�]|[bcdfghjklmnpqrstvwxz]{6,}", text))
        while i < len(pieces):
            n = 1
            if self._spec:
                while rng.random() < a and n < self._k + 1:
                    n += 1
                self._counters["drafts"] += 1
                self._counters["draft_tokens"] += self._k
                self._counters["accepted"] += n - 1
            n = min(n, len(pieces) - i)
            chunk = "".join(pieces[i:i + n])
            i += n
            total += n
            lps = [(-abs(rng.gauss(0.35, 0.3)) - 0.001) if not low_conf else -abs(rng.gauss(3.2, 1.5)) for _ in range(n)]
            if total > n:  # first chunk lands at TTFT, later ones one engine step apart
                t += step_s
            self._counters["gen_tokens"] += n
            yield {"t": t, "text": chunk, "n": n, "lp": lps}
            await asyncio.sleep(_pace())
        yield {"done": True, "finish_reason": finish, "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": total}, "total_t": t}

    async def metrics(self) -> str | None:
        c = self._counters
        lines = [
            "# HELP vllm:generation_tokens_total Number of generation tokens processed.",
            "# TYPE vllm:generation_tokens_total counter",
            f'vllm:generation_tokens_total{{model_name="eval-model"}} {c["gen_tokens"]}',
        ]
        if self._spec:
            lines += [
                "# HELP vllm:spec_decode_num_drafts_total Number of spec decoding drafts.",
                f'vllm:spec_decode_num_drafts_total{{model_name="eval-model"}} {c["drafts"]}',
                f'vllm:spec_decode_num_draft_tokens_total{{model_name="eval-model"}} {c["draft_tokens"]}',
                f'vllm:spec_decode_num_accepted_tokens_total{{model_name="eval-model"}} {c["accepted"]}',
            ]
        return "\n".join(lines) + "\n"


class MockProvider(Provider):
    name = "mock"
    label = "Demo (simulated)"

    def available(self) -> tuple[bool, str]:
        return True, "Simulated model — no GPU or credentials needed. Great for exploring the platform."

    async def start(self, spec: LaunchSpec, progress: ProgressFn) -> Session:
        gpu = spec.gpu or spec.model.get("min_gpu") or "H100"
        steps = [
            ("info", f"[demo] Requesting {gpu} container …", 0.5),
            ("info", f"[demo] Pulling vLLM image, allocating {gpu} …", 0.6),
            ("info", f"[demo] Downloading weights for {spec.model['hf_repo']} (cached after first run) …", 0.8),
            ("info", "[demo] Loading weights, capturing CUDA graphs …", 0.7),
        ]
        if spec.speculative_config:
            steps.append(("info", f"[demo] Initialising speculative decoding: {spec.speculative_config}", 0.4))
        for level, msg, delay in steps:
            progress(level, msg)
            await asyncio.sleep(delay)
        gpu_name, gpu_n = parse_gpu(gpu)
        p = spec.model["params_b"]
        info = {
            "provider": "mock",
            "engine": "vLLM (simulated)",
            "engine_version": "0.11.0-demo",
            "served_model": "eval-model",
            "model": spec.model["hf_repo"],
            "gpu": gpu_name,
            "gpu_names": [f"NVIDIA {gpu_name}"],
            "gpu_count": gpu_n,
            "requested_gpu": gpu,
            "max_model_len": spec.max_model_len,
            "dtype": spec.dtype,
            "quantization": spec.quantization,
            "speculative_config": spec.speculative_config,
            "speculative_known": True,
            "speculative_log_lines": ([f"INFO speculative config: {spec.speculative_config}"] if spec.speculative_config else []),
            "cold_start_s": round(25 + p * 1.9, 1),
            "provision_s": round(25 + p * 1.9 + 8, 1),
            "metrics_available": True,
            "command": f"vllm serve {spec.model['hf_repo']} --max-model-len {spec.max_model_len}"
                       + (f" --speculative-config '{json.dumps(spec.speculative_config)}'" if spec.speculative_config else ""),
            "simulated": True,
        }
        progress("ok", f"[demo] Model online on {gpu} (simulated).")
        return MockSession(spec, info)
