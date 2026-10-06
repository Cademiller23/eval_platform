"""Hyperparameter suite: statistics, derivations and the whole lab, driven by fake models whose behaviour is known exactly."""
from __future__ import annotations

import hashlib
import random
import re
import uuid

import pytest

from evalplatform.providers.base import ChatResult
from evalplatform.suite import sampling as S
from evalplatform.suite.tasks import build_suite

TASKS = {t.id: t for t in build_suite()}
BY_PROMPT = {t.messages[-1]["content"]: t for t in TASKS.values()}
PROBE_BY_PROMPT = {p.prompt: p for p in S.PROBES.values()}
ALL = {"temperature", "top_p", "top_k", "min_p", "seed", "frequency_penalty", "repetition_penalty", "stop", "max_tokens"}


def _seed(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


class FakeModel:
    """A model whose accuracy collapses above ``knee`` and that emits word salad from ``salad_at`` (effective temperature)."""

    def __init__(self, knee: float = 1.0, salad_at: float = 1.6, honor: set[str] | None = None, reject: set[str] | None = None,
                 greedy_loops: bool = False, nondeterministic: bool = False, base_acc: float = 0.97, repeats: bool = False):
        self.knee, self.salad_at, self.honor, self.reject = knee, salad_at, (ALL if honor is None else honor), reject or set()
        self.greedy_loops, self.nondeterministic, self.base_acc, self.repeats = greedy_loops, nondeterministic, base_acc, repeats
        self.calls = 0

    def p_correct(self, t: float) -> float:
        return self.base_acc if t <= self.knee else max(0.0, self.base_acc - (t - self.knee) * 1.4)

    async def ask(self, messages, *, max_tokens, temperature, extra=None) -> ChatResult:
        self.calls += 1
        extra = extra or {}
        for k in self.reject:
            if k in extra:
                return ChatResult(error=f"HTTP 400: Unsupported parameter: {k}")
        t = temperature if "temperature" in self.honor else 0.0
        if "top_k" in self.honor and extra.get("top_k") == 1:
            t = 0.0
        if "top_p" in self.honor and extra.get("top_p", 1.0) <= 0.2:
            t = 0.0
        if "min_p" in self.honor and extra.get("min_p", 0.0) >= 0.9:
            t = 0.0
        if "top_p" in self.honor and 0.2 < extra.get("top_p", 1.0) < 1.0:
            t *= 0.6 + 0.4 * extra["top_p"]
        seed = extra.get("seed") if ("seed" in self.honor and t > 0) else (uuid.uuid4().hex if t > 0 else 0)
        if t == 0 and self.nondeterministic:
            seed = uuid.uuid4().hex
        rng = random.Random(_seed(messages[-1]["content"], round(t, 3), seed, sorted((k, v) for k, v in extra.items() if k in ("frequency_penalty", "repetition_penalty"))))
        prompt = messages[-1]["content"]
        text, finish = self._text(prompt, t, extra, rng, max_tokens)
        toks = len(text.split())
        if toks > max_tokens and "max_tokens" in self.honor:
            text, finish, toks = " ".join(text.split()[:max_tokens]), "length", max_tokens
        return ChatResult(text=text, finish_reason=finish, completion_tokens=toks)

    def _text(self, prompt: str, t: float, extra: dict, rng: random.Random, max_tokens: int):
        if prompt in BY_PROMPT:
            task = BY_PROMPT[prompt]
            pen_harm = 0.35 if (extra.get("frequency_penalty", 0) >= 1.5 or extra.get("repetition_penalty", 1) >= 1.3) else 0.0
            if t >= self.salad_at:
                return self._salad(rng), "length"
            ok = rng.random() < max(0.0, self.p_correct(t) - pen_harm)
            if ok:
                return task.reference, "stop"
            return re.sub(r"Answer:.*$", "Answer: 7777", task.reference, flags=re.S) if "Answer:" in task.reference else "I am not sure.", "stop"
        probe = PROBE_BY_PROMPT[prompt]
        if probe.id == "samp-stop":
            text = "alpha beta gamma delta"
            if "stop" in self.honor and "gamma" in (extra.get("stop") or []):
                return "alpha beta ", "stop"
            return text, "stop"
        if probe.id == "samp-len":
            return " ".join(["word"] * 200), "stop"
        if probe.id == "samp-rep":
            base = probe.reference
            loops = self.repeats and not (extra.get("frequency_penalty", 0) >= 0.5 or extra.get("repetition_penalty", 1) >= 1.1)
            if loops:
                base = "This bottle is great. " * 30
            elif extra.get("frequency_penalty") or extra.get("repetition_penalty"):
                base = base.replace("This", "That", 1 + int(extra.get("frequency_penalty", 0) * 2))
            return base, "stop"
        # open-ended coherence / reproducibility prompts
        if t == 0 and self.nondeterministic and rng.random() < 0.5:
            t = 0.35                      # batch-variance style jitter at temperature 0
        if t >= self.salad_at:
            return self._salad(rng), "length"
        if t == 0 and self.greedy_loops:
            return probe.reference[:60] + (" the same words again and again" * 25), "length"
        sentences = re.split(r"(?<=[.!?])\s+", probe.reference)
        if t > 0:
            for _ in range(int(1 + t * 3)):
                i = rng.randrange(len(sentences))
                sentences[i] = rng.choice(["Moreover, ", "In fact, ", "Notably, ", "Still, ", "Then "]) + sentences[i][0].lower() + sentences[i][1:]
            if t > 0.3:
                rng.shuffle(sentences)
        return " ".join(sentences), "stop"

    @staticmethod
    def _salad(rng: random.Random) -> str:
        return " ".join("".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(3, 11))) + rng.choice(["", "ñ", "ß", "я"]) for _ in range(120))


