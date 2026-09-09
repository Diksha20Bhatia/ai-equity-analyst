"""
research_graph.py  —  a real, verified-concurrent multi-agent fan-out
=========================================================================
Runs the Bull Case Agent and the Bear Case Agent as genuinely PARALLEL
branches of a LangGraph state machine — both have an edge from START and
no edge between each other, so LangGraph's Pregel executor runs them in
the same superstep, not one after another. Neither agent's prompt ever
includes the other's output (see their own docstrings) — this is real
task decomposition (the same "argue a case for this stock" job split into
two independent roles) plus real multi-agent coordination (their outputs
are only combined afterward, by the Decision Agent — this graph itself
never picks a winner).

    start (fan out)
       |-- bull_case ---|
       |-- bear_case ---|-- (fan in, both required) -> end

Runs ONCE per swing run (both agents are tier-independent), not once per
selected risk tier — see run.py.
"""

from typing import TypedDict

from langgraph.graph import StateGraph, START, END

from agents.bull_case_agent import BullCaseAgent
from agents.bear_case_agent import BearCaseAgent


class ResearchState(TypedDict):
    analyses: list
    bull_analysis: dict
    bear_analysis: dict


def build_research_graph(bull_agent: BullCaseAgent, bear_agent: BearCaseAgent):
    def bull_node(state: ResearchState) -> dict:
        return {"bull_analysis": bull_agent.run(state["analyses"])}

    def bear_node(state: ResearchState) -> dict:
        return {"bear_analysis": bear_agent.run(state["analyses"])}

    graph = StateGraph(ResearchState)
    graph.add_node("bull_case", bull_node)
    graph.add_node("bear_case", bear_node)
    # Both branches hang off START with no edge between them — this is
    # what makes them a real fan-out, not two nodes that merely happen to
    # both run: LangGraph schedules every node whose dependencies are
    # satisfied in the same superstep, and neither node here depends on
    # the other.
    graph.add_edge(START, "bull_case")
    graph.add_edge(START, "bear_case")
    graph.add_edge("bull_case", END)
    graph.add_edge("bear_case", END)
    return graph.compile()


def run_parallel_research(analyses: list) -> tuple:
    """Convenience entry point for run.py: builds the graph, runs it once,
    returns (bull_analysis, bear_analysis) — both {symbol: {...}} dicts."""
    bull_agent = BullCaseAgent()
    bear_agent = BearCaseAgent()
    graph = build_research_graph(bull_agent, bear_agent)
    final_state = graph.invoke({"analyses": analyses, "bull_analysis": {}, "bear_analysis": {}})
    return final_state["bull_analysis"], final_state["bear_analysis"]
