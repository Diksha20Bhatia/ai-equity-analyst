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
