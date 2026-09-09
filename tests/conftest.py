"""
conftest.py — test isolation shared across the whole suite.

Two autouse fixtures:
  - isolate_observability_logs: redirects every observability JSONL log
    (failures, LLM call telemetry, orchestrator/tool-call decisions) to a
    throwaway temp directory for every test, so running the suite never
    writes to the real output/ tree — several tests exercise real code
    paths (route_node, text_safety) that call observability functions as
    a real side effect, not a mock.
  - disable_real_tradingview_calls: forces both TradingView tool-call
    flags off for every test, regardless of what a developer's real
    .env has set. Without this, any test that exercises
    agents/decision_agent.py's real _build_prompt() (most of
    tests/test_orchestrator.py and the full-pipeline scenario tests)
    would make a REAL subprocess call to the tradingview-mcp CLI — slow
    (~20s for a live quote) and dependent on TradingView Desktop
    actually being open, exactly the kind of non-deterministic external
    dependency this suite otherwise refuses to have. Tests that want to
    exercise the tool call for real (tests/test_tradingview_quote.py)
    explicitly re-enable it with their own monkeypatch, which safely
    overrides this fixture for just that test. Also disables
    MCP_VERIFICATION_ENABLED for the same reason (a real subprocess per
    call, ~2s each) — tests/test_mcp_pipeline_client.py re-enables it
    explicitly, the same pattern.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

import observability
from config import settings


@pytest.fixture(autouse=True)
def isolate_observability_logs(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(observability, "_FAILURE_LOG", tmp / "reliability_log.jsonl")
    monkeypatch.setattr(observability, "_CALL_LOG", tmp / "llm_call_log.jsonl")
    monkeypatch.setattr(observability, "_DECISION_LOG", tmp / "orchestrator_decisions.jsonl")
    monkeypatch.setattr(observability, "_TOOL_CALL_LOG", tmp / "tool_call_log.jsonl")
    yield
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(autouse=True)
def disable_real_tradingview_calls(monkeypatch):
    monkeypatch.setattr(settings, "tradingview_quote_enabled", False)
    monkeypatch.setattr(settings, "tradingview_symbol_search_enabled", False)
    monkeypatch.setattr(settings, "mcp_verification_enabled", False)
