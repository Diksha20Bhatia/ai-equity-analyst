"""
bull_case_agent.py  —  "The Optimist"
========================================
An independent, parallel counterpart to bear_case_agent.py: given the SAME
real per-stock data, argues the upside case for every shortlisted stock at
once — one batched call, all stocks together, same batching discipline as
every other AI agent in this codebase.

Runs CONCURRENTLY with the Bear Case Agent (agents/research_graph.py) —
neither ever sees the other's output. That independence is the actual
point: two agents reasoning over identical real data with no coordination
between them, then a separate Decision Agent weighing both afterward, is
genuine task decomposition and multi-agent collaboration — not one agent
asked to argue both sides of its own conclusion in sequence.

Tier-independent: the bull case for a stock's real fundamentals doesn't
change based on an investor's risk appetite, so this runs ONCE per run,
not once per selected risk tier — the tier-specific weighing happens
downstream, in the Decision Agent.
"""

from agents.base_agent import BaseAgent
from observability import traceable
from schemas import BullCaseItem, ValidationError


class BullCaseAgent(BaseAgent):
    name = "Bull Case Agent"
    system_instruction = (
        "You are the Bull Case analyst on an Indian equity research desk. For each "
        "stock you're given, using ONLY the real data provided, make the strongest "
        "honest case for why it could be a good opportunity. You do not know what any "
        "other analyst has concluded — reason independently from the real numbers "
        "alone. Never invent data you weren't given, and never overstate a case the "
        "real numbers don't actually support — an honest 'the case here is weak' is "
        "more useful than a manufactured one."
    )

    @traceable(run_type="chain", name="Bull Case Agent")
    def run(self, analyses: list) -> dict:
        """analyses: the same per-stock real data bundles run.py already has —
        no bear-case or decision-agent output is ever shown to this agent."""
        prompt = self._build_prompt(analyses)
        result = self.ask_json(prompt, temperature=0.4)
        missing = [a["symbol"] for a in analyses if a["symbol"] not in result]
        if missing:
            raise RuntimeError(f"[{self.name}] response missing symbols: {missing}")

        # Typed contract at this agent's handoff to the Decision Agent
        # (schemas.BullCaseItem) — a malformed entry is rejected here,
        # naming exactly which symbol/field is wrong, rather than
        # surfacing as a confusing KeyError deep inside decision_agent.py.
        validated = {}
        for symbol, item in result.items():
            try:
                validated[symbol] = BullCaseItem.model_validate(item).model_dump()
            except ValidationError as e:
                raise RuntimeError(f"[{self.name}] {symbol}: response doesn't match the expected shape: {e}") from e
        return validated

    def _build_prompt(self, analyses: list) -> str:
        blocks = []
        for a in analyses:
            stats = a.get("market_stats", {})
            event = a.get("event", {})
            perf_lines = []
            for label, key in (("1-week", "1w"), ("1-month", "1m"), ("6-month", "6m")):
                if key in stats:
                    w = stats[key]
                    perf_lines.append(f"    {label}: {w['change_pct']:+.1f}%")
            blocks.append(
                f"{a['symbol']} (sector: {a.get('sector', 'unknown')}):\n"
                f"  Current price: ₹{stats.get('current_price', 'n/a')}\n"
                + ("  Real performance:\n" + "\n".join(perf_lines) + "\n" if perf_lines else "")
                + f"  Fundamental {a['fundamental']['score']}/10 — {a['fundamental']['verdict']}\n"
                f"  Technical   {a['technical']['score']}/10 — {a['technical']['read']}\n"
                f"  Event/news  sentiment={event.get('sentiment', 'n/a')}, "
                f"materiality={event.get('materiality', 'n/a')}: {event.get('summary', 'no summary')}\n"
                f"  Why flagged by the Scout: {a.get('research_note', 'n/a')}"
            )

        return (
            "PER-STOCK REAL DATA (this is everything you get — no other analyst's "
            "conclusions are shown to you):\n" + "\n\n".join(blocks) +
            "\n\nFor EACH stock above, write the strongest HONEST bull case using only "
            "this real data — cite the specific numbers, don't just assert optimism. "
            'Reply as JSON: {"SYMBOL": {'
            '"bull_case": "2-3 sentences, cite the specific real data", '
            '"bull_target_price": <number, your own reference upside level>, '
            '"bull_strength": <0-100, how strong is this case genuinely, not just how '
            'optimistic you can sound>}, ...}'
        )
