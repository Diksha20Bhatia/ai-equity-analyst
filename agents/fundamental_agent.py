"""
fundamental_agent.py  —  "The Analyst"
======================================
Pure code, ZERO AI calls. Every input is a real number from Yahoo Finance
(revenue growth, ROE, margins, debt, cash flow, valuation ratios). Turning
those into a 0-10 score is a fixed, transparent formula — not a judgment
call an LLM needs to make, and not fabricated data: the inputs are 100%
real, only the scoring RULE is deterministic code.

Valuation is judged against the STOCK'S OWN historical range (not an
absolute P/E number) using data/signals.py's historical_pe_context.
ROCE isn't exposed by any free source, so it's never asked about.
"""

from observability import traceable


class FundamentalAgent:
    name = "Fundamental Agent"

    @traceable(run_type="tool", name="Fundamental Agent")
    def run(self, symbol: str, fundamentals: dict) -> dict:
        f = fundamentals
        score = 5.0

        if "revenue_growth" in f:
            score += max(min(f["revenue_growth"] / 5, 2), -2)
        if "eps_growth" in f:
            score += max(min(f["eps_growth"] / 10, 1), -1)
        if "roe" in f:
            score += min(f["roe"] / 10, 1.5) if f["roe"] > 0 else -1
        if "operating_margin" in f:
            score += 1 if f["operating_margin"] > 15 else (-0.5 if f["operating_margin"] < 5 else 0)
        if "debt_to_equity" in f:
            score += -1.5 if f["debt_to_equity"] > 1.5 else (0.5 if f["debt_to_equity"] < 0.3 else 0)
        if "current_ratio" in f:
            score -= 1 if f["current_ratio"] < 1 else 0
        if "free_cashflow_cr" in f:
            score += 0.5 if f["free_cashflow_cr"] > 0 else -1

        valuation = "unknown (no historical valuation data)"
        if "valuation_vs_own_history" in f:
            pctl = f["valuation_vs_own_history"]["percentile_in_own_range"]
            if pctl < 25:
                valuation, adj = "cheap relative to its own history", 1
            elif pctl < 50:
                valuation, adj = "fair relative to its own history", 0.5
            elif pctl < 75:
                valuation, adj = "slightly expensive relative to its own history", -0.5
            else:
                valuation, adj = "expensive relative to its own history", -1
            score += adj

        score = max(0, min(10, round(score, 1)))
        business_quality = "Strong" if score >= 7 else "Moderate" if score >= 4 else "Weak"
        verdict = f"{business_quality} business quality; valuation looks {valuation}."

        return {
            "symbol": symbol,
            "score": score,
            "verdict": verdict,
            "business_quality": business_quality,
            "valuation": valuation,
        }
