import { useState, type ReactNode } from "react";

export const scoreColor = (s: number) => (s >= 80 ? "var(--green)" : s >= 60 ? "var(--cyan)" : s >= 45 ? "var(--amber)" : "var(--red)");

export const fmt = (v: number | null | undefined, d = 1, suffix = "") => (v == null || Number.isNaN(v) ? "—" : `${v.toFixed(d)}${suffix}`);
export const pct = (v: number | null | undefined, d = 0) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);

const FAMILY_COLORS: Record<string, string> = {
  Llama: "#60a5fa", Qwen: "#a78bfa", Mistral: "#fb923c", Gemma: "#34d399", Phi: "#22d3ee", DeepSeek: "#818cf8", GLM: "#f472b6", SmolLM: "#fbbf24",
};
export const familyColor = (f: string) => FAMILY_COLORS[f] ?? "#94a3b8";

type IconName = "check" | "x" | "chev" | "search" | "bolt" | "code" | "calc" | "chat" | "shield" | "gauge" | "gear" | "download" | "refresh" | "spark" | "clock" | "cpu" | "trash" | "stop" | "copy" | "arrow" | "flask" | "history" | "layers" | "sun" | "moon" | "key" | "compare" | "alert";
const PATHS: Record<IconName, ReactNode> = {
  check: <path d="M5 12.5l4.5 4.5L19 7" />,
  x: <path d="M6 6l12 12M18 6L6 18" />,
  chev: <path d="M6 9l6 6 6-6" />,
  search: <><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4-4" /></>,
  bolt: <path d="M13 3L5 13.5h6L10 21l8-10.5h-6L13 3z" />,
  code: <path d="M8 8l-5 4 5 4M16 8l5 4-5 4M14 5l-4 14" />,
  calc: <><rect x="5" y="3" width="14" height="18" rx="2.5" /><path d="M8.5 7.5h7M9 12h.01M12 12h.01M15 12h.01M9 16h.01M12 16h.01M15 16h.01" /></>,
  chat: <path d="M4 5.5A2.5 2.5 0 016.5 3h11A2.5 2.5 0 0120 5.5v8a2.5 2.5 0 01-2.5 2.5H10l-5 4v-4h-.5" />,
  shield: <path d="M12 3l7.5 3v5.5c0 4.3-3 7.7-7.5 9.5-4.5-1.8-7.5-5.2-7.5-9.5V6L12 3zM8.5 12l2.5 2.5L15.5 10" />,
  gauge: <><path d="M4 16a8 8 0 1116 0" /><path d="M12 16l4-5" /></>,
  gear: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 00.3 1.9l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.9-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 01-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.9.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.9 1.7 1.7 0 00-1.5-1H3a2 2 0 010-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.9l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.9.3h0a1.7 1.7 0 001-1.5V3a2 2 0 014 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.9-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.9v0a1.7 1.7 0 001.5 1H21a2 2 0 010 4h-.1a1.7 1.7 0 00-1.5 1z" /></>,
  download: <path d="M12 4v11m0 0l-4-4m4 4l4-4M5 20h14" />,
  refresh: <path d="M20 11a8 8 0 10-2.3 6M20 4v7h-7" />,
  spark: <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3zM19 17l.8 2.2L22 20l-2.2.8L19 23l-.8-2.2L16 20l2.2-.8L19 17z" />,
  clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></>,
  cpu: <><rect x="6" y="6" width="12" height="12" rx="2" /><rect x="9.5" y="9.5" width="5" height="5" rx="1" /><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3" /></>,
  trash: <path d="M5 7h14M10 7V4.5h4V7M7 7l1 13h8l1-13" />,
  stop: <rect x="6.5" y="6.5" width="11" height="11" rx="2" />,
  copy: <><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V6a2 2 0 012-2h9" /></>,
  arrow: <path d="M5 12h14m-5-5l5 5-5 5" />,
  flask: <path d="M9 3h6M10 3v6L4.5 19a1.5 1.5 0 001.3 2.2h12.4a1.5 1.5 0 001.3-2.2L14 9V3M7.5 15h9" />,
  history: <><path d="M4 12a8 8 0 108-8 8 8 0 00-6.4 3.2M4 4v4h4" /><path d="M12 8v4l3 2" /></>,
  layers: <path d="M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5M3 17.5l9 5 9-5" transform="translate(0 -1.5)" />,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4L6 18M18 6l1.4-1.4" /></>,
  moon: <path d="M20 14.5A8.5 8.5 0 019.5 4 8.5 8.5 0 1020 14.5z" />,
  key: <><circle cx="8" cy="15" r="4" /><path d="M10.8 12.2L20 3m-4 4l3 3m-6-0l2 2" /></>,
  compare: <path d="M7 4v16M17 4v16M3 8h8M13 16h8M3 12h8M13 12h8" />,
  alert: <><path d="M12 3.5l9.5 16.5h-19L12 3.5z" /><path d="M12 10v4.5M12 17.5v.01" /></>,
};
export function Icon({ name, size = 18, stroke = 1.9 }: { name: IconName; size?: number; stroke?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {PATHS[name]}
    </svg>
  );
}