async def run_lab(fake: FakeModel, quick: bool = False, model: dict | None = None) -> dict:
    lab = S.SamplingLab(fake.ask, model=model or {"family": "Qwen", "reasoning": False}, quick=quick, concurrency=8)
    return await lab.run()


# ------------------------------------------------------------------ statistics
def test_wilson_interval_behaves():
    lo, hi = S.wilson(0, 10)
    assert lo == 0.0 and 0.2 < hi < 0.4
    lo, hi = S.wilson(10, 10)
    assert hi == 1.0 and 0.6 < lo < 0.8
    lo, hi = S.wilson(5, 10)
    assert 0.2 < lo < 0.5 < hi < 0.8
    assert S.wilson(0, 0) == (0.0, 1.0)


def test_diff_ci_never_reports_zero_width_for_tiny_samples():
    d, lo, hi = S.diff_ci(6, 6, 6, 6)
    assert d == 0 and lo < 0 < hi
    d, lo, hi = S.diff_ci(18, 18, 6, 18)
    assert d > 0.6 and lo > 0.3


def test_diversity_and_identity():
    same = ["The cat sat on the mat today.", "the cat sat on the mat today."]
    assert S.diversity(same) == 0.0 and S.identical_fraction(same) == 1.0
    diff = ["The cat sat on the mat today and purred.", "Quantum chromodynamics describes the strong interaction between quarks."]
    assert S.diversity(diff) > 0.9 and S.identical_fraction(diff) == 0.0
    assert S.identical_fraction(["only one"]) == 1.0


def test_repetition_index_separates_loops_from_prose():
    assert S.repetition_index("This is great. " * 20) > 0.7
    assert S.repetition_index(S.STORY_REF) < 0.05


# ------------------------------------------------------------------ plan
def test_plan_sizes():
    full, quick = S.plan_for(False, False), S.plan_for(False, True)
    assert 200 <= full.requests(3) <= 300
    assert 40 <= quick.requests(2) <= 80
    assert S.plan_for(True, False).temps == [0.0, 0.6, 1.0, 1.5]       # reasoning models always get the compact grid
    assert set(S.ACC_PROBES).isdisjoint(S.VAL_PROBES)                  # tuning never sees the validation problems
    assert all(p in TASKS for p in S.ACC_PROBES + S.VAL_PROBES)


# ------------------------------------------------------------------ whole lab
@pytest.mark.asyncio
async def test_lab_finds_the_cliff_and_confirms_every_parameter():
    res = await run_lab(FakeModel(knee=1.0, salad_at=1.6))
    t = res["temperature"]
    assert t["best_t"] == 0.0 and t["honored"] == "honored"
    assert 0.8 <= t["cliff_t"] <= 1.2 and 1.2 <= t["breaks_at"] <= 1.6    # salad from 1.6 (accuracy decays from 1.0 but is noisy at n=12)
    assert t["sensitivity_label"] in ("moderate", "high")
    pts = {p["t"]: p for p in t["points"]}
    assert pts[0.0]["diversity"] == 0.0 and pts[1.2]["diversity"] > 0.05
    assert pts[2.0]["clean"] < 0.2 and pts[0.0]["clean"] == 1.0   # word salad at T=2
    assert all(c["status"] == "honored" for c in res["controls"]), res["controls"]
    assert res["determinism"]["greedy"]["identical"] == 1.0 and res["determinism"]["seed_same"]["identical"] == 1.0
    assert res["score"]["overall"] > 75
    assert set(res["profiles"]) == {"precise", "balanced", "creative"}
    assert res["profiles"]["precise"]["params"]["temperature"] == 0.0
    assert res["profiles"]["creative"]["params"]["temperature"] <= 1.2
    assert res["validation"]["arms"][0]["id"] == "api_default" and any(a["id"] == "tuned" for a in res["validation"]["arms"])
    assert any(e["label"] == "T = 2" for e in res["examples"])


