#!/usr/bin/env python3
"""Grade recorded responses (verification/raw/*.md) and print every failure with its evidence.

This is the human-review step: for each test the harness marks wrong, read the response and decide whether the
*model* was wrong or the *grader* was. Usage:  python scripts/grade_recordings.py [files...]
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from evalplatform.verify import grade_recording, parse_responses  # noqa: E402


async def main(paths: list[str]) -> None:
    for p in paths:
        res = await grade_recording(parse_responses(p))
        by_dom: dict[str, list] = {}
        for r in res:
            by_dom.setdefault(r["domain"], []).append(r)
        print(f"\n##### {Path(p).stem}: {len(res)} responses")
        for d, rs in by_dom.items():
            print(f"  {d:10s} pass {sum(r['passed'] for r in rs)}/{len(rs)}  clean {sum(not r['health']['severe'] for r in rs)}/{len(rs)}")
        for r in res:
            bad_checks = [c for c in r["checks"] if not c["passed"]]
            issues = [i for i in r["health"]["issues"] if i["severity"] != "minor"]
            if not r["passed"] or issues:
                print(f"\n  ✗ {r['id']}  score={r['score']:.2f}")
                for c in bad_checks:
                    print(f"      check failed: {c['name']} — {c['detail']}")
                for i in issues:
                    print(f"      health: {i['kind']} ({i['severity']}) — {i['detail']}")
                print("      response: " + r["response"][:300].replace("\n", "\\n"))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or sorted(str(x) for x in Path("verification/raw").glob("*.md"))))
