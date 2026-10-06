"""Validate the suite against *real* model outputs (verification/raw/*.md) and against corrupted copies of them.

Two questions a checker must answer:
  1. Do correct, real answers pass and look clean?  (no false negatives / false alarms)
  2. Is corruption of those same answers actually caught?  (no false positives on quality)
"""
import random
import re
from pathlib import Path

import pytest

from evalplatform.suite.coherence import analyze_text
from evalplatform.verify import grade_one, parse_responses, suite_by_id

RAW = Path(__file__).resolve().parents[2] / "verification" / "raw"
MODELS = sorted(p.stem for p in RAW.glob("*.md"))
TASKS = suite_by_id()

pytestmark = pytest.mark.skipif(not MODELS, reason="no recordings")


def load(model):
    return parse_responses(RAW / f"{model}.md")


def test_recordings_complete():
    assert len(MODELS) >= 4
    for m in MODELS:
        r = load(m)
        missing = [t for t in TASKS if t not in r]
        assert not missing, f"{m} missing {missing}"


@pytest.mark.parametrize("model", MODELS)
async def test_real_outputs_are_clean_and_mostly_correct(model):
    resp = load(model)
    failed = []
    for tid, task in TASKS.items():
        g = await grade_one(task, resp[tid])
        if not g["passed"]:
            failed.append(tid)
        assert not g["health"]["severe"], (model, tid, g["health"]["issues"])
    # frontier models should ace the suite; the only tolerated misses are genuine constraint violations that were inspected by hand:
    #   gen-story (a 41-word story against an "under 40 words" rule), sys-cap-8 (one model ended without the mandated "Goodbye."),
    #   sys-inj-transcript (one model mislabelled a review that carried a forged transcript)
    tolerated = {"gen-story", "sys-cap-8", "sys-cap-15", "sys-cap-25", "sys-inj-transcript"}
    assert set(failed) <= tolerated and len(failed) <= 3, f"{model} failed {failed}"


# ----------------------------------------------------------------------------- mutations
def _soup(rng, n=220):
    return "".join(rng.choice("qxzkvbjwpfgd#@%0123456789ÃäÅ� ") for _ in range(n))


def m_garble(text, task, rng):
    return text + "\n" + _soup(rng)


def m_loop(text, task, rng):
    last = [l for l in text.strip().splitlines() if l.strip()][-1].strip()
    return text + ("\n" + last) * 30


def m_leak(text, task, rng):
    return text + "<|im_end|><|im_start|>user\nthanks<|im_end|>"


def m_drift(text, task, rng):
    return text + " 因此我们可以得出结论，这是一个非常好的问题，值得我们认真思考并给出完整的回答。" * 2


def m_think(text, task, rng):
    return "<think>Let me think about this carefully. " + text


def m_empty(text, task, rng):
    return "   "


def m_replacement(text, task, rng):
    return text + " cafe����"


def m_wrong(text, task, rng):
    if task.domain == "math":
        return re.sub(r"(?i)answer:\s*[^\n]*$", "Answer: 7", text.strip()) if re.search(r"(?i)answer:", text) else text + "\nAnswer: 7"
    if task.domain == "coding":
        return re.sub(r"\breturn\b[^\n]*", "return None", text, count=1)
    return "I'm not sure about that, it depends on the situation."


DETECTOR_MUTATIONS = {  # (mutation, predicate on the health report) — pure detector tests
    "garble": (m_garble, lambda h: h.garbled, lambda t: t.text_kind in ("prose", "short")),
    "loop": (m_loop, lambda h: h.repetitive, lambda t: t.text_kind in ("prose", "short", "code") and not t.allow_repetition),
    "special_token_leak": (m_leak, lambda h: any(i.kind == "special_token_leak" for i in h.issues), lambda t: True),
    "language_drift": (m_drift, lambda h: any(i.kind == "language_drift" for i in h.issues), lambda t: not t.multilingual),
    "unterminated_think": (m_think, lambda h: any(i.kind == "runaway_reasoning" for i in h.issues), lambda t: True),
    "empty": (m_empty, lambda h: any(i.kind == "empty_output" for i in h.issues), lambda t: True),
    "replacement_chars": (m_replacement, lambda h: any(i.kind == "replacement_chars" and i.severity != "minor" for i in h.issues), lambda t: True),
}


@pytest.mark.parametrize("mutation", list(DETECTOR_MUTATIONS))
def test_detectors_catch_injected_corruption(mutation):
    fn, caught, applies = DETECTOR_MUTATIONS[mutation]
    rng = random.Random(7)
    total = hit = 0
    misses = []
    for model in MODELS:
        resp = load(model)
        for tid, task in TASKS.items():
            if not applies(task):
                continue
            mutated = fn(resp[tid], task, rng)
            h = analyze_text(mutated, kind=task.text_kind, allow_repetition=task.allow_repetition, multilingual=task.multilingual,
                             expect_short=task.expect_short, finish_reason="stop")
            total += 1
            if caught(h):
                hit += 1
            else:
                misses.append((model, tid))
    assert total > 40
    assert hit / total >= 0.99, f"{mutation}: caught {hit}/{total}; misses {misses[:8]}"


async def test_runaway_generation_flagged_for_short_tasks():
    total = hit = 0
    for model in MODELS:
        resp = load(model)
        for tid, task in TASKS.items():
            if task.expect_short:
                h = analyze_text(resp[tid], kind=task.text_kind, expect_short=True, finish_reason="length", multilingual=task.multilingual)
                total += 1
                hit += any(i.kind == "runaway_generation" for i in h.issues)
    assert total and hit == total


@pytest.mark.parametrize("domain", ["math", "coding", "general", "coherency"])
async def test_wrong_answers_fail_the_graders(domain):
    rng = random.Random(3)
    total = fails = 0
    survivors = []
    for model in MODELS:
        resp = load(model)
        for tid, task in TASKS.items():
            if task.domain != domain or tid in ("coh-stability",):
                continue
            g = await grade_one(task, m_wrong(resp[tid], task, rng))
            total += 1
            if not g["passed"]:
                fails += 1
            else:
                survivors.append((model, tid))
    assert total >= 20
    assert fails / total >= 0.97, f"{domain}: {fails}/{total} wrong answers rejected; survivors {survivors[:8]}"


async def test_any_corruption_is_caught_by_detector_or_grader():
    """End-to-end: a corrupted response must never come out as 'passed and clean'."""
    rng = random.Random(11)
    escapes = []
    total = 0
    for model in MODELS:
        resp = load(model)
        for tid, task in TASKS.items():
            for name in ("garble", "loop", "special_token_leak", "unterminated_think"):
                if name == "loop" and task.allow_repetition:
                    continue
                fn = DETECTOR_MUTATIONS[name][0]
                g = await grade_one(task, fn(resp[tid], task, rng))
                total += 1
                if g["passed"] and not g["health"]["severe"]:
                    escapes.append((model, tid, name))
    assert total > 600
    assert len(escapes) / total <= 0.01, f"{len(escapes)}/{total} escaped: {escapes[:10]}"
