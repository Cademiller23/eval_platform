import { useCallback, useEffect, useState } from "react";
import { Link, Route, Routes, useNavigate } from "react-router-dom";
import { api } from "./api";
import { Compare } from "./Compare";
import { Home, RunRow } from "./Home";
import { RunPage } from "./RunPage";
import type { AppConfig, RunSummary } from "./types";
import { Icon } from "./ui";

type Theme = "dark" | "light";
const THEME_KEY = "coherence-lab.theme";

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const saved = localStorage.getItem(THEME_KEY) as Theme | null;
      if (saved === "dark" || saved === "light") return saved;
    } catch { /* ignore */ }
    return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem(THEME_KEY, theme); } catch { /* ignore */ }
  }, [theme]);
  return [theme, useCallback(() => setTheme((t) => (t === "dark" ? "light" : "dark")), [])];
}

function History() {
  const nav = useNavigate();
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [sel, setSel] = useState<string[]>([]);
  useEffect(() => { api.runs().then(setRuns).catch(() => setRuns([])); }, []);
  const toggle = (id: string) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : s.length >= 5 ? s : [...s, id]));
  return (
    <div style={{ paddingTop: 20 }}>
      <div className="run-head">
        <h1 style={{ letterSpacing: "-0.03em", margin: 0 }}>History</h1>
        <div className="actions" style={{ marginTop: 0 }}>
          <button className="btn primary" disabled={sel.length < 2} onClick={() => nav(`/compare?ids=${sel.join(",")}`)}><Icon name="compare" size={16} /> Compare {sel.length > 1 ? `${sel.length} runs` : "selected"}</button>
        </div>
      </div>
      <p className="muted" style={{ margin: "6px 0 18px" }}>Tick two or more completed runs to compare them side by side.</p>
      {runs === null ? <div className="skeleton" style={{ height: 200 }} /> : runs.length === 0 ? <div className="empty">No evaluations yet. <Link className="gradient-text" to="/">Start one</Link>.</div>
        : <div className="runs-list">{runs.map((r) => <RunRow key={r.id} r={r} selected={sel.includes(r.id)} onToggle={toggle} />)}</div>}
    </div>
  );
}

export default function App() {
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  const [theme, toggleTheme] = useTheme();
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
            <button className="pill link icon-only" onClick={toggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} title="Toggle theme"><Icon name={theme === "dark" ? "sun" : "moon"} size={15} /></button>
          </nav>
        </header>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/history" element={<History />} />
          <Route path="/compare" element={<Compare />} />
          <Route path="/runs/:id" element={<RunPage />} />
          <Route path="*" element={<div className="empty">Not found. <Link to="/">Home</Link></div>} />
        </Routes>
      </div>
    </>
  );
}
