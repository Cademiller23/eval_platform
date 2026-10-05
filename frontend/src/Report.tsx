import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "./api";
import { IS_DEMO } from "./env";
import { providerLabel, savedOpenRouterKey } from "./Home";
import { Recommendations } from "./Recs";
import { TestsTable } from "./TestsTable";
import type { Report as R, RunFull } from "./types";
import { Badge, Bar, Icon, Radar, ScoreRing, Sparkline, fmt, pct, scoreColor } from "./ui";

export function Report({ run }: { run: RunFull }) {
  const r = run.report as R;
  const nav = useNavigate();
  const [parent, setParent] = useState<R | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const pid = r.options.parent_run_id;

  const copyMarkdown = async () => {
    try {
      const md = await (await fetch(`/api/runs/${r.run_id}/report.md`)).text();
      await navigator.clipboard.writeText(md);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch { setErr("Copy is blocked in this view. Open the full app to download the report."); }
  };

  useEffect(() => {
    if (!pid) return;
    api.run(pid).then((p) => setParent(p.report)).catch(() => {});
  }, [pid]);

  const rerun = async (spec?: string) => {
    setBusy(true); setErr(null);
    try {
      const o = run.options;
      const serverKey = o.openrouter_model ? (await api.config()).openrouter_key_set : false;
      const target = o.openrouter_model
        ? { openrouter_model: o.openrouter_model, openrouter_key: serverKey ? undefined : savedOpenRouterKey() || undefined }
        : o.model_id ? { model_id: o.model_id } : { custom_model: o.custom_model };
      const { run_id } = await api.start({
        ...target,
        provider: o.provider, gpu: o.gpu ?? undefined, quick: o.quick, max_model_len: o.max_model_len, stress: o.stress ?? undefined,
        speculative: o.openrouter_model ? "auto" : spec ?? o.speculative ?? "auto", speculative_custom: o.speculative_custom,
        parent_run_id: run.id,
      });
      nav(`/runs/${run_id}`);
    } catch (e) { setErr((e as Error).message); setBusy(false); }
  };

  const v = r.verdict;
  const s = r.scores;
  const canRerun = r.options.provider !== "openai";
  const apply = r.recommendations.speculative.apply;
  const passedTests = r.tests.filter((t) => t.passed).length;
  const passRate = r.tests.length ? (100 * passedTests) / r.tests.length : 0;

  return (
    <>
      {parent && <Compare a={parent} b={r} />}

      <div className={`card verdict ${v.label}`} style={{ marginTop: 24 }}>
        <ScoreRing value={s.overall} grade={s.grade} />
        <div>
          <div className="status"><i /> Overall verdict</div>
          <h2>{v.title}</h2>
          <p className="summary">{v.summary}</p>
          <div className="chips">
            {v.blockers.map((b) => <span key={b} className="chip bad"><Icon name="x" size={15} /> {b}</span>)}
            {v.warnings.map((b) => <span key={b} className="chip warn"><Icon name="spark" size={15} /> {b}</span>)}
            {v.strengths.map((b) => <span key={b} className="chip good"><Icon name="check" size={15} /> {b}</span>)}
          </div>
          <div className="actions">
            {canRerun && apply && <button className="btn primary" disabled={busy} onClick={() => rerun(apply.speculative)}><Icon name="bolt" size={16} /> Re-run with {apply.speculative.toUpperCase()}</button>}
            {canRerun && <button className="btn" disabled={busy} onClick={() => rerun()}><Icon name="refresh" size={16} /> Run again</button>}
            {IS_DEMO ? (
              <button className="btn" onClick={copyMarkdown}><Icon name="copy" size={16} /> {copied ? "Copied" : "Copy as Markdown"}</button>
            ) : (
              <>
                <a className="btn" href={`/api/runs/${r.run_id}/report.md`} download={`report-${r.run_id}.md`}><Icon name="download" size={16} /> Markdown</a>
                <a className="btn" href={`/api/runs/${r.run_id}/report.json`}><Icon name="download" size={16} /> JSON</a>
              </>
            )}
          </div>
          {err && <div className="banner err" style={{ marginTop: 12 }}>{err}</div>}
        </div>
      </div>

      {r.options.stress && <StressCheck r={r} />}
      {r.environment.hosted && (
        <div className="banner" style={{ marginTop: 16 }}>
          <Icon name="bolt" size={18} />
          <div><b>Hosted API run.</b> Tokens/s and latency include network and provider queueing{r.environment.providers_seen ? ` (served by ${Object.keys(r.environment.providers_seen).join(", ")})` : ""}; the engine is invisible, so speculative decoding can't be observed. Quality scores are unaffected.</div>
        </div>
      )}

      <div className="section">
        <h2>Scores</h2>
        <div className="row" style={{ alignItems: "stretch" }}>
          <div style={{ flex: "1 1 520px" }}>
            <div className="score-grid">
              {([["coherency", "Coherency", "shield"], ["coding", "Coding", "code"], ["math", "Math", "calc"], ["general", "General", "chat"], ["performance", "Speed", "bolt"]] as const).map(([k, label, ic]) => {
                const val = s[k];
                const d = r.domains[k];
                return (
                  <div className="card score-card" key={k}>
                    <div className="lbl"><span>{label}</span><Icon name={ic} size={15} /></div>
                    <div className="val" style={{ color: scoreColor(val) }}>{Math.round(val)}</div>
                    <Bar value={val} />
                    <div className="note faint">{d ? `${d.passed}/${d.total} tests passed` : k === "performance" ? `${fmt(r.performance.decode_tps_median, 0)} tok/s decode` : ""}</div>
                  </div>
                );
              })}
              <div className="card score-card">
                <div className="lbl"><span>Pass rate</span><Icon name="check" size={15} /></div>
                <div className="val" style={{ color: scoreColor(passRate) }}>{Math.round(passRate)}<small>%</small></div>
                <Bar value={passRate} />
                <div className="note faint">{passedTests} of {r.tests.length} tests overall</div>
              </div>
            </div>
          </div>
          <div className="card" style={{ flex: "1 1 300px", maxWidth: 380, display: "grid", placeItems: "center" }}>
            <Radar axes={[
              { label: "Coherency", value: s.coherency }, { label: "Coding", value: s.coding }, { label: "Math", value: s.math },
              { label: "General", value: s.general }, { label: "Speed", value: s.performance },
            ]} />
          </div>
        </div>
      </div>

      <div className="section">
        <h2>{r.environment.hosted ? "Speed & cost" : "Speed & hardware"}</h2>
        <div className="grid3">
          <div className="card">
            <h3>Decode throughput</h3>
            <p className="sub">Single stream, after the first token</p>
            <div className="big-stat" style={{ color: scoreColor(Math.min(100, ((r.performance.decode_tps_median ?? 0) / 60) * 100)) }}>{fmt(r.performance.decode_tps_median, 1)}<small>tok/s</small></div>
            <div className="bars-mini">
              {r.performance.decode_tps_runs.map((x, i) => <i key={i} title={`${x} tok/s`} style={{ height: `${(x / Math.max(...r.performance.decode_tps_runs, 1)) * 100}%` }} />)}
            </div>
            <div className="faint" style={{ fontSize: 12, marginTop: 8 }}>{r.performance.decode_tps_runs.length} runs · range {fmt(r.performance.decode_tps_min, 0)}–{fmt(r.performance.decode_tps_max, 0)}</div>
          </div>
          <div className="card">
            <h3>Latency & scaling</h3>
            <p className="sub">Time to first token and concurrency</p>
            <div className="kv">
              <div><span className="k">TTFT p50 / p95</span><span className="v">{fmt(r.performance.ttft_ms_p50, 0)} / {fmt(r.performance.ttft_ms_p95, 0)} ms</span></div>
              <div><span className="k">TTFT, ~1.5k-token prompt</span><span className="v">{fmt(r.performance.ttft_long_ms, 0, " ms")}</span></div>
              <div><span className="k">×{r.performance.concurrent?.n ?? 8} concurrent</span><span className="v">{fmt(r.performance.concurrent?.aggregate_tps, 0, " tok/s")}</span></div>
              <div><span className="k">Batching gain</span><span className="v">{r.performance.concurrent?.aggregate_tps && r.performance.decode_tps_median ? `${(r.performance.concurrent.aggregate_tps / r.performance.decode_tps_median).toFixed(1)}×` : "—"}</span></div>
              <div><span className="k">Cold start</span><span className="v">{r.environment.hosted ? "n/a (hosted)" : fmt(r.environment.cold_start_s, 0, " s")}</span></div>
            </div>
          </div>
          {r.environment.hosted ? (
            <div className="card">
              <h3>Cost & provider</h3>
              <p className="sub">What this evaluation cost through OpenRouter</p>
              <div className="big-stat">{r.usage?.cost_usd != null ? `$${r.usage.cost_usd < 0.1 ? r.usage.cost_usd.toFixed(4) : r.usage.cost_usd.toFixed(2)}` : "—"}<small>this run</small></div>
              <div className="kv" style={{ marginTop: 16 }}>
                <div><span className="k">Tokens (in / out)</span><span className="v">{(r.usage?.prompt_tokens ?? 0).toLocaleString()} / {(r.usage?.completion_tokens ?? 0).toLocaleString()}</span></div>
                <div><span className="k">Price per M (in / out)</span><span className="v">{r.environment.pricing?.prompt_per_m != null ? `$${r.environment.pricing.prompt_per_m} / $${r.environment.pricing.completion_per_m}` : "—"}</span></div>
                <div><span className="k">Served by</span><span className="v">{r.environment.providers_seen ? Object.entries(r.environment.providers_seen).map(([k, v]) => `${k} ×${v}`).join(", ") : "—"}</span></div>
                <div><span className="k">Weights</span><span className="v">{r.environment.open_weights ? "Open" : "Closed"}</span></div>
              </div>
            </div>
          ) : (
<div className="card">
              <h3>Hardware efficiency</h3>
              <p className="sub">Measured vs the memory-bandwidth ceiling</p>
              {r.performance.roofline ? (
                <>
                  <div className="big-stat">{pct(r.performance.roofline.efficiency)}<small>of roofline</small></div>
                  <div style={{ margin: "14px 0 10px" }}><Bar value={r.performance.roofline.efficiency * 100} color="var(--cyan)" /></div>
                  <div className="faint" style={{ fontSize: 12.5, lineHeight: 1.5 }}>Ceiling ≈ {fmt(r.performance.roofline.theoretical_tps, 0)} tok/s on {r.performance.roofline.gpu_count}× {r.performance.roofline.gpu}. Plain decoding cannot exceed it — only speculation, quantisation or a faster GPU can.</div>
                </>
              ) : <div className="faint">Roofline unavailable for this endpoint.</div>}
              <div className="kv" style={{ marginTop: 14 }}>
                <div><span className="k">GPU</span><span className="v">{r.environment.requested_gpu ?? r.environment.gpu ?? "—"}</span></div>
                <div><span className="k">Engine</span><span className="v">{r.environment.engine} {r.environment.engine_version}</span></div>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="section">
        <h2>Speculative decoding</h2>
        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
            <div>
              <h3 style={{ fontSize: 22, letterSpacing: "-0.025em" }}>{r.speculative.headline}</h3>
              <p className="sub" style={{ marginBottom: 0 }}>{r.speculative.hosted
                ? "Hosted APIs don't expose their engine, so nothing can be confirmed from outside."
                : `Read from the engine config, its metrics and how tokens arrive · confidence ${pct(r.speculative.confidence)}`}</p>
            </div>
            <Badge tone={r.speculative.status === "active" ? "green" : r.speculative.status === "not_detected" ? "amber" : r.speculative.status === "likely" ? "cyan" : undefined}>
              {r.speculative.status === "active" ? "ACTIVE" : r.speculative.status === "likely" ? "LIKELY" : r.speculative.status === "not_detected" ? "NOT IN USE" : "UNKNOWN"}
              {r.speculative.method ? ` · ${r.speculative.method}` : ""}
            </Badge>
          </div>
          <div className="grid2" style={{ marginTop: 18, alignItems: "start" }}>
            <div>
              {r.speculative.evidence.map((e, i) => (
                <div className="ev" key={i}>
                  <span className={`mk ${e.positive === true ? "t" : e.positive === false ? "f" : "n"}`}>{e.positive === true ? "✓" : e.positive === false ? "–" : "?"}</span>
                  <span className="src">{e.source}</span>
                  <span>{e.text}</span>
                </div>
              ))}
            </div>
            <div className="kv">
              {!r.speculative.hosted && <>
                <div><span className="k">Draft acceptance rate</span><span className="v">{pct(r.speculative.acceptance_rate)}</span></div>
                <div><span className="k">Tokens per engine step</span><span className="v">{fmt(r.speculative.mean_accepted_length, 2)}</span></div>
                <div><span className="k">Multi-token stream chunks</span><span className="v">{pct(r.speculative.multi_token_chunk_ratio)}</span></div>
              </>}
              <div><span className="k">Prompt-copy potential (n-gram)</span><span className="v">{r.speculative.copy_ratio_by_domain ? pct(Object.values(r.speculative.copy_ratio_by_domain).reduce((a, b) => a + b, 0) / Math.max(1, Object.keys(r.speculative.copy_ratio_by_domain).length)) : "—"}</span></div>
              <div><span className="k">Native MTP layers</span><span className="v">{r.speculative.native_mtp ? "Yes" : "No"}</span></div>
              {r.speculative.hosted && <div><span className="k">Weights</span><span className="v">{r.environment.open_weights ? "Open, can be self-hosted" : "Closed"}</span></div>}
            </div>
          </div>
        </div>
      </div>

      <div className="section">
        <h2>Coherency analysis</h2>
        <div className="card">
          <div className="coh-grid">
            {([
              ["Clean responses", r.coherency.clean_ratio, true], ["Garbled language", r.coherency.garble_rate, false], ["Repetition loops", r.coherency.repetition_rate, false],
              ["Special-token leaks", r.coherency.special_token_leak_rate, false], ["Runaway output", r.coherency.runaway_rate, false], ["Empty answers", r.coherency.empty_rate, false],
            ] as const).map(([label, val, good]) => (
              <div key={label}>
                <div className="label-s">{label}</div>
                <div className="num" style={{ fontFamily: "var(--rounded)", fontSize: 30, fontWeight: 700, letterSpacing: "-0.03em", margin: "6px 0 10px", color: good ? scoreColor(val * 100) : val === 0 ? "var(--green)" : val < 0.08 ? "var(--amber)" : "var(--red)" }}>{pct(val)}</div>
                <Bar value={good ? val * 100 : (1 - Math.min(1, val * 4)) * 100} />
              </div>
            ))}
          </div>
          {Object.keys(r.coherency.issue_kinds).length > 0 && (
            <div style={{ marginTop: 22 }}>
              <div className="label-s" style={{ marginBottom: 10 }}>Issues found across {r.coherency.responses} responses</div>
              <div className="chips">
                {Object.entries(r.coherency.issue_kinds).map(([k, v]) => <span key={k} className="chip"><b>{k.replace(/_/g, " ")}</b> <span className="muted">×{v.count}</span></span>)}
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="section">
        <h2>Make it better</h2>
        <Recommendations rec={r.recommendations} onApply={canRerun ? rerun : undefined} busy={busy} />
      </div>

      <div className="section">
        <h2>All tests</h2>
        <TestsTable tests={r.tests} />
      </div>

      <div className="footer">{providerLabel(r.options.provider)} · Run {r.run_id} · {r.duration_s}s · {new Date(r.generated_at).toLocaleString()} · {r.environment.command && <span className="mono">{r.environment.command.split("\n")[0].slice(0, 90)}</span>}</div>
    </>
  );
}

function Compare({ a, b }: { a: R; b: R }) {
  const ta = a.performance.decode_tps_median, tb = b.performance.decode_tps_median;
  const speed = ta && tb ? tb / ta : null;
  const dCoh = b.scores.coherency - a.scores.coherency;
  const dAll = b.scores.overall - a.scores.overall;
  const sign = (n: number) => (n > 0 ? "+" : "") + n.toFixed(1);
  return (
    <div className="delta" style={{ marginTop: 20 }}>
      <Icon name="refresh" size={22} />
      <div><div className="d" style={{ color: speed && speed >= 1.1 ? "var(--green)" : speed && speed < 0.95 ? "var(--red)" : "var(--text)" }}>{speed ? `${speed.toFixed(2)}×` : "—"}</div><div className="l">decode speed vs previous run ({fmt(ta, 0)} → {fmt(tb, 0)} tok/s)</div></div>
      <div><div className="d" style={{ color: dCoh >= -1 ? "var(--green)" : "var(--red)" }}>{sign(dCoh)}</div><div className="l">coherency score change</div></div>
      <div><div className="d">{sign(dAll)}</div><div className="l">overall score change</div></div>
      <div style={{ flex: 1, minWidth: 220, fontSize: 13.5, color: "var(--muted)" }}>
        Compared with <b style={{ color: "var(--text)" }}>{a.options.speculative === "auto" || a.options.speculative === "none" ? "baseline" : a.options.speculative}</b> →
        <b style={{ color: "var(--text)" }}> {b.options.speculative === "auto" || b.options.speculative === "none" ? "baseline" : b.options.speculative}</b>.{" "}
        {speed && speed >= 1.2 && dCoh >= -3 ? "Faster with no coherency loss — keep it." : speed && speed < 1.05 ? "No meaningful speed-up on this workload." : dCoh < -3 ? "Quality dropped — investigate before shipping." : ""}
      </div>
    </div>
  );
}

export { Sparkline };


function StressCheck({ r }: { r: R }) {
  const c = r.coherency;
  const kind = r.options.stress;
  const fired = kind === "garble" ? c.garble_rate >= 0.3 || c.severe_rate >= 0.4 : c.repetition_rate >= 0.2 || c.runaway_rate >= 0.2 || c.severe_rate >= 0.3;
  return (
    <div className={`banner ${fired ? "ok" : "err"}`} style={{ marginTop: 16 }} role="status">
      <Icon name={fired ? "check" : "alert"} size={18} />
      <div>
        <b>Detector self-check — {kind === "garble" ? "temperature 2.0" : "negative repetition penalties"}.</b>{" "}
        {fired
          ? <>Working as intended: {kind === "garble" ? `garbling was flagged in ${pct(c.garble_rate)} of responses` : `repetition/runaway output was flagged in ${pct(Math.max(c.repetition_rate, c.runaway_rate))} of responses`}, {pct(c.severe_rate)} of all responses had serious problems, and the verdict dropped to “{r.verdict.title}”. The same model without the stress control is the baseline to compare against.</>
          : <>The corruption was <b>not</b> reliably flagged ({pct(c.severe_rate)} serious). The provider may have clamped the settings — check a few responses in the table below before trusting this run.</>}
      </div>
    </div>
  );
}
