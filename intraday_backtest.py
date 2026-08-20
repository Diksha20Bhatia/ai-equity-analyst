"""
intraday_backtest.py  —  end-of-day outcome log for today's intraday picks
==============================================================================
Run this AFTER market close (from ~15:30 IST onwards) to see how today's
HIGH-conviction intraday picks actually played out. Reads the LATEST
intraday_run.py scan saved today, refetches real end-of-day price action
for those symbols, and asks ONE batched AI call to write plain outcome
facts (target hit / stop hit / neither) — historical analysis for a
running log, never a live trading suggestion.

    uv run python intraday_backtest.py
"""

import datetime as dt
import glob
import json

from observability import traceable
from data.intraday_data import get_intraday_bundles
from agents.intraday_backtest_agent import IntradayBacktestAgent


def banner(text):
    print("\n" + "=" * 64)
    print(f"  {text}")
    print("=" * 64)


def _load_today_picks() -> list:
    today = dt.date.today().isoformat()
    files = sorted(glob.glob(f"output/intraday_scan_{today}_*.json"))
    if not files:
        raise RuntimeError(
            f"No intraday scans found for today ({today}) in output/. "
            "Run intraday_run.py at least once earlier today first."
        )
    latest = files[-1]
    print(f"Using scan: {latest}")
    return json.loads(open(latest).read()).get("picks", [])


@traceable(run_type="chain", name="AI Equity Analyst — intraday backtest")
def main():
    banner("AI EQUITY ANALYST — intraday backtest (end-of-day)")

    picks = _load_today_picks()
    high_conv = [p for p in picks if p.get("conviction") == "HIGH" and p.get("entry") is not None]
    if not high_conv:
        print("No HIGH-conviction picks with entry/stop/target today — nothing to backtest.")
        return

    symbols = [p["symbol"] for p in high_conv]
    banner(f"Fetching real end-of-day price action for {len(symbols)} symbol(s)")
    eod_bundles = get_intraday_bundles(symbols)

    rows = []
    for p in high_conv:
        b = eod_bundles.get(p["symbol"])
        if not b:
            print(f"  [backtest] skipping {p['symbol']}: no end-of-day data")
            continue
        rows.append({
            "symbol": p["symbol"],
            "conviction": p["conviction"],
            "entry": p["entry"],
            "stop": p.get("stop_loss"),
            "target": p.get("target"),
            "signals_read": p.get("read", ""),
            "day_high": b["day_high"],
            "day_low": b["day_low"],
            "close": b["last_price"],
        })

    banner("Writing outcome log — 1 batched AI call")
    agent = IntradayBacktestAgent()
    result = agent.run(rows)

    for line in result.get("log", []):
        print(f"  {line}")
    print(f"\n{result.get('summary', '')}")

    out_path = f"output/intraday_backtest_{dt.date.today().isoformat()}.json"
    with open(out_path, "w") as fp:
        json.dump(result, fp, indent=2)
    print(f"\nSaved backtest log to {out_path}")


if __name__ == "__main__":
    main()
