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
  7. Decision Agent makes ONE batched AI call PER selected risk-appetite
     tier (a plain "moderate" investor gets 1 call; someone who selected
     conservative + aggressive gets 2, one shortlist per tier).
  8. Store today's scores into memory. Then, if BODHI_ENABLED=true, send
     today's real shortlist to a Bodhi workflow for risk verification and
     wait for its answer (see integrations/bodhi_risk_insights.py) — its
     per-symbol verdict (if it returns one) is shown inline in the
     Telegram alert, and any symbol it flags avoid/high-risk is left out
     of the TradingView push (Telegram still shows every pick either way).
     Finally: send the Telegram alert, (if TRADINGVIEW_WATCHLIST_ENABLED=
     true) push the remaining shortlisted symbols into your real
     TradingView watchlist, and write today's results as a new dated
     sheet in output/portfolio_tracker.xlsx — see
     alerts/tradingview_watchlist.py and alerts/excel_report.py. If Bodhi
     is off, unreachable, or returns no parseable verdict, nothing is
     filtered — every symbol goes to TradingView exactly as before this
     integration existed.

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
from alerts import telegram_alert, tradingview_watchlist, excel_report
from integrations import bodhi_risk_insights

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


@traceable(run_type="chain", name="AI Equity Analyst — swing run")
def main():
    banner("AI EQUITY ANALYST  —  daily run")
    print(f"Date        : {dt.date.today().isoformat()}")
    if settings.use_bodhi_llm_gateway:
        print(f"LLM backend : Bodhi LLM Gateway "
              f"({'connected' if settings.bodhi_llm_gateway_token else 'NOT CONFIGURED'})")
        print(f"Model       : {settings.bodhi_llm_model}")
        if not settings.bodhi_llm_gateway_token:
            raise RuntimeError(
                "Bodhi LLM Gateway is not configured — set BODHI_LLM_GATEWAY_TOKEN "
                "(and BODHI_LLM_TRACE_USER_ID) in .env, or set USE_BODHI_LLM_GATEWAY=false "
                "to use Gemini instead."
            )
    else:
        vertex_ready = bool(settings.google_cloud_project) and (
            settings.google_auth_mode == "adc"
            or (settings.google_auth_mode == "auto" and settings.google_genai_use_vertexai)
        )
        api_key_ready = bool(settings.google_api_key)
        configured = vertex_ready or (settings.google_auth_mode != "adc" and api_key_ready)
        mode_label = "adc/vertex" if vertex_ready else ("token/api_key" if api_key_ready else settings.google_auth_mode)
        print(f"LLM backend : Google Gemini ({mode_label}) "
              f"({'connected' if configured else 'NOT CONFIGURED'})")
        print(f"Model       : {settings.google_model}")
        if not configured:
            raise RuntimeError(
                "Gemini is not configured — set GOOGLE_API_KEY in .env (GOOGLE_AUTH_MODE=token), "
                "or GOOGLE_CLOUD_PROJECT with GOOGLE_AUTH_MODE=adc for Vertex AI, "
                "or set USE_BODHI_LLM_GATEWAY=true to route through Bodhi instead."
            )
    print(f"Tracing     : {'LangSmith (connected)' if observability.enabled else 'disabled (set LANGSMITH_API_KEY to enable)'}")

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

    # 7. Decision Agent — ONE batched AI call PER selected risk-appetite tier ---
    risk_tiers = _resolve_risk_tiers(profile)
    banner(f"STEP 5 — Decision Agent — 1 batched AI call x {len(risk_tiers)} risk tier(s)")
    decision = DecisionAgent()
    results_by_tier = {}
    for tier in risk_tiers:
        tier_profile = dict(profile)
        tier_profile["risk_appetite"] = tier
        tier_profile.pop("risk_appetites", None)
        results_by_tier[tier] = decision.run(analyses, tier_profile, mkt, memory_deltas, top_n=settings.top_n)
        print(f"  [{tier}] {len(results_by_tier[tier].get('shortlist', []))} pick(s)")

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

    alert = format_alert(results_by_tier, bodhi_result.get("verdicts"))
    banner("STEP 7 — Final output")
    telegram_alert.send(alert)
    if bodhi_result["ran"]:
        telegram_alert.send("*🧠 Bodhi Risk Insight — full report*\n\n" + bodhi_result["report"])

    # TradingView only gets symbols Bodhi didn't flag as avoid/high-risk.
    # If Bodhi didn't run (disabled, unreachable, or no verdict was found),
    # flagged_symbols is empty and every shortlisted symbol still goes —
    # unchanged from before this integration existed.
    tv_symbols = [s for s in actions_by_symbol.keys() if s not in bodhi_result["flagged_symbols"]]
    tradingview_watchlist.add_symbols(tv_symbols)
    excel_report.write_run(results_by_tier, analyses)

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
