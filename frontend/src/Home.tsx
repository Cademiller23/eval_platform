import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "./api";
import { Picker, catalogEntries, openrouterEntries, type Selection } from "./ModelPicker";
import type { AppConfig, ModelInfo, OrModels, OrStatus, RunOptions, RunSummary } from "./types";
import { Badge, Icon, fmt, scoreColor, timeAgo, verdictTone } from "./ui";

const FEATURES = [
  { icon: "shield", color: "#34d399", title: "Coherency & garble", text: "Detects gibberish, mojibake, repetition loops, leaked chat-template tokens, language drift, runaway reasoning and long-context failures." },
  { icon: "code", color: "#60a5fa", title: "Coding", text: "Models write real Python that is executed against hidden unit tests in a sandbox — LRU caches, interval merging, bug-fixing and more." },
  { icon: "calc", color: "#a78bfa", title: "Mathematics", text: "Word problems, algebra, combinatorics, number theory and probability graded on the exact final answer." },
  { icon: "chat", color: "#fbbf24", title: "General purpose", text: "Instruction following, strict JSON, constraints, knowledge, reasoning, summarisation and translation." },
  { icon: "bolt", color: "#22d3ee", title: "Speed & speculation", text: "Decode tok/s, TTFT, concurrency scaling, hardware-roofline efficiency, and whether speculative decoding is really running." },
  { icon: "spark", color: "#f472b6", title: "Exact fix-it plan", text: "Model-specific steps to add speculative decoding, make it faster and more coherent — each backed by the evidence from your run." },
] as const;

