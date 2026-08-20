"""
intraday_narrator_agent.py  —  "The Narrator"
=================================================
Runs AFTER the mechanical scan has ranked and scored the top-N picks — it
only ever WRITES an explanation, it never reads back into score, rank, or
conviction. Those stay exactly as the rules engine (agents/intraday_agent.py)
set them; this agent has no ability to change them.

Two related, small write-only fields, combined into ONE batched call to
keep AI usage minimal (top-N stocks only, never the whole universe):

  narrative   - plain-English explanation of WHY the score came out the way
                it did, referencing only the structured signals it's given.
  pattern_tag - a descriptive label for the shape of today's bar-by-bar
                price action (clean_breakout / fakeout_risk / choppy_range /
                exhaustion / retest_in_progress), read from the real 5-min
                bar sequence.

Both are descriptive only — no "buy/sell/should" language, no invented
price levels or news.
"""

from agents.base_agent import BaseAgent
from observability import traceable

_PATTERN_TAGS = (
    "clean_breakout", "fakeout_risk", "choppy_range", "exhaustion", "retest_in_progress",
)


class IntradayNarratorAgent(BaseAgent):
    name = "Narrator"
    system_instruction = (
        "You are a same-day intraday market narrator for Indian equities. For "
        "each stock you do two things, using ONLY the data given — never invent "
        "price levels, news, or context:\n"
        "1) NARRATIVE: explain WHY the stock scored the way it did, in 2-3 "
        "plain-English, trader-readable sentences. Explicitly reference which "
        "of the signals fired (opening-range break, VWAP position, volume "
        "surge, momentum) and in what direction. If conviction is LOW, make "
        "clear what's missing (e.g. no volume confirmation, range not broken "
        "cleanly). Never use words like 'should', 'recommend', 'buy', 'sell' "
        "— describe what happened, not what to do next.\n"
        "2) PATTERN_TAG: classify the shape of today's 5-minute bar sequence "
        "into exactly one of: clean_breakout (decisive move through the "
        "opening-range level, sustained, rising volume), fakeout_risk (broke "
        "the level but is already showing signs of reversing back in), "
        "choppy_range (no clear directional move, whipsawing near the range), "
        "exhaustion (a strong earlier move now stalling/flattening on falling "
        "volume), retest_in_progress (broke out, pulled back to retest the "
        "level, holding or failing). Base this only on the bar sequence given; "
        "if it's ambiguous, choose choppy_range rather than guessing. You do "
        "not have opinions on whether to trade any stock, and you never "
        "suggest a different score, rank, or conviction than what is given — "
        "those are fixed upstream and are not yours to change."
    )

    @traceable(run_type="chain", name="Narrator")
    def run(self, picks: list) -> dict:
        """
        picks: list of dicts, each the merged intraday-agent read PLUS a
        "bars" key (list of {time, open, high, low, close, volume}) for
        that stock's real 5-min session so far. Top-N only.
        """
        if not picks:
            return {}
        prompt = self._build_prompt(picks)
        result = self.ask_json(prompt, temperature=0.3)
        if "notes" not in result:
            raise RuntimeError(f"[{self.name}] response missing 'notes': {result}")
        by_symbol = {n["symbol"]: n for n in result["notes"]}
        missing = [p["symbol"] for p in picks if p["symbol"] not in by_symbol]
        if missing:
            raise RuntimeError(f"[{self.name}] response missing symbols: {missing}")
        return by_symbol

    def _build_prompt(self, picks: list) -> str:
        blocks = []
        for p in picks:
            bar_lines = "\n".join(
                f"    {b['time']}  O{b['open']:.2f} H{b['high']:.2f} "
                f"L{b['low']:.2f} C{b['close']:.2f} V{b['volume']:.0f}"
                for b in p["bars"]
            )
            blocks.append(
                f"{p['symbol']}: score={p['score']}, conviction={p['conviction']}, "
                f"direction={p['direction']}\n"
                f"  signals: opening_range_break="
                f"{'high' if p.get('broke_range_high') else 'low' if p.get('broke_range_low') else 'none'}, "
                f"vwap_position={'above' if p.get('above_vwap') else 'below'}, "
                f"volume_surge_ratio={p.get('volume_surge_ratio')}, "
                f"momentum_pct={p.get('momentum_pct')}\n"
                f"  opening_range: high={p.get('opening_range_high')}, low={p.get('opening_range_low')}\n"
                f"  bars (time, OHLCV):\n{bar_lines}"
            )

        return (
            "Real intraday data for today's top picks:\n\n" + "\n\n".join(blocks) +
            '\n\nReply as JSON: {"notes": [{"symbol": "SYM", '
            '"narrative": "2-3 sentences", '
            f'"pattern_tag": "{"/".join(_PATTERN_TAGS)}", '
            '"pattern_confidence": "high/medium/low"}]}'
        )
