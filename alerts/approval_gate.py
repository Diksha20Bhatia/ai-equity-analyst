"""
approval_gate.py  —  a real pause-and-resume human checkpoint before publish
==============================================================================
This project hands out real financial recommendations, so nothing goes out
to Telegram or gets pushed to a live TradingView watchlist automatically.
run.py stops short of publishing: it writes the finished shortlist here as
a PENDING run and does nothing else. Publishing is a separate, deliberate
step — approve_run.py (CLI) or the dashboard's "Review & Approve" section —
that a human has to explicitly take, against a specific run, not just
"whatever's newest."

This is a genuine pause, not a formality: alerts/trigger_telegram.py and
alerts/trigger_tradingview.py (the dashboard's re-send buttons) only ever
read from output/approved/ — a run that was never explicitly approved can
never be sent, not even by accident via a "re-send the latest" button.

    save_pending(payload: dict) -> Path
    list_pending() -> [Path, ...]           newest first
    load(path) -> dict
    approve(path) -> dict                    moves pending -> approved
    reject(path, reason: str = "") -> dict   moves pending -> rejected

A run can only be approved or rejected once — approve()/reject() move the
file out of the pending directory, so it physically cannot be actioned
twice.
"""

import json
import datetime as dt
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
PENDING_DIR = _ROOT / "output" / "pending_approval"
APPROVED_DIR = _ROOT / "output" / "approved"
REJECTED_DIR = _ROOT / "output" / "rejected"


def save_pending(payload: dict) -> Path:
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = PENDING_DIR / f"run_{ts}.json"
    record = {"created_at": dt.datetime.now().isoformat(timespec="seconds"), **payload}
    path.write_text(json.dumps(record, indent=2, default=str))
    return path


def list_pending() -> list:
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(PENDING_DIR.glob("run_*.json"), reverse=True)


def list_approved() -> list:
    APPROVED_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(APPROVED_DIR.glob("run_*.json"), reverse=True)


def load(path) -> dict:
    return json.loads(Path(path).read_text())


def _move_with_stamp(path, target_dir: Path, stamp_key: str, extra: dict) -> dict:
    path = Path(path)
    record = json.loads(path.read_text())
    record[stamp_key] = dt.datetime.now().isoformat(timespec="seconds")
    record.update(extra)
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / path.name
    dest.write_text(json.dumps(record, indent=2, default=str))
    path.unlink()  # remove from pending only after the copy is safely written
    return record


def approve(path) -> dict:
    """Moves the pending run to output/approved/ — this is what actually
    unblocks alerts/trigger_telegram.py and alerts/trigger_tradingview.py.
    Publishing itself (the real Telegram send / TradingView push) is the
    caller's job — approve_run.py does both in sequence — this function
    only guards the state transition so a run can't be approved twice."""
    return _move_with_stamp(path, APPROVED_DIR, "approved_at", {})


def reject(path, reason: str = "") -> dict:
    return _move_with_stamp(path, REJECTED_DIR, "rejected_at", {"rejection_reason": reason})
