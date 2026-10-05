"""Graders must accept every *valid* phrasing and reject every wrong answer; detectors must not cry wolf."""
import inspect

import pytest

from evalplatform.suite.coherence import analyze_text
from evalplatform.suite.tasks import GradeCtx, build_suite

T = {t.id: t for t in build_suite()}


async def passes(task_id: str, text: str, **ctx) -> bool:
    task = T[task_id]
    g = task.grader(text, GradeCtx(finish_reason=ctx.get("finish_reason", "stop"), completion_tokens=5, all_texts=[text, text]))
    g = await g if inspect.isawaitable(g) else g
    return g.passed


@pytest.mark.parametrize("text", [
    "Step 1: 250*1.2 = 300\nAnswer: 240", "**Answer:** 240", "Answer: $240.00", "Answer: **240**", "The final answer is 240 dollars.",
    "So the price is $240.\n\n\\boxed{240}", "Answer: 240.\n\nLet me know if you want another answer!", "<think>maybe 250?</think>\nAnswer: 240",
])
async def test_math_valid_phrasings_pass(text):
    assert await passes("math-percent", text)


@pytest.mark.parametrize("text", ["Answer: 250", "Answer: 24", "I think it is about 240 but let me redo it. Answer: 300", "No idea.", "Answer: 2400"])
async def test_math_wrong_answers_fail(text):
    assert not await passes("math-percent", text)


@pytest.mark.parametrize("text", ["Answer: 0.167", "Answer: 1/6", "The probability is 6/36 = 1/6 ≈ 0.1667.\nAnswer: 0.167", "Answer: 16.7%", "Answer: \\frac{1}{6}"])
async def test_probability_phrasings(text):
    assert await passes("math-dice", text)


async def test_probability_wrong():
    assert not await passes("math-dice", "Answer: 0.5")
    assert not await passes("math-dice", "Answer: 1/36")


@pytest.mark.parametrize("text,ok", [
    ("The chemical symbol for gold is Au.", True), ("Au", True), ("**Au** (from Latin aurum)", True),
    ("The symbol is Ag.", False), ("Gold is symbolised by Gd, a cause of confusion.", False), ("I believe it is AU... actually Aurum", False),
])
async def test_gold_symbol_is_word_boundary_and_case_sensitive(text, ok):
    assert await passes("gen-gold", text) is ok


@pytest.mark.parametrize("text,ok", [("Dave", True), ("The shortest is Dave.", True), ("Carol", False), ("Davenport", False)])
async def test_ordering_puzzle(text, ok):
    assert await passes("gen-order", text) is ok


@pytest.mark.parametrize("fence", ["```python", "```python3", "```py", "```Python", "```"])
async def test_code_fence_variants(fence):
    code = f"Here you go:\n\n{fence}\ndef is_palindrome(s):\n    t = [c.lower() for c in s if c.isalnum()]\n    return t == t[::-1]\n```\n\nThis ignores punctuation."
    assert await passes("code-palindrome", code)


async def test_code_unfenced_and_with_usage_block():
    plain = "def is_palindrome(s):\n    t = [c.lower() for c in s if c.isalnum()]\n    return t == t[::-1]"
    assert await passes("code-palindrome", plain)
    both = f"```python\n{plain}\n```\nExample:\n```python\nprint(is_palindrome('abba'))\n```"
    assert await passes("code-palindrome", both)


async def test_code_with_main_guard_and_prints_is_fine():
    code = "```python\ndef is_palindrome(s):\n    t = [c.lower() for c in s if c.isalnum()]\n    return t == t[::-1]\n\nif __name__ == '__main__':\n    print(is_palindrome('abba'))\n```"
    assert await passes("code-palindrome", code)


async def test_infinite_loop_and_dangerous_code_fail_safely():
    assert not await passes("code-palindrome", "```python\ndef is_palindrome(s):\n    while True:\n        pass\n```")
    assert not await passes("code-palindrome", "```python\nimport subprocess\ndef is_palindrome(s):\n    return True\n```")


@pytest.mark.parametrize("text,ok", [
    ('{"city": "Paris", "population": 2100000, "country": "France"}', True),
    ('```json\n{"city": "Paris", "population": 2161000, "country": "France"}\n```', True),
    ('{"city": "Paris", "population": "2.1 million", "country": "France"}', False),
    ('Sure! {"city": "Paris", "population": 1, "country": "France"}', False),
    ('{"city": "Lyon", "population": 500000, "country": "France"}', False),
])
async def test_json_task(text, ok):
    assert await passes("gen-json", text) is ok


@pytest.mark.parametrize("text,ok", [("Yes. Every bloop is a razzie and every razzie a lazzie.", True), ("**Yes** — by transitivity.", True), ("No, not necessarily.", False), ("It depends. Yes and no.", False)])
async def test_syllogism(text, ok):
    assert await passes("gen-logic", text) is ok


@pytest.mark.parametrize("text,ok", [("OK", True), ("OK.", True), ("Ok!", True), ("\"OK\"", True), ("Sure! OK.", False), ("OK, let me know if you need anything else.", False)])
async def test_stop_task(text, ok):
    assert await passes("coh-stop", text) is ok


async def test_stop_task_requires_stop_finish():
    assert not await passes("coh-stop", "OK", finish_reason="length")


