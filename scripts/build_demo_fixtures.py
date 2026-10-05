#!/usr/bin/env python
"""Record the platform's own output as fixtures for the offline UI preview (``npm run build:demo``).

Nothing here is hand-written: every run is produced by the real ``Runner`` (suite, graders, detectors, scoring,
recommendation engine) and its event stream is captured exactly as the live server would emit it.

* **Demo provider** – every catalogued model, on its recommended GPU, as served (baseline) and with every
  speculative-decoding recipe the catalogue has for it. The demo provider simulates the model, so these
  runs are labelled "Demo" in the interface.
* **OpenRouter replay** – the real answers four Claude models gave to every evaluation prompt (verification/raw),
  streamed through the OpenRouter code path by the replay server, plus an emulated weak model and the
  "detector self-check" runs (garble / loop), so the report shows the detectors firing on real text.

Usage:  python scripts/build_demo_fixtures.py            # writes frontend/src/demo/fixtures.json.gz
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("EVAL_MOCK_PACE", "0.0015")          # wall-clock pause per mock chunk; event timing is virtual anyway
os.environ.setdefault("EVAL_DATA_DIR", str(ROOT / "data"))

OUT = ROOT / "frontend" / "src" / "demo" / "fixtures.json.gz"
SPEC_MODES = ("ngram", "eagle3", "mtp", "draft_model")
OR_MODELS = ("haiku", "sonnet", "opus", "fable", "tiny")
OR_STRESS = {"haiku": ("garble", "loop"), "sonnet": ("garble",)}

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_replay_server(port: int) -> None:
    import uvicorn

    from evalplatform.devtools.replay_server import build_app

    cfg = uvicorn.Config(build_app(speed=1.0), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("replay server did not start")


async def record(options: dict, label: str) -> dict:
    """Run one evaluation and capture its event stream (relative timestamps in ms) and report."""
    from evalplatform.runner import PHASES, Runner

    t0 = time.monotonic()
    events: list[tuple[int, dict]] = []
    runner = Runner(uuid.uuid4().hex[:12], options, lambda ev: events.append((int((time.monotonic() - t0) * 1000), ev)))
    report = await runner.run()
    total = int((time.monotonic() - t0) * 1000)

    phases = [{"id": pid, "title": title, "status": "pending", "detail": ""} for pid, title in PHASES]
    environment = None
    slim: list[list] = []
    n_test = 0
    for t, ev in events:
        typ = ev["type"]
        if typ == "report":
            continue
        ev = {k: v for k, v in ev.items() if k != "ts"}
        if typ == "phase":
            for p in phases:
                if p["id"] == ev["id"]:
                    p["status"], p["detail"] = ev["status"], ev.get("detail", "")
        elif typ == "environment":
            environment = ev["environment"]
        elif typ == "test":
            same = n_test < len(report["tests"]) and ev["test"] == report["tests"][n_test]
            ev = {"type": "test", "i": n_test} if same else ev      # the report already holds the identical test: store a pointer
            n_test += 1
        slim.append([t, ev])

    from evalplatform.report_md import to_markdown

    markdown = to_markdown({**report, "generated_at": "@@GENERATED@@"})
    model = {k: runner.model.get(k) for k in ("id", "name", "family", "hf_repo", "params_b", "reasoning", "tags")}
    print(f"  ✓ {label:44s} {report['scores']['overall']:5.1f}  {report['verdict']['label']:9s} {report['performance']['decode_tps_median'] or 0:7.1f} tok/s  "
          f"spec={report['speculative']['status']:12s} {total / 1000:5.1f}s  {len(slim)} events", flush=True)
    return {"options": options, "model": model, "phases": phases, "environment": environment, "events": slim, "report": report, "markdown": markdown, "ms": total}


def sanitize(obj):
    """Drop anything environment-specific (loopback URLs, keys, absolute paths) from recorded data."""
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items() if k not in ("openrouter_key", "endpoint", "api_key", "generated_at")}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, str) and "127.0.0.1" in obj:
        return obj.replace("http://127.0.0.1:", "http://replay:")
    return obj


async def main(args: argparse.Namespace) -> None:
    from fastapi.testclient import TestClient

    from evalplatform import catalog
    from evalplatform.api import create_app
    from evalplatform.providers import openrouter_provider as orp
    from evalplatform.runner import resolve_speculative

    port = free_port()
    start_replay_server(port)
    os.environ["EVAL_OPENROUTER_BASE_URL"] = f"http://127.0.0.1:{port}/api/v1"
    os.environ["OPENROUTER_API_KEY"] = "test-key"
    or_models, _ = await orp.fetch_models(force=True)

    client = TestClient(create_app())
    config = client.get("/api/config").json()
    models = client.get("/api/models").json()

    jobs: list[tuple[str, dict]] = []     # (key, options)
    errors: dict[str, str] = {}
    for m in catalog.list_models():
        if args.only and args.only not in m["id"]:
            continue
        jobs.append((f"mock|{m['id']}|auto", {"model_id": m["id"], "provider": "mock", "speculative": "auto"}))
        for mode in SPEC_MODES:
            try:
                resolve_speculative(m, mode, None)
            except ValueError as e:
                errors[f"mock|{m['id']}|{mode}"] = str(e)
                continue
            jobs.append((f"mock|{m['id']}|{mode}", {"model_id": m["id"], "provider": "mock", "speculative": mode}))
    for name in OR_MODELS:
        if args.only and args.only not in name:
            continue
        base = {"provider": "openrouter", "openrouter_model": f"replay/{name}", "speculative": "auto"}
        jobs.append((f"openrouter|replay/{name}|", base))
        for st in OR_STRESS.get(name, ()):
            jobs.append((f"openrouter|replay/{name}|{st}", {**base, "stress": st}))
    if args.limit:
        jobs = jobs[: args.limit]

    print(f"Recording {len(jobs)} runs …", flush=True)
    sem = asyncio.Semaphore(args.parallel)
    runs: dict[str, dict] = {}

    async def one(key: str, options: dict) -> None:
        async with sem:
            try:
                runs[key] = await record(options, key)
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ {key}: {type(e).__name__}: {e}", flush=True)

    await asyncio.gather(*[one(k, o) for k, o in jobs])

    out_runs = []
    for key, _ in jobs:
        if key not in runs:
            continue
        r = runs[key]
        out_runs.append({"key": key, **r})

    or_out = [{**m, "vendor": "Recorded answers", "hf_id": None} for m in or_models if m["id"].startswith("replay/") and ":" not in m["id"]]
    fixtures = sanitize({
        "built_with": config["version"],
        "config": config,
        "models": models,
        "openrouter": {"live": True, "count": len(or_out), "featured": [m["id"] for m in or_out], "models": or_out},
        "errors": errors,
        "runs": out_runs,
    })
    raw = json.dumps(fixtures, separators=(",", ":"), ensure_ascii=False).encode()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(gzip.compress(raw, 9, mtime=0))
    print(f"\n{len(out_runs)} runs · {len(raw) / 1e6:.2f} MB JSON → {OUT.stat().st_size / 1e6:.2f} MB gzip → {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--only", default="", help="substring filter on model id (for quick tries)")
    ap.add_argument("--limit", type=int, default=0)
    asyncio.run(main(ap.parse_args()))
