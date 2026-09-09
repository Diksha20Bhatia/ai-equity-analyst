"""
test_bodhi_client.py — extract_bodhi_answer() against real response shapes.

Regression tests built from actual raw Bodhi API responses seen during
this project's live testing — the exec_metadata.allOutputs path (with an
explicit End node, and the agent-node fallback when there's no End node),
plus the earlier bug this function was fixed for: a raw response with a
node's stored, UNRESOLVED template config sitting alongside the real
result must never be picked up as if it were the answer.
"""

import pytest

from integrations.bodhi_client import _node_progress_summary, extract_bodhi_answer


def test_prefers_the_end_node_result_when_present():
    result = {
        "exec_metadata": {
            "allOutputs": {
                "run-1": {
                    "Risk Analyst Agent": {"data": {"result": {"response": "draft commentary"}}},
                    "Contrarian Agent": {"data": {"result": {"response": "verdicts json here"}}},
                    "End": {"data": {"result": "verdicts json here"}},
                }
            }
        }
    }
    assert extract_bodhi_answer(result) == "verdicts json here"


def test_falls_back_to_last_agent_node_when_no_end_node():
    result = {
        "exec_metadata": {
            "allOutputs": {
                "run-1": {
                    "Risk Analyst Agent": {"data": {"result": {"response": "first agent's answer"}}},
                    "Contrarian Agent": {"data": {"result": {"response": "final agent's answer"}}},
                }
            }
        }
    }
    assert extract_bodhi_answer(result) == "final agent's answer"


def test_ignores_unresolved_template_text_outside_exec_metadata():
    # The exact bug this function was fixed for: a node's raw, unresolved
    # config text (e.g. a template variable that was never substituted)
    # sitting at the top level of the response must never be mistaken for
    # the real answer once exec_metadata.allOutputs has a real one.
    result = {
        "exec_metadata": {
            "allOutputs": {
                "run-1": {
                    "End": {"data": {"result": "the real, resolved final answer"}},
                }
            }
        },
        "workflow_definition": {
            "nodes": [
                {"id": "End", "data": {"input": {"template": "{{Some Node[data.result.response]}}"}}},
            ]
        },
    }
    assert extract_bodhi_answer(result) == "the real, resolved final answer"


def test_empty_end_node_falls_back_to_agent_response():
    result = {
        "exec_metadata": {
            "allOutputs": {
                "run-1": {
                    "Contrarian Agent": {"data": {"result": {"response": "the agent's real answer"}}},
                    "End": {"data": {"result": ""}},
                }
            }
        }
    }
    assert extract_bodhi_answer(result) == "the agent's real answer"


def test_raises_loudly_when_nothing_can_be_found():
    with pytest.raises(RuntimeError):
        extract_bodhi_answer({"id": "run-1", "status": "completed"})


def test_node_progress_summary_reports_real_completed_nodes_mid_run():
    """Real intermediate workflow state, not a guessed percentage: if
    Bodhi's poll response already shows some nodes with status "success"
    while the overall run is still in-progress, that's genuine
    operator-visible progress."""
    data = {
        "status": "in-progress",
        "exec_metadata": {
            "allOutputs": {
                "run-1": {
                    "start": {"status": "success"},
                    "Risk Analyst Agent": {"status": "success"},
                    "Contrarian Agent": {"status": "running"},
                }
            }
        },
    }
    summary = _node_progress_summary(data)
    assert "start" in summary
    assert "Risk Analyst Agent" in summary
    assert "Contrarian Agent" not in summary


def test_node_progress_summary_returns_none_when_shape_is_absent():
    assert _node_progress_summary({"status": "in-progress"}) is None
    assert _node_progress_summary({}) is None
