"""
run.py  —  the conductor
========================
This is the one file you actually run:

    python run.py

It walks through the whole morning routine, in order:

  1. Load your settings, investor profile and memory.
  2. Research Agent scans the basket and picks a short watchlist.
  3. For each watchlist stock, the four specialist agents analyse it.
  4. Memory recalls anything relevant from past runs.
  5. Decision Agent combines everything into a final shortlist.
  6. Store today's analysis into memory and send a Telegram alert.

Everything is printed as it happens so you can watch the agents "think".
"""

import json
import datetime as dt

from config import settings, auth_used, client
from data import market_data
from data.nse_universe import resolve_universe
from memory.memory_layer import MemoryLayer
from profile.profile_loader import load_profile
from alerts import telegram_alert

from agents.research_agent import ResearchAgent
from agents.fundamental_agent import FundamentalAgent
from agents.technical_agent import TechnicalAgent
from agents.sentiment_agent import SentimentAgent
from agents.risk_agent import RiskAgent
from agents.decision_agent import DecisionAgent


def banner(text):
    print("\n" + "=" * 64)
    print(f"  {text}")
    print("=" * 64)


def main():
    banner("AI EQUITY ANALYST  —  daily run")
    print(f"Date        : {dt.date.today().isoformat()}")
    print(f"Gemini login: {auth_used}  ({'connected' if client else 'NOT CONNECTED'})")
    print(f"Data mode   : {settings.data_mode}")
    print(f"Model       : {settings.model}")

    if client is None:
        raise RuntimeError(
            "Gemini is not configured — check GOOGLE_AUTH_MODE, GOOGLE_API_KEY, "
            "or your Google Cloud ADC login in .env."
        )

    # 1. Load profile + memory --------------------------------------------------
    profile = load_profile(settings.profile_path)
    memory = MemoryLayer()
    print(f"Memory      : {memory.backend} backend")

    universe = resolve_universe(settings.universe)
    print(f"Universe    : {len(universe)} stocks ({settings.universe})")

    # 2. Research Agent scans everything ---------------------------------------
    banner("STEP 1 — Research Agent scans the market")
    bundles = market_data.get_bundles(universe)
    activity = market_data.get_activity(bundles)

    research = ResearchAgent()
    scan = research.run(activity, watchlist_size=min(5, len(universe)))
    watchlist = scan["watchlist"]
    for sym in watchlist:
        print(f"  • {sym}: {scan['notes'].get(sym, '')}")

    # 3. Specialist agents study each watchlist stock --------------------------
    banner("STEP 2 — Specialist agents analyse the watchlist")
    fundamental, technical = FundamentalAgent(), TechnicalAgent()
    sentiment, risk = SentimentAgent(), RiskAgent()

    analyses = []
    for sym in watchlist:
        b = bundles[sym]
        print(f"\n  Analysing {sym} ...")
        f = fundamental.run(sym, b["fundamentals"])
        t = technical.run(sym, b["technicals"])
        s = sentiment.run(sym, b["headlines"])
        r = risk.run(sym, b["risk_inputs"])
        print(f"    Fundamental {f['score']}/10 | Technical {t['score']}/10 "
              f"| Sentiment {s['score']}/10 | Risk {r['risk_score']}/10")
        analyses.append({
            "symbol": sym,
            "fundamental": f,
            "technical": t,
            "sentiment": s,
            "risk": r,
            "research_note": scan["notes"].get(sym, ""),
        })

    # 4. Recall memory ----------------------------------------------------------
    memory_notes = []
    for sym in watchlist:
        for note in memory.recall(sym):
            memory_notes.append(f"{sym}: {note}")

    # 5. Decision Agent decides -------------------------------------------------
    banner("STEP 3 — Decision Agent writes the shortlist")
    decision = DecisionAgent()
    result = decision.run(analyses, profile, memory_notes, top_n=settings.top_n)

    # 6. Store memory + build the alert ----------------------------------------
    for a in analyses:
        note = (f"F{a['fundamental']['score']}/T{a['technical']['score']}/"
                f"S{a['sentiment']['score']}/Risk{a['risk']['risk_score']}")
        memory.store(a["symbol"], note)

    alert = format_alert(result)
    banner("STEP 4 — Final output")
    telegram_alert.send(alert)

    # Save a copy to disk for your records.
    out_path = f"output/analysis_{dt.date.today().isoformat()}.json"
    with open(out_path, "w") as fp:
        json.dump({"profile": profile, "analyses": analyses, "result": result}, fp, indent=2)
    print(f"\nSaved full analysis to {out_path}")


def format_alert(result: dict) -> str:
    lines = ["*AI Equity Analyst — Today's Shortlist*", ""]
    for i, pick in enumerate(result.get("shortlist", []), 1):
        lines.append(f"{i}. *{pick['symbol']}*  ({pick.get('conviction','?')} conviction)")
        lines.append(f"   {pick.get('thesis','')}")
        lines.append(f"   ⚠ Risk: {pick.get('key_risk','')}")
        lines.append("")
    if result.get("summary"):
        lines.append("_" + result["summary"] + "_")
    lines.append("")
    lines.append("Not investment advice. Do your own research.")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
