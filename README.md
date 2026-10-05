# Coherence Lab — model evaluation platform

Pick a model from a dropdown. The platform boots it on [Modal](https://modal.com) GPUs (vLLM), runs a full
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

### Evaluate a server you already run

Run options → *Custom endpoint* → base URL of any OpenAI-compatible server (vLLM, SGLang, TGI, Ollama,
LM Studio…). Speculative decoding is then inferred from `/metrics` (vLLM/SGLang) and token-arrival behaviour.

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
  providers/         modal_provider (Modal SDK) · openai_provider (any endpoint) · mock_provider (demo)
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
* Tests: `make test` (detectors, graders, sandbox, speculative detection, scoring, API, OpenAI-compatible
  streaming, Modal container code against a fake vLLM).

## Configuration

See `.env.example`. Useful variables: `EVAL_DEFAULT_PROVIDER`, `EVAL_MODAL_APP`, `EVAL_VLLM_VERSION` (deploy
time), `EVAL_MODAL_SCALEDOWN`, `EVAL_MODAL_MAX_CONTAINERS`, `EVAL_PORT`, `EVAL_DATA_DIR`, `EVAL_MOCK_PACE`, `EVAL_HOST`, `EVAL_CORS_ORIGINS`.

**Security:** the server has no authentication and can spend money on your Modal account, so it binds to
`127.0.0.1` and sends no CORS headers by default. Only set `EVAL_HOST=0.0.0.0` behind a trusted network or an
authenticating reverse proxy.
