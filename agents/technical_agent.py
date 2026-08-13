"""
technical_agent.py  —  "The Chart Reader"
=========================================
Looks only at price and volume behaviour: is the stock trending up, is it
breaking out, is the move backed by real volume? Produces a 0-10 score.
The indicators (trend, RSI, breakout) are computed in data/market_data.py;
this agent interprets them.
"""

from agents.base_agent import BaseAgent


class TechnicalAgent(BaseAgent):
    name = "Technical Agent"
    system_instruction = (
        "You are a technical analyst for Indian equities. You read trend "
        "strength, breakouts and volume confirmation. You give a technical "
        "score from 0 (bearish) to 10 (strong bullish setup) and a one-line read."
    )

    def run(self, symbol: str, technicals: dict) -> dict:
        t = technicals
        prompt = (
            f"Company: {symbol}\n"
            f"Price vs 50-day avg: {t['above_sma50']}\n"
            f"Price vs 200-day avg: {t['above_sma200']}\n"
            f"14-day RSI: {t['rsi']:.0f}\n"
            f"Near 52-week high: {t['near_high']}\n"
            f"Volume vs average: {t['volume_ratio']:.1f}x\n\n"
            "Assess the technical setup. Reply as JSON: "
            '{"score": <0-10>, "read": "one line"}'
        )
        result = self.ask_json(prompt)
        if result and "score" in result:
            return {"symbol": symbol, **result}

        # ---- Fallback: simple rule ----
        score = 5.0
        score += 1.5 if t["above_sma50"] else -1.5
        score += 1.5 if t["above_sma200"] else -1.5
        score += 1 if t["near_high"] else 0
        score += 1 if t["volume_ratio"] > 1.5 else 0
        if t["rsi"] > 70:
            score -= 1   # overbought
        elif t["rsi"] < 30:
            score -= 1   # oversold / weak
        score = max(0, min(10, round(score, 1)))
        read = (
            "Strong uptrend with volume" if score >= 7
            else "Neutral / choppy" if score >= 4
            else "Weak / downtrend"
        )
        return {"symbol": symbol, "score": score, "read": read}
