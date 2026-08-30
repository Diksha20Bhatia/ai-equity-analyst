"""
bodhi_client.py  —  generic REST client for a Bodhi workflow run
=====================================================================
A generalised version of the start-run / poll / extract-answer pattern
already proven against Bodhi in an earlier project (a RAG workflow with
a single "ragprompt" variable). This version takes the task ID, workflow
ID and the variables dict as arguments, so it can drive ANY Bodhi
workflow — including a multi-agent one like a Risk Insights Generator,
whose UI Node takes two different inputs instead of one.

Nothing here is ai-equity-analyst-specific — see bodhi_risk_insights.py
for the part that builds a real portfolio from today's shortlist and
calls this client. Kept isolated on purpose: only imported when
BODHI_ENABLED=true, and every function raises a specific error rather
than ever inventing an answer.
"""

import json
import time
from typing import Any, Dict, Optional

import requests


def bodhi_headers(pat_token: str, trace_prefix: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {pat_token}",
        "Content-Type": "application/json",
        "X-Trace-User-ID": f"{trace_prefix}-{int(time.time())}",
    }


def start_bodhi_run(
    base_url: str,
    pat_token: str,
    task_id: str,
    workflow_id: str,
    trace_prefix: str,
    variables: Dict[str, Any],
    run_name: Optional[str] = None,
) -> str:
    url = f"{base_url}/api/v1/tasks/{task_id}/runs"
    payload = {
        "runName": run_name or f"run-{int(time.time())}",
        "overrides": {"workflow": workflow_id, "variables": variables},
    }
    response = requests.post(url, json=payload, headers=bodhi_headers(pat_token, trace_prefix), timeout=30)
    response.raise_for_status()
    run_id = response.json()["id"]
    print(f"[bodhi] run started: {run_id}")
    return run_id


def poll_bodhi_run(
    base_url: str,
    pat_token: str,
    task_id: str,
    run_id: str,
    trace_prefix: str,
    poll_interval: float,
    timeout: float,
) -> Dict[str, Any]:
    url = f"{base_url}/api/v1/tasks/{task_id}/runs/{run_id}"
    started = time.time()

    while time.time() - started < timeout:
        response = requests.get(url, headers=bodhi_headers(pat_token, trace_prefix), timeout=30)
        response.raise_for_status()
        data = response.json()
        status = str(data.get("status", "")).lower()
        print(f"[bodhi] status: {status}")

        if status in ("completed", "success", "succeeded"):
            return data
        if status in ("failed", "error", "errored"):
            raise RuntimeError(f"[bodhi] run failed:\n{json.dumps(data, indent=2, ensure_ascii=False)}")
        time.sleep(poll_interval)

    raise TimeoutError(f"[bodhi] run did not finish within {timeout}s")


def _node_outputs_from_all_outputs(all_outputs: Any) -> Optional[str]:
    """Given Bodhi's confirmed real per-run node results —
    {<run_name>: {<node_name>: {"data": {...}, "status": ...}, ...}} — return
    the best answer. Two node-result shapes are confirmed real:
      - an agent node: data.result is a dict with a "response" string
      - the End node: data.result is the resolved output string directly
    Prefers an explicit "End" node's result — that's the officially
    configured final output (see Bodhi's "Configure Workflow Output") —
    falling back to the LAST agent-shaped node with a real response if
    there's no End node or it's empty."""
    if not isinstance(all_outputs, dict):
        return None
    for nodes in all_outputs.values():
        if not isinstance(nodes, dict):
            continue

        end_node = nodes.get("End")
        if isinstance(end_node, dict):
            end_data = end_node.get("data")
            if isinstance(end_data, dict):
                end_result = end_data.get("result")
                if isinstance(end_result, str) and end_result.strip():
                    return end_result.strip()

        last_response = None
        for node_result in nodes.values():
            if not isinstance(node_result, dict):
                continue
            data = node_result.get("data")
            if not isinstance(data, dict):
                continue
            node_answer = data.get("result")
            if isinstance(node_answer, dict):
                resp = node_answer.get("response")
                if isinstance(resp, str) and resp.strip():
                    last_response = resp.strip()
        if last_response:
            return last_response
    return None


def extract_bodhi_answer(result: Dict[str, Any]) -> str:
    """Bodhi's response shape isn't fixed across workflows, so this tries
    a few likely paths before giving up loudly.

    Confirmed against a real raw API response: the actual per-node run
    results live at result["exec_metadata"]["allOutputs"] — NOT at the top
    level of the response, which is mostly run metadata (id/status/timestamps)
    plus the full workflow GRAPH DEFINITION (every node's stored config,
    including any node's raw, unresolved template text). That distinction
    matters: a blind fuzzy search over the whole raw response risks matching
    a node's stored config text instead of its real resolved output — which
    is exactly what happened before this fix (a malformed template string
    sitting in the End node's config was returned as if it were the answer).
    So the exec_metadata.allOutputs path below is checked FIRST and is
    normally sufficient; the older fallbacks after it exist only for a
    differently-shaped Bodhi response."""
    exec_metadata = result.get("exec_metadata") if isinstance(result, dict) else None
    if isinstance(exec_metadata, dict):
        found = _node_outputs_from_all_outputs(exec_metadata.get("allOutputs"))
        if found:
            return found

    found = _node_outputs_from_all_outputs(result)
    if found:
        return found

    for parent_key in ("outputs", "outputVariables", "data", "result"):
        parent = result.get(parent_key)
        if isinstance(parent, dict):
            found = _find_first_response_string(parent)
            if found:
                return found
        elif isinstance(parent, str) and parent.strip():
            return parent.strip()

    all_outputs = result.get("allOutputs", {})
    if isinstance(all_outputs, dict):
        for workflow_output in all_outputs.values():
            if not isinstance(workflow_output, dict):
                continue
            for node_output in workflow_output.values():
                if isinstance(node_output, dict):
                    found = _find_first_response_string(node_output)
                    if found:
                        return found

    found = _find_first_response_string(result)
    if found:
        return found

    raise RuntimeError(
        "[bodhi] run completed but no response text could be found in the result. "
        "Open the run in the Bodhi UI and check the real output-variable/node name — "
        "this extractor may need a path added for this workflow."
    )


def _find_first_response_string(obj: Any) -> Optional[str]:
    if isinstance(obj, dict):
        for key in ("result", "response", "answer", "text", "content", "message", "output"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, (dict, list)):
                found = _find_first_response_string(value)
                if found:
                    return found
        for value in obj.values():
            found = _find_first_response_string(value)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_first_response_string(item)
            if found:
                return found
    return None


def call_bodhi_workflow(
    base_url: str,
    pat_token: str,
    task_id: str,
    workflow_id: str,
    trace_prefix: str,
    variables: Dict[str, Any],
    poll_interval: float = 5,
    timeout: float = 300,
) -> str:
    run_id = start_bodhi_run(base_url, pat_token, task_id, workflow_id, trace_prefix, variables)
    result = poll_bodhi_run(base_url, pat_token, task_id, run_id, trace_prefix, poll_interval, timeout)
    return extract_bodhi_answer(result)
