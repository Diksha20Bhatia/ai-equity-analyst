"""
approval_queue.py  —  the human checkpoint before anything leaves the app
=====================================================================
run.py no longer sends Telegram or pushes TradingView the moment a
shortlist is computed — that's now a deliberate human action, not an
automatic side effect of running the pipeline. Every run instead saves a
pending approval record here; a person reviews it in the dashboard's
Approvals tab and clicks **Approve** (sends the Telegram alert and pushes
the Bodhi-approved subset of symbols to TradingView — the exact same
verdict-based gating that always applied) or **Reject** (sends nothing).

What's NOT gated by this: the Excel record (alerts/excel_report.py) and
the raw analysis JSON (output/analysis_DATE.json) are this app's own
internal record-keeping, not external delivery — they're written
immediately by run.py regardless of approval status, so a rejected run
still leaves a real, honest trace of what the AI would have sent.
"""

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import contracts  # noqa: E402
import observability  # noqa: E402

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
QUEUE_DIR = OUTPUT_DIR / "approvals"


def _pending_path(run_date: dt.date) -> Path:
    return QUEUE_DIR / f"pending_{run_date.isoformat()}.json"


def save_pending(results_by_tier: dict, bodhi_result: dict, actions_by_symbol: dict,
                  run_date: dt.date = None) -> str:
    """Saves today's shortlist + Bodhi verdict as awaiting approval —
    called by run.py in place of sending Telegram/pushing TradingView
    directly. A same-day re-run replaces the earlier pending record
    (matches how excel_report.py and the analysis JSON already behave)
    unless that record was already decided, in which case it's left
    alone and a fresh one is NOT created — re-running twice in one day
    shouldn't silently re-open something a human already rejected."""
    run_date = run_date or dt.date.today()
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    path = _pending_path(run_date)

    if path.exists():
        existing = load(path)
        if existing["status"] != "pending":
            print(f"[approval] {path.name} was already {existing['status']} — "
                  "not overwriting; re-run tomorrow or review it manually if this "
                  "was a genuine same-day re-analysis.")
            return str(path)

    # The release decision is computed HERE, at save time, by the shared
    # rules in contracts.py — so what the reviewer sees in the dashboard is
    # the same evaluation that will actually gate the push on approve, not
    # a separate description of it.
    plan = contracts.release_plan(
        results_by_tier,
        bodhi_result.get("decisions") or [],
        bodhi_result.get("ran", False),
    )

    record = {
        "run_date": run_date.isoformat(),
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "status": "pending",
        "results_by_tier": results_by_tier,
        "bodhi_decisions": bodhi_result.get("decisions") or [],
        "bodhi_report": bodhi_result.get("report"),
        "bodhi_ran": bodhi_result.get("ran", False),
        "flagged_symbols": sorted(bodhi_result.get("flagged_symbols") or []),
        "unverified_symbols": sorted(bodhi_result.get("unverified_symbols") or []),
        "actions_by_symbol": actions_by_symbol,
        "release_plan": plan,
    }
    path.write_text(json.dumps(record, indent=2, default=str))
    print(f"[approval] shortlist saved for review -> {path}")
    print("[approval] nothing sent to Telegram/TradingView yet — "
          "open the dashboard's Approvals tab to release it.")
    return str(path)


def list_queue() -> list:
    """Every approval record on disk, newest first, regardless of status."""
    if not QUEUE_DIR.exists():
        return []
    return sorted(QUEUE_DIR.glob("*.json"), reverse=True)


def load(path) -> dict:
    return json.loads(Path(path).read_text())


def approve(path, approver: str = "dashboard") -> dict:
    """Sends the Telegram alert and pushes the Bodhi-approved subset of
    symbols to TradingView — exactly what run.py used to do automatically,
    now gated behind this explicit action. Raises if this record isn't
    actually pending (e.g. a double-click), so a decision can never be
    silently made twice."""
    from run import format_alert
    from alerts import telegram_alert, tradingview_watchlist

    path = Path(path)
    record = load(path)
    if record["status"] != "pending":
        raise RuntimeError(f"{path.name} is already {record['status']}, not pending.")

    alert = format_alert(record["results_by_tier"], record.get("bodhi_decisions"))
    telegram_alert.send(alert)
    if record.get("bodhi_ran"):
        telegram_alert.send("*🧠 Bodhi Risk Insight — full report*\n\n" + record["bodhi_report"])

    # Re-evaluated at approve time against contracts.py rather than trusting
    # the plan stored at save time — the record on disk could have been
    # written by an older version of the rules, and the gate that actually
    # fires should always be the current one.
    plan = contracts.release_plan(
        record["results_by_tier"], record.get("bodhi_decisions"), record.get("bodhi_ran", False),
    )
    tv_symbols = plan["allowed"]
    if plan["held"]:
        print(f"[approval] held back by release rules: {', '.join(plan['held'])}")
        for symbol in plan["held"]:
            blocked_by = plan["detail"][symbol]["blocked_by"]
            print(f"[approval]   {symbol}: failed {', '.join(blocked_by)}")
    tradingview_watchlist.add_symbols(tv_symbols)

    record["status"] = "approved"
    record["approved_at"] = dt.datetime.now().isoformat(timespec="seconds")
    record["approved_by"] = approver
    record["tv_symbols_sent"] = tv_symbols
    record["release_plan"] = plan
    path.write_text(json.dumps(record, indent=2, default=str))
    observability.record_metric(
        "approval_decision", decision="approved", run_date=record["run_date"],
        approver=approver, released=len(tv_symbols), held=len(plan["held"]),
    )
    print(f"[approval] {path.name} approved and sent.")
    return record


def reject(path, reason: str = "") -> dict:
    """Marks the record rejected. Never sends anything — a rejected
    shortlist's only trace is the Excel/JSON record already written."""
    path = Path(path)
    record = load(path)
    if record["status"] != "pending":
        raise RuntimeError(f"{path.name} is already {record['status']}, not pending.")
    record["status"] = "rejected"
    record["rejected_at"] = dt.datetime.now().isoformat(timespec="seconds")
    record["reject_reason"] = reason
    path.write_text(json.dumps(record, indent=2, default=str))
    observability.record_metric(
        "approval_decision", decision="rejected", run_date=record["run_date"], reason=reason,
    )
    print(f"[approval] {path.name} rejected — nothing sent.")
    return record
