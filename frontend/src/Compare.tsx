import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "./api";
import { providerLabel } from "./Home";
import type { RunFull } from "./types";
import { Badge, Icon, MultiRadar, SERIES_COLORS, fmt, scoreColor, verdictTone } from "./ui";

type Col = { key: string; label: string; get: (r: RunFull) => number | null; fmt: (v: number) => string; best: "max" | "min" };

const COLS: Col[] = [
  { key: "overall", label: "Overall", get: (r) => r.report?.scores.overall ?? null, fmt: (v) => v.toFixed(1), best: "max" },
  { key: "coherency", label: "Coherency", get: (r) => r.report?.scores.coherency ?? null, fmt: (v) => v.toFixed(0), best: "max" },
  { key: "coding", label: "Coding", get: (r) => r.report?.scores.coding ?? null, fmt: (v) => v.toFixed(0), best: "max" },
  { key: "math", label: "Math", get: (r) => r.report?.scores.math ?? null, fmt: (v) => v.toFixed(0), best: "max" },
  { key: "general", label: "General", get: (r) => r.report?.scores.general ?? null, fmt: (v) => v.toFixed(0), best: "max" },
  { key: "tps", label: "Decode tok/s", get: (r) => r.report?.performance.decode_tps_median ?? null, fmt: (v) => v.toFixed(0), best: "max" },
  { key: "ttft", label: "TTFT p50 (ms)", get: (r) => r.report?.performance.ttft_ms_p50 ?? null, fmt: (v) => v.toFixed(0), best: "min" },
  { key: "cost", label: "Run cost", get: (r) => r.report?.usage?.cost_usd ?? null, fmt: (v) => `$${v < 0.1 ? v.toFixed(4) : v.toFixed(2)}`, best: "min" },
  { key: "clean", label: "Clean output", get: (r) => (r.report ? r.report.coherency.clean_ratio * 100 : null), fmt: (v) => `${v.toFixed(0)}%`, best: "max" },
];

