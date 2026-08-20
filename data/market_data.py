"""
market_data.py  —  the data plumbing
====================================
Pulls real data for every stock, no synthetic fallback:

  activity/technicals -> real prices & volume via Yahoo Finance (yfinance),
                          plus pure-math signals (data/signals.py): RSI,
                          ATR, volatility, beta, drawdown, relative
                          strength, breakout/gap detection.
  fundamentals         -> real figures from Yahoo's company info (revenue
                           growth, margins, ROE, debt/equity, valuation
                           ratios, cash flow). ROCE isn't exposed by any
                           free source, so it's simply omitted.
  headlines             -> real recent headlines via Google News RSS
  risk_inputs           -> real turnover, volatility, beta, drawdown

If real data can't be fetched for a stock, get_bundles() SKIPS it (logs why)
rather than crashing the whole scan — a data-quality gate, not a fabricated
substitute. Every bundle also carries a "data_quality" list of any issues
found, so downstream agents can lower confidence rather than pretend
everything is pristine.
"""

import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests

from data import signals

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ai-equity-analyst/1.0)"}


# ----------------------------------------------------------------------
# REAL NEWS  — Google News RSS search, free and keyless.
# ----------------------------------------------------------------------
def _real_headlines(symbol: str, limit: int = 8) -> list:
    query = quote(f"{symbol} NSE stock")
    url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
    resp = requests.get(url, headers=_HEADERS, timeout=10)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    titles = [item.findtext("title") for item in root.findall(".//item")]
    titles = [t for t in titles if t][:limit]
    return titles


# ----------------------------------------------------------------------
# DATA QUALITY GATE  — code only, never let an agent guess over a gap.
# ----------------------------------------------------------------------
def _assess_quality(hist, info: dict, headlines: list) -> list:
    issues = []
    if hist.empty:
        return ["no price history"]
    last_date = hist.index[-1].date()
    import datetime as dt
    if (dt.date.today() - last_date).days > 5:
        issues.append(f"price data stale (last bar {last_date.isoformat()})")
    if len(hist) < 200:
        issues.append(f"only {len(hist)} days of history (< 200) — some signals less reliable")
    if not info.get("trailingPE") and not info.get("priceToBook"):
        issues.append("valuation fields missing from Yahoo Finance")
    if not headlines:
        issues.append("no recent news found")
    return issues


# ----------------------------------------------------------------------
# REAL MARKET DATA  — yfinance for prices/volume/company info.
# ----------------------------------------------------------------------
def _fetch_bundle(symbol: str, index_close=None) -> dict:
    import yfinance as yf

    ticker = yf.Ticker(f"{symbol}.NS")
    hist = ticker.history(period="1y")
    if hist.empty:
        raise RuntimeError(f"no live price history for {symbol}.NS")

    close = hist["Close"]
    vol = hist["Volume"]
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2]) if len(close) > 1 else last
    vol_ratio = float(vol.iloc[-1] / max(vol.tail(20).mean(), 1))
    turnover_cr = float((close.tail(20) * vol.tail(20)).mean() / 1e7)

    info = ticker.info
    headlines = _real_headlines(symbol)
    quality = _assess_quality(hist, info, headlines)

    # ---- fundamentals: only real fields Yahoo actually reports ----
    fundamentals = {}
    if info.get("revenueGrowth") is not None:
        fundamentals["revenue_growth"] = round(info["revenueGrowth"] * 100, 1)
    if info.get("operatingMargins") is not None:
        fundamentals["operating_margin"] = round(info["operatingMargins"] * 100, 1)
    if info.get("returnOnEquity") is not None:
        fundamentals["roe"] = round(info["returnOnEquity"] * 100, 1)
    if info.get("earningsGrowth") is not None:
        fundamentals["eps_growth"] = round(info["earningsGrowth"] * 100, 1)
    if info.get("debtToEquity") is not None:
        fundamentals["debt_to_equity"] = round(info["debtToEquity"] / 100, 2)
    if info.get("currentRatio") is not None:
        fundamentals["current_ratio"] = round(info["currentRatio"], 2)
    if info.get("trailingPE") is not None:
        fundamentals["pe_ratio"] = round(info["trailingPE"], 1)
    if info.get("priceToBook") is not None:
        fundamentals["price_to_book"] = round(info["priceToBook"], 2)
    if info.get("enterpriseToEbitda") is not None:
        fundamentals["ev_to_ebitda"] = round(info["enterpriseToEbitda"], 1)
    if info.get("priceToSalesTrailing12Months") is not None:
        fundamentals["price_to_sales"] = round(info["priceToSalesTrailing12Months"], 2)
    if info.get("operatingCashflow") is not None:
        fundamentals["operating_cashflow_cr"] = round(info["operatingCashflow"] / 1e7, 1)
    if info.get("freeCashflow") is not None:
        fundamentals["free_cashflow_cr"] = round(info["freeCashflow"] / 1e7, 1)
    pe_context = signals.historical_pe_context(close, info.get("trailingEps"))
    if pe_context:
        fundamentals["valuation_vs_own_history"] = pe_context
    if not fundamentals:
        raise RuntimeError(f"Yahoo Finance returned no fundamentals fields for {symbol}")

    # ---- pure-math technical/risk signals ----
    rel_strength = {}
    beta_val = None
    if index_close is not None:
        rel_strength = {
            "1m": signals.relative_strength(close, index_close, 21),
            "3m": signals.relative_strength(close, index_close, 63),
            "6m": signals.relative_strength(close, index_close, 126),
        }
        beta_val = signals.beta(close, index_close)

    breakout = signals.breakout_flags(close, hist)

    return {
        "symbol": symbol,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "data_quality": quality,
        "activity": {
            "symbol": symbol,
            "pct_change": signals.pct_change(last, prev),
            "volume_ratio": round(vol_ratio, 1),
            "has_block_deal": vol_ratio > 3,  # heuristic
            "gap_pct": signals.gap_pct(hist),
            "volatility_anomaly_z": signals.volatility_anomaly_z(close),
            "breakout_20d": breakout["breakout_20d"],
            "near_52w_high": breakout["near_52w_high"],
        },
        "fundamentals": fundamentals,
        "technicals": {
            "above_sma50": bool(last > signals.sma(close, 50)),
            "above_sma200": bool(last > signals.sma(close, min(200, len(close)))),
            "rsi": signals.rsi(close),
            "near_high": breakout["near_52w_high"],
            "volume_ratio": round(vol_ratio, 1),
            "trend_structure": signals.trend_structure(close),
            "atr": signals.atr(hist),
            "relative_strength_vs_nifty": rel_strength,
        },
        "headlines": headlines,
        "risk_inputs": {
            "liquidity_cr": round(turnover_cr, 1),
            "historical_volatility_pct": signals.historical_volatility(close),
            "downside_volatility_pct": signals.downside_volatility(close),
            "beta_vs_nifty": beta_val,
            "max_drawdown_pct": signals.max_drawdown(close),
            "gap_pct": signals.gap_pct(hist),
        },
        "close_series": close,  # kept for the caller (memory audit, dashboard charts)
    }


