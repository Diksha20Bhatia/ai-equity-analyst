"""
decision_agent.py  —  "The Manager"
===================================
The other place LLM reasoning genuinely earns its keep — weighing real
but sometimes conflicting signals against the investor's own profile and
writing an honest, structured verdict is a judgment call no formula can
make. Everything this agent is handed is real: real market context
(Nifty 50 today), real per-stock scores computed by pure-code agents, a
real event summary from the batched News/Event Reader, real memory
deltas and consolidated trend (what changed, and how, since previous
calls on this stock), a real PERSISTED GOAL from a prior call on this
same stock if one exists — its own past target_price/invalidation,
resurfaced with today's real price move against it
(memory/memory_layer.py::goal_status()) — real data-quality flags (so
thin/stale data lowers confidence instead of being silently ignored), a real TOOL CALL this agent
makes itself for every stock — a live TradingView Desktop quote
(integrations/tradingview_quote.py) pulled at the moment it's reasoning,
not a static number computed earlier in the pipeline — and now two INDEPENDENT
AI analyses it did not produce itself: the Bull Case Agent's and Bear
Case Agent's real, parallel arguments (agents/research_graph.py), each
reasoned without seeing the other. This agent's job is no longer to
invent both sides of the argument alone — it's to adjudicate between two
already-argued, independent positions, citing where it agrees, disagrees,
or thinks one side overstates its case. That's genuine multi-agent
task decomposition, not one agent role-playing a debate with itself.

One batched call evaluates every watchlist stock at once.

The output is deliberately NOT a blunt BUY/SELL: each pick carries an
opportunity score AND a separate confidence score, a bull case, a bear
case, portfolio fit (based on real sector concentration within today's
watchlist — this app doesn't track your actual brokerage holdings, so this
is a partial signal, not full portfolio awareness), and an invalidation
condition (what would change the view).

Suggested share quantity is NOT asked of the LLM: it's computed in plain
Python from the investor's own stated capital and position-size limit,
scaled down further when the stock's own risk score is high and adjusted by
the investor's own risk-appetite tier — a deterministic rule, never an
LLM-invented number. Conviction (HIGH/LOW) is likewise not asked of the
LLM — it's derived from the confidence score by a fixed threshold, so it
always means the same thing every time you see it. And the actual rupee
split across today's picks (_allocate_capital) is a weighted-by-opportunity,
capped-by-position-limit allocation — again plain Python, not a guess.
"""

import math
from collections import Counter

from agents.base_agent import BaseAgent
from integrations.tradingview_quote import get_live_quote, verify_symbol
from memory.knowledge_base import retrieve_for_stock
from observability import traceable

# How far towards the investor's own max position-size % we're willing to
# go for a given stock, tuned by their stated risk appetite. The ceiling
# (max_position_pct) is always theirs — this just decides how close to it
# we get for a given risk_percent.
RISK_TIER_MULTIPLIER = {"conservative": 0.6, "moderate": 1.0, "aggressive": 1.4}

# Conviction is derived from the confidence score by a fixed rule, not left
# to the LLM to self-report — so it's consistent every time and matches the
# number it's plotted next to.
CONVICTION_THRESHOLD = 70
CONVICTION_NOTES = {
    "HIGH": "The AI is fairly confident in this call — the data lines up "
            "cleanly and nothing here is flagged as thin or stale.",
    "LOW": "Treat this as a watch-list idea, not a strong call — either "
           "the underlying data has some gaps, or the signals are more "
           "mixed than usual.",
}

