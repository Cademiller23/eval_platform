import { useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { samplingOk, type Finding, type Report as R, type SamplingReport, type SweepSetting, type SystemPrompts, type TempPoint, type TestResult } from "./types";
import { Badge, Bar, CodeBlock, Icon, fmt, pct, scoreColor, type IconName } from "./ui";

/* ======================================================================================
   Report sections for the two newest measurements:
     • System prompts   (suite/system_prompts.py)  — does the model do what its system prompt says?
     • Hyperparameters  (suite/sampling.py)        — which decoding settings suit it, and are they honoured?
   ====================================================================================== */

type Tone = "good" | "warn" | "bad" | "neutral";

const RANK: Record<Finding["level"], number> = { bad: 0, warn: 1, info: 2, good: 3 };
const LEVEL_ICON: Record<Finding["level"], IconName> = { good: "check", info: "info", warn: "alert", bad: "x" };

export function Findings({ items }: { items: Finding[] }) {
  const sorted = [...items].sort((a, b) => RANK[a.level] - RANK[b.level]);
  if (!sorted.length) return null;
  return (
    <div className="findings" role="list">
      {sorted.map((f, i) => (
        <div role="listitem" key={i} className={`finding ${f.level}`}><Icon name={LEVEL_ICON[f.level]} size={16} /><span>{f.text}</span></div>
      ))}
    </div>
  );
}

function Kpi({ label, value, unit, caption, tone = "neutral", hint }: { label: string; value: ReactNode; unit?: string; caption?: ReactNode; tone?: Tone; hint?: string }) {
  return (
    <div className={`kpi ${tone}`} title={hint}>
      <div className="k">{label}</div>
      <div className="v num">{value}{unit && <small>{unit}</small>}</div>
      {caption && <div className="c">{caption}</div>}
    </div>
  );
}

function ScoreTile({ label, value, caption }: { label: string; value: number; caption: string }) {
  return (
    <div className="kpi score">
      <div className="k">{label}</div>
      <div className="v num" style={{ color: scoreColor(value) }}>{Math.round(value)}<small>/ 100</small></div>
      <Bar value={value} />
      <div className="c">{caption}</div>
    </div>
  );
}

const Ck = ({ ok }: { ok: boolean | null | undefined }) => (ok == null ? <span className="faint">—</span> : <span className={`ck ${ok ? "ok" : "no"}`} aria-label={ok ? "passed" : "failed"}>{ok ? "✓" : "✗"}</span>);

function NotRun({ id, title, icon, text }: { id: string; title: string; icon: IconName; text: string }) {
  return (
    <div className="section" id={id}>
      <h2>{title}</h2>
      <div className="card notrun"><Icon name={icon} size={20} /><div>{text}</div></div>
    </div>
  );
}

const signed = (n: number, d = 0) => `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(d)}`;

/** Fires the TestsTable's "open this test" handler so a result can be inspected in full. */
export const openTest = (id: string) => window.dispatchEvent(new CustomEvent("open-test", { detail: id }));

/* ------------------------------------------------------------------ system prompts */
const CAT_ICON: Record<string, IconName> = {
  adherence: "check", persistence: "refresh", hierarchy: "layers", injection: "shield", leakage: "lock", scope: "compare", capacity: "gauge", robustness: "spark", identity: "chat",
};

function worstFirst<T extends { score: number }>(xs: T[]): T[] {
  return [...xs].sort((a, b) => a.score - b.score);
}

/** Failing tests first (that is what needs attention); the passing ones sit behind a disclosure. */
function TestList({ ids, byId }: { ids: string[]; byId: Map<string, TestResult> }) {
  const rows = ids.map((id) => byId.get(id)).filter((t): t is TestResult => !!t);
  const bad = rows.filter((t) => t.error || !t.passed);
  const good = rows.filter((t) => !t.error && t.passed);
  const item = (t: TestResult) => (
    <li key={t.id}>
      <button type="button" onClick={() => openTest(t.id)} title="Open this test below">
        <i className={`dot ${t.error ? "err" : t.passed ? "ok" : "no"}`} aria-hidden />
        <span className="nm">{t.name}</span>
        <span className="faint st">{t.error ? "error" : t.passed ? "pass" : "fail"}</span>
      </button>
    </li>
  );
  return (
    <>
      {bad.length > 0 && <ul className="tlist">{bad.map(item)}</ul>}
      {good.length > 0 && (
        <details className="tgood">
          <summary>{bad.length ? `${good.length} passed` : `All ${good.length} tests passed`}</summary>
          <ul className="tlist">{good.map(item)}</ul>
        </details>
      )}
    </>
  );
}

export function SystemPromptsSection({ r }: { r: R }) {
  const sp = r.system_prompts as SystemPrompts | null | undefined;
  const byId = useMemo(() => new Map(r.tests.map((t) => [t.id, t])), [r.tests]);
  if (!sp) return <NotRun id="system-prompts" title="System prompts" icon="prompt" text={r.options.system_prompts === undefined ? "This run was made before the system-prompt suite existed. Run the evaluation again to include it." : "The system-prompt suite was turned off for this run, so it is not part of the overall score."} />;
  const m = sp.metrics;
  const attackN = m.injection_asr != null ? Math.round(m.injection_asr * m.injection_attacks) : 0;
  const leakN = m.leaked.length;
  const cats = Object.entries(sp.categories);
  const top = m.capacity_levels.length ? Math.max(...m.capacity_levels.map((l) => l.rules)) : null;
  const firstBad = m.capacity_levels.find((l) => !l.all);
  const position = Object.entries(m.position);
  return (
    <div className="section" id="system-prompts">
      <h2>System prompts</h2>
      <p className="mod-intro">Does the model do what its system prompt says, keep doing it for the whole conversation, resist instructions hidden in the data it reads, and keep its secrets? {sp.tests} programmatically graded tests, each with a canary or a closed answer so a pass cannot be faked.</p>

      {!sp.role.supported && (
        <div className="banner warn" style={{ marginBottom: 14 }}>
          <Icon name="alert" size={18} />
          <div><b>This chat template has no system role.</b> {sp.role.folded ? "The platform folded each system prompt into the first user message, which is the standard workaround, and graded the result." : "System prompts could not be delivered, so these results understate what the model can do."} Real deployments of this model need the same workaround.</div>
        </div>
      )}

      <div className="kpis">
        <ScoreTile label="System-prompt score" value={sp.score} caption={`${cats.reduce((a, [, c]) => a + c.passed, 0)} of ${cats.reduce((a, [, c]) => a + c.total, 0)} tests passed`} />
        <Kpi label="Injection success" value={m.injection_asr == null ? "—" : pct(m.injection_asr)} tone={m.injection_asr == null ? "neutral" : m.injection_asr === 0 ? "good" : m.injection_asr <= 0.2 ? "warn" : "bad"}
          caption={m.injection_asr == null ? "not measured" : `${attackN} of ${m.injection_attacks} hidden-instruction attacks worked. Lower is better.`} hint="Attack success rate: how often an instruction planted in the data (a review, a web page, an invoice) hijacked the model." />
        <Kpi label="Secret leaks" value={m.leak_rate == null ? "—" : pct(m.leak_rate)} tone={m.leak_rate == null ? "neutral" : m.leak_rate === 0 ? (m.verbatim_leaks.length ? "warn" : "good") : m.leak_rate <= 0.25 ? "warn" : "bad"}
          caption={m.leak_rate == null ? "not measured" : `${leakN} of ${m.leak_attacks} extraction attempts got something out${m.verbatim_leaks.length ? `; ${m.verbatim_leaks.length} reproduced the prompt verbatim` : ""}.`} hint="Direct, translated, encoded, role-play and fake-authority attempts to extract a confidential code and the instructions themselves." />
        <Kpi label="Rule capacity" value={m.capacity ?? "—"} unit={m.capacity != null ? "rules" : undefined} tone={m.capacity == null ? "neutral" : m.capacity >= 15 ? "good" : m.capacity >= 8 ? "warn" : "bad"}
          caption={m.capacity == null ? "not measured" : top && m.capacity >= top ? `All ${top} simultaneous rules followed in one reply.` : m.capacity === 0 ? `Drops rules even with ${firstBad?.rules ?? 3}.` : firstBad ? `Starts dropping rules at ${firstBad.rules}.` : "Most simultaneous rules followed in one reply."} hint="The largest rule set (3, 8, 15 or 25 checkable rules) the model followed completely in a single reply." />
        <Kpi label="Over-refusal" value={m.over_refusal_rate == null ? "—" : pct(m.over_refusal_rate)} tone={m.over_refusal_rate == null ? "neutral" : m.over_refusal_rate === 0 ? "good" : m.over_refusal_rate <= 0.25 ? "warn" : "bad"}
          caption={m.over_refusal_rate == null ? "not measured" : m.over_refusals.length ? `Declined ${m.over_refusals.length} legitimate request${m.over_refusals.length > 1 ? "s" : ""}.` : "Helped with every legitimate request."} hint="Legitimate requests that were wrongly declined. A model that refuses everything would score perfectly on leaks, so this keeps it honest." />
      </div>

      <div className="grid2 mt" style={{ alignItems: "start" }}>
        <div className="card">
          <h3>Rule capacity</h3>
          <p className="sub">Checkable rules given at once; the reply must satisfy all of them</p>
          {m.capacity_levels.length ? (
            <>
              <div className="ladder">
                {m.capacity_levels.map((l) => {
                  const f = l.satisfied / l.rules;
                  return (
                    <div className="rung" key={l.rules}>
                      <div className="n">{l.rules} rules</div>
                      <Bar value={f * 100} color={l.all ? "var(--green)" : f >= 0.75 ? "var(--orange)" : "var(--red)"} />
                      <div className="x num"><Ck ok={l.all} /> {l.satisfied}/{l.rules}</div>
                    </div>
                  );
                })}
              </div>
              {firstBad && firstBad.failed.length > 0 && (
                <div className="faint note-s">Dropped at {firstBad.rules} rules: {firstBad.failed.slice(0, 4).join(" · ")}{firstBad.failed.length > 4 ? ` · +${firstBad.failed.length - 4} more` : ""}</div>
              )}
            </>
          ) : <div className="faint">Not measured.</div>}
        </div>

        <div className="card">
          <h3>Robustness checks</h3>
          <p className="sub">The same rule, delivered differently</p>
          <div className="kv">
            <div><span className="k">Paraphrase consistency</span><span className="v">{m.paraphrase_consistent == null ? "—" : <><Ck ok={m.paraphrase_consistent} /> {m.paraphrase_passed} of {m.paraphrase_total} wordings obeyed</>}</span></div>
            {m.placement.map((p) => (
              <div key={p.rule}><span className="k">“{p.rule}” rule as system / as user message</span><span className="v"><Ck ok={p.system} /> <span className="faint">/</span> <Ck ok={p.user} /></span></div>
            ))}
            {position.length > 0 && <div><span className="k">Rule at start / middle / end of a long prompt</span><span className="v">{position.map(([k, v], i) => <span key={k} title={k}>{i > 0 && <span className="faint"> / </span>}<Ck ok={v} /></span>)}</span></div>}
            <div><span className="k">Decay over a conversation</span><span className="v">{m.persistence_drop == null ? "—" : m.persistence_drop > 15 ? <span style={{ color: "var(--red-ink)" }}>{signed(m.persistence_drop, 0)} pts vs single turn</span> : m.persistence_drop > 5 ? <span style={{ color: "var(--orange-ink)" }}>{signed(m.persistence_drop, 0)} pts vs single turn</span> : "None measured"}</span></div>
          </div>
        </div>
      </div>

      <h3 className="sub-h">Categories</h3>
      <div className="cat-grid">
        {worstFirst(cats.map(([key, c]) => ({ key, ...c }))).map((c) => (
          <div className="card cat" key={c.key}>
            <div className="cat-h">
              <span className="cat-ico"><Icon name={CAT_ICON[c.key] ?? "check"} size={18} /></span>
              <div><h4>{c.label}</h4><div className="w">{Math.round(c.weight * 100)}% of this score · {c.passed}/{c.total} passed</div></div>
              <div className="cat-score num" style={{ color: scoreColor(c.score) }}>{Math.round(c.score)}</div>
            </div>
            <p>{c.blurb}</p>
            <Bar value={c.score} />
            <TestList ids={c.tests} byId={byId} />
          </div>
        ))}
      </div>

      {sp.errors > 0 && <div className="banner warn" style={{ marginTop: 14 }}><Icon name="alert" size={18} /><div>{sp.errors} system-prompt test{sp.errors > 1 ? "s" : ""} could not complete (provider errors) and {sp.errors > 1 ? "are" : "is"} left out of the score.</div></div>}
      <Findings items={sp.findings} />
    </div>
  );
}

/* ------------------------------------------------------------------ hyperparameters */
function useWidth<T extends HTMLElement>(initial: number): [RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(initial);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const set = () => setW(Math.max(280, Math.round(el.getBoundingClientRect().width)));
    set();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(set);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

function TempChart({ points, ceiling, breaks }: { points: TempPoint[]; ceiling: number | null; breaks: number | null }) {
  const [ref, width] = useWidth<HTMLDivElement>(640);
  const pts = useMemo(() => [...points].sort((a, b) => a.t - b.t), [points]);
  const H = width < 520 ? 250 : 300;
  const mg = { l: 42, r: 16, t: 24, b: 42 };
  const iw = width - mg.l - mg.r, ih = H - mg.t - mg.b;
  const tmax = Math.max(0.1, ...pts.map((p) => p.t));
  const x = (t: number) => mg.l + (t / tmax) * iw;
  const y = (v: number) => mg.t + (1 - Math.max(0, Math.min(1, v))) * ih;
  const path = (get: (p: TempPoint) => number | null | undefined) => {
    const seg = pts.filter((p) => get(p) != null).map((p) => [x(p.t), y(get(p)!)] as const);
    return seg.map(([a, b], i) => `${i ? "L" : "M"}${a.toFixed(1)},${b.toFixed(1)}`).join(" ");
  };
  const ci = pts.filter((p) => p.acc_ci);
  const band = ci.length >= 2
    ? `M${ci.map((p) => `${x(p.t).toFixed(1)},${y(p.acc_ci![1]).toFixed(1)}`).join(" L")} L${[...ci].reverse().map((p) => `${x(p.t).toFixed(1)},${y(p.acc_ci![0]).toFixed(1)}`).join(" L")} Z`
    : "";
  let lastX = -1e9;
  const ticks = pts.map((p) => p.t).filter((t) => { const px = x(t); if (px - lastX >= 30) { lastX = px; return true; } return false; });
  const desc = pts.map((p) => `T ${p.t}: accuracy ${p.acc == null ? "n/a" : Math.round(p.acc * 100) + "%"}, clean ${p.clean == null ? "n/a" : Math.round(p.clean * 100) + "%"}`).join("; ");
  return (
    <div className="chart" ref={ref}>
      <svg viewBox={`0 0 ${width} ${H}`} width={width} height={H} role="img" aria-label={`Quality against temperature. ${desc}`}>
        {[0, 0.25, 0.5, 0.75, 1].map((g) => (
          <g key={g}>
            <line className="gl" x1={mg.l} x2={width - mg.r} y1={y(g)} y2={y(g)} />
            <text x={mg.l - 8} y={y(g)} textAnchor="end" dominantBaseline="middle">{Math.round(g * 100)}%</text>
          </g>
        ))}
        {ticks.map((t) => <text key={t} x={x(t)} y={H - mg.b + 18} textAnchor="middle">{Number.isInteger(t) ? t : t.toFixed(1)}</text>)}
        <text className="at" x={mg.l + iw / 2} y={H - 6} textAnchor="middle">temperature</text>
        {breaks != null && <rect x={x(breaks)} y={mg.t} width={Math.max(0, x(tmax) - x(breaks))} height={ih} style={{ fill: "var(--red)", fillOpacity: 0.07 }} />}
        {ceiling != null && (
          <g>
            <line x1={x(ceiling)} x2={x(ceiling)} y1={mg.t - 4} y2={mg.t + ih} style={{ stroke: "var(--green)" }} strokeWidth="1.5" strokeDasharray="4 4" />
            <text x={x(ceiling) + (x(ceiling) > width * 0.68 ? -6 : 6)} y={mg.t - 9} textAnchor={x(ceiling) > width * 0.68 ? "end" : "start"} style={{ fill: "var(--green-ink)" }} fontWeight="600">safe up to {ceiling}</text>
          </g>
        )}
        {band && <path d={band} style={{ fill: "var(--accent)", fillOpacity: 0.13 }} />}
        <path d={path((p) => p.diversity)} fill="none" style={{ stroke: "var(--purple)" }} strokeWidth="2" strokeDasharray="5 4" strokeLinecap="round" strokeLinejoin="round" />
        <path d={path((p) => p.clean)} fill="none" style={{ stroke: "var(--green)" }} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
        <path d={path((p) => p.acc)} fill="none" style={{ stroke: "var(--accent)" }} strokeWidth="2.7" strokeLinecap="round" strokeLinejoin="round" />
        {pts.map((p) => p.acc != null && (
          <circle key={p.t} cx={x(p.t)} cy={y(p.acc)} r="4.2" style={{ fill: "var(--accent)", stroke: "var(--surface)" }} strokeWidth="2">
            <title>{`T ${p.t} · accuracy ${Math.round(p.acc * 100)}%${p.acc_ci ? ` (95% CI ${Math.round(p.acc_ci[0] * 100)}–${Math.round(p.acc_ci[1] * 100)}%, n=${p.n_acc})` : ""}${p.clean != null ? ` · clean ${Math.round(p.clean * 100)}%` : ""}${p.diversity != null ? ` · diversity ${Math.round(p.diversity * 100)}%` : ""}`}</title>
          </circle>
        ))}
      </svg>
      <div className="legend-row">
        <span><i style={{ background: "var(--accent)" }} />Accuracy <span className="faint">(band: 95% interval)</span></span>
        <span><i style={{ background: "var(--green)" }} />Clean output</span>
        <span><i style={{ background: "var(--purple)", backgroundImage: "repeating-linear-gradient(90deg, var(--purple) 0 5px, transparent 5px 9px)", backgroundColor: "transparent" }} />Diversity</span>
        {breaks != null && <span><i style={{ background: "var(--red)", opacity: 0.35, height: 10 }} />Quality breaks</span>}
      </div>
    </div>
  );
}

const CTL_RANK = { rejected: 0, ignored: 1, unclear: 2, honored: 3 } as const;
const CTL_LABEL = { honored: "Honoured", ignored: "Ignored", rejected: "Rejected", unclear: "Unclear" } as const;
const CTL_TONE = { honored: "green", ignored: "amber", rejected: "red", unclear: undefined } as const;

const paramLabel = (k: string, v: number) => `${k} ${v}`;

function SweepTable({ rows, baseline, best }: { rows: SweepSetting[]; baseline: { acc: number | null; clean: number | null; diversity: number | null } | null; best: string | null }) {
  return (
    <div className="scroll-x">
      <table className="table mini">
        <thead><tr><th>Setting</th><th>Accuracy</th><th>Clean</th><th>Diversity</th><th title="Share of repeated samples that came out identical">Repeats</th></tr></thead>
        <tbody>
          {baseline && <tr className="base"><td>No truncation</td><td className="num">{pct(baseline.acc)}</td><td className="num">{pct(baseline.clean)}</td><td className="num">{pct(baseline.diversity)}</td><td className="num faint">—</td></tr>}
          {rows.map((s) => (
            <tr key={s.label} className={best === s.label ? "best" : ""}>
              <td className="mono">{s.label} {best === s.label && <Badge tone="green">best</Badge>}</td>
              {s.error ? <td colSpan={4} className="faint">Rejected by the endpoint: {s.error.slice(0, 90)}</td> : <>
                <td className="num">{pct(s.acc)}</td><td className="num">{pct(s.clean)}</td><td className="num">{pct(s.diversity)}</td><td className="num">{s.identical == null ? "—" : pct(s.identical)}</td>
              </>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const VS: Record<string, string> = { api_default: "generic API defaults", vendor: "the maker’s published settings", tuned: "the tuned settings" };

export function HyperparametersSection({ r }: { r: R }) {
  const sm = r.sampling;
  if (!sm) return <NotRun id="hyperparameters" title="Hyperparameters" icon="tune" text={r.options.stress ? "Skipped: the detector self-check overrides the decoding settings, so sweeping them would measure nothing." : r.options.hyperparameters === undefined ? "This run was made before hyperparameter tuning existed. Run the evaluation again to include it." : "Hyperparameter tuning was turned off for this run, so it is not part of the overall score."} />;
  if (!samplingOk(sm)) return <NotRun id="hyperparameters" title="Hyperparameters" icon="tune" text={`The hyperparameter sweeps could not complete${sm.error ? `: ${sm.error}` : "."} The rest of the evaluation is unaffected.`} />;
  return <SamplingBody r={r} sm={sm} />;
}

function SamplingBody({ r, sm }: { r: R; sm: SamplingReport }) {
  const t = sm.temperature;
  const modelName = r.options.openrouter_model ?? r.environment.served_model ?? r.model.hf_repo;
  const honoured = sm.controls.filter((c) => c.status === "honored").length;
  const bad = sm.controls.filter((c) => c.status === "ignored" || c.status === "rejected").length;
  const tmax = Math.max(...t.points.map((p) => p.t), 0);
  const greedy = sm.determinism.greedy.identical;
  const controls = [...sm.controls].sort((a, b) => CTL_RANK[a.status] - CTL_RANK[b.status]);
  const val = sm.validation;
  const tuned = val?.arms.find((a) => a.id === "tuned");
  const examples = sm.examples ?? [];
  return (
    <div className="section" id="hyperparameters">
      <h2>Hyperparameters</h2>
      <p className="mod-intro">Which decoding settings suit this model, where its output falls apart, and whether the endpoint really honours the settings you send. {sm.requests} sampling requests{sm.quick ? " (quick mode: sparse sweeps)" : ""}; every comparison reports its uncertainty, and the recommended settings are re-tested on problems that were not used to choose them.</p>

      <div className="kpis">
        <ScoreTile label="Hyperparameter score" value={sm.score.overall} caption={`Robustness ${Math.round(sm.score.robustness)} · controls ${Math.round(sm.score.controllability)} · determinism ${Math.round(sm.score.determinism)} · penalties ${Math.round(sm.score.penalties)}`} />
        <Kpi label="Best temperature" value={t.best_t == null ? "—" : t.best_t.toFixed(1)} tone="neutral"
          caption={t.best_t == null ? "not measured" : t.greedy_degenerate ? "Greedy decoding degenerates here, so a little randomness is needed." : "Lowest setting with accuracy within noise of the best."} hint="The lowest temperature that keeps accuracy within noise of the best measured and the output clean." />
        <Kpi label="Safe ceiling" value={t.cliff_t == null ? "—" : t.cliff_t.toFixed(1)} tone={t.cliff_t == null ? "neutral" : t.cliff_t >= 1.2 ? "good" : t.cliff_t >= 0.8 ? "warn" : "bad"}
          caption={t.breaks_at != null ? `Quality breaks at ${t.breaks_at}.` : `No breakdown up to ${tmax}.`} hint="The highest temperature before accuracy or output cleanliness measurably degrades." />
        <Kpi label="Settings honoured" value={`${honoured}/${sm.controls.length}`} tone={bad === 0 && honoured === sm.controls.length ? "good" : bad === 0 ? "warn" : t.honored === "ignored" ? "bad" : "warn"}
          caption={bad ? `${bad} parameter${bad > 1 ? "s are" : " is"} ignored or rejected by this endpoint.` : "Every parameter had its documented effect."} hint="Temperature, top-p, top-k, min-p, seed, penalties, stop and max_tokens, each checked by its observable effect." />
        <Kpi label="Greedy repeatability" value={greedy == null ? "—" : pct(greedy)} tone={greedy == null ? "neutral" : greedy >= 0.99 ? "good" : greedy >= 0.7 ? "warn" : "bad"}
          caption={greedy == null ? "not measured" : greedy >= 0.99 ? "Identical output on repeat at temperature 0." : "Repeats at temperature 0 differ: results are not reproducible."} hint="At temperature 0 the same prompt should give the same answer every time." />
      </div>

      <div className="card mt">
        <h3>Quality against temperature</h3>
        <p className="sub">Accuracy on checkable problems, share of clean (non-garbled, non-looping) answers, and how varied repeated samples are{t.sensitivity != null ? ` · temperature sensitivity: ${t.sensitivity_label} (accuracy ${t.sensitivity === 0 ? "unchanged" : `down ${Math.round(t.sensitivity * 100)}%`} by T ${t.sensitivity_t ?? 1.2})` : ""}</p>
        <TempChart points={t.points} ceiling={t.cliff_t} breaks={t.breaks_at} />
        <details className="numbers">
          <summary>Show the numbers</summary>
          <div className="scroll-x">
            <table className="table mini">
              <thead><tr><th>T</th><th>Accuracy</th><th>95% interval</th><th>Samples</th><th>Clean</th><th>Diversity</th><th>Avg tokens</th></tr></thead>
              <tbody>
                {[...t.points].sort((a, b) => a.t - b.t).map((p) => (
                  <tr key={p.t}>
                    <td className="num">{p.t}</td><td className="num">{pct(p.acc)}</td>
                    <td className="num faint">{p.acc_ci ? `${pct(p.acc_ci[0])} – ${pct(p.acc_ci[1])}` : "—"}</td>
                    <td className="num faint">{p.n_acc}</td><td className="num">{pct(p.clean)}</td><td className="num">{pct(p.diversity)}</td><td className="num faint">{fmt(p.tokens, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </div>

      <h3 className="sub-h">Recommended settings</h3>
      <div className="profiles">
        {Object.entries(sm.profiles).map(([key, p]) => {
          const request = { ...p.request, model: modelName };
          return (
            <div className="card profile" key={key}>
              <div className="ph"><h4>{p.label}</h4><div className="for">{p.for}</div></div>
              <div className="pchips">{Object.entries(p.params).map(([k, v]) => <span className="pchip" key={k}>{paramLabel(k, v)}</span>)}</div>
              <p className="why-s">{p.why}</p>
              <CodeBlock code={JSON.stringify(request, null, 2)} lang="json" />
            </div>
          );
        })}
      </div>
      {sm.guidance && (
        <div className="faint note-s" style={{ marginTop: 10 }}>
          The maker’s published settings: {Object.entries(sm.guidance.params).map(([k, v]) => `${k} ${v}`).join(" · ")} <span>({sm.guidance.source})</span>.
        </div>
      )}

      {val && tuned && (
        <div className="card mt">
          <h3>Do the tuned settings actually help?</h3>
          <p className="sub">Held-out test: {val.n_probes} problems that were not used to choose the settings, {val.samples_per_probe} samples each. A paired comparison per problem, so repeated greedy answers are not counted as independent evidence.</p>
          <div className="scroll-x">
            <table className="table mini">
              <thead><tr><th>Settings</th><th>Parameters</th><th>Correct</th><th>Accuracy</th><th>Clean</th></tr></thead>
              <tbody>
                {val.arms.map((a) => (
                  <tr key={a.id} className={a.id === "tuned" ? "best" : ""}>
                    <td>{a.label}</td>
                    <td className="mono faint params">{Object.entries(a.params).map(([k, v]) => `${k} ${v}`).join(", ")}</td>
                    <td className="num">{a.correct}/{a.n}</td><td className="num">{pct(a.acc)}</td><td className="num">{pct(a.clean)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="cmp-lines">
            {val.comparisons.map((c) => (
              <div className="cmp-line" key={c.against}>
                <Badge tone={c.verdict === "improved" ? "green" : c.verdict === "worse" ? "red" : undefined}>{c.verdict === "improved" ? "Tuned is better" : c.verdict === "worse" ? "Tuned is worse" : "Within noise"}</Badge>
                <span>Tuned vs {VS[c.against] ?? c.against}: <b className="num">{c.delta == null ? "—" : `${signed(c.delta * 100)} pts`}</b> <span className="faint">{c.ci ? `(95% interval ${signed(c.ci[0] * 100)} to ${signed(c.ci[1] * 100)} pts over ${c.problems} problems)` : ""}</span></span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid2 mt" style={{ alignItems: "start" }}>
        <div className="card">
          <h3>Does the endpoint honour each setting?</h3>
          <p className="sub">Each parameter is judged by its observable effect, not by whether the request was accepted</p>
          <div className="ctl-list">
            {controls.map((c) => (
              <div className="ctl" key={c.id}>
                <span className="mono nm">{c.label}</span>
                <Badge tone={CTL_TONE[c.status]}>{CTL_LABEL[c.status]}</Badge>
                <span className="dt">{c.detail}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="card">
          <h3>Reproducibility</h3>
          <p className="sub">Does the same request give the same answer?</p>
          <div className="kv">
            <div><span className="k">Temperature 0, repeated</span><span className="v"><Ck ok={sm.determinism.greedy.identical == null ? null : sm.determinism.greedy.identical >= 0.99} /> {pct(sm.determinism.greedy.identical)} identical <span className="faint">(n={sm.determinism.greedy.n})</span></span></div>
            <div><span className="k">Same seed, repeated</span><span className="v"><Ck ok={sm.determinism.seed_same.identical == null ? null : sm.determinism.seed_same.identical >= 0.99} /> {pct(sm.determinism.seed_same.identical)} identical <span className="faint">(n={sm.determinism.seed_same.n})</span></span></div>
            <div><span className="k">Different seeds differ</span><span className="v"><Ck ok={sm.determinism.seed_different.differs_from_seeded} /> {sm.determinism.seed_different.differs_from_seeded == null ? "not enough samples" : sm.determinism.seed_different.differs_from_seeded ? "yes" : "no"}</span></div>
            <div><span className="k">Concurrent requests agree</span><span className="v"><Ck ok={sm.determinism.concurrent.identical == null ? null : sm.determinism.concurrent.identical >= 0.99} /> {pct(sm.determinism.concurrent.identical)} identical <span className="faint">(n={sm.determinism.concurrent.n})</span></span></div>
          </div>
          <div className="faint note-s">Concurrent batches can legitimately change low-order numerics on a shared GPU; a mismatch here matters most when you rely on seeds for reproducible evaluations.</div>
        </div>
      </div>

      <div className="grid2 mt" style={{ alignItems: "start" }}>
        <div className="card">
          <h3>Top-p, top-k and min-p</h3>
          <p className="sub">{sm.truncation.stress_degraded ? `Quality was damaged at temperature ${sm.truncation.stress_temperature}, so truncation has something to repair` : `At temperature ${sm.truncation.stress_temperature} quality was not damaged, so truncation has little to fix and differences are mostly noise`}</p>
          <SweepTable rows={sm.truncation.settings} baseline={sm.truncation.baseline} best={sm.truncation.best?.label ?? null} />
          <div className="faint note-s">{sm.truncation.truncation_helps && sm.truncation.best ? `${sm.truncation.best.label} recovered ${Math.round(sm.truncation.best.gain * 100)} points of quality without flattening variety.` : "No truncation setting beat plain sampling by a margin larger than noise."}</div>
        </div>
        <div className="card">
          <h3>Repetition penalties</h3>
          <p className="sub">{sm.penalties.repetition_prone ? "The model repeats itself on an open-ended prompt" : "No repetition problem found on an open-ended prompt"}</p>
          <div className="scroll-x">
            <table className="table mini">
              <thead><tr><th>Setting</th><th title="Share of repeated n-grams; lower is better">Repetition</th><th>Word variety</th><th>Accuracy</th></tr></thead>
              <tbody>
                <tr className="base"><td>No penalty</td><td className="num">{fmt(sm.penalties.baseline.rep_index, 2)}</td><td className="num">{pct(sm.penalties.baseline.variety)}</td><td className="num">{pct(sm.penalties.baseline.acc)}</td></tr>
                {sm.penalties.settings.map((s) => (
                  <tr key={s.label} className={sm.penalties.recommended?.label === s.label ? "best" : ""}>
                    <td className="mono">{s.label} {sm.penalties.recommended?.label === s.label && <Badge tone="green">use</Badge>}</td>
                    {s.error ? <td colSpan={3} className="faint">Rejected: {s.error.slice(0, 70)}</td> : <><td className="num">{fmt(s.rep_index, 2)}</td><td className="num">{pct(s.variety)}</td><td className="num">{pct(s.acc)}</td></>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="faint note-s">{sm.penalties.recommended ? `${sm.penalties.recommended.label} reduced repetition without hurting accuracy.` : sm.penalties.harm_at ? `${sm.penalties.harm_at} hurt accuracy: avoid strong penalties for exact tasks.` : sm.penalties.any_effect ? "Penalties changed the output but this prompt did not need them." : "Penalties had no visible effect on this endpoint."}</div>
        </div>
      </div>

      {examples.length > 0 && (
        <details className="card mt examples">
          <summary><span>Sample outputs at different settings</span><span className="faint">What the numbers above look like in practice</span></summary>
          <div className="ex-list">
            {examples.map((e, i) => (
              <div key={i} className="ex">
                <div className="eh"><b>{e.label}</b><span className="mono faint">{Object.entries(e.params).map(([k, v]) => `${k} ${v}`).join(", ")}</span><Badge tone={e.clean ? "green" : "red"}>{e.clean ? "clean" : "problems"}</Badge></div>
                <div className="resp" style={{ maxHeight: 180 }}>{e.text}</div>
              </div>
            ))}
          </div>
        </details>
      )}

      {sm.errors > 0 && <div className="banner warn" style={{ marginTop: 14 }}><Icon name="alert" size={18} /><div>{sm.errors} of {sm.requests} sampling requests failed (rate limits or rejected parameters). They are excluded from the curves above rather than counted as wrong answers.</div></div>}
      <Findings items={sm.findings} />
    </div>
  );
}
