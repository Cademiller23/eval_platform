from evalplatform.speculative import analyze, copy_ratio, spec_counters

BEFORE = (
    'vllm:spec_decode_num_drafts_total{model_name="m"} 10\n'
    'vllm:spec_decode_num_draft_tokens_total{model_name="m"} 50\n'
    'vllm:spec_decode_num_accepted_tokens_total{model_name="m"} 20\n'
)
AFTER = BEFORE.replace(" 10\n", " 110\n").replace(" 50\n", " 550\n").replace(" 20\n", " 220\n")
FLAT = {"chunks": 200, "multi_token_chunk_ratio": 0.0, "tokens_per_chunk": 1.0}
BURSTY = {"chunks": 200, "multi_token_chunk_ratio": 0.45, "tokens_per_chunk": 1.9}
MODEL = {"speculators": [{"method": "ngram"}], "mtp_native": False}


def test_counters_parse_and_diff():
    c = spec_counters(AFTER)
    assert c == {"drafts": 110.0, "draft_tokens": 550.0, "accepted": 220.0}
    assert spec_counters("vllm:generation_tokens_total 4\n") is None


def test_active_from_metrics():
    r = analyze({"speculative_config": {"method": "ngram"}, "speculative_known": True}, BEFORE, AFTER, BURSTY, MODEL)
    assert r["status"] == "active"
    assert r["acceptance_rate"] == 0.4 and r["mean_accepted_length"] == 3.0
    assert r["method"] == "ngram"


def test_not_detected_when_known_off_and_flat():
    r = analyze({"speculative_known": True, "metrics_available": True}, None, "vllm:generation_tokens_total 4\n", FLAT, MODEL)
    assert r["status"] == "not_detected" and r["confidence"] > 0.9


def test_unknown_endpoint_behaviour_only():
    r = analyze({"speculative_known": False}, None, None, BURSTY, MODEL)
    assert r["status"] == "likely"
    r = analyze({"speculative_known": False}, None, None, {"chunks": 0}, MODEL)
    assert r["status"] == "unknown"


def test_configured_but_idle():
    flat_metrics = BEFORE
    r = analyze({"speculative_config": {"method": "ngram"}, "speculative_known": True}, flat_metrics, flat_metrics, FLAT, MODEL)
    assert r["status"] == "likely" and "no activity" in r["headline"]


def test_sglang_gauges():
    text = "sglang:spec_accept_length 2.4\nsglang:spec_accept_rate 0.6\n"
    r = analyze({"speculative_known": False, "metrics_available": True}, None, text, FLAT, MODEL)
    assert r["status"] == "active" and r["mean_accepted_length"] == 2.4


def test_copy_ratio():
    assert copy_ratio("write def add(a, b): return a + b please", "def add(a, b): return a + b") > 0.8
    assert copy_ratio("hello there", "completely unrelated words appear here today") == 0.0
