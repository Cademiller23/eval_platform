import { Fragment, useState } from "react";
import type { TestResult } from "./types";
import { Badge, fmt } from "./ui";

const DOMAINS = ["all", "coherency", "coding", "math", "general"] as const;

export function TestsTable({ tests }: { tests: TestResult[] }) {
  const [dom, setDom] = useState<(typeof DOMAINS)[number]>("all");
  const [open, setOpen] = useState<string | null>(null);
  const [onlyFail, setOnlyFail] = useState(false);
  const rows = tests
    .filter((t) => (dom === "all" || t.domain === dom) && (!onlyFail || !t.passed || t.health?.severe))
    .sort((a, b) => a.domain.localeCompare(b.domain) || a.id.localeCompare(b.id));
  return (
    <div className="card" style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ padding: "16px 18px 0" }} className="filters">
        {DOMAINS.map((d) => <button key={d} className={dom === d ? "on" : ""} onClick={() => setDom(d)}>{d === "all" ? `All (${tests.length})` : `${d[0].toUpperCase()}${d.slice(1)} (${tests.filter((t) => t.domain === d).length})`}</button>)}
        <button className={onlyFail ? "on" : ""} onClick={() => setOnlyFail(!onlyFail)} style={{ marginLeft: "auto" }}>Only problems</button>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="table">
          <thead><tr><th>Test</th><th>Domain</th><th>Result</th><th>Output health</th><th>Tokens</th><th>tok/s</th></tr></thead>
          <tbody>
            {rows.map((t) => (
              <Fragment key={t.id}>
                <tr className="t-row" onClick={() => setOpen(open === t.id ? null : t.id)}>
                  <td><div style={{ fontWeight: 600 }}>{t.name}</div><div className="faint" style={{ fontSize: 12 }}>{t.skill} · {t.difficulty}</div></td>
                  <td><Badge>{t.domain}</Badge></td>
                  <td>{t.passed ? <Badge tone="green">✓ pass</Badge> : t.score > 0 ? <Badge tone="amber">partial {Math.round(t.score * 100)}%</Badge> : <Badge tone="red">✗ fail</Badge>}</td>
                  <td>{t.health?.issues.length ? t.health.issues.slice(0, 3).map((i) => <Badge key={i.kind} tone={i.severity === "minor" ? "amber" : "red"}>{i.kind.replace(/_/g, " ")}</Badge>) : <Badge tone="green">clean</Badge>}</td>
                  <td className="mono muted">{t.metrics.tokens ?? "—"}</td>
                  <td className="mono muted">{fmt(t.metrics.decode_tps, 0)}</td>
                </tr>
                {open === t.id && (
                  <tr><td colSpan={6}>
                    <div className="detail">
                      {t.error && <div className="banner err">{t.error}</div>}
                      <div><h5>Prompt</h5><div className="resp" style={{ maxHeight: 140 }}>{t.prompt}</div></div>
                      <div><h5>Model response {t.metrics.finish_reason && <span className="faint">· finish: {t.metrics.finish_reason}</span>}</h5><div className="resp">{t.response || "(empty)"}</div></div>
                      <div className="grid2" style={{ alignItems: "start" }}>
                        <div>
                          <h5>Checks</h5>
                          {t.checks.map((c, i) => <div key={i} className={`check ${c.passed ? "ok" : "no"}`}><span>{c.passed ? "✓" : "✗"}</span><span>{c.name}{c.detail && !c.passed ? <span className="faint"> — {c.detail}</span> : ""}</span></div>)}
                        </div>
                        <div>
                          <h5>Output health</h5>
                          {t.health?.issues.length ? t.health.issues.map((i, k) => <div key={k} className={`check ${i.severity === "minor" ? "" : "no"}`}><span>{i.severity === "minor" ? "•" : "✗"}</span><span><b>{i.kind.replace(/_/g, " ")}</b> <span className="faint">— {i.detail}</span></span></div>) : <div className="check ok"><span>✓</span><span>No garbling, loops, token leaks or truncation detected.</span></div>}
                          {t.health && <div className="faint mono" style={{ fontSize: 11.5, marginTop: 8 }}>{Object.entries(t.health.metrics).map(([k, v]) => `${k}=${v}`).join("  ")}</div>}
                        </div>
                      </div>
                    </div>
                  </td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
