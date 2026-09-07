"""
observability.py  —  always-on metrics + optional LangSmith tracing
====================================================================
Two independent layers, deliberately separated:

1. ALWAYS-ON (no setup, no keys, no network). Every real execution path
   in this app — every LLM call, every agent, every retrieval, every
   Bodhi workflow run, every prompt-safety event, every pipeline run —
   writes a structured record to output/metrics.jsonl, and every failure
   writes to output/reliability_log.jsonl. This is the layer that answers
   "what actually happened on Tuesday's run, how long did it take, how
   many tokens did it burn, and what broke" — with zero configuration.
   `tracker/ops_report.py` and the dashboard's Ops tab read it back.

2. OPT-IN LangSmith tracing (LANGSMITH_API_KEY). Adds a hosted, visual
   trace tree on top. If it isn't configured, `traceable` is a no-op and
   nothing about the app's behaviour changes — but layer 1 above still
   records everything, so the app is never running blind just because an
   external tracing service wasn't set up.
"""

import datetime as dt
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

# ---------------------------------------------------------------------
# Layer 2 — optional LangSmith tracing
# ---------------------------------------------------------------------
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
            "Continuing without it (local metrics still record everything)."
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
# Layer 1 — always-on structured metrics and failure telemetry
# ---------------------------------------------------------------------
_OUTPUT_DIR = Path(__file__).resolve().parent / "output"
_FAILURE_LOG = _OUTPUT_DIR / "reliability_log.jsonl"
_METRICS_LOG = _OUTPUT_DIR / "metrics.jsonl"

# Correlates every event emitted during one pipeline execution. Set by
# new_run(); until then events are still recorded, just tagged "adhoc"
# (e.g. a standalone tracker or on-demand Bodhi call outside a full run).
_RUN_ID = "adhoc"
_RUN_KIND = "adhoc"


def new_run(kind: str) -> str:
    """Starts a new correlated execution. Returns the run id every
    subsequent event in this process will be tagged with."""
    global _RUN_ID, _RUN_KIND
    _RUN_ID = f"{kind}-{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    _RUN_KIND = kind
    record_metric("run_started", kind=kind)
    return _RUN_ID


def current_run_id() -> str:
    return _RUN_ID


def _append(path: Path, record: dict) -> None:
    """Never raises — instrumentation must never break the thing it
    instruments."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:  # noqa: BLE001
        pass


def record_metric(event: str, **fields) -> None:
    """Appends one structured metric record. Always on, no setup needed."""
    _append(_METRICS_LOG, {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "run_id": _RUN_ID,
        "run_kind": _RUN_KIND,
        "event": event,
        **fields,
    })


def record_failure(component: str, detail: str, **extra) -> None:
    """Appends one structured failure record. Never raises — a logging
    hiccup must never break the real failure path it's recording. Also
    mirrored into the metrics stream so one read gives the whole picture."""
    _append(_FAILURE_LOG, {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "run_id": _RUN_ID,
        "component": component,
        "detail": str(detail),
        **extra,
    })
    record_metric("failure", component=component, detail=str(detail)[:300])


@contextmanager
def track(event: str, **fields):
    """Times a block of real work and records how it ended — always on.

        with observability.track("agent_run", agent="Decision Agent"):
            ...

    Records duration_ms and status="ok"/"error" (with the exception type
    and message on error), then re-raises. Anything the block wants to add
    to the record can be written into the yielded dict."""
    extra = {}
    started = time.perf_counter()
    try:
        yield extra
    except Exception as e:  # noqa: BLE001
        record_metric(
            event, status="error", error_type=type(e).__name__, error=str(e)[:300],
            duration_ms=round((time.perf_counter() - started) * 1000), **fields, **extra,
        )
        raise
    record_metric(
        event, status="ok",
        duration_ms=round((time.perf_counter() - started) * 1000), **fields, **extra,
    )


def read_metrics(limit: int = 5000) -> list:
    """Reads the metrics log back, newest last. Used by the Ops report and
    the dashboard — returns [] rather than raising if nothing's logged."""
    if not _METRICS_LOG.exists():
        return []
    out = []
    try:
        with open(_METRICS_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out[-limit:]


def read_failures(limit: int = 1000) -> list:
    """Reads the failure log back, newest last."""
    if not _FAILURE_LOG.exists():
        return []
    out = []
    try:
        with open(_FAILURE_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out[-limit:]
