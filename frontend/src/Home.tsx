import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "./api";
import { ModelPicker } from "./ModelPicker";
import type { AppConfig, ModelInfo, RunOptions, RunSummary } from "./types";
import { Badge, Icon, fmt, scoreColor, timeAgo, verdictTone } from "./ui";

const FEATURES = [
  { icon: "shield", color: "#34d399", title: "Coherency & garble", text: "Detects gibberish, mojibake, repetition loops, leaked chat-template tokens, language drift, runaway reasoning and long-context failures." },
  { icon: "code", color: "#60a5fa", title: "Coding", text: "Models write real Python that is executed against hidden unit tests in a sandbox — LRU caches, interval merging, bug-fixing and more." },
  { icon: "calc", color: "#a78bfa", title: "Mathematics", text: "Word problems, algebra, combinatorics, number theory and probability graded on the exact final answer." },
  { icon: "chat", color: "#fbbf24", title: "General purpose", text: "Instruction following, strict JSON, constraints, knowledge, reasoning, summarisation and translation." },
  { icon: "bolt", color: "#22d3ee", title: "Speed & speculation", text: "Decode tok/s, TTFT, concurrency scaling, hardware-roofline efficiency, and whether speculative decoding is really running." },
  { icon: "spark", color: "#f472b6", title: "Exact fix-it plan", text: "Model-specific steps to add speculative decoding, make it faster and more coherent — each backed by the evidence from your run." },
] as const;

