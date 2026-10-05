/**
 * Offline preview backend.
 *
 * `npm run build:demo` bundles the real interface together with this module, which stands in for the FastAPI server:
 * it answers `fetch("/api/…")` and `new EventSource("/api/runs/…/events")` from fixtures that were recorded by the
 * platform itself (scripts/build_demo_fixtures.py). Starting a run replays the recorded event stream, compressed to
 * about half a minute, so the live progress screen, the report and the comparison all behave like the real thing.
 *
 * Nothing in here is used by the normal build.
 */
import type {
  AppConfig, Environment, ModelInfo, OrModels, Phase, Report, RunFull, RunOptions, RunSummary, TestResult,
} from "../types";

interface FixtureRun {
  key: string;
  options: RunOptions;
  model: RunSummary["model"];
  phases: Phase[];
  environment: Environment | null;
  events: [number, Record<string, any>][];
  report: Report;
  markdown: string;
  ms: number;
}
interface Fixtures {
  config: AppConfig;
  models: ModelInfo[];
  openrouter: OrModels;
  errors: Record<string, string>;
  runs: FixtureRun[];
}

type Listener = (ev: Record<string, any> | null) => void;

interface DemoRun {
  id: string;
  fx: FixtureRun;
  options: RunOptions;
  status: RunSummary["status"];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  phases: Phase[];
  events: Record<string, any>[];
  environment: Environment | null;
  report: Report | null;
  listeners: Set<Listener>;
  timers: ReturnType<typeof setTimeout>[];
}

/** How long a replayed evaluation takes on screen. A real run takes minutes; the preview is meant to be watched. */
const PLAYBACK_MS = 34_000;
/** Finished runs already in History when the preview opens: [fixture key, minutes ago]. A mix of verdicts and set-ups. */
const SEED_RUNS: [string, number][] = [
  ["mock|llama-3.1-8b|auto", 52],
  ["mock|qwen3-8b|eagle3", 30],
  ["openrouter|replay/opus|", 20],
  ["mock|r1-distill-qwen-7b|auto", 9],
  ["openrouter|replay/tiny|", 5],
  ["openrouter|replay/haiku|", 2],
];
/** Tests shorten it with `window.__demoPlaybackMs`. */
const playbackMs = (): number => (window as unknown as { __demoPlaybackMs?: number }).__demoPlaybackMs ?? PLAYBACK_MS;

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const fail = (detail: string, status = 422) => json({ detail }, status);

async function loadFixtures(): Promise<Fixtures> {
  const el = document.getElementById("demo-fixtures");
  const b64 = el?.textContent?.trim();
  if (!b64) throw new Error("The preview data is missing from this page.");
  if (typeof DecompressionStream === "undefined") throw new Error("This preview needs a current browser (Chrome, Edge, Safari 16.4+ or Firefox 113+).");
  const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const stream = new Blob([bin]).stream().pipeThrough(new DecompressionStream("gzip"));
  const fx = JSON.parse(await new Response(stream).text()) as Fixtures;
  // The preview only offers what it has recordings for: simulated models and the recorded OpenRouter answers.
  fx.config = {
    ...fx.config,
    default_provider: "mock",
    hf_token_set: true,
    openrouter_key_set: true,
    providers: ["mock", "openrouter"].map((id) => ({
      ...fx.config.providers.find((p) => p.id === id)!,
      available: true,
      ...(id === "openrouter" ? { hint: "Real answers recorded from hosted models, replayed through the OpenRouter code path. No key needed here." } : {}),
    })),
  };
  // The replayed "hosted" models are real answers with simulated serving: say so wherever they are listed.
  fx.openrouter = {
    ...fx.openrouter,
    models: fx.openrouter.models.map((m) => ({
      ...m,
      description: m.id === "replay/tiny" ? "Not a real model: a weak one emulated from the Opus answers, to show how the suite tells strong from weak."
        : `Real answers recorded from a ${m.name.split(" ")[0]} model. Speed, price and provider are simulated.`,
    })),
  };
  return fx;
}

function newId(): string {
  const a = new Uint8Array(6);
  crypto.getRandomValues(a);
  return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
}

