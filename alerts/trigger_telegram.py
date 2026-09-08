"""
trigger_telegram.py  —  on-demand Telegram resend, standalone
=====================================================================
    uv run python alerts/trigger_telegram.py

Re-sends the most recently APPROVED run's shortlist to Telegram — reads
only from output/approved/ (alerts/approval_gate.py), never from a
pending or rejected run. This is deliberate: a run that was never
explicitly approved (approve_run.py, or the dashboard's "Review &
Approve" section) can never be sent by this button, not even by
accident. This is what the dashboard's "📨 Send Telegram alert" button
calls, the same way tracker/price_tracker.py is a standalone companion
to run.py rather than a step inside it.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alerts import telegram_alert, approval_gate  # noqa: E402


def run():
    approved = approval_gate.list_approved()
    if not approved:
        raise RuntimeError(
            "No approved runs found — nothing to re-send. Run `uv run python "
            "run.py` then approve it with `uv run python approve_run.py --latest`."
        )
    run_path = approved[0]
    saved = approval_gate.load(run_path)
    print(f"[telegram] re-sending shortlist from {run_path.name} (approved {saved.get('approved_at', '?')})")

    alert = saved.get("alert_text")
    if not alert:
        raise RuntimeError(f"No alert text found in {run_path.name}.")

    delivered = telegram_alert.send(alert)
    if not delivered:
        raise RuntimeError(
            "Telegram did not confirm delivery — see the [telegram] lines above "
            "for the real reason (not configured, or the send attempt failed)."
        )
    print("[telegram] sent.")


if __name__ == "__main__":
    run()