const KEY_STORE = "coherence-lab.openrouter-key";
const readKey = () => { try { return sessionStorage.getItem(KEY_STORE) ?? ""; } catch { return ""; } };
export const savedOpenRouterKey = readKey;

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
  const [multi, setMulti] = useState(false);
  const [stress, setStress] = useState<"" | "garble" | "loop">("");
  const [ep, setEp] = useState({ base_url: "http://localhost:8000/v1", api_key: "", model: "" });
  const [orKey, setOrKey] = useState(readKey());
  const [orModels, setOrModels] = useState<OrModels | null>(null);
  const [orLoading, setOrLoading] = useState(false);
  const [orStatus, setOrStatus] = useState<OrStatus | null>(null);
  const [orFailed, setOrFailed] = useState(false);

  useEffect(() => {
    api.config().then((c) => { setCfg(c); setProvider(c.default_provider); }).catch((e) => setErr(`Cannot reach the API: ${e.message}`));
    api.models().then(setModels).catch(() => {});
    const load = () => api.runs().then(setRuns).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (provider !== "openrouter" || orModels || orLoading || orFailed) return;
    setOrLoading(true);
    api.openrouterModels().then(setOrModels).catch((e) => { setOrFailed(true); setErr(`Could not load OpenRouter models: ${e.message}`); }).finally(() => setOrLoading(false));
  }, [provider, orModels, orLoading, orFailed]);

  useEffect(() => { try { orKey ? sessionStorage.setItem(KEY_STORE, orKey) : sessionStorage.removeItem(KEY_STORE); } catch { /* private mode */ } }, [orKey]);

  const prov = cfg?.providers.find((p) => p.id === provider);
  const isOR = provider === "openrouter";
  const entries = useMemo(
    () => (isOR ? openrouterEntries(orModels?.models ?? [], orModels?.featured ?? []) : catalogEntries(models)),
    [isOR, orModels, models],
  );
  const keyOk = !isOR || !!orKey.trim() || !!cfg?.openrouter_key_set;

  const buildOptions = (id: string | null, custom?: string): RunOptions => {
    const opts: RunOptions = { provider, quick, speculative: isOR ? "auto" : spec };
    if (isOR) {
      opts.openrouter_model = id ?? custom!;
      if (orKey.trim() && !cfg?.openrouter_key_set) opts.openrouter_key = orKey.trim();
    } else if (id) opts.model_id = id;
    else opts.custom_model = { hf_repo: custom! };
    if (!isOR && gpu) opts.gpu = gpu;
    if (!isOR && spec === "custom") opts.speculative_custom = specJson;
    if (provider === "openai") opts.endpoint = { base_url: ep.base_url, api_key: ep.api_key || undefined, model: ep.model || undefined };
    if (stress) opts.stress = stress;
    return opts;
  };

  const start = async (sel: Selection) => {
    setErr(null);
    if (!keyOk) { setOpen(true); setErr("Enter an OpenRouter API key in Run options (or set OPENROUTER_API_KEY on the server)."); return; }
    setBusy(true);
    try {
      const targets: { id: string | null; custom?: string }[] = sel.ids.length ? sel.ids.map((id) => ({ id })) : [{ id: null, custom: sel.custom }];
      const started: string[] = [];
      for (const t of targets) {
        const { run_id } = await api.start(buildOptions(t.id, t.custom));
        started.push(run_id);
      }
      nav(started.length === 1 ? `/runs/${started[0]}` : `/compare?ids=${started.join(",")}`);
    } catch (e) {
      setErr((e as Error).message);
      setBusy(false);
    }
  };

  const checkKey = async () => {
    setOrStatus(null);
    try { setOrStatus(await api.openrouterStatus()); } catch (e) { setOrStatus({ configured: true, valid: null, error: (e as Error).message }); }
  };

  const summary = [
    prov?.label ?? "…",
    ...(isOR ? [] : [gpu || "recommended GPU", cfg?.speculative_modes.find((m) => m.id === spec)?.label ?? spec]),
    quick ? "quick suite" : "full suite",
    ...(multi ? ["compare mode"] : []),
    ...(stress ? [`stress: ${stress}`] : []),
  ].join(" · ");

  const eta = isOR ? "1–4 min" : provider === "mock" ? "under a minute" : quick ? "3–8 min" : "5–15 min";

  return (
    <>
      <section className="hero">
        <div className="eyebrow"><span className="dot green pulse" /> Modal GPUs · OpenRouter · any OpenAI-compatible endpoint</div>
        <h1>Is your model <span className="gradient-text">actually ready</span> to ship?</h1>
        <p className="lead">Pick a model. We boot it, stress-test its coherency across coding, math and general tasks, measure decode speed, detect speculative decoding — then hand you a scorecard and the exact steps to make it faster and better.</p>

        <Picker
          entries={entries}
          onSelect={start}
          busy={busy}
          loading={isOR && orLoading}
          disabled={!cfg}
          multi={multi}
          searchPlaceholder={isOR ? "Search OpenRouter — or paste any model slug (vendor/model)" : "Search models — or paste any Hugging Face repo (org/name)"}
          customPattern={isOR ? /^~?[\w.-]+\/[\w.:~-]+$/ : /^[\w.-]+\/[\w.-]+$/}
          customLabel={isOR ? "Evaluate OpenRouter model" : "Evaluate custom model"}
          placeholder={isOR ? "Select an OpenRouter model to evaluate" : "Select a model to evaluate"}
          footer={isOR ? "Picking a model starts the evaluation through OpenRouter" : undefined}
        />
        <div className="hint-line">{multi ? "Compare mode: tick up to 4 models, then start them together" : "Selecting a model starts the evaluation immediately"} · ~{eta}{isOR ? " · cost depends on the model (prices shown in the list)" : ""}</div>

        <div className="opts">
          <button className="opts-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
            <Icon name="gear" size={16} /> Run options <span className="faint">— {summary}</span> <Icon name="chev" size={14} />
          </button>
          {open && cfg && (
            <div className="card opts-panel">
              <div className="field">
                <label>Where to run the model</label>
                <div className="seg" role="radiogroup">
                  {cfg.providers.map((p) => (
                    <button key={p.id} className={provider === p.id ? "on" : ""} onClick={() => setProvider(p.id)} role="radio" aria-checked={provider === p.id}>
                      <span className={`dot ${p.available || p.id === "openai" || p.id === "openrouter" ? "green" : "red"}`} /> {p.id === "mock" ? "Demo" : p.label}
                    </button>
                  ))}
                </div>
                {prov && <div className="hint">{prov.hint}</div>}
              </div>

              {isOR && (
                <div className="field">
                  <label>OpenRouter API key</label>
                  {cfg.openrouter_key_set ? (
                    <div className="key-row">
                      <span className="badge green"><Icon name="check" size={12} stroke={3} /> OPENROUTER_API_KEY found on the server</span>
                      <button className="btn small" onClick={checkKey}>Check key &amp; credit</button>
                      {orStatus && (orStatus.valid
                        ? <span className="muted" style={{ fontSize: 13 }}>Valid{orStatus.remaining != null ? ` · $${orStatus.remaining.toFixed(2)} credit left` : orStatus.limit == null ? " · no spend limit" : ""}</span>
                        : <span style={{ color: "var(--red)", fontSize: 13 }}>{orStatus.error ?? "Key rejected"}</span>)}
                    </div>
                  ) : (
                    <>
                      <input className="input" type="password" autoComplete="off" spellCheck={false} placeholder="sk-or-v1-…" value={orKey} onChange={(e) => setOrKey(e.target.value)} />
                      <div className="hint">Kept only in this browser tab (session storage) and sent with your run — never stored on the server. Or export <span className="mono">OPENROUTER_API_KEY</span> before starting the server.</div>
                    </>
                  )}
                </div>
              )}

              {provider === "openai" && (
                <div className="grid3">
                  <div className="field"><label>Base URL</label><input className="input" value={ep.base_url} onChange={(e) => setEp({ ...ep, base_url: e.target.value })} placeholder="http://localhost:8000/v1" /></div>
                  <div className="field"><label>API key (optional)</label><input className="input" type="password" value={ep.api_key} onChange={(e) => setEp({ ...ep, api_key: e.target.value })} /></div>
                  <div className="field"><label>Served model name</label><input className="input" value={ep.model} onChange={(e) => setEp({ ...ep, model: e.target.value })} placeholder="auto-detect" /></div>
                </div>
              )}

              {!isOR && (
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
              )}
              {!isOR && spec === "custom" && (
                <div className="field"><label>--speculative-config (JSON)</label><textarea className="input mono" rows={3} value={specJson} onChange={(e) => setSpecJson(e.target.value)} /></div>
              )}

              <div className="grid2">
                <div className="toggle">
                  <button className={`switch ${quick ? "on" : ""}`} onClick={() => setQuick(!quick)} aria-pressed={quick} aria-label="Quick mode" />
                  <div><div style={{ fontWeight: 600, fontSize: 14 }}>Quick mode</div><div className="faint" style={{ fontSize: 12.5 }}>{cfg.suite.quick} representative tests instead of {cfg.suite.full}. Same pipeline, faster result.</div></div>
                </div>
                <div className="toggle">
                  <button className={`switch ${multi ? "on" : ""}`} onClick={() => setMulti(!multi)} aria-pressed={multi} aria-label="Compare mode" />
                  <div><div style={{ fontWeight: 600, fontSize: 14 }}>Compare several models</div><div className="faint" style={{ fontSize: 12.5 }}>Multi-select up to 4 models and get a side-by-side leaderboard.</div></div>
                </div>
              </div>

              <div className="field">
                <label>Detector self-check (advanced)</label>
                <select className="select" value={stress} onChange={(e) => setStress(e.target.value as "" | "garble" | "loop")}>
                  <option value="">Off — evaluate the model as it is</option>
                  <option value="garble">Break sampling: temperature 2.0 (should produce garbled text)</option>
                  <option value="loop">Break sampling: negative repetition penalties (should produce loops)</option>
                </select>
                <div className="hint">Deliberately corrupts decoding on the <b>same</b> model so you can confirm the platform really flags garbling and repetition. Scores are expected to drop.</div>
              </div>

              {provider === "modal" && !cfg.hf_token_set && (
                <div className="banner warn"><Icon name="shield" size={18} /><div>No <b>HF_TOKEN</b> set — gated models (Llama, Gemma) will fail to download. Export it before starting the server.</div></div>
              )}
            </div>
          )}

          {cfg && prov && !prov.available && provider !== "openrouter" && provider !== "openai" && (
            <div className="banner warn" style={{ marginTop: 14 }}>
              <Icon name="bolt" size={18} />
              <div><b>{prov.label} isn't ready:</b> {prov.hint} Switch to <a style={{ textDecoration: "underline", cursor: "pointer" }} onClick={() => { setProvider("mock"); setOpen(true); }}>Demo mode</a> to explore the platform without a GPU.</div>
            </div>
          )}
          {isOR && (
            <div className={`banner ${keyOk ? "" : "warn"}`} style={{ marginTop: 14 }}>
              <Icon name={keyOk ? "bolt" : "key"} size={18} />
              <div>
                {keyOk ? <><b>Hosted API.</b> Tokens/s and latency include network and provider queueing, and the serving engine is invisible — so speculative decoding can't be observed. Quality scores are measured exactly as for any model (hosted providers may serve quantised builds, so results can vary by provider).</>
                  : <><b>OpenRouter key needed.</b> Open <a style={{ textDecoration: "underline", cursor: "pointer" }} onClick={() => setOpen(true)}>Run options</a> and paste a key, or export <span className="mono">OPENROUTER_API_KEY</span> and restart.</>}
                {orModels && !orModels.live && <div style={{ marginTop: 6 }}><Icon name="alert" size={14} /> Couldn't reach OpenRouter's model list — showing a built-in selection. Any valid slug can still be pasted.</div>}
              </div>
            </div>
          )}
          {cfg && provider === "mock" && (
            <div className="banner ok" style={{ marginTop: 14 }}>
              <Icon name="flask" size={18} />
              <div><b>Demo mode</b> — models are simulated (no GPU, no credentials). Quality, speed and failure modes are synthetic; use Modal or OpenRouter for real measurements.</div>
            </div>
          )}
          {err && <div className="banner err" style={{ marginTop: 14 }} role="alert"><Icon name="x" size={18} /><div>{err}</div></div>}
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
          <h2>Recent evaluations <Link to="/history" className="faint" style={{ fontSize: 12, letterSpacing: 0, textTransform: "none" }}>View all →</Link></h2>
          <div className="runs-list">
            {runs.slice(0, 8).map((r) => <RunRow key={r.id} r={r} />)}
          </div>
        </section>
      )}
    </>
  );
}

