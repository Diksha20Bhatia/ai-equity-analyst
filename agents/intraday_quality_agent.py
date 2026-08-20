"""
intraday_quality_agent.py  —  "The Data Sentinel"
=====================================================
A pre-scoring gate, not a trading signal. Most data-quality problems
(stale bars, zero volume, a single bar moving >10% with no volume behind
it) are 100% mechanical and already caught in plain code by
data/intraday_data.py — no ambiguity, no AI needed there.

The one thing code CAN'T tell on its own: whether a big, otherwise-clean
overnight gap is a real corporate action (split/bonus/rights) or a bad
print — both look identical in the raw numbers. That's a genuine judgment
call, so it gets ONE batched AI call across every stock that shows such a
gap today (often zero stocks, in which case this agent isn't even invoked).

A stock flagged usable=false is EXCLUDED from scoring entirely — never
scored and then explained away.
"""

from agents.base_agent import BaseAgent
from observability import traceable


class IntradayQualityAgent(BaseAgent):
    name = "Data Sentinel"
    system_instruction = (
        "You review same-day price gaps for Indian equities BEFORE they are "
        "scored by a mechanical intraday scan. You are a data-quality gate, "
        "not a trading signal. For each stock you're given only its previous "
        "close and today's open — you have no corporate-actions calendar and "
        "no news feed, so you cannot confirm whether a gap is a real split/"
        "bonus/rights action or a bad print. Say so plainly. Flag usable=false "
        "only when the gap is extreme enough (e.g. a clean round-ratio jump "
        "like ~50%, ~66%, ~80% consistent with a common split/bonus ratio, or "
        "an implausible multi-hundred-percent move) that scoring it as a real "
        "single-day price move would be clearly wrong. Otherwise usable=true "
        "with a note flagging the uncertainty — do not exclude a stock just "
        "because it moved a lot; big real moves happen."
    )

    @traceable(run_type="chain", name="Data Sentinel")
    def run(self, bundles: dict) -> dict:
        """
        bundles: {symbol: intraday_bundle} — only symbols with
        needs_gap_review=True should be passed in.
        """
        if not bundles:
            return {}
        prompt = self._build_prompt(bundles)
        result = self.ask_json(prompt, temperature=0.1)
        if "checks" not in result:
            raise RuntimeError(f"[{self.name}] response missing 'checks': {result}")
        by_symbol = {c["symbol"]: c for c in result["checks"]}
        missing = [s for s in bundles if s not in by_symbol]
        if missing:
            raise RuntimeError(f"[{self.name}] response missing symbols: {missing}")
        return by_symbol

    def _build_prompt(self, bundles: dict) -> str:
        blocks = [
            f"{sym}: previous_close={b['prev_close']}, today_open={b['day_open']}, "
            f"gap={b['gap_pct']:+.1f}%"
            for sym, b in bundles.items()
        ]
        return (
            "Review each stock's overnight gap:\n\n" + "\n".join(blocks) +
            '\n\nReply as JSON: {"checks": [{"symbol": "SYM", "usable": true/false, '
            '"flags": ["..."], "note": "one line, or empty"}]}'
        )
