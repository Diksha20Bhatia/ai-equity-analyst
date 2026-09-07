"""
trigger_tradingview.py  —  on-demand TradingView push, standalone
=====================================================================
    uv run python alerts/trigger_tradingview.py

Loads the most recent output/analysis_*.json and pushes its deduped
shortlisted symbols to your real TradingView watchlist — without
re-running the whole pipeline. This is what the dashboard's "📺 Push to
TradingView" button calls. Requires TRADINGVIEW_WATCHLIST_ENABLED=true
and TradingView Desktop running with its debug port enabled — see
README.md; if it isn't reachable, this logs a skip and exits 0 rather
than failing loudly, matching alerts/tradingview_watchlist.py's own
best-effort behaviour.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alerts import tradingview_watchlist  # noqa: E402
from config import settings  # noqa: E402

OUTPUT_DIR = ROOT / "output"


def _latest_run() -> Path:
    runs = sorted(OUTPUT_DIR.glob("analysis_*.json"), reverse=True)
    if not runs:
        raise RuntimeError(
            f"No saved runs found in {OUTPUT_DIR} — run.py needs to complete "
            "at least once before there's a shortlist to push."
        )
    return runs[0]


def run():
    if not settings.tradingview_watchlist_enabled:
        raise RuntimeError(
            "TRADINGVIEW_WATCHLIST_ENABLED isn't set to true in .env — nothing to push."
        )

    run_path = _latest_run()
    saved = json.loads(run_path.read_text())
    print(f"[tradingview] using shortlist from {run_path.name}")

    results_by_tier = saved.get("results_by_tier") or (
        {"moderate": saved["result"]} if saved.get("result") else {}
    )
    symbols = []
    for result in results_by_tier.values():
        for pick in result.get("shortlist", []):
            if pick["symbol"] not in symbols:
                symbols.append(pick["symbol"])

    if not symbols:
        raise RuntimeError(f"No shortlist found in {run_path.name}.")

    print(f"[tradingview] pushing {len(symbols)} symbol(s): {', '.join(symbols)}")
    result = tradingview_watchlist.add_symbols(symbols)
    if not result or result.get("added", 0) == 0:
        raise RuntimeError(
            "Nothing was actually added to TradingView — see the [tradingview] "
            "lines above for the real reason (Desktop not running with its "
            "debug port enabled is the usual cause)."
        )


if __name__ == "__main__":
    run()