export function providerLabel(p?: string): string {
  return p === "mock" ? "Demo" : p === "openai" ? "Endpoint" : p === "openrouter" ? "OpenRouter" : "Modal";
}

export function RunRow({ r, selected, onToggle }: { r: RunSummary; selected?: boolean; onToggle?: (id: string) => void }) {
  const live = r.status === "running" || r.status === "queued";
  return (
    <div className={`run-wrap ${onToggle ? "selectable" : ""}`}>
      {onToggle && (
        <button className={`cbox ${selected ? "on" : ""}`} onClick={() => onToggle(r.id)} aria-label={`Select ${r.model.name} for comparison`} aria-pressed={selected} disabled={r.status !== "completed"}>
          {selected && <Icon name="check" size={13} stroke={3} />}
        </button>
      )}
      <Link to={`/runs/${r.id}`} className="run-row">
        <div>
          <div className="nm">{r.model.name}</div>
          <div className="sm">
            {providerLabel(r.options.provider)} · {r.options.stress ? `stress: ${r.options.stress}` : r.options.speculative && r.options.speculative !== "auto" ? `spec: ${r.options.speculative}` : "baseline"} · {timeAgo(r.created_at)}
          </div>
        </div>
        <div>
          {live ? <Badge tone="violet"><span className="spinner" style={{ width: 10, height: 10 }} /> running</Badge>
            : r.status === "completed" ? <Badge tone={verdictTone(r.verdict)}>{r.verdict === "ready" ? "Ready" : r.verdict === "caution" ? "Caution" : "Not ready"}</Badge>
            : <Badge tone="red">{r.status}</Badge>}
        </div>
        <div className="mono" style={{ color: r.scores ? scoreColor(r.scores.overall) : "var(--faint)", fontWeight: 700 }}>{r.scores ? `${Math.round(r.scores.overall)} / 100` : "—"}</div>
        <div className="mono muted">{r.decode_tps ? `${fmt(r.decode_tps, 0)} tok/s` : "—"}</div>
        <Icon name="arrow" size={16} />
      </Link>
    </div>
  );
}
