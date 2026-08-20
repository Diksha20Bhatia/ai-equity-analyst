"""
research_agent.py  —  "The Scout"
=================================
Pure code, ZERO AI calls. Ranks every stock in the universe by a real
anomaly score built entirely from real signals: how far it moved relative
to the Nifty 50 (not in isolation), how unusual that move is for THIS
specific stock (a volatility z-score), volume, 20-day breakouts, overnight
gaps and block deals. The top N most anomalous names go on to deeper
(still mostly code) analysis. "Unusual" has an exact mathematical
definition here — there's nothing for an LLM to judge at this stage.
"""

from observability import traceable


class ResearchAgent:
    name = "Research Agent"

    @traceable(run_type="tool", name="Research Agent (Scout)")
    def run(self, activity: list, market_pct_change: float, watchlist_size: int = 5) -> dict:
        scored = []
        for a in activity:
            relative_move = round(a["pct_change"] - market_pct_change, 2)
            score = (
                abs(relative_move) * 1.0
                + abs(a.get("volatility_anomaly_z", 0)) * 1.5
                + max(a["volume_ratio"] - 1, 0) * 2
                + (5 if a.get("breakout_20d") else 0)
                + (5 if a.get("has_block_deal") else 0)
                + abs(a.get("gap_pct", 0)) * 1.5
            )
            scored.append((round(score, 2), relative_move, a))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:watchlist_size]

        watchlist = [a["symbol"] for _, _, a in top]
        notes, anomaly_scores = {}, {}
        for score, relative_move, a in top:
            bits = [
                f"{a['pct_change']:+.1f}% (vs Nifty {relative_move:+.1f}%)",
                f"{a['volume_ratio']:.1f}x volume",
            ]
            if abs(a.get("volatility_anomaly_z", 0)) >= 2:
                bits.append(f"{a['volatility_anomaly_z']:+.1f}σ move — unusual for this stock")
            if a.get("breakout_20d"):
                bits.append("20-day breakout")
            if a.get("has_block_deal"):
                bits.append("block deal")
            if abs(a.get("gap_pct", 0)) >= 1:
                bits.append(f"{a['gap_pct']:+.1f}% overnight gap")
            notes[a["symbol"]] = ", ".join(bits)
            anomaly_scores[a["symbol"]] = score

        return {"watchlist": watchlist, "notes": notes, "anomaly_scores": anomaly_scores}
