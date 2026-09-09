"""
test_tradingview_quote.py — the two real tools the Decision Agent calls
mid-reasoning (agents/decision_agent.py): a live TradingView Desktop
quote (CDP) and a symbol-search verification (REST), both via the local
tradingview-mcp CLI. Never a live TradingView call in tests —
subprocess.run is mocked throughout.
"""

import json
import subprocess

from integrations import tradingview_quote
from integrations.tradingview_quote import get_live_quote, verify_symbol


def test_disabled_returns_none_without_touching_subprocess(monkeypatch):
    monkeypatch.setattr(tradingview_quote.settings, "tradingview_quote_enabled", False)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("subprocess.run should not be called")),
    )
    tradingview_quote._cache.clear()
    assert get_live_quote("RELIANCE") is None


def test_enabled_parses_a_successful_quote(monkeypatch):
    monkeypatch.setattr(tradingview_quote.settings, "tradingview_quote_enabled", True)
    tradingview_quote._cache.clear()

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout='{"success": true, "last": 1292, "open": 1293.3, "high": 1293.5, '
                   '"low": 1288.6, "volume": 1749756}',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    quote = get_live_quote("RELIANCE")
    assert quote == {"last": 1292, "open": 1293.3, "high": 1293.5, "low": 1288.6, "volume": 1749756}
    assert "NSE:RELIANCE" in calls[0]

    # Second call for the same symbol is served from the in-process cache —
    # no second real subprocess call for a stock already priced this run.
    get_live_quote("RELIANCE")
    assert len(calls) == 1


def test_a_failed_first_attempt_is_retried_and_can_still_succeed(monkeypatch):
    """Real repair-or-retry, not just validate-then-drop: a malformed
    first reply gets one bounded retry before get_live_quote gives up."""
    monkeypatch.setattr(tradingview_quote.settings, "tradingview_quote_enabled", True)
    tradingview_quote._cache.clear()

    responses = iter([
        subprocess.CompletedProcess(["node"], 0, stdout="not json", stderr=""),  # attempt 1: malformed
        subprocess.CompletedProcess(["node"], 0, stdout=json.dumps({
            "success": True, "last": 1292, "open": 1293.3, "high": 1293.5, "low": 1288.6, "volume": 100,
        }), stderr=""),  # attempt 2: succeeds
    ])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: next(responses))

    quote = get_live_quote("RELIANCE")
    assert quote["last"] == 1292


def test_two_failed_attempts_give_up_and_return_none(monkeypatch):
    monkeypatch.setattr(tradingview_quote.settings, "tradingview_quote_enabled", True)
    tradingview_quote._cache.clear()
    calls = []

    def fake_run(*a, **k):
        calls.append(1)
        return subprocess.CompletedProcess(["node"], 0, stdout="not json", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert get_live_quote("RELIANCE") is None
    assert len(calls) == 2  # exactly the bounded retry, never more


def test_cli_failure_returns_none_not_an_exception(monkeypatch):
    monkeypatch.setattr(tradingview_quote.settings, "tradingview_quote_enabled", True)
    tradingview_quote._cache.clear()
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("node not found")),
    )
    assert get_live_quote("TCS") is None


def test_handles_empty_or_malformed_output(monkeypatch):
    monkeypatch.setattr(tradingview_quote.settings, "tradingview_quote_enabled", True)
    tradingview_quote._cache.clear()
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="not json", stderr=""),
    )
    assert get_live_quote("INFY") is None


# ------------------------------------------------------------------
# verify_symbol() — the second, distinct tool call: fast REST symbol
# search, not the slow CDP live-quote connection above.
# ------------------------------------------------------------------

def test_symbol_search_disabled_returns_none_without_touching_subprocess(monkeypatch):
    monkeypatch.setattr(tradingview_quote.settings, "tradingview_symbol_search_enabled", False)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("subprocess.run should not be called")),
    )
    tradingview_quote._search_cache.clear()
    assert verify_symbol("RELIANCE") is None


def test_symbol_search_confirms_a_real_nse_match(monkeypatch):
    monkeypatch.setattr(tradingview_quote.settings, "tradingview_symbol_search_enabled", True)
    tradingview_quote._search_cache.clear()

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({
            "success": True,
            "results": [
                {"symbol": "RELIANCE", "description": "Reliance Industries Limited",
                 "exchange": "NSE", "type": "stock"},
                {"symbol": "RELIANCE", "description": "RELIANCE INDS FUTURES",
                 "exchange": "NSE", "type": "futures"},
            ],
        }), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = verify_symbol("RELIANCE")
    assert result == {"verified": True, "description": "Reliance Industries Limited", "exchange": "NSE"}


def test_symbol_search_flags_an_unresolved_symbol(monkeypatch):
    monkeypatch.setattr(tradingview_quote.settings, "tradingview_symbol_search_enabled", True)
    tradingview_quote._search_cache.clear()
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout=json.dumps({
            "success": True, "results": [],
        }), stderr=""),
    )
    result = verify_symbol("FAKESYMBOLXYZ")
    assert result == {"verified": False, "description": None, "exchange": None}


def test_symbol_search_cli_failure_returns_none(monkeypatch):
    monkeypatch.setattr(tradingview_quote.settings, "tradingview_symbol_search_enabled", True)
    tradingview_quote._search_cache.clear()
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("node not found")),
    )
    assert verify_symbol("TCS") is None
