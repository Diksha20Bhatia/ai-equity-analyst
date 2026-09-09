"""
mcp_tool_guard.py  —  a real tool-shadowing defense for the Bodhi
Workflow Builder graph
=====================================================================
Tool-shadowing: a node whose LABEL matches a trusted tool ("bodhi-deep-
research") but whose actual server identity (URL) has been swapped for
something else — a malicious or accidental substitute quietly
intercepting calls a reader assumes go to the real research backend.
Nothing before this project checked for that; a workflow JSON is just
text, and a label is not an identity.

The invariant this enforces is real and checkable: every `mcp-tools`
node that shares the same label must ALSO share the exact same pinned
server URL and tool name — the label alone is never trusted as proof of
identity. `workflow-AI-Equity-Analyst-846bb836-...json` has three
`bodhi-deep-research` nodes (Risk Analyst Agent's, the Escalation
Analyst's, and the one attached to the Quick Risk Check Subflow's own
standalone workflow) — this is exactly the shape a shadowing attack
would target: three chances for one of them to have been quietly
swapped, and no prior code that would have noticed.

    uv run python integrations/mcp_tool_guard.py [path/to/workflow.json]
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKFLOW = ROOT / "integrations" / "bodhi_workflow" / \
    "workflow-AI-Equity-Analyst-846bb836-ab0a-4b48-9226-1253617a3699.json"

# The one real, pinned MCP server identity this project's workflows are
# meant to use. Any mcp-tools node whose label matches but whose real
# url/tool name doesn't is flagged — a mismatch here is exactly what a
# tool-shadowing attack (or an innocent copy-paste from a different
# project) would produce.
_TRUSTED_MCP_IDENTITIES = {
    "bodhi-deep-research": {
        "url": "https://bodhi-mcp.sandbox.psbodhi.live/mcp/bodhi_deep_research",
        "tool_name": "bodhi_deep_research-deep_research",
    },
}


def find_shadowed_tools(workflow: dict) -> list:
    """Returns a list of human-readable violation strings — empty means
    every mcp-tools node's real identity (server url + tool name)
    matches what its label claims to be. Never raises: a malformed
    workflow structure is itself reported as a violation, not an
    exception, so this can always be called safely against arbitrary
    JSON without a try/except at every call site."""
    violations = []
    nodes = workflow.get("nodes") if isinstance(workflow, dict) else None
    if not isinstance(nodes, list):
        return ["workflow JSON has no 'nodes' array — cannot check for tool shadowing"]

    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "mcp-tools":
            continue
        label = (node.get("data") or {}).get("label", "?")
        node_id = node.get("id", "?")
        trusted = _TRUSTED_MCP_IDENTITIES.get(label)
        if trusted is None:
            violations.append(
                f"node {node_id} ({label!r}): no known trusted identity for this label — "
                f"either a legitimately new MCP server that needs adding to "
                f"_TRUSTED_MCP_IDENTITIES, or an unrecognised/shadowed tool"
            )
            continue

        server = ((node.get("data") or {}).get("input") or {}).get("server") or {}
        real_url = server.get("url")
        real_tool_names = {t.get("name") for t in (server.get("tools") or []) if isinstance(t, dict)}

        if real_url != trusted["url"]:
            violations.append(
                f"node {node_id} ({label!r}): server url {real_url!r} does NOT match the "
                f"trusted identity {trusted['url']!r} for this label — possible tool shadowing"
            )
        if trusted["tool_name"] not in real_tool_names:
            violations.append(
                f"node {node_id} ({label!r}): expected tool {trusted['tool_name']!r} not found "
                f"in this node's declared tools {sorted(real_tool_names)} — possible tool shadowing"
            )

    return violations


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_WORKFLOW
    workflow = json.loads(path.read_text())
    violations = find_shadowed_tools(workflow)

    mcp_node_count = sum(1 for n in workflow.get("nodes", []) if n.get("type") == "mcp-tools")
    print(f"[mcp-guard] checked {mcp_node_count} mcp-tools node(s) in {path.name}")

    if violations:
        print(f"[mcp-guard] {len(violations)} possible tool-shadowing issue(s) found:")
        for v in violations:
            print(f"  ⚠ {v}")
        sys.exit(1)

    print("[mcp-guard] clean — every mcp-tools node's real identity matches its label")


if __name__ == "__main__":
    main()