export async function installDemoBackend(): Promise<void> {
  const fx = await loadFixtures();
  const byKey = new Map(fx.runs.map((r) => [r.key, r]));
  const runs = new Map<string, DemoRun>();
  const nativeFetch = window.fetch.bind(window);

  // ------------------------------------------------------------------ run lifecycle (mirrors backend/manager.py)
  const summary = (r: DemoRun): RunSummary => {
    const rep = r.report;
    return {
      id: r.id, status: r.status, created_at: r.created_at, finished_at: r.finished_at, error: r.error,
      model: r.fx.model, options: r.options, scores: rep?.scores ?? null, verdict: rep?.verdict.label ?? null,
      decode_tps: rep?.performance.decode_tps_median ?? null, speculative_status: rep?.speculative.status ?? null, phases: r.phases,
    };
  };

  const expand = (r: DemoRun, ev: Record<string, any>): Record<string, any> => {
    if (ev.type === "test" && typeof ev.i === "number") return { type: "test", test: r.fx.report.tests[ev.i] as TestResult };
    return ev;
  };

  const emit = (r: DemoRun, raw: Record<string, any>) => {
    const ev: Record<string, any> = { ...expand(r, raw) };
    if (ev.type === "log" || ev.type === "phase") ev.ts = new Date().toISOString();
    if (ev.type === "phase") r.phases = r.phases.map((p) => (p.id === ev.id ? { ...p, status: ev.status, detail: ev.detail ?? "" } : p));
    if (ev.type === "environment") r.environment = ev.environment;
    if (!ev.ephemeral) r.events.push(ev);   // like the server, live-only events (progress, speed ticks) aren't kept for late subscribers
    r.listeners.forEach((l) => l(ev));
  };

  const finish = (r: DemoRun, status: DemoRun["status"], error: string | null = null) => {
    r.status = status;
    r.error = error;
    r.finished_at = new Date().toISOString();
    if (status === "completed") {
      r.report = {
        ...r.fx.report, run_id: r.id, generated_at: r.finished_at,
        options: { ...r.fx.report.options, speculative: r.options.speculative ?? r.fx.report.options.speculative, parent_run_id: r.options.parent_run_id ?? null },
      };
      r.phases = r.fx.phases;
      emit(r, { type: "report_ready" });
    }
    emit(r, { type: "status", status });
    r.listeners.forEach((l) => l(null));
    r.listeners.clear();
  };

  const play = (r: DemoRun) => {
    const evs = r.fx.events;
    const total = Math.max(1, r.fx.ms);
    const scale = playbackMs() / total;
    r.status = "running";
    r.started_at = new Date().toISOString();
    emit(r, { type: "status", status: "running" });
    for (const [t, ev] of evs) r.timers.push(setTimeout(() => emit(r, ev), Math.round(t * scale)));
    r.timers.push(setTimeout(() => finish(r, "completed"), Math.round(total * scale) + 250));
  };

  const cancel = (r: DemoRun): boolean => {
    if (r.status !== "running" && r.status !== "queued") return false;
    r.timers.forEach(clearTimeout);
    r.timers = [];
    r.phases = r.phases.map((p) => (p.status === "running" ? { ...p, status: "error" as const } : p));
    emit(r, { type: "error", message: "Cancelled." });
    finish(r, "cancelled", "Cancelled by user.");
    return true;
  };

  const seed = (key: string, ageS: number): void => {
    const f = byKey.get(key);
    if (!f) return;
    const created = new Date(Date.now() - ageS * 1000);
    const id = newId();
    const run: DemoRun = {
      id, fx: f, options: f.options, status: "completed", created_at: created.toISOString(),
      started_at: created.toISOString(), finished_at: new Date(created.getTime() + f.report.duration_s * 1000).toISOString(), error: null,
      phases: f.phases, events: [], environment: f.environment, report: { ...f.report, run_id: id, generated_at: created.toISOString() }, listeners: new Set(), timers: [],
    };
    runs.set(id, run);
  };
  SEED_RUNS.forEach(([key, minutes]) => seed(key, minutes * 60));

  // ------------------------------------------------------------------ fixture lookup
  const specKey = (o: RunOptions) => (!o.speculative || o.speculative === "none" ? "auto" : o.speculative);
  const pick = (o: RunOptions): { fx: FixtureRun } | { error: string } => {
    if (o.custom_model) return { error: "The preview only contains the listed models. Install the app to evaluate any Hugging Face repo." };
    if (o.provider === "openrouter") {
      if (!o.openrouter_model) return { error: "Choose an OpenRouter model." };
      const k = `openrouter|${o.openrouter_model}|${o.stress ?? ""}`;
      const f = byKey.get(k);
      if (f) return { fx: f };
      return { error: o.stress ? "The preview has recorded self-checks for the Haiku and Sonnet answers only." : "The preview only has recorded answers for the listed models. Install the app to evaluate any OpenRouter model." };
    }
    if (o.provider === "openai") return { error: "Custom endpoints need a real server. Install the app to evaluate an endpoint." };
    if (o.stress) return { error: "In the preview, the detector self-check runs on OpenRouter models. Switch “Run on” to OpenRouter." };
    if (o.speculative === "custom") return { error: "Custom speculative configs need a real server. Pick one of the built-in recipes." };
    const spec = specKey(o);
    const err = fx.errors[`mock|${o.model_id}|${spec}`];
    if (err) return { error: err };
    const f = byKey.get(`mock|${o.model_id}|${spec}`);
    return f ? { fx: f } : { error: `No recording for ${o.model_id} with “${spec}” in the preview.` };
  };

  // ------------------------------------------------------------------ fetch
  const route = async (path: string, method: string, body: any): Promise<Response> => {
    const url = new URL(path, "http://preview");
    const p = url.pathname;
    if (p === "/api/config" && method === "GET") return json(fx.config);
    if (p === "/api/models") return json(fx.models);
    if (p === "/api/openrouter/models") return json(fx.openrouter);
    if (p === "/api/openrouter/status") return json({ configured: true, valid: true, free_tier: false, usage: 0, limit: null, remaining: null });
    if (p === "/api/runs" && method === "GET") {
      return json([...runs.values()].sort((a, b) => b.created_at.localeCompare(a.created_at)).map(summary));
    }
    if (p === "/api/runs" && method === "POST") {
      const o = (body ?? {}) as RunOptions;
      if (!o.model_id && !o.custom_model && !o.openrouter_model) return fail("Provide model_id, custom_model or openrouter_model.");
      if (o.quick) return fail("Quick mode isn't recorded in the preview. Run the full suite.");
      const hit = pick(o);
      if ("error" in hit) return fail(hit.error);
      const id = newId();
      const { openrouter_key: _k, endpoint: _e, ...safe } = o as RunOptions & Record<string, unknown>;
      const run: DemoRun = {
        id, fx: hit.fx, options: { ...hit.fx.options, ...safe }, status: "queued", created_at: new Date().toISOString(), started_at: null, finished_at: null,
        error: null, phases: hit.fx.phases.map((ph) => ({ ...ph, status: "pending" as const, detail: "" })), events: [], environment: null, report: null,
        listeners: new Set(), timers: [],
      };
      runs.set(id, run);
      play(run);
      return json({ run_id: id }, 201);
    }
    const exp = p.match(/^\/api\/runs\/([\w-]+)\/report\.(md|json)$/);
    if (exp) {
      const r = runs.get(exp[1]);
      if (!r?.report) return fail("Report not available", 404);
      if (exp[2] === "json") return json(r.report);
      return new Response(r.fx.markdown.replace("@@GENERATED@@", r.report.generated_at), { headers: { "content-type": "text/plain; charset=utf-8" } });
    }
    const m = p.match(/^\/api\/runs\/([\w-]+)(\/cancel|\/events)?$/);
    if (m) {
      const r = runs.get(m[1]);
      if (!r) return fail("Run not found", 404);
      if (m[2] === "/cancel" && method === "POST") return json({ cancelled: cancel(r) });
      if (!m[2] && method === "DELETE") {
        cancel(r);
        runs.delete(r.id);
        return json({ deleted: true });
      }
      if (!m[2] && method === "GET") {
        const d: RunFull & Record<string, unknown> = { ...summary(r), report: r.report, environment: r.environment };
        if (!r.report) {
          d.tests = r.events.filter((e) => e.type === "test").map((e) => e.test);
          d.logs = r.events.filter((e) => e.type === "log").slice(-200);
        }
        return json(d);
      }
    }
    return fail("Not available in the preview", 404);
  };

  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (!raw.startsWith("/api/")) return nativeFetch(input, init);
    let body: unknown;
    try { body = init?.body ? JSON.parse(String(init.body)) : undefined; } catch { body = undefined; }
    return new Promise<Response>((resolve) => setTimeout(() => route(raw, (init?.method ?? "GET").toUpperCase(), body).then(resolve), 60 + Math.random() * 90));
  };

  // ------------------------------------------------------------------ server-sent events
  class DemoEventSource {
    static readonly CONNECTING = 0; static readonly OPEN = 1; static readonly CLOSED = 2;
    readonly CONNECTING = 0; readonly OPEN = 1; readonly CLOSED = 2;
    readyState = 0;
    onmessage: ((m: { data: string }) => void) | null = null;
    onerror: (() => void) | null = null;
    onopen: (() => void) | null = null;
    private ends: (() => void)[] = [];
    private off: (() => void) | null = null;

    constructor(public url: string) {
      const id = url.match(/\/api\/runs\/([\w-]+)\/events/)?.[1] ?? "";
      setTimeout(() => {
        const r = runs.get(id);
        if (this.readyState === 2) return;
        if (!r) { this.readyState = 2; this.onerror?.(); return; }
        this.readyState = 1;
        this.onopen?.();
        const send = (ev: Record<string, any>) => this.onmessage?.({ data: JSON.stringify(ev) });
        const backlog = [...r.events];
        backlog.forEach(send);
        if (r.status !== "queued" && r.status !== "running") { this.finish(); return; }
        const l: Listener = (ev) => { if (ev === null) this.finish(); else if (this.readyState === 1) send(ev); };
        r.listeners.add(l);
        this.off = () => r.listeners.delete(l);
      }, 20);
    }
    private finish() { this.ends.forEach((f) => f()); this.close(); }
    addEventListener(type: string, fn: () => void) { if (type === "end") this.ends.push(fn); }
    removeEventListener() { /* unused */ }
    close() { this.readyState = 2; this.off?.(); }
  }
  (window as unknown as { EventSource: unknown }).EventSource = DemoEventSource;
}
