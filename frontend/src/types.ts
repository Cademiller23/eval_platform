export interface ModelInfo {
  id: string; name: string; family: string; hf_repo: string; params_b: number; active_params_b: number;
  context: number; min_gpu: string; gated: boolean; reasoning: boolean; tags: string[]; description: string;
  mtp_native: boolean; speculators: string[];
}
export interface ProviderInfo { id: "modal" | "openrouter" | "openai" | "mock"; label: string; available: boolean; hint: string }
export interface GpuInfo { id: string; mem_gb: number; bw_gbs: number; arch: string }
export interface AppConfig {
  version: string; default_provider: string; providers: ProviderInfo[]; gpus: GpuInfo[]; hf_token_set: boolean; openrouter_key_set: boolean;
  speculative_modes: { id: string; label: string; hint: string }[]; suite: { full: number; quick: number };
}
export interface RunOptions {
  model_id?: string; custom_model?: { hf_repo: string; params_b?: number; reasoning?: boolean };
  provider?: string; gpu?: string; speculative?: string; speculative_custom?: string; quick?: boolean;
  max_model_len?: number; temperature?: number; endpoint?: { base_url: string; api_key?: string; model?: string };
  parent_run_id?: string; openrouter_model?: string; openrouter_key?: string; stress?: "garble" | "loop" | null;
}
export interface OrModel {
  id: string; name: string; vendor: string; context_length: number | null; prompt_per_m: number | null; completion_per_m: number | null;
  free: boolean; open_weights: boolean; hf_id: string | null; reasoning: boolean; description: string;
}
export interface OrModels { live: boolean; count: number; featured: string[]; models: OrModel[] }
export interface OrStatus { configured: boolean; valid: boolean | null; free_tier?: boolean; usage?: number; limit?: number | null; remaining?: number | null; error?: string }
export interface Phase { id: string; title: string; status: "pending" | "running" | "done" | "error" | "skipped"; detail: string }
export interface Check { name: string; passed: boolean; detail: string }
export interface Issue { kind: string; severity: "minor" | "major" | "critical"; detail: string; value?: number | null }
export interface TestResult {
  id: string; domain: "coherency" | "coding" | "math" | "general"; name: string; difficulty: string; skill: string;
  prompt: string; response: string; passed: boolean; score: number; checks: Check[]; error?: string | null;
  health: { issues: Issue[]; metrics: Record<string, number>; severe: boolean; garbled: boolean; repetitive: boolean; severity_score: number } | null;
  metrics: { ttft_ms?: number | null; decode_tps?: number | null; tokens?: number; duration_ms?: number; finish_reason?: string | null };
  thinking_chars?: number; copy_ratio?: number | null;
}
export interface Verdict { label: "ready" | "caution" | "not_ready"; title: string; summary: string; blockers: string[]; warnings: string[]; strengths: string[] }
export interface Scores { overall: number; coherency: number; coding: number; math: number; general: number; performance: number; grade: string }
export interface Perf {
  decode_tps_median: number | null; decode_tps_runs: number[]; decode_tps_min?: number; decode_tps_max?: number;
  ttft_ms_p50: number | null; ttft_ms_p95: number | null; ttft_long_ms: number | null;
  concurrent: { n: number; aggregate_tps: number | null; ok: number } | null; tokens_generated: number;
  roofline?: { theoretical_tps: number; efficiency: number; gpu: string; gpu_count: number };
}
export interface Evidence { source: string; positive: boolean | null; text: string }
export interface SpecMethod {
  method: string; title: string; fit: "best" | "good" | "ok"; expected_speedup: string; why: string; pros: string[]; cons: string[];
  repo: string | null; verified: boolean; note?: string | null; config: Record<string, unknown>; vllm_cmd: string; sglang_cmd: string | null;
}
export interface Spec {
  status: "active" | "likely" | "not_detected" | "unknown"; headline: string; confidence: number; method: string | null;
  evidence: Evidence[]; acceptance_rate: number | null; mean_accepted_length: number | null;
  multi_token_chunk_ratio: number; tokens_per_chunk: number; drafts: number | null; draft_tokens: number | null; accepted_tokens: number | null; hosted?: boolean;
  copy_ratio_by_domain?: Record<string, number>; native_mtp: boolean;
}
export interface Step { title: string; body: string; code: string | null; lang: string | null }
export interface SpecPlan { status: string; summary: string; methods: SpecMethod[]; steps: Step[]; recommended: string | null; apply: { speculative: string; config: Record<string, unknown> } | null }
export interface Rec { id: string; title: string; impact: "high" | "medium" | "low" | "info"; why: string; detail: string; code: string | null; effort?: string; severity?: string }
export interface CoherencySummary {
  responses: number; clean_ratio: number; clean_score: number; garble_rate: number; repetition_rate: number; special_token_leak_rate: number;
  empty_rate: number; truncation_rate: number; language_drift_rate: number; runaway_rate: number; severe_rate: number; mean_logprob: number | null;
  issue_kinds: Record<string, { count: number; tests: string[] }>;
}
export interface Environment {
  provider?: string; engine?: string; engine_version?: string; gpu?: string; gpu_names?: string[]; gpu_count?: number; requested_gpu?: string;
  max_model_len?: number; dtype?: string; quantization?: string | null; speculative_config?: Record<string, unknown> | null;
  cold_start_s?: number; provision_s?: number; command?: string; simulated?: boolean; base_url?: string;
  hosted?: boolean; open_weights?: boolean; hf_id?: string | null; pricing?: { prompt_per_m: number | null; completion_per_m: number | null }; free_tier?: boolean;
  providers_seen?: Record<string, number>; served_model?: string;
}
export interface Report {
  run_id: string; generated_at: string; duration_s: number; model: Omit<ModelInfo, "min_gpu" | "speculators">;
  options: { provider: string; gpu: string | null; speculative: string; speculative_config: Record<string, unknown> | null; quick: boolean; max_model_len: number; parent_run_id?: string | null; stress?: string | null; openrouter_model?: string | null };
  usage?: { cost_usd?: number | null; prompt_tokens?: number; completion_tokens?: number; providers_seen?: Record<string, number> };
  environment: Environment; scores: Scores; verdict: Verdict; domains: Record<string, { score: number; passed: number; total: number }>;
  performance: Perf; coherency: CoherencySummary; speculative: Spec; tests: TestResult[];
  recommendations: { speculative: SpecPlan; speed: Rec[]; coherence: Rec[] };
}
export interface RunSummary {
  id: string; status: "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted"; created_at: string; finished_at: string | null;
  error: string | null; model: { id: string; name: string; family: string; hf_repo: string; params_b: number };
  options: RunOptions; scores: Scores | null; verdict: string | null; decode_tps: number | null; speculative_status: string | null; phases: Phase[];
}
export interface RunFull extends RunSummary { report: Report | null; environment: Environment | null }
export interface LogLine { level: string; message: string; ts: string }
