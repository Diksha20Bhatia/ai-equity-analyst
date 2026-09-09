"""
test_mcp_pipeline_client.py — a real MCP client/server round-trip, all
three server primitives, over real stdio transport.

Unlike every other "MCP" reference in this project before this file
(Bodhi's own bodhi-deep-research, reached over HTTP), this test suite
exercises a REAL, LOCAL MCP server (integrations/mcp_server.py) via a
REAL MCP client (integrations/mcp_pipeline_client.py) — actual JSON-RPC
messages over actual stdin/stdout pipes to a real subprocess. Nothing
here is mocked: these are genuine integration tests, deterministic and
fast enough to run in CI because the server is fully local (no network
call beyond data/nse_universe.py's own cached NSE fetch, no TradingView
Desktop dependency, no Bodhi credentials).
"""

import pytest

from config import settings
from integrations.mcp_pipeline_client import (
    get_risk_review_prompt_via_mcp,
    get_sector_notes_via_mcp,
    verify_symbol_via_mcp,
)


@pytest.fixture(autouse=True)
def _enable_real_mcp_calls_for_this_file(monkeypatch):
    """conftest.py's autouse fixture disables MCP_VERIFICATION_ENABLED
    for the whole suite (a real subprocess per call, ~2s each) — this
    file explicitly re-enables it, the same pattern
    tests/test_tradingview_quote.py uses for its own real calls."""
    monkeypatch.setattr(settings, "mcp_verification_enabled", True)


def test_real_tool_call_confirms_a_real_nifty50_constituent():
    result = verify_symbol_via_mcp("RELIANCE")
    assert result is not None
    assert result["symbol"] == "RELIANCE"
    assert result["verified"] is True
    assert result["universe_size"] > 0


def test_real_tool_call_flags_a_symbol_that_is_not_a_constituent():
    result = verify_symbol_via_mcp("FAKESTOCKXYZ")
    assert result is not None
    assert result["verified"] is False


def test_real_tool_call_repairs_a_decorated_input_before_the_round_trip():
    """The input contract (schemas.McpVerifySymbolInput) is really wired
    into the live call path, not just tested in isolation: a decorated
    symbol still resolves correctly end to end."""
    result = verify_symbol_via_mcp("nse:reliance")
    assert result is not None
    assert result["symbol"] == "RELIANCE"
    assert result["verified"] is True


def test_real_tool_call_refuses_an_unusable_input_without_a_round_trip():
    assert verify_symbol_via_mcp("@@@---") is None


def test_real_resource_read_returns_this_projects_own_sector_notes():
    notes = get_sector_notes_via_mcp("IT")
    assert notes is not None
    assert "USD/INR" in notes  # the real IT sector doc's actual real content


def test_real_resource_read_handles_an_unknown_sector_without_raising():
    notes = get_sector_notes_via_mcp("NotARealSector")
    assert notes is not None
    assert "No sector notes" in notes


def test_real_prompt_fetch_returns_the_parameterized_template():
    prompt = get_risk_review_prompt_via_mcp("TCS", "IT")
    assert prompt is not None
    assert "TCS" in prompt
    assert "IT sector" in prompt


def test_stdout_is_not_corrupted_by_an_imported_modules_print_statements():
    """Regression test: data/nse_universe.py prints real status lines to
    stdout for normal CLI/run.py use, which would corrupt the stdio MCP
    protocol channel (stdout IS the protocol channel under stdio
    transport) unless the server redirects it during the call — see
    integrations/mcp_server.py::_stdout_reserved_for_mcp_protocol(). If
    that redirect ever regresses, the JSON-RPC stream corrupts and this
    returns None instead of a real result."""
    assert verify_symbol_via_mcp("HDFCBANK") is not None
