"""The evaluation suite: coding, math, general-purpose and coherency tasks.

Every task carries a programmatic grader (no LLM judge → results are reproducible and free),
plus a ``reference`` answer that doubles as (a) the demo-mode oracle and (b) a self-test that
proves each grader accepts a correct answer.
"""
from __future__ import annotations

import difflib
import json
import random
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .sandbox import run_tests
from .text import extract_code, extract_final_number, fold_accents, split_thinking, word_count


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class Grade:
    checks: list[Check]
    score: float | None = None
    note: str = ""

    def __post_init__(self):
        if self.score is None:
            self.score = (sum(c.passed for c in self.checks) / len(self.checks)) if self.checks else 0.0

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)


@dataclass
class GradeCtx:
    finish_reason: str | None = None
    completion_tokens: int = 0
    all_texts: list[str] = field(default_factory=list)


Grader = Callable[[str, GradeCtx], "Grade | Awaitable[Grade]"]


@dataclass
class Task:
    id: str
    domain: str                      # coding | math | general | coherency
    name: str
    messages: list[dict[str, str]]
    grader: Grader
    reference: str
    max_tokens: int = 512
    temperature: float = 0.0
    text_kind: str = "prose"         # prose | code | json | short | number
    allow_repetition: bool = False
    expect_short: bool = False
    multilingual: bool = False
    runs: int = 1                    # >1 → grader sees every run in ctx.all_texts
    quick: bool = False              # included in "quick" mode
    difficulty: str = "medium"
    entry: str = ""                  # coding: function/class name (demo-mode helper)
    skill: str = ""                  # short label of what this probes

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "domain": self.domain, "name": self.name, "difficulty": self.difficulty, "skill": self.skill}


def user(prompt: str) -> list[dict[str, str]]:
    return [{"role": "user", "content": prompt}]


# ======================================================================================
# MATH
# ======================================================================================
MATH_SUFFIX = " Think step by step, then finish with a final line of exactly the form `Answer: <number>`."


def math_grader(expected: float, tol: float = 0.01) -> Grader:
    def grade(text: str, ctx: GradeCtx) -> Grade:
        got = extract_final_number(text)
        if got is None:
            return Grade([Check("final answer present", False, "No number found in the answer.")], 0.0)
        ok = abs(got - expected) <= max(tol, abs(expected) * 1e-9)
        return Grade([Check("correct final answer", ok, f"expected {expected:g}, got {got:g}")])
    return grade


def _math(i: str, name: str, prompt: str, expected: float, ref_work: str, difficulty="easy", quick=False, max_tokens=700, skill="arithmetic") -> Task:
    shown = f"{expected:g}"
    return Task(
        id=f"math-{i}", domain="math", name=name, messages=user(prompt + MATH_SUFFIX),
        grader=math_grader(expected), reference=f"{ref_work}\nAnswer: {shown}", max_tokens=max_tokens,
        text_kind="prose", difficulty=difficulty, quick=quick, skill=skill,
    )