# ----------------------------------------------------------------------
# PUBLIC API
# ----------------------------------------------------------------------
def get_bundles(symbols: list, index_close=None) -> dict:
    """
    Return {symbol: bundle} for every symbol real data could be fetched for.
    A bad/delisted symbol or a transient Yahoo failure is skipped and logged
    — a data-quality gate, not a crash — so one bad ticker doesn't abort a
    50-500 stock scan.
    """
    out = {}
    for sym in symbols:
        try:
            out[sym] = _fetch_bundle(sym, index_close=index_close)
        except Exception as e:  # noqa: BLE001
            print(f"[data] skipping {sym}: insufficient data ({e})")
    return out


def get_activity(bundles: dict) -> list:
    """Extract just the Research-Agent scan rows."""
    return [b["activity"] for b in bundles.values()]


def bundle_market_stats(bundle: dict) -> dict:
    """
    Current price + real 1w/1m/6m performance, computed from a bundle's
    already-fetched 1-year history — avoids a second network call for
    stocks we just fetched in get_bundles().
    """
    close = bundle["close_series"]
    current = float(close.iloc[-1])

    def window_stats(trading_days: int) -> dict:
        w = close.tail(min(trading_days, len(close)))
        start = float(w.iloc[0])
        return {
            "change_pct": round((current - start) / start * 100, 2),
            "high": round(float(w.max()), 2),
            "low": round(float(w.min()), 2),
        }

    return {
        "current_price": round(current, 2),
        "1w": window_stats(5),
        "1m": window_stats(21),
        "6m": window_stats(126),
    }


def get_price_history(symbol: str, period: str = "6mo"):
    """Real daily OHLCV history for `symbol` (pandas DataFrame indexed by date)."""
    import yfinance as yf

    hist = yf.Ticker(f"{symbol}.NS").history(period=period)
    if hist.empty:
        raise RuntimeError(f"[data] no price history for {symbol}.NS over {period}.")
    return hist


def get_market_stats(symbol: str) -> dict:
    """
    Current price plus real 1-week / 1-month / 6-month change%, high, low.
    One 6-month fetch, sliced into windows — no repeated network calls.
    """
    hist = get_price_history(symbol, period="6mo")
    close = hist["Close"]
    current = float(close.iloc[-1])

    def window_stats(trading_days: int) -> dict:
        w = close.tail(min(trading_days, len(close)))
        start = float(w.iloc[0])
        return {
            "change_pct": round((current - start) / start * 100, 2),
            "high": round(float(w.max()), 2),
            "low": round(float(w.min()), 2),
        }

    return {
        "current_price": round(current, 2),
        "1w": window_stats(5),
        "1m": window_stats(21),
        "6m": window_stats(len(close)),
    }
