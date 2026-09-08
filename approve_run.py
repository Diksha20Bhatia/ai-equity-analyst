"""
approve_run.py  —  the human checkpoint: review, then publish (or reject)
=============================================================================
run.py never sends Telegram alerts or pushes to TradingView itself — it
saves the finished shortlist as a PENDING run (alerts/approval_gate.py) and
stops. This script is the other half: it shows you exactly what a pending
run would send, and only on your explicit approval does it actually send
the Telegram alert and push the remaining symbols to TradingView. Nothing
in this project's automated pipeline can publish a recommendation without
a human looking at it first.

    uv run python approve_run.py                    # list pending runs
    uv run python approve_run.py --latest            # review + approve the newest pending run
    uv run python approve_run.py --show <path>       # review a specific pending run, no action
    uv run python approve_run.py --latest --reject "reason"

--latest without --reject prompts for a real y/n confirmation before
publishing — this is interactive by design. For unattended/cron use, pass
--auto-approve explicitly (never the default) alongside --latest.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from alerts import approval_gate, telegram_alert, tradingview_watchlist  # noqa: E402


def _summarize(record: dict) -> str:
    lines = [f"Run date   : {record.get('date', '?')}", f"Created at : {record.get('created_at', '?')}"]
    results_by_tier = record.get("results_by_tier") or {}
    for tier, result in results_by_tier.items():
        picks = result.get("shortlist", [])
        symbols = ", ".join(p["symbol"] for p in picks)
        lines.append(f"  [{tier}] {len(picks)} pick(s): {symbols or '(none)'}")
    tv_symbols = record.get("tv_symbols") or []
    lines.append(f"TradingView candidates: {', '.join(tv_symbols) or '(none)'}")
    if record.get("bodhi_ran"):
        lines.append("Bodhi risk verification: ran (full report will also be sent to Telegram)")
    return "\n".join(lines)


def _publish(record: dict) -> None:
    delivered = telegram_alert.send(record["alert_text"])
    if not delivered:
        print("[approve] WARNING: Telegram did not confirm delivery — see [telegram] lines above.")
    if record.get("bodhi_ran") and record.get("bodhi_report"):
        telegram_alert.send("*🧠 Bodhi Risk Insight — full report*\n\n" + record["bodhi_report"])
    tv_symbols = record.get("tv_symbols") or []
    if tv_symbols:
        tradingview_watchlist.add_symbols(tv_symbols)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--latest", action="store_true", help="review + act on the newest pending run")
    parser.add_argument("--show", metavar="PATH", help="review a specific pending run, no action taken")
    parser.add_argument("--reject", metavar="REASON", nargs="?", const="", default=None,
                         help="reject instead of approve (use with --latest or --show)")
    parser.add_argument("--auto-approve", action="store_true",
                         help="skip the interactive confirmation — for unattended/cron use only, "
                              "never the default; must be passed explicitly every time")
    args = parser.parse_args()

    if args.show:
        record = approval_gate.load(args.show)
        print(_summarize(record))
        return

    if not args.latest:
        pending = approval_gate.list_pending()
        if not pending:
            print("No pending runs. Run `uv run python run.py` first.")
            return
        print(f"{len(pending)} pending run(s), newest first:\n")
        for p in pending:
            print(f"  {p}")
        print("\nReview one with --show <path>, or act on the newest with --latest.")
        return

    pending = approval_gate.list_pending()
    if not pending:
        print("No pending runs. Run `uv run python run.py` first.")
        return
    path = pending[0]
    record = approval_gate.load(path)
    print(f"Reviewing: {path}\n")
    print(_summarize(record))
    print()

    if args.reject is not None:
        approval_gate.reject(path, reason=args.reject)
        print(f"\n[approve] Rejected — moved to {approval_gate.REJECTED_DIR}. Nothing was sent.")
        return

    if not args.auto_approve:
        answer = input("\nApprove and publish this run to Telegram/TradingView? [y/N] ").strip().lower()
        if answer != "y":
            print("[approve] Not approved — left pending. Nothing was sent.")
            return

    _publish(record)
    approval_gate.approve(path)
    print(f"\n[approve] Approved and published — moved to {approval_gate.APPROVED_DIR}.")


if __name__ == "__main__":
    main()
