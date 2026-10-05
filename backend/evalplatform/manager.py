"""Run lifecycle: creation, background execution, event fan-out and persistence."""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from .config import get_settings
from .runner import PHASES, Runner, now_iso, resolve_model


class RunHandle:
    def __init__(self, run_id: str, options: dict[str, Any], model: dict[str, Any]):
        self.id = run_id
        self.options = options
        self.model = {k: model.get(k) for k in ("id", "name", "family", "hf_repo", "params_b", "reasoning", "tags")}
        self.status = "queued"
        self.created_at = now_iso()
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.error: str | None = None
        self.phases = [{"id": pid, "title": title, "status": "pending", "detail": ""} for pid, title in PHASES]
        self.events: list[dict[str, Any]] = []
        self.report: dict[str, Any] | None = None
        self.environment: dict[str, Any] | None = None
        self.task: asyncio.Task | None = None
        self.subscribers: set[asyncio.Queue] = set()

    # ---- serialisation
    def summary(self) -> dict[str, Any]:
        rep = self.report or {}
        return {
            "id": self.id, "status": self.status, "created_at": self.created_at, "started_at": self.started_at,
            "finished_at": self.finished_at, "error": self.error, "model": self.model,
            "options": {k: v for k, v in self.options.items() if k not in ("endpoint", "openrouter_key")},
            "scores": rep.get("scores"), "verdict": (rep.get("verdict") or {}).get("label"),
            "decode_tps": (rep.get("performance") or {}).get("decode_tps_median"),
            "speculative_status": (rep.get("speculative") or {}).get("status"),
            "phases": self.phases,
        }

    def full(self) -> dict[str, Any]:
        d = self.summary()
        d["report"] = self.report
        d["environment"] = self.environment
        return d

    # ---- event handling
    def emit(self, ev: dict[str, Any]) -> None:
        t = ev.get("type")
        if t == "phase":
            for p in self.phases:
                if p["id"] == ev["id"]:
                    p["status"], p["detail"] = ev["status"], ev.get("detail", "")
        elif t == "environment":
            self.environment = ev["environment"]
        if not ev.get("ephemeral"):
            self.events.append(ev)
        for q in list(self.subscribers):
            q.put_nowait(ev)

    def persist(self) -> None:
        path = get_settings().runs_dir / f"{self.id}.json"
        tmp = path.with_suffix(".tmp")
        data = {**self.full(), "events": [e for e in self.events if e.get("type") not in ("test", "report")]}
        tmp.write_text(json.dumps(data))
        tmp.replace(path)


class RunManager:
    def __init__(self) -> None:
        self.runs: dict[str, RunHandle] = {}
        self._load()

    def _load(self) -> None:
        for p in sorted(get_settings().runs_dir.glob("*.json")):
            try:
                d = json.loads(p.read_text())
            except Exception:
                continue
            h = RunHandle(d["id"], d.get("options", {}), d.get("model", {}))
            h.model = d.get("model", h.model)
            h.status = d.get("status", "failed")
            h.created_at, h.started_at, h.finished_at = d.get("created_at"), d.get("started_at"), d.get("finished_at")
            h.error = d.get("error")
            h.phases = d.get("phases", h.phases)
            h.report = d.get("report")
            h.environment = d.get("environment")
            h.events = d.get("events", [])
            if h.status in ("queued", "running"):
                h.status, h.error = "interrupted", "Server restarted while this run was in progress."
            self.runs[h.id] = h

    def create(self, options: dict[str, Any]) -> RunHandle:
        model = resolve_model(options)  # validates early
        rid = uuid.uuid4().hex[:12]
        h = RunHandle(rid, options, model)
        self.runs[rid] = h
        h.task = asyncio.create_task(self._execute(h))
        return h

    @staticmethod
    def _sync_model(h: RunHandle, runner: Runner, ev: dict[str, Any]) -> None:
        """The provider learns the real display name / context / weights while starting; reflect that in the run."""
        if ev.get("type") == "environment":
            for k in ("name", "family", "hf_repo", "params_b", "reasoning", "tags"):
                if runner.model.get(k) is not None:
                    h.model[k] = runner.model[k]

    async def _execute(self, h: RunHandle) -> None:
        h.status, h.started_at = "running", now_iso()
        h.emit({"type": "status", "status": "running"})
        try:
            h.persist()
        except Exception:  # pragma: no cover
            pass
        runner = Runner(h.id, h.options, lambda ev: (self._sync_model(h, runner, ev), h.emit(ev)))
        try:
            h.report = await runner.run()
            h.status = "completed"
            # attach tests without the heavy event duplication
            h.emit({"type": "report", "report": h.report})
        except asyncio.CancelledError:
            h.status, h.error = "cancelled", "Cancelled by user."
            for p in h.phases:
                if p["status"] == "running":
                    p["status"] = "error"
            h.emit({"type": "error", "message": "Cancelled."})
        except Exception as e:  # noqa: BLE001
            h.status, h.error = "failed", f"{type(e).__name__}: {e}" if str(e) == "" else str(e)
            for p in h.phases:
                if p["status"] == "running":
                    p["status"], p["detail"] = "error", h.error[:160]
            h.emit({"type": "log", "level": "error", "message": h.error, "ts": now_iso()})
            h.emit({"type": "error", "message": h.error})
        finally:
            h.finished_at = now_iso()
            h.emit({"type": "status", "status": h.status})
            try:
                h.persist()
            except Exception:  # pragma: no cover
                pass
            for q in list(h.subscribers):
                q.put_nowait(None)

    def get(self, rid: str) -> RunHandle | None:
        return self.runs.get(rid)

    def list(self) -> list[dict[str, Any]]:
        return [h.summary() for h in sorted(self.runs.values(), key=lambda x: x.created_at, reverse=True)]

    def cancel(self, rid: str) -> bool:
        h = self.runs.get(rid)
        if h and h.task and not h.task.done():
            h.task.cancel()
            return True
        return False

    def delete(self, rid: str) -> bool:
        h = self.runs.pop(rid, None)
        if not h:
            return False
        if h.task and not h.task.done():
            h.task.cancel()
        try:
            (get_settings().runs_dir / f"{rid}.json").unlink(missing_ok=True)
        except Exception:  # pragma: no cover
            pass
        return True

    async def subscribe(self, rid: str) -> AsyncIterator[dict[str, Any]]:
        h = self.runs[rid]
        q: asyncio.Queue = asyncio.Queue()
        backlog = list(h.events)
        h.subscribers.add(q)
        try:
            for ev in backlog:
                yield ev
            if h.status not in ("queued", "running"):
                return
            while True:
                ev = await q.get()
                if ev is None:
                    return
                yield ev
        finally:
            h.subscribers.discard(q)
