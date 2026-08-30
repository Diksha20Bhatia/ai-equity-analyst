"""
run_bodhi_risk_insight.py  —  on-demand Bodhi risk verification, standalone
=====================================================================
    uv run python integrations/run_bodhi_risk_insight.py

Loads the most recent output/analysis_*.json (written by run.py at the
end of a swing run) and sends its real shortlist to Bodhi for risk
verification — without re-running the whole pipeline. This is what the
dashboard's "🧠 Get Bodhi Risk Insight" button calls, the same way
tracker/price_tracker.py is a standalone companion to run.py rather
than a step inside it.

Requires at least one swing run (run.py) to have completed today or
earlier, and BODHI_ENABLED=true with the Bodhi variables set in .env —
see .env.example. Prints Bodhi's per-symbol verdicts (if any were found)
and sends the full report to Telegram; does not touch TradingView here
(that gating only happens inside a real run.py run).
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from integrations.bodhi_risk_insights import verify_shortlist  # noqa: E402

OUTPUT_DIR = ROOT / "output"


def _latest_run() -> Path:
    runs = sorted(OUTPUT_DIR.glob("analysis_*.json"), reverse=True)
    if not runs:
        raise RuntimeError(
            f"No saved runs found in {OUTPUT_DIR} — run.py needs to complete "
            "at least once before there's a shortlist to send to Bodhi."
        )
    return runs[0]


def run():
    run_path = _latest_run()
    saved = json.loads(run_path.read_text())
    print(f"[bodhi] using shortlist from {run_path.name}")

    result = verify_shortlist(saved["results_by_tier"], saved["analyses"])
    if not result["ran"]:
        raise RuntimeError("Bodhi did not return a result — see the [bodhi] log above.")

    if result["verdicts"]:
        print("\nPer-symbol verdicts:")
        for symbol, v in result["verdicts"].items():
            flag = " ⚠ FLAGGED" if symbol in result["flagged_symbols"] else ""
            print(f"  {symbol}: {v['verdict']}{flag} — {v['notes']}")
    else:
        print("\nNo structured per-symbol verdict found — see the full report below.")

    from alerts import telegram_alert
    telegram_alert.send("*🧠 Bodhi Risk Insight*\n\n" + result["report"])

    print("\n" + result["report"])


if __name__ == "__main__":
    run()
