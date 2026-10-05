"""Heuristic detectors for garbled, degenerate or malformed model output.

Every detector is pure and deterministic so results are reproducible and unit-testable.
They look for the failure modes that actually show up when serving LLMs:

* corrupted text      — replacement chars, mojibake, control bytes, random-character soup
* word-level garbage  — vowel-less / consonant-run "words", very low common-word coverage
* degeneration loops  — repeated n-grams, repeated lines, tail loops, extreme compressibility
* special-token leak  — chat-template / EOS tokens showing up in the text (bad stop config)
* language drift      — stray CJK / Cyrillic / Arabic in an English answer
* runaway output      — hitting max_tokens on a short task, never-closed <think>
* low confidence      — mean token log-probability far below what coherent text shows
"""
from __future__ import annotations

import math
import re
import zlib
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .text import split_thinking

SEVERITY_WEIGHT = {"minor": 0.25, "major": 0.7, "critical": 1.0}

GARBLE_KINDS = {
    "replacement_chars", "mojibake", "control_chars", "gibberish_words", "low_word_coverage",
    "symbol_soup", "random_chars", "language_drift", "giant_token", "low_confidence",
}
REPETITION_KINDS = {"repetition", "line_loop", "tail_loop", "compressible_loop"}

STOPWORDS = set(
    """the of and to a in is that it for as with was on are by this be or from at an have not but they which you
    were has had his her their there can will one all would about more when what so if my out up who we do how than
    them these some into only other its also your me no just like time over any then now may been such our most him
    she he i said each very many those because new first two use because could people make way see after
    where before should still between both while same through much last good well even own these here said""".split()
)

SPECIAL_TOKEN_PATTERNS = [
    r"<\|(?:im_start|im_end|endoftext|eot_id|start_header_id|end_header_id|begin_of_text|end_of_text|eom_id|assistant|user|system|pad|fim_[a-z]+)\|>",
    r"</s>", r"<s>", r"<unk>", r"<pad>", r"<eos>", r"<bos>",
    r"\[/?INST\]", r"<<SYS>>", r"<start_of_turn>", r"<end_of_turn>",
    r"<\|channel\|>", r"<\|message\|>", r"<\|return\|>",
]
_SPECIAL_RE = re.compile("|".join(SPECIAL_TOKEN_PATTERNS))
_MOJIBAKE_RE = re.compile(r"Ã[\u0080-¿]|â€[™œ”“¦\u009d]|Â[ -¿]|ï¿½")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NON_LATIN_LETTER = re.compile(r"[Ѐ-ӿ֐-׿؀-ۿऀ-ॿ฀-๿぀-ヿ㐀-䶿一-鿿가-힯]")


@dataclass
class Issue:
    kind: str
    severity: str  # minor | major | critical
    detail: str
    value: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "severity": self.severity, "detail": self.detail, "value": self.value}


@dataclass
class TextHealth:
    issues: list[Issue] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def severe(self) -> bool:
        return any(i.severity in ("major", "critical") for i in self.issues)

    @property
    def garbled(self) -> bool:
        return any(i.kind in GARBLE_KINDS and i.severity in ("major", "critical") for i in self.issues)

    @property
    def repetitive(self) -> bool:
        return any(i.kind in REPETITION_KINDS and i.severity in ("major", "critical") for i in self.issues)

    @property
    def severity_score(self) -> float:
        """0 = clean, 1 = completely broken."""
        if not self.issues:
            return 0.0
        ws = sorted((SEVERITY_WEIGHT[i.severity] for i in self.issues), reverse=True)
        # worst issue dominates, extra issues add a little
        return min(1.0, ws[0] + 0.1 * sum(ws[1:3]))

    def as_dict(self) -> dict[str, Any]:
        return {
            "issues": [i.as_dict() for i in self.issues],
            "metrics": {k: round(v, 4) for k, v in self.metrics.items()},
            "severe": self.severe,
            "garbled": self.garbled,
            "repetitive": self.repetitive,
            "severity_score": round(self.severity_score, 3),
        }


def char_entropy(text: str) -> float:
    if not text:
        return 0.0
    c = Counter(text)
    n = len(text)
    return -sum((v / n) * math.log2(v / n) for v in c.values())


def ngram_dup_ratio(tokens: list[str], n: int = 4) -> float:
    if len(tokens) < n + 5:
        return 0.0
    grams = [tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]
    return 1.0 - len(set(grams)) / len(grams)


def compress_ratio(text: str) -> float:
    raw = text.encode("utf-8", "ignore")
    if len(raw) < 50:
        return 1.0
    return len(zlib.compress(raw, 6)) / len(raw)


