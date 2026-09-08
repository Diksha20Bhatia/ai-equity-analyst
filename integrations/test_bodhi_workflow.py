"""
test_bodhi_workflow.py  —  fast, isolated Bodhi workflow test with fake data
=============================================================================
    uv run python integrations/test_bodhi_workflow.py

Sends a small, obviously-synthetic test portfolio straight to Bodhi via
integrations/bodhi_client.py — no market data fetch, no Gemini/LLM calls,
no Telegram send. Only for iterating on the Bodhi workflow itself (the
JS expressions, branching, prompts) without paying the cost of a full
run.py pass each time.

The test portfolio deliberately includes a bank name with a rate-risk
narrative, so a real risk review is likely to flag at least one symbol
as caution/avoid — exercising the escalation branch, not just the clean
path.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import settings  # noqa: E402
from integrations.bodhi_client import call_bodhi_workflow  # noqa: E402
from integrations.bodhi_risk_insights import _query_text  # noqa: E402

TEST_PORTFOLIO = [
    {
        "symbol": "RELIANCE",
        "sector": "Energy",
        "action": "accumulate",
        "opportunity_score": 78,
        "confidence": 82,
        "conviction": "HIGH",
        "current_price": 2950.5,
        "target_price": 3150.0,
        "bull_case": "TEST DATA — refining margins improving, retail arm scaling well.",
        "bear_case": "TEST DATA — crude price volatility could pressure near-term margins.",
        "risk_percent": 35,
        "allocated_amount": 50000,
        "tiers": ["moderate"],
    },
    {
        "symbol": "ICICIBANK",
        "sector": "Banking",
        "action": "accumulate",
        "opportunity_score": 71,
        "confidence": 65,
        "conviction": "LOW",
        "current_price": 1180.0,
        "target_price": 1260.0,
        "bull_case": "TEST DATA — improving asset quality, strong deposit growth.",
        "bear_case": "TEST DATA — NIM compression risk if RBI cuts rates faster than expected.",
        "risk_percent": 40,
        "allocated_amount": 30000,
        "tiers": ["moderate"],
    },
]


def run():
    missing = [
        name for name, value in (
            ("BODHI_PAT_TOKEN", settings.bodhi_pat_token),
            ("BODHI_TASK_ID", settings.bodhi_task_id),
            ("BODHI_WORKFLOW_ID", settings.bodhi_workflow_id),
            ("BODHI_BASE_URL", settings.bodhi_base_url),
        ) if not value
    ]
    if missing:
        raise RuntimeError(f"Bodhi isn't configured — set {', '.join(missing)} in .env.")

    print("[test] sending synthetic test portfolio to Bodhi (no other agents, no Telegram)...")
    print(f"[test] test symbols: {', '.join(p['symbol'] for p in TEST_PORTFOLIO)}")

    variables = {
        settings.bodhi_portfolio_variable: json.dumps(TEST_PORTFOLIO, indent=2, default=str),
        settings.bodhi_query_variable: _query_text(),
    }

    report, run_id = call_bodhi_workflow(
        base_url=settings.bodhi_base_url,
        pat_token=settings.bodhi_pat_token,
        task_id=settings.bodhi_task_id,
        workflow_id=settings.bodhi_workflow_id,
        trace_prefix=settings.bodhi_trace_prefix,
        variables=variables,
        poll_interval=settings.bodhi_poll_interval,
        timeout=settings.bodhi_timeout,
    )

    print(f"\n[test] run id: {run_id}")
    print("[test] full report:\n")
    print(report)


if __name__ == "__main__":
    run()
