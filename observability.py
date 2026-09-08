"""
observability.py  —  optional LangSmith tracing
====================================================
Wraps every pipeline step and every real Gemini call with LangSmith's
`@traceable` decorator, so a full run shows up in LangSmith as one tree:
the swing/intraday conductor at the root, each agent as a child, and each
actual LLM call as a grandchild with the real prompt, response, latency,
and token usage — https://smith.langchain.com

This is entirely OPT-IN and has no effect on the app's behaviour if it
isn't configured:
  - Set LANGSMITH_API_KEY in .env to turn tracing on.
  - Leave it blank (the default) and `traceable` becomes a no-op — the
    functions it wraps run exactly as before, nothing is sent anywhere,
    and the `langsmith` package doesn't even need to be installed.
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


def record_failure(component: str, detail: str, **extra) -> None:
    """Appends one structured failure record. Never raises — a logging
    hiccup must never break the real failure path it's recording."""
    try:
        _FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
            "component": component,
            "detail": str(detail),
            **extra,
        }
        with open(_FAILURE_LOG, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:  # noqa: BLE001
        pass
