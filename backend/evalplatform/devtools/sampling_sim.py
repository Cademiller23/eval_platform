"""Shared, deterministic emulation of how decoding settings change a model's output.

Used by the demo (mock) provider and by the OpenRouter replay server, so that the hyperparameter suite can be exercised offline
with behaviour that matches what real models do: output varies with temperature, truncation (top-p/top-k/min-p) narrows it, a fixed
seed repeats itself, penalties change repetition, and very high temperatures dissolve into word salad.
"""
from __future__ import annotations

import hashlib
import random
import re
from typing import Any


def stable_int(*parts: Any) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


def effective_temperature(temperature: float, extra: dict[str, Any] | None) -> float:
    """Truncation samplers act like a lower temperature: fewer unlikely tokens survive."""
    ex = extra or {}
    t = float(temperature or 0.0)
    tp, tk, mp = ex.get("top_p"), ex.get("top_k"), ex.get("min_p")
    if tp is not None and tp < 1.0:
        t *= 0.0 if tp <= 0.2 else 0.35 + 0.65 * tp
    if tk is not None and tk > 0:
        t *= 0.0 if tk <= 1 else min(1.0, 0.45 + 0.55 * min(1.0, (tk - 1) / 60))
    if mp is not None and mp > 0:
        t *= 0.0 if mp >= 0.9 else 1 - 0.5 * mp
    return t


def knee_for(quality: float) -> float:
    """Temperature above which accuracy starts to fall: better models tolerate more."""
    return 0.55 + 0.75 * max(0.0, min(1.0, quality))


def salad_at(quality: float) -> float:
    """Effective temperature from which output dissolves into word salad."""
    return 1.45 + 0.35 * max(0.0, min(1.0, quality))


def accuracy_factor(t_eff: float, knee: float) -> float:
    if t_eff <= knee:
        return 1.0
    return max(0.0, 1.0 - ((t_eff - knee) / 0.95) ** 1.4)


def garble_probability(t_eff: float, salad: float) -> float:
    if t_eff < salad - 0.3:
        return 0.0
    return min(1.0, (t_eff - (salad - 0.3)) / 0.5)


def penalty_strength(extra: dict[str, Any] | None) -> tuple[float, float]:
    """(frequency/presence penalty, repetition penalty) as given."""
    ex = extra or {}
    return float(ex.get("frequency_penalty") or 0.0) + float(ex.get("presence_penalty") or 0.0), float(ex.get("repetition_penalty") or 1.0)


def penalty_harm(extra: dict[str, Any] | None) -> float:
    """Extreme penalties suppress legitimate repeats (digits, variable names) and cost accuracy."""
    fp, rp = penalty_strength(extra)
    return 0.35 if (fp >= 1.5 or rp >= 1.3) else (0.12 if (fp >= 1.0 or rp >= 1.2) else 0.0)


def salad(rng: random.Random, n_words: int = 110) -> str:
    return " ".join("".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(3, 11))) + rng.choice(["", "", "ñ", "ß", "я", "漢"]) for _ in range(n_words))


_MARKERS = ["Moreover, ", "In fact, ", "Notably, ", "Still, ", "Then, ", "Indeed, ", "At the same time, "]


def perturb(text: str, t_eff: float, rng: random.Random) -> str:
    """Sentence-level variation whose amount grows with temperature. Never touches digits, so correct answers stay correct."""
    if t_eff <= 0 or not text.strip():
        return text
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    if len(parts) < 2:
        return text
    p = min(0.9, 0.18 + 0.45 * t_eff)
    out = []
    for s in parts:
        if rng.random() < p and not re.search(r"\d|```|^\s*[-*•]", s):
            s = rng.choice(_MARKERS) + s[0].lower() + s[1:]
        out.append(s)
    for _ in range(int(t_eff * 2)):                       # swap neighbours more often when hot
        i = rng.randrange(len(out) - 1)
        if not any(re.search(r"\d|```", x) for x in out[i:i + 2]):
            out[i], out[i + 1] = out[i + 1], out[i]
    return " ".join(out)


def apply_stop(text: str, stop: Any) -> tuple[str, bool]:
    """Truncate at the earliest stop sequence like a real engine does. Returns (text, stopped)."""
    seqs = [stop] if isinstance(stop, str) else [s for s in (stop or []) if isinstance(s, str) and s]
    cut = min((text.find(s) for s in seqs if s in text), default=-1)
    return (text[:cut], True) if cut >= 0 else (text, False)


def penalised_repetition(text: str, extra: dict[str, Any] | None) -> str:
    """A repetition penalty visibly changes wording; emulate by swapping a few repeated sentence openers."""
    fp, rp = penalty_strength(extra)
    strength = fp + max(0.0, (rp - 1.0) * 5)
    if strength <= 0:
        return text
    return text.replace("This", "That", 1 + int(strength * 2))
