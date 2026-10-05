import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "./api";
import { IS_DEMO } from "./env";
import { Picker, catalogEntries, openrouterEntries, type Selection } from "./ModelPicker";
import type { AppConfig, ModelInfo, OrModels, OrStatus, RunOptions, RunSummary } from "./types";
import { Badge, Icon, Segmented, Switch, fmt, scoreColor, timeAgo, verdictTone, type IconName } from "./ui";

const FEATURES: { icon: IconName; from: string; to: string; title: string; text: string }[] = [
  { icon: "shield", from: "#5de0a0", to: "#16a34a", title: "Coherency & garble", text: "Catches gibberish, mojibake, repetition loops, leaked chat-template tokens, language drift, runaway reasoning and long-context failures." },
  { icon: "code", from: "#4facfe", to: "#0a64e0", title: "Coding", text: "The model writes real Python, which runs against hidden unit tests in a sandbox: LRU caches, interval merging, bug-fixing and more." },
  { icon: "calc", from: "#b48dff", to: "#6a3de8", title: "Mathematics", text: "Word problems, algebra, combinatorics, number theory and probability, graded on the exact final answer." },
  { icon: "chat", from: "#ffd75e", to: "#f59e0b", title: "General purpose", text: "Instruction following, strict JSON, constraints, knowledge, reasoning, summarising and translation." },
  { icon: "bolt", from: "#5ee7ff", to: "#0a8fb0", title: "Speed & speculation", text: "Decode tokens per second, time to first token, concurrency, hardware efficiency, and whether speculative decoding is really running." },
  { icon: "spark", from: "#ff8cc6", to: "#d6246e", title: "A plan to improve it", text: "Model-specific steps to add speculative decoding, run faster and answer more coherently, each tied to evidence from your run." },
];

const KEY_STORE = "coherence-lab.openrouter-key";
const readKey = () => { try { return sessionStorage.getItem(KEY_STORE) ?? ""; } catch { return ""; } };
export const savedOpenRouterKey = readKey;

