"""Small text helpers shared by graders and detectors."""
from __future__ import annotations

import re
from fractions import Fraction

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)


def split_thinking(text: str) -> tuple[str, str, bool]:
    """Return (visible_answer, thinking, unterminated).

    Handles three shapes: ``<think>..</think>answer``; templates that pre-fill ``<think>`` so
    only ``..</think>answer`` is generated; and a never-closed ``<think>`` (runaway reasoning).
    """
    if not text:
        return "", "", False
    low = text.lower()
    thinking_parts = re.findall(r"<think>(.*?)</think>", text, flags=re.S | re.I)
    visible = _THINK_BLOCK.sub("", text)
    unterminated = False
    if "<think>" in visible.lower():
        i = visible.lower().index("<think>")
        thinking_parts.append(visible[i + 7:])
        visible = visible[:i]
        unterminated = True
    elif "</think>" in low and "<think>" not in low:
        i = low.index("</think>")
        thinking_parts.append(text[:i])
        visible = text[i + 8:]
    return visible.strip(), "\n".join(thinking_parts).strip(), unterminated


_CODE_FENCE = re.compile(r"```[A-Za-z0-9_+.\-]*[ \t]*\n(.*?)```", re.S)
_OPEN_FENCE = re.compile(r"```[A-Za-z0-9_+.\-]*[ \t]*\n(.*)$", re.S)


def extract_code(text: str) -> str | None:
    visible, _, _ = split_thinking(text)
    blocks = _CODE_FENCE.findall(visible)
    if not blocks:
        m = _OPEN_FENCE.search(visible)  # unterminated fence (truncated output)
        if m:
            blocks = [m.group(1)]
    if blocks:
        defs = [b for b in blocks if re.search(r"^\s*(def|class)\s", b, re.M)]
        return max(defs or blocks, key=len).strip("\n")
    if re.search(r"^\s*(def|class)\s", visible, re.M):
        return visible
    return None


_NUM = r"-?\$?\s?\d[\d,]*(?:\.\d+)?(?:\s*/\s*\d+)?%?"
_NUM_RE = re.compile(_NUM)


def _to_number(tok: str) -> float | None:
    t = tok.replace("$", "").replace(",", "").replace("%", "").replace(" ", "")
    try:
        if "/" in t:
            return float(Fraction(t))
        return float(t)
    except (ValueError, ZeroDivisionError):
        return None


def extract_final_number(text: str) -> float | None:
    visible, _, _ = split_thinking(text)
    if not visible:
        return None
    m = re.findall(r"\\boxed\{([^}]*)\}", visible)
    if m:
        frac = re.search(r"\\frac\{(\d+)\}\{(\d+)\}", m[-1])
        if frac:
            return int(frac.group(1)) / int(frac.group(2))
        n = _NUM_RE.search(m[-1])
        if n:
            return _to_number(n.group(0))
    # Walk "answer" mentions from the last to the first until one yields a number on the same line; a trailing
    # "let me know if you want another answer" must not hide the real final answer, and "Answer: x = 12" must
    # return 12, not a number from the working that follows.
    for m_ans in reversed(list(re.finditer(r"answer\s*(?:is)?\s*[:=]?\s*\**", visible, flags=re.I))):
        rest = visible[m_ans.end():].split("\n", 1)[0][:100]
        frac = re.search(r"\\frac\{(\d+)\}\{(\d+)\}", rest)
        num = _NUM_RE.search(rest)
        if frac and (not num or frac.start() <= num.start()):
            return int(frac.group(1)) / int(frac.group(2))
        if num:
            v = _to_number(num.group(0))
            if v is not None:
                return v
    nums = _NUM_RE.findall(visible)
    for tok in reversed(nums):
        v = _to_number(tok)
        if v is not None:
            return v
    return None


def words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z'\-]*", text)


def word_count(text: str) -> int:
    return len(text.split())


def fold_accents(text: str) -> str:
    """Lower-case and strip diacritics so 'Buenos días' == 'buenos dias' regardless of normalisation form."""
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def nfc(text: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFC", text)


_LATEX = re.compile(r"\$\$.*?\$\$|\\\[.*?\\\]|\\\(.*?\\\)|(?<![\w$])\$(?=[^\s$])[^$\n]{1,200}?(?<=[^\s$])\$(?![\w$])", re.S)
_FENCED = re.compile(r"```.*?(?:```|$)", re.S)
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_URL = re.compile(r"https?://\S+|www\.\S+")


def strip_markup(text: str) -> str:
    """Remove code, LaTeX and URLs so prose heuristics only judge the natural-language part."""
    t = _FENCED.sub(" ", text)
    t = _INLINE_CODE.sub(" ", t)
    t = _LATEX.sub(" ", t)
    return _URL.sub(" ", t)