export function Compare() {
  const [params] = useSearchParams();
  const ids = useMemo(() => (params.get("ids") ?? "").split(",").filter(Boolean), [params]);
  const [runs, setRuns] = useState<RunFull[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const rs = await Promise.all(ids.map((id) => api.run(id)));
        if (stop) return;
        setRuns(rs);
        if (rs.some((r) => r.status === "running" || r.status === "queued")) timer = setTimeout(load, 2500);
      } catch (e) { if (!stop) setErr((e as Error).message); }
    };
    load();
    return () => { stop = true; clearTimeout(timer); };
  }, [ids]);

  const done = runs.filter((r) => r.status === "completed" && r.report);
  const live = runs.filter((r) => r.status === "running" || r.status === "queued");
  const sorted = [...done].sort((a, b) => (b.report!.scores.overall) - (a.report!.scores.overall));
  const color = (id: string) => SERIES_COLORS[ids.indexOf(id) % SERIES_COLORS.length];

  const bestOf = (c: Col) => {
    const vals = sorted.map((r) => c.get(r)).filter((v): v is number => v != null);
    if (vals.length < 2) return null;
    return c.best === "max" ? Math.max(...vals) : Math.min(...vals);
  };

  const insights = (() => {
    if (sorted.length < 2) return [];
    const out: string[] = [];
    const name = (r: RunFull) => r.model.name;
    const col = (k: string) => COLS.find((c) => c.key === k)!;
    // all runs within `eps` of the best value (so ties are reported as ties, not as a winner)
    const leaders = (c: Col, dir: "max" | "min", eps: number) => {
      const vs = sorted.map((r) => ({ r, v: c.get(r) })).filter((x): x is { r: RunFull; v: number } => x.v != null);
      if (!vs.length) return [];
      const best = dir === "max" ? Math.max(...vs.map((x) => x.v)) : Math.min(...vs.map((x) => x.v));
      return vs.filter((x) => Math.abs(x.v - best) <= eps).map((x) => x.r);
    };
    const list = (rs: RunFull[]) => rs.map(name).join(", ");
    const ov = leaders(col("overall"), "max", 0.5);
    out.push(ov.length === sorted.length ? `All ${sorted.length} models are within 0.5 points overall (${sorted[0].report!.scores.overall.toFixed(1)}–${sorted[sorted.length - 1].report!.scores.overall.toFixed(1)}) — pick on speed and cost.`
      : ov.length > 1 ? `${list(ov)} tie for the top overall score (${ov[0].report!.scores.overall.toFixed(1)}).` : `${name(ov[0])} leads overall at ${ov[0].report!.scores.overall.toFixed(1)}/100.`);
    const coh = leaders(col("coherency"), "max", 0.5);
    out.push(coh.length === sorted.length ? "All models are equally coherent." : coh.length > 1 ? `Most coherent (tie): ${list(coh)}.` : `Most coherent: ${name(coh[0])} (${coh[0].report!.scores.coherency.toFixed(0)}).`);
    const fast = leaders(col("tps"), "max", 0), slow = leaders(col("tps"), "min", 0);
    if (fast.length === 1 && slow.length === 1 && fast[0] !== slow[0] && slow[0].report!.performance.decode_tps_median)
      out.push(`Fastest: ${name(fast[0])} at ${fmt(fast[0].report!.performance.decode_tps_median, 0)} tok/s — ${(fast[0].report!.performance.decode_tps_median! / slow[0].report!.performance.decode_tps_median!).toFixed(1)}× ${name(slow[0])}.`);
    const cheap = leaders(col("cost"), "min", 0), dear = leaders(col("cost"), "max", 0);
    if (cheap.length === 1 && dear.length === 1 && cheap[0] !== dear[0] && cheap[0].report!.usage?.cost_usd)
      out.push(`Cheapest to run: ${name(cheap[0])} — ${(dear[0].report!.usage!.cost_usd! / cheap[0].report!.usage!.cost_usd!).toFixed(1)}× less than ${name(dear[0])}.`);
    const flagged = sorted.filter((r) => r.report!.verdict.label !== "ready");
    out.push(flagged.length ? `Not rated "ready": ${list(flagged)}.` : `All ${sorted.length} models are rated “ready”.`);
    return out;
  })();

  if (!ids.length) return <div className="empty"><h2>Nothing to compare</h2><p>Select runs from History, or enable <b>Compare several models</b> in Run options.</p><Link className="btn" to="/history">Open history</Link></div>;
  if (err) return <div className="empty"><h2>Could not load runs</h2><p>{err}</p><Link className="btn" to="/">Home</Link></div>;

  return (
    <>
      <div className="run-head">
        <div>
          <h1>Model comparison</h1>
          <div className="sub"><span>{ids.length} models</span>{live.length > 0 && <Badge tone="violet"><span className="spinner" style={{ width: 10, height: 10 }} /> {live.length} still running</Badge>}</div>
        </div>
        <div className="actions" style={{ marginTop: 0 }}><Link className="btn" to="/history">History</Link><Link className="btn" to="/">New evaluation</Link></div>
      </div>

      {live.length > 0 && (
        <div className="section">
          <h2>In progress</h2>
          <div className="grid3">
            {runs.map((r) => {
              const cur = r.phases.find((p) => p.status === "running");
              const donePh = r.phases.filter((p) => p.status === "done").length;
              return (
                <Link key={r.id} to={`/runs/${r.id}`} className="card" style={{ display: "block" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                    <b>{r.model.name}</b>
                    {r.status === "completed" ? <Badge tone="green">done</Badge> : r.status === "failed" ? <Badge tone="red">failed</Badge> : <Badge tone="violet"><span className="spinner" style={{ width: 10, height: 10 }} /> running</Badge>}
                  </div>
                  <div className="bar" style={{ margin: "14px 0 8px" }}><i style={{ width: `${(donePh / r.phases.length) * 100}%`, background: color(r.id) }} /></div>
                  <div className="faint" style={{ fontSize: 12.5 }}>{r.status === "failed" ? r.error : cur ? `${cur.title}${cur.detail ? ` · ${cur.detail}` : ""}` : `${donePh}/${r.phases.length} phases`}</div>
                </Link>
              );
            })}
          </div>
        </div>
      )}

      {sorted.length > 0 && (
        <>
          <div className="section">
            <h2>Leaderboard</h2>
            <div className="card" style={{ padding: 0, overflow: "hidden" }}>
              <div style={{ overflowX: "auto" }}>
                <table className="table cmp">
                  <thead><tr><th>#</th><th>Model</th><th>Verdict</th>{COLS.map((c) => <th key={c.key}>{c.label}</th>)}</tr></thead>
                  <tbody>
                    {sorted.map((r, i) => (
                      <tr key={r.id}>
                        <td className="mono muted">{i + 1}</td>
                        <td>
                          <Link to={`/runs/${r.id}`} style={{ fontWeight: 650, display: "flex", gap: 9, alignItems: "center" }}>
                            <span className="swatch" style={{ background: color(r.id) }} />{r.model.name}
                          </Link>
                          <div className="faint" style={{ fontSize: 11.5, marginLeft: 19 }}>{providerLabel(r.options.provider)}{r.options.stress ? ` · stress: ${r.options.stress}` : ""}</div>
                        </td>
                        <td><Badge tone={verdictTone(r.report!.verdict.label)}>{r.report!.verdict.title}</Badge></td>
                        {COLS.map((c) => {
                          const v = c.get(r);
                          const best = bestOf(c);
                          const isBest = v != null && best != null && v === best;
                          return (
                            <td key={c.key} className="mono" style={{ fontWeight: isBest ? 800 : 500, color: isBest ? "var(--green)" : c.key === "overall" && v != null ? scoreColor(v) : undefined }}>
                              {v == null ? "—" : c.fmt(v)}{isBest && <span title="best in column"> ★</span>}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div className="section">
            <h2>Profile</h2>
            <div className="row" style={{ alignItems: "center" }}>
              <div className="card" style={{ flex: "0 1 420px", display: "grid", placeItems: "center" }}>
                <MultiRadar axes={["Coherency", "Coding", "Math", "General", "Speed"]}
                  series={sorted.map((r) => ({ label: r.model.name, color: color(r.id), values: [r.report!.scores.coherency, r.report!.scores.coding, r.report!.scores.math, r.report!.scores.general, r.report!.scores.performance] }))} />
                <div className="legend">{sorted.map((r) => <span key={r.id}><i style={{ background: color(r.id) }} />{r.model.name}</span>)}</div>
              </div>
              <div className="card" style={{ flex: "1 1 320px" }}>
                <h3>Takeaways</h3>
                <p className="sub">Generated from the measured results</p>
                <ul className="takeaways">{insights.map((t) => <li key={t}>{t}</li>)}</ul>
              </div>
            </div>
          </div>
        </>
      )}
      {sorted.length === 0 && live.length === 0 && <div className="empty">No completed runs to compare yet.</div>}
      <div className="footer"><Icon name="compare" size={14} /> ★ marks the best value in each column</div>
    </>
  );
}
