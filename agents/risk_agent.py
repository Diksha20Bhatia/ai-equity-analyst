"""
risk_agent.py  —  "The Skeptic"
===============================
Flags thin-liquidity risk from real traded turnover. Produces a 0-10 RISK
score, where HIGHER means MORE risky (the opposite direction to the others).

Promoter pledging / stake changes / earnings quality need NSE corporate
filings data (paid providers like Trendlyne/Screener.in) and aren't wired
up — rather than fabricate those numbers, this agent only reasons over
what's actually real.
"""

from agents.base_agent import BaseAgent


class RiskAgent(BaseAgent):
    name = "Risk Agent"
    system_instruction = (
        "You are a risk analyst for Indian equities. Based only on trading "
        "liquidity, you give a RISK score from 0 (very safe) to 10 (very risky) "
        "and list flags. Thin turnover means wider spreads and harder exits."
    )

    def run(self, symbol: str, risk_inputs: dict) -> dict:
        r = risk_inputs
        prompt = (
            f"Company: {symbol}\n"
            f"Avg daily turnover (Cr): {r['liquidity_cr']:.1f}\n\n"
            "Assess liquidity risk. Reply as JSON: "
            '{"risk_score": <0-10>, "flags": ["...", ...]}'
        )
        result = self.ask_json(prompt)
        if "risk_score" not in result:
            raise RuntimeError(f"[{self.name}] response missing 'risk_score' for {symbol}: {result}")
        return {"symbol": symbol, **result}
