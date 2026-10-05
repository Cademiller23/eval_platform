# Coherence Lab — model evaluation platform

Pick a model from a dropdown. The platform boots it on [Modal](https://modal.com) GPUs (vLLM) — or calls it through
[OpenRouter](https://openrouter.ai) / any OpenAI-compatible endpoint — runs a full
**coherency / garble** test plus **coding, math and general-purpose** suites, measures **decode tokens/s** and
**TTFT**, detects whether **speculative decoding** is in use, and finishes with a scorecard, a ship/no-ship
verdict, and model-specific instructions for adding speculative decoding, making the model faster and making
its output more coherent.

```
 dropdown ──▶ Modal container (vLLM) ──▶ benchmark ──▶ 4 domain suites ──▶ speculative detection ──▶ report
              (or any OpenAI-compatible                                                              ├ scores + verdict
               endpoint, or the built-in demo)                                                       ├ speculative-decoding recipe
                                                                                                     ├ speed-ups
                                                                                                     └ coherency fixes
```

## Quick start

```bash
git clone … && cd eval_platform
./scripts/start.sh            # installs deps, builds the UI, serves http://localhost:8000
# or:  make setup && make start
```

With no credentials the platform starts in **Demo mode** (simulated models — explore the whole UI and report
in seconds). To evaluate real models:

### Run on Modal (GPUs)

1. `pip install modal && modal token new` (or export `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`).
2. For gated models (Llama, Gemma) export a Hugging Face token: `export HF_TOKEN=hf_…`
   (copy `.env.example` to `.env` if you prefer a file).
3. `./scripts/start.sh`, open the page, choose a model.

The first run **deploys the serving app automatically** (`modal_app/serve.py`, builds the vLLM image — a few
minutes). Weights are cached in a Modal Volume, so later cold starts are much faster. Each distinct
(model, speculative-config) pair gets its own autoscaled container that shuts down 2 minutes after the run
(`EVAL_MODAL_SCALEDOWN`). You pay for GPU time while it is up; the *Quick mode* toggle runs 16 representative
tests instead of 38.

### Evaluate hosted models through OpenRouter

1. Create a key at <https://openrouter.ai/keys> and `export OPENROUTER_API_KEY=sk-or-…` before starting the server
   (or paste it into *Run options → OpenRouter API key*; it stays in that browser tab's session storage and is never
   stored by the server or written to reports).
2. Choose **OpenRouter** in *Run options*; the dropdown then lists OpenRouter's live model catalogue with context
   length and prices (featured models first, any `vendor/model` slug can be pasted).
3. Pick a model — it starts immediately. Turn on **Compare several models** to tick up to 4 models and get a
   side-by-side leaderboard, radar chart and takeaways.

What is different on a hosted API (and clearly labelled in the report): tokens/s and TTFT are measured **client-side**
(network + provider queueing included), the serving **engine is invisible** so speculative decoding is reported as
*cannot be observed* rather than guessed, cost comes from OpenRouter's `usage.cost`, the serving **provider** is recorded
per request (garbling is often provider-specific), and 429/5xx are retried with back-off. Coherency, coding, maths and
general scores are fully valid on hosted models. Open-weight models still get a self-hosting speculative-decoding
recipe; closed models get provider-routing advice instead.

### Evaluate a server you already run

Run options → *Custom endpoint* → base URL of any OpenAI-compatible server (vLLM, SGLang, TGI, Ollama,
LM Studio…). Speculative decoding is then inferred from `/metrics` (vLLM/SGLang) and token-arrival behaviour.

## Verifying that the suite itself is right

A scorecard is only useful if the checker can trust it. This repo ships the machinery to *prove* it, at four levels:

| Level | Command | What it establishes |
|---|---|---|
| **Real model outputs** | `python scripts/grade_recordings.py` | `verification/raw/*.md` holds the answers four different real models gave to all 41 evaluation prompts (38 tests + 3 open-ended generation probes). Graded result: **151/152** tests correct (4 models × 38 tests); the single miss is a *true* catch (a 41-word story against an "under 40 words" constraint); **0 false alarms** (0/152 real responses flagged as garbled, looping or leaking). |
| **Mutation testing** | `make test` (`test_real_recordings.py`) | Those real responses are corrupted programmatically — word-salad, loops, leaked `<\|im_end\|>` tokens, language drift, unterminated `<think>`, empty output, `�` bytes, runaway generation, wrong math answers, broken code — and the suite must catch ≥99%: measured **1008/1008** injected corruptions caught (100% for every corruption type) and **148/148** wrong answers rejected by the graders. |
| **Format tolerance** | `make test` (`test_robustness.py`) | Valid answers in many phrasings pass (`**Answer:** 240`, `\boxed{}`, `16.7%`, ` ```python3` fences, NFD-accented text…); wrong answers fail (`"aurum"` ≠ `Au`, `None` ≠ `False`); realistic markdown / LaTeX / emoji / CJK text is not flagged. |
| **Live hosted models** | `python scripts/verify_openrouter.py` | Evaluates a strong→weak spread of real OpenRouter models (default: GPT-4o-mini, Llama 3.3 70B, Llama 3.1 8B, Llama 3.2 1B), then runs two **stress controls** on the best one — temperature 2.0 (must produce flagged garbling) and negative repetition penalties (must produce flagged loops) — and asserts: strong ≫ weak, detectors fire on *real* corruption, no false alarms on high scorers, honest hosted semantics, cost tracking (`--repeat` adds reproducibility). It writes `verification/openrouter/{summary.md,reports/,review/}`; **read `review/*.md`** — it lists every failed test with prompt, response and failed checks so you can judge model-vs-grader. Exit code 0 = all assertions passed. A default run costs roughly cents. |

The stress controls are also in the UI (*Run options → Detector self-check*): they break decoding on the same model so you can
see the platform flag it, with a banner stating whether the detectors fired.

For offline use, `python -m evalplatform.devtools.replay_server` serves those recorded real answers behind an
OpenRouter-compatible API (set `EVAL_OPENROUTER_BASE_URL=http://127.0.0.1:9999/api/v1`, `OPENROUTER_API_KEY=test-key`),
including 429s, mid-stream errors and emulated stress behaviour. `make e2e` drives the whole UI in a real browser against
it (17 journey steps: dropdown → run → report → exports → stress banner → compare → history → theme → cancel →
errors → key handling → mobile layout).

> **Honesty note.** The development environment this was built in could not reach `openrouter.ai` (egress policy) and had
> no key, so the live-OpenRouter path was verified against the protocol-faithful replay server and real recorded outputs,
> *not* against OpenRouter itself. `scripts/verify_openrouter.py` is how you close that gap in one command.

## What gets measured

| Area | How |
|---|---|
| **Coherency** | Pure, deterministic detectors run on *every* response: Unicode replacement chars, mojibake, control bytes, random-character soup (entropy), vowel-less/consonant-run words, common-word coverage, n-gram & line loops, tail loops, compressibility, leaked chat-template tokens, language drift (stray CJK/Cyrillic…), runaway generation (short task hits `max_tokens`), unterminated `<think>`, and mean token log-probability. Plus targeted tests: long-form essay (T=0.7), multi-turn memory, ~3k-token needle-in-a-haystack, temperature-0 stability, multilingual/non-Latin output, counting 1–60 (loop probe), and "reply with OK" (EOS/chat-template check). |
| **Coding** | 9 Python tasks (palindromes, two-sum, interval merge, Roman numerals, brackets, LRU cache, flatten, Kadane, bug-fix). Generated code is executed against hidden unit tests in a resource-limited subprocess. |
| **Math** | 10 problems (arithmetic → number theory/probability) graded on the exact final answer (`Answer:`, `\boxed{}`, fractions, currency handled). |
| **General** | 12 tasks: strict instruction following, JSON-only output, constraint following, knowledge, logic, summarisation, translation. All programmatic checks — no LLM judge, so runs are reproducible and free. |
| **Speed** | Median decode tok/s over 6 streams, TTFT p50/p95, TTFT with a ~1.5k-token prompt, 8-way concurrency throughput, and efficiency vs the memory-bandwidth roofline of the GPU. Timestamps are taken **inside the Modal container**, so network latency never pollutes tok/s. |
| **Speculative decoding** | Three independent signals — engine config, Prometheus counters (`vllm:spec_decode_*`, `sglang:spec_accept_*`) diffed across the run, and token-arrival burstiness (multi-token chunks). Reports status (active / likely / not in use / unknown), confidence, acceptance rate and mean tokens per step. |

### Scoring

`overall = 30% coherency + 20% coding + 20% math + 15% general + 15% speed`, with **verdict gates**
(garbling in > 5% of responses, > 10% seriously broken responses, coherency < 50, or < 5 tok/s ⇒ *Not ready*;
overall ≥ 75 with coherency ≥ 75–80, every domain ≥ 45–50 and ≥ 15 tok/s ⇒ *Ready*; otherwise *Usable with caveats*).
Coherency = 60% output cleanliness across all responses + 40% the dedicated coherency tests.

### Recommendations (all tied to your run's evidence)

* **Speculative decoding** — ranked methods for *this* model (native MTP, EAGLE-3 head, n-gram, draft model;
  n-gram ranking uses the measured prompt-copy ratio of your outputs), exact `vllm serve` / SGLang commands,
  an ordered implementation plan (prerequisites → download → launch → verify → measure → tune `k` → Modal
  snippet → production caveats), and a **Try it now** button that re-runs the same model with the method
  enabled and shows the speed-up and any quality change against the baseline (A/B banner).
* **Faster** — FP8/AWQ quantisation, GPU upgrade with estimated tok/s, roofline-efficiency diagnosis,
  prefix-caching/chunked-prefill for TTFT, batching, MoE alternatives, cold-start tuning, reasoning budgets.
* **More coherent** — per-issue fixes with code (stop tokens / chat template, repetition penalties, bf16 vs
  fp16, tokenizer/numerics debugging, language pinning, YaRN/context limits, structured outputs, self-consistency
  for maths, …) plus model-family quirks (Llama EOS ids, Gemma fp16 overflow, Qwen language drift, R1 sampling…).

## Architecture

```
frontend/            React + Vite UI (dropdown, live run view, report)
backend/evalplatform/
  api.py             FastAPI REST + SSE; serves the built UI
  manager.py         run lifecycle, event replay, persistence (data/runs/*.json)
  runner.py          phases: provision → warmup → perf → suites → speculative → analysis
  providers/         modal_provider (Modal SDK) · openrouter_provider · openai_provider (any endpoint) · mock_provider (demo)
  checker.py         the verification harness behind scripts/verify_openrouter.py
  devtools/          replay_server.py — OpenRouter-compatible server replaying recorded real answers
  suite/             tasks.py (+graders) · coherence.py (detectors) · sandbox.py (code execution)
  speculative.py     config + metrics + behaviour detection
  scoring.py         scores & verdict            recommendations.py   the advice engine
  catalog.py         the model dropdown          knowledge.py         GPU table, roofline math
modal_app/serve.py   parameterised Modal class: boots vLLM, exposes timed streaming methods
```

Add a model: append an entry to `backend/evalplatform/catalog.py` (or paste any `org/name` into the dropdown
search for an ad-hoc run). Add a test: append a `Task` in `suite/tasks.py` — the test-suite automatically
checks that its reference answer passes its grader.

## API

`GET /api/config` · `GET /api/models` · `POST /api/runs` · `GET /api/runs` · `GET /api/runs/{id}` ·
`GET /api/runs/{id}/events` (SSE) · `POST /api/runs/{id}/cancel` · `DELETE /api/runs/{id}` ·
`GET /api/runs/{id}/report.md|json`. Interactive docs at `/docs`.

```bash
curl -XPOST localhost:8000/api/runs -H 'content-type: application/json' \
     -d '{"model_id":"llama-3.1-8b","provider":"modal","speculative":"eagle3"}'
```

## Notes & limits

* Demo mode is a simulation — numbers there are synthetic. Use Modal or a real endpoint for real measurements.
* The code sandbox is best-effort (temp dir, scrubbed env, CPU/memory/file limits, timeout, static blocklist).
  It is not a security boundary; run the platform in a container if you evaluate untrusted models.
* Speculative-decoding artifacts flagged *verify repo* are community-maintained; draft-model support varies by
  engine version (vLLM V1 only recently regained it). Check compatibility before relying on them.
* Heuristic detectors can false-positive on unusual-but-valid text; every flag is shown with its evidence and the
  raw response so you can judge. The default suite is deliberately small and fast — it is a sanity/regression
  gate, not a replacement for large benchmarks.
* Tests: `make test` (≈ 200: detectors, graders, sandbox, speculative detection, scoring, API, OpenAI-compatible and
  OpenRouter streaming incl. retries / mid-stream errors / key hygiene, Modal container code against a fake vLLM,
  real-output grading and mutation tests); `make e2e` for the browser journey; `make verify` for live OpenRouter.

## Configuration

See `.env.example`. Useful variables: `OPENROUTER_API_KEY`, `EVAL_OPENROUTER_BASE_URL`, `EVAL_OPENROUTER_CONCURRENCY`, `EVAL_DEFAULT_PROVIDER`, `EVAL_MODAL_APP`, `EVAL_VLLM_VERSION` (deploy
time), `EVAL_MODAL_SCALEDOWN`, `EVAL_MODAL_MAX_CONTAINERS`, `EVAL_PORT`, `EVAL_DATA_DIR`, `EVAL_MOCK_PACE`, `EVAL_HOST`, `EVAL_CORS_ORIGINS`.

**Security:** the server has no authentication and can spend money on your Modal/OpenRouter accounts, so it binds to
`127.0.0.1`, sends no CORS headers, only answers requests addressed to a loopback host name (blocks DNS-rebinding;
add names via `EVAL_ALLOWED_HOSTS`), rejects state-changing requests from a different Origin (allow a dev frontend with
`EVAL_CORS_ORIGINS`), never echoes request values in validation errors, and redacts API keys from error messages. Only set `EVAL_HOST=0.0.0.0` behind a trusted network or an
authenticating reverse proxy.
