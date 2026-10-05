import { useEffect, useState } from "react";
import { Link, Route, Routes } from "react-router-dom";
import { api } from "./api";
import { Home, RunRow } from "./Home";
import { RunPage } from "./RunPage";
import type { AppConfig, RunSummary } from "./types";
import { Icon } from "./ui";

function History() {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  useEffect(() => { api.runs().then(setRuns).catch(() => setRuns([])); }, []);
  return (
    <div style={{ paddingTop: 20 }}>
      <h1 style={{ letterSpacing: "-0.03em", margin: "0 0 20px" }}>History</h1>
      {runs === null ? <div className="skeleton" style={{ height: 200 }} /> : runs.length === 0 ? <div className="empty">No evaluations yet. <Link className="gradient-text" to="/">Start one</Link>.</div> : <div className="runs-list">{runs.map((r) => <RunRow key={r.id} r={r} />)}</div>}
    </div>
  );
}

export default function App() {
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  useEffect(() => { api.config().then(setCfg).catch(() => {}); }, []);
  const prov = cfg?.providers.find((p) => p.id === cfg.default_provider);
  return (
    <>
      <div className="aurora"><i /><i /><i /></div>
      <div className="grid-bg" />
      <div className="shell">
        <header className="topbar">
          <Link to="/" className="brand">
            <span className="mark"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12.5l4.5 4.5L19 7" /></svg></span>
            Coherence Lab <small>model evaluation</small>
          </Link>
          <nav className="nav">
            {prov && <span className="pill"><span className={`dot ${prov.available ? "green" : "amber"}`} /> {prov.label}</span>}
            <Link className="pill link" to="/history"><Icon name="history" size={14} /> History</Link>
          </nav>
        </header>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/history" element={<History />} />
          <Route path="/runs/:id" element={<RunPage />} />
          <Route path="*" element={<div className="empty">Not found. <Link to="/">Home</Link></div>} />
        </Routes>
      </div>
    </>
  );
}
