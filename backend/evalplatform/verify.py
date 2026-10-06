"""Offline grading of recorded model responses (used by tests and scripts/grade_recordings.py)."""
from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any

from .suite.coherence import analyze_text
from .suite.tasks import GradeCtx, Task, build_suite

_BLOCK = re.compile(r"^=== RESPONSE (\S+) ===\n(.*?)\n=== END ===\s*$", re.S | re.M)


def parse_responses(path: str | Path) -> dict[str, str]:
    text = Path(path).read_text(encoding="utf-8")
    return {m.group(1): m.group(2) for m in _BLOCK.finditer(text)}


def suite_by_id() -> dict[str, Task]:
    return {t.id: t for t in build_suite()}


async def grade_one(task: Task, text: str, finish_reason: str = "stop") -> dict[str, Any]:
    ctx = GradeCtx(finish_reason=finish_reason, completion_tokens=max(1, len(text) // 4), all_texts=[text, text])
    graded = task.grader(text, ctx)
    if inspect.isawaitable(graded):
        graded = await graded
    health = analyze_text(
        text, kind=task.text_kind, allow_repetition=task.allow_repetition, multilingual=task.multilingual,
        finish_reason=finish_reason, expect_short=task.expect_short, temperature=task.temperature,
    )
    return {
        "id": task.id, "domain": task.domain, "passed": graded.passed, "score": graded.score,
        "checks": [c.as_dict() for c in graded.checks], "health": health.as_dict(), "response": text,
    }


async def grade_recording(responses: dict[str, str]) -> list[dict[str, Any]]:
    tasks = suite_by_id()
    out = []
    for tid, text in responses.items():
        if tid in tasks:
            out.append(await grade_one(tasks[tid], text))
        elif tid.startswith("samp-"):
            from .suite.sampling import PROBES

            h = analyze_text(text, kind=PROBES[tid].kind if tid in PROBES else "prose")
            out.append({"id": tid, "domain": "sampling", "passed": not h.severe, "score": 1 - h.severity_score,
                        "checks": [], "health": h.as_dict(), "response": text})
        elif tid.startswith("bench-"):
            h = analyze_text(text, kind="prose")
            out.append({"id": tid, "domain": "coherency", "passed": not h.severe, "score": 1 - h.severity_score,
                        "checks": [], "health": h.as_dict(), "response": text})
    return out