MATH_TASKS = [
    _math("change", "Shopping change", "Maria buys 12 pencils at $0.35 each and pays with a $5 bill. How many dollars of change does she receive?", 0.8,
          "12 × 0.35 = 4.20 dollars. Change = 5.00 − 4.20 = 0.80.", quick=True),
    _math("distance", "Two-leg trip", "A train travels at 60 mph for 2.5 hours and then at 80 mph for 1.5 hours. What is the total distance in miles?", 270,
          "60 × 2.5 = 150 and 80 × 1.5 = 120, so the total is 270 miles."),
    _math("gauss", "Sum of 1..50", "What is the sum of the first 50 positive integers?", 1275,
          "Using n(n+1)/2 with n = 50: 50 × 51 / 2 = 1275."),
    _math("linear", "Linear equation", "Solve for x: 3x + 7 = 2x + 19.", 12,
          "Subtract 2x from both sides: x + 7 = 19, so x = 12.", skill="algebra", quick=True),
    _math("rectangle", "Rectangle area", "A rectangle's length is 3 times its width and its perimeter is 64. What is its area?", 192,
          "Let width w. Perimeter 2(w + 3w) = 8w = 64, so w = 8 and length 24. Area = 8 × 24 = 192.", difficulty="medium", skill="algebra"),
    _math("percent", "Up then down 20%", "A $250 jacket is marked up by 20% and then the new price is reduced by 20%. What is the final price in dollars?", 240,
          "250 × 1.2 = 300, then 300 × 0.8 = 240.", difficulty="medium", skill="percentages", quick=True),
    _math("committee", "Committee choices", "In how many ways can a committee of 3 people be chosen from 8 people?", 56,
          "C(8,3) = 8·7·6 / (3·2·1) = 56.", difficulty="medium", skill="combinatorics"),
    _math("squares", "Sum of squares", "Two numbers have sum 30 and product 200. What is the sum of their squares?", 500,
          "(a+b)² = a² + b² + 2ab, so a² + b² = 900 − 400 = 500.", difficulty="hard", skill="algebra", quick=True),
    _math("remainder", "Remainder of 7^100 mod 5", "What is the remainder when 7^100 is divided by 5?", 1,
          "7 ≡ 2 (mod 5). Powers of 2 mod 5 cycle 2, 4, 3, 1 with period 4; 100 is divisible by 4, so 2^100 ≡ 1.", difficulty="hard", skill="number theory"),
    _math("dice", "Dice probability", "Two fair six-sided dice are rolled. What is the probability that the sum is 7? Give the answer as a decimal rounded to 3 places.", 0.167,
          "There are 6 ways to make 7 out of 36 outcomes, so 6/36 = 1/6 ≈ 0.167.", difficulty="medium", skill="probability"),
]
# the dice grader should tolerate fractions/rounding
MATH_TASKS[-1].grader = math_grader(1 / 6, tol=0.004)


# ======================================================================================
# CODING
# ======================================================================================
CODE_SUFFIX = " Return only the code in a single ```python code block, without explanations or example usage."


def _case(name: str, snippet: str) -> tuple[str, str]:
    return (name, snippet)


def code_grader(tests: list[tuple[str, str]]) -> Grader:
    async def grade(text: str, ctx: GradeCtx) -> Grade:
        code = extract_code(text)
        if not code:
            return Grade([Check("code block present", False, "No Python code found in the response.")], 0.0)
        res = await run_tests(code, tests)
        if res.error:
            return Grade([Check("program runs", False, res.error)], 0.0)
        checks = [Check(name, ok, detail) for name, ok, detail in res.results]
        return Grade(checks)
    return grade


def _code(i: str, name: str, entry: str, prompt: str, ref: str, tests: list[tuple[str, str]], difficulty="easy", quick=False, skill="algorithms", max_tokens=600) -> Task:
    return Task(
        id=f"code-{i}", domain="coding", name=name, messages=user(prompt + CODE_SUFFIX),
        grader=code_grader(tests), reference=f"```python\n{ref.strip()}\n```", max_tokens=max_tokens,
        text_kind="code", difficulty=difficulty, quick=quick, entry=entry, skill=skill,
    )


