import random

from evalplatform.suite.coherence import analyze_text
from evalplatform.suite.text import extract_code, extract_final_number, split_thinking

GOOD = (
    "Biodiversity matters because every species plays a role in the ecosystems that sustain human life. Forests filter air and water, "
    "wetlands buffer floods, and pollinators make a third of our food possible. When species disappear, the web of relationships that "
    "keeps these services running begins to fray, often in ways that scientists only understand after the damage is done."
)


def kinds(text, **kw):
    return {i.kind for i in analyze_text(text, **kw).issues}


def test_clean_prose_has_no_issues():
    h = analyze_text(GOOD)
    assert not h.issues and h.severity_score == 0


def test_loop_is_detected():
    assert "repetition" in kinds("The cat sat on the mat. " * 30)


def test_counting_allowed_when_repetition_ok():
    text = ", ".join(map(str, range(1, 61)))
    assert "repetition" not in kinds(text, allow_repetition=True, kind="short")


def test_random_characters_are_garble():
    rnd = random.Random(1)
    s = "".join(rnd.choice("abcdefghijklmnopqrstuvwxyz ABCDEFGHIJ!@#$%^&*0123456789") for _ in range(400))
    h = analyze_text(s)
    assert h.garbled


def test_consonant_soup_is_garble():
    rnd = random.Random(2)
    s = " ".join("".join(rnd.choice("bcdfghjklmnpqrstvwxz") for _ in range(rnd.randint(4, 9))) for _ in range(60))
    assert analyze_text(s).garbled


def test_special_token_leak():
    assert "special_token_leak" in kinds("Sure thing<|im_end|><|im_start|>user")


def test_mojibake_and_replacement():
    assert "mojibake" in kinds("It’s fine, donâ€™t worry. Itâ€™s Ã©lan")
    assert "replacement_chars" in kinds("hello w����rld")


def test_language_drift_and_multilingual_exemption():
    t = "The answer is simple and clear. 因此我们可以得出结论 这是一个很好的问题 and then more text here"
    assert "language_drift" in kinds(t)
    assert "language_drift" not in kinds(t, multilingual=True)


def test_runaway_reasoning_and_empty():
    assert "runaway_reasoning" in kinds("<think>hmm let me see", finish_reason="length")
    assert "empty_output" in kinds("   ")


def test_runaway_generation_only_for_short_tasks():
    assert "runaway_generation" in kinds("OK " * 5, finish_reason="length", expect_short=True, kind="short")
    assert "runaway_generation" not in kinds(GOOD, finish_reason="length")


def test_low_confidence_logprobs():
    assert "low_confidence" in kinds("A fine answer sentence here.", logprobs=[-4.0] * 30)
    assert "low_confidence" not in kinds("A fine answer sentence here.", logprobs=[-0.3] * 30)


def test_reasoning_trace_not_flagged_but_loop_is():
    think = " ".join(f"Step {i}: consider case {i} and verify the result carefully." for i in range(20))
    assert "repetition" not in kinds(f"<think>{think}</think>\n\n{GOOD}")
    loop = "wait let me recheck this part again " * 40
    assert "repetition" in kinds(f"<think>{loop}</think>\n\n{GOOD}")


def test_code_is_not_flagged():
    code = "```python\ndef add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n```"
    assert not analyze_text(code, kind="code").severe


def test_split_thinking_shapes():
    assert split_thinking("<think>a</think>b") == ("b", "a", False)
    assert split_thinking("a</think>b") == ("b", "a", False)
    v, t, un = split_thinking("answer<think>never ends")
    assert v == "answer" and un


def test_number_extraction():
    assert extract_final_number("so 4.2 and then\nAnswer: $0.80") == 0.8
    assert extract_final_number("the result is \\boxed{240}") == 240
    assert abs(extract_final_number("Answer: 1/6") - 1 / 6) < 1e-9
    assert extract_final_number("Answer: 1,275") == 1275
    assert extract_final_number("no digits") is None


def test_extract_code():
    assert "def f" in extract_code("Here:\n```python\ndef f():\n    pass\n```\nDone")
    assert "def f" in extract_code("```python\ndef f():\n    pass")  # truncated fence
    assert extract_code("just words") is None