@pytest.mark.parametrize("text,ok", [
    ("Buenos días, ¿cómo estás?", True), ("buenos dias, como estas?", True), ("Buen día, ¿cómo está usted?", True), ("Bonjour, comment ça va ?", False),
])
async def test_translation_unicode_normalisation(text, ok):
    assert await passes("gen-translate", text) is ok
    nfd = __import__("unicodedata").normalize("NFD", text)
    assert await passes("gen-translate", nfd) is ok


async def test_multilingual_unicode_normalisation():
    import unicodedata

    text = "French: Bonjour\nGerman: Hallo\nJapanese: こんにちは\nHindi: नमस्ते"
    assert await passes("coh-multilingual", text)
    assert await passes("coh-multilingual", unicodedata.normalize("NFD", text))
    assert not await passes("coh-multilingual", "French: Bonjour\nGerman: Hallo\nJapanese: Konnichiwa\nHindi: Namaste")


# ------------------------------------------------------------------------------ detectors: no false alarms
CLEAN_SAMPLES = {
    "ml_identifiers": "We served Qwen2.5-7B-Instruct on an A100-80GB with vLLM 0.11.0 and compared it against Llama-3.1-8B on an H100. Throughput was 2x higher on x86_64 hosts, and the sha256sum of the weights matched the published checksum, so we trusted the download.",
    "cjk_translation": "你好，今天天气很好。我们可以去公园散步，然后在附近的餐厅吃午饭。下午天气可能会变冷，所以请带上外套。「谢谢你的邀请」，她笑着说。",
    "markdown": "## Why sleep matters\n\n- **Memory**: sleep consolidates what you learned.\n- **Mood**: a rested brain regulates emotion better.\n- **Health**: the immune system repairs itself overnight.\n\n> Aim for 7–9 hours.\n\n| Age | Hours |\n|---|---|\n| Adult | 7–9 |\n| Teen | 8–10 |\n\nIn short, protecting your sleep protects almost everything else you care about, from focus at work to patience with the people around you.",
    "emoji": "Great question! 🎉 Here's the short version: the library is open from nine to five 📚, and the café next door 🍵 stays open until eight. Bring a laptop 💻 and a jacket 🧥 because the reading room gets chilly in the evening, and don't forget your card ✅ so you can borrow books for the whole month.",
    "latex": "The sum of squares follows from the identity $(a+b)^2 = a^2 + b^2 + 2ab$. Substituting $a+b=30$ and $ab=200$ gives $$a^2+b^2 = 30^2 - 2\\cdot 200 = 900 - 400 = 500.$$ We can double check with $\\sqrt{500} \\approx 22.36$ and the quadratic $x^2 - 30x + 200 = 0$, whose roots are $x = 10$ and $x = 20$. Indeed $10^2 + 20^2 = 500$, so the answer is consistent.\n\nAnswer: 500",
    "code_and_prose": "You can do this with a dictionary. Use `collections.defaultdict(list)` and loop once over the input:\n\n```python\nfrom collections import defaultdict\n\ndef group(words):\n    d = defaultdict(list)\n    for w in words:\n        d[''.join(sorted(w))].append(w)\n    return list(d.values())\n```\n\nThis runs in O(n·k log k) where k is the longest word, and it keeps the original order within each group, which is usually what callers expect.",
    "numbers": "Quarterly results were 1,204 units in Q1, 1,377 in Q2, 1,512 in Q3 and 1,640 in Q4. That is a total of 5,733 units, or roughly 1,433 per quarter, and growth stayed between 8% and 14% in every period, with the strongest jump arriving between the second and third quarters.",
}


@pytest.mark.parametrize("name", list(CLEAN_SAMPLES))
def test_realistic_clean_text_is_not_flagged(name):
    h = analyze_text(CLEAN_SAMPLES[name], multilingual=name == "cjk_translation")
    assert not h.severe, [i.as_dict() for i in h.issues]


def test_multilingual_text_is_checked_for_soup_but_not_for_english_words():
    import random

    rng = random.Random(5)
    soup = "".join(rng.choice("qxzkvbjwpfgd#@%0123456789ÃäÅ ") for _ in range(160))
    assert analyze_text("Buenos días, ¿cómo estás?\n" + soup, kind="short", multilingual=True).garbled
    assert not analyze_text("Bonjour, comment allez-vous aujourd'hui ? J'espère que vous passez une excellente journée.", kind="short", multilingual=True).severe


def test_bulleted_lists_with_repeated_openers_not_major():
    text = "\n".join([
        "- You can start by listing every service that touches customer data and who owns it.",
        "- You can then rank those services by blast radius, so the riskiest get reviewed first.",
        "- You can add automated scans to the build so regressions are caught before they ship.",
        "- You can rotate credentials on a schedule instead of waiting for an incident to force it.",
        "- You can write short runbooks, because nobody reads a forty page policy at three in the morning.",
        "- You can rehearse a breach twice a year and fix whatever the rehearsal exposes.",
        "- You can track a handful of metrics, such as time to patch and time to detect, and review them monthly.",
    ])
    assert not analyze_text(text).severe


def test_near_identical_templated_lines_are_flagged():
    text = "\n".join(f"- You can improve step {i} by checking the inputs, the outputs, and the logs before moving on to the next stage of the pipeline." for i in range(1, 9))
    assert analyze_text(text).repetitive


def test_json_output_is_not_judged_as_prose():
    assert not analyze_text('{"city": "Paris", "population": 2100000, "country": "France"}', kind="json").severe
