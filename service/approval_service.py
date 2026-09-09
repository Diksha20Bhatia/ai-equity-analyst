"""
approval_service.py  —  the human-approval/publish path, as a genuinely
SEPARATE, independently deployable HTTP microservice
=============================================================================
Everywhere else in this project, "approval" means a CLI script
(approve_run.py) or a Streamlit sidebar button (dashboard.py) — both only
exist while a human is sitting at that terminal or has that browser tab
open. This module is a THIRD, independently-deployable interface onto
the exact same logic, reachable over plain HTTP from anywhere: another
service, a scheduled job, a phone.

WHY THIS BOUNDARY, not some other split of the codebase: run.py (market
data fetch + multi-agent analysis) is compute-heavy, runs once a day, and
has no reason to be always-on or network-reachable — it's a batch job.
The approval/publish path is the opposite shape: a human (or another
system) needs to be able to review a pending run and act on it AT ANY
TIME, independent of whether an analysis run is currently executing.
That's a real architectural seam already implied by this project's own
design (run.py never publishes; approve_run.py is already a separate
step) — this module just gives that seam a second, always-available
interface instead of only a CLI/dashboard one.

NOT a second implementation to keep in sync: every endpoint below calls
straight into `alerts.approval_gate` (the same pending/approved/rejected
state machine) and `approve_run._publish()` (the same function that
sends to Telegram + pushes to TradingView — see tests/test_approve_run.py)
that the CLI and dashboard already use. There is exactly one real
"what does approving a run do" in this codebase; this is a new door onto
it, not a fork of it.

Run standalone (separately from run.py/dashboard.py, on its own
host/container/port):

    uv run uvicorn service.approval_service:app --host 0.0.0.0 --port 8000

Or via Docker (a real, independent deployment artifact — see
service/Dockerfile):

    docker build -f service/Dockerfile -t ai-equity-analyst-approval-service .
    docker run -p 8000:8000 --env-file .env ai-equity-analyst-approval-service

Interactive API docs (FastAPI auto-generates these): http://localhost:8000/docs
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import approve_run  # noqa: E402
from alerts import approval_gate  # noqa: E402

app = FastAPI(
    title="AI Equity Analyst — Approval Service",
    description=(
        "Independently deployable HTTP interface onto the same human-approval "
        "gate approve_run.py (CLI) and dashboard.py (Streamlit) both use — "
        "review a pending run and approve (publish to every configured "
        "channel) or reject it over plain HTTP, from anywhere."
    ),
    version="1.0.0",
)

# Every pending/approved/rejected filename is always exactly
# "run_YYYY-MM-DD_HHMMSS.json" (see alerts/approval_gate.py::save_pending) —
# validated BEFORE it's ever used to build a filesystem path, so a
# run_id taken from an HTTP request path can never escape
# output/{pending_approval,approved,rejected}/.
_RUN_ID_RE = re.compile(r"^run_\d{4}-\d{2}-\d{2}_\d{6}$")


class RejectBody(BaseModel):
    reason: str = ""


def _validated_path(run_id: str, directory: Path) -> Path:
    if not _RUN_ID_RE.match(run_id):
        raise HTTPException(status_code=400, detail=f"Malformed run_id: {run_id!r}")
    path = directory / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"No run found: {run_id}")
    return path


def _to_run_id(path: Path) -> str:
    return path.stem


def _summary(record: dict) -> dict:
    results_by_tier = record.get("results_by_tier") or {}
    return {
        "date": record.get("date"),
        "created_at": record.get("created_at"),
        "picks_by_tier": {tier: len(r.get("shortlist", [])) for tier, r in results_by_tier.items()},
        "bodhi_ran": record.get("bodhi_ran", False),
    }


@app.get("/health")
def health():
    """A real health-check endpoint — what an actual deployment (a load
    balancer, a container orchestrator, an uptime check) needs, not just
    an implicit "the process didn't crash"."""
    return {"status": "ok", "service": "ai-equity-analyst-approval-service"}


@app.get("/pending")
def list_pending():
    return [{"run_id": _to_run_id(p), **_summary(approval_gate.load(p))} for p in approval_gate.list_pending()]


@app.get("/approved")
def list_approved():
    return [{"run_id": _to_run_id(p), **_summary(approval_gate.load(p))} for p in approval_gate.list_approved()]


@app.get("/rejected")
def list_rejected():
    return [{"run_id": _to_run_id(p), **_summary(approval_gate.load(p))} for p in approval_gate.list_rejected()]


@app.get("/pending/{run_id}")
def get_pending(run_id: str):
    path = _validated_path(run_id, approval_gate.PENDING_DIR)
    return approval_gate.load(path)


@app.post("/pending/{run_id}/approve")
def approve(run_id: str):
    """Publishes to every configured channel (Telegram + TradingView —
    see approve_run.py::_publish(), the exact function the CLI and
    dashboard call) and moves the run to output/approved/ — the file is
    removed from pending as part of that move, so a concurrent second
    approve call on the same run_id 404s instead of double-publishing."""
    path = _validated_path(run_id, approval_gate.PENDING_DIR)
    record = approval_gate.load(path)
    approve_run._publish(record)
    approval_gate.approve(path)
    return {"run_id": run_id, "status": "approved_and_published"}


@app.post("/pending/{run_id}/reject")
def reject(run_id: str, body: RejectBody = RejectBody()):
    path = _validated_path(run_id, approval_gate.PENDING_DIR)
    approval_gate.reject(path, reason=body.reason)
    return {"run_id": run_id, "status": "rejected", "reason": body.reason}