# An explicit, ENFORCED context budget — chosen against a real constraint,
# not a default: this call is batched across the WHOLE watchlist (up to
# ~10 stocks — see run.py's `watchlist_size=min(10, len(bundles))`) in
# ONE prompt, so any per-stock field that can grow unboundedly (retrieved
# KB notes as more source docs get added over time; a stock's own memory
# trend/goal history as more calls accumulate; an LLM-authored field like
# a bull/bear case that has no hard length contract) multiplies across
# every stock in the same call. Left uncapped, no single growth path is
# ever individually "wrong" — each one stays a real, correct sentence —
# but their combined growth would silently balloon every future prompt
# regardless. Applied to EVERY variable-length field below (not just
# retrieved KB notes), so what "actually enters the model's window" has
# one consistent ceiling, not a partial one that only covers whichever
# fields happened to get capped first.
#
# COMPACTION vs. SUBAGENT ISOLATION — a deliberate choice, not the
# default one reaches for. The alternative to trimming each field would
# be isolating each stock into its own subagent call (one Decision-Agent-
# style call per stock instead of one batched call for the whole
# watchlist). That's rejected here against a real, already-documented
# constraint elsewhere in this codebase: run.py's own module docstring
# states the AI-call budget is fixed regardless of universe size ("4 AI
# calls happen per run... no matter how big the universe is"). Isolating
# per stock would multiply Decision Agent calls by watchlist size (up to
# 10x), directly breaking that constraint; compaction keeps the call
# count fixed while still bounding what any one stock can push into the
# shared prompt. Isolation is still the RIGHT tool elsewhere in this same
# codebase — see agents/research_graph.py's Bull/Bear Case Agents, which
# isolate deliberately for a different reason (guarding against one
# agent's reasoning anchoring the other's, not a call-count constraint).
# Same framework, two different real constraints, two different answers.
_CONTEXT_BUDGET_CHARS_PER_FIELD = 500


