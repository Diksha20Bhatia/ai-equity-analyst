"""
intraday_run.py  —  the day-trading conductor
=================================================
A separate, on-demand scan from run.py's daily swing scan. Only meaningful
while the NSE cash market is open (09:15-15:30 IST) — outside those hours
Yahoo has no fresh intraday bars to score.

Pipeline, in order:

  1. Fetch real 5-minute bars for the universe (data/intraday_data.py).
  2. Objective, code-only quality checks exclude stocks with stale/zero-
     volume/discontinuous bars — no AI needed, these are unambiguous.
  3. Data Sentinel — ONE batched AI call, but ONLY for stocks with an
     unusually large, otherwise-clean overnight gap (often zero stocks,
     in which case this call doesn't happen at all). Decides whether that
     gap is safe to score as a real move or should be excluded.
  4. Mechanical scoring (agents/intraday_agent.py) — ZERO AI calls. Fixed
     rules on opening-range breakout / VWAP / volume-surge / momentum
     decide score, direction, and conviction for every remaining stock.
  5. Rank by score magnitude, keep the top TOP_N.
  6. Narrator — ONE batched AI call, top-N only. Writes a plain-English
     explanation and a bar-shape pattern tag for each pick. These are
     WRITE-ONLY fields: they explain the score, they never change it. The
     score/rank/conviction computed in step 4 are never touched again
     past this point.
  7. Send the alert, save the scan to disk.

For the end-of-day outcome log, run intraday_backtest.py separately after
market close.

    uv run python intraday_run.py
"""

import datetime as dt
import json

import observability
from observability import traceable
from config import settings
from data.nse_universe import resolve_universe
from data.intraday_data import get_intraday_bundles, bars_to_records
from agents.intraday_agent import IntradayAgent
from agents.intraday_quality_agent import IntradayQualityAgent
from agents.intraday_narrator_agent import IntradayNarratorAgent
from alerts import telegram_alert

CONVICTION_EMOJI = {"HIGH": "\U0001F525", "LOW": "\U0001F440"}  # fire / eyes


def banner(text):
    print("\n" + "=" * 64)
    print(f"  {text}")
    print("=" * 64)


