import type { AppConfig, ModelInfo, RunFull, RunOptions, RunSummary } from "./types";

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const d = await res.json();
      msg = typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail ?? d);
    } catch { /* ignore */ }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

export const api = {
  config: () => fetch("/api/config").then((r) => j<AppConfig>(r)),
  models: () => fetch("/api/models").then((r) => j<ModelInfo[]>(r)),
  runs: () => fetch("/api/runs").then((r) => j<RunSummary[]>(r)),
  run: (id: string) => fetch(`/api/runs/${id}`).then((r) => j<RunFull>(r)),
  start: (opts: RunOptions) =>
    fetch("/api/runs", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(opts) }).then((r) => j<{ run_id: string }>(r)),
  cancel: (id: string) => fetch(`/api/runs/${id}/cancel`, { method: "POST" }).then((r) => j<{ cancelled: boolean }>(r)),
  remove: (id: string) => fetch(`/api/runs/${id}`, { method: "DELETE" }).then((r) => j<{ deleted: boolean }>(r)),
};
