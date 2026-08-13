"""
fundamental_agent.py  —  "The Analyst"
======================================
Judges whether the BUSINESS behind a stock is healthy: revenue growth,
profit margins, debt, ROCE, and whether the valuation looks stretched.
Produces a 0-10 score and a short verdict per stock.
"""

from agents.base_agent import BaseAgent


class FundamentalAgent(BaseAgent):
    name = "Fundamental Agent"
    system_instruction = (
        "You are a fundamental equity analyst for Indian companies. You assess "
        "revenue growth, margins, debt, ROCE and valuation. You give a health "
        "score from 0 (weak) to 10 (excellent) and a one-line verdict."
    )

    def run(self, symbol: str, fundamentals: dict) -> dict:
        f = fundamentals
        prompt = (
            f"Company: {symbol}\n"
            f"Revenue growth YoY: {f['revenue_growth']:.1f}%\n"
            f"Operating margin: {f['operating_margin']:.1f}%\n"
            f"ROCE: {f['roce']:.1f}%\n"
            f"Debt-to-equity: {f['debt_to_equity']:.2f}\n"
            f"P/E ratio: {f['pe_ratio']:.1f}\n\n"
            "Assess the fundamental health. Reply as JSON: "
            '{"score": <0-10>, "verdict": "one line"}'
        )
        result = self.ask_json(prompt)
        if result and "score" in result:
            return {"symbol": symbol, **result}

        # ---- Fallback: simple weighted rule ----
        score = 5.0
        score += min(f["revenue_growth"] / 5, 2)        # growth helps
        score += min(f["roce"] / 10, 2)                 # returns help
        score += 1 if f["operating_margin"] > 15 else 0  # healthy margins
        score -= 2 if f["debt_to_equity"] > 1.5 else 0   # too much debt hurts
        score -= 1 if f["pe_ratio"] > 40 else 0          # very expensive
        score = max(0, min(10, round(score, 1)))
        verdict = (
            "Strong, growing business" if score >= 7
            else "Mixed fundamentals" if score >= 4
            else "Weak fundamentals"
        )
        return {"symbol": symbol, "score": score, "verdict": verdict}
