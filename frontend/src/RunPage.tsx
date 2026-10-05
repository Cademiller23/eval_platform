import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "./api";
import { Report as ReportView } from "./Report";
import type { Environment, LogLine, Perf, Phase, RunFull, TestResult } from "./types";
import { Badge, Icon, Sparkline, fmt } from "./ui";

interface Live {
  phases: Phase[];
  logs: LogLine[];
  tests: TestResult[];
  perf: Perf | null;
  env: Environment | null;
  tps: number | null;
  tpsHistory: number[];
  progress: Record<string, { done: number; total: number }>;
}

const empty = (phases: Phase[]): Live => ({ phases, logs: [], tests: [], perf: null, env: null, tps: null, tpsHistory: [], progress: {} });

export function RunPage() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const [run, setRun] = useState<RunFull | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [live, setLive] = useState<Live>(empty([]));
  const esRef = useRef<EventSource | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await api.run(id);
      setRun(r);
      return r;
    } catch (e) {
      setErr((e as Error).message);
      return null;
    }
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | undefined;
    setRun(null); setErr(null);
    (async () => {
      const r = await load();
      if (!r || cancelled) return;
      setLive(empty(r.phases));
      if (r.status !== "running" && r.status !== "queued") return;
      const es = new EventSource(`/api/runs/${id}/events`);
      esRef.current = es;
      let pending: Live | null = null;
      let state = empty(r.phases);
      const flush = () => { if (pending) { setLive(pending); pending = null; } };
      timer = setInterval(flush, 120);
      es.onmessage = (m) => {
        const ev = JSON.parse(m.data);
        state = reduce(state, ev);
        pending = state;
        if (ev.type === "status" && ev.status !== "running") { flush(); load(); }
        if (ev.type === "report_ready") load();
      };
      es.addEventListener("end", () => { flush(); es.close(); load(); });
      es.onerror = () => { es.close(); setTimeout(load, 1500); };
    })();
    return () => { cancelled = true; clearInterval(timer); esRef.current?.close(); };
  }, [id, load]);

  useEffect(() => {
    // while the page is open on an in-flight run whose stream dropped, keep polling for completion
    if (!run || (run.status !== "running" && run.status !== "queued")) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [run, load]);

  if (err) return <div className="empty"><h2>Run not found</h2><p>{err}</p><Link className="btn" to="/">Back home</Link></div>;
  if (!run) return <div className="skeleton" style={{ height: 320, marginTop: 30 }} />;

  const finished = run.status === "completed" && run.report;
  const active = run.status === "running" || run.status === "queued";

  return (
    <>
      <div className="run-head">
        <div>
          <h1>{run.model.name}</h1>
          <div className="sub">
            <span className="mono">{run.model.hf_repo}</span>
            {run.options.provider === "mock" && <Badge tone="violet">demo</Badge>}
            {run.options.provider === "openrouter" && <Badge tone="cyan">OpenRouter</Badge>}
            {run.options.stress && <Badge tone="amber">stress: {run.options.stress}</Badge>}
            {run.options.speculative && run.options.speculative !== "auto" && run.options.provider !== "openrouter" && <Badge tone="cyan">spec: {run.options.speculative}</Badge>}
            {run.options.quick && <Badge>quick</Badge>}
            {active && <Badge tone="violet"><span className="spinner" style={{ width: 10, height: 10 }} /> evaluating</Badge>}
          </div>
        </div>
        <div className="actions" style={{ marginTop: 0 }}>
          {active && <button className="btn danger" onClick={() => api.cancel(id).then(load)}><Icon name="stop" size={15} /> Cancel</button>}
          {!active && <button className="btn" onClick={() => api.remove(id).then(() => nav("/"))}><Icon name="trash" size={15} /> Delete</button>}
          <Link className="btn" to="/">New evaluation</Link>
        </div>
      </div>

      {(run.status === "failed" || run.status === "cancelled" || run.status === "interrupted") && (
        <div className="banner err" style={{ marginTop: 18 }}>
          <Icon name="x" size={18} />
          <div><b>Evaluation {run.status}.</b> {run.error}</div>
        </div>
      )}

      {finished ? (
        <ReportView run={run} />
      ) : (
        <LiveView run={run} live={live.phases.length ? live : empty(run.phases)} />
      )}
    </>
  );
}

