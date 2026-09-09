"""
tradingview_quote.py  —  two real tools the Decision Agent calls DURING
its own reasoning, not a one-way push fired after a human approves
(that's alerts/tradingview_watchlist.py — same local CLI, different,
procedural use of it, fired once at the end of a run).

Two distinct primitives, over two genuinely different transports, both
via the local tradingview-mcp CLI
(https://github.com/tradesdontlie/tradingview-mcp) — no credentials
stored or sent anywhere:

  1. get_live_quote() — Chrome DevTools Protocol against a real running
     TradingView Desktop instance. Slow (measured ~20-22s/call, see
     below) but genuinely live: today's real intraday
     last/open/high/low/volume off your own logged-in session.
  2. verify_symbol() — TradingView's public symbol-search REST endpoint
     (`"source": "rest_api"` in its own reply — no Desktop connection
     needed at all). Fast (measured <1s/call): confirms a shortlisted
     ticker actually resolves to a real NSE equity before the model
     reasons about it, catching a mistyped/delisted/wrong-exchange
     symbol as a real data-quality signal rather than silently trusting
     whatever string upstream code produced.

agents/decision_agent.py calls both for every stock it's about to
adjudicate, before it forms its final view. Both results are shown to
the LLM alongside its other, slower-moving data — the live quote so it
can flag disagreement with a stale price, the symbol verification so it
can flag a shortlisted ticker TradingView itself can't confirm.

Best-effort and non-blocking throughout: if TradingView Desktop isn't
running, the CLI/node aren't available, or a call times out or fails,
both functions return None and the agent reasons on its existing data
alone — a missing tool result should never block or corrupt a run.

Real, measured latency (why these are separate settings/functions, not
one): get_live_quote() opens a fresh Chrome DevTools Protocol connection
per call (no persistent connection is reused) — live testing showed this
consistently takes ~20-22s per call. verify_symbol() hits a plain REST
endpoint and returned in well under a second in the same live testing.
The per-process cache below is what keeps get_live_quote() from
compounding across risk tiers within one run.py execution.

Malformed tool results are RETRIED where they're consumed, not just
validated and discarded: a CLI reply that fails its typed contract
(schemas.LiveQuoteResult / schemas.SymbolSearchResult) — a crash mid-CDP-
handshake, a transient empty REST body — gets one bounded retry (2
attempts total, never unbounded) before either function gives up and
returns None. Each individual attempt is logged to
output/tool_call_log.jsonl with its own `attempt` number, so a retry
that then succeeds is visible as two real records, not silently hidden
behind a single success.
"""

import json
import subprocess
import time

import observability
from config import settings
from schemas import LiveQuoteResult, SymbolSearchResult, ValidationError

# Per-process cache: a run's own picks don't need a second live read of
# the same stock a few seconds later (e.g. the same symbol showing up
# across more than one risk tier in a single run.py execution).
_cache: dict = {}
_search_cache: dict = {}


_MAX_ATTEMPTS = 2  # one real retry, never unbounded


def get_live_quote(symbol: str, agent: str = "Decision Agent") -> dict | None:
    """symbol: a real NSE ticker, e.g. "RELIANCE". Returns real
    {"last", "open", "high", "low", "volume"} off TradingView Desktop's
    own live feed (validated against schemas.LiveQuoteResult), or None
    if unavailable after every attempt — never raises. Retries once (see
    module docstring) if the first attempt fails or comes back malformed.
    Every real attempt (cache hits excluded — those aren't a new call) is
    logged to output/tool_call_log.jsonl via
    observability.record_tool_call(), success or failure, so a run's
    real tool-use footprint — including any retry — is visible after
    the fact."""
    if not settings.tradingview_quote_enabled:
        return None
    if symbol in _cache:
        return _cache[symbol]

    quote = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        quote = _attempt_live_quote(symbol, agent, attempt)
        if quote is not None:
            break

    _cache[symbol] = quote
    return quote


def _attempt_live_quote(symbol: str, agent: str, attempt: int) -> dict | None:
    quote = None
    started = time.monotonic()
    try:
        proc = subprocess.run(
            ["node", settings.tradingview_cli_path, "quote", f"NSE:{symbol}"],
            capture_output=True, text=True, timeout=45,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            if data.get("success") and data.get("last"):
                try:
                    quote = LiveQuoteResult.model_validate(data).model_dump()
                except ValidationError:
                    quote = None
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        quote = None

    observability.record_tool_call(
        tool="tradingview_live_quote", target=f"NSE:{symbol}", success=quote is not None,
        latency_ms=(time.monotonic() - started) * 1000, agent=agent, attempt=attempt,
    )
    return quote


def verify_symbol(symbol: str, agent: str = "Decision Agent") -> dict | None:
    """symbol: a real NSE ticker, e.g. "RELIANCE". Calls TradingView's
    symbol-search REST endpoint (a genuinely different transport from
    get_live_quote's CDP connection — no Desktop round-trip, typically
    <1s) and reports whether it resolves to a real NSE equity. Returns
    {"verified": bool, "description": str|None, "exchange": str|None} —
    validated against schemas.SymbolSearchResult — or None if the TOOL
    CALL itself failed after every attempt (CLI/node unavailable,
    timeout, malformed reply); None is "couldn't check", never treated
    as "verified". Retries once (see module docstring) before giving up."""
    if not settings.tradingview_symbol_search_enabled:
        return None
    if symbol in _search_cache:
        return _search_cache[symbol]

    result = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        result = _attempt_symbol_search(symbol, agent, attempt)
        if result is not None:
            break

    _search_cache[symbol] = result
    return result


def _attempt_symbol_search(symbol: str, agent: str, attempt: int) -> dict | None:
    result = None
    started = time.monotonic()
    try:
        proc = subprocess.run(
            ["node", settings.tradingview_cli_path, "search", symbol],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            if data.get("success"):
                match = next(
                    (r for r in data.get("results", [])
                     if r.get("symbol") == symbol and r.get("exchange") == "NSE" and r.get("type") == "stock"),
                    None,
                )
                try:
                    result = SymbolSearchResult.model_validate({
                        "verified": match is not None,
                        "description": (match or {}).get("description"),
                        "exchange": (match or {}).get("exchange"),
                    }).model_dump()
                except ValidationError:
                    result = None
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        result = None

    observability.record_tool_call(
        tool="tradingview_symbol_search", target=symbol, success=result is not None,
        latency_ms=(time.monotonic() - started) * 1000, agent=agent, attempt=attempt,
    )
    return result