export function Home() {
  const nav = useNavigate();
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);

  const [provider, setProvider] = useState("");
  const [gpu, setGpu] = useState("");
  const [spec, setSpec] = useState("auto");
  const [specJson, setSpecJson] = useState('{"method": "ngram", "num_speculative_tokens": 5, "prompt_lookup_max": 4}');
  const [quick, setQuick] = useState(false);
  const [ep, setEp] = useState({ base_url: "http://localhost:8000/v1", api_key: "", model: "" });

  useEffect(() => {
    api.config().then((c) => { setCfg(c); setProvider(c.default_provider); }).catch((e) => setErr(`Cannot reach the API: ${e.message}`));
    api.models().then(setModels).catch(() => {});
    const load = () => api.runs().then(setRuns).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  const prov = cfg?.providers.find((p) => p.id === provider);

  const start = async (sel: { model_id?: string; custom_hf?: string }) => {
    setErr(null);
    setBusy(true);
    const opts: RunOptions = { provider, quick, speculative: spec };
    if (sel.model_id) opts.model_id = sel.model_id; else opts.custom_model = { hf_repo: sel.custom_hf! };
    if (gpu) opts.gpu = gpu;
    if (spec === "custom") opts.speculative_custom = specJson;
    if (provider === "openai") opts.endpoint = { base_url: ep.base_url, api_key: ep.api_key || undefined, model: ep.model || undefined };
    try {
      const { run_id } = await api.start(opts);
      nav(`/runs/${run_id}`);
    } catch (e) {
      setErr((e as Error).message);
      setBusy(false);
    }
  };

  const summary = [
    cfg?.providers.find((p) => p.id === provider)?.label ?? "…",
    gpu || "recommended GPU",
    cfg?.speculative_modes.find((m) => m.id === spec)?.label ?? spec,
    quick ? "quick suite" : "full suite",
  ].join(" · ");

  return (
    <>
      <section className="hero">
        <div className="eyebrow"><span className="dot green pulse" /> Evaluates on Modal GPUs · vLLM · OpenAI-compatible endpoints</div>
        <h1>Is your model <span className="gradient-text">actually ready</span> to ship?</h1>
        <p className="lead">Pick a model. We boot it, stress-test its coherency across coding, math and general tasks, measure decode speed, detect speculative decoding — then hand you a scorecard and the exact steps to make it faster and better.</p>

        <ModelPicker models={models} onSelect={start} busy={busy} disabled={!cfg} />
        <div className="hint-line">Selecting a model starts the evaluation immediately · ~{quick ? "2" : "5"}–15 min on Modal (cold start included)</div>

        <div className="opts">
          <button className="opts-toggle" onClick={() => setOpen((o) => !o)}>
            <Icon name="gear" size={16} /> Run options <span className="faint">— {summary}</span> <Icon name="chev" size={14} />
          </button>
          {open && cfg && (
            <div className="card opts-panel">
              <div className="field">
                <label>Where to run the model</label>
                <div className="seg">
                  {cfg.providers.map((p) => (
                    <button key={p.id} className={provider === p.id ? "on" : ""} onClick={() => setProvider(p.id)} disabled={!p.available && p.id !== "mock" && p.id !== "openai"}>
                      <span className={`dot ${p.available ? "green" : "red"}`} /> {p.label}
                    </button>
                  ))}
                </div>
                {prov && <div className="hint">{prov.hint}</div>}
              </div>

              {provider === "openai" && (
                <div className="grid3">
                  <div className="field"><label>Base URL</label><input className="input" value={ep.base_url} onChange={(e) => setEp({ ...ep, base_url: e.target.value })} placeholder="http://localhost:8000/v1" /></div>
                  <div className="field"><label>API key (optional)</label><input className="input" type="password" value={ep.api_key} onChange={(e) => setEp({ ...ep, api_key: e.target.value })} /></div>
                  <div className="field"><label>Served model name</label><input className="input" value={ep.model} onChange={(e) => setEp({ ...ep, model: e.target.value })} placeholder="auto-detect" /></div>
                </div>
              )}

              <div className="grid2">
                {provider !== "openai" && (
                  <div className="field">
                    <label>GPU</label>
                    <select className="select" value={gpu} onChange={(e) => setGpu(e.target.value)}>
                      <option value="">Recommended for the model</option>
                      {cfg.gpus.map((g) => <option key={g.id} value={g.id}>{g.id} · {g.mem_gb} GB · {g.bw_gbs} GB/s</option>)}
                      <option value="H100:2">H100 × 2</option><option value="H100:4">H100 × 4</option><option value="H200:2">H200 × 2</option><option value="H200:8">H200 × 8</option>
                    </select>
                    <div className="hint">Decode speed is memory-bandwidth bound — more GB/s means more tokens/s.</div>
                  </div>
                )}
                <div className="field">
                  <label>Speculative decoding</label>
                  <select className="select" value={spec} onChange={(e) => setSpec(e.target.value)}>
                    {cfg.speculative_modes.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                  </select>
                  <div className="hint">{cfg.speculative_modes.find((m) => m.id === spec)?.hint}</div>
                </div>
              </div>
              {spec === "custom" && (
                <div className="field"><label>--speculative-config (JSON)</label><textarea className="input mono" rows={3} value={specJson} onChange={(e) => setSpecJson(e.target.value)} /></div>
              )}
              <div className="toggle">
                <button className={`switch ${quick ? "on" : ""}`} onClick={() => setQuick(!quick)} aria-pressed={quick} aria-label="Quick mode" />
                <div><div style={{ fontWeight: 600, fontSize: 14 }}>Quick mode</div><div className="faint" style={{ fontSize: 12.5 }}>{cfg.suite.quick} representative tests instead of {cfg.suite.full}. Same pipeline, faster result.</div></div>
              </div>
              {provider === "modal" && !cfg.hf_token_set && (
                <div className="banner warn"><Icon name="shield" size={18} /><div>No <b>HF_TOKEN</b> set — gated models (Llama, Gemma) will fail to download. Export it before starting the server.</div></div>
              )}
            </div>
          )}
          {cfg && prov && !prov.available && (
            <div className="banner warn" style={{ marginTop: 14 }}>
              <Icon name="bolt" size={18} />
              <div><b>{prov.label} isn't ready:</b> {prov.hint} Switch to <a style={{ textDecoration: "underline", cursor: "pointer" }} onClick={() => { setProvider("mock"); setOpen(true); }}>Demo mode</a> to explore the platform without a GPU.</div>
            </div>
          )}
          {cfg && provider === "mock" && (
            <div className="banner ok" style={{ marginTop: 14 }}>
              <Icon name="flask" size={18} />
              <div><b>Demo mode</b> — models are simulated (no GPU, no credentials). Quality, speed and failure modes are synthetic; use Modal for real measurements.</div>
            </div>
          )}
          {err && <div className="banner err" style={{ marginTop: 14 }}><Icon name="x" size={18} /><div>{err}</div></div>}
        </div>
      </section>

      <section className="section" style={{ marginTop: 70 }}>
        <h2>What every run measures</h2>
        <div className="grid3">
          {FEATURES.map((f) => (
            <div className="card feature" key={f.title}>
              <div className="ico" style={{ color: f.color }}><Icon name={f.icon} size={20} /></div>
              <h3>{f.title}</h3>
              <p>{f.text}</p>
            </div>
          ))}
        </div>
      </section>

      {runs.length > 0 && (
        <section className="section">
          <h2>Recent evaluations</h2>
          <div className="runs-list">
            {runs.slice(0, 8).map((r) => <RunRow key={r.id} r={r} />)}
          </div>
        </section>
      )}
    </>
  );
}

export function RunRow({ r }: { r: RunSummary }) {
  const live = r.status === "running" || r.status === "queued";
  return (
    <Link to={`/runs/${r.id}`} className="run-row">
      <div><div className="nm">{r.model.name}</div><div className="sm">{r.options.provider === "mock" ? "Demo" : r.options.provider === "openai" ? "Endpoint" : "Modal"} · {r.options.speculative && r.options.speculative !== "auto" ? `spec: ${r.options.speculative}` : "baseline"} · {timeAgo(r.created_at)}</div></div>
      <div>
        {live ? <Badge tone="violet"><span className="spinner" style={{ width: 10, height: 10 }} /> running</Badge>
          : r.status === "completed" ? <Badge tone={verdictTone(r.verdict)}>{r.verdict === "ready" ? "Ready" : r.verdict === "caution" ? "Caution" : "Not ready"}</Badge>
          : <Badge tone="red">{r.status}</Badge>}
      </div>
      <div className="mono" style={{ color: r.scores ? scoreColor(r.scores.overall) : "var(--faint)", fontWeight: 700 }}>{r.scores ? `${Math.round(r.scores.overall)} / 100` : "—"}</div>
      <div className="mono muted">{r.decode_tps ? `${fmt(r.decode_tps, 0)} tok/s` : "—"}</div>
      <Icon name="arrow" size={16} />
    </Link>
  );
}
