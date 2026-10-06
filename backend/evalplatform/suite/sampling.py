"""Hyperparameter tuning suite: how a model (and the endpoint serving it) responds to decoding settings.

Everything here is measured, never assumed:

* **Temperature curve** — accuracy, clean-output rate and diversity at T = 0 … 2, with Wilson confidence intervals; derives the
  best temperature, the *coherence cliff* (highest temperature that still produces clean, accurate output) and the sensitivity.
* **Truncation** — top-p / top-k / min-p at a stress temperature: do they rescue quality, and what do they cost in diversity?
* **Repetition controls** — frequency / repetition penalties on a repetition-prone prompt, and the accuracy they cost.
* **Reproducibility** — greedy repeats, seeded repeats, concurrent greedy requests.
* **Endpoint compliance** — is each parameter *honoured*, silently ignored or rejected? (``top_k=1`` must make sampling deterministic,
  the same ``seed`` must repeat itself, a ``stop`` string must stop generation, ``max_tokens`` must cap the length.)
* **Tuning** — recommended *precise / balanced / creative* profiles, then a held-out A/B of the tuned settings against the
  generic API defaults (and the maker's published settings) with a confidence interval, so "tuning helped" is only claimed when it did.

The lab talks to the model through one ``ask`` callable, so it is provider-agnostic and unit-tested with fake models.
"""
from __future__ import annotations

import asyncio
import inspect
import math
import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..knowledge import API_DEFAULT_SAMPLING, sampling_guidance
from .coherence import analyze_text, ngram_dup_ratio
from .tasks import GradeCtx, Task, build_suite
from .text import split_thinking

# ======================================================================================
# probes
# ======================================================================================


@dataclass
class Probe:
    id: str
    prompt: str
    reference: str                 # demo-mode / replay oracle
    max_tokens: int = 220
    kind: str = "prose"


STORY_REF = ("Before dawn, Elias climbs the spiral stairs of the lighthouse with a mug of strong tea warming his hands. He trims the wick, polishes the great lens until it gleams, "
             "and records the night's weather in a salt-stained logbook. Gulls begin to cry as the sky pales over the grey water, and a fishing boat slips out past the rocks. "
             "Elias opens the shutters, lets the cold wind rush in, and watches the beam fade into morning. Then he walks the cliff path to check the foghorn, feeds the cat that "
             "sleeps by the generator, and sits on the doorstep to eat his bread while the waves keep their patient count.")
EXPLAIN_REF = ("A rainbow appears when sunlight meets raindrops while the sun is behind you. Light enters each drop and slows down, which bends it. Inside the drop it reflects off the "
               "back surface, then exits and bends again. Because each colour bends by a slightly different amount, white light is spread into a spectrum, with red on the outer edge "
               "and violet on the inside. Your eyes receive each colour from drops at a slightly different angle, about forty-two degrees from the point opposite the sun, so the "
               "colours line up as a curved band across the sky. The lower the sun, the taller the arch.")
REP_REF = ("This reusable water bottle keeps drinks cold for twenty-four hours. This double-wall steel body stays dry on the outside. This wide mouth fits ice cubes and most cup holders. "
           "This leak-proof lid locks with a single twist. This durable finish resists scratches in a crowded bag. This carry loop clips easily onto a backpack. "
           "This bottle leaves no plastic taste or chemical smell. This design is easy to clean by hand or in a dishwasher. This simple purchase replaces hundreds of disposable bottles each year. "
           "This bottle is built to be refilled for years to come.")
DET_REF = ("1. Exercise strengthens your heart and improves circulation.\n2. It helps control body weight by burning calories.\n3. Regular activity lifts mood by releasing endorphins.\n"
           "4. It builds stronger bones and muscles as you age.\n5. Exercise improves sleep quality and daytime energy.")
LEN_REF = ("The Roman Empire grew from the city of Rome, which became a republic after overthrowing its kings around 509 BC. Over centuries Roman legions conquered Italy, then the "
           "Mediterranean world, bringing Carthage, Greece and Egypt under Roman control. Civil wars ended the republic, and in 27 BC Augustus became the first emperor, beginning the "
           "Pax Romana, two centuries of relative peace and prosperity. Roads, aqueducts, law and Latin spread across Europe, North Africa and the Near East. From the third century "
           "the empire faced plagues, invasions and political chaos. Diocletian divided its administration, and Constantine moved the capital to Constantinople and legalised Christianity. "
           "The western empire collapsed in 476 AD when Odoacer deposed the last emperor, while the eastern, Byzantine, empire lasted until the Ottomans took Constantinople in 1453.")

PROBES: dict[str, Probe] = {p.id: p for p in [
    Probe("samp-coh-story", "Write a short description (about 120 words) of a lighthouse keeper's morning routine.", STORY_REF, 260),
    Probe("samp-coh-explain", "Explain in about 100 words how rainbows form.", EXPLAIN_REF, 230),
    Probe("samp-rep", "Write a 10-sentence product description for a reusable water bottle. Start every sentence with the word \"This\".", REP_REF, 260),
    Probe("samp-det", "List five reasons why exercise is good for your health, one short sentence each, as a numbered list.", DET_REF, 150),
    Probe("samp-stop", "Repeat exactly this line and nothing else: alpha beta gamma delta", "alpha beta gamma delta", 40, "short"),
    Probe("samp-len", "Write a detailed history of the Roman Empire.", LEN_REF, 400),
]}

ACC_PROBES = ["math-change", "math-percent", "math-committee", "math-squares"]            # temperature / truncation sweeps
VAL_PROBES = ["math-distance", "math-gauss", "math-rectangle", "math-remainder", "math-dice", "gen-order"]   # held out: never used while tuning
COH_PROBES = ["samp-coh-story", "samp-coh-explain"]

# ======================================================================================
# statistics
# ======================================================================================


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion (well behaved at 0, 1 and tiny n)."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def diff_ci(k1: int, n1: int, k2: int, n2: int, z: float = 1.96) -> tuple[float, float, float]:
    """Difference of two proportions (arm 1 − arm 2) with a normal-approximation interval."""
    if n1 <= 0 or n2 <= 0:
        return 0.0, -1.0, 1.0
    p1, p2 = k1 / n1, k2 / n2
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    d = p1 - p2
    # never report a zero-width interval from a handful of samples
    se = max(se, 0.5 / math.sqrt(max(1, min(n1, n2))) * 0.35)
    return d, d - z * se, d + z * se


_T975 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36, 8: 2.31, 9: 2.26, 10: 2.23}