CODING_TASKS = [
    _code("palindrome", "Palindrome check", "is_palindrome",
          "Write a Python function `is_palindrome(s: str) -> bool` that returns True if `s` is a palindrome, considering only alphanumeric characters and ignoring case.",
          """
def is_palindrome(s: str) -> bool:
    t = [c.lower() for c in s if c.isalnum()]
    return t == t[::-1]
""",
          [_case("classic phrase", 'assert is_palindrome("A man, a plan, a canal: Panama") is True'),
           _case("non-palindrome", 'assert is_palindrome("race a car") is False'),
           _case("empty / blank", 'assert is_palindrome("") is True and is_palindrome("  ") is True'),
           _case("digits vs letters", 'assert is_palindrome("0P") is False')], quick=True, skill="strings"),
    _code("twosum", "Two sum", "two_sum",
          "Write a Python function `two_sum(nums: list[int], target: int) -> list[int]` returning the two indices `[i, j]` with `i < j` whose values add up to `target`. Exactly one solution exists. Aim for O(n) time.",
          """
def two_sum(nums, target):
    seen = {}
    for j, x in enumerate(nums):
        need = target - x
        if need in seen:
            return [seen[need], j]
        seen[x] = j
""",
          [_case("basic", 'assert sorted(two_sum([2,7,11,15], 9)) == [0,1]'),
           _case("middle pair", 'assert sorted(two_sum([3,2,4], 6)) == [1,2]'),
           _case("duplicates", 'assert sorted(two_sum([3,3], 6)) == [0,1]'),
           _case("negatives", 'assert sorted(two_sum([-1,-2,-3,-4,-5], -8)) == [2,4]')], quick=True, skill="hash maps"),
    _code("intervals", "Merge intervals", "merge_intervals",
          "Write a Python function `merge_intervals(intervals: list[list[int]]) -> list[list[int]]` that merges overlapping intervals and returns them sorted by start. Intervals that merely touch (end == next start) must also be merged. The input may be unsorted.",
          """
def merge_intervals(intervals):
    out = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out
""",
          [_case("overlap", 'assert [list(x) for x in merge_intervals([[1,3],[2,6],[8,10],[15,18]])] == [[1,6],[8,10],[15,18]]'),
           _case("touching", 'assert [list(x) for x in merge_intervals([[1,4],[4,5]])] == [[1,5]]'),
           _case("empty", 'assert merge_intervals([]) == []'),
           _case("unsorted input", 'assert [list(x) for x in merge_intervals([[5,6],[1,2],[2,3]])] == [[1,3],[5,6]]'),
           _case("contained", 'assert [list(x) for x in merge_intervals([[1,10],[2,3]])] == [[1,10]]')], difficulty="medium", skill="sorting"),
    _code("roman", "Roman numerals", "roman_to_int",
          "Write a Python function `roman_to_int(s: str) -> int` converting a Roman numeral (I, V, X, L, C, D, M, with subtractive pairs like IV and CM) to an integer.",
          """
def roman_to_int(s):
    v = {'I':1,'V':5,'X':10,'L':50,'C':100,'D':500,'M':1000}
    total = 0
    for i, c in enumerate(s):
        if i + 1 < len(s) and v[c] < v[s[i+1]]:
            total -= v[c]
        else:
            total += v[c]
    return total
""",
          [_case("simple", 'assert roman_to_int("III") == 3'),
           _case("mixed", 'assert roman_to_int("LVIII") == 58'),
           _case("subtractive", 'assert roman_to_int("IV") == 4 and roman_to_int("IX") == 9 and roman_to_int("XLII") == 42'),
           _case("large", 'assert roman_to_int("MCMXCIV") == 1994')], skill="strings"),
    _code("brackets", "Valid brackets", "valid_parentheses",
          "Write a Python function `valid_parentheses(s: str) -> bool` that returns True if the brackets `()[]{}` in `s` are balanced and correctly nested. An empty string is valid.",
          """
def valid_parentheses(s):
    pairs = {')': '(', ']': '[', '}': '{'}
    stack = []
    for c in s:
        if c in '([{':
            stack.append(c)
        elif c in pairs:
            if not stack or stack.pop() != pairs[c]:
                return False
    return not stack
""",
          [_case("simple", 'assert valid_parentheses("()") and valid_parentheses("()[]{}")'),
           _case("mismatch", 'assert not valid_parentheses("(]") and not valid_parentheses("([)]")'),
           _case("nested", 'assert valid_parentheses("{[]}")'),
           _case("empty", 'assert valid_parentheses("") is True'),
           _case("unbalanced", 'assert not valid_parentheses("((") and not valid_parentheses("]")')], skill="stacks"),
    _code("lru", "LRU cache", "LRUCache",
          "Implement a Python class `LRUCache` with `__init__(self, capacity: int)`, `get(self, key) -> int` (returns -1 if the key is missing) and `put(self, key, value) -> None`. When capacity is exceeded evict the least recently used key. Both `get` and `put` count as a use.",
          """
from collections import OrderedDict

class LRUCache:
    def __init__(self, capacity):
        self.cap = capacity
        self.d = OrderedDict()

    def get(self, key):
        if key not in self.d:
            return -1
        self.d.move_to_end(key)
        return self.d[key]

    def put(self, key, value):
        if key in self.d:
            self.d.move_to_end(key)
        self.d[key] = value
        if len(self.d) > self.cap:
            self.d.popitem(last=False)
""",
          [_case("eviction order", 'c=LRUCache(2); c.put(1,1); c.put(2,2); assert c.get(1)==1; c.put(3,3); assert c.get(2)==-1; c.put(4,4); assert c.get(1)==-1; assert c.get(3)==3; assert c.get(4)==4'),
           _case("update refreshes", 'c=LRUCache(2); c.put(1,1); c.put(2,2); c.put(1,10); c.put(3,3); assert c.get(1)==10; assert c.get(2)==-1'),
           _case("capacity one", 'c=LRUCache(1); c.put(1,1); c.put(2,2); assert c.get(1)==-1; assert c.get(2)==2')],
          difficulty="hard", skill="data structures", max_tokens=700, quick=True),
    _code("flatten", "Flatten nested lists", "flatten",
          "Write a Python function `flatten(nested)` that flattens arbitrarily nested lists into a single flat list, preserving order. Elements that are not lists (including strings) are kept as-is.",
          """
def flatten(nested):
    out = []
    for x in nested:
        if isinstance(x, list):
            out.extend(flatten(x))
        else:
            out.append(x)
    return out
""",
          [_case("deep nesting", 'assert flatten([1,[2,[3,[4]],5]]) == [1,2,3,4,5]'),
           _case("empties", 'assert flatten([]) == [] and flatten([[],[[]]]) == []'),
           _case("mixed", 'assert flatten([[1,2],[3],4]) == [1,2,3,4]'),
           _case("strings kept whole", 'assert flatten(["ab",["c"]]) == ["ab","c"]')], difficulty="medium", skill="recursion"),
    _code("kadane", "Maximum subarray", "max_subarray",
          "Write a Python function `max_subarray(nums: list[int]) -> int` returning the largest sum of any non-empty contiguous subarray.",
          """
def max_subarray(nums):
    best = cur = nums[0]
    for x in nums[1:]:
        cur = max(x, cur + x)
        best = max(best, cur)
    return best
""",
          [_case("classic", 'assert max_subarray([-2,1,-3,4,-1,2,1,-5,4]) == 6'),
           _case("single", 'assert max_subarray([1]) == 1'),
           _case("all negative", 'assert max_subarray([-3,-1,-2]) == -1'),
           _case("mostly positive", 'assert max_subarray([5,4,-1,7,8]) == 23')], difficulty="medium", skill="dynamic programming"),
    Task(
        id="code-bugfix", domain="coding", name="Fix a buggy function", entry="count_words", difficulty="easy", skill="debugging",
        messages=user("This function should count the words in a string, treating any amount of whitespace (spaces, tabs, newlines) as a single separator, and return 0 for empty or whitespace-only strings. It has a bug — fix it.\n\n```python\ndef count_words(text):\n    words = text.split(\" \")\n    return len(words)\n```" + CODE_SUFFIX),
        grader=code_grader([
            _case("simple", 'assert count_words("hello world") == 2'),
            _case("mixed whitespace", 'assert count_words("  a   b\\tc\\n") == 3'),
            _case("empty", 'assert count_words("") == 0'),
            _case("blank", 'assert count_words("   ") == 0'),
        ]),
        reference="```python\ndef count_words(text):\n    return len(text.split())\n```", max_tokens=400, text_kind="code",
    ),
]


