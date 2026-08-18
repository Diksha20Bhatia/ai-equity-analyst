"""
research_agent.py  —  "The Scout"
=================================
Looks at the whole basket of stocks and picks the handful that are doing
something UNUSUAL today: big price moves, volume spikes, block deals.
Everything downstream only studies this short watchlist.
"""

from agents.base_agent import BaseAgent


class ResearchAgent(BaseAgent):
    name = "Research Agent"
    system_instruction = (
        "You are an equity market scout for Indian stocks. You spot unusual "
        "activity — abnormal volume, sharp price moves, block deals — and "
        "surface the most interesting names. You are concise and never invent data."
    )

    def run(self, universe: list, watchlist_size: int = 5) -> dict:
        """
        universe: list of dicts, each with keys like
            symbol, pct_change, volume_ratio, has_block_deal
        Returns: {"watchlist": [symbol, ...], "notes": {symbol: reason}}
        """
        # Build a compact table for the prompt.
        rows = [
            f"{s['symbol']}: change {s['pct_change']:+.1f}%, "
            f"volume {s['volume_ratio']:.1f}x avg, "
            f"block_deal={'yes' if s.get('has_block_deal') else 'no'}"
            for s in universe
        ]
        prompt = (
            "Here is today's activity across a basket of NSE stocks:\n"
            + "\n".join(rows)
            + f"\n\nPick the {watchlist_size} MOST unusual / noteworthy names. "
            'Reply as JSON: {"watchlist": ["SYM", ...], '
            '"notes": {"SYM": "one-line reason", ...}}'
        )

        result = self.ask_json(prompt)
        if "watchlist" not in result:
            raise RuntimeError(f"[{self.name}] response missing 'watchlist': {result}")
        return result
