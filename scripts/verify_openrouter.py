#!/usr/bin/env python3
"""Verify the evaluation suite against REAL hosted models through OpenRouter.

    export OPENROUTER_API_KEY=sk-or-...
    python scripts/verify_openrouter.py                      # default 4-model spread (strong → tiny), full suite
    python scripts/verify_openrouter.py --quick              # ~40% of the tests, cheaper/faster
    python scripts/verify_openrouter.py --models openai/gpt-4o-mini qwen/qwen-2.5-7b-instruct meta-llama/llama-3.2-1b-instruct

What it does (details in backend/evalplatform/checker.py):
  1. evaluates every model through the real pipeline
  2. runs two decoding *stress controls* (temperature 2.0 → garble, negative penalties → loops) on the best model
  3. asserts: strong ≫ weak, detectors fire on real corruption, no false alarms on strong models, honest hosted
     semantics, cost tracking (optionally: reproducibility)
  4. writes verification/openrouter/{summary.md, reports/, review/} — read review/*.md as the human check

Exit code 0 = every assertion passed. A full default run costs roughly $0.05–0.30 depending on the models.
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from evalplatform.checker import DEFAULT_MODELS, summary_markdown, verify  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS, help="OpenRouter model slugs (3-4 recommended, include one weak model)")
    ap.add_argument("--quick", action="store_true", help="use the quick subset of the suite")
    ap.add_argument("--out", default="verification/openrouter", help="output directory")
    ap.add_argument("--base-url", help="override OpenRouter base URL (e.g. the local replay server)")
    ap.add_argument("--key", help="API key (defaults to $OPENROUTER_API_KEY)")
    ap.add_argument("--stress-model", help="model used for the stress controls (default: best scorer)")
    ap.add_argument("--no-stress", action="store_true", help="skip the stress-control runs")
    ap.add_argument("--repeat", action="store_true", help="also re-run the best model to check reproducibility")
    ap.add_argument("--require-spread", action="store_true", help="fail (not warn) if strong/weak models are not clearly separated")
    args = ap.parse_args()
    if args.base_url:
        os.environ["EVAL_OPENROUTER_BASE_URL"] = args.base_url
    if not (args.key or os.environ.get("OPENROUTER_API_KEY")):
        print("error: set OPENROUTER_API_KEY (or pass --key).", file=sys.stderr)
        return 2
    res = await verify(args.models, key=args.key, quick=args.quick, out_dir=args.out, stress=not args.no_stress, stress_model=args.stress_model,
                       repeat=args.repeat, require_spread=args.require_spread)
    print("\n" + summary_markdown(res))
    print(f"\nFiles written to {args.out}/ — open review/*.md for the human check.")
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