# ======================================================================================
# GENERAL PURPOSE
# ======================================================================================
def _strip(text: str) -> str:
    return split_thinking(text)[0]


def _contains(text: str, *needles: str, any_of: bool = False) -> bool:
    low = text.lower()
    hits = [n.lower() in low for n in needles]
    return any(hits) if any_of else all(hits)


def g_bullets(text: str, ctx: GradeCtx) -> Grade:
    t = _strip(text)
    lines = [l for l in t.splitlines() if l.strip()]
    bullets = [l for l in lines if l.lstrip().startswith(("- ", "* ", "• "))]
    return Grade([
        Check("exactly 3 bullet points", len(bullets) == 3, f"found {len(bullets)}"),
        Check("no other text", len(lines) == len(bullets), f"{len(lines) - len(bullets)} extra line(s)"),
        Check("on topic (sleep)", _contains(t, "sleep", "rest", "night", "dream", any_of=True)),
    ])


def g_lowercase(text: str, ctx: GradeCtx) -> Grade:
    t = _strip(text)
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", t.strip()) if s.strip()]
    return Grade([
        Check("all lowercase", t == t.lower() and len(t) > 10, "contains capital letters" if t != t.lower() else ""),
        Check("two sentences", len(sentences) == 2, f"found {len(sentences)}"),
        Check("about the ocean", _contains(t, "ocean", "sea", "water", "wave", any_of=True)),
    ])


