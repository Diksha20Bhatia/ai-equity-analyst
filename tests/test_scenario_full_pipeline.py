"""
test_scenario_full_pipeline.py — a real end-to-end MULTI-AGENT scenario,
not isolated unit checks: the actual Bull/Bear parallel fan-out
(agents/research_graph.py) feeding its real output into the actual
decide -> reflect -> route retry loop (agents/orchestrator_agent.py),
exactly the sequence run.py wires together for a real swing run.

This is the concrete evidence for "genuine multi-agent coordination,
not just isolated node tests" — two independent agents run in real
LangGraph parallel superstep, their real (mocked-LLM, real-graph)
outputs reach a third agent that adjudicates them, and a self-correcting
retry loop reacts to a real quality defect the same way it would on a
real run. No real network/LLM calls anywhere — only BaseAgent.ask_json
is mocked, never the graphs, the routing logic, or the typed validation
around them.
"""

from unittest.mock import patch

from agents.bear_case_agent import BearCaseAgent
from agents.bull_case_agent import BullCaseAgent
from agents.decision_agent import DecisionAgent
from agents.orchestrator_agent import OrchestratorAgent, build_tier_graph
from agents.research_graph import run_parallel_research

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

BULL_REPLY = {"RELIANCE": {"bull_case": "strong refining margins", "bull_target_price": 3200, "bull_strength": 80}}
BEAR_REPLY = {"RELIANCE": {"bear_case": "crude price volatility", "key_risk": "margin compression", "bear_severity": 40}}

GOOD_PICK = {
    "symbol": "RELIANCE", "opportunity_score": 78, "confidence": 72, "action": "accumulate",
    "bull_case": "adjudicated bull view citing strong refining margins", "bear_case": "adjudicated bear view citing crude volatility",
    "thesis": "z", "target_price": 3100, "target_price_rationale": "r", "near_term_outlook": "n",
    "portfolio_fit": "good", "portfolio_fit_note": "p", "invalidation": "i", "key_risk": "k",
}
BROKEN_PICK = {**GOOD_PICK, "bear_case": ""}  # fails schemas.DecisionPick (min_length=1)


def test_full_pipeline_bull_bear_fanout_feeds_a_real_retry_loop():
    """Stage 1: the real research_graph runs Bull + Bear in a genuine
    LangGraph parallel superstep. Stage 2: their real output is threaded
    into the real decide -> reflect -> route loop, which hits a real
    typed-shape defect on the first attempt (schemas.DecisionPick
    rejects an empty bear_case), retries exactly once via the mocked
    Orchestrator's routing call, and finishes clean on the second
    attempt — proving the two stages are actually wired together, not
    just individually correct."""
    with patch.object(BullCaseAgent, "ask_json", return_value=BULL_REPLY), \
         patch.object(BearCaseAgent, "ask_json", return_value=BEAR_REPLY):
        bull_analysis, bear_analysis = run_parallel_research(ANALYSES)

    assert bull_analysis["RELIANCE"]["bull_case"] == "strong refining margins"
    assert bear_analysis["RELIANCE"]["bear_case"] == "crude price volatility"

    decision = DecisionAgent()
    orchestrator = OrchestratorAgent()
    graph = build_tier_graph(decision, orchestrator)

    captured_prompts = []
    replies = iter([{"shortlist": [BROKEN_PICK], "summary": "s"}, {"shortlist": [GOOD_PICK], "summary": "s"}])

    def spy_ask_json(prompt, temperature=0.4):
        captured_prompts.append(prompt)
        return next(replies)

    route_decision = {
        "next": "retry_decision", "reason": "shape: shortlist.0.bear_case — too short",
        "feedback_for_decision_agent": "fill in bear_case",
    }
    with patch.object(decision, "ask_json", side_effect=spy_ask_json), \
         patch.object(orchestrator, "ask_json", return_value=route_decision):
        final = graph.invoke({
            "tier": "moderate", "analyses": ANALYSES, "profile": PROFILE, "market_context": MARKET_CTX,
            "memory_deltas": [], "top_n": 3, "bull_analysis": bull_analysis, "bear_analysis": bear_analysis,
            "decision_result": {}, "issues": [], "retries": 0, "max_retries": 2,
            "route_reason": "", "feedback": "", "next": "",
        })

    # The real defect was found and fixed within the bounded retry loop.
    assert final["retries"] == 1
    assert final["decision_result"]["shortlist"][0]["bear_case"] != ""
    # Both independent analyses genuinely reached the Decision Agent's own
    # prompt — not dropped anywhere across the two-stage handoff.
    assert "strong refining margins" in captured_prompts[0]
    assert "crude price volatility" in captured_prompts[0]
    assert "ADJUDICATE" in captured_prompts[0]


def test_full_pipeline_finishes_with_zero_retries_when_first_attempt_is_clean():
    with patch.object(BullCaseAgent, "ask_json", return_value=BULL_REPLY), \
         patch.object(BearCaseAgent, "ask_json", return_value=BEAR_REPLY):
        bull_analysis, bear_analysis = run_parallel_research(ANALYSES)

    decision = DecisionAgent()
    orchestrator = OrchestratorAgent()
    graph = build_tier_graph(decision, orchestrator)

    with patch.object(decision, "ask_json", return_value={"shortlist": [GOOD_PICK], "summary": "s"}):
        final = graph.invoke({
            "tier": "moderate", "analyses": ANALYSES, "profile": PROFILE, "market_context": MARKET_CTX,
            "memory_deltas": [], "top_n": 3, "bull_analysis": bull_analysis, "bear_analysis": bear_analysis,
            "decision_result": {}, "issues": [], "retries": 0, "max_retries": 2,
            "route_reason": "", "feedback": "", "next": "",
        })

    assert final["retries"] == 0
    assert "no issues found" in final["route_reason"]
