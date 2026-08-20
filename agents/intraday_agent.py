"""
intraday_agent.py  —  "The Day Trader"
========================================
Pure code, ZERO AI calls. Scores each stock's REAL same-day price action —
opening-range breakout, position vs VWAP, volume surge, and momentum since
the open — using a fixed, mechanical rule set. This is a day-trading signal
only: it says nothing about the company's fundamentals, and it only means
anything while the market is open.

Conviction is a plain label, not an opinion — it's just whether a real
breakout level has held WITH volume confirming it:
  HIGH conviction = broke a real opening-range level, and volume backs it up
  LOW conviction  = no clean break yet, or a move without volume behind it
"""

from observability import traceable

_CONVICTION_NOTES = {
    "HIGH": "a real breakout level held with volume backing it up — the "
            "kind of setup worth acting on with a tight stop-loss",
    "LOW": "no clean breakout with volume behind it yet — treat this as a "
           "name to watch, not a trade to take",
}


class IntradayAgent:
    name = "Intraday Agent"

    @traceable(run_type="tool", name="Intraday Agent")
    def run(self, symbol: str, bundle: dict) -> dict:
        b = bundle
        score = 0
        reasons = []

        if b.get("broke_range_high"):
            score += 3
            reasons.append("broke above its opening-range high")
        elif b.get("broke_range_low"):
            score -= 3
            reasons.append("broke below its opening-range low")

        if b.get("above_vwap"):
            score += 2
            reasons.append("trading above VWAP (buyers in control today)")
        else:
            score -= 1
            reasons.append("trading below VWAP (sellers in control today)")

        surge = b.get("volume_surge_ratio")
        volume_confirmed = False
        if surge is not None:
            if surge > 1.5:
                score += 2
                volume_confirmed = True
                reasons.append(f"volume running {surge:.1f}x the normal pace for this time of day")
            elif surge < 0.7:
                score -= 1
                reasons.append("volume is unusually thin today")

        momentum = b.get("momentum_pct", 0)
        if abs(momentum) >= 1:
            score += 1 if momentum > 0 else -1
            reasons.append(f"{momentum:+.1f}% since today's open")

        direction = "bullish" if score > 0 else "bearish" if score < 0 else "neutral"

        has_breakout = b.get("broke_range_high") or b.get("broke_range_low")
        conviction = "HIGH" if (has_breakout and volume_confirmed) else "LOW"

        entry, stop, target = self._levels(b, direction)

        return {
            "symbol": symbol,
            "direction": direction,
            "score": score,
            "conviction": conviction,
            "conviction_note": _CONVICTION_NOTES[conviction],
            "read": "; ".join(reasons) + "." if reasons else "no clear signal yet today.",
            "entry": entry,
            "stop_loss": stop,
            "target": target,
        }

    @staticmethod
    def _levels(b: dict, direction: str):
        """
        Mechanical entry/stop/target off the real opening range — a textbook
        ORB rule (target = breakout level projected by the opening range's
        own width), not an invented number. Only offered once a clean
        breakout direction exists.
        """
        or_high, or_low, last = b.get("opening_range_high"), b.get("opening_range_low"), b.get("last_price")
        if or_high is None or or_low is None:
            return last, None, None
        rng = or_high - or_low
        if direction == "bullish" and b.get("broke_range_high"):
            return round(or_high, 2), round(or_low, 2), round(or_high + rng, 2)
        if direction == "bearish" and b.get("broke_range_low"):
            return round(or_low, 2), round(or_high, 2), round(or_low - rng, 2)
        return last, None, None
