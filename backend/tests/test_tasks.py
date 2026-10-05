import inspect

import pytest

from evalplatform.suite.sandbox import run_tests
from evalplatform.suite.tasks import GradeCtx, build_suite, build_haystack
from evalplatform.suite.coherence import analyze_text

SUITE = build_suite()


async def grade(task, text, **ctx):
    g = task.grader(text, GradeCtx(**ctx))
    return await g if inspect.isawaitable(g) else g


def test_suite_shape():
    ids = [t.id for t in SUITE]
    assert len(ids) == len(set(ids))
    for d in ("coherency", "coding", "math", "general"):
        assert sum(t.domain == d for t in SUITE) >= 6
    assert 10 <= len(build_suite(quick=True)) < len(SUITE)


@pytest.mark.parametrize("task", SUITE, ids=lambda t: t.id)
async def test_reference_answer_passes_its_grader_and_is_clean(task):
    g = await grade(task, task.reference, finish_reason="stop", completion_tokens=3, all_texts=[task.reference, task.reference])
    assert g.passed, [c.as_dict() for c in g.checks]
    h = analyze_text(task.reference, kind=task.text_kind, allow_repetition=task.allow_repetition, multilingual=task.multilingual,
                     finish_reason="stop", expect_short=task.expect_short)
    assert not h.severe, [i.as_dict() for i in h.issues]


@pytest.mark.parametrize("task", [t for t in SUITE if t.domain in ("math", "coding")], ids=lambda t: t.id)
async def test_blank_answers_fail(task):
    g = await grade(task, "I don't know.")
    assert not g.passed


async def test_math_wrong_number_fails():
    t = next(t for t in SUITE if t.id == "math-gauss")
    assert not (await grade(t, "Answer: 1276")).passed
    assert (await grade(t, "…so the sum is 1,275.\nAnswer: 1275")).passed


async def test_buggy_code_fails_partially():
    t = next(t for t in SUITE if t.id == "code-palindrome")
    g = await grade(t, "```python\ndef is_palindrome(s):\n    return s == s[::-1]\n```")
    assert not g.passed and 0 < g.score < 1


async def test_sandbox_blocks_and_times_out():
    r = await run_tests("import subprocess\n", [("t", "assert True")])
    assert r.error and "Blocked" in r.error
    r = await run_tests("while True:\n    pass\n", [("t", "assert True")], timeout=2)
    assert r.timed_out
    r = await run_tests("def f(:\n", [("t", "assert True")])
    assert r.results and not r.results[0][1]


async def test_sandbox_reports_each_test():
    r = await run_tests("def f(x):\n    return x*2\n", [("ok", "assert f(2)==4"), ("bad", "assert f(2)==5")])
    assert [ok for _, ok, _ in r.results] == [True, False]


def test_haystack_contains_needle_once_and_scales():
    doc = build_haystack(1500, "7391")
    assert doc.count("7391") == 1
    assert len(build_haystack(3000)) > 1.8 * len(doc)
