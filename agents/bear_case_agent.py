"""
bear_case_agent.py  —  "The Skeptic's Voice"
===============================================
The independent, parallel counterpart to bull_case_agent.py — see that
file's docstring for the shared design rationale (genuine multi-agent
task decomposition: two agents reason over identical real data with no
coordination between them, then a separate Decision Agent weighs both).

This is a different role from agents/risk_agent.py: RiskAgent computes a
deterministic 0-10 risk SCORE from real volatility/drawdown/liquidity
numbers — pure math, zero AI. This agent's job is different — it argues
the qualitative downside CASE (why the opportunity might not play out,
what could already be priced in), which genuinely needs judgment a
formula can't produce, same as its Bull Case counterpart.

Tier-independent — runs ONCE per run, not once per selected risk tier.
"""

from agents.base_agent import BaseAgent
from observability import traceable
from schemas import BearCaseItem, ValidationError


class BearCaseAgent(BaseAgent):
    name = "Bear Case Agent"
    system_instruction = (
        "You are the Bear Case analyst — the skeptical voice — on an Indian equity "
        "research desk. For each stock you're given, using ONLY the real data "
        "provided, make the strongest honest case for why this could go wrong or why "
        "the opportunity might already be priced in. You do not know what any other "
        "analyst has concluded — reason independently from the real numbers alone. "
        "Never invent data you weren't given, and never manufacture risk that isn't "
        "actually supported by the real numbers — an honest 'this looks genuinely "
        "clean' is more useful than manufactured skepticism."
    )

    @traceable(run_type="chain", name="Bear Case Agent")
    def run(self, analyses: list) -> dict:
        """analyses: the same per-stock real data bundles run.py already has —
        no bull-case or decision-agent output is ever shown to this agent."""
        prompt = self._build_prompt(analyses)
        result = self.ask_json(prompt, temperature=0.4)
        missing = [a["symbol"] for a in analyses if a["symbol"] not in result]
        if missing:
            raise RuntimeError(f"[{self.name}] response missing symbols: {missing}")

        # Typed contract at this agent's handoff to the Decision Agent
        # (schemas.BearCaseItem) — a malformed entry is rejected here,
        # naming exactly which symbol/field is wrong, rather than
        # surfacing as a confusing KeyError deep inside decision_agent.py.
        validated = {}
        for symbol, item in result.items():
            try:
                validated[symbol] = BearCaseItem.model_validate(item).model_dump()
            except ValidationError as e:
                raise RuntimeError(f"[{self.name}] {symbol}: response doesn't match the expected shape: {e}") from e
        return validated

    def _build_prompt(self, analyses: list) -> str:
        blocks = []
        for a in analyses:
            stats = a.get("market_stats", {})
            event = a.get("event", {})
            quality = a.get("data_quality") or []
            perf_lines = []
            for label, key in (("1-week", "1w"), ("1-month", "1m"), ("6-month", "6m")):
                if key in stats:
                    w = stats[key]
                    perf_lines.append(f"    {label}: {w['change_pct']:+.1f}%")
            blocks.append(
                f"{a['symbol']} (sector: {a.get('sector', 'unknown')}):\n"
                f"  Current price: ₹{stats.get('current_price', 'n/a')}\n"
                + ("  Real performance:\n" + "\n".join(perf_lines) + "\n" if perf_lines else "")
                + f"  Risk        {a['risk']['risk_score']}/10 (higher=worse) — "
                f"{', '.join(a['risk']['flags']) or 'no flags'}\n"
                f"  Technical   {a['technical']['score']}/10 — {a['technical']['read']}\n"
                f"  Event/news  sentiment={event.get('sentiment', 'n/a')}, "
                f"materiality={event.get('materiality', 'n/a')}: {event.get('summary', 'no summary')}\n"
                f"  Data quality: {'; '.join(quality) if quality else 'no issues flagged'}"
            )

        return (
            "PER-STOCK REAL DATA (this is everything you get — no other analyst's "
            "conclusions are shown to you):\n" + "\n\n".join(blocks) +
            "\n\nFor EACH stock above, write the strongest HONEST bear case using only "
            "this real data — cite the specific numbers, don't just assert caution. "
            'Reply as JSON: {"SYMBOL": {'
            '"bear_case": "2-3 sentences, cite the specific real data", '
            '"key_risk": "one line, the single most concrete risk", '
            '"bear_severity": <0-100, how severe is this case genuinely, not just how '
            'cautious you can sound>}, ...}'
        )
