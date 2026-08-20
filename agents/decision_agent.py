"""
decision_agent.py  —  "The Manager"
===================================
The other place LLM reasoning genuinely earns its keep — weighing four
real but sometimes conflicting signals (fundamentals, chart, news event,
risk) against the investor's own profile and writing an honest, structured
verdict is a judgment call no formula can make. Everything this agent is
handed is real: real market context (Nifty 50 today), real per-stock
scores computed by pure-code agents, a real event summary from the batched
News/Event Reader, real memory deltas (what changed since the last call on
this stock), and real data-quality flags (so thin/stale data lowers
confidence instead of being silently ignored).

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
    def run(self, analyses: list, profile: dict, market_context: dict, memory_deltas: list, top_n: int = 3) -> dict:
        """
        analyses: list of per-stock dicts with fundamental/technical/risk (pure
                  code), event (from the batched News/Event Reader), real
                  market_stats, data_quality flags, sector.
        profile:  the investor profile dict (may be empty).
        market_context: real Nifty 50 today/trend (data/market_context.py).
        memory_deltas: list of real "what changed since last call" strings.
        """
        prompt = self._build_prompt(analyses, profile, market_context, memory_deltas, top_n)
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
    def _build_prompt(self, analyses, profile, market_context, memory_deltas, top_n):
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
                f"{event.get('summary', 'no summary')}\n"
                f"  Why flagged by the Scout: {a.get('research_note', 'n/a')}\n"
                f"  Data quality: {quality_txt}"
            )

        profile_txt = "\n".join(
            f"  {k}: {v}" for k, v in (profile or {}).items()
            if k not in ("capital_numeric", "max_position_pct")
        ) or "  (none provided)"
        mem_txt = "\n".join(f"  - {d}" for d in memory_deltas) or "  (no prior context)"
        market_txt = (
            f"  Nifty 50: {market_context.get('nifty_pct_change', 0):+.1f}% today, "
            f"trend={market_context.get('nifty_trend', 'unknown')}, "
            f"volatility={market_context.get('nifty_volatility_pct', 0)}% annualised"
        )

        return (
            "REAL MARKET CONTEXT TODAY:\n" + market_txt +
            "\n\nPER-STOCK ANALYSIS (all real data):\n" + "\n\n".join(blocks) +
            f"\n\nSECTOR SPREAD IN TODAY'S WATCHLIST: {sector_txt}\n"
            "(Note: this reflects only today's watchlist, not the investor's actual "
            "brokerage holdings, which this system doesn't have access to.)"
            "\n\nINVESTOR PROFILE:\n" + profile_txt +
            "\n\nMEMORY DELTAS (what changed since the last call on each stock):\n" + mem_txt +
            f"\n\nChoose the top {top_n} high-conviction ideas that fit this investor. "
            "Skip anything that breaks their exclusions or risk appetite. Use only the "
            "real data given above — do not invent numbers. Lower confidence when data "
            "quality issues are flagged, even if the opportunity looks strong. "
            'Reply as JSON: {"shortlist": [{"symbol": "SYM", '
            '"opportunity_score": <0-100>, '
            '"confidence": <0-100>, '
            '"action": "accumulate/hold/avoid", '
            '"bull_case": "2-3 sentences: what supports this opportunity", '
            '"bear_case": "2-3 sentences: why this view could be wrong, what could already be priced in", '
            '"thesis": "2-3 sentence overall reasoning", '
            '"target_price": <number, your reference exit level>, '
            '"target_price_rationale": "one line", '
            '"near_term_outlook": "2-3 sentences on how it may behave in the coming days/weeks", '
            '"portfolio_fit": "good/neutral/poor", '
            '"portfolio_fit_note": "one line, referencing the real sector spread above", '
            '"invalidation": "one line: what future evidence would change this view", '
            '"key_risk": "one line"}], '
            '"summary": "one-paragraph market note referencing the real Nifty 50 context"}'
        )
