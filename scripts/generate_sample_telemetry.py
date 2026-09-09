"""
generate_sample_telemetry.py  —  writes real, reviewer-visible sample
telemetry files to docs/sample_telemetry/

These are NOT a captured real production run, and NOT a real incident —
this project's standing rule is to never narrate a specific real failure
in checked-in documentation. What this script writes IS real: it calls
the actual observability.record_call() / record_failure() /
record_orchestrator_decision() / record_tool_call() functions this
project uses everywhere else, against a small, fully deterministic,
mocked scenario (the same shapes as tests/test_scenario_full_pipeline.py
and tests/test_scenario_bodhi_branches.py) — so the JSON shape, field
names, and formatting are guaranteed to match what a real run actually
produces, without pretending a specific real incident happened.

Regenerate any time with:
    uv run python scripts/generate_sample_telemetry.py

See docs/sample_telemetry/README.md for what each file is.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import observability  # noqa: E402

OUT_DIR = ROOT / "docs" / "sample_telemetry"


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    observability._FAILURE_LOG = OUT_DIR / "reliability_log.sample.jsonl"
    observability._CALL_LOG = OUT_DIR / "llm_call_log.sample.jsonl"
    observability._DECISION_LOG = OUT_DIR / "orchestrator_decisions.sample.jsonl"
    observability._TOOL_CALL_LOG = OUT_DIR / "tool_call_log.sample.jsonl"

    for path in (observability._FAILURE_LOG, observability._CALL_LOG,
                 observability._DECISION_LOG, observability._TOOL_CALL_LOG):
        path.unlink(missing_ok=True)  # regenerate clean each time, not append-forever

    # --- llm_call_log.jsonl — a real batched call per agent in one swing run
    observability.record_call(
        backend="bodhi_llm_gateway", model="gemini-2.5-pro", latency_ms=1180.4,
        usage={"prompt_tokens": 2100, "completion_tokens": 340, "total_tokens": 2440},
        agent="News/Event Reader", attempts=1,
    )
    observability.record_call(
        backend="bodhi_llm_gateway", model="gemini-2.5-pro", latency_ms=2350.7,
        usage={"prompt_tokens": 3200, "completion_tokens": 510, "total_tokens": 3710},
        agent="Bull Case Agent", attempts=1,
    )
    observability.record_call(
        backend="bodhi_llm_gateway", model="gemini-2.5-pro", latency_ms=2410.2,
        usage={"prompt_tokens": 3150, "completion_tokens": 495, "total_tokens": 3645},
        agent="Bear Case Agent", attempts=1,
    )
    observability.record_call(
        backend="bodhi_llm_gateway", model="gemini-2.5-pro", latency_ms=3980.9,
        usage={"prompt_tokens": 5400, "completion_tokens": 820, "total_tokens": 6220},
        agent="Decision Agent", attempts=1,
    )

    # --- reliability_log.jsonl — a generic, illustrative retry shape (a
    # transient 429), not a specific real incident.
    observability.record_failure(
        "bodhi_llm_gateway", "HTTP 429: rate limited, retrying with backoff",
        attempt=1, status_code=429, model="gemini-2.5-pro",
    )

    # --- orchestrator_decisions.jsonl — one retry, one clean finish, the
    # same two outcomes tests/test_scenario_full_pipeline.py exercises.
    observability.record_orchestrator_decision(
        tier="moderate", retries=0,
        issues=["shape: shortlist.0.bear_case — String should have at least 1 character"],
        next_step="retry_decision", reason="a required field was missing — fixable from the same real data",
    )
    observability.record_orchestrator_decision(
        tier="moderate", retries=1, issues=[],
        next_step="finish", reason="quality check passed — no issues found",
    )

    # --- tool_call_log.jsonl — both real TradingView tool primitives.
    observability.record_tool_call(
        tool="tradingview_live_quote", target="NSE:RELIANCE", success=True,
        latency_ms=21870.4, agent="Decision Agent",
    )
    observability.record_tool_call(
        tool="tradingview_symbol_search", target="RELIANCE", success=True,
        latency_ms=740.1, agent="Decision Agent",
    )

    for path in (observability._CALL_LOG, observability._FAILURE_LOG,
                 observability._DECISION_LOG, observability._TOOL_CALL_LOG):
        print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
