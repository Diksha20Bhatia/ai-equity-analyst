"""
orchestrator_agent.py  —  a real, dynamic multi-agent controller
==================================================================
Everywhere else in agents/, control flow is a fixed Python sequence:
run.py calls Research, then Fundamental/Technical/Risk, then Sentiment,
then Decision, in that hardcoded order, every time. That's the right
choice for those steps — they're a genuine linear pipeline, each one
strictly needs the previous one's output, and there's nothing to route
between.

The Decision Agent's output is different: it's a judgment call that can
be wrong in checkable ways (a missing field, an internally inconsistent
stance, confidence that ignores a flagged data-quality issue), and there
IS a real choice to make about what happens next — accept it, or send it
back with specific feedback for another attempt. That's genuine agent
routing, not a fixed sequence, so it's built as an actual LangGraph
StateGraph with a conditional edge, not a Python if/else:

    decide (Decision Agent) -> reflect (deterministic quality check)
        -> route (Orchestrator Agent, an LLM call, decides: retry or finish)
              -> [conditional edge] back to decide, or END

The quality CHECK is plain code — "is a required field missing" has one
right answer, same philosophy as every non-AI agent in this codebase. The
quality DECISION — is this issue worth a retry, and what should the
Decision Agent specifically fix — is a judgment call, so that part is a
real LLM call. Retries are hard-capped (max_retries) regardless of what
the LLM says, so a stuck loop can never run away.
"""

from typing import TypedDict

from langgraph.graph import StateGraph, END

from agents.base_agent import BaseAgent
from observability import traceable


class OrchestratorAgent(BaseAgent):
    name = "Orchestrator"
    system_instruction = (
        "You are the routing controller for a multi-agent equity analysis pipeline. "
        "You are handed a quality-check report on the Decision Agent's latest output "
        "and you decide what happens next: send it back for one more attempt with "
        "specific, actionable feedback, or accept it as final. Only ask for a retry "
        "when the issues are real and fixable by reasoning better over the SAME real "
        "data already given to the Decision Agent — never ask it to invent new data, "
        "and never ask for a retry over a cosmetic/trivial issue."
    )

    @traceable(run_type="chain", name="Orchestrator — route decision")
    def route(self, issues: list, retries_used: int, max_retries: int) -> dict:
        issues_txt = "\n".join(f"- {i}" for i in issues) if issues else "(none — output passed all checks)"
        prompt = (
            f"Quality-check issues found in the Decision Agent's most recent output "
            f"(attempt {retries_used + 1}, {max_retries - retries_used} retry(ies) remaining):\n"
            f"{issues_txt}\n\n"
            'Reply as JSON: {"next": "retry_decision" or "finish", '
            '"reason": "one sentence explaining your call", '
            '"feedback_for_decision_agent": "specific instruction to fix the issues — '
            'empty string if finishing"}'
        )
        return self.ask_json(prompt, temperature=0.1)


class TierState(TypedDict):
    analyses: list
    profile: dict
    market_context: dict
    memory_deltas: list
    top_n: int
    decision_result: dict
    issues: list
    retries: int
    max_retries: int
    route_reason: str
    feedback: str
    next: str


def _check_decision_quality(result: dict, analyses: list, top_n: int) -> list:
    """
    Deterministic self-check on the Decision Agent's own output — the
    "Self Reflection" step. Every check here has one objectively correct
    answer (a required field is present or it isn't), same philosophy as
    every non-AI agent in this codebase: never use an LLM where a formula
    can answer the question.
    """
    issues = []
    shortlist = (result or {}).get("shortlist") or []
    if not shortlist:
        issues.append("shortlist is empty")
        return issues

    if len(shortlist) > top_n:
        issues.append(f"shortlist has {len(shortlist)} picks, more than the requested top_n={top_n}")

    quality_flags = {a["symbol"]: a.get("data_quality") for a in analyses}
    for pick in shortlist:
        sym = pick.get("symbol", "?")
        for field in ("bull_case", "bear_case", "target_price", "action"):
            if not pick.get(field) and pick.get(field) != 0:
                issues.append(f"{sym}: missing '{field}'")

        conf = pick.get("confidence")
        if conf is not None and not (0 <= conf <= 100):
            issues.append(f"{sym}: confidence {conf} is out of the 0-100 range")

        opp = pick.get("opportunity_score")
        if opp is not None and not (0 <= opp <= 100):
            issues.append(f"{sym}: opportunity_score {opp} is out of the 0-100 range")

        if pick.get("action") == "avoid" and (opp or 0) >= 70:
            issues.append(f"{sym}: action is 'avoid' but opportunity_score is {opp} — internally inconsistent")

        if quality_flags.get(sym) and (conf or 0) >= 80:
            issues.append(
                f"{sym}: data quality issues were flagged ({', '.join(quality_flags[sym])}) "
                f"but confidence is still {conf} — should be lower"
            )
    return issues


def build_tier_graph(decision_agent, orchestrator_agent):
    """
    Compiles the decide -> reflect -> route loop into a real LangGraph
    state machine. Called once per risk-appetite tier from run.py, since
    each tier gets its own independently-reasoned shortlist.
    """

    def decide_node(state: TierState) -> dict:
        result = decision_agent.run(
            state["analyses"], state["profile"], state["market_context"],
            state["memory_deltas"], top_n=state["top_n"], feedback=state.get("feedback", ""),
        )
        return {"decision_result": result}

    def reflect_node(state: TierState) -> dict:
        issues = _check_decision_quality(state["decision_result"], state["analyses"], state["top_n"])
        return {"issues": issues}

    def route_node(state: TierState) -> dict:
        retries = state["retries"]
        if not state["issues"]:
            return {"next": "finish", "route_reason": "quality check passed — no issues found", "feedback": ""}
        if retries >= state["max_retries"]:
            return {
                "next": "finish",
                "route_reason": f"{len(state['issues'])} issue(s) remain but max_retries "
                                f"({state['max_retries']}) reached — accepting output as-is",
                "feedback": "",
            }
        decision = orchestrator_agent.route(state["issues"], retries, state["max_retries"])
        if decision.get("next") == "retry_decision":
            return {
                "next": "retry_decision",
                "route_reason": decision.get("reason", ""),
                "feedback": decision.get("feedback_for_decision_agent", ""),
                "retries": retries + 1,
            }
        return {"next": "finish", "route_reason": decision.get("reason", ""), "feedback": ""}

    def should_retry(state: TierState) -> str:
        return "decide" if state.get("next") == "retry_decision" else "end"

    graph = StateGraph(TierState)
    graph.add_node("decide", decide_node)
    graph.add_node("reflect", reflect_node)
    graph.add_node("route", route_node)
    graph.set_entry_point("decide")
    graph.add_edge("decide", "reflect")
    graph.add_edge("reflect", "route")
    graph.add_conditional_edges("route", should_retry, {"decide": "decide", "end": END})
    return graph.compile()
