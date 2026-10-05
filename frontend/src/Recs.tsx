import { useState } from "react";
import type { Rec, Report } from "./types";
import { Badge, CodeBlock, Icon, impactTone } from "./ui";

type Recs = Report["recommendations"];

export function Recommendations({ rec, onApply, busy }: { rec: Recs; onApply?: (spec?: string) => void; busy: boolean }) {
  const [tab, setTab] = useState<"spec" | "speed" | "coh">("spec");
  const urgent = rec.coherence.filter((r) => r.impact === "high").length;
  return (
    <>
      <div className="tabs">
        <button className={tab === "spec" ? "on" : ""} onClick={() => setTab("spec")}><Icon name="bolt" size={16} /> Speculative decoding</button>
        <button className={tab === "speed" ? "on" : ""} onClick={() => setTab("speed")}><Icon name="gauge" size={16} /> Make it faster <Badge>{rec.speed.filter((r) => r.impact !== "low").length}</Badge></button>
        <button className={tab === "coh" ? "on" : ""} onClick={() => setTab("coh")}><Icon name="shield" size={16} /> Make it more coherent {urgent > 0 && <Badge tone="red">{urgent}</Badge>}</button>
      </div>
      <div className="tab-body">
        {tab === "spec" && <SpecTab plan={rec.speculative} onApply={onApply} busy={busy} />}
        {tab === "speed" && rec.speed.map((r) => <RecCard key={r.id} r={r} />)}
        {tab === "coh" && rec.coherence.map((r) => <RecCard key={r.id} r={r} />)}
      </div>
    </>
  );
}

function RecCard({ r }: { r: Rec }) {
  return (
    <div className="rec">
      <div className="rh">
        <h4>{r.title}</h4>
        {r.impact !== "info" && <Badge tone={impactTone(r.impact)}>{r.impact} impact</Badge>}
        {r.effort && r.effort !== "n/a" && <Badge>{r.effort} effort</Badge>}
      </div>
      <div className="why">{r.why}</div>
      <div className="dt">{r.detail}</div>
      {r.code && <CodeBlock code={r.code} />}
    </div>
  );
}

function SpecTab({ plan, onApply, busy }: { plan: Recs["speculative"]; onApply?: (s?: string) => void; busy: boolean }) {
  const [sel, setSel] = useState<string | null>(null);
  const chosen = plan.methods.find((m) => m.method === sel);
  return (
    <>
      <div className="banner" style={{ fontSize: 14.5 }}><Icon name="spark" size={20} /><div>{plan.summary}</div></div>

      {plan.methods.length > 0 && (
        <>
          <div className="faint" style={{ fontSize: 12, textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 700 }}>Methods that work for this model</div>
          <div className="methods">
            {plan.methods.map((m) => (
              <div key={m.method} className={`method ${m.fit}`} onClick={() => setSel(sel === m.method ? null : m.method)} style={{ cursor: "pointer" }}>
                <h4>{m.title} {m.fit === "best" && <Badge tone="violet">best fit</Badge>} {!m.verified && <Badge tone="amber">verify repo</Badge>}</h4>
                <div className="x gradient-text">{m.expected_speedup}</div>
                <p>{m.why}</p>
                {m.repo && <p className="mono" style={{ fontSize: 12, color: "var(--cyan)" }}>{m.repo}</p>}
                <ul>{m.pros.map((p) => <li key={p}>{p}</li>)}{m.cons.map((p) => <li key={p} style={{ color: "var(--faint)" }}>{p}</li>)}</ul>
                {onApply && (
                  <div style={{ marginTop: 14, display: "flex", gap: 8 }}>
                    <button className="btn small primary" disabled={busy} onClick={(e) => { e.stopPropagation(); onApply(m.method); }}>Try it now</button>
                    <button className="btn small" onClick={(e) => { e.stopPropagation(); setSel(sel === m.method ? null : m.method); }}>Config</button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {chosen && (
        <div className="card">
          <h3>{chosen.title} — launch config</h3>
          <p className="sub">Exact flags for this model</p>
          <div className="faint" style={{ fontSize: 12, fontWeight: 700 }}>vLLM</div>
          <CodeBlock code={chosen.vllm_cmd} lang="bash" />
          {chosen.sglang_cmd && <><div className="faint" style={{ fontSize: 12, fontWeight: 700, marginTop: 14 }}>SGLang</div><CodeBlock code={chosen.sglang_cmd} lang="bash" /></>}
        </div>
      )}

      <div className="card">
        <h3>Implementation steps</h3>
        <p className="sub">{plan.recommended ? `Following the recommended method: ${plan.methods.find((m) => m.method === plan.recommended)?.title ?? plan.recommended}` : "Step by step"}</p>
        <div className="steps" style={{ marginTop: 18 }}>
          {plan.steps.map((s, i) => (
            <div className="stp" key={i}>
              <div className="num">{i + 1}</div>
              <div>
                <h4>{s.title}</h4>
                <div className="body">{s.body}</div>
                {s.code && <CodeBlock code={s.code} lang={s.lang} />}
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
