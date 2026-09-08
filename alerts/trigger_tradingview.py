"""
trigger_tradingview.py  —  on-demand TradingView push, standalone
=====================================================================
    uv run python alerts/trigger_tradingview.py

Pushes the most recently APPROVED run's candidate symbols to your real
TradingView watchlist — reads only from output/approved/
(alerts/approval_gate.py), never from a pending or rejected run, so a
run that was never explicitly approved can never reach TradingView by
accident. This is what the dashboard's "📺 Push to TradingView" button
calls. Requires TRADINGVIEW_WATCHLIST_ENABLED=true and TradingView
Desktop running with its debug port enabled — see README.md; if it
isn't reachable, this logs a skip and exits 0 rather than failing
loudly, matching alerts/tradingview_watchlist.py's own best-effort
behaviour.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alerts import tradingview_watchlist, approval_gate  # noqa: E402
from config import settings  # noqa: E402


def run():
    if not settings.tradingview_watchlist_enabled:
        raise RuntimeError(
            "TRADINGVIEW_WATCHLIST_ENABLED isn't set to true in .env — nothing to push."
        )

    approved = approval_gate.list_approved()
    if not approved:
        raise RuntimeError(
            "No approved runs found — nothing to push. Run `uv run python "
            "run.py` then approve it with `uv run python approve_run.py --latest`."
        )
    run_path = approved[0]
    saved = approval_gate.load(run_path)
    print(f"[tradingview] using shortlist from {run_path.name} (approved {saved.get('approved_at', '?')})")

    symbols = saved.get("tv_symbols") or []
    if not symbols:
        raise RuntimeError(f"No TradingView candidates found in {run_path.name}.")

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