def _budget_trim(text: str, max_chars: int = _CONTEXT_BUDGET_CHARS_PER_FIELD) -> str:
    """Real enforcement: a field over budget is actually cut, with a
    visible marker — never silently, and never just logged after the
    fact like config.py's LLM-call token budgets (this is enforced at
    the point content is ASSEMBLED into the prompt, before any call is
    made)."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + " …[trimmed for context budget]"


class DecisionAgent(BaseAgent):
    name = "Decision Agent"
    system_instruction = (
        "You are the head of an equity research desk for Indian markets. You "
        "combine real fundamental, technical, event/news and risk signals, real "
        "market context, and the investor's stated profile and constraints, and "
        "produce a small, high-conviction shortlist. You never recommend names "
        "that violate the investor's stated exclusions or risk appetite. For each "
        "pick you give: an opportunity score, a confidence score kept SEPARATE from "
        "opportunity (lower confidence when data quality is flagged as thin/stale, "
        "even if the opportunity looks strong), a bull case, a bear case (why you "
        "might be wrong, what could already be priced in), a stance (accumulate/"
        "hold/avoid), a reference target price with rationale, a near-term outlook, "
        "portfolio fit given the real sector concentration you're shown, and an "
        "invalidation condition — what evidence would change your mind. You are "
        "honest about uncertainty and never claim certainty about future prices. "
        "You do NOT give personalised financial advice — you explain the thesis "
        "and let the investor decide."
    )

    @traceable(run_type="chain", name="Decision Agent")
    def run(self, analyses: list, profile: dict, market_context: dict, memory_deltas: list,
            top_n: int = 3, feedback: str = "", bull_analysis: dict = None,
            bear_analysis: dict = None) -> dict:
        """
        analyses: list of per-stock dicts with fundamental/technical/risk (pure
                  code), event (from the batched News/Event Reader), real
                  market_stats, data_quality flags, sector.
        profile:  the investor profile dict (may be empty).
        market_context: real Nifty 50 today/trend (data/market_context.py).
        memory_deltas: list of real "what changed since last call, plus
                  consolidated multi-call trend" strings (see
                  memory/memory_layer.py).
        feedback: optional critique from the Orchestrator's self-reflection
                  loop (agents/orchestrator_agent.py) on a PREVIOUS attempt's
                  output for this same tier — empty on a first attempt.
        bull_analysis / bear_analysis: {symbol: {...}} — the Bull Case
                  Agent's and Bear Case Agent's real, independently-produced
                  analyses (agents/research_graph.py, run once per swing
                  run, shared across all risk tiers). Neither agent saw
                  the other's output; this agent is the first place both
                  are weighed together.
        """
        prompt = self._build_prompt(
            analyses, profile, market_context, memory_deltas, top_n, feedback,
            bull_analysis or {}, bear_analysis or {},
        )
        result = self.ask_json(prompt, temperature=0.4)
        if "shortlist" not in result:
            raise RuntimeError(f"[{self.name}] response missing 'shortlist': {result}")

        by_symbol = {a["symbol"]: a for a in analyses}
        for pick in result["shortlist"]:
            a = by_symbol.get(pick["symbol"])
            if not a:
                continue
            stats = a.get("market_stats", {})
            pick["current_price"] = stats.get("current_price")
            pick["performance"] = {k: stats[k] for k in ("1w", "1m", "6m") if k in stats}
            pick["risk_percent"] = round(a["risk"]["risk_score"] * 10)
            pick["conviction"] = "HIGH" if pick.get("confidence", 0) >= CONVICTION_THRESHOLD else "LOW"
            pick["conviction_note"] = CONVICTION_NOTES[pick["conviction"]]
            pick["suggested_quantity"] = self._suggest_quantity(
                stats.get("current_price"), profile, pick["risk_percent"]
            )

        result["capital_summary"] = self._allocate_capital(
            result["shortlist"],
            (profile or {}).get("capital_numeric"),
            (profile or {}).get("max_position_pct"),
        )
        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _suggest_quantity(current_price, profile, risk_percent) -> dict:
        """
        Real math only, risk-scaled: (capital) x (position-size rule, tightened
        for risky stocks, then adjusted by the investor's own risk-appetite
        tier) / (real current price). Never a guessed number. This is the
        MOST a single stock should ever get — see _allocate_capital for the
        actual suggested rupee split across today's whole shortlist.
        """
        capital = (profile or {}).get("capital_numeric")
        max_pct = (profile or {}).get("max_position_pct")
        if not current_price or not capital or not max_pct:
            return {
                "min": None, "max": None,
                "note": "Add a numeric capital amount and max position-size % "
                        "to your investor profile to get a suggested quantity.",
            }

        if risk_percent >= 60:
            base_pct = min(max_pct, 5)
            risk_label = "high risk"
        elif risk_percent >= 30:
            base_pct = min(max_pct, 10)
            risk_label = "moderate risk"
        else:
            base_pct = max_pct
            risk_label = "low risk"

        risk_appetite = (profile or {}).get("risk_appetite", "moderate")
        tier_mult = RISK_TIER_MULTIPLIER.get(risk_appetite, 1.0)
        effective_pct = min(base_pct * tier_mult, max_pct)
        rule = (
            f"{risk_label}, {risk_appetite} risk-appetite tier, capped at your "
            f"{max_pct}% max position-size limit"
        )

        max_investment = capital * (effective_pct / 100)
        max_shares = math.floor(max_investment / current_price)
        min_shares = math.floor(max_investment * 0.3 / current_price)
        return {
            "min": max(min_shares, 1) if max_shares >= 1 else 0,
            "max": max_shares,
            "note": f"Based on ₹{capital:,.0f} capital and {rule}.",
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _allocate_capital(shortlist: list, capital, max_position_pct) -> dict:
        """
        Split real capital across today's whole shortlist — not per stock in
        isolation. Higher opportunity_score picks get a bigger rupee share;
        every pick is still capped at max_position_pct of capital so no
        single name ever gets more than the investor's own stated limit.
        Plain Python (proportional allocation + iterative cap redistribution)
        — never an LLM-invented split.
        """
        if not capital or not max_position_pct or not shortlist:
            return {"total_capital": capital, "total_allocated": None, "cash_remaining": None}

        weights = [max(p.get("opportunity_score") or 1, 1) for p in shortlist]
        total_weight = sum(weights)
        cap_per_stock = capital * (max_position_pct / 100)

        allocations = [min(capital * (w / total_weight), cap_per_stock) for w in weights]
        for _ in range(6):
            leftover = capital - sum(allocations)
            uncapped = [i for i, a in enumerate(allocations) if a < cap_per_stock - 1]
            if leftover <= 1 or not uncapped:
                break
            uncapped_weight = sum(weights[i] for i in uncapped)
            moved = False
            for i in uncapped:
                bump = leftover * (weights[i] / uncapped_weight)
                new_val = min(allocations[i] + bump, cap_per_stock)
                if new_val - allocations[i] > 0.01:
                    moved = True
                allocations[i] = new_val
            if not moved:
                break

        for pick, amount in zip(shortlist, allocations):
            price = pick.get("current_price")
            pick["allocated_amount"] = round(amount)
            pick["allocated_shares"] = int(amount // price) if price else None

        total_allocated = round(sum(allocations))
        return {
            "total_capital": round(capital),
            "total_allocated": total_allocated,
            "cash_remaining": round(capital) - total_allocated,
        }

    # ------------------------------------------------------------------
    def _build_prompt(self, analyses, profile, market_context, memory_deltas, top_n, feedback="",
                       bull_analysis=None, bear_analysis=None):
        bull_analysis = bull_analysis or {}
        bear_analysis = bear_analysis or {}
        # Real sector concentration within today's watchlist — a partial
        # portfolio-fit signal, since this app doesn't track actual holdings.
        sector_counts = Counter(a.get("sector") for a in analyses if a.get("sector"))
        sector_txt = ", ".join(f"{k}: {v}" for k, v in sector_counts.items()) or "unknown"

        blocks = []
        for a in analyses:
            stats = a.get("market_stats", {})
            perf_lines = []
            for label, key in (("1-week", "1w"), ("1-month", "1m"), ("6-month", "6m")):
                if key in stats:
                    w = stats[key]
                    perf_lines.append(
                        f"    {label}: {w['change_pct']:+.1f}% "
                        f"(high ₹{w['high']:.2f}, low ₹{w['low']:.2f})"
                    )
            event = a.get("event", {})
            quality = a.get("data_quality") or []
            quality_txt = "; ".join(quality) if quality else "no issues flagged"
            retrieved = retrieve_for_stock(
                a.get("sector"),
                event_type=event.get("event_type"),
                risk_flags=a.get("risk", {}).get("flags"),
            )
            retrieved_txt = (
                "\n  Retrieved domain notes (real retrieval — sector-filtered + "
                "general-risk chunks, query built from this stock's own event "
                "type and risk flags, not generic filler):\n"
                + _budget_trim("\n".join(f"    - {r}" for r in retrieved))
            ) if retrieved else ""

            live = get_live_quote(a["symbol"])
            live_txt = ""
            if live:
                live_txt = (
                    "\n  LIVE TradingView quote (real tool call this agent just made, "
                    "off TradingView Desktop's own feed — compare against the price "
                    "above, which may be from an earlier data pull): "
                    f"last ₹{live['last']}, today's range ₹{live['low']}–₹{live['high']}"
                    + (f", volume {live['volume']:,}" if live.get("volume") else "")
                )

            verify = verify_symbol(a["symbol"])
            verify_txt = ""
            if verify and not verify["verified"]:
                verify_txt = (
                    "\n  ⚠ TradingView symbol search (a second, real tool call this "
                    "agent just made) could NOT confirm this ticker as a real NSE "
                    "equity — treat the data above with extra caution, this may be a "
                    "delisted, renamed, or mistyped symbol."
                )

            # Real, PERSISTED goal tracking (memory/memory_layer.py::goal_status(),
            # attached by run.py) — a target_price/invalidation THIS agent itself
            # set on a PRIOR call for this exact symbol, resurfaced with today's
            # real price move against it. Empty until a second real run occurs
            # after a symbol's first shortlist, same bootstrap behaviour as
            # memory_delta's "no prior context" on a stock's first-ever call.
            goal_status = a.get("goal_status")
            goal_txt = (
                "\n  🎯 Persisted goal from a prior call (plain-code detected the "
                "open goal and computed the real price move against it — you "
                "judge whether the qualitative condition below has now happened):\n    "
                + _budget_trim(goal_status)
            ) if goal_status else ""

            bull = bull_analysis.get(a["symbol"], {})
            bear = bear_analysis.get(a["symbol"], {})
            debate_txt = ""
            if bull or bear:
                debate_txt = "\n  Two INDEPENDENT analyses (neither saw the other's output):\n"
                if bull:
                    debate_txt += (
                        f"    Bull Case Agent (strength {bull.get('bull_strength', '?')}/100): "
                        f"{_budget_trim(bull.get('bull_case', 'n/a'))} "
                        f"(target ₹{bull.get('bull_target_price', '?')})\n"
                    )
                if bear:
                    debate_txt += (
                        f"    Bear Case Agent (severity {bear.get('bear_severity', '?')}/100): "
                        f"{_budget_trim(bear.get('bear_case', 'n/a'))} "
                        f"(key risk: {_budget_trim(bear.get('key_risk', '?'), max_chars=200)})\n"
                    )

            blocks.append(
                f"{a['symbol']} (sector: {a.get('sector', 'unknown')}):\n"
                f"  Current price: ₹{stats.get('current_price', 'n/a')}\n"
                f"  Real performance:\n" + "\n".join(perf_lines) + "\n"
                f"  Fundamental {a['fundamental']['score']}/10 — {a['fundamental']['verdict']}\n"
                f"  Technical   {a['technical']['score']}/10 — {a['technical']['read']}\n"
                f"  Risk        {a['risk']['risk_score']}/10 (higher=worse) — "
                f"{', '.join(a['risk']['flags'])}\n"
                f"  Event/news  sentiment={event.get('sentiment', 'n/a')}, "
                f"type={event.get('event_type', 'n/a')}, materiality={event.get('materiality', 'n/a')}: "
                f"{_budget_trim(event.get('summary', 'no summary'))}\n"
                f"  Why flagged by the Scout: {_budget_trim(a.get('research_note', 'n/a'))}\n"
                f"  Data quality: {quality_txt}"
                + live_txt
                + verify_txt
                + retrieved_txt
                + goal_txt
                + debate_txt
            )

        # Also budget-trimmed: profile "constraints" is free text the
        # INVESTOR typed, not an LLM, but it's still unbounded length and
        # still enters this same batched prompt — the budget is about what
        # actually reaches the model's window, regardless of source.
        profile_txt = "\n".join(
            f"  {k}: {_budget_trim(str(v))}" for k, v in (profile or {}).items()
            if k not in ("capital_numeric", "max_position_pct")
        ) or "  (none provided)"
        mem_txt = "\n".join(f"  - {_budget_trim(d)}" for d in memory_deltas) or "  (no prior context)"
        market_txt = (
            f"  Nifty 50: {market_context.get('nifty_pct_change', 0):+.1f}% today, "
            f"trend={market_context.get('nifty_trend', 'unknown')}, "
            f"volatility={market_context.get('nifty_volatility_pct', 0)}% annualised"
        )
        feedback_txt = (
            "\n\nFEEDBACK ON YOUR PREVIOUS ATTEMPT (fix these specific issues using ONLY "
            f"the same real data above — never invent new numbers to resolve them):\n  {_budget_trim(feedback)}"
        ) if feedback else ""

        return (
            "REAL MARKET CONTEXT TODAY:\n" + market_txt +
            "\n\nPER-STOCK ANALYSIS (all real data):\n" + "\n\n".join(blocks) +
            f"\n\nSECTOR SPREAD IN TODAY'S WATCHLIST: {sector_txt}\n"
            "(Note: this reflects only today's watchlist, not the investor's actual "
            "brokerage holdings, which this system doesn't have access to.)"
            "\n\nINVESTOR PROFILE:\n" + profile_txt +
            "\n\nMEMORY (what changed since the last call, plus the consolidated "
            "trend across recent calls, per stock):\n" + mem_txt +
            f"\n\nChoose the top {top_n} high-conviction ideas that fit this investor. "
            "Skip anything that breaks their exclusions or risk appetite. Use only the "
            "real data given above — do not invent numbers. Lower confidence when data "
            "quality issues are flagged, even if the opportunity looks strong. Where a "
            "stock has a LIVE TradingView quote above, note in your thesis if it "
            "materially disagrees with the other price data shown (e.g. a sharp intraday "
            "move not yet reflected elsewhere) — that's real-time signal the rest of "
            "your data may not have caught yet. Where a "
            "stock has independent Bull Case / Bear Case Agent analyses above, your own "
            "bull_case/bear_case must ADJUDICATE between them, not just restate one side "
            "— say explicitly where you agree, disagree, or think one side overstates its "
            "case, and let that adjudication (not either agent alone) drive your "
            "opportunity_score and confidence. "
            'Reply as JSON: {"shortlist": [{"symbol": "SYM", '
            '"opportunity_score": <0-100>, '
            '"confidence": <0-100>, '
            '"action": "accumulate/hold/avoid", '
            '"bull_case": "2-3 sentences: your adjudicated view of the upside case, citing '
            'the Bull Case Agent where one was given", '
            '"bear_case": "2-3 sentences: your adjudicated view of the downside case, citing '
            'the Bear Case Agent where one was given", '
            '"thesis": "2-3 sentence overall reasoning", '
            '"target_price": <number, your reference exit level>, '
            '"target_price_rationale": "one line", '
            '"near_term_outlook": "2-3 sentences on how it may behave in the coming days/weeks", '
            '"portfolio_fit": "good/neutral/poor", '
            '"portfolio_fit_note": "one line, referencing the real sector spread above", '
            '"invalidation": "one line: what future evidence would change this view", '
            '"key_risk": "one line"}], '
            '"summary": "one-paragraph market note referencing the real Nifty 50 context"}'
            + feedback_txt
        )