def g_three_lines(text: str, ctx: GradeCtx) -> Grade:
    t = _strip(text)
    lines = [l for l in t.splitlines() if l.strip()]
    return Grade([
        Check("exactly 3 lines", len(lines) == 3, f"found {len(lines)}"),
        Check("avoids the letter 'z'", "z" not in t.lower(), "contains 'z'" if "z" in t.lower() else ""),
        Check("about autumn", _contains(t, "autumn", "fall", "leaf", "leaves", "harvest", "golden", any_of=True)),
    ])


def g_json(text: str, ctx: GradeCtx) -> Grade:
    t = _strip(text).strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I).strip()
    try:
        obj = json.loads(t)
        parsed = isinstance(obj, dict)
    except Exception as e:
        return Grade([Check("valid JSON", False, str(e)[:120]), Check("required keys", False), Check("correct values", False)], 0.0)
    obj = obj if parsed else {}
    keys = all(k in obj for k in ("city", "population", "country"))
    vals = (str(obj.get("city", "")).lower() == "paris" and isinstance(obj.get("population"), int)
            and "france" in str(obj.get("country", "")).lower())
    return Grade([Check("valid JSON object", parsed), Check("required keys", keys), Check("correct types/values", vals,
                 "population must be an integer; country France")])


def g_story(text: str, ctx: GradeCtx) -> Grade:
    t = _strip(text)
    return Grade([
        Check("uses 'river'", "river" in t.lower()),
        Check("uses 'lantern'", "lantern" in t.lower()),
        Check("under 40 words", 5 <= word_count(t) <= 40, f"{word_count(t)} words"),
    ])


def contains_grader(*needles: str, any_of: bool = False, label: str = "correct answer") -> Grader:
    def grade(text: str, ctx: GradeCtx) -> Grade:
        t = _strip(text)
        return Grade([Check(label, _contains(t, *needles, any_of=any_of), f"expected {' / '.join(needles)}")])
    return grade


def g_syllogism(text: str, ctx: GradeCtx) -> Grade:
    t = _strip(text).strip().lower()
    return Grade([Check("answers Yes", re.match(r"^\W*yes\b", t) is not None),
                  Check("short explanation", 0 < len(re.findall(r"[.!?]", t)) <= 4, "keep it to one sentence of explanation")])


def g_summary(text: str, ctx: GradeCtx) -> Grade:
    t = _strip(text)
    hits = sum(k in t.lower() for k in ("solar", "cost", "renewable", "energy", "grid", "storage", "battery", "price"))
    return Grade([Check("mentions key ideas", hits >= 3, f"{hits} key terms"),
                  Check("one sentence ≤ 35 words", word_count(t) <= 35 and len(re.findall(r"[.!?](?:\s|$)", t)) <= 1, f"{word_count(t)} words")])


def g_translate(text: str, ctx: GradeCtx) -> Grade:
    t = fold_accents(_strip(text))
    return Grade([Check("correct Spanish greeting", "buenos dias" in t or "buen dia" in t, "expected 'Buenos días, ¿cómo estás?'"),
                  Check("asks how you are", "como esta" in t, "")])


HAYSTACK_SUMMARY = (
    "Renewable energy costs have fallen sharply over the last decade. Solar panels are now among the cheapest sources of new electricity, "
    "but the sun does not shine at night, so grid operators increasingly rely on battery storage to balance supply and demand. "
    "Analysts expect that falling battery prices will accelerate the shift away from fossil fuels."
)

