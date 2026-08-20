"""
technical_agent.py  —  "The Chart Reader"
=========================================
Pure code, ZERO AI calls. Trend structure, RSI, relative strength vs the
Nifty 50, breakout/near-high status and volume confirmation are all real
numbers computed in data/signals.py from real price history. Turning them
into a score is a fixed, transparent rule set — reading a chart is exactly
the kind of "compute a number from other numbers" task that doesn't need
an LLM.
"""

from observability import traceable

_TREND_LABELS = {
    "strong_uptrend": "Strong uptrend",
    "possible_recovery": "Possible recovery",
    "downtrend": "Downtrend",
    "sideways": "Sideways",
}


class TechnicalAgent:
    name = "Technical Agent"

    @traceable(run_type="tool", name="Technical Agent")
    def run(self, symbol: str, technicals: dict) -> dict:
        t = technicals
        score = 5.0

        trend = t.get("trend_structure", "sideways")
        score += {"strong_uptrend": 2.5, "possible_recovery": 0.5, "downtrend": -2.5, "sideways": 0}[trend]

        rsi = t.get("rsi", 50)
        if rsi > 70:
            score -= 1  # overbought
        elif rsi < 30:
            score -= 0.5  # weak/oversold

        if t.get("near_high"):
            score += 1
        if t.get("volume_ratio", 1) > 1.5:
            score += 1

        rel = t.get("relative_strength_vs_nifty", {})
        if rel.get("1m", 0) > 0:
            score += 0.5
        if rel.get("3m", 0) > 0:
            score += 0.5

        score = max(0, min(10, round(score, 1)))
        trend_label = _TREND_LABELS[trend]
        read = (
            f"{trend_label}, RSI {rsi}, "
            f"{'near' if t.get('near_high') else 'off'} 52-week highs, "
            f"{t.get('volume_ratio', 1):.1f}x volume"
            + (f", {rel['1m']:+.1f}% vs Nifty over 1m" if "1m" in rel else "")
            + (f", ATR ₹{t['atr']:.2f} (typical daily range)" if "atr" in t else "")
            + "."
        )
        return {"symbol": symbol, "score": score, "read": read, "trend": trend_label}