@traceable(run_type="chain", name="AI Equity Analyst — intraday scan")
def main():
    banner("AI EQUITY ANALYST — intraday scan")
    print(f"Time     : {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Tracing  : {'LangSmith (connected)' if observability.enabled else 'disabled (set LANGSMITH_API_KEY to enable)'}")

    universe = resolve_universe(settings.universe)
    print(f"Universe : {len(universe)} stocks ({settings.universe})")

    banner("STEP 1 — Fetching real intraday bars (5-min, today's session) — 0 AI calls")
    bundles = get_intraday_bundles(universe)
    if not bundles:
        raise RuntimeError(
            "No real intraday data could be fetched for any symbol — the "
            "market may be closed, or Yahoo Finance has no bars for today yet."
        )
    skipped = len(universe) - len(bundles)
    if skipped:
        print(f"  ({skipped} symbol(s) skipped — see [intraday] lines above)")

    # STEP 2 — objective, code-only quality checks -----------------------------
    banner("STEP 2 — Objective data-quality checks — 0 AI calls")
    excluded_mechanical = {s: b for s, b in bundles.items() if b["mechanical_flags"]}
    for sym, b in excluded_mechanical.items():
        print(f"  [quality] excluding {sym}: {'; '.join(b['mechanical_flags'])}")
    scorable = {s: b for s, b in bundles.items() if s not in excluded_mechanical}

    # STEP 3 — Data Sentinel: ONLY for stocks with an unexplained large gap ----
    review_needed = {s: b for s, b in scorable.items() if b["needs_gap_review"]}
    if review_needed:
        banner(f"STEP 3 — Data Sentinel — 1 batched AI call ({len(review_needed)} gap(s) to review)")
        quality_agent = IntradayQualityAgent()
        quality_checks = quality_agent.run(review_needed)
        for sym, check in quality_checks.items():
            if not check.get("usable", True):
                print(f"  [quality] excluding {sym}: {'; '.join(check.get('flags', []))}")
                scorable.pop(sym, None)
            elif check.get("note"):
                print(f"  [quality] {sym}: {check['note']}")
    else:
        banner("STEP 3 — Data Sentinel — skipped (no unusual gaps to review, 0 AI calls)")

    if not scorable:
        raise RuntimeError("Every symbol was excluded by the data-quality checks — nothing to score today.")

    # STEP 4 — mechanical scoring, ZERO AI calls --------------------------------
    banner("STEP 4 — Scoring — mechanical opening-range / VWAP / volume rules, 0 AI calls")
    agent = IntradayAgent()
    reads = [agent.run(sym, b) for sym, b in scorable.items()]
    reads.sort(key=lambda r: abs(r["score"]), reverse=True)
    top = reads[: settings.top_n]

    for r in top:
        print(f"  {r['symbol']}: {r['direction']} / {r['conviction']} conviction — {r['read']}")

    # STEP 5 — Narrator: ONE batched AI call, top-N only, write-only fields ----
    banner(f"STEP 5 — Narrator — 1 batched AI call ({len(top)} pick(s), write-only fields)")
    narrator_input = []
    for r in top:
        b = scorable[r["symbol"]]
        narrator_input.append({**r, **b, "bars": bars_to_records(b["bars"])})
    narrator = IntradayNarratorAgent()
    notes = narrator.run(narrator_input)
    for r in top:
        note = notes.get(r["symbol"], {})
        # Write-only: adds narrative/pattern fields, never touches
        # score/direction/conviction set by the rules engine in step 4.
        r["narrative"] = note.get("narrative", "")
        r["pattern_tag"] = note.get("pattern_tag", "")
        r["pattern_confidence"] = note.get("pattern_confidence", "")

    alert = format_intraday_alert(top)
    banner("Final output")
    telegram_alert.send(alert)

    out_path = f"output/intraday_scan_{dt.date.today().isoformat()}_{dt.datetime.now().strftime('%H%M')}.json"
    with open(out_path, "w") as fp:
        json.dump({"picks": top, "all_reads": reads}, fp, indent=2)
    print(f"\nSaved intraday scan to {out_path}")


def format_intraday_alert(picks: list) -> str:
    lines = ["*AI Equity Analyst — Intraday Scan*", ""]
    lines.append(
        "_Mechanical same-day signals only — no AI judgment involved in score/rank. "
        f"{CONVICTION_EMOJI['HIGH']} HIGH CONVICTION = a real breakout held with volume behind it. "
        f"{CONVICTION_EMOJI['LOW']} LOW CONVICTION = just a watch, no confirmed breakout yet. "
        "Only meaningful while the market is open._"
    )
    lines.append("")
    for i, p in enumerate(picks, 1):
        emoji = CONVICTION_EMOJI.get(p["conviction"], "")
        lines.append(f"{i}. *{p['symbol']}*  {emoji} {p['conviction']} CONVICTION ({p['direction']})")
        if p.get("narrative"):
            lines.append(f"   {p['narrative']}")
        if p.get("pattern_tag"):
            lines.append(f"   Pattern: {p['pattern_tag'].replace('_', ' ')} (confidence: {p.get('pattern_confidence', '?')})")
        lines.append(f"   Read: {p['read']}")
        if p.get("entry") is not None:
            level_bits = [f"Entry: ₹{p['entry']:.2f}"]
            if p.get("stop_loss") is not None:
                level_bits.append(f"Stop: ₹{p['stop_loss']:.2f}")
            if p.get("target") is not None:
                level_bits.append(f"Target: ₹{p['target']:.2f}")
            lines.append("   " + " | ".join(level_bits))
        lines.append("")
    lines.append(
        "⚠️ Not investment advice — for informational purposes only. Intraday "
        "trading carries high risk; use your own stop-loss discipline."
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