export function ScoreRing({ value, grade, size = 200 }: { value: number; grade?: string; size?: number }) {
  const r = size / 2 - 14;
  const c = 2 * Math.PI * r;
  const col = scoreColor(value);
  return (
    <div className="ring-wrap" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <defs>
          <linearGradient id="ringg" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor={col} />
            <stop offset="1" stopColor="var(--violet)" />
          </linearGradient>
        </defs>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" style={{ stroke: "var(--track)" }} strokeWidth="12" />
        <circle
          cx={size / 2} cy={size / 2} r={r} fill="none" stroke="url(#ringg)" strokeWidth="12" strokeLinecap="round"
          strokeDasharray={`${(c * Math.max(0, Math.min(100, value))) / 100} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`}
          style={{ transition: "stroke-dasharray 1s cubic-bezier(.2,.8,.2,1)", filter: `drop-shadow(0 0 10px ${col})` }}
        />
      </svg>
      <div className="ring-center">
        <div>
          <div className="big" style={{ color: col }}>{Math.round(value)}</div>
          {grade && <div className="grade">Grade {grade}</div>}
        </div>
      </div>
    </div>
  );
}

export function Radar({ axes, size = 300 }: { axes: { label: string; value: number }[]; size?: number }) {
  const cx = size / 2, cy = size / 2, R = size / 2 - 52;
  const n = axes.length;
  const pt = (i: number, v: number): [number, number] => {
    const a = (Math.PI * 2 * i) / n - Math.PI / 2;
    return [cx + Math.cos(a) * R * (v / 100), cy + Math.sin(a) * R * (v / 100)];
  };
  const poly = axes.map((a, i) => pt(i, a.value).join(",")).join(" ");
  return (
    <svg viewBox={`0 0 ${size} ${size}`} width="100%" style={{ maxWidth: size }}>
      <defs>
        <linearGradient id="radg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#8b5cf6" stopOpacity="0.55" />
          <stop offset="1" stopColor="#22d3ee" stopOpacity="0.45" />
        </linearGradient>
      </defs>
      {[25, 50, 75, 100].map((g) => (
        <polygon key={g} points={axes.map((_, i) => pt(i, g).join(",")).join(" ")} fill="none" style={{ stroke: "var(--grid)" }} />
      ))}
      {axes.map((_, i) => {
        const [x, y] = pt(i, 100);
        return <line key={i} x1={cx} y1={cy} x2={x} y2={y} style={{ stroke: "var(--grid)" }} />;
      })}
      <polygon points={poly} fill="url(#radg)" stroke="#a78bfa" strokeWidth="2" strokeLinejoin="round" />
      {axes.map((a, i) => {
        const [x, y] = pt(i, a.value);
        const [lx, ly] = pt(i, 128);
        return (
          <g key={a.label}>
            <circle cx={x} cy={y} r="4" fill={scoreColor(a.value)} style={{ stroke: "var(--bg)" }} strokeWidth="2" />
            <text x={lx} y={ly} style={{ fill: "var(--text)" }} fontSize="12" fontWeight="600" textAnchor="middle" dominantBaseline="middle">{a.label}</text>
            <text x={lx} y={ly + 14} fill={scoreColor(a.value)} fontSize="12" fontWeight="700" textAnchor="middle" dominantBaseline="middle">{Math.round(a.value)}</text>
          </g>
        );
      })}
    </svg>
  );
}