GENERAL_TASKS = [
    Task("gen-bullets", "general", "Exactly three bullets", user("Write exactly 3 bullet points about the benefits of sleep. Each bullet must start with '- '. Output nothing except the bullets."),
         g_bullets, "- Sleep improves memory and learning.\n- Sleep strengthens the immune system.\n- Sleep stabilises mood and energy.", max_tokens=200, quick=True, skill="instruction following", difficulty="easy"),
    Task("gen-lowercase", "general", "All-lowercase, two sentences", user("In all lowercase letters and exactly two sentences, describe the ocean."),
         g_lowercase, "the ocean is a vast body of salt water that covers most of the planet. its waves and currents shape the climate and sustain countless forms of life.",
         max_tokens=150, skill="instruction following", difficulty="medium"),
    Task("gen-haiku", "general", "Three lines, no 'z'", user("Write a three-line poem about autumn. Do not use the letter 'z' anywhere."),
         g_three_lines, "Golden leaves descend\nWhispering to the cool earth\nHarvest moon rises", max_tokens=120, skill="constraint following", difficulty="medium"),
    Task("gen-json", "general", "Strict JSON output", user("Return only a JSON object (no prose, no code fences) with the keys \"city\" (string), \"population\" (integer, an approximate number) and \"country\" (string) for Paris."),
         g_json, '{"city": "Paris", "population": 2100000, "country": "France"}', max_tokens=120, text_kind="json", skill="structured output", difficulty="medium", quick=True),
    Task("gen-story", "general", "Word-limit with keywords", user("Write the opening of a short story in under 40 words that includes the words \"river\" and \"lantern\"."),
         g_story, "By the river, Mara raised her lantern and watched its light tremble across the dark water as the village slept.", max_tokens=120, skill="constraint following", difficulty="medium"),
    Task("gen-capital", "general", "Capital of Australia", user("What is the capital city of Australia? Answer in one short sentence."),
         contains_grader("canberra", label="names Canberra"), "The capital of Australia is Canberra.", max_tokens=80, expect_short=True, text_kind="short", skill="world knowledge", difficulty="easy", quick=True),
    Task("gen-author", "general", "Author of Pride and Prejudice", user("Who wrote the novel 'Pride and Prejudice'? Answer in one short sentence."),
         contains_grader("austen", label="names Jane Austen"), "Pride and Prejudice was written by Jane Austen.", max_tokens=80, expect_short=True, text_kind="short", skill="world knowledge", difficulty="easy"),
    Task("gen-gold", "general", "Chemical symbol for gold", user("What is the chemical symbol for gold? Answer in one short sentence."),
         contains_grader("au", label="symbol Au"), "The chemical symbol for gold is Au.", max_tokens=80, expect_short=True, text_kind="short", skill="science knowledge", difficulty="easy"),
    Task("gen-logic", "general", "Syllogism", user("If all bloops are razzies and all razzies are lazzies, are all bloops definitely lazzies? Begin your answer with Yes or No, then explain in one sentence."),
         g_syllogism, "Yes. Since every bloop is a razzie and every razzie is a lazzie, every bloop must be a lazzie.", max_tokens=150, skill="logical reasoning", difficulty="easy", quick=True),
    Task("gen-order", "general", "Ordering puzzle", user("Alice is taller than Bob. Bob is taller than Carol. Dave is shorter than Carol. Who is the shortest? Answer with just the name."),
         contains_grader("dave", label="Dave is shortest"), "Dave", max_tokens=100, expect_short=True, text_kind="short", skill="logical reasoning", difficulty="medium"),
    Task("gen-summary", "general", "One-sentence summary", user("Summarise the following paragraph in a single sentence of at most 30 words.\n\n" + HAYSTACK_SUMMARY),
         g_summary, "Falling renewable costs, especially solar paired with cheaper battery storage, are pushing grids away from fossil fuels.", max_tokens=150, skill="summarisation", difficulty="medium"),
    Task("gen-translate", "general", "Translate to Spanish", user("Translate into Spanish: \"Good morning, how are you?\" Reply with just the translation."),
         g_translate, "Buenos días, ¿cómo estás?", max_tokens=80, multilingual=True, expect_short=True, text_kind="short", skill="translation", difficulty="easy", quick=True),
]


# ======================================================================================
# COHERENCY
# ======================================================================================
def g_essay(text: str, ctx: GradeCtx) -> Grade:
    t = _strip(text)
    wc = word_count(t)
    paras = [p for p in re.split(r"\n\s*\n", t) if p.strip()]
    return Grade([
        Check("sensible length (150-400 words)", 150 <= wc <= 400, f"{wc} words"),
        Check("at least 3 paragraphs", len(paras) >= 3, f"{len(paras)} paragraphs"),
        Check("stays on topic", t.lower().count("biodiversity") >= 2, ""),
        Check("finishes cleanly", t.rstrip()[-1:] in ".!?\"”)" and ctx.finish_reason != "length", f"finish_reason={ctx.finish_reason}"),
    ])


