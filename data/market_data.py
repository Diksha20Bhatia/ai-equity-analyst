"""
market_data.py  —  the data plumbing
====================================
Gives every agent the raw numbers it needs. Two modes:

  demo : returns realistic, deterministic sample data. No internet, no keys.
         Perfect for your first run and for understanding the flow.
  live : pulls real prices from Yahoo Finance (yfinance) using the .NS
         (NSE) suffix, real recent headlines from Google News, and real
         traded turnover for liquidity risk. Deep fundamentals (ROCE) and
         promoter/pledge data need paid APIs — see the notes below.

Each stock returns four bundles that map 1:1 to the four analysis agents:
  fundamentals -> Fundamental Agent
  technicals   -> Technical Agent
  headlines    -> Sentiment Agent
  risk_inputs  -> Risk Agent
plus the "activity" row the Research Agent scans.
"""

import hashlib
import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests

from config import settings

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ai-equity-analyst/1.0)"}


# ----------------------------------------------------------------------
# DEMO DATA  — deterministic so every run looks the same and is explainable.
# We hash the symbol to generate stable pseudo-random-but-sensible numbers.
# ----------------------------------------------------------------------
def _seed(symbol: str) -> float:
    h = int(hashlib.md5(symbol.encode()).hexdigest(), 16)
    return (h % 1000) / 1000.0  # 0.000 - 0.999


def _demo_bundle(symbol: str) -> dict:
    s = _seed(symbol)
    pct = round((s - 0.5) * 12, 1)              # -6% .. +6%
    vol = round(0.8 + s * 2.5, 1)               # 0.8x .. 3.3x
    return {
        "symbol": symbol,
        "activity": {
            "symbol": symbol,
            "pct_change": pct,
            "volume_ratio": vol,
            "has_block_deal": s > 0.75,
        },
        "fundamentals": {
            "revenue_growth": round(2 + s * 28, 1),     # 2% .. 30%
            "operating_margin": round(8 + s * 27, 1),   # 8% .. 35%
            "roce": round(6 + s * 24, 1),               # 6% .. 30%
            "debt_to_equity": round(0.1 + (1 - s) * 1.8, 2),
            "pe_ratio": round(12 + s * 40, 1),          # 12 .. 52
        },
        "technicals": {
            "above_sma50": s > 0.4,
            "above_sma200": s > 0.35,
            "rsi": round(35 + s * 45),                  # 35 .. 80
            "near_high": s > 0.7,
            "volume_ratio": vol,
        },
        "headlines": _demo_headlines(symbol, s),
        "risk_inputs": {
            "liquidity_cr": round(3 + s * 120, 1),      # 3 .. 123 Cr
        },
    }


def _demo_headlines(symbol, s):
    if s > 0.66:
        return [
            f"{symbol} posts record quarterly profit, beats estimates",
            f"Brokerages upgrade {symbol} on strong order book",
        ]
    if s > 0.33:
        return [
            f"{symbol} in line with expectations this quarter",
            f"{symbol} announces routine board meeting",
        ]
    return [
        f"{symbol} slips as margins decline on higher costs",
        f"Analysts flag weak demand outlook for {symbol}",
    ]


# ----------------------------------------------------------------------
# REAL NEWS  — Google News RSS search, free and keyless.
# ----------------------------------------------------------------------
def _real_headlines(symbol: str, limit: int = 5) -> list:
    query = quote(f"{symbol} NSE stock")
    url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        titles = [item.findtext("title") for item in root.findall(".//item")]
        titles = [t for t in titles if t][:limit]
        return titles or [f"No recent news found for {symbol}"]
    except Exception as e:
        print(f"[data] news fetch failed for {symbol}: {e}")
        return [f"No recent news found for {symbol}"]


# ----------------------------------------------------------------------
# LIVE DATA  — real prices via yfinance; ROCE still needs a paid API.
# ----------------------------------------------------------------------
def _live_bundle(symbol: str) -> dict:
    try:
        import yfinance as yf
    except ImportError:
        print("[data] yfinance not installed — falling back to demo for", symbol)
        return _demo_bundle(symbol)

    ticker = yf.Ticker(f"{symbol}.NS")
    hist = ticker.history(period="1y")
    if hist.empty:
        print(f"[data] no live data for {symbol}, using demo.")
        return _demo_bundle(symbol)

    close = hist["Close"]
    vol = hist["Volume"]
    last = close.iloc[-1]
    prev = close.iloc[-2] if len(close) > 1 else last
    sma50 = close.tail(50).mean()
    sma200 = close.tail(200).mean()
    high52 = close.max()
    vol_ratio = float(vol.iloc[-1] / max(vol.tail(20).mean(), 1))
    turnover_cr = float((close.tail(20) * vol.tail(20)).mean() / 1e7)  # real avg daily turnover

    # RSI (14)
    delta = close.diff()
    gain = delta.clip(lower=0).tail(14).mean()
    loss = (-delta.clip(upper=0)).tail(14).mean()
    rs = gain / loss if loss else 0
    rsi = 100 - (100 / (1 + rs)) if loss else 70

    info = {}
    try:
        info = ticker.info  # may be rate-limited; wrapped in try
    except Exception:
        info = {}

    demo = _demo_bundle(symbol)  # only used to fill fundamentals gaps (ROCE has no free source)
    bundle = {
        "symbol": symbol,
        "activity": {
            "symbol": symbol,
            "pct_change": round((last - prev) / prev * 100, 1),
            "volume_ratio": round(vol_ratio, 1),
            "has_block_deal": vol_ratio > 3,  # heuristic
        },
        "fundamentals": {
            "revenue_growth": round(info.get("revenueGrowth", 0) * 100, 1)
            if info.get("revenueGrowth") is not None else demo["fundamentals"]["revenue_growth"],
            "operating_margin": round(info.get("operatingMargins", 0) * 100, 1)
            if info.get("operatingMargins") is not None else demo["fundamentals"]["operating_margin"],
            "roce": demo["fundamentals"]["roce"],  # not in yfinance -> needs paid API
            "debt_to_equity": round(info.get("debtToEquity", 0) / 100, 2)
            if info.get("debtToEquity") is not None else demo["fundamentals"]["debt_to_equity"],
            "pe_ratio": round(info.get("trailingPE", demo["fundamentals"]["pe_ratio"]), 1),
        },
        "technicals": {
            "above_sma50": bool(last > sma50),
            "above_sma200": bool(last > sma200),
            "rsi": round(float(rsi)),
            "near_high": bool(last > high52 * 0.95),
            "volume_ratio": round(vol_ratio, 1),
        },
        "headlines": _real_headlines(symbol),
        "risk_inputs": {
            "liquidity_cr": round(turnover_cr, 1),
        },
    }
    return bundle


# ----------------------------------------------------------------------
# PUBLIC API
# ----------------------------------------------------------------------
def get_bundles(symbols: list) -> dict:
    """Return {symbol: bundle} for every requested symbol."""
    mode = settings.data_mode
    out = {}
    for sym in symbols:
        out[sym] = _live_bundle(sym) if mode == "live" else _demo_bundle(sym)
    return out


def get_activity(bundles: dict) -> list:
    """Extract just the Research-Agent scan rows."""
    return [b["activity"] for b in bundles.values()]
