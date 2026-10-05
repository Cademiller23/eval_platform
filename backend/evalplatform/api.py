"""FastAPI application: REST + SSE API and the static frontend."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import __version__, catalog
from .config import get_settings, hf_token, modal_credentials_present, modal_sdk_installed
from .knowledge import GPUS
from .manager import RunManager
from .providers import list_providers
from .providers import openrouter_provider as orp
from .report_md import to_markdown
from .suite.tasks import build_suite

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


class CustomModel(BaseModel):
    hf_repo: str = Field(min_length=3, pattern=r"^[\w.\-]+/[\w.\-]+$")
    params_b: float | None = Field(default=None, gt=0, lt=3000)
    reasoning: bool | None = None


class Endpoint(BaseModel):
    base_url: str
    api_key: str | None = None
    model: str | None = None


class RunRequest(BaseModel):
    model_id: str = ""
    custom_model: CustomModel | None = None
    provider: Literal["modal", "openrouter", "openai", "mock"] | None = None
    openrouter_model: str | None = Field(default=None, max_length=200, pattern=r"^[\w.\-]+/[\w.\-:~]+$")
    openrouter_key: str | None = Field(default=None, max_length=300)
    stress: Literal["garble", "loop"] | None = None
    gpu: str | None = None
    speculative: Literal["auto", "none", "ngram", "eagle3", "mtp", "draft_model", "custom"] = "auto"
    speculative_custom: str | None = None
    quick: bool = False
    max_model_len: int | None = Field(default=None, ge=1024, le=262144)
    dtype: str | None = None
    quantization: str | None = None
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    endpoint: Endpoint | None = None
    parent_run_id: str | None = None


def default_provider() -> str:
    s = get_settings()
    if s.default_provider:
        return s.default_provider
    return "modal" if (modal_sdk_installed() and modal_credentials_present()) else "mock"


def create_app() -> FastAPI:
    app = FastAPI(title="Model Evaluation Platform", version=__version__)
    # The UI is served from the same origin, so CORS stays off by default — an open policy would let any
    # website you visit start GPU runs on your Modal account. Opt in explicitly for a separate frontend.
    origins = [o.strip() for o in os.environ.get("EVAL_CORS_ORIGINS", "").split(",") if o.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])
    manager = RunManager()
    app.state.manager = manager

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        provs = list_providers()
        return {
            "version": __version__,
            "default_provider": default_provider(),
            "providers": provs,
            "gpus": [{"id": k, **v} for k, v in GPUS.items()],
            "hf_token_set": bool(hf_token()),
            "openrouter_key_set": bool(orp.api_key()),
            "speculative_modes": [
                {"id": "auto", "label": "Baseline (as served)", "hint": "No speculative decoding — measures the model as-is."},
                {"id": "ngram", "label": "N-gram (prompt lookup)", "hint": "Works for every model."},
                {"id": "eagle3", "label": "EAGLE-3 head", "hint": "Needs a catalogued EAGLE-3 head."},
                {"id": "mtp", "label": "Native MTP", "hint": "Models with built-in multi-token prediction."},
                {"id": "draft_model", "label": "Draft model", "hint": "Small sibling model as the drafter."},
                {"id": "custom", "label": "Custom JSON", "hint": "Your own --speculative-config."},
            ],
            "suite": {"full": len(build_suite()), "quick": len(build_suite(quick=True))},
        }

    @app.get("/api/models")
    def models() -> list[dict[str, Any]]:
        out = []
        for m in catalog.list_models():
            d = {k: m[k] for k in ("id", "name", "family", "hf_repo", "params_b", "active_params_b", "context", "min_gpu", "gated", "reasoning", "tags", "description", "mtp_native")}
            d["speculators"] = [sp["method"] for sp in m.get("speculators", [])] + (["mtp"] if m.get("mtp_native") else [])
            out.append(d)
        return out

    @app.get("/api/openrouter/models")
    async def openrouter_models(refresh: bool = False) -> dict[str, Any]:
        models, live = await orp.fetch_models(force=refresh)
        featured = {f["id"] for f in orp.FEATURED}
        return {"live": live, "count": len(models), "featured": [f["id"] for f in orp.FEATURED if any(m["id"] == f["id"] for m in models)] or sorted(featured),
                "models": models}

    @app.get("/api/openrouter/status")
    async def openrouter_status() -> dict[str, Any]:
        """Is a key configured, and does OpenRouter accept it? (never returns the key itself)"""
        import httpx

        key = orp.api_key()
        if not key:
            return {"configured": False, "valid": None}
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(f"{orp.base_url()}/key", headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as e:
            return {"configured": True, "valid": None, "error": f"Cannot reach OpenRouter: {e}"}
        if r.status_code != 200:
            return {"configured": True, "valid": False, "error": f"HTTP {r.status_code}"}
        d = (r.json() or {}).get("data") or {}
        limit, usage = d.get("limit"), d.get("usage")
        return {"configured": True, "valid": True, "free_tier": d.get("is_free_tier"), "usage": usage, "limit": limit,
                "remaining": (limit - usage) if (limit is not None and usage is not None) else None}

    @app.get("/api/tasks")
    def tasks() -> list[dict[str, Any]]:
        return [t.public() for t in build_suite()]

    @app.post("/api/runs", status_code=201)
    async def create_run(req: RunRequest) -> dict[str, Any]:
        if not req.model_id and not req.custom_model and not req.openrouter_model:
            raise HTTPException(422, "Provide model_id, custom_model or openrouter_model.")
        opts = req.model_dump(exclude_none=True)
        opts["provider"] = "openrouter" if req.openrouter_model and not req.provider else (req.provider or default_provider())
        if opts["provider"] == "openrouter":
            if not req.openrouter_model:
                raise HTTPException(422, "Choose an OpenRouter model.")
            if not orp.api_key(req.openrouter_key):
                raise HTTPException(400, "No OpenRouter API key. Set OPENROUTER_API_KEY on the server or enter a key in Run options.")
            if req.speculative not in ("auto", "none"):
                raise HTTPException(422, "Speculative decoding can't be configured on a hosted API. Use the Modal provider to test it.")
        if opts["provider"] == "openai" and not (req.endpoint and req.endpoint.base_url):
            raise HTTPException(422, "The custom-endpoint provider needs an endpoint base_url.")
        from .providers import get_provider

        ok, why = get_provider(opts["provider"]).available()
        if not ok and not (opts["provider"] == "openrouter" and req.openrouter_key):
            raise HTTPException(400, why)
        try:
            h = manager.create(opts)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return {"run_id": h.id}

    @app.get("/api/runs")
    def list_runs() -> list[dict[str, Any]]:
        return manager.list()

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        h = manager.get(run_id)
        if not h:
            raise HTTPException(404, "Run not found")
        d = h.full()
        # live runs: include tests gathered so far so a page refresh can rebuild the grid
        if not h.report:
            d["tests"] = [e["test"] for e in h.events if e.get("type") == "test"]
            d["logs"] = [e for e in h.events if e.get("type") == "log"][-200:]
            d["perf"] = next((e["perf"] for e in reversed(h.events) if e.get("type") == "perf"), None)
        return d

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str) -> StreamingResponse:
        if not manager.get(run_id):
            raise HTTPException(404, "Run not found")

        async def gen():
            try:
                async for ev in manager.subscribe(run_id):
                    if ev.get("type") == "report":  # large; clients refetch /runs/{id}
                        ev = {"type": "report_ready"}
                    yield f"data: {json.dumps(ev)}\n\n"
                    await asyncio.sleep(0)
            except asyncio.CancelledError:  # client went away
                return
            yield "event: end\ndata: {}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str) -> dict[str, Any]:
        return {"cancelled": manager.cancel(run_id)}

    @app.delete("/api/runs/{run_id}")
    def delete(run_id: str) -> dict[str, Any]:
        if not manager.delete(run_id):
            raise HTTPException(404, "Run not found")
        return {"deleted": True}

    @app.get("/api/runs/{run_id}/report.md", response_class=PlainTextResponse)
    def report_md(run_id: str) -> str:
        h = manager.get(run_id)
        if not h or not h.report:
            raise HTTPException(404, "Report not available")
        return to_markdown(h.report)

    @app.get("/api/runs/{run_id}/report.json")
    def report_json(run_id: str) -> JSONResponse:
        h = manager.get(run_id)
        if not h or not h.report:
            raise HTTPException(404, "Report not available")
        return JSONResponse(h.report, headers={"Content-Disposition": f'attachment; filename="report-{run_id}.json"'})

    # ---- static frontend (production build)
    if FRONTEND_DIST.exists():
        assets = FRONTEND_DIST / "assets"
        if assets.exists():
            from fastapi.staticfiles import StaticFiles

            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            f = (FRONTEND_DIST / path).resolve()
            if path and f.is_file() and FRONTEND_DIST in f.parents:
                return FileResponse(f)
            return FileResponse(FRONTEND_DIST / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        def no_ui():
            return PlainTextResponse("API is running. Build the UI with `make build` (or run `make dev` for the Vite dev server).")

    return app


app = create_app()
