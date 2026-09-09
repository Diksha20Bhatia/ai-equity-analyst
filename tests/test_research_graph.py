"""
test_research_graph.py — the Bull Case / Bear Case parallel fan-out.

Real behaviour under test: the two agents actually run CONCURRENTLY, not
just in a structure that looks parallel on paper — proven by timing two
mocked calls that each sleep 1s and asserting the graph's wall-clock time
is close to 1s (max), not 2s (sum). Also verifies neither agent's prompt
ever contains the other's output — the independence the whole design
depends on.
"""

import time
from unittest.mock import patch

from agents.bull_case_agent import BullCaseAgent
from agents.bear_case_agent import BearCaseAgent
from agents.research_graph import build_research_graph, run_parallel_research


ANALYSES = [{
    "symbol": "RELIANCE", "sector": "Energy",
    "fundamental": {"score": 7, "verdict": "solid"},
    "technical": {"score": 6, "read": "uptrend"},
    "risk": {"risk_score": 4, "flags": []},
    "event": {"sentiment": "positive", "materiality": "medium", "summary": "beat"},
    "research_note": "anomaly",
    "market_stats": {"current_price": 2900},
    "data_quality": [],
}]

BULL_RESULT = {"RELIANCE": {"bull_case": "strong momentum", "bull_target_price": 3100, "bull_strength": 75}}
BEAR_RESULT = {"RELIANCE": {"bear_case": "crude risk", "key_risk": "margin compression", "bear_severity": 35}}


def test_bull_and_bear_agents_run_genuinely_concurrently():
    bull_agent = BullCaseAgent()
    bear_agent = BearCaseAgent()
    graph = build_research_graph(bull_agent, bear_agent)

    def slow_bull(prompt, temperature=0.4):
        time.sleep(0.6)
        return BULL_RESULT

    def slow_bear(prompt, temperature=0.4):
        time.sleep(0.6)
        return BEAR_RESULT

    with patch.object(bull_agent, "ask_json", side_effect=slow_bull), \
         patch.object(bear_agent, "ask_json", side_effect=slow_bear):
        started = time.monotonic()
        final = graph.invoke({"analyses": ANALYSES, "bull_analysis": {}, "bear_analysis": {}})
        elapsed = time.monotonic() - started

    # Two independent 0.6s calls: sequential would take ~1.2s, genuinely
    # parallel takes ~0.6s. 1.0s is a safe cutoff with real margin either way.
    assert elapsed < 1.0, f"took {elapsed:.2f}s — looks sequential, not parallel"
    # Both agents now validate their reply against a typed schema
    # (schemas.BullCaseItem/BearCaseItem) before returning it, which fills
    # in optional fields (e.g. bear_target_price: None) and normalises
    # numeric types — so these compare specific fields, not full dict
    # equality against the raw mocked payload.
    assert final["bull_analysis"]["RELIANCE"]["bull_case"] == "strong momentum"
    assert final["bull_analysis"]["RELIANCE"]["bull_strength"] == 75
    assert final["bear_analysis"]["RELIANCE"]["bear_case"] == "crude risk"
    assert final["bear_analysis"]["RELIANCE"]["bear_severity"] == 35


def test_neither_agent_prompt_contains_the_others_output():
    bull_agent = BullCaseAgent()
    bear_agent = BearCaseAgent()

    # Neither prompt-builder even accepts the other agent's output as an
    # argument — the independence is structural, not just a convention —
    # so there's nothing for the other side's vocabulary to leak in from.
    bull_prompt = bull_agent._build_prompt(ANALYSES)
    bear_prompt = bear_agent._build_prompt(ANALYSES)

    assert "bear case" not in bull_prompt.lower()
    assert "bull case" not in bear_prompt.lower()


def test_run_parallel_research_returns_dicts_keyed_by_symbol():
    with patch.object(BullCaseAgent, "ask_json", return_value=BULL_RESULT), \
         patch.object(BearCaseAgent, "ask_json", return_value=BEAR_RESULT):
        bull_analysis, bear_analysis = run_parallel_research(ANALYSES)

    assert bull_analysis["RELIANCE"]["bull_strength"] == 75
    assert bear_analysis["RELIANCE"]["bear_severity"] == 35


def test_missing_symbol_in_agent_response_raises():
    bull_agent = BullCaseAgent()
    with patch.object(bull_agent, "ask_json", return_value={}):
        try:
            bull_agent.run(ANALYSES)
            assert False, "expected a RuntimeError for a missing symbol"
        except RuntimeError as e:
            assert "RELIANCE" in str(e)
