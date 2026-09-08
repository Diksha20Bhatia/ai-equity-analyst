"""
run.py  —  the conductor
========================
This is the one file you actually run:

    python run.py

It walks through the whole morning routine, in order:

  1. Load your settings, investor profile and memory.
  2. Fetch real market context (Nifty 50) once for the whole run.
  3. Research Agent (pure code) scans the basket and picks a short watchlist
     using a real anomaly score — no AI call.
  4. Fundamental / Technical / Risk agents (pure code) score the watchlist
     from real data — no AI calls.
  5. News/Event Reader makes ONE batched AI call to interpret real headlines
     for the whole watchlist at once.
  6. Memory reports what changed since the last call on each stock.
  7. Decision Agent runs inside a real Orchestrator loop (agents/
     orchestrator_agent.py, a LangGraph state machine) PER selected
     risk-appetite tier: propose a shortlist -> deterministic quality check
     -> an Orchestrator agent decides at runtime whether to accept it or
     send it back with specific feedback for one more attempt. A plain
     "moderate" investor gets 1 tier; conservative + aggressive gets 2 —
     each tier runs its own independent loop.
  8. Store today's scores into memory. Then, if BODHI_ENABLED=true, send
     today's real shortlist to a Bodhi workflow for risk verification and
     wait for its answer (see integrations/bodhi_risk_insights.py) — its
     per-symbol verdict (if it returns one) is included with the shortlist,
     and any symbol it flags avoid/high-risk is excluded from the
     TradingView candidate list. If Bodhi is off, unreachable, or returns
     no parseable verdict, nothing is filtered — every symbol stays a
     candidate exactly as before this integration existed. Today's results
     are also written as a new dated sheet in output/portfolio_tracker.xlsx
     (alerts/excel_report.py) — that's local record-keeping, not "sending
     out" anything, so it always happens.
  9. NOTHING is sent to Telegram or pushed to TradingView here. This
     project hands out real financial recommendations, so the finished
     shortlist is saved as a PENDING run (alerts/approval_gate.py) instead
     — a real pause. Publishing is a separate, deliberate step a human has
     to take against this specific run: `uv run python approve_run.py
     --latest`, or the dashboard's "Review & Approve" section.

To see how past picks have actually performed since they were listed,
run tracker/price_tracker.py separately — a standalone tool that appends
a new dated price column to every past sheet in that workbook. To get an
on-demand Bodhi Risk Insight without a full re-run, run
integrations/run_bodhi_risk_insight.py separately instead.

2 AI calls happen per run for a single risk tier, no matter how big the
universe is — see README.md for why. Selecting more than one risk-appetite
tier adds one more Decision Agent call per extra tier, since each tier
gets its own independently-reasoned shortlist.

For same-day intraday picks (opening-range breakout / VWAP / volume-surge),
run intraday_run.py instead — a separate, zero-AI, on-demand scan that only
means anything while the market is open.
"""

import json
import datetime as dt

import observability
from observability import traceable
from config import settings
from data import market_data, market_context
from data.nse_universe import resolve_universe
from memory.memory_layer import MemoryLayer
from profile.profile_loader import load_profile
from alerts import excel_report, approval_gate
from integrations import bodhi_risk_insights

from agents.research_agent import ResearchAgent
from agents.fundamental_agent import FundamentalAgent
from agents.technical_agent import TechnicalAgent
from agents.sentiment_agent import SentimentAgent
from agents.risk_agent import RiskAgent
from agents.decision_agent import DecisionAgent
from agents.orchestrator_agent import OrchestratorAgent, build_tier_graph


def banner(text):
    print("\n" + "=" * 64)
    print(f"  {text}")
    print("=" * 64)