@pytest.mark.asyncio
async def test_lab_detects_ignored_and_rejected_parameters():
    res = await run_lab(FakeModel(honor=ALL - {"top_k", "seed", "stop"}, reject={"repetition_penalty"}))
    st = {c["id"]: c["status"] for c in res["controls"]}
    assert st["top_k"] == "ignored" and st["seed"] == "ignored" and st["stop"] == "ignored"
    assert st["repetition_penalty"] == "rejected" and st["temperature"] == "honored" and st["top_p"] == "honored"
    kinds = " ".join(f["text"] for f in res["findings"])
    assert "top_k" in kinds and "ignored" in kinds
    assert res["score"]["controllability"] < 80


@pytest.mark.asyncio
async def test_lab_detects_an_endpoint_that_ignores_temperature():
    res = await run_lab(FakeModel(honor=ALL - {"temperature"}))
    assert res["temperature"]["honored"] == "ignored"
    assert next(c for c in res["controls"] if c["id"] == "temperature")["status"] == "ignored"


@pytest.mark.asyncio
async def test_lab_flags_degenerate_greedy_decoding_and_avoids_it():
    res = await run_lab(FakeModel(greedy_loops=True))
    t = res["temperature"]
    assert t["greedy_degenerate"] is True and t["best_t"] > 0
    assert res["profiles"]["precise"]["params"]["temperature"] > 0
    assert any("Greedy decoding" in f["text"] for f in res["findings"])


@pytest.mark.asyncio
async def test_lab_reports_non_deterministic_greedy():
    res = await run_lab(FakeModel(nondeterministic=True))
    assert res["determinism"]["greedy"]["identical"] < 1.0
    assert any("not deterministic" in f["text"] for f in res["findings"])


@pytest.mark.asyncio
async def test_penalties_fix_repetition_and_cost_accuracy_when_strong():
    res = await run_lab(FakeModel(repeats=True))
    pen = res["penalties"]
    assert pen["repetition_prone"] is True and pen["baseline"]["rep_index"] > 0.5
    assert pen["recommended"] is not None and pen["recommended"]["param"] in ("frequency_penalty", "repetition_penalty")
    assert pen["harm_at"] is not None                                   # frequency 1.5 / repetition 1.3 break the maths probes in this fake


@pytest.mark.asyncio
async def test_tuned_settings_are_only_claimed_better_when_the_interval_says_so():
    # a model that is great cold but falls apart at the API default T=1.0
    res = await run_lab(FakeModel(knee=0.4, salad_at=1.9, base_acc=0.95))
    cmp = {c["against"]: c for c in res["validation"]["comparisons"]}
    assert cmp["api_default"]["verdict"] == "improved" and cmp["api_default"]["ci"][0] > 0
    # a model that is insensitive to temperature: defaults are fine, and we say so instead of claiming a win
    res2 = await run_lab(FakeModel(knee=1.9, salad_at=2.5, base_acc=0.95))
    cmp2 = {c["against"]: c for c in res2["validation"]["comparisons"]}
    assert cmp2["api_default"]["verdict"] == "within_noise"


@pytest.mark.asyncio
async def test_quick_mode_is_small_and_still_checks_the_controls():
    fake = FakeModel()
    res = await run_lab(fake, quick=True)
    assert res["quick"] is True and fake.calls < 90
    ids = {c["id"] for c in res["controls"]}
    assert {"temperature", "top_p", "top_k", "seed", "stop", "max_tokens"} <= ids


@pytest.mark.asyncio
async def test_reasoning_models_use_the_compact_grid():
    fake = FakeModel()
    res = await run_lab(fake, model={"family": "DeepSeek", "reasoning": True})
    assert res["plan"]["temps"] == [0.0, 0.6, 1.0, 1.5] and fake.calls < 100
    assert res["guidance"]["params"]["temperature"] == 0.6


@pytest.mark.asyncio
async def test_progress_is_reported_and_reaches_total():
    seen = []
    lab = S.SamplingLab(FakeModel().ask, model={"family": "Qwen"}, quick=True, concurrency=4, progress=lambda d, t: seen.append((d, t)))
    await lab.run()
    assert seen[0][0] == 0 and seen[-1][0] == seen[-1][1] and all(d <= t for d, t in seen)
