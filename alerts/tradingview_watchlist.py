"""
tradingview_watchlist.py  —  pushes today's shortlist to your real TradingView watchlist
=============================================================================================
Optional. If TRADINGVIEW_WATCHLIST_ENABLED isn't set, this is a no-op — the
pipeline behaves exactly as if this file didn't exist.

When enabled, every symbol from today's shortlist(s) is added to whatever
watchlist is currently active in your TradingView Desktop app, via the
tradingview-mcp CLI (https://github.com/tradesdontlie/tradingview-mcp). This
is a LOCAL connection only — no TradingView credentials are stored or sent
anywhere; the CLI drives your already-logged-in Desktop app over Chrome
DevTools Protocol on localhost.

This can only work while TradingView Desktop is running with the debug
port enabled (`scripts/launch_tv_debug_mac.sh` in that repo). If it isn't,
this fails loudly to the console but never raises — a missing watchlist
push should never take down the rest of the run.
"""

import json
import subprocess

from config import settings


def add_symbols(symbols: list) -> None:
    """
    symbols: real NSE ticker symbols, e.g. ["RELIANCE", "TCS"]. Converted to
    TradingView's EXCHANGE:SYMBOL format and bulk-added to whichever
    watchlist is currently open in TradingView Desktop.
    """
    if not settings.tradingview_watchlist_enabled:
        return
    if not symbols:
        return

    tv_symbols = [f"NSE:{s}" for s in dict.fromkeys(symbols)]  # de-dupe, keep order

    try:
        proc = subprocess.run(
            ["node", settings.tradingview_cli_path, "watchlist", "add-bulk", *tv_symbols],
            capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        print("[tradingview] skipped: 'node' not found, or tradingview-mcp isn't cloned "
              f"at {settings.tradingview_cli_path}")
        return
    except subprocess.TimeoutExpired:
        print("[tradingview] skipped: timed out — is TradingView Desktop running "
              "with the debug port enabled?")
        return

    if proc.returncode != 0:
        print(f"[tradingview] failed: {proc.stderr.strip() or proc.stdout.strip()}")
        return

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"[tradingview] unexpected output: {proc.stdout.strip()}")
        return

    added = result.get("added", 0)
    failed = result.get("failed", 0)
    print(f"[tradingview] watchlist updated: {added} added, {failed} failed "
          f"(of {len(tv_symbols)})")
    if failed:
        for r in result.get("results", []):
            if not r.get("success"):
                print(f"  [tradingview] {r.get('symbol')}: {r.get('error', 'not added')}")
