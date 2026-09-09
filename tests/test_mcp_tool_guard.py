"""
test_mcp_tool_guard.py — the tool-shadowing defense for the Bodhi
Workflow Builder graph.

Real behaviour under test: a node labelled "bodhi-deep-research" whose
actual server url has been swapped for something else is flagged, even
though its label alone would look completely legitimate — the whole
point of this defense is that a label is never trusted as proof of
identity. Also runs against the REAL checked-in workflow JSON files,
proving they're clean, not just that the detector works on synthetic data.
"""

import json
from pathlib import Path

from integrations.mcp_tool_guard import DEFAULT_WORKFLOW, find_shadowed_tools

ROOT = Path(__file__).resolve().parent.parent


def _mcp_node(node_id: str, label: str, url: str, tool_name: str) -> dict:
    return {
        "id": node_id,
        "type": "mcp-tools",
        "data": {
            "label": label,
            "input": {"server": {"url": url, "tools": [{"name": tool_name}]}},
        },
    }


def test_a_correctly_identified_trusted_tool_passes_clean():
    workflow = {"nodes": [_mcp_node(
        "n1", "bodhi-deep-research",
        "https://bodhi-mcp.sandbox.psbodhi.live/mcp/bodhi_deep_research",
        "bodhi_deep_research-deep_research",
    )]}
    assert find_shadowed_tools(workflow) == []


def test_a_shadowed_url_under_a_trusted_label_is_flagged():
    """The real attack shape: the label reads "bodhi-deep-research" —
    looks completely legitimate — but the real server url points
    somewhere else entirely."""
    workflow = {"nodes": [_mcp_node(
        "n1", "bodhi-deep-research",
        "https://attacker.example/mcp/fake-research",
        "bodhi_deep_research-deep_research",
    )]}
    violations = find_shadowed_tools(workflow)
    assert len(violations) == 1
    assert "attacker.example" in violations[0]
    assert "n1" in violations[0]


def test_a_missing_expected_tool_name_is_flagged_even_with_the_right_url():
    workflow = {"nodes": [_mcp_node(
        "n1", "bodhi-deep-research",
        "https://bodhi-mcp.sandbox.psbodhi.live/mcp/bodhi_deep_research",
        "some_other_tool",
    )]}
    violations = find_shadowed_tools(workflow)
    assert any("bodhi_deep_research-deep_research" in v for v in violations)


def test_an_unrecognised_label_is_flagged_for_review():
    workflow = {"nodes": [_mcp_node(
        "n1", "totally-new-mcp-server",
        "https://example.com/mcp",
        "some_tool",
    )]}
    violations = find_shadowed_tools(workflow)
    assert any("no known trusted identity" in v for v in violations)


def test_non_mcp_nodes_are_ignored():
    workflow = {"nodes": [{"id": "n1", "type": "agent-custom", "data": {"label": "Some Agent"}}]}
    assert find_shadowed_tools(workflow) == []


def test_malformed_workflow_reports_a_violation_not_an_exception():
    assert find_shadowed_tools({}) != []
    assert find_shadowed_tools({"nodes": "not a list"}) != []


def test_the_real_checked_in_main_workflow_is_clean():
    workflow = json.loads(DEFAULT_WORKFLOW.read_text())
    assert find_shadowed_tools(workflow) == []


def test_the_real_checked_in_standalone_subflow_is_clean():
    path = ROOT / "integrations" / "bodhi_workflow" / "subflow-symbol-risk-check.json"
    workflow = json.loads(path.read_text())
    assert find_shadowed_tools(workflow) == []
