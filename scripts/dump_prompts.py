#!/usr/bin/env python3
"""Write every evaluation prompt to a Markdown file so responses from *any* real model can be collected
by hand / by another tool and then graded offline with scripts/grade_recordings.py.

Format of the output (and of the response files you produce):

    === TASK <id> ===
    [user]
    ...
    [assistant]
    ...
    === END ===
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from evalplatform.runner import BENCH_PROMPTS  # noqa: E402
from evalplatform.suite.tasks import build_suite  # noqa: E402


def main(out: str = "verification/prompts.md") -> None:
    lines: list[str] = []
    for t in build_suite():
        lines.append(f"=== TASK {t.id} ===")
        if t.id == "coh-stability":
            lines.append("(answer once; the harness normally asks twice)")
        for m in t.messages:
            lines.append(f"[{m['role']}]")
            lines.append(m["content"])
        lines.append("=== END ===\n")
    for i, p in enumerate(BENCH_PROMPTS, 1):
        lines += [f"=== TASK bench-{i} ===", "(open-ended: answer in roughly 180-200 words)", "[user]", p, "=== END ===\n"]
    Path(out).write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out} ({len(lines)} lines)")


if __name__ == "__main__":
    main(*sys.argv[1:])