def g_memory(text: str, ctx: GradeCtx) -> Grade:
    t = _strip(text).lower()
    return Grade([Check("remembers name (Priya)", "priya" in t), Check("remembers colour (teal)", "teal" in t)])


def g_needle(code: str) -> Grader:
    def grade(text: str, ctx: GradeCtx) -> Grade:
        return Grade([Check("retrieves the buried fact", code in _strip(text), f"expected {code}")])
    return grade


def g_stability(text: str, ctx: GradeCtx) -> Grade:
    a, b = (ctx.all_texts + ["", ""])[:2]
    ratio = difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0
    return Grade([Check("two temperature-0 runs agree (≥85% similar)", ratio >= 0.85, f"similarity {ratio:.0%}"),
                  Check("lists the correct primes", all(str(p) in _strip(a) for p in (2, 3, 5, 7, 11)))])


def g_multilingual(text: str, ctx: GradeCtx) -> Grade:
    t = _strip(text)
    return Grade([
        Check("French", _contains(t, "bonjour", "salut", any_of=True)),
        Check("German", _contains(t, "hallo", "guten tag", any_of=True)),
        Check("Japanese", _contains(t, "こんにちは", "ハロー", "コンニチハ", any_of=True)),
        Check("Hindi", _contains(t, "नमस्ते", "नमस्कार", any_of=True)),
    ])


def g_count(text: str, ctx: GradeCtx) -> Grade:
    t = _strip(text)
    nums = [int(x) for x in re.findall(r"\d+", t)]
    ok_prefix = 0
    for i, n in enumerate(nums, start=1):
        if n != i:
            break
        ok_prefix = i
    return Grade([Check("counts 1..60 without skipping or looping", nums == list(range(1, 61)), f"correct up to {ok_prefix}")],
                 score=min(1.0, ok_prefix / 60))


def g_ok(text: str, ctx: GradeCtx) -> Grade:
    t = _strip(text).strip(" .!\n\"'").lower()
    return Grade([Check("replies with OK", t == "ok", f"got {t[:30]!r}"),
                  Check("stops on its own", ctx.finish_reason == "stop", f"finish_reason={ctx.finish_reason}"),
                  Check("does not ramble", ctx.completion_tokens <= 20 or not ctx.completion_tokens, f"{ctx.completion_tokens} tokens")])


def build_haystack(target_tokens: int, code: str = "7391", seed: int = 7) -> str:
    rng = random.Random(seed)
    topics = [
        "The harbour authority revised the tide tables for {m} and posted them near gate {n}.",
        "Inventory report {n}: the warehouse holds {m} crates of ceramic tiles and {n} pallets of paper.",
        "During the {m} survey, volunteers counted {n} migrating birds along the eastern ridge.",
        "The committee meeting on day {n} approved a budget of {m} credits for maintenance.",
        "A traveller noted that the market in district {n} sells {m} varieties of dried fruit.",
        "Weather log {n}: light winds, clear skies, and a visibility of {m} kilometres.",
        "The librarian catalogued {m} new volumes in section {n} before closing for the evening.",
        "Engineers measured vibration level {n} on bridge segment {m} and recorded no anomalies.",
    ]
    months = ["March", "April", "June", "August", "October", "January"]
    target_chars = target_tokens * 4
    sentences: list[str] = []
    total = 0
    while total < target_chars:
        s = rng.choice(topics).format(m=rng.choice(months) if rng.random() < 0.4 else rng.randint(12, 980), n=rng.randint(2, 99))
        sentences.append(s)
        total += len(s) + 1
    pos = int(len(sentences) * 0.62)
    sentences.insert(pos, f"IMPORTANT: The vault access code is {code}. Remember it.")
    paragraphs = [" ".join(sentences[i:i + 6]) for i in range(0, len(sentences), 6)]
    return "\n\n".join(paragraphs)


