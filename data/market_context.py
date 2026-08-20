"""
market_context.py  —  is the market hot or cold today?
========================================================
A stock's move means nothing in isolation — a 4% move is extraordinary in a
flat market and unremarkable if the whole market is up 5%. This fetches the
Nifty 50 index ONCE per run (not per stock) and computes real, code-only
context every other module can compare against.

Sector-relative comparison (e.g. vs Nifty IT) is intentionally not included
yet — there's no free, reliable per-stock-to-sector-index mapping, so rather
than guess, this sticks to broad-market context only.
"""

from data import signals


def get_market_context() -> dict:
    """Real Nifty 50 data: today's move, trend, volatility regime."""
    import yfinance as yf

    hist = yf.Ticker("^NSEI").history(period="1y")
    if hist.empty:
        raise RuntimeError("[market_context] no Nifty 50 (^NSEI) data — check internet connectivity.")

    close = hist["Close"]
    last, prev = float(close.iloc[-1]), float(close.iloc[-2])

    return {
        "nifty_close": round(last, 2),
        "nifty_pct_change": signals.pct_change(last, prev),
        "nifty_trend": signals.trend_structure(close),
        "nifty_volatility_pct": signals.historical_volatility(close),
        "close_series": close,  # kept for relative-strength calcs elsewhere in this run
    }


def market_breadth(activity: list) -> dict:
    """% of the scanned universe advancing vs declining today — pure arithmetic."""
    if not activity:
        return {"advancing_pct": 0.0, "declining_pct": 0.0}
    advancing = sum(1 for a in activity if a["pct_change"] > 0)
    declining = sum(1 for a in activity if a["pct_change"] < 0)
    total = len(activity)
    return {
        "advancing_pct": round(advancing / total * 100, 1),
        "declining_pct": round(declining / total * 100, 1),
    }
