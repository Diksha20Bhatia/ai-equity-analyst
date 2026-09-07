"""
trigger_telegram.py  —  on-demand Telegram resend, standalone
=====================================================================
    uv run python alerts/trigger_telegram.py

Loads the most recent output/analysis_*.json and re-sends its shortlist
to Telegram, using the exact same formatting run.py uses — without
re-running the whole pipeline. This is what the dashboard's "📨 Send
Telegram alert" button calls, the same way tracker/price_tracker.py is a
standalone companion to run.py rather than a step inside it.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alerts import telegram_alert  # noqa: E402
from run import format_alert  # noqa: E402

OUTPUT_DIR = ROOT / "output"


def _latest_run() -> Path:
    runs = sorted(OUTPUT_DIR.glob("analysis_*.json"), reverse=True)
    if not runs:
        raise RuntimeError(
            f"No saved runs found in {OUTPUT_DIR} — run.py needs to complete "
            "at least once before there's a shortlist to send."
        )
    return runs[0]


def run():
    run_path = _latest_run()
    saved = json.loads(run_path.read_text())
    print(f"[telegram] using shortlist from {run_path.name}")

    results_by_tier = saved.get("results_by_tier") or (
        {"moderate": saved["result"]} if saved.get("result") else {}
    )
    if not results_by_tier:
        raise RuntimeError(f"No shortlist found in {run_path.name}.")

    alert = format_alert(results_by_tier)
    delivered = telegram_alert.send(alert)
    if not delivered:
        raise RuntimeError(
            "Telegram did not confirm delivery — see the [telegram] lines above "
            "for the real reason (not configured, or the send attempt failed)."
        )
    print("[telegram] sent.")


if __name__ == "__main__":
    run()
