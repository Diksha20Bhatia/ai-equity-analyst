"""
decision_agent.py  —  "The Manager"
===================================
Reads every specialist's report for every watchlist stock, folds in the
investor's profile and any relevant memory of past analysis, and writes ONE
final shortlist of the top-N high-conviction ideas with reasoning.
"""

from agents.base_agent import BaseAgent


class DecisionAgent(BaseAgent):
    name = "Decision Agent"
    system_instruction = (
        "You are the head of an equity research desk for Indian markets. You "
        "combine fundamental, technical, sentiment and risk views, respect the "
        "investor's profile and constraints, and produce a small, high-conviction "
        "shortlist with clear reasoning. You never recommend names that violate "
        "the investor's stated exclusions or risk appetite. You are honest about "
        "uncertainty. You do NOT give personalised financial advice — you explain "
        "the thesis and let the investor decide."
    )

    def run(self, analyses: list, profile: dict, memory_notes: list, top_n: int = 3) -> dict:
        """
        analyses: list of per-stock dicts, each holding the sub-agent outputs.
        profile:  the investor profile dict (may be empty).
        memory_notes: list of short strings recalled from past runs.
        """
        prompt = self._build_prompt(analyses, profile, memory_notes, top_n)
        result = self.ask_json(prompt, temperature=0.4)
        if "shortlist" not in result:
            raise RuntimeError(f"[{self.name}] response missing 'shortlist': {result}")
        return result

    # ------------------------------------------------------------------
    def _build_prompt(self, analyses, profile, memory_notes, top_n):
        blocks = []
        for a in analyses:
            blocks.append(
                f"{a['symbol']}:\n"
                f"  Fundamental {a['fundamental']['score']}/10 — {a['fundamental']['verdict']}\n"
                f"  Technical   {a['technical']['score']}/10 — {a['technical']['read']}\n"
                f"  Sentiment   {a['sentiment']['score']}/10 — {a['sentiment']['summary']}\n"
                f"  Risk        {a['risk']['risk_score']}/10 (higher=worse) — "
                f"{', '.join(a['risk']['flags'])}\n"
                f"  Why flagged: {a.get('research_note', 'n/a')}"
            )
        profile_txt = "\n".join(f"  {k}: {v}" for k, v in profile.items()) or "  (none provided)"
        mem_txt = "\n".join(f"  - {m}" for m in memory_notes) or "  (no prior context)"

        return (
            "PER-STOCK ANALYSIS:\n" + "\n\n".join(blocks) +
            "\n\nINVESTOR PROFILE:\n" + profile_txt +
            "\n\nMEMORY (past analysis context):\n" + mem_txt +
            f"\n\nChoose the top {top_n} high-conviction ideas that fit this investor. "
            "Skip anything that breaks their exclusions or risk appetite. "
            'Reply as JSON: {"shortlist": [{"symbol": "SYM", '
            '"conviction": "high/medium", "thesis": "2-3 sentence reasoning", '
            '"key_risk": "one line"}], "summary": "one-paragraph market note"}'
        )
