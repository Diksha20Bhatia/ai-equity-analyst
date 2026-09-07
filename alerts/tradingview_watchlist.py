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


def add_symbols(symbols: list) -> dict | None:
    """
    symbols: real NSE ticker symbols, e.g. ["RELIANCE", "TCS"]. Converted to
    TradingView's EXCHANGE:SYMBOL format and bulk-added to whichever
    watchlist is currently open in TradingView Desktop.

    Never raises — a missing watchlist push should never take down the rest
    of a run. Returns None for a no-op (disabled, or no symbols given) or a
    real attempt's result: {"added": N, "failed": N, "results": [...]}.
    Callers that need to know whether it genuinely worked (e.g. a
    standalone "push now" button) should check "added"/"failed" rather
    than assume success just because this didn't raise.
    """
    if not settings.tradingview_watchlist_enabled:
        return None
    if not symbols:
        return None

    import observability

    tv_symbols = [f"NSE:{s}" for s in dict.fromkeys(symbols)]  # de-dupe, keep order

    try:
        proc = subprocess.run(
            ["node", settings.tradingview_cli_path, "watchlist", "add-bulk", *tv_symbols],
            capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        detail = f"'node' not found, or tradingview-mcp isn't cloned at {settings.tradingview_cli_path}"
        print(f"[tradingview] skipped: {detail}")
        observability.record_failure("tradingview_watchlist", detail, symbols=tv_symbols)
        return {"added": 0, "failed": len(tv_symbols), "results": []}
    except subprocess.TimeoutExpired:
        detail = "timed out — is TradingView Desktop running with the debug port enabled?"
        print(f"[tradingview] skipped: {detail}")
        observability.record_failure("tradingview_watchlist", detail, symbols=tv_symbols)
        return {"added": 0, "failed": len(tv_symbols), "results": []}

    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        print(f"[tradingview] failed: {detail}")
        observability.record_failure("tradingview_watchlist", detail, symbols=tv_symbols)
        return {"added": 0, "failed": len(tv_symbols), "results": []}

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"[tradingview] unexpected output: {proc.stdout.strip()}")
        observability.record_failure("tradingview_watchlist", f"unexpected output: {proc.stdout.strip()[:300]}")
        return {"added": 0, "failed": len(tv_symbols), "results": []}

    added = result.get("added", 0)
    failed = result.get("failed", 0)
    print(f"[tradingview] watchlist updated: {added} added, {failed} failed "
          f"(of {len(tv_symbols)})")
    if failed:
        for r in result.get("results", []):
            if not r.get("success"):
                print(f"  [tradingview] {r.get('symbol')}: {r.get('error', 'not added')}")

    return {"added": added, "failed": failed, "results": result.get("results", [])}