def paired_ci(diffs: list[float], floor: float = 0.08) -> tuple[float, float, float]:
    """Mean of per-problem differences with a t interval. The unit is the *problem*, not the sample: repeated samples of one problem are
    correlated (greedy repeats are identical), so counting them as independent would overstate the evidence."""
    n = len(diffs)
    if n == 0:
        return 0.0, -1.0, 1.0
    mean = sum(diffs) / n
    sd = statistics.stdev(diffs) if n > 1 else 0.5
    se = max(sd / math.sqrt(n), floor)             # n problems can never resolve differences much below ~10 points
    t = _T975.get(n - 1, 1.96 if n > 11 else 2.23)
    return mean, mean - t * se, mean + t * se


_WS = re.compile(r"\s+")


def _norm(t: str) -> str:
    return _WS.sub(" ", split_thinking(t)[0].strip().lower())


def _grams(t: str) -> set[tuple[str, ...]]:
    w = re.findall(r"\w+", _norm(t))
    n = 2 if len(w) >= 6 else 1
    return {tuple(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def diversity(texts: list[str]) -> float:
    """Mean pairwise (1 − Jaccard similarity of word bigrams). 0 = all samples identical, 1 = nothing in common."""
    gs = [_grams(t) for t in texts if t.strip()]
    if len(gs) < 2:
        return 0.0
    dists = []
    for i in range(len(gs)):
        for j in range(i + 1, len(gs)):
            u = gs[i] | gs[j]
            dists.append(1 - (len(gs[i] & gs[j]) / len(u) if u else 1.0))
    return sum(dists) / len(dists)


def identical_fraction(texts: list[str]) -> float:
    """Share of sample pairs that are word-for-word identical (whitespace/case-insensitive)."""
    ts = [_norm(t) for t in texts]
    if len(ts) < 2:
        return 1.0
    pairs = [(a, b) for i, a in enumerate(ts) for b in ts[i + 1:]]
    return sum(a == b for a, b in pairs) / len(pairs)


def repetition_index(text: str) -> float:
    return ngram_dup_ratio(re.findall(r"\w+", split_thinking(text)[0].lower()), 4)


def lexical_variety(text: str) -> float:
    w = re.findall(r"\w+", split_thinking(text)[0].lower())
    return len(set(w)) / len(w) if w else 0.0


# ======================================================================================
# plan
# ======================================================================================


@dataclass
class Plan:
    temps: list[float]
    acc_probes: list[str]
    acc_n: int
    coh_probes: list[str]
    coh_n: int
    nucleus_t: float
    nucleus: list[dict[str, Any]]            # {"label","param","value"}
    nucleus_acc: list[str]
    nucleus_acc_n: int
    nucleus_coh_n: int
    penalties: list[dict[str, Any]]
    penalty_acc: list[str]
    val_probes: list[str]
    val_n: int
    det_greedy: int
    det_seed_same: int
    det_seed_diff: int
    det_conc: int
    max_tokens_cap: int = 450

    def requests(self, arms: int = 3) -> int:
        t = len(self.acc_probes) * (1 + (len(self.temps) - 1) * self.acc_n) + len(self.coh_probes) * (1 + (len(self.temps) - 1) * self.coh_n)
        n = len(self.nucleus) * (len(self.nucleus_acc) * self.nucleus_acc_n + len(self.coh_probes) * self.nucleus_coh_n)
        p = 1 + len(self.penalties) * (1 + len(self.penalty_acc))
        d = self.det_greedy + self.det_seed_same + self.det_seed_diff + self.det_conc
        return t + n + p + d + 2 + len(self.val_probes) * (1 + self.val_n * (arms - 1))      # greedy arm: 1 sample per problem


def plan_for(reasoning: bool = False, quick: bool = False) -> Plan:
    if quick or reasoning:
        # reasoning models think for thousands of tokens per request, so they always get the compact grid
        return Plan(
            temps=[0.0, 0.7, 1.4] if not reasoning else [0.0, 0.6, 1.0, 1.5], acc_probes=ACC_PROBES[:3], acc_n=2, coh_probes=COH_PROBES[:1], coh_n=3,
            nucleus_t=1.4 if not reasoning else 1.5,
            nucleus=[dict(label="top_p 0.1", param="top_p", value=0.1), dict(label="top_k 1", param="top_k", value=1)],
            nucleus_acc=ACC_PROBES[1:3], nucleus_acc_n=1, nucleus_coh_n=2,
            penalties=[dict(label="frequency_penalty 1.0", param="frequency_penalty", value=1.0)], penalty_acc=ACC_PROBES[:2],
            val_probes=VAL_PROBES[:3], val_n=2, det_greedy=3, det_seed_same=2, det_seed_diff=1, det_conc=2,
            max_tokens_cap=450 if not reasoning else 2048,
        )
    return Plan(
        temps=[0.0, 0.4, 0.8, 1.2, 1.6, 2.0], acc_probes=ACC_PROBES, acc_n=3, coh_probes=COH_PROBES, coh_n=3, nucleus_t=1.2,
        nucleus=[dict(label="top_p 0.95", param="top_p", value=0.95), dict(label="top_p 0.8", param="top_p", value=0.8), dict(label="top_p 0.5", param="top_p", value=0.5),
                 dict(label="top_p 0.1", param="top_p", value=0.1), dict(label="top_k 50", param="top_k", value=50), dict(label="top_k 10", param="top_k", value=10),
                 dict(label="top_k 1", param="top_k", value=1), dict(label="min_p 0.05", param="min_p", value=0.05), dict(label="min_p 0.95", param="min_p", value=0.95)],
        nucleus_acc=ACC_PROBES[1:3], nucleus_acc_n=2, nucleus_coh_n=2,
        penalties=[dict(label="frequency_penalty 0.5", param="frequency_penalty", value=0.5), dict(label="frequency_penalty 1.0", param="frequency_penalty", value=1.0),
                   dict(label="frequency_penalty 1.5", param="frequency_penalty", value=1.5), dict(label="repetition_penalty 1.1", param="repetition_penalty", value=1.1),
                   dict(label="repetition_penalty 1.3", param="repetition_penalty", value=1.3)],
        penalty_acc=ACC_PROBES[:3], val_probes=VAL_PROBES, val_n=3, det_greedy=4, det_seed_same=3, det_seed_diff=2, det_conc=4,
    )


# ======================================================================================
# the lab
# ======================================================================================
AskFn = Callable[..., Awaitable[Any]]      # ask(messages, max_tokens=, temperature=, extra=) -> ChatResult-like (text, finish_reason, completion_tokens, error)
_REJECT = re.compile(r"unsupported|unrecognized|unknown (?:parameter|field|argument)|extra (?:inputs|fields)|not (?:permitted|allowed|supported)|invalid (?:parameter|value)|HTTP 4(?:00|22)", re.I)


def _clean(health: Any) -> bool:
    return not health.severe


class SamplingLab:
    def __init__(self, ask: AskFn, *, model: dict[str, Any], quick: bool = False, concurrency: int = 4,
                 progress: Callable[[int, int], None] | None = None, log: Callable[[str, str], None] | None = None) -> None:
        self.ask = ask
        self.model = model
        self.reasoning = bool(model.get("reasoning"))
        self.quick = quick
        self.plan = plan_for(self.reasoning, quick)
        self.sem = asyncio.Semaphore(max(1, concurrency))
        self._progress = progress or (lambda d, t: None)
        self._log = log or (lambda lvl, msg: None)
        self.done = 0
        self.total = 0
        self.errors: list[str] = []
        suite = {t.id: t for t in build_suite()}
        self.tasks: dict[str, Task] = {i: suite[i] for i in set(ACC_PROBES + VAL_PROBES) if i in suite}
        self.examples: list[dict[str, Any]] = []
        self.guidance = sampling_guidance(model)

    # ---------------------------------------------------------------- plumbing
    async def _ask(self, messages: list[dict[str, str]], *, t: float, max_tokens: int, extra: dict[str, Any] | None = None) -> Any:
        async with self.sem:
            try:
                r = await self.ask(messages, max_tokens=max_tokens, temperature=t, extra=extra or None)
            finally:
                self.done += 1
                self._progress(self.done, self.total)
        if getattr(r, "error", None):
            self.errors.append(str(r.error))
        return r

    def _acc_tokens(self, task: Task) -> int:
        return min(task.max_tokens, self.plan.max_tokens_cap)

    async def _acc(self, tid: str, t: float, n: int, extra: dict[str, Any] | None = None, seed0: int = 1000) -> dict[str, Any]:
        """n graded samples of one accuracy probe."""
        task = self.tasks[tid]

        async def one(i: int) -> dict[str, Any]:
            ex = dict(extra or {})
            if t > 0:
                ex.setdefault("seed", seed0 + i)
            r = await self._ask(task.messages, t=t, max_tokens=self._acc_tokens(task), extra=ex)
            if r.error:
                return {"error": r.error}
            g = task.grader(r.text, GradeCtx(finish_reason=r.finish_reason, completion_tokens=r.completion_tokens, all_texts=[r.text, r.text]))
            if inspect.isawaitable(g):
                g = await g
            h = analyze_text(r.text, kind="prose", temperature=t, finish_reason=r.finish_reason)
            return {"ok": bool(g.passed), "clean": _clean(h), "text": r.text, "tokens": r.completion_tokens}

        return {"probe": tid, "samples": await asyncio.gather(*[one(i) for i in range(n)])}

    async def _coh(self, pid: str, t: float, n: int, extra: dict[str, Any] | None = None, seed0: int = 2000) -> dict[str, Any]:
        probe = PROBES[pid]

        async def one(i: int) -> dict[str, Any]:
            ex = dict(extra or {})
            if t > 0:
                ex.setdefault("seed", seed0 + i)
            r = await self._ask([{"role": "user", "content": probe.prompt}], t=t, max_tokens=min(probe.max_tokens, self.plan.max_tokens_cap), extra=ex)
            if r.error:
                return {"error": r.error}
            h = analyze_text(r.text, kind="prose", temperature=t, finish_reason=r.finish_reason)
            return {"text": r.text, "clean": _clean(h), "garbled": h.garbled, "repetitive": h.repetitive, "tokens": r.completion_tokens,
                    "length": r.finish_reason == "length", "rep": repetition_index(r.text)}

        return {"probe": pid, "samples": await asyncio.gather(*[one(i) for i in range(n)])}

    @staticmethod
    def _summarise(acc: list[dict[str, Any]], coh: list[dict[str, Any]]) -> dict[str, Any]:
        a = [s for g in acc for s in g["samples"] if "error" not in s]
        c = [s for g in coh for s in g["samples"] if "error" not in s]
        errs = [s["error"] for g in acc + coh for s in g["samples"] if "error" in s]
        k, n = sum(s["ok"] for s in a), len(a)
        lo, hi = wilson(k, n)
        allclean = [s["clean"] for s in a + c]
        divs = [diversity([s["text"] for s in g["samples"] if "error" not in s]) for g in coh]
        return {
            "n_acc": n, "acc": round(k / n, 4) if n else None, "acc_ci": [round(lo, 3), round(hi, 3)] if n else None,
            "n_coh": len(c), "clean": round(sum(allclean) / len(allclean), 4) if allclean else None,
            "diversity": round(sum(divs) / len(divs), 4) if divs else None,
            "garble": round(sum(s["garbled"] for s in c) / len(c), 4) if c else None,
            "repeat": round(sum(s["repetitive"] for s in c) / len(c), 4) if c else None,
            "tokens": round(statistics.mean(s["tokens"] for s in c), 1) if c else None,
            "length_stop": round(sum(s["length"] for s in c) / len(c), 4) if c else None,
            "errors": len(errs), "error": errs[0] if errs else None,
        }

    def _keep_example(self, label: str, params: dict[str, Any], groups: list[dict[str, Any]]) -> None:
        for g in groups:
            for s in g["samples"]:
                if "error" not in s and s.get("text") and g["probe"].startswith("samp-coh"):
                    self.examples.append({"label": label, "params": params, "probe": g["probe"], "text": s["text"][:600], "clean": s.get("clean")})
                    return

    # ---------------------------------------------------------------- stage 1: temperature curve
    async def _temperature(self) -> list[dict[str, Any]]:
        p = self.plan
        results = await asyncio.gather(*[self._temp_point(t) for t in p.temps])
        return list(results)

    async def _temp_point(self, t: float) -> dict[str, Any]:
        p = self.plan
        acc, coh = await asyncio.gather(
            asyncio.gather(*[self._acc(i, t, 1 if t == 0 else p.acc_n) for i in p.acc_probes]),
            asyncio.gather(*[self._coh(i, t, 1 if t == 0 else p.coh_n) for i in p.coh_probes]))
        pt = {"t": t, **self._summarise(list(acc), list(coh))}
        pt["_acc_groups"], pt["_coh_groups"] = list(acc), list(coh)
        return pt

    # ---------------------------------------------------------------- stage 2: truncation
    async def _nucleus(self) -> list[dict[str, Any]]:
        p = self.plan

        async def setting(s: dict[str, Any]) -> dict[str, Any]:
            extra = {s["param"]: s["value"]}
            acc, coh = await asyncio.gather(
                asyncio.gather(*[self._acc(i, p.nucleus_t, p.nucleus_acc_n, extra, seed0=3000) for i in p.nucleus_acc]),
                asyncio.gather(*[self._coh(i, p.nucleus_t, p.nucleus_coh_n, extra, seed0=4000) for i in p.coh_probes]))
            coh = list(coh)
            ident = [identical_fraction([x["text"] for x in g["samples"] if "error" not in x]) for g in coh]
            row = {**s, **self._summarise(list(acc), coh), "identical": round(sum(ident) / len(ident), 3) if ident else None}
            row["_coh_groups"] = coh
            return row

        return list(await asyncio.gather(*[setting(s) for s in p.nucleus]))

    # ---------------------------------------------------------------- stage 3: penalties
    async def _penalties(self, temp_points: list[dict[str, Any]]) -> dict[str, Any]:
        p = self.plan
        rep = PROBES["samp-rep"]

        async def rep_run(extra: dict[str, Any] | None) -> dict[str, Any]:
            r = await self._ask([{"role": "user", "content": rep.prompt}], t=0.0, max_tokens=min(rep.max_tokens, p.max_tokens_cap), extra=extra)
            if r.error:
                return {"error": r.error}
            return {"text": r.text, "rep": repetition_index(r.text), "variety": lexical_variety(r.text), "tokens": r.completion_tokens}

        async def setting(s: dict[str, Any] | None) -> dict[str, Any]:
            extra = {s["param"]: s["value"]} if s else None
            base_acc = await asyncio.gather(*[self._acc(i, 0.0, 1, extra) for i in p.penalty_acc])
            r = await rep_run(extra)
            return {"setting": s, "rep": r, "acc": list(base_acc)}

        base_rep, *rows = await asyncio.gather(rep_run(None), *[setting(s) for s in p.penalties])
        # accuracy baseline reuses the temperature curve at T=0 for the same probes (no extra requests)
        t0 = next((pt for pt in temp_points if pt["t"] == 0.0), None)
        base_acc_ok = {g["probe"]: g["samples"][0].get("ok") for g in (t0 or {}).get("_acc_groups", []) if g["samples"] and "error" not in g["samples"][0]}
        base_k = [v for k, v in base_acc_ok.items() if k in p.penalty_acc]
        base_acc = (sum(bool(v) for v in base_k) / len(base_k)) if base_k else None
        out_rows = []
        for r in rows:
            s = r["setting"]
            samples = [x for g in r["acc"] for x in g["samples"] if "error" not in x]
            acc = sum(x["ok"] for x in samples) / len(samples) if samples else None
            err = r["rep"].get("error") or next((x["error"] for g in r["acc"] for x in g["samples"] if "error" in x), None)
            changed = (not err) and (not base_rep.get("error")) and _norm(r["rep"]["text"]) != _norm(base_rep["text"])
            out_rows.append({**s, "rep_index": round(r["rep"].get("rep", 0.0), 3) if not err else None, "variety": round(r["rep"].get("variety", 0.0), 3) if not err else None,
                             "acc": round(acc, 3) if acc is not None else None, "changed": changed, "error": err})
        return {"baseline": {"rep_index": round(base_rep.get("rep", 0.0), 3) if not base_rep.get("error") else None,
                             "variety": round(base_rep.get("variety", 0.0), 3) if not base_rep.get("error") else None, "acc": round(base_acc, 3) if base_acc is not None else None,
                             "text": (base_rep.get("text") or "")[:500]},
                "settings": out_rows}

    # ---------------------------------------------------------------- stage 4: reproducibility
    async def _determinism(self) -> dict[str, Any]:
        p = self.plan
        det = PROBES["samp-det"]
        msgs = [{"role": "user", "content": det.prompt}]
        mt = min(det.max_tokens, p.max_tokens_cap)

        async def run(t: float, extra: dict[str, Any] | None = None) -> str | None:
            r = await self._ask(msgs, t=t, max_tokens=mt, extra=extra)
            return None if r.error else r.text

        greedy = [x for x in await asyncio.gather(*[run(0.0) for _ in range(p.det_greedy)]) if x is not None]
        seeded = [x for x in await asyncio.gather(*[run(0.8, {"seed": 4242}) for _ in range(p.det_seed_same)]) if x is not None]
        others = [x for x in await asyncio.gather(*[run(0.8, {"seed": 7000 + i}) for i in range(p.det_seed_diff)]) if x is not None]
        conc = [x for x in await asyncio.gather(*[run(0.0) for _ in range(p.det_conc)]) if x is not None]
        return {
            "greedy": {"n": len(greedy), "identical": round(identical_fraction(greedy), 3) if len(greedy) > 1 else None},
            "seed_same": {"n": len(seeded), "identical": round(identical_fraction(seeded), 3) if len(seeded) > 1 else None},
            "seed_different": {"n": len(others), "differs_from_seeded": bool(seeded) and any(_norm(o) != _norm(seeded[0]) for o in others)},
            "concurrent": {"n": len(conc), "identical": round(identical_fraction(conc), 3) if len(conc) > 1 else None},
        }

    # ---------------------------------------------------------------- stage 5: stop / max_tokens
    async def _length_controls(self) -> dict[str, Any]:
        stop, ln = PROBES["samp-stop"], PROBES["samp-len"]
        r1, r2 = await asyncio.gather(
            self._ask([{"role": "user", "content": stop.prompt}], t=0.0, max_tokens=40, extra={"stop": ["gamma"]}),
            self._ask([{"role": "user", "content": ln.prompt}], t=0.0, max_tokens=20))
        out: dict[str, Any] = {}
        if r1.error:
            out["stop"] = {"error": r1.error}
        else:
            out["stop"] = {"stopped": "gamma" not in r1.text.lower(), "started": "alpha" in r1.text.lower(), "finish_reason": r1.finish_reason, "text": r1.text[:80]}
        if r2.error:
            out["max_tokens"] = {"error": r2.error}
        else:
            out["max_tokens"] = {"tokens": r2.completion_tokens, "finish_reason": r2.finish_reason, "capped": r2.completion_tokens <= 22 and r2.finish_reason == "length"}
        return out

    # ---------------------------------------------------------------- run
    async def run(self) -> dict[str, Any]:
        arms_guess = 3 if self.guidance else 2
        self.total = self.plan.requests(arms_guess)
        self._progress(0, self.total)
        temps = await self._temperature()
        self._log("info", f"Temperature sweep done ({len(temps)} settings).")
        nucleus, pens, det, ctrl = await asyncio.gather(self._nucleus(), self._penalties(temps), self._determinism(), self._length_controls())
        for pt in temps:
            if pt["t"] in (0.0, max(self.plan.temps)):
                self._keep_example(f"T = {pt['t']:g}", {"temperature": pt["t"]}, pt["_coh_groups"])
        temperature = derive_temperature(temps)
        controls = derive_controls(temps, nucleus, pens, det, ctrl, self.plan)
        nuc = derive_nucleus(temps, nucleus, self.plan)
        nuc["stress_temperature"] = self.plan.nucleus_t
        pen = derive_penalties(pens)
        profiles = build_profiles(temperature, nuc, pen, controls, self.guidance, self.model)
        validation = await self._validate(profiles)
        score = score_sampling({**temperature, "points": temps}, controls, det, pen)
        findings = build_findings(temperature, nuc, pen, det, controls, validation, self.model, quick=self.quick)
        self._progress(self.total, self.total)
        for pt in temps:
            pt.pop("_acc_groups", None)
            pt.pop("_coh_groups", None)
        for row in nucleus:
            row.pop("_coh_groups", None)
        return {
            "status": "ok", "quick": self.quick, "requests": self.done, "errors": len(self.errors),
            "plan": {"temps": self.plan.temps, "acc_probes": self.plan.acc_probes, "acc_samples": self.plan.acc_n, "coh_samples": self.plan.coh_n,
                     "nucleus_temperature": self.plan.nucleus_t, "validation_probes": len(self.plan.val_probes), "validation_samples": self.plan.val_n},
            "temperature": {**temperature, "points": temps},
            "truncation": {**nuc, "stress_temperature": self.plan.nucleus_t, "settings": nucleus},
            "penalties": pen, "determinism": det, "controls": controls, "profiles": profiles, "validation": validation,
            "guidance": self.guidance, "examples": self.examples[:6], "score": score, "findings": findings,
        }

    # ---------------------------------------------------------------- stage 6: held-out validation of the tuned profile
    async def _validate(self, profiles: dict[str, Any]) -> dict[str, Any]:
        p = self.plan
        arms: list[dict[str, Any]] = [{"id": "api_default", "label": "Generic API defaults", "params": dict(API_DEFAULT_SAMPLING)}]
        if self.guidance:
            arms.append({"id": "vendor", "label": "Maker's published settings", "params": dict(self.guidance["params"]), "source": self.guidance["source"]})
        tuned = profiles.get("precise")
        if tuned:
            arms.append({"id": "tuned", "label": "Tuned (precise profile)", "params": dict(tuned["params"])})

        async def arm_run(arm: dict[str, Any]) -> dict[str, Any]:
            t = float(arm["params"].get("temperature", 0.0))
            extra = {k: v for k, v in arm["params"].items() if k != "temperature"}
            n_samples = 1 if t == 0 else p.val_n            # greedy repeats are identical: one sample per problem is the whole evidence
            groups = await asyncio.gather(*[self._acc(i, t, n_samples, extra, seed0=9000) for i in p.val_probes])
            per_probe: dict[str, float] = {}
            samples = []
            for g in groups:
                ok = [x for x in g["samples"] if "error" not in x]
                if ok:
                    per_probe[g["probe"]] = sum(x["ok"] for x in ok) / len(ok)
                    samples += ok
            k, n = sum(x["ok"] for x in samples), len(samples)
            acc = sum(per_probe.values()) / len(per_probe) if per_probe else None
            return {**arm, "n": n, "problems": len(per_probe), "correct": k, "acc": round(acc, 4) if acc is not None else None,
                    "clean": round(sum(x["clean"] for x in samples) / n, 3) if n else None, "per_problem": {k2: round(v, 3) for k2, v in per_probe.items()}}

        rows = list(await asyncio.gather(*[arm_run(a) for a in arms]))
        by = {r["id"]: r for r in rows}
        out: dict[str, Any] = {"n_probes": len(p.val_probes), "samples_per_probe": p.val_n, "arms": rows, "comparisons": []}
        tuned_row = by.get("tuned")
        for base_id in ("api_default", "vendor"):
            b = by.get(base_id)
            if not (tuned_row and b and tuned_row["per_problem"] and b["per_problem"]):
                continue
            common = sorted(set(tuned_row["per_problem"]) & set(b["per_problem"]))
            d, lo, hi = paired_ci([tuned_row["per_problem"][k] - b["per_problem"][k] for k in common])
            verdict = "improved" if lo > 0 else "worse" if hi < 0 else "within_noise"
            out["comparisons"].append({"against": base_id, "delta": round(d, 4), "ci": [round(lo, 3), round(hi, 3)], "verdict": verdict, "problems": len(common)})
        return out


# ======================================================================================
# derivations (pure functions: unit-tested without any model)
# ======================================================================================
def _pt(points: list[dict[str, Any]], t: float) -> dict[str, Any] | None:
    return min((p for p in points if p.get("acc") is not None or p.get("clean") is not None), key=lambda p: abs(p["t"] - t), default=None)


def derive_temperature(points: list[dict[str, Any]]) -> dict[str, Any]:
    pts = sorted([p for p in points if p.get("acc") is not None or p.get("clean") is not None], key=lambda p: p["t"])
    if not pts:
        return {"best_t": None, "cliff_t": None, "breaks_at": None, "sensitivity": None, "sensitivity_label": None, "honored": "unclear"}
    accs = [p["acc"] for p in pts if p.get("acc") is not None]
    acc_max = max(accs) if accs else 1.0
    clean_max = max((p["clean"] for p in pts if p.get("clean") is not None), default=1.0)
    tol = 0.08
    cands = [p for p in pts if (p.get("acc") is None or p["acc"] >= acc_max - tol) and (p.get("clean") is None or p["clean"] >= clean_max - 0.05)]
    greedy = next((p for p in pts if p["t"] == 0.0), None)
    greedy_degenerate = bool(greedy) and ((greedy.get("clean") is not None and greedy["clean"] < 0.9) or (greedy.get("repeat") or 0) >= 0.3)
    pool = [p for p in cands if not (greedy_degenerate and p["t"] == 0.0)] or cands or pts
    best = min(pool, key=lambda p: p["t"])
    acc_ref = best["acc"] if best.get("acc") is not None else acc_max
    cliff, breaks = pts[0]["t"], None
    ok_so_far = True
    for p in pts:
        # Garbling is abrupt, so the clean-output rate can be read directly; accuracy falls gradually and is noisy at a dozen
        # samples, so a point only counts as degraded when even the *upper* end of its confidence interval is below 85% of the best.
        acc_hi = (p.get("acc_ci") or [0, p["acc"]])[1] if p.get("acc") is not None else None
        good = (p.get("clean") is None or p["clean"] >= 0.95) and (acc_hi is None or acc_ref <= 0 or acc_hi >= 0.85 * acc_ref)
        if p["t"] == 0.0 and greedy_degenerate:
            good = True               # a degenerate greedy point must not define the ceiling of the usable range
        if ok_so_far and good:
            cliff = p["t"]
        elif ok_so_far:
            ok_so_far, breaks = False, p["t"]
    ref12 = _pt(pts, 1.2)
    drop = None
    if ref12 and ref12.get("acc") is not None and acc_ref:
        drop = max(0.0, min(1.0, (acc_ref - ref12["acc"]) / max(acc_ref, 1e-9)))
    label = None if drop is None else ("low" if drop < 0.10 else "moderate" if drop < 0.35 else "high")
    divs = [(p["t"], p.get("diversity") or 0.0) for p in pts]
    d0 = next((d for t, d in divs if t == 0.0), 0.0)
    hot = [d for t, d in divs if t >= 1.2]
    last = pts[-1]
    if (hot and max(hot) > d0 + 0.02) or (last["t"] >= 1.5 and (last.get("clean") is not None and last["clean"] < 0.9)):
        honored = "honored"
    elif hot and all(abs(d - d0) < 0.02 for d in hot) and (last.get("clean") is None or last["clean"] >= 0.9):
        honored = "ignored"
    else:
        honored = "unclear"
    creative_pool = [p for p in pts if p["t"] <= min(cliff, 1.2) and p["t"] > 0]
    creative = max((p["t"] for p in creative_pool), default=None)
    return {"best_t": best["t"], "cliff_t": cliff, "breaks_at": breaks, "greedy_degenerate": greedy_degenerate, "creative_t": creative,
            "sensitivity": round(drop, 3) if drop is not None else None, "sensitivity_label": label,
            "sensitivity_t": ref12["t"] if drop is not None and ref12 else None,        # the measured temperature nearest 1.2 that the drop refers to
            "honored": honored, "acc_max": round(acc_max, 3)}


def derive_nucleus(temps: list[dict[str, Any]], rows: list[dict[str, Any]], plan: Plan) -> dict[str, Any]:
    base = next((p for p in temps if abs(p["t"] - plan.nucleus_t) < 1e-9), None)
    best = None
    acc_ref = max((p["acc"] for p in temps if p.get("acc") is not None), default=None)
    # truncation can only help if the stress temperature actually damaged quality; otherwise differences are sampling noise
    degraded = bool(base) and ((base.get("clean") is not None and base["clean"] < 0.95) or
                               (base.get("acc") is not None and acc_ref and base["acc"] < 0.85 * acc_ref))
    if base and degraded:
        for r in rows:
            if r.get("error") or r.get("acc") is None or r["param"] == "min_p" and r["value"] >= 0.5:
                continue
            if r["value"] in (1,) and r["param"] == "top_k":
                continue                       # top_k=1 is greedy: informative, but never a "recommendation"
            gain = ((r.get("acc") or 0) - (base.get("acc") or 0)) + ((r.get("clean") or 0) - (base.get("clean") or 0))
            keeps_variety = (r.get("diversity") or 0) >= 0.4 * (base.get("diversity") or 0.0)
            if r["param"] == "top_p" and r["value"] <= 0.2:
                continue
            if gain >= 0.25 and keeps_variety and (best is None or gain > best["gain"]):
                best = {"label": r["label"], "param": r["param"], "value": r["value"], "gain": round(gain, 3)}
    helps = best is not None
    return {"baseline": {k: base.get(k) for k in ("acc", "clean", "diversity")} if base else None, "best": best, "truncation_helps": helps, "stress_degraded": degraded}


def derive_penalties(pens: dict[str, Any]) -> dict[str, Any]:
    base = pens["baseline"]
    rows = pens["settings"]
    harm_at = None
    for r in rows:
        if r.get("acc") is not None and base.get("acc") is not None and r["acc"] < base["acc"] - 0.2:
            harm_at = harm_at or r["label"]
    base_rep = base.get("rep_index") or 0.0
    rep_prone = base_rep >= 0.15
    effective = [r for r in rows if r.get("rep_index") is not None and base_rep - r["rep_index"] >= 0.05]
    safe = [r for r in rows if r.get("error") is None and r.get("acc") is not None and (base.get("acc") is None or r["acc"] >= base["acc"] - 0.2)]
    recommend = None
    if rep_prone and effective:
        ok = [r for r in effective if r in safe]
        if ok:
            r = min(ok, key=lambda x: x["value"] if x["param"] == "frequency_penalty" else (x["value"] - 1) * 5)
            recommend = {"param": r["param"], "value": r["value"], "label": r["label"]}
    return {"baseline": base, "settings": rows, "repetition_prone": rep_prone, "harm_at": harm_at, "recommended": recommend,
            "any_effect": any(r.get("changed") for r in rows)}


def _status(honored: bool | None, rejected: str | None = None, ignored: bool = False) -> str:
    if rejected:
        return "rejected"
    if honored is None:
        return "unclear"
    return "honored" if honored else ("ignored" if ignored else "unclear")


def derive_controls(temps: list[dict[str, Any]], nucleus: list[dict[str, Any]], pens: dict[str, Any], det: dict[str, Any], ctrl: dict[str, Any], plan: Plan) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    tcurve = derive_temperature(temps)
    out.append({"id": "temperature", "label": "temperature", "status": tcurve["honored"],
                "detail": {"honored": "Output diversity rises (and quality eventually breaks) as temperature increases.",
                           "ignored": "Output is identical at every temperature, including 2.0, so the endpoint appears to ignore it.",
                           "unclear": "The response to temperature was inconclusive."}[tcurve["honored"]]})

    def trunc(param: str, value: float, label: str, what: str) -> None:
        r = next((x for x in nucleus if x["param"] == param and x["value"] == value), None)
        if r is None:
            return
        if r.get("error") and r.get("n_acc", 0) == 0 and r.get("n_coh", 0) == 0:
            out.append({"id": param, "label": label, "status": "rejected", "detail": f"The endpoint rejected the parameter: {str(r['error'])[:140]}"})
            return
        ident = r.get("identical")
        if ident is None:
            out.append({"id": param, "label": label, "status": "unclear", "detail": "Not enough samples."})
        elif ident >= 0.75:
            out.append({"id": param, "label": label, "status": "honored", "detail": f"{what} made sampling deterministic ({ident:.0%} of sample pairs identical at T={plan.nucleus_t:g})."})
        else:
            out.append({"id": param, "label": label, "status": "ignored", "detail": f"{what} left outputs varied ({ident:.0%} identical at T={plan.nucleus_t:g}); the parameter appears to be ignored."})

    trunc("top_p", 0.1, "top_p", "top_p=0.1")
    trunc("top_k", 1, "top_k", "top_k=1")
    if any(x["param"] == "min_p" for x in nucleus):
        trunc("min_p", 0.95, "min_p", "min_p=0.95")
    seed_same = det["seed_same"]["identical"]
    if seed_same is None:
        out.append({"id": "seed", "label": "seed", "status": "unclear", "detail": "Not enough samples."})
    elif seed_same >= 0.99 and det["seed_different"]["differs_from_seeded"]:
        out.append({"id": "seed", "label": "seed", "status": "honored", "detail": "The same seed repeats the same sample; a different seed gives a different one."})
    elif seed_same >= 0.99:
        out.append({"id": "seed", "label": "seed", "status": "unclear", "detail": "Seeded repeats match, but different seeds did not differ (a very peaked model, or seed forced)."})
    else:
        out.append({"id": "seed", "label": "seed", "status": "ignored", "detail": f"The same seed gave different outputs ({seed_same:.0%} of pairs identical): reproducible sampling is not available."})

    def pen(param: str, label: str) -> None:
        rows = [r for r in pens["settings"] if r["param"] == param]
        if not rows:
            return
        if all(r.get("error") for r in rows):
            out.append({"id": param, "label": label, "status": "rejected", "detail": f"The endpoint rejected the parameter: {str(rows[0]['error'])[:140]}"})
        elif any(r.get("changed") for r in rows):
            out.append({"id": param, "label": label, "status": "honored", "detail": "Changing the value changes the output."})
        else:
            out.append({"id": param, "label": label, "status": "unclear" if not pens["baseline"].get("rep_index") else "ignored",
                        "detail": "No observable effect at any tested value (the endpoint may ignore it, or the output never repeats)."})

    pen("frequency_penalty", "frequency_penalty")
    pen("repetition_penalty", "repetition_penalty")
    st = ctrl.get("stop", {})
    if st.get("error"):
        out.append({"id": "stop", "label": "stop sequences", "status": "rejected", "detail": f"The endpoint rejected `stop`: {str(st['error'])[:140]}"})
    elif st.get("stopped") and st.get("started"):
        out.append({"id": "stop", "label": "stop sequences", "status": "honored", "detail": f"Generation halted before the stop string (output: {st.get('text')!r})."})
    else:
        out.append({"id": "stop", "label": "stop sequences", "status": "ignored" if not st.get("stopped") else "unclear", "detail": f"The stop string still appeared in the output ({st.get('text')!r})."})
    mt = ctrl.get("max_tokens", {})
    if mt.get("error"):
        out.append({"id": "max_tokens", "label": "max_tokens", "status": "rejected", "detail": f"The endpoint rejected `max_tokens`: {str(mt['error'])[:140]}"})
    elif mt.get("capped"):
        out.append({"id": "max_tokens", "label": "max_tokens", "status": "honored", "detail": f"20-token cap respected ({mt['tokens']} tokens, finish_reason=length)."})
    else:
        out.append({"id": "max_tokens", "label": "max_tokens", "status": "ignored", "detail": f"Asked for at most 20 tokens, received {mt.get('tokens')} (finish_reason={mt.get('finish_reason')})."})
    g = det["greedy"]["identical"]
    if g is not None:
        out.append({"id": "greedy", "label": "greedy determinism (T=0)", "status": "honored" if g >= 0.99 else "ignored",
                    "detail": "Repeated temperature-0 requests are identical." if g >= 0.99 else f"Temperature-0 requests differ ({g:.0%} of pairs identical): batching or kernels are non-deterministic."})
    return out


def build_profiles(temperature: dict[str, Any], nuc: dict[str, Any], pen: dict[str, Any], controls: list[dict[str, Any]], guidance: dict[str, Any] | None, model: dict[str, Any]) -> dict[str, Any]:
    if temperature.get("best_t") is None:
        return {}
    ok = {c["id"] for c in controls if c["status"] == "honored"}
    best_t, cliff = temperature["best_t"], temperature["cliff_t"]
    extras: dict[str, Any] = {}
    note = ""
    if nuc.get("best") and nuc["best"]["param"] in ok:
        extras[nuc["best"]["param"]] = nuc["best"]["value"]
        note = f" {nuc['best']['label']} recovered quality at the stress temperature."
    rec = pen.get("recommended")
    pen_extra = {rec["param"]: rec["value"]} if rec and rec["param"] in ok else {}
    precise = {"temperature": round(best_t, 2)}
    if "top_p" in ok:
        precise["top_p"] = 1.0 if best_t == 0 else 0.95
    deg = " Greedy decoding degenerated on this model, so the lowest *clean* temperature is used." if temperature.get("greedy_degenerate") else ""
    profiles = {
        "precise": {"label": "Precise", "for": "code, maths, JSON, extraction, anything graded",
                    "params": {**precise, **pen_extra}, "why": f"Lowest temperature that keeps accuracy within noise of the best ({temperature.get('acc_max', 0):.0%}).{deg}"},
    }
    bal_t = min(0.8, max(0.3, round(0.6 * cliff, 1))) if cliff and cliff > 0 else (best_t or 0.0)
    bal_t = max(bal_t, best_t)
    balanced = {"temperature": round(min(bal_t, cliff or bal_t), 2)}
    if "top_p" in ok:
        balanced["top_p"] = 0.95
    profiles["balanced"] = {"label": "Balanced", "for": "chat assistants, customer support, summarisation", "params": {**balanced, **pen_extra, **{k: v for k, v in extras.items() if k != "top_p" or "top_p" not in balanced}},
                            "why": f"About 60% of the coherence cliff ({cliff:g}), never below the precise setting.{note}"}
    cre_t = temperature.get("creative_t")
    if cre_t:
        creative = {"temperature": round(cre_t, 2)}
        if "top_p" in ok:
            creative["top_p"] = 0.95
        if "min_p" in ok and "min_p" not in extras:
            creative["min_p"] = 0.05
        profiles["creative"] = {"label": "Creative", "for": "brainstorming, fiction, marketing copy, varied outputs",
                                "params": {**creative, **pen_extra, **extras}, "why": f"Highest temperature that still produced clean, accurate output ({cre_t:g}); above {temperature.get('breaks_at') or cre_t:g} quality degrades."}
    for prof in profiles.values():
        prof["request"] = {"model": "MODEL", "temperature": prof["params"].get("temperature"), **{k: v for k, v in prof["params"].items() if k in ("top_p", "frequency_penalty")},
                           "extra_body": {k: v for k, v in prof["params"].items() if k in ("top_k", "min_p", "repetition_penalty")}}
    return profiles


def score_sampling(temperature: dict[str, Any], controls: list[dict[str, Any]], det: dict[str, Any], pen: dict[str, Any]) -> dict[str, Any]:
    pts = [p for p in (temperature.get("points") or []) if p.get("acc") is not None]
    acc_max = max((p["acc"] for p in pts), default=0.0) or 1.0
    stable = [p for p in pts if p["t"] <= 1.0]
    stable_mean = (sum((p["acc"] / acc_max) * (p.get("clean") if p.get("clean") is not None else 1.0) for p in stable) / len(stable)) if stable else 0.0
    cliff = temperature.get("cliff_t") or 0.0
    robust = 0.6 * stable_mean + 0.4 * min(1.0, cliff / 1.2)
    weights = {"temperature": 0.25, "top_p": 0.15, "top_k": 0.10, "min_p": 0.05, "seed": 0.15, "frequency_penalty": 0.075, "repetition_penalty": 0.075, "stop": 0.10, "max_tokens": 0.10}
    value = {"honored": 1.0, "unclear": 0.6, "rejected": 0.3, "ignored": 0.0}
    num = den = 0.0
    for c in controls:
        w = weights.get(c["id"])
        if w:
            num += w * value.get(c["status"], 0.5)
            den += w
    control = num / den if den else 0.5
    g = det["greedy"]["identical"]
    s = det["seed_same"]["identical"]
    cc = det["concurrent"]["identical"]
    dparts = [(0.6, g), (0.25, s), (0.15, cc)]
    dn = sum(w * (v if v is not None else 0.6) for w, v in dparts) / sum(w for w, _ in dparts)
    harm = pen.get("harm_at")
    any_eff = pen.get("any_effect")
    pen_score = 0.5 if (not any_eff and pen.get("repetition_prone")) else (0.6 if harm and "0.5" in harm else (0.85 if harm else 1.0))
    overall = 100 * (0.45 * robust + 0.30 * control + 0.15 * dn + 0.10 * pen_score)
    return {"overall": round(overall, 1), "robustness": round(100 * robust, 1), "controllability": round(100 * control, 1),
            "determinism": round(100 * dn, 1), "penalties": round(100 * pen_score, 1)}


def build_findings(temperature: dict[str, Any], nuc: dict[str, Any], pen: dict[str, Any], det: dict[str, Any], controls: list[dict[str, Any]], validation: dict[str, Any], model: dict[str, Any], quick: bool = False) -> list[dict[str, str]]:
    f: list[dict[str, str]] = []

    def add(level: str, text: str) -> None:
        f.append({"level": level, "text": text})

    cliff, breaks = temperature.get("cliff_t"), temperature.get("breaks_at")
    if breaks is not None and cliff is not None:
        add("warn" if cliff < 0.8 else "info", f"Output stays clean up to T = {cliff:g} and degrades from T = {breaks:g}. Cap temperature at {max(cliff, 0.1):g} in production.")
    elif cliff is not None:
        add("good", f"No quality breakdown found up to T = {cliff:g}: the model is unusually tolerant of high temperature.")
    if temperature.get("greedy_degenerate"):
        add("warn", "Greedy decoding (T = 0) produced degenerate or repetitive output. Use a small non-zero temperature instead of greedy.")
    if temperature.get("sensitivity_label") == "high":
        add("warn", "Accuracy drops sharply as temperature rises: keep temperature low for anything graded.")
    elif temperature.get("sensitivity_label") == "low":
        add("good", "Accuracy is insensitive to temperature in the normal range: tuning matters little for this model.")
    for c in controls:
        if c["status"] in ("ignored", "rejected") and c["id"] != "greedy":
            add("warn", f"`{c['label']}` is {'rejected' if c['status'] == 'rejected' else 'silently ignored'} by this endpoint. {c['detail']}")
    g = det["greedy"]["identical"]
    if g is not None and g < 0.99:
        add("warn", f"Temperature 0 is not deterministic ({g:.0%} of repeat pairs identical).")
    if nuc.get("truncation_helps"):
        b = nuc["best"]
        add("info", f"Truncation helps at high temperature: {b['label']} recovered quality at T = {nuc.get('stress_temperature', '')} while keeping output varied.")
    elif nuc.get("stress_degraded") is False:
        add("info", "Quality did not degrade at the stress temperature, so top-p / top-k truncation is not needed for this model.")
    if pen.get("repetition_prone"):
        r = pen.get("recommended")
        add("info", "The model repeats itself on a repetition-prone prompt" + (f"; {r['label']} reduces it without hurting accuracy." if r else ", and no penalty setting fixed it safely."))
    if pen.get("harm_at"):
        add("warn", f"Accuracy dropped at {pen['harm_at']}: keep penalties mild on maths and code.")
    for c in validation.get("comparisons", []):
        name = "the generic API defaults" if c["against"] == "api_default" else "the maker's published settings"
        lo, hi = c["ci"]
        if c["verdict"] == "improved":
            add("good", f"Tuned settings beat {name} by {c['delta']:+.0%} on held-out problems (95% CI {lo:+.0%} to {hi:+.0%}).")
        elif c["verdict"] == "worse":
            add("warn", f"Tuned settings did worse than {name} ({c['delta']:+.0%}, 95% CI {lo:+.0%} to {hi:+.0%}); prefer {name}.")
        else:
            add("info", f"Tuned settings and {name} are within noise of each other ({c['delta']:+.0%}, 95% CI {lo:+.0%} to {hi:+.0%}): the defaults are fine.")
    if quick:
        add("info", "Quick mode samples sparsely (a few temperatures and problems): treat the curves and the tuned profiles as indicative, and run the full suite before committing to settings.")
    return f