def _bad_word(w: str) -> bool:
    if len(w) < 3 or (w.isupper() and len(w) <= 6):
        return False
    lw = w.lower()
    if not re.search(r"[aeiouy]", lw) and len(lw) >= 4:
        return True
    if re.search(r"[^aeiouy\W\d_]{6,}", lw):
        return True
    if re.search(r"(.)\1{3,}", lw):
        return True
    return False


def analyze_text(
    text: str,
    *,
    kind: str = "prose",              # prose | code | json | short | number
    allow_repetition: bool = False,
    multilingual: bool = False,
    finish_reason: str | None = None,
    expect_short: bool = False,
    logprobs: list[float] | None = None,
    temperature: float = 0.0,
    check_think: bool = True,
) -> TextHealth:
    h = TextHealth()
    visible, thinking, unterminated = split_thinking(text)
    full = text
    issues = h.issues

    # ---- emptiness / runaway reasoning ---------------------------------------------------
    if unterminated:
        issues.append(Issue("runaway_reasoning", "major", "A <think> block was never closed — the model reasoned until it ran out of tokens without answering."))
    elif not visible.strip():
        issues.append(Issue("empty_output", "critical", "The model returned no visible answer."))

    # ---- character level -----------------------------------------------------------------
    rep_chars = full.count("�")
    h.metrics["replacement_chars"] = rep_chars
    if rep_chars:
        sev = "major" if rep_chars >= 4 else "minor"
        issues.append(Issue("replacement_chars", sev, f"{rep_chars} Unicode replacement character(s) (�) — broken byte-level decoding or corrupted logits.", rep_chars))
    moj = len(_MOJIBAKE_RE.findall(full))
    if moj >= 2:
        issues.append(Issue("mojibake", "major", f"{moj} mojibake sequences (e.g. 'Ã©', 'â€™') — text was double-encoded.", moj))
    ctrl = len(_CONTROL_RE.findall(full))
    if ctrl:
        issues.append(Issue("control_chars", "major", f"{ctrl} control character(s) in output.", ctrl))
    leaks = _SPECIAL_RE.findall(full)
    if leaks:
        uniq = sorted(set(leaks))[:4]
        issues.append(Issue("special_token_leak", "critical" if len(leaks) >= 3 else "major",
                            f"Chat-template/special tokens leaked into the text: {', '.join(uniq)} — usually a wrong stop-token/EOS or chat-template config.", len(leaks)))

    body = visible + ("\n" + thinking if check_think and thinking else "")
    body = body.strip() or full
    letters = [c for c in body if c.isalpha()]
    nonlatin = len(_NON_LATIN_LETTER.findall(body))

    if len(body) >= 200 and kind == "prose":
        ent = char_entropy(body)
        h.metrics["char_entropy"] = ent
        if ent > 5.4:
            issues.append(Issue("random_chars", "major", f"Character entropy {ent:.2f} bits is far above natural language (~4.2) — output looks like random characters.", ent))

    if kind == "prose" and len(body) >= 40:
        allowed = set(".,;:!?'\"()-_/%$&@#*[]{}<>=+~`’“”—–…\n\t ")
        weird = sum(1 for c in body if not (c.isalnum() or c in allowed))
        ratio = weird / max(1, len(body))
        h.metrics["symbol_ratio"] = ratio
        if ratio > 0.15 and not multilingual:
            issues.append(Issue("symbol_soup", "major", f"{ratio:.0%} of characters are unusual symbols.", ratio))

    if not multilingual and letters and nonlatin >= 3:
        frac = nonlatin / len(letters)
        h.metrics["nonlatin_ratio"] = frac
        if frac > 0.02:
            issues.append(Issue("language_drift", "major" if frac > 0.10 else "minor",
                                f"{frac:.0%} of letters are non-Latin script in an English task (language drift).", frac))

    # ---- word level -----------------------------------------------------------------------
    if kind in ("prose", "short") and not multilingual:
        toks = re.findall(r"[A-Za-z][A-Za-z'’\-]*", body)
        long_toks = [t for t in toks if len(t) >= 3]
        if len(long_toks) >= 8:
            bad = [t for t in long_toks if _bad_word(t)]
            br = len(bad) / len(long_toks)
            h.metrics["bad_word_ratio"] = br
            if br > 0.12:
                issues.append(Issue("gibberish_words", "major", f"{br:.0%} of words are implausible (e.g. {', '.join(bad[:4])}).", br))
            elif br > 0.05:
                issues.append(Issue("gibberish_words", "minor", f"{br:.0%} of words look implausible (e.g. {', '.join(bad[:3])}).", br))
        if len(toks) >= 40:
            cov = sum(1 for t in toks if t.lower() in STOPWORDS) / len(toks)
            h.metrics["common_word_coverage"] = cov
            if cov < 0.12:
                issues.append(Issue("low_word_coverage", "major", f"Only {cov:.0%} of words are common English function words (natural prose is ~35-50%) — text is not coherent English.", cov))
            elif cov < 0.20:
                issues.append(Issue("low_word_coverage", "minor", f"Common-word coverage is low ({cov:.0%}).", cov))
        giant = [t for t in re.findall(r"\S+", body) if len(t) > 45 and not t.startswith(("http", "www", "/", "```"))]
        if giant:
            issues.append(Issue("giant_token", "minor", f"{len(giant)} unbroken string(s) over 45 chars (e.g. '{giant[0][:30]}…').", len(giant)))

    # ---- degeneration / repetition ---------------------------------------------------------
    if thinking and not allow_repetition:
        twt = re.findall(r"\w+", thinking.lower())
        tdup = ngram_dup_ratio(twt, 5)
        h.metrics["think_ngram_dup_ratio"] = tdup
        tail = thinking[-120:]
        if (len(twt) >= 60 and tdup > 0.7) or (len(thinking) >= 600 and thinking.count(tail) >= 3):
            issues.append(Issue("repetition", "major", "The reasoning trace is stuck in a loop.", tdup))
    rep_body = visible if visible else body
    if not allow_repetition and kind in ("prose", "code", "short"):
        wtoks = re.findall(r"\w+", rep_body.lower())
        dup = ngram_dup_ratio(wtoks, 4)
        h.metrics["ngram4_dup_ratio"] = dup
        hi, lo = (0.75, 0.55) if kind == "code" else (0.45, 0.25)
        if len(wtoks) >= 40:
            if dup > hi:
                issues.append(Issue("repetition", "major", f"{dup:.0%} of 4-word phrases are repeats — the model is looping.", dup))
            elif dup > lo:
                issues.append(Issue("repetition", "minor", f"{dup:.0%} of 4-word phrases are repeats.", dup))
        if len(rep_body) >= 300:
            cr = compress_ratio(rep_body)
            h.metrics["compress_ratio"] = cr
            if cr < (0.07 if kind == "code" else 0.12):
                issues.append(Issue("compressible_loop", "major", f"Output compresses to {cr:.0%} of its size — it is almost entirely repeated text.", cr))
        lines = [l.strip() for l in rep_body.splitlines() if l.strip()]
        run, best, prev = 1, 1, None
        for l in lines:
            run = run + 1 if l == prev else 1
            best = max(best, run)
            prev = l
        if best >= 4:
            issues.append(Issue("line_loop", "major", f"The same line repeats {best} times in a row.", best))
        if len(rep_body) >= 600:
            tail = rep_body[-120:]
            if rep_body.count(tail) >= 3:
                issues.append(Issue("tail_loop", "major", "The end of the output repeats itself — classic degenerate loop that only stops at max_tokens.", 3))

    # ---- runaway generation ----------------------------------------------------------------
    if finish_reason == "length" and not unterminated:
        if expect_short:
            issues.append(Issue("runaway_generation", "major", "A short-answer task hit the token limit — the model did not emit an end-of-sequence token when it should have.", None))
        else:
            issues.append(Issue("hit_token_limit", "minor", "Output was cut off at the token limit.", None))

    # ---- confidence ------------------------------------------------------------------------
    if logprobs and len(logprobs) >= 20:
        lp = [x for x in logprobs if x is not None and math.isfinite(x)]
        if lp:
            mean = sum(lp) / len(lp)
            frac_bad = sum(1 for x in lp if x < -8) / len(lp)
            h.metrics["mean_logprob"] = mean
            h.metrics["frac_logprob_below_-8"] = frac_bad
            major_t, minor_t = (-2.5, -1.6) if temperature <= 0.3 else (-3.5, -2.4)
            if mean < major_t:
                issues.append(Issue("low_confidence", "major", f"Mean token log-prob {mean:.2f} — the model is extremely uncertain about its own output (typical for numerical issues, bad quantisation or a mismatched tokenizer).", mean))
            elif mean < minor_t:
                issues.append(Issue("low_confidence", "minor", f"Mean token log-prob {mean:.2f} is lower than typical coherent output.", mean))
            elif frac_bad > 0.05:
                issues.append(Issue("low_confidence", "minor", f"{frac_bad:.0%} of sampled tokens had log-prob below -8.", frac_bad))

    return h