@traceable(run_type="chain", name="AI Equity Analyst — swing run")
def main():
    banner("AI EQUITY ANALYST  —  daily run")
    print(f"Date        : {dt.date.today().isoformat()}")
    print(f"LLM backend : Bodhi LLM Gateway "
          f"({'connected' if settings.bodhi_llm_gateway_token else 'NOT CONFIGURED'})")
    print(f"Model       : {settings.bodhi_llm_model}")
    print(f"Tracing     : {'LangSmith (connected)' if observability.enabled else 'disabled (set LANGSMITH_API_KEY to enable)'}")

    if not settings.bodhi_llm_gateway_token:
        raise RuntimeError(
            "Bodhi LLM Gateway is not configured — set BODHI_LLM_GATEWAY_TOKEN "
            "(and BODHI_LLM_TRACE_USER_ID) in .env."
        )

    # 1. Load profile + memory --------------------------------------------------
    profile = load_profile(settings.profile_path)
    memory = MemoryLayer()
    print(f"Memory      : {memory.backend} backend")

    universe = resolve_universe(settings.universe)
    print(f"Universe    : {len(universe)} stocks ({settings.universe})")

    # 2. Real market context, fetched ONCE for the whole run --------------------
    banner("STEP 1 — Real market context (Nifty 50)")
    mkt = market_context.get_market_context()
    print(f"  Nifty 50: {mkt['nifty_pct_change']:+.1f}% today, trend={mkt['nifty_trend']}, "
          f"volatility={mkt['nifty_volatility_pct']}% annualised")

    # 3. Scout scans everything — pure code, zero AI calls -----------------------
    banner("STEP 2 — Research Agent (Scout) scans the market — 0 AI calls")
    bundles = market_data.get_bundles(universe, index_close=mkt["close_series"])
    if not bundles:
        raise RuntimeError("No real data could be fetched for any symbol in the universe.")
    skipped = len(universe) - len(bundles)
    if skipped:
        print(f"  ({skipped} symbol(s) skipped — insufficient real data, see [data] lines above)")
    activity = market_data.get_activity(bundles)

    research = ResearchAgent()
    scan = research.run(activity, market_pct_change=mkt["nifty_pct_change"],
                         watchlist_size=min(10, len(bundles)))
    watchlist = scan["watchlist"]
    for sym in watchlist:
        print(f"  • {sym}: {scan['notes'].get(sym, '')}")

    # 4. Fundamental / Technical / Risk — pure code, zero AI calls ---------------
    banner("STEP 3 — Fundamental / Technical / Risk agents — 0 AI calls")
    fundamental, technical, risk = FundamentalAgent(), TechnicalAgent(), RiskAgent()

    analyses = []
    for sym in watchlist:
        b = bundles[sym]
        f = fundamental.run(sym, b["fundamentals"])
        t = technical.run(sym, b["technicals"])
        r = risk.run(sym, b["risk_inputs"])
        print(f"  {sym}: Fundamental {f['score']}/10 | Technical {t['score']}/10 | Risk {r['risk_score']}/10")
        analyses.append({
            "symbol": sym,
            "sector": b.get("sector"),
            "fundamental": f,
            "technical": t,
            "risk": r,
            "research_note": scan["notes"].get(sym, ""),
            "market_stats": market_data.bundle_market_stats(b),
            "headlines": b["headlines"],
            "data_quality": b.get("data_quality", []),
        })

    # 5. News/Event Reader — ONE batched AI call for the whole watchlist --------
    banner("STEP 4 — News/Event Reader — 1 batched AI call")
    event_agent = SentimentAgent()
    headlines_by_symbol = {a["symbol"]: a["headlines"] for a in analyses}
    events = event_agent.run(headlines_by_symbol)
    for a in analyses:
        a["event"] = events.get(a["symbol"], {})
        print(f"  {a['symbol']}: {a['event'].get('sentiment', '?')} / "
              f"{a['event'].get('event_type', '?')} (materiality={a['event'].get('materiality', '?')})")

    # 6. Memory deltas — what changed since the last call on each stock ---------
    memory_deltas = []
    for a in analyses:
        current_scores = {
            "fundamental_score": a["fundamental"]["score"],
            "technical_score": a["technical"]["score"],
            "risk_score": a["risk"]["risk_score"],
        }
        memory_deltas.append(
            memory.build_delta(a["symbol"], a["market_stats"]["current_price"], current_scores)
        )

    # 7. Decision Agent — routed through a real Orchestrator loop PER tier ------
    # Not a fixed "call it once and trust it" step: a real LangGraph state
    # machine (agents/orchestrator_agent.py) runs the Decision Agent, checks
    # its own output for real issues, and lets an Orchestrator agent decide
    # at runtime whether to accept it or send it back with specific feedback
    # for another attempt — bounded retries, never a runaway loop.
    risk_tiers = _resolve_risk_tiers(profile)
    banner(f"STEP 5 — Decision Agent (orchestrated) — x {len(risk_tiers)} risk tier(s)")
    decision = DecisionAgent()
    orchestrator = OrchestratorAgent()
    tier_graph = build_tier_graph(decision, orchestrator)
    results_by_tier = {}
    for tier in risk_tiers:
        tier_profile = dict(profile)
        tier_profile["risk_appetite"] = tier
        tier_profile.pop("risk_appetites", None)
        final_state = tier_graph.invoke({
            "analyses": analyses, "profile": tier_profile, "market_context": mkt,
            "memory_deltas": memory_deltas, "top_n": settings.top_n,
            "decision_result": {}, "issues": [], "retries": 0, "max_retries": 1,
            "route_reason": "", "feedback": "", "next": "",
        })
        results_by_tier[tier] = final_state["decision_result"]
        print(f"  [{tier}] {len(results_by_tier[tier].get('shortlist', []))} pick(s) "
              f"— {final_state['retries']} retry(ies) — {final_state.get('route_reason', '')}")

    # 8. Store memory + send the alert -------------------------------------------
    # A symbol can get a different call at different risk tiers — memory keeps
    # all of them, tier-labelled, rather than picking just one.
    actions_by_symbol = {}
    for tier, result in results_by_tier.items():
        for p in result.get("shortlist", []):
            actions_by_symbol.setdefault(p["symbol"], []).append(f"{tier}:{p.get('action', 'n/a')}")
    for a in analyses:
        memory.store(a["symbol"], {
            "price": a["market_stats"]["current_price"],
            "fundamental_score": a["fundamental"]["score"],
            "technical_score": a["technical"]["score"],
            "risk_score": a["risk"]["risk_score"],
            "action": ", ".join(actions_by_symbol.get(a["symbol"], ["not_shortlisted"])),
        })

    banner("STEP 6 — Bodhi risk verification (optional)")
    bodhi_result = bodhi_risk_insights.verify_shortlist(results_by_tier, analyses)

    # TradingView only gets symbols Bodhi didn't flag as avoid/high-risk.
    # If Bodhi didn't run (disabled, unreachable, or no verdict was found),
    # flagged_symbols is empty and every shortlisted symbol is still a
    # candidate — unchanged from before this integration existed.
    tv_symbols = [s for s in actions_by_symbol.keys() if s not in bodhi_result["flagged_symbols"]]
    excel_report.write_run(results_by_tier, analyses)

    # STEP 7 — Pending approval, NOT auto-published --------------------------
    # This project hands out real financial recommendations, so nothing is
    # sent to Telegram or pushed to a live TradingView watchlist here. The
    # finished shortlist is saved as a PENDING run — a real pause — and a
    # human has to explicitly approve THIS specific run (approve_run.py, or
    # the dashboard's "Review & Approve" section) before anything goes out.
    # See alerts/approval_gate.py.
    banner("STEP 7 — Saved for approval (not sent yet)")
    alert = format_alert(results_by_tier, bodhi_result.get("verdicts"))
    pending_path = approval_gate.save_pending({
        "date": dt.date.today().isoformat(),
        "results_by_tier": results_by_tier,
        "alert_text": alert,
        "bodhi_ran": bodhi_result["ran"],
        "bodhi_report": bodhi_result.get("report"),
        "tv_symbols": tv_symbols,
    })
    print(f"  Pending: {pending_path}")
    print(f"  Nothing has been sent to Telegram or TradingView yet.")
    print(f"  Review and approve with:  uv run python approve_run.py --latest")
    print(f"  or reject with:           uv run python approve_run.py --latest --reject \"reason\"")

    # Save a copy to disk for your records (drop the raw price series first —
    # it's a pandas Series, not JSON-serialisable, and the dashboard/agents
    # only needed it transiently during this run).
    for a in analyses:
        a.pop("close_series", None)
    out_path = f"output/analysis_{dt.date.today().isoformat()}.json"
    with open(out_path, "w") as fp:
        json.dump(
            {"profile": profile, "market_context": {k: v for k, v in mkt.items() if k != "close_series"},
             "analyses": analyses, "results_by_tier": results_by_tier},
            fp, indent=2,
        )
    print(f"\nSaved full analysis to {out_path}")