export function Bar({ value, color, max = 100 }: { value: number; color?: string; max?: number }) {
  const w = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="bar">
      <i style={{ width: `${w}%`, background: color ?? scoreColor(value) }} />
    </div>
  );
}

export function Sparkline({ values, height = 56, width = 300 }: { values: number[]; height?: number; width?: number }) {
  if (values.length < 2) return <div style={{ height }} />;
  const max = Math.max(...values) * 1.1 || 1;
  const pts = values.map((v, i) => [(i / (values.length - 1)) * width, height - (v / max) * (height - 4) - 2]);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} preserveAspectRatio="none">
      <defs>
        <linearGradient id="spk" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#8b5cf6" stopOpacity="0.35" />
          <stop offset="1" stopColor="#8b5cf6" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${d} L${width},${height} L0,${height} Z`} fill="url(#spk)" />
      <path d={d} fill="none" stroke="#a78bfa" strokeWidth="2.2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" strokeLinecap="round" />
    </svg>
  );
}

export function CodeBlock({ code, lang }: { code: string; lang?: string | null }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="code" data-lang={lang ?? ""}>
      <button
        className="cp"
        onClick={() => {
          navigator.clipboard?.writeText(code).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1400); }).catch(() => {});
        }}
      >
        {copied ? "Copied ✓" : "Copy"}
      </button>
      <pre>{code}</pre>
    </div>
  );
}

export function Badge({ children, tone }: { children: ReactNode; tone?: "green" | "amber" | "red" | "violet" | "cyan" }) {
  return <span className={`badge ${tone ?? ""}`}>{children}</span>;
}

export const impactTone = (i: string): "red" | "amber" | "cyan" | undefined => (i === "high" ? "red" : i === "medium" ? "amber" : i === "low" ? "cyan" : undefined);

export const verdictTone = (v: string | null | undefined): "green" | "amber" | "red" | undefined =>
  v === "ready" ? "green" : v === "caution" ? "amber" : v === "not_ready" ? "red" : undefined;

export function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export interface RadarSeries { label: string; color: string; values: number[] }

export function MultiRadar({ axes, series, size = 340 }: { axes: string[]; series: RadarSeries[]; size?: number }) {
  const cx = size / 2, cy = size / 2, R = size / 2 - 50;
  const n = axes.length;
  const pt = (i: number, v: number): [number, number] => {
    const a = (Math.PI * 2 * i) / n - Math.PI / 2;
    return [cx + Math.cos(a) * R * (v / 100), cy + Math.sin(a) * R * (v / 100)];
  };
  return (
    <svg viewBox={`0 0 ${size} ${size}`} width="100%" style={{ maxWidth: size }} role="img" aria-label="Radar chart comparing models">
      {[25, 50, 75, 100].map((g) => <polygon key={g} points={axes.map((_, i) => pt(i, g).join(",")).join(" ")} fill="none" stroke="var(--border)" />)}
      {axes.map((a, i) => {
        const [x, y] = pt(i, 100);
        const [lx, ly] = pt(i, 122);
        return (
          <g key={a}>
            <line x1={cx} y1={cy} x2={x} y2={y} stroke="var(--border)" />
            <text x={lx} y={ly} fill="var(--muted)" fontSize="12" fontWeight="600" textAnchor="middle" dominantBaseline="middle">{a}</text>
          </g>
        );
      })}
      {series.map((s) => (
        <g key={s.label}>
          <polygon points={s.values.map((v, i) => pt(i, v).join(",")).join(" ")} fill={s.color} fillOpacity="0.14" stroke={s.color} strokeWidth="2" strokeLinejoin="round" />
          {s.values.map((v, i) => { const [x, y] = pt(i, v); return <circle key={i} cx={x} cy={y} r="3.2" fill={s.color} />; })}
        </g>
      ))}
    </svg>
  );
}

export const SERIES_COLORS = ["#8b5cf6", "#22d3ee", "#fbbf24", "#f472b6", "#34d399"];
