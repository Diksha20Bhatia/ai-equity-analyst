"""
test_orchestrator.py — the decide -> reflect -> route LangGraph loop.

Real behaviour under test: a clean Decision Agent output finishes in 0
retries; an output with a real, checkable defect (missing bear_case)
triggers exactly one bounded retry and then stops, even when the mocked
Orchestrator would keep asking for more — max_retries must never be
exceeded regardless of what the routing LLM says.

No real network/LLM calls — BaseAgent.ask_json is mocked directly on the
agent instances the graph was built with.
"""

from unittest.mock import patch

from agents.decision_agent import DecisionAgent
from agents.orchestrator_agent import OrchestratorAgent, build_tier_graph, _check_decision_quality


ANALYSES = [{
    "symbol": "RELIANCE", "sector": "Energy",
    "fundamental": {"score": 7, "verdict": "solid"},
    "technical": {"score": 6, "read": "uptrend"},
    "risk": {"risk_score": 4, "flags": []},
    "event": {"sentiment": "positive", "event_type": "earnings", "materiality": "medium", "summary": "beat"},
    "research_note": "anomaly",
    "market_stats": {"current_price": 2900},
    "data_quality": [],
}]
PROFILE = {"risk_appetite": "moderate", "capital_numeric": 100000, "max_position_pct": 20}
MARKET_CTX = {"nifty_pct_change": 0.5, "nifty_trend": "up", "nifty_volatility_pct": 12}

GOOD_PICK = {
    "symbol": "RELIANCE", "opportunity_score": 80, "confidence": 75, "action": "accumulate",
    "bull_case": "x", "bear_case": "y", "thesis": "z", "target_price": 3100,
    "target_price_rationale": "r", "near_term_outlook": "n", "portfolio_fit": "good",
    "portfolio_fit_note": "p", "invalidation": "i", "key_risk": "k",
}
BROKEN_PICK = {**GOOD_PICK, "bear_case": ""}  # missing a required field


def _initial_state(**overrides):
    state = {
        "tier": "moderate", "analyses": ANALYSES, "profile": PROFILE, "market_context": MARKET_CTX,
        "memory_deltas": [], "top_n": 3, "bull_analysis": {}, "bear_analysis": {},
        "decision_result": {}, "issues": [], "retries": 0, "max_retries": 1,
        "route_reason": "", "feedback": "", "next": "",
    }
    state.update(overrides)
    return state


def test_clean_output_finishes_with_zero_retries():
    decision = DecisionAgent()
    orchestrator = OrchestratorAgent()
    graph = build_tier_graph(decision, orchestrator)

    with patch.object(decision, "ask_json", return_value={"shortlist": [GOOD_PICK], "summary": "s"}):
        final = graph.invoke(_initial_state())

    assert final["retries"] == 0
    assert final["decision_result"]["shortlist"][0]["symbol"] == "RELIANCE"
    assert "no issues found" in final["route_reason"]


def test_broken_output_retries_exactly_once_then_stops():
    decision = DecisionAgent()
    orchestrator = OrchestratorAgent()
    graph = build_tier_graph(decision, orchestrator)

    route_decision = {
        "next": "retry_decision",
        "reason": "missing bear_case",
        "feedback_for_decision_agent": "add a bear_case",
    }
    with patch.object(decision, "ask_json", return_value={"shortlist": [BROKEN_PICK], "summary": "s"}), \
         patch.object(orchestrator, "ask_json", return_value=route_decision):
        final = graph.invoke(_initial_state(max_retries=1))

    # Bounded: max_retries=1 means AT MOST one retry, never more, even
    # though the mocked orchestrator would keep asking to retry forever.
    assert final["retries"] == 1
    assert "max_retries" in final["route_reason"]


def test_quality_check_flags_missing_required_field():
    issues = _check_decision_quality({"shortlist": [BROKEN_PICK]}, ANALYSES, top_n=3)
    assert any("bear_case" in i for i in issues)


def test_quality_check_flags_inconsistent_avoid_with_high_opportunity():
    inconsistent = {**GOOD_PICK, "action": "avoid", "opportunity_score": 85}
    issues = _check_decision_quality({"shortlist": [inconsistent]}, ANALYSES, top_n=3)
    assert any("inconsistent" in i for i in issues)


def test_quality_check_passes_clean_output():
    issues = _check_decision_quality({"shortlist": [GOOD_PICK]}, ANALYSES, top_n=3)
    assert issues == []


def test_quality_check_flags_empty_shortlist():
    issues = _check_decision_quality({"shortlist": []}, ANALYSES, top_n=3)
    assert issues == ["shortlist is empty"]


def test_bull_and_bear_analyses_reach_the_decision_agent_prompt():
    """The graph's whole point is that the Decision Agent adjudicates
    between two independent analyses it didn't produce — verify they
    actually reach its prompt, not just get threaded through state and
    silently dropped."""
    decision = DecisionAgent()
    orchestrator = OrchestratorAgent()
    graph = build_tier_graph(decision, orchestrator)

    bull = {"RELIANCE": {"bull_case": "strong refining margins", "bull_target_price": 3200, "bull_strength": 80}}
    bear = {"RELIANCE": {"bear_case": "crude price volatility", "key_risk": "margin compression", "bear_severity": 40}}

    captured_prompts = []

    def spy_ask_json(prompt, temperature=0.4):
        captured_prompts.append(prompt)
        return {"shortlist": [GOOD_PICK], "summary": "s"}

    with patch.object(decision, "ask_json", side_effect=spy_ask_json):
        graph.invoke(_initial_state(bull_analysis=bull, bear_analysis=bear))

    assert captured_prompts, "Decision Agent was never called"
    prompt = captured_prompts[0]
    assert "strong refining margins" in prompt
    assert "crude price volatility" in prompt
    assert "INDEPENDENT" in prompt
