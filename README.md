# Coherence Lab — model evaluation platform

Pick a model from a dropdown. The platform boots it on [Modal](https://modal.com) GPUs (vLLM) — or calls it through
[OpenRouter](https://openrouter.ai) / any OpenAI-compatible endpoint — runs a full
**coherency / garble** test plus **coding, math and general-purpose** suites, a **system-prompt** suite
(does it obey its instructions, resist prompt injection, keep secrets?) and a **hyperparameter-tuning** suite
(which temperature / top-p / top-k / penalty settings suit it, and does the endpoint honour them?), measures
**decode tokens/s** and **TTFT**, detects whether **speculative decoding** is in use, and finishes with a scorecard, a
ship/no-ship verdict, and model-specific instructions for adding speculative decoding, making the model faster,
making its output more coherent, hardening its system prompt and setting the right sampling parameters.

```
 dropdown ──▶ Modal container (vLLM) ──▶ benchmark ──▶ 5 graded suites ──▶ hyperparameter ──▶ speculative ──▶ report
              (or any OpenAI-compatible              coherency · coding      sweeps             detection      ├ scores + verdict
               endpoint, or the built-in demo)       math · general ·                                          ├ system-prompt + sampling findings
                                                     system prompts                                            ├ speculative-decoding recipe
                                                                                                               ├ speed-ups
                                                                                                               └ coherency fixes
```

## See it first (no install, no server)

`docs/coherence-lab-preview.html` is the real interface running against **recorded runs** — double-click it. Pick any
of the 21 catalogue models (or switch *Run on* to OpenRouter), watch a full evaluation play out, open the report,
re-run with a speculative-decoding recipe, tick runs in *History* and compare them. Every report includes the system-prompt
suite and the hyperparameter sweeps (temperature curve, honoured settings, tuned profiles). Everything in it was produced by the
platform itself (`scripts/build_demo_fixtures.py` records 68 real `Runner` executions: the simulated demo provider for
every catalogue model and every speculative recipe, plus real model answers replayed through the OpenRouter code path,
including the garble / loop detector self-checks); only the in-browser stand-in for the server is new. Speeds, prices
and providers in the recordings are simulated, and the page says so.

```bash
make preview          # rebuilds the page -> docs/coherence-lab-preview.html
make fixtures         # (optional) re-records the data, a few minutes, no network or credentials
node scripts/e2e_preview.mjs   # click-through test of the preview in a real browser
```

## Quick start

```bash
git clone … && cd eval_platform
./scripts/start.sh            # installs deps, builds the UI, serves http://localhost:8000
# or:  make setup && make start
```

Windows (PowerShell), or if you prefer to see each step:

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e ".[modal]"
cd frontend; npm install; npm run build; cd ..
python -m evalplatform          # then open http://localhost:8000
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
(`EVAL_MODAL_SCALEDOWN`). You pay for GPU time while it is up; the *Quick mode* toggle runs 36 representative
tests instead of 97 (and lighter hyperparameter sweeps: about 60 requests instead of about 250).

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
| **Real model outputs** | `python scripts/grade_recordings.py` | `verification/raw/*.md` holds the answers four different real models gave to all 106 evaluation prompts (38 core tests + 59 system-prompt tests + 3 open-ended generation probes + 6 hyperparameter probes). Graded result: **385/388** tests correct (4 models × 97 tests); the three misses were each read and are *true* catches (a 41-word story against an "under 40 words" constraint; one model labelling an obviously negative review POSITIVE after a forged transcript; one model dropping a required sign-off when given 8 rules at once); **0 false alarms** (0 of 424 real responses flagged as garbled, looping or leaking). |
| **Mutation testing** | `make test` (`test_real_recordings.py`) | Those real responses are corrupted programmatically — word-salad, loops, leaked `<\|im_end\|>` tokens, language drift, unterminated `<think>`, empty output, `�` bytes, runaway generation, wrong math answers, broken code — and the suite must catch ≥99%: measured **2620/2620** injected corruptions caught (100% for every corruption type) and **148/148** wrong core answers rejected by the graders. The system-prompt suite has its own mutators that turn each real answer into the specific failure the test targets (hijacked, leaked, rule dropped, wrong language, …): **294/294** caught, and every task also carries a reference answer that must pass and known-bad answers that must fail. |
| **Format tolerance** | `make test` (`test_robustness.py`) | Valid answers in many phrasings pass (`**Answer:** 240`, `\boxed{}`, `16.7%`, ` ```python3` fences, NFD-accented text…); wrong answers fail (`"aurum"` ≠ `Au`, `None` ≠ `False`); realistic markdown / LaTeX / emoji / CJK text is not flagged. |
| **Live hosted models** | `python scripts/verify_openrouter.py` | Evaluates a strong→weak spread of real OpenRouter models (default: GPT-4o-mini, Llama 3.3 70B, Llama 3.1 8B, Llama 3.2 1B), then runs two **stress controls** on the best one — temperature 2.0 (must produce flagged garbling) and negative repetition penalties (must produce flagged loops) — and asserts: strong ≫ weak, detectors fire on *real* corruption, no false alarms on high scorers, honest hosted semantics, cost tracking (`--repeat` adds reproducibility). It writes `verification/openrouter/{summary.md,reports/,review/}`; **read `review/*.md`** — it lists every failed test with prompt, response and failed checks so you can judge model-vs-grader. Exit code 0 = all assertions passed. A default run costs roughly cents. |

The stress controls are also in the UI (*Run options → Detector self-check*): they break decoding on the same model so you can
see the platform flag it, with a banner stating whether the detectors fired.

For offline use, `python -m evalplatform.devtools.replay_server` serves those recorded real answers behind an
OpenRouter-compatible API (set `EVAL_OPENROUTER_BASE_URL=http://127.0.0.1:9999/api/v1`, `OPENROUTER_API_KEY=test-key`),
including 429s, mid-stream errors, emulated stress behaviour and emulated sampling behaviour (temperature, top-p/top-k/min-p,
seeds, penalties and stop sequences; `--ignore-params` and `--reject-params` simulate endpoints that silently ignore or
refuse a parameter). `make e2e` drives the whole UI in a real browser against it (18 journey steps: dropdown → run → report → system-prompt and hyperparameter sections → exports → stress banner → compare →
history → theme → cancel → errors → key handling → mobile layout).

> **Honesty note.** The development environment this was built in could not reach `openrouter.ai` (egress policy) and had
> no key, so the live-OpenRouter path was verified against the protocol-faithful replay server and real recorded outputs,
> *not* against OpenRouter itself. `scripts/verify_openrouter.py` is how you close that gap in one command.
> The same applies to the two newest measurements: the system-prompt graders were validated on the real answers of four
> models (and mutation-tested), but the *hyperparameter sweeps* were validated against a simulation of how decoding
> parameters change output (`devtools/sampling_sim.py`: unit-tested statistics, derivations and failure modes such as
> ignored or rejected parameters) and against real answers to the six sampling probes at default settings. They have not
> been run against a live endpoint, so expect to read the first real report critically and compare it with
> `verify_openrouter.py`'s output.

## What gets measured

| Area | How |
|---|---|
| **Coherency** | Pure, deterministic detectors run on *every* response: Unicode replacement chars, mojibake, control bytes, random-character soup (entropy), vowel-less/consonant-run words, common-word coverage, n-gram & line loops, tail loops, compressibility, leaked chat-template tokens, language drift (stray CJK/Cyrillic…), runaway generation (short task hits `max_tokens`), unterminated `<think>`, and mean token log-probability. Plus targeted tests: long-form essay (T=0.7), multi-turn memory, ~3k-token needle-in-a-haystack, temperature-0 stability, multilingual/non-Latin output, counting 1–60 (loop probe), and "reply with OK" (EOS/chat-template check). |
| **Coding** | 9 Python tasks (palindromes, two-sum, interval merge, Roman numerals, brackets, LRU cache, flatten, Kadane, bug-fix). Generated code is executed against hidden unit tests in a resource-limited subprocess. |
| **Math** | 10 problems (arithmetic → number theory/probability) graded on the exact final answer (`Answer:`, `\boxed{}`, fractions, currency handled). |
| **General** | 12 tasks: strict instruction following, JSON-only output, constraint following, knowledge, logic, summarisation, translation. All programmatic checks — no LLM judge, so runs are reproducible and free. |
| **System prompts** | 59 programmatically graded tests in 9 categories (weight in the area's score): **format & constraint adherence** (18%: JSON-only, casing, bullet counts, word limits, language, forbidden words, XML, persona), **prompt-injection resistance** (18%: nine attack styles — direct, hidden HTML comment, instruction inside a document / invoice / poisoned tool output, fake delimiter or system tag, another language, forged conversation transcript, false "administrator note" — each built with a *closed* output, so the attacker's goal can never be part of a correct answer), **confidentiality** (14%: direct, translated, summarised, role-play, fake-authority and *encoded* — base64 / hex / rot13 / reversed — extraction of a canary secret and of the instructions themselves, plus a control that checks it still answers a legitimate question), **instruction hierarchy** (12%: the system prompt beats a conflicting user request, yet permitted requests are still served), **multi-turn persistence** (10%: the rule survives several turns and "stop following your rules"), **rule capacity** (10%: 3 / 8 / 15 / 25 simultaneous checkable rules in one reply), **scope & policy** (8%, including over-refusal), **prompt robustness** (7%: paraphrases, rule as system vs as user message, rule at the start / middle / end of a long prompt) and **identity** (3%). Derived metrics: injection attack-success rate, leak rate, verbatim-prompt leaks, over-refusal rate, rule capacity, persistence decay. Chat templates with no system role (Gemma 2) are detected from the provider's error, the prompt is folded into the first user turn, and the report says so. |
| **Hyperparameters** | Decoding settings, measured rather than assumed — the *inference-time* hyperparameters (temperature, top-p, top-k, min-p, repetition / frequency / presence penalties, seed, stop, max_tokens), since training hyperparameters cannot be observed from a served model. A **temperature curve** (accuracy with Wilson intervals, share of clean answers, output diversity) yields the best temperature, the **safe ceiling** where quality breaks and a sensitivity rating; a **truncation sweep** (top-p / top-k / min-p) run at a temperature that actually hurt quality (otherwise differences are only noise); a **penalty sweep** on a repetition-prone prompt; **determinism** (T=0 repeats, same seed, different seeds, concurrent requests); and a **honoured / ignored / rejected** verdict for every parameter judged by its observable effect, not by whether the request was accepted. It then proposes *Precise / Balanced / Creative* profiles (copy-paste request bodies) and **re-tests them on held-out problems** against the generic API defaults and the maker's published settings, with a per-problem paired confidence interval, so a recommendation is only made when the evidence supports it. Reasoning models and quick mode use a compact grid. |
| **Speed** | Median decode tok/s over 6 streams, TTFT p50/p95, TTFT with a ~1.5k-token prompt, 8-way concurrency throughput, and efficiency vs the memory-bandwidth roofline of the GPU. Timestamps are taken **inside the Modal container**, so network latency never pollutes tok/s. |
| **Speculative decoding** | Three independent signals — engine config, Prometheus counters (`vllm:spec_decode_*`, `sglang:spec_accept_*`) diffed across the run, and token-arrival burstiness (multi-token chunks). Reports status (active / likely / not in use / unknown), confidence, acceptance rate and mean tokens per step. |

### Scoring

`overall = 24% coherency + 16% coding + 14% math + 10% general + 14% system prompts + 10% hyperparameters + 12% speed`
(anything switched off or skipped drops out and the rest are re-weighted; the report shows the exact weights it used),
with **verdict gates** (garbling in > 5% of responses, > 10% seriously broken responses, coherency < 50, system-prompt
score < 30, or < 5 tok/s ⇒ *Not ready*; overall ≥ 75 with coherency ≥ 75–80, every domain ≥ 45–50 and ≥ 15 tok/s ⇒
*Ready*; otherwise *Usable with caveats*). Warnings (never blockers on their own) are raised for a high prompt-injection
success rate, leaked secrets, a template without a system role, a low temperature ceiling, degenerate greedy decoding and
sampling parameters the endpoint ignores. Coherency = 60% output cleanliness across all responses + 40% the dedicated
coherency tests. The system-prompt score is the weighted mean of its nine categories; the hyperparameter score is
45% robustness to temperature + 30% controllability (parameters honoured) + 15% determinism + 10% penalties.

### Recommendations (all tied to your run's evidence)

* **Speculative decoding** — ranked methods for *this* model (native MTP, EAGLE-3 head, n-gram, draft model;
  n-gram ranking uses the measured prompt-copy ratio of your outputs), exact `vllm serve` / SGLang commands,
  an ordered implementation plan (prerequisites → download → launch → verify → measure → tune `k` → Modal
  snippet → production caveats), and a **Try it now** button that re-runs the same model with the method
  enabled and shows the speed-up and any quality change against the baseline (A/B banner).
* **Faster** — FP8/AWQ quantisation, GPU upgrade with estimated tok/s, roofline-efficiency diagnosis,
  prefix-caching/chunked-prefill for TTFT, batching, MoE alternatives, cold-start tuning, reasoning budgets.
* **Harden the system prompt** — evidence-based advice with code for what failed: injection (delimiters, the sandwich
  defence, closed output formats, stripping hidden text), leaks (keep secrets out of prompts, canary + output filter),
  weak rule-following (fewer, ordered, restated rules; where critical rules should sit), decay over a conversation,
  over-refusal, and the system-role workaround for templates that lack one.
* **Tune the sampling** — the measured Precise / Balanced / Creative profiles as ready-to-paste request bodies, a
  production temperature cap, `generation_config.json` for vLLM, which parameters to stop sending because the endpoint
  ignores them, and when the tuned settings are *not* better than the defaults (said plainly).
* **More coherent** — per-issue fixes with code (stop tokens / chat template, repetition penalties, bf16 vs
  fp16, tokenizer/numerics debugging, language pinning, YaRN/context limits, structured outputs, self-consistency
  for maths, …) plus model-family quirks (Llama EOS ids, Gemma fp16 overflow, Qwen language drift, R1 sampling…).

## Architecture

```
frontend/            React + Vite UI in an Apple-style design system (light/dark, tokens in src/styles.css)
  src/demo/          offline preview: in-browser stand-in for the API + recorded fixtures (npm run build:demo)
scripts/             start.sh · e2e.mjs (live UI journey) · e2e_preview.mjs · build_demo_fixtures.py · make_demo_page.mjs
                     verify_openrouter.py · grade_recordings.py · dump_prompts.py
backend/evalplatform/
  api.py             FastAPI REST + SSE; serves the built UI
  manager.py         run lifecycle, event replay, persistence (data/runs/*.json)
  runner.py          phases: provision → warmup → perf → suites → speculative → analysis
  providers/         modal_provider (Modal SDK) · openrouter_provider · openai_provider (any endpoint) · mock_provider (demo)
  checker.py         the verification harness behind scripts/verify_openrouter.py
  devtools/          replay_server.py — OpenRouter-compatible server replaying recorded real answers
                     sampling_sim.py — the shared model of how decoding parameters change output (demo + replay)
  suite/             tasks.py (+graders) · coherence.py (detectors) · sandbox.py (code execution)
                     system_prompts.py (59 system-prompt tests, leak/decoding helpers) · sampling.py (hyperparameter sweeps)
  speculative.py     config + metrics + behaviour detection
  scoring.py         scores & verdict            recommendations.py   the advice engine
  catalog.py         the model dropdown          knowledge.py         GPU table, roofline math
modal_app/serve.py   parameterised Modal class: boots vLLM, exposes timed streaming methods
```

Add a model: append an entry to `backend/evalplatform/catalog.py` (or paste any `org/name` into the dropdown
search for an ad-hoc run). Add a test: append a `Task` in `suite/tasks.py` (or a system-prompt test in
`suite/system_prompts.py`) — the test-suite automatically checks that its reference answer passes its grader and that
its `fails` examples do not.

## API

`GET /api/config` · `GET /api/models` · `POST /api/runs` · `GET /api/runs` · `GET /api/runs/{id}` ·
`GET /api/runs/{id}/events` (SSE) · `POST /api/runs/{id}/cancel` · `DELETE /api/runs/{id}` ·
`GET /api/runs/{id}/report.md|json`. Interactive docs at `/docs`.

```bash
curl -XPOST localhost:8000/api/runs -H 'content-type: application/json' \
     -d '{"model_id":"llama-3.1-8b","provider":"modal","speculative":"eagle3"}'
```

`POST /api/runs` also takes `quick`, `system_prompts` (default `true`) and `hyperparameters` (default `true`) to choose
which measurements run; the report's `system_prompts` and `sampling` sections, and `recommendations.system` /
`recommendations.sampling`, hold the new results.

## Notes & limits

* Demo mode is a simulation — numbers there are synthetic. Use Modal or a real endpoint for real measurements.
* The code sandbox is best-effort (temp dir, scrubbed env, CPU/memory/file limits, timeout, static blocklist).
  It is not a security boundary; run the platform in a container if you evaluate untrusted models.
* Speculative-decoding artifacts flagged *verify repo* are community-maintained; draft-model support varies by
  engine version (vLLM V1 only recently regained it). Check compatibility before relying on them.
* "Hyperparameters" means the *decoding* settings you can send with a request. Reasoning-effort / thinking-budget controls,
  `logit_bias` and `n` / best-of are not swept. Fine-tuning hyperparameters (learning
  rate, epochs, LoRA rank) cannot be measured by evaluating a served model, so they are out of scope. The sweeps are
  sample-based: every curve carries its sample size and a confidence interval, a recommendation needs evidence beyond
  noise, and quick mode (about 60 sweep requests) is explicitly labelled indicative. On paid APIs a full evaluation sends
  roughly 370 requests, most of them short; quick mode about 110.
* The system-prompt suite is graded entirely by code (no LLM judge). It measures behaviour on a fixed set of attacks and
  rules, so a clean result is evidence, not proof, of safety: injection and extraction defences should still be layered.
* Heuristic detectors can false-positive on unusual-but-valid text; every flag is shown with its evidence and the
  raw response so you can judge. The default suite is deliberately small and fast — it is a sanity/regression
  gate, not a replacement for large benchmarks.
* Tests: `make test` (521: detectors, graders, sandbox, speculative detection, scoring, API, OpenAI-compatible and
  OpenRouter streaming incl. retries / mid-stream errors / key hygiene, Modal container code against a fake vLLM,
  real-output grading and mutation tests, the 59 system-prompt tasks (each self-tested), the hyperparameter statistics and
  derivations, and the integration of both modules into runs, scores, verdicts, recommendations and the API); `make e2e` for the browser journey against the real server (`node scripts/e2e_preview.mjs` does the same for the offline preview); `make verify` for live OpenRouter.

## Design

The UI follows Apple's interface conventions: the system font stack (SF Pro on Apple devices, a self-hosted Inter
elsewhere), system colours, hairline separators, a frosted navigation bar, squircle corners, iOS-style segmented
controls, switches and grouped lists, Fitness-style score rings and a bento grid. It is light by default, follows the OS
dark setting, and the toggle in the header overrides both. All colours are tokens at the top of
`frontend/src/styles.css`, so re-theming is a one-block change. It is responsive down to phone width.

## Configuration

See `.env.example`. Useful variables: `OPENROUTER_API_KEY`, `EVAL_OPENROUTER_BASE_URL`, `EVAL_OPENROUTER_CONCURRENCY`, `EVAL_DEFAULT_PROVIDER`, `EVAL_MODAL_APP`, `EVAL_VLLM_VERSION` (deploy
time), `EVAL_MODAL_SCALEDOWN`, `EVAL_MODAL_MAX_CONTAINERS`, `EVAL_PORT`, `EVAL_DATA_DIR`, `EVAL_MOCK_PACE`, `EVAL_HOST`, `EVAL_CORS_ORIGINS`.

**Security:** the server has no authentication and can spend money on your Modal/OpenRouter accounts, so it binds to
`127.0.0.1`, sends no CORS headers, only answers requests addressed to a loopback host name (blocks DNS-rebinding;
add names via `EVAL_ALLOWED_HOSTS`), rejects state-changing requests from a different Origin (allow a dev frontend with
`EVAL_CORS_ORIGINS`), never echoes request values in validation errors, and redacts API keys from error messages. Only set `EVAL_HOST=0.0.0.0` behind a trusted network or an
authenticating reverse proxy.
