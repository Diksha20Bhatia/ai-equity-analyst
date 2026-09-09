"""
observability.py  —  optional LangSmith tracing + always-on run telemetry
============================================================================
Wraps every pipeline step and every real AI call with LangSmith's
`@traceable` decorator, so a full run shows up in LangSmith as one tree:
the swing/intraday conductor at the root, each agent as a child, and each
actual LLM call as a grandchild with the real prompt, response, latency,
and token usage — https://smith.langchain.com

This part is entirely OPT-IN and has no effect on the app's behaviour if
it isn't configured:
  - Set LANGSMITH_API_KEY in .env to turn tracing on.
  - Leave it blank (the default) and `traceable` becomes a no-op — the
    functions it wraps run exactly as before, nothing is sent anywhere,
    and the `langsmith` package doesn't even need to be installed.

Below that, four ALWAYS-ON, zero-setup JSONL logs — no LangSmith needed
— that persist real runtime evidence to disk every run:
  output/reliability_log.jsonl        — every Bodhi Gateway retry/timeout/failure
  output/llm_call_log.jsonl           — every successful call's real latency + tokens
  output/orchestrator_decisions.jsonl — every real retry-vs-finish branch decision
  output/tool_call_log.jsonl          — every real non-LLM tool call an agent makes
                                         mid-reasoning (e.g. the Decision Agent's live
                                         TradingView quote — integrations/tradingview_quote.py)
"""

import os

enabled = bool(os.getenv("LANGSMITH_API_KEY", "").strip())

if enabled:
    try:
        from langsmith import traceable as _traceable

        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_PROJECT", os.getenv("LANGSMITH_PROJECT", "ai-equity-analyst"))
        traceable = _traceable
    except ImportError:
        print(
            "[observability] LANGSMITH_API_KEY is set but the `langsmith` package "
            "isn't installed — run `uv pip install langsmith` to enable tracing. "
            "Continuing without it."
        )
        enabled = False

if not enabled:
    def traceable(*dargs, **dkwargs):
        """No-op stand-in for langsmith.traceable when tracing isn't configured."""
        if dargs and callable(dargs[0]) and len(dargs) == 1 and not dkwargs:
            return dargs[0]  # used as bare @traceable

        def decorator(func):
            return func

        return decorator


# ---------------------------------------------------------------------
# Always-on structured failure telemetry — separate from LangSmith above
# (which only covers AI calls, and only when configured). This always
# runs, needs no setup, and is what a "why did this fail" question would
# actually be answered from — every Bodhi Gateway retry, timeout, and
# integration failure across a run, not just a console print that
# scrolls away.
# ---------------------------------------------------------------------
import json
import datetime as dt
from pathlib import Path

_FAILURE_LOG = Path(__file__).resolve().parent / "output" / "reliability_log.jsonl"
_CALL_LOG = Path(__file__).resolve().parent / "output" / "llm_call_log.jsonl"
_DECISION_LOG = Path(__file__).resolve().parent / "output" / "orchestrator_decisions.jsonl"
_TOOL_CALL_LOG = Path(__file__).resolve().parent / "output" / "tool_call_log.jsonl"


def _append_jsonl(path: Path, record: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:  # noqa: BLE001
        pass


def record_failure(component: str, detail: str, **extra) -> None:
    """Appends one structured failure record. Never raises — a logging
    hiccup must never break the real failure path it's recording."""
    _append_jsonl(_FAILURE_LOG, {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "component": component,
        "detail": str(detail),
        **extra,
    })


def record_call(backend: str, model: str, latency_ms: float, usage: dict = None,
                 agent: str = None, **extra) -> None:
    """Appends one structured record of a SUCCESSFUL LLM call — real
    per-call latency and real token usage the gateway reported, persisted
    to output/llm_call_log.jsonl regardless of whether LangSmith is
    configured. This is what answers "what did this run actually cost,
    and how fast was it" after the fact, from a plain run artifact rather
    than only a console line that scrolls away. Never raises."""
    usage = usage or {}
    _append_jsonl(_CALL_LOG, {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "backend": backend,
        "model": model,
        "agent": agent,
        "latency_ms": round(latency_ms, 1),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        **extra,
    })


def record_orchestrator_decision(tier: str, retries: int, issues: list,
                                  next_step: str, reason: str) -> None:
    """Appends one structured record of a real Orchestrator routing
    decision (retry vs finish) to output/orchestrator_decisions.jsonl —
    persisted branch-decision evidence, not just a console print. Never
    raises."""
    _append_jsonl(_DECISION_LOG, {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "tier": tier,
        "retries_so_far": retries,
        "issues_found": issues,
        "next_step": next_step,
        "reason": reason,
    })


def record_tool_call(tool: str, target: str, success: bool, latency_ms: float = None,
                      agent: str = None, **extra) -> None:
    """Appends one structured record of a real TOOL call an agent makes
    mid-reasoning — distinct from an LLM call (record_call above): this
    is external I/O the agent's own code path triggers to fetch real
    data before/while it reasons (e.g. integrations/tradingview_quote.py's
    live quote). Persisted to output/tool_call_log.jsonl regardless of
    whether the call succeeded, so a run's real tool-use footprint is
    visible after the fact. Never raises."""
    _append_jsonl(_TOOL_CALL_LOG, {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "tool": tool,
        "target": target,
        "success": success,
        "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
        "agent": agent,
        **extra,
    })


def read_recent(log: str, n: int = 20) -> list:
    """Reads the last n records from one of this module's JSONL logs, by
    short name: "failures", "calls", "decisions", "tool_calls". Returns
    [] if the file doesn't exist yet or can't be read — this is a
    best-effort dashboard/reporting helper, never a data path anything
    else in the app depends on."""
    paths = {
        "failures": _FAILURE_LOG, "calls": _CALL_LOG,
        "decisions": _DECISION_LOG, "tool_calls": _TOOL_CALL_LOG,
    }
    path = paths.get(log)
    if not path or not path.exists():
        return []
    try:
        lines = path.read_text().strip().splitlines()
    except Exception:  # noqa: BLE001
        return []
    records = []
    for line in lines[-n:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    records.reverse()  # newest first
    return records
