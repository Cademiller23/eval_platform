import { useCallback, useEffect, useState } from "react";
import { Link, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api } from "./api";
import { Compare } from "./Compare";
import { IS_DEMO } from "./env";
import { Home, RunRow } from "./Home";
import { RunPage } from "./RunPage";
import type { AppConfig, RunSummary } from "./types";
import { Icon } from "./ui";

type Theme = "light" | "dark";
const THEME_KEY = "coherence-lab.theme";

/**
 * Follows the system by default (no attribute set → the CSS media query decides).
 * The toggle stores an explicit choice, which wins over the system setting.
 */
function useTheme(): [Theme, () => void] {
  const systemDark = () => !!window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  const [explicit, setExplicit] = useState<Theme | null>(() => {
    if (IS_DEMO) return null; // the host page owns theming inside the preview
    try {
      const saved = localStorage.getItem(THEME_KEY);
      return saved === "light" || saved === "dark" ? saved : null;
    } catch { return null; }
  });
  const [sys, setSys] = useState<Theme>(() => (systemDark() ? "dark" : "light"));
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!mq) return;
    const on = () => setSys(mq.matches ? "dark" : "light");
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  useEffect(() => {
    if (IS_DEMO) return;
    const root = document.documentElement;
    if (explicit) root.dataset.theme = explicit; else delete root.dataset.theme;
    try { explicit ? localStorage.setItem(THEME_KEY, explicit) : localStorage.removeItem(THEME_KEY); } catch { /* private mode */ }
  }, [explicit]);
  const effective = explicit ?? sys;
  return [effective, useCallback(() => setExplicit(effective === "dark" ? "light" : "dark"), [effective])];
}

function History() {
  const nav = useNavigate();
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [sel, setSel] = useState<string[]>([]);
  useEffect(() => { api.runs().then(setRuns).catch(() => setRuns([])); }, []);
  const toggle = (id: string) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : s.length >= 5 ? s : [...s, id]));
  return (
    <>
      <div className="run-head">
        <div>
          <h1>History</h1>
          <div className="sub">Tick two or more finished runs to compare them side by side.</div>
        </div>
        <div className="actions" style={{ marginTop: 0 }}>
          <button className="btn primary" disabled={sel.length < 2} onClick={() => nav(`/compare?ids=${sel.join(",")}`)}><Icon name="compare" size={16} /> Compare {sel.length > 1 ? `${sel.length} runs` : "selected"}</button>
        </div>
      </div>
      <div style={{ marginTop: 28 }}>
        {runs === null ? <div className="skeleton" style={{ height: 220 }} />
          : runs.length === 0 ? <div className="empty"><h2>No evaluations yet</h2><p>Pick a model on the home screen to run your first one.</p><Link className="btn primary" to="/">Start an evaluation</Link></div>
          : <div className="runs-list">{runs.map((r) => <RunRow key={r.id} r={r} selected={sel.includes(r.id)} onToggle={toggle} />)}</div>}
      </div>
    </>
  );
}

export default function App() {
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  const [theme, toggleTheme] = useTheme();
  const loc = useLocation();
  useEffect(() => { api.config().then(setCfg).catch(() => {}); }, []);
  useEffect(() => { window.scrollTo({ top: 0 }); }, [loc.pathname]);
  const prov = cfg?.providers.find((p) => p.id === cfg.default_provider);
  return (
    <div className="app">
      <header className="nav-bar">
        <div className="nav-inner">
          <Link to="/" className="brand" aria-label="Coherence Lab home">
            <span className="mark"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12.5l4.5 4.5L19 7" /></svg></span>
            Coherence Lab
          </Link>
          <nav className="nav" aria-label="Main">
            {IS_DEMO && <span className="badge cyan" title="Recorded sample data, no server attached">Interactive preview</span>}
            {!IS_DEMO && prov && <span className="pill status-pill"><span className={`dot ${prov.available ? "green" : "amber"}`} /> {prov.label}</span>}
            <Link className="pill link" to="/history"><Icon name="history" size={15} /> History</Link>
            {!IS_DEMO && (
              <button className="pill link icon-only" onClick={toggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} title="Toggle theme">
                <Icon name={theme === "dark" ? "sun" : "moon"} size={16} />
              </button>
            )}
          </nav>
        </div>
      </header>
      <main className="shell">
        <div className="page" key={loc.pathname + loc.search}>
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/history" element={<History />} />
            <Route path="/compare" element={<Compare />} />
            <Route path="/runs/:id" element={<RunPage />} />
            <Route path="*" element={<div className="empty"><h2>Page not found</h2><Link className="btn primary" to="/">Back to start</Link></div>} />
          </Routes>
        </div>
      </main>
      <footer className="site-footer">
        Coherence Lab{cfg ? ` ${cfg.version}` : ""} · Evaluate any model for coherency, coding, maths and speed · Runs on Modal, OpenRouter or any OpenAI-compatible endpoint
      </footer>
    </div>
  );
}
