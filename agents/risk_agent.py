"""
risk_agent.py  —  "The Skeptic"
===============================
Pure code, ZERO AI calls. Expanded beyond liquidity to historical
volatility, beta vs the Nifty 50, maximum drawdown and overnight gap risk —
all real, free signals computed from price/volume history in
data/signals.py. Produces a 0-10 RISK score (higher = more risky) and
plain-English flags.

Promoter pledging / stake changes / earnings quality still need NSE
corporate filings data (paid providers) and aren't wired up — this agent
only reasons over what's actually real and free.
"""

from observability import traceable


class RiskAgent:
    name = "Risk Agent"

    @traceable(run_type="tool", name="Risk Agent")
    def run(self, symbol: str, risk_inputs: dict) -> dict:
        r = risk_inputs
        risk = 0.0
        flags = []

        liquidity = r.get("liquidity_cr", 0)
        if liquidity < 5:
            risk += 3
            flags.append(f"Thin liquidity (₹{liquidity:.1f} Cr avg daily turnover)")
        elif liquidity < 20:
            risk += 1

        vol = r.get("historical_volatility_pct", 0)
        if vol > 50:
            risk += 2.5
            flags.append(f"High historical volatility ({vol:.0f}% annualised)")
        elif vol > 30:
            risk += 1

        beta = r.get("beta_vs_nifty")
        if beta is not None:
            if beta > 1.5:
                risk += 1.5
                flags.append(f"High beta ({beta:.2f}) — amplifies market moves")
            elif beta < 0.5:
                risk -= 0.5

        dd = r.get("max_drawdown_pct", 0)
        if dd < -40:
            risk += 2
            flags.append(f"Worst drawdown over the last year: {dd:.0f}%")
        elif dd < -25:
            risk += 1

        gap = r.get("gap_pct", 0)
        if abs(gap) > 3:
            risk += 1
            flags.append(f"Large overnight gap today ({gap:+.1f}%)")

        risk = max(0, min(10, round(risk, 1)))
        if not flags:
            flags = ["No major red flags in the available real data"]

        return {"symbol": symbol, "risk_score": risk, "flags": flags}