def needle_task(target_tokens: int) -> Task:
    code = "7391"
    doc = build_haystack(target_tokens, code)
    return Task(
        "coh-needle", "coherency", f"Long-context recall (~{target_tokens // 1000 or 1}k tokens)",
        user(f"Read the following document carefully.\n\n{doc}\n\nQuestion: What is the vault access code? Reply with just the code."),
        g_needle(code), code, max_tokens=60, expect_short=True, text_kind="short", skill="long context", difficulty="medium", quick=True,
    )


ESSAY_REFERENCE = (
    "Biodiversity matters because every living thing is woven into the systems that keep our planet habitable. Forests filter the air we breathe, wetlands absorb floodwater, "
    "and countless insects, birds and bats pollinate the crops that feed billions of people. When many species live together, the ecosystem can absorb shocks such as drought, fire or disease; "
    "when species vanish, those protective layers grow thin and fragile.\n\n"
    "There is also a deep economic and medical case. A large share of modern medicines trace back to compounds first discovered in plants, fungi and marine organisms, and wild relatives of crops "
    "hold the genetic traits that breeders need to fight new pests and a warming climate. Every extinction closes a door on discoveries that we may never get the chance to make.\n\n"
    "Finally, biodiversity carries cultural and ethical weight. Communities around the world draw identity, recreation and meaning from the wildlife around them, and many people feel a responsibility "
    "to leave future generations a living world rather than an impoverished one. Protecting habitats, reducing pollution and restoring damaged land are practical steps that safeguard both nature and ourselves."
)

COHERENCY_TASKS = [
    Task("coh-essay", "coherency", "Long-form essay (sampled, T=0.7)", user("Write an essay of about 250 words on why biodiversity matters. Use at least three paragraphs."),
         g_essay, ESSAY_REFERENCE,
         max_tokens=700, temperature=0.7, skill="sustained generation", difficulty="medium", quick=True),
    Task("coh-memory", "coherency", "Multi-turn memory", [
        {"role": "user", "content": "Hi! My name is Priya and my favourite colour is teal."},
        {"role": "assistant", "content": "Nice to meet you, Priya! Teal is a lovely colour."},
        {"role": "user", "content": "What is 15 + 27?"},
        {"role": "assistant", "content": "15 + 27 = 42."},
        {"role": "user", "content": "Great. Now remind me: what is my name and what is my favourite colour?"},
    ], g_memory, "Your name is Priya and your favourite colour is teal.", max_tokens=100, skill="conversation state", difficulty="easy", quick=True),
    Task("coh-stability", "coherency", "Temperature-0 stability", user("List the first five prime numbers, separated by commas."),
         g_stability, "2, 3, 5, 7, 11", max_tokens=60, runs=2, expect_short=True, text_kind="short", skill="determinism", difficulty="easy"),
    Task("coh-multilingual", "coherency", "Multilingual / non-Latin text", user("Say 'hello' in French, German, Japanese and Hindi. Put each on its own line formatted as `Language: word`."),
         g_multilingual, "French: Bonjour\nGerman: Hallo\nJapanese: こんにちは\nHindi: नमस्ते", max_tokens=150, multilingual=True, text_kind="short", skill="tokenizer / unicode", difficulty="medium"),
    Task("coh-count", "coherency", "Counting without looping", user("Count from 1 to 60, separated by commas. Output only the numbers."),
         g_count, ", ".join(str(i) for i in range(1, 61)), max_tokens=300, allow_repetition=True, text_kind="short", skill="degeneration probe", difficulty="easy"),
    Task("coh-stop", "coherency", "Stops when done", user("Reply with exactly one word: OK"),
         g_ok, "OK", max_tokens=100, expect_short=True, text_kind="short", skill="EOS / chat template", difficulty="easy", quick=True),
]


def build_suite(haystack_tokens: int = 3000, max_model_len: int = 8192, quick: bool = False) -> list[Task]:
    """All tasks, optionally the quick subset. The haystack is clamped to fit the context window."""
    budget = max(500, min(haystack_tokens, int(max_model_len * 0.55)))
    coh = COHERENCY_TASKS + [needle_task(budget)]
    tasks = coh + CODING_TASKS + MATH_TASKS + GENERAL_TASKS
    if quick:
        tasks = [t for t in tasks if t.quick]
    return tasks


DOMAINS = ["coherency", "coding", "math", "general"]