def _resolve_risk_tiers(profile: dict) -> list:
    """
    The dashboard lets an investor pick MULTIPLE risk-appetite tiers (e.g.
    conservative + aggressive) to see how the same market looks at each
    level. Normalise whatever the profile has into a list of tiers to run.
    """
    tiers = (profile or {}).get("risk_appetites")
    if tiers:
        return list(dict.fromkeys(tiers))  # de-dupe, keep order
    single = (profile or {}).get("risk_appetite")
    return [single] if single else ["moderate"]


TIER_LABELS = {
    "conservative": "🟢 CONSERVATIVE profile",
    "moderate": "🟡 MODERATE profile",
    "aggressive": "🔴 AGGRESSIVE profile",
}
CONVICTION_EMOJI = {"HIGH": "🔥", "LOW": "👀"}


def format_alert(results_by_tier: dict, bodhi_verdicts: dict = None) -> str:
    lines = ["*AI Equity Analyst — Today's Shortlist*", ""]
    lines.append(
        "_🔥 HIGH CONVICTION = data lines up cleanly, worth acting on. "
        "👀 LOW CONVICTION = a watch-list idea, treat with caution._"
    )
    lines.append("")

    multi_tier = len(results_by_tier) > 1
    for tier, result in results_by_tier.items():
        if multi_tier:
            lines.append(f"━━━ {TIER_LABELS.get(tier, tier.upper())} ━━━")
            lines.append("")
        for i, pick in enumerate(result.get("shortlist", []), 1):
            conv = pick.get("conviction", "?")
            conv_emoji = CONVICTION_EMOJI.get(conv, "")
            lines.append(f"{i}. *{pick['symbol']}*  {conv_emoji} {conv} CONVICTION  "
                         f"(opportunity {pick.get('opportunity_score', '?')}/100, "
                         f"confidence {pick.get('confidence', '?')}%)")
            if pick.get("conviction_note"):
                lines.append(f"   _{pick['conviction_note']}_")
            lines.append(f"   Stance: *{pick.get('action', '?').upper()}*")
            if pick.get("current_price") is not None:
                lines.append(f"   Current price: ₹{pick['current_price']:.2f}")
            perf = pick.get("performance", {})
            for label, key in (("1w", "1w"), ("1m", "1m"), ("6m", "6m")):
                if key in perf:
                    p = perf[key]
                    lines.append(f"   {label}: {p['change_pct']:+.1f}% (high ₹{p['high']:.2f}, low ₹{p['low']:.2f})")
            lines.append(f"   Risk: {pick.get('risk_percent', '?')}%")
            lines.append(f"   Bull case: {pick.get('bull_case', pick.get('thesis', ''))}")
            if pick.get("bear_case"):
                lines.append(f"   Bear case: {pick['bear_case']}")
            if pick.get("near_term_outlook"):
                lines.append(f"   Near-term outlook: {pick['near_term_outlook']}")
            if pick.get("target_price") is not None:
                lines.append(
                    f"   Reference target: ₹{pick['target_price']:.2f} — {pick.get('target_price_rationale', '')}"
                )
            qty = pick.get("suggested_quantity", {})
            if qty.get("max") is not None:
                lines.append(f"   Position-size cap: {qty['min']}–{qty['max']} shares max ({qty.get('note', '')})")
            elif qty.get("note"):
                lines.append(f"   Position-size cap: n/a — {qty['note']}")
            if pick.get("allocated_amount") is not None:
                shares_txt = f" (~{pick['allocated_shares']} shares)" if pick.get("allocated_shares") else ""
                lines.append(f"   💰 Suggested allocation today: ₹{pick['allocated_amount']:,}{shares_txt}")
            if pick.get("portfolio_fit"):
                lines.append(f"   Portfolio fit: {pick['portfolio_fit']} — {pick.get('portfolio_fit_note', '')}")
            if pick.get("invalidation"):
                lines.append(f"   What would change this view: {pick['invalidation']}")
            lines.append(f"   ⚠ Key risk: {pick.get('key_risk', '')}")
            bodhi_v = (bodhi_verdicts or {}).get(pick["symbol"])
            if bodhi_v:
                lines.append(f"   🧠 Bodhi verdict: *{bodhi_v['verdict'].upper()}* — {bodhi_v['notes']}")
            lines.append("")

        cap = result.get("capital_summary", {})
        if cap.get("total_allocated") is not None:
            lines.append(
                f"   Capital split: ₹{cap['total_allocated']:,} deployed of "
                f"₹{cap['total_capital']:,} (₹{cap['cash_remaining']:,} left in cash)"
            )
            lines.append("")

        if result.get("summary"):
            lines.append("_" + result["summary"] + "_")
        lines.append("")

    lines.append("⚠️ Not investment advice — AI-generated analysis for informational "
                  "purposes only. Do your own research and consult a SEBI-registered "
                  "advisor before investing.")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