const PROVIDER_SHORT: Record<string, string> = { modal: "Modal", openrouter: "OpenRouter", openai: "Endpoint", mock: "Demo" };

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
    if (!keyOk) { setOpen(true); setErr("Enter an OpenRouter API key in Options, or set OPENROUTER_API_KEY on the server."); return; }
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
    PROVIDER_SHORT[provider] ?? "…",
    ...(isOR ? [] : [gpu || "recommended GPU", cfg?.speculative_modes.find((m) => m.id === spec)?.label ?? spec]),
    quick ? "quick suite" : "full suite",
    ...(multi ? ["compare"] : []),
    ...(stress ? [`self-check: ${stress}`] : []),
  ].join(" · ");

  const eta = isOR ? "about 1 to 4 minutes" : provider === "mock" ? "under a minute" : quick ? "about 3 to 8 minutes" : "about 5 to 15 minutes";

  return (
    <>
      <section className="hero">
        <div className="eyebrow">Model evaluation</div>
        <h1>Is your model <span className="gradient-text">ready to ship?</span></h1>
        <p className="lead">Pick a model. Coherence Lab starts it, tests how coherent it stays across coding, maths and everyday tasks, measures how fast it decodes, and spots speculative decoding. You get a scorecard and the exact steps to make it faster and better.</p>

        <Picker
          entries={entries}
          onSelect={start}
          busy={busy}
          loading={isOR && orLoading}
          disabled={!cfg}
          multi={multi}
          searchPlaceholder={isOR ? "Search, or paste vendor/model" : "Search, or paste a Hugging Face org/name"}
          customPattern={isOR ? /^~?[\w.-]+\/[\w.:~-]+$/ : /^[\w.-]+\/[\w.-]+$/}
          customLabel={isOR ? "Evaluate OpenRouter model" : "Evaluate custom model"}
          placeholder={isOR ? "Choose an OpenRouter model to evaluate" : "Choose a model to evaluate"}
          footer={isOR ? "Choosing a model starts the evaluation through OpenRouter" : undefined}
        />
        <div className="hint-line">{multi ? "Compare mode: tick up to 4 models, then start them together" : "Choosing a model starts the evaluation straight away"} · {IS_DEMO ? "a recorded run plays back in about half a minute" : eta}{isOR ? " · cost depends on the model (prices are in the list)" : ""}</div>

        <div className="opts">
          <button className="opts-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
            <Icon name="gear" size={15} /> Options <span className="faint">· {summary}</span> <Icon name="chev" size={14} />
          </button>

          {open && cfg && (
            <div className="sheet">
              <div>
                <div className="sheet-title">Run on</div>
                <div className="group">
                  <div className="cell stack">
                    <Segmented
                      label="Where to run the model"
                      value={provider}
                      onChange={setProvider}
                      options={cfg.providers.map((p) => ({ id: p.id, label: PROVIDER_SHORT[p.id] ?? p.label, title: p.hint }))}
                    />
                    {prov && <div className="hint">{prov.hint}</div>}
                  </div>
                </div>
              </div>

              {isOR && (
                <div>
                  <div className="sheet-title">OpenRouter</div>
                  <div className="group">
                    {cfg.openrouter_key_set ? (
                      <div className="cell">
                        <div className="text">
                          <div className="label">API key</div>
                          <div className="hint">{IS_DEMO ? "Not needed in the preview." : "Found in OPENROUTER_API_KEY on the server."}</div>
                        </div>
                        <div className="ctrl key-row">
                          {orStatus && (orStatus.valid
                            ? <span className="muted" style={{ fontSize: 13 }}>Valid{orStatus.remaining != null ? ` · $${orStatus.remaining.toFixed(2)} left` : orStatus.limit == null ? " · no spend limit" : ""}</span>
                            : <span style={{ color: "var(--red-ink)", fontSize: 13 }}>{orStatus.error ?? "Key rejected"}</span>)}
                          {!IS_DEMO && <button className="btn small" onClick={checkKey}>Check key</button>}
                        </div>
                      </div>
                    ) : (
                      <div className="cell stack">
                        <div className="label">API key</div>
                        <input className="input" type="password" autoComplete="off" spellCheck={false} placeholder="sk-or-v1-…" value={orKey} onChange={(e) => setOrKey(e.target.value)} aria-label="OpenRouter API key" id="or-key" />
                        <div className="hint">Stays in this browser tab (session storage) and is sent only with your run. The server never stores it. You can also export OPENROUTER_API_KEY before starting the server.</div>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {provider === "openai" && (
                <div>
                  <div className="sheet-title">Endpoint</div>
                  <div className="group">
                    <div className="cell"><div className="label">Base URL</div><input className="input ctrl" style={{ width: "62%" }} value={ep.base_url} onChange={(e) => setEp({ ...ep, base_url: e.target.value })} placeholder="http://localhost:8000/v1" aria-label="Base URL" /></div>
                    <div className="cell"><div className="label">API key</div><input className="input ctrl" style={{ width: "62%" }} type="password" value={ep.api_key} onChange={(e) => setEp({ ...ep, api_key: e.target.value })} placeholder="optional" aria-label="Endpoint API key" /></div>
                    <div className="cell"><div className="label">Model name</div><input className="input ctrl" style={{ width: "62%" }} value={ep.model} onChange={(e) => setEp({ ...ep, model: e.target.value })} placeholder="detected automatically" aria-label="Served model name" /></div>
                  </div>
                </div>
              )}

              {!isOR && (
                <div>
                  <div className="sheet-title">Hardware</div>
                  <div className="group">
                    {provider !== "openai" && (
                      <div className="cell">
                        <div className="text">
                          <div className="label">GPU</div>
                          <div className="hint">{IS_DEMO ? "The preview uses each model's recommended GPU." : "Decode speed is memory-bandwidth bound: more GB/s, more tokens/s."}</div>
                        </div>
                        <select className="select ctrl" value={gpu} onChange={(e) => setGpu(e.target.value)} aria-label="GPU" disabled={IS_DEMO}>
                          <option value="">Recommended</option>
                          {cfg.gpus.map((g) => <option key={g.id} value={g.id}>{g.id} · {g.mem_gb} GB</option>)}
                          <option value="H100:2">H100 × 2</option><option value="H100:4">H100 × 4</option><option value="H200:2">H200 × 2</option><option value="H200:8">H200 × 8</option>
                        </select>
                      </div>
                    )}
                    <div className="cell">
                      <div className="text">
                        <div className="label">Speculative decoding</div>
                        <div className="hint">{cfg.speculative_modes.find((m) => m.id === spec)?.hint}</div>
                      </div>
                      <select className="select ctrl" value={spec} onChange={(e) => setSpec(e.target.value)} aria-label="Speculative decoding">
                        {cfg.speculative_modes.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                      </select>
                    </div>
                    {spec === "custom" && (
                      <div className="cell stack">
                        <div className="label">--speculative-config (JSON)</div>
                        <textarea className="input mono" rows={3} value={specJson} onChange={(e) => setSpecJson(e.target.value)} aria-label="Speculative config JSON" />
                      </div>
                    )}
                  </div>
                </div>
              )}

              <div>
                <div className="sheet-title">Evaluation</div>
                <div className="group">
                  <div className="cell">
                    <div className="text"><div className="label">Quick mode</div><div className="hint">{IS_DEMO ? "Every recording in the preview is a full run." : `${cfg.suite.quick} representative tests instead of ${cfg.suite.full}. Same pipeline, faster result.`}</div></div>
                    <div className="ctrl"><Switch on={quick} onChange={setQuick} label="Quick mode" disabled={IS_DEMO} /></div>
                  </div>
                  <div className="cell">
                    <div className="text"><div className="label">Compare several models</div><div className="hint">Tick up to 4 models and get a side-by-side leaderboard.</div></div>
                    <div className="ctrl"><Switch on={multi} onChange={setMulti} label="Compare mode" /></div>
                  </div>
                  <div className="cell">
                    <div className="text">
                      <div className="label">Detector self-check</div>
                      <div className="hint">Deliberately breaks decoding on the same model to prove that garbling and loops get flagged. Scores are expected to drop.{IS_DEMO && " In the preview, run it on OpenRouter models."}</div>
                    </div>
                    <select className="select ctrl" value={stress} onChange={(e) => setStress(e.target.value as "" | "garble" | "loop")} aria-label="Detector self-check" disabled={IS_DEMO && !isOR}>
                      <option value="">Off</option>
                      <option value="garble">Garble (temperature 2.0)</option>
                      <option value="loop">Loops (negative penalties)</option>
                    </select>
                  </div>
                </div>
                {provider === "modal" && !cfg.hf_token_set && <div className="sheet-note">No HF_TOKEN is set, so gated models (Llama, Gemma) will fail to download. Export it before starting the server.</div>}
              </div>
            </div>
          )}

          <div style={{ display: "grid", gap: 12, marginTop: 16 }}>
            {cfg && prov && !prov.available && provider !== "openrouter" && provider !== "openai" && !IS_DEMO && (
              <div className="banner warn">
                <Icon name="bolt" size={18} />
                <div><b>{prov.label} isn't ready.</b> {prov.hint} Switch to <a onClick={() => { setProvider("mock"); setOpen(true); }}>Demo mode</a> to explore without a GPU.</div>
              </div>
            )}
            {isOR && (
              <div className={`banner ${keyOk ? "" : "warn"}`}>
                <Icon name={keyOk ? "bolt" : "key"} size={18} />
                <div>
                  {keyOk
                    ? <><b>Hosted API.</b> Tokens per second and latency include network and provider queueing, and the serving engine is hidden, so speculative decoding can't be observed. Quality scores are measured exactly as for any model; hosted providers may serve quantised builds, so results can vary by provider.</>
                    : <><b>OpenRouter key needed.</b> Open <a onClick={() => setOpen(true)}>Options</a> and paste a key, or export <span className="mono">OPENROUTER_API_KEY</span> and restart.</>}
                  {orModels && !orModels.live && <div style={{ marginTop: 6 }}>Couldn't reach OpenRouter's model list, so a built-in selection is shown. Any valid slug can still be pasted.</div>}
                </div>
              </div>
            )}
            {cfg && provider === "mock" && (
              <div className="banner ok">
                <Icon name="flask" size={18} />
                <div><b>Demo mode.</b> Models are simulated, with no GPU or credentials. Quality, speed and failure modes are synthetic; use Modal or OpenRouter for real measurements.</div>
              </div>
            )}
            {IS_DEMO && (
              <div className="banner">
                <Icon name="spark" size={18} />
                <div><b>This is an interactive preview.</b> It runs the real interface against recorded results, with no server attached. Speeds, prices and providers are simulated. Pick any model to watch a full evaluation play out.</div>
              </div>
            )}
            {err && <div className="banner err" role="alert"><Icon name="alert" size={18} /><div>{err}</div></div>}
          </div>
        </div>
      </section>

      <section className="section">
        <h2>What every run measures</h2>
        <div className="bento">
          {FEATURES.map((f) => (
            <div className="card feature" key={f.title}>
              <div className="ico" style={{ background: `linear-gradient(145deg, ${f.from}, ${f.to})` } as CSSProperties}><Icon name={f.icon} size={22} /></div>
              <h3>{f.title}</h3>
              <p>{f.text}</p>
            </div>
          ))}
        </div>
      </section>

      {runs.length > 0 && (
        <section className="section">
          <h2>Recent evaluations <Link to="/history">See all</Link></h2>
          <div className="runs-list">
            {runs.slice(0, 6).map((r) => <RunRow key={r.id} r={r} />)}
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
            {providerLabel(r.options.provider)} · {r.options.stress ? `self-check: ${r.options.stress}` : r.options.speculative && r.options.speculative !== "auto" ? `speculative: ${r.options.speculative}` : "baseline"} · {timeAgo(r.created_at)}
          </div>
        </div>
        <div className="status-cell">
          {live ? <Badge tone="violet"><span className="spinner" style={{ width: 10, height: 10 }} /> Running</Badge>
            : r.status === "completed" ? <Badge tone={verdictTone(r.verdict)}>{r.verdict === "ready" ? "Ready" : r.verdict === "caution" ? "Caution" : "Not ready"}</Badge>
            : <Badge tone="red">{r.status}</Badge>}
          {r.scores && <div className="m-score num" style={{ color: scoreColor(r.scores.overall) }}>{Math.round(r.scores.overall)} / 100</div>}
        </div>
        <div className="num" style={{ color: r.scores ? scoreColor(r.scores.overall) : "var(--text-3)", fontWeight: 600 }}>{r.scores ? `${Math.round(r.scores.overall)} / 100` : "—"}</div>
        <div className="num muted">{r.decode_tps ? `${fmt(r.decode_tps, 0)} tok/s` : "—"}</div>
        <Icon name="chevr" size={16} />
      </Link>
    </div>
  );
}