function reduce(s: Live, ev: any): Live {
  switch (ev.type) {
    case "phase":
      return { ...s, phases: s.phases.map((p) => (p.id === ev.id ? { ...p, status: ev.status, detail: ev.detail ?? "" } : p)) };
    case "log": return { ...s, logs: [...s.logs.slice(-300), { level: ev.level, message: ev.message, ts: ev.ts }] };
    case "test": return { ...s, tests: [...s.tests, ev.test] };
    case "perf": return { ...s, perf: ev.perf };
    case "environment": return { ...s, env: ev.environment };
    case "progress": return { ...s, progress: { ...s.progress, [ev.phase]: { done: ev.done, total: ev.total } } };
    case "metric":
      if (ev.name === "tps") return { ...s, tps: ev.value, tpsHistory: [...s.tpsHistory.slice(-59), ev.value] };
      return s;
    default: return s;
  }
}

function LiveView({ run, live }: { run: RunFull; live: Live }) {
  const consoleRef = useRef<HTMLDivElement>(null);
  useEffect(() => { consoleRef.current?.scrollTo({ top: 1e9 }); }, [live.logs.length]);
  const phases = live.phases.length ? live.phases : run.phases;
  const env = live.env ?? run.environment;
  const current = phases.find((p) => p.status === "running");
  const prog = current ? live.progress[current.id] : undefined;

  return (
    <>
      <div className="stepper">
        {phases.map((p, i) => {
          const pr = live.progress[p.id];
          const pctw = p.status === "running" && pr ? (pr.done / Math.max(1, pr.total)) * 100 : p.status === "running" ? 12 : 0;
          return (
            <div key={p.id} className={`step ${p.status}`} style={{ ["--p" as string]: `${pctw}%` }}>
              <div className="n">
                <span>{String(i + 1).padStart(2, "0")}</span>
                {p.status === "running" ? <span className="spinner" /> : p.status === "done" ? <span style={{ color: "var(--green)" }}><Icon name="check" size={14} stroke={2.6} /></span> : p.status === "error" ? <span style={{ color: "var(--red)" }}><Icon name="x" size={14} stroke={2.6} /></span> : null}
              </div>
              <div className="t">{p.title}</div>
              <div className="d">{p.status === "running" && pr ? `${pr.done}/${pr.total}` : p.detail}</div>
            </div>
          );
        })}
      </div>

      <div className="live-grid">
        <div className="card">
          <h3>Live decode speed</h3>
          <p className="sub">Tokens per second as the model streams</p>
          <div className="gauge-num">{live.tps != null ? fmt(live.tps, 0) : live.perf?.decode_tps_median ? fmt(live.perf.decode_tps_median, 0) : "—"}<small>tok/s</small></div>
          <div style={{ marginTop: 16 }}><Sparkline values={live.tpsHistory} /></div>
          <div className="kv" style={{ marginTop: 18 }}>
            <div><span className="k">Phase</span><span className="v">{current?.title ?? "—"}{prog ? ` · ${prog.done}/${prog.total}` : ""}</span></div>
            <div><span className="k">Tests done</span><span className="v">{live.tests.length}</span></div>
            {env && <div><span className="k">{env.hosted ? "Via" : "GPU"}</span><span className="v">{env.hosted ? "OpenRouter" : env.requested_gpu ?? env.gpu ?? "—"}</span></div>}
            {env?.engine && <div><span className="k">Engine</span><span className="v">{env.engine} {env.engine_version}</span></div>}
            {live.perf && <div><span className="k">TTFT p50</span><span className="v">{fmt(live.perf.ttft_ms_p50, 0, " ms")}</span></div>}
          </div>
        </div>
        <div className="card">
          <h3>Activity</h3>
          <p className="sub">Provisioning, benchmarks and test progress</p>
          <div className="console" ref={consoleRef}>
            {live.logs.length === 0 && <span className="faint">Waiting for the first event…</span>}
            {live.logs.map((l, i) => (
              <div className="l" key={i}><span className="ts">{new Date(l.ts).toLocaleTimeString([], { hour12: false })}</span><span className={l.level}>{l.message}</span></div>
            ))}
          </div>
        </div>
      </div>

      {live.tests.length > 0 && (
        <div className="section">
          <h2>Results so far</h2>
          <div className="tiles">
            {live.tests.map((t) => (
              <div key={t.id} className={`tile ${t.passed ? "pass" : "fail"}`}>
                <span className="ic">{t.passed ? "✓" : "✗"}</span>
                <div><div className="tn">{t.name}</div><div className="td">{t.domain}{t.health?.severe ? " · output issue" : ""}</div></div>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
