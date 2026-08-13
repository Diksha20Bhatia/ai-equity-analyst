"""
risk_agent.py  —  "The Skeptic"
===============================
Actively hunts for RED FLAGS: promoter selling / pledging, weak earnings
quality, thin liquidity, operator-style volatility. Produces a 0-10 RISK
score, where HIGHER means MORE risky (the opposite direction to the others).
"""

from agents.base_agent import BaseAgent


class RiskAgent(BaseAgent):
    name = "Risk Agent"
    system_instruction = (
        "You are a risk analyst for Indian equities. You flag promoter pledging "
        "or selling, weak earnings quality, low liquidity and operator activity. "
        "You give a RISK score from 0 (very safe) to 10 (very risky) and list flags."
    )

    def run(self, symbol: str, risk_inputs: dict) -> dict:
        r = risk_inputs
        prompt = (
            f"Company: {symbol}\n"
            f"Promoter holding change: {r['promoter_change']:+.1f}%\n"
            f"Shares pledged: {r['pledge_pct']:.1f}%\n"
            f"Earnings quality (0-10): {r['earnings_quality']:.0f}\n"
            f"Avg daily turnover (Cr): {r['liquidity_cr']:.0f}\n\n"
            "Assess downside risk. Reply as JSON: "
            '{"risk_score": <0-10>, "flags": ["...", ...]}'
        )
        result = self.ask_json(prompt)
        if result and "risk_score" in result:
            return {"symbol": symbol, **result}

        # ---- Fallback: rule-based flags ----
        flags = []
        risk = 3.0
        if r["promoter_change"] < -1:
            flags.append("Promoters reducing stake")
            risk += 2
        if r["pledge_pct"] > 20:
            flags.append(f"{r['pledge_pct']:.0f}% shares pledged")
            risk += 2
        if r["earnings_quality"] < 5:
            flags.append("Weak earnings quality")
            risk += 2
        if r["liquidity_cr"] < 5:
            flags.append("Thin liquidity")
            risk += 1
        risk = max(0, min(10, round(risk, 1)))
        if not flags:
            flags = ["No major red flags"]
        return {"symbol": symbol, "risk_score": risk, "flags": flags}
