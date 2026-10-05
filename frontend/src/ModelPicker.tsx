import { useEffect, useMemo, useRef, useState } from "react";
import type { ModelInfo } from "./types";
import { Badge, Icon, familyColor } from "./ui";

interface Props {
  models: ModelInfo[];
  disabled?: boolean;
  busy?: boolean;
  onSelect: (sel: { model_id?: string; custom_hf?: string }) => void;
}

const HF_RE = /^[\w.\-]+\/[\w.\-]+$/;

export function ModelPicker({ models, onSelect, disabled, busy }: Props) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const h = (e: MouseEvent) => { if (root.current && !root.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);
  useEffect(() => { if (open) setTimeout(() => input.current?.focus(), 30); else setQ(""); }, [open]);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    return models.filter((m) => !s || `${m.name} ${m.hf_repo} ${m.family} ${m.tags.join(" ")}`.toLowerCase().includes(s));
  }, [models, q]);
  const customCandidate = HF_RE.test(q.trim()) && !models.some((m) => m.hf_repo.toLowerCase() === q.trim().toLowerCase()) ? q.trim() : null;

  const flat: { kind: "custom" | "model"; model?: ModelInfo; repo?: string }[] = [
    ...(customCandidate ? [{ kind: "custom" as const, repo: customCandidate }] : []),
    ...filtered.map((m) => ({ kind: "model" as const, model: m })),
  ];
  useEffect(() => setIdx(0), [q]);

  const groups = useMemo(() => {
    const g = new Map<string, ModelInfo[]>();
    filtered.forEach((m) => g.set(m.family, [...(g.get(m.family) ?? []), m]));
    return [...g.entries()];
  }, [filtered]);

  const choose = (item: (typeof flat)[number]) => {
    setOpen(false);
    if (item.kind === "custom") onSelect({ custom_hf: item.repo });
    else onSelect({ model_id: item.model!.id });
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(flat.length - 1, i + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)); }
    else if (e.key === "Enter" && flat[idx]) { e.preventDefault(); choose(flat[idx]); }
    else if (e.key === "Escape") setOpen(false);
  };
  const posOf = (m: ModelInfo) => flat.findIndex((f) => f.model?.id === m.id);

  return (
    <div className={`picker ${open ? "open" : ""}`} ref={root}>
      <button className="picker-trigger" onClick={() => setOpen((o) => !o)} disabled={disabled || busy} aria-haspopup="listbox" aria-expanded={open}>
        <span style={{ display: "grid", placeItems: "center", color: "var(--violet)" }}><Icon name={busy ? "refresh" : "flask"} size={22} /></span>
        <span className="ph">{busy ? "Starting evaluation…" : "Select a model to evaluate"}</span>
        <span className="chev"><Icon name="chev" size={20} /></span>
      </button>

      {open && (
        <div className="picker-menu" role="listbox" onKeyDown={onKey}>
          <div className="picker-search">
            <Icon name="search" size={18} />
            <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search models — or paste any Hugging Face repo (org/name)" onKeyDown={onKey} />
            <span className="faint" style={{ fontSize: 12 }}>{filtered.length} models</span>
          </div>
          <div className="picker-list">
            {customCandidate && (
              <button className={`picker-item ${idx === 0 ? "active" : ""}`} onClick={() => choose(flat[0])}>
                <span className="logo" style={{ background: "var(--grad)" }}>+</span>
                <span className="meta">
                  <span className="name">Evaluate custom model</span>
                  <span className="desc mono">{customCandidate}</span>
                </span>
                <span className="right"><Badge tone="violet">custom</Badge></span>
              </button>
            )}
            {groups.map(([family, ms]) => (
              <div key={family}>
                <div className="picker-group">{family}</div>
                {ms.map((m) => (
                  <button key={m.id} className={`picker-item ${posOf(m) === idx ? "active" : ""}`} onClick={() => choose({ kind: "model", model: m })} onMouseEnter={() => setIdx(posOf(m))} role="option">
                    <span className="logo" style={{ background: familyColor(m.family) }}>{m.family.slice(0, 2)}</span>
                    <span className="meta">
                      <span className="name">{m.name}{m.reasoning && <Badge tone="violet">reasoning</Badge>}{m.mtp_native && <Badge tone="cyan">MTP</Badge>}</span>
                      <span className="desc">{m.description}</span>
                    </span>
                    <span className="right">
                      <Badge>{m.params_b >= 100 ? `${Math.round(m.params_b)}B` : `${+m.params_b.toFixed(1)}B`}{m.active_params_b < m.params_b ? ` · ${+m.active_params_b.toFixed(0)}B act` : ""}</Badge>
                      <Badge>{m.min_gpu}</Badge>
                    </span>
                  </button>
                ))}
              </div>
            ))}
            {!flat.length && <div className="picker-empty">No match. Paste a Hugging Face repo id like <span className="mono">mistralai/Mistral-7B-v0.1</span>.</div>}
          </div>
          <div className="picker-foot">
            <span>↑↓ navigate · Enter to start</span>
            <span>Picking a model boots it and starts the evaluation</span>
          </div>
        </div>
      )}
    </div>
  );
}
