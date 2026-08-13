"""
nse_universe.py  —  Nifty 50 / Nifty 500 constituent lists
============================================================
Set UNIVERSE=NIFTY50 or UNIVERSE=NIFTY500 in .env to scan the whole index
instead of a hand-picked list. Constituents are pulled straight from NSE's
official index CSVs, so the list always matches the current index
composition — no stale hardcoded basket to maintain. A successful fetch is
cached locally so reruns (and offline runs) keep working afterwards.
"""

import csv
import io
import json
import time
from pathlib import Path

import requests

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_MAX_AGE_SECS = 24 * 60 * 60  # refresh once a day

INDEX_URLS = {
    "NIFTY50": "https://archives.nseindia.com/content/indices/ind_nifty50list.csv",
    "NIFTY500": "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
}

# Last-resort list, only used if NSE can't be reached AND no cache exists yet.
# The network fetch above is the source of truth; this can drift over time.
FALLBACK_NIFTY50 = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO",
    "HINDALCO", "HINDUNILVR", "ICICIBANK", "ITC", "INDUSINDBK",
    "INFY", "JSWSTEEL", "JIOFIN", "KOTAKBANK", "LT",
    "M&M", "MARUTI", "NTPC", "NESTLEIND", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SHRIRAMFIN", "SBIN",
    "SUNPHARMA", "TCS", "TATACONSUM", "TATAMOTORS", "TATASTEEL",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def _cache_file(index_name: str) -> Path:
    return CACHE_DIR / f"{index_name.lower()}.json"


def _load_cache(index_name: str) -> list:
    path = _cache_file(index_name)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text()).get("symbols", [])
    except Exception:
        return []


def _save_cache(index_name: str, symbols: list) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    _cache_file(index_name).write_text(
        json.dumps({"fetched_at": time.time(), "symbols": symbols})
    )


def _cache_is_fresh(index_name: str) -> bool:
    path = _cache_file(index_name)
    if not path.exists():
        return False
    try:
        fetched_at = json.loads(path.read_text()).get("fetched_at", 0)
        return (time.time() - fetched_at) < CACHE_MAX_AGE_SECS
    except Exception:
        return False


def _fetch_from_nse(index_name: str) -> list:
    resp = requests.get(INDEX_URLS[index_name], headers=_HEADERS, timeout=15)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    return [row["Symbol"].strip().upper() for row in reader if row.get("Symbol")]


def fetch_index_symbols(index_name: str) -> list:
    """Return the constituent symbols for NIFTY50 / NIFTY500."""
    index_name = index_name.upper()
    if _cache_is_fresh(index_name):
        cached = _load_cache(index_name)
        if cached:
            return cached

    try:
        symbols = _fetch_from_nse(index_name)
        _save_cache(index_name, symbols)
        print(f"[universe] fetched {len(symbols)} {index_name} symbols from NSE")
        return symbols
    except Exception as e:
        cached = _load_cache(index_name)
        if cached:
            print(f"[universe] NSE fetch failed ({e}); using cached {index_name} list")
            return cached
        if index_name == "NIFTY50":
            print(f"[universe] NSE fetch failed ({e}); using bundled NIFTY50 fallback")
            return FALLBACK_NIFTY50
        raise RuntimeError(
            f"Could not fetch {index_name} constituents from NSE and no cached "
            "copy exists yet. Check your internet connection, or set UNIVERSE "
            "to an explicit comma-separated symbol list instead."
        ) from e


def resolve_universe(raw: str) -> list:
    """Turn the UNIVERSE env value into a list of NSE symbols."""
    key = raw.strip().upper().replace(" ", "")
    if key in INDEX_URLS:
        return fetch_index_symbols(key)
    return [s.strip().upper() for s in raw.split(",") if s.strip()]
