import { useEffect, useMemo, useRef, useState } from "react";
import type { ModelInfo, OrModel } from "./types";
import { Badge, Icon, familyColor } from "./ui";

export interface Entry {
  id: string;
  name: string;
  desc: string;
  group: string;
  logo: string;
  color: string;
  badges: { text: string; tone?: "green" | "amber" | "red" | "violet" | "cyan" }[];
  right: string[];
  search: string;
}

export interface Selection { ids: string[]; custom?: string }

interface Props {
  entries: Entry[];
  placeholder?: string;
  searchPlaceholder: string;
  customPattern?: RegExp;
  customLabel?: string;
  multi?: boolean;
  maxMulti?: number;
  disabled?: boolean;
  busy?: boolean;
  loading?: boolean;
  footer?: string;
  onSelect: (sel: Selection) => void;
}

export function Picker({ entries, onSelect, disabled, busy, loading, multi, maxMulti = 4, customPattern, customLabel, placeholder, searchPlaceholder, footer }: Props) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);
  const [picked, setPicked] = useState<string[]>([]);
  const root = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const h = (e: MouseEvent) => { if (root.current && !root.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);
  useEffect(() => { if (open) setTimeout(() => input.current?.focus(), 30); else setQ(""); }, [open]);
  useEffect(() => { if (!multi) setPicked([]); }, [multi]);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    return entries.filter((e) => !s || e.search.includes(s));
  }, [entries, q]);
  const trimmed = q.trim();
  const customCandidate = customPattern && customPattern.test(trimmed) && !entries.some((e) => e.id.toLowerCase() === trimmed.toLowerCase()) ? trimmed : null;

  type Row = { kind: "custom"; id: string } | { kind: "entry"; entry: Entry };
  // Display order == keyboard order: groups in insertion order with "★ Featured" pinned first.
  const groups = useMemo(() => {
    const g = new Map<string, Entry[]>();
    filtered.forEach((e) => g.set(e.group, [...(g.get(e.group) ?? []), e]));
    return [...g.entries()].sort(([a], [b]) => Number(b.startsWith("★")) - Number(a.startsWith("★")));
  }, [filtered]);
  const rows: Row[] = [
    ...(customCandidate ? [{ kind: "custom" as const, id: customCandidate }] : []),
    ...groups.flatMap(([, es]) => es.map((entry) => ({ kind: "entry" as const, entry }))),
  ];
  useEffect(() => setIdx(0), [q, entries]);
  useEffect(() => {
    list.current?.querySelector<HTMLElement>(".picker-item.active")?.scrollIntoView({ block: "nearest" });
  }, [idx]);

  const finish = (ids: string[], custom?: string) => { setOpen(false); onSelect({ ids, custom }); };
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length >= maxMulti ? p : [...p, id]));
  const choose = (row: Row) => {
    if (row.kind === "custom") return multi ? toggle(row.id) : finish([], row.id);
    return multi ? toggle(row.entry.id) : finish([row.entry.id]);
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(rows.length - 1, i + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)); }
    else if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && multi && picked.length) { e.preventDefault(); finish(picked); }
    else if (e.key === "Enter" && rows[idx]) { e.preventDefault(); choose(rows[idx]); }
    else if (e.key === "Escape") setOpen(false);
  };
  const rowIndex = (id: string) => rows.findIndex((r) => (r.kind === "entry" ? r.entry.id === id : false));

  const label = busy ? "Starting evaluation…" : multi && picked.length ? `${picked.length} model${picked.length > 1 ? "s" : ""} selected — open to start` : placeholder ?? "Select a model to evaluate";

  return (
    <div className={`picker ${open ? "open" : ""}`} ref={root}>
      <button className="picker-trigger" onClick={() => setOpen((o) => !o)} disabled={disabled || busy} aria-haspopup="listbox" aria-expanded={open}>
        <span>{busy || loading ? <span className="spinner" style={{ width: 20, height: 20, borderWidth: 2.5 }} /> : <Icon name={multi ? "layers" : "search"} size={22} />}</span>
        <span className="ph" style={multi && picked.length ? { color: "var(--text)" } : undefined}>{label}</span>
        <span className="chev"><Icon name="chev" size={18} stroke={2.2} /></span>
      </button>

      {open && (
        <div className="picker-menu" role="listbox" onKeyDown={onKey}>
          <div className="picker-search">
            <Icon name="search" size={18} />
            <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder={searchPlaceholder} onKeyDown={onKey} aria-label="Search models" />
            <span className="faint">{loading ? "loading…" : `${filtered.length} models`}</span>
          </div>
          <div className="picker-list" ref={list}>
            {customCandidate && (
              <button className={`picker-item ${idx === 0 ? "active" : ""}`} onClick={() => choose(rows[0])}>
                <span className="logo" style={{ background: "var(--grad)" }}>+</span>
                <span className="meta">
                  <span className="name">{customLabel ?? "Evaluate custom model"}</span>
                  <span className="desc mono">{customCandidate}</span>
                </span>
                <span className="right"><Badge tone="violet">custom</Badge></span>
              </button>
            )}
            {groups.map(([group, es]) => (
              <div key={group}>
                <div className="picker-group">{group}</div>
                {es.map((m) => {
                  const on = picked.includes(m.id);
                  return (
                    <button key={m.id} className={`picker-item ${rowIndex(m.id) === idx ? "active" : ""} ${on ? "picked" : ""}`} onClick={() => choose({ kind: "entry", entry: m })} onMouseEnter={() => setIdx(rowIndex(m.id))} role="option" aria-selected={on}>
                      {multi && <span className={`cbox ${on ? "on" : ""}`}>{on && <Icon name="check" size={13} stroke={3} />}</span>}
                      <span className="logo" style={{ background: m.color }}>{m.logo}</span>
                      <span className="meta">
                        <span className="name">{m.name}{m.badges.map((b) => <Badge key={b.text} tone={b.tone}>{b.text}</Badge>)}</span>
                        <span className="desc">{m.desc}</span>
                      </span>
                      <span className="right">{m.right.map((r) => <Badge key={r}>{r}</Badge>)}</span>
                    </button>
                  );
                })}
              </div>
            ))}
            {!rows.length && !loading && <div className="picker-empty">No match.{customPattern ? <> Paste a full id like <span className="mono">org/model-name</span>.</> : null}</div>}
          </div>
          <div className="picker-foot">
            {multi ? (
              <>
                <span>Pick up to {maxMulti} · {picked.length} selected · Ctrl/⌘+Enter to start</span>
                <button className="btn small primary" disabled={!picked.length} onClick={() => finish(picked)}>Evaluate {picked.length || ""} model{picked.length === 1 ? "" : "s"}</button>
              </>
            ) : (
              <>
                <span>↑↓ navigate · Enter to start</span>
                <span>{footer ?? "Picking a model boots it and starts the evaluation"}</span>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ entry builders
export function catalogEntries(models: ModelInfo[]): Entry[] {
  return models.map((m) => ({
    id: m.id, name: m.name, desc: m.description, group: m.family, logo: m.family.slice(0, 2), color: familyColor(m.family),
    badges: [...(m.reasoning ? [{ text: "reasoning", tone: "violet" as const }] : []), ...(m.mtp_native ? [{ text: "MTP", tone: "cyan" as const }] : [])],
    right: [m.params_b >= 100 ? `${Math.round(m.params_b)}B` : `${+m.params_b.toFixed(1)}B${m.active_params_b < m.params_b ? ` · ${+m.active_params_b.toFixed(0)}B act` : ""}`, m.min_gpu],
    search: `${m.name} ${m.hf_repo} ${m.family} ${m.tags.join(" ")}`.toLowerCase(),
  }));
}

const money = (v: number | null) => (v == null ? "?" : v === 0 ? "free" : v < 0.1 ? `$${v.toFixed(3)}` : `$${v.toFixed(2)}`);

const VENDOR_NAMES: Record<string, string> = {
  openai: "OpenAI", anthropic: "Anthropic", google: "Google", "meta-llama": "Meta Llama", mistralai: "Mistral AI", qwen: "Qwen", deepseek: "DeepSeek",
  "x-ai": "xAI", cohere: "Cohere", microsoft: "Microsoft", nvidia: "NVIDIA", amazon: "Amazon", perplexity: "Perplexity", "z-ai": "Z.ai", moonshotai: "Moonshot AI",
  nousresearch: "Nous Research", replay: "Sample models", "ai21": "AI21", "01-ai": "01.AI", thudm: "THUDM", inflection: "Inflection", liquid: "Liquid",
};
const vendorName = (v: string) => VENDOR_NAMES[v] ?? v.replace(/^~/, "").replace(/[-_]/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export function openrouterEntries(models: OrModel[], featured: string[]): Entry[] {
  const feat = new Set(featured);
  return models.map((m) => ({
    id: m.id, name: m.name, desc: m.description || m.id, group: feat.has(m.id) ? "★ Featured" : vendorName(m.vendor), logo: vendorName(m.vendor).slice(0, 2),
    color: familyColor(vendorName(m.vendor)),
    badges: [
      ...(m.open_weights ? [{ text: "open", tone: "green" as const }] : []),
      ...(m.reasoning ? [{ text: "reasoning", tone: "violet" as const }] : []),
      ...(m.free ? [{ text: "free", tone: "cyan" as const }] : []),
    ],
    right: [
      ...(m.context_length ? [`${m.context_length >= 1e6 ? `${(m.context_length / 1e6).toFixed(1)}M` : `${Math.round(m.context_length / 1000)}k`} ctx`] : []),
      `${money(m.prompt_per_m)} / ${money(m.completion_per_m)}`,
    ],
    search: `${m.name} ${m.id} ${m.vendor} ${m.open_weights ? "open" : "closed"} ${m.reasoning ? "reasoning" : ""}`.toLowerCase(),
  }));
}
