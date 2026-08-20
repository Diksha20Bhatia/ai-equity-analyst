"""
intraday_backtest_agent.py  —  "The Scorekeeper"
====================================================
ONE batched AI call, run only by intraday_backtest.py at end of day. Writes
a short factual post-mortem comparing today's HIGH-conviction intraday
picks to what actually happened by market close — historical analysis for
a running log reviewed later to judge which signal combinations are
working, NOT a live trading suggestion. States outcome facts only: target
hit / stop hit / neither. No advice, no "next time" language.
"""

from agents.base_agent import BaseAgent
from observability import traceable


class IntradayBacktestAgent(BaseAgent):
    name = "Scorekeeper"
    system_instruction = (
        "You write a short factual post-mortem comparing today's HIGH-conviction "
        "intraday picks to what actually happened by market close. This is for a "
        "running log reviewed later to judge which signal combinations are "
        "working — historical analysis, not a live trading suggestion. For each "
        "stock: state plainly whether the target was hit, the stop was hit, or "
        "neither (closed inside the range). If neither, say where it closed "
        "relative to entry, in %. One line per stock. No advice, no 'next time' "
        "language — just outcome facts. End with an aggregate summary line."
    )

    @traceable(run_type="chain", name="Scorekeeper")
    def run(self, rows: list) -> dict:
        if not rows:
            return {"log": [], "summary": "No HIGH-conviction picks with entry/stop/target to review today."}
        prompt = self._build_prompt(rows)
        result = self.ask_json(prompt, temperature=0.1)
        if "log" not in result or "summary" not in result:
            raise RuntimeError(f"[{self.name}] response missing 'log'/'summary': {result}")
        return result

    def _build_prompt(self, rows: list) -> str:
        blocks = [
            f"{r['symbol']}: conviction={r['conviction']}, entry={r['entry']}, "
            f"stop={r['stop']}, target={r['target']}, signals_at_pick=\"{r['signals_read']}\", "
            f"day_high={r['day_high']}, day_low={r['day_low']}, close={r['close']}"
            for r in rows
        ]
        return (
            "Today's HIGH-conviction intraday picks vs. real end-of-day outcome:\n\n"
            + "\n".join(blocks) +
            '\n\nReply as JSON: {"log": ["SYMBOL: outcome, detail", ...], '
            '"summary": "X of Y HIGH-conviction picks hit target, Z hit stop, W unresolved."}'
        )
