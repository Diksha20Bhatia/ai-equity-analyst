"""
sentiment_agent.py  —  "The News Reader" (Event Reader)
=========================================================
The one specialist agent that still needs real language understanding —
figuring out WHAT a headline means and how material it is can't be done
with a formula, unlike everything else in agents/. Two things keep this
AI call cheap:

1. Near-duplicate headlines (multiple publishers covering the same story)
   are clustered into ONE event, with a source count, before the LLM ever
   sees them — this both saves tokens and stops a story from seeming more
   important just because five outlets copied it.
2. ALL watchlist stocks are evaluated in a single batched call, not one
   call per stock.
"""

from difflib import SequenceMatcher

from agents.base_agent import BaseAgent
from data.text_safety import sanitize_for_prompt, wrap_untrusted
from observability import traceable


def _dedupe(headlines: list, threshold: float = 0.6) -> list:
    clusters = []
    for h in headlines:
        for c in clusters:
            if SequenceMatcher(None, h.lower(), c["text"].lower()).ratio() >= threshold:
                c["count"] += 1
                break
        else:
            clusters.append({"text": h, "count": 1})
    return clusters


class SentimentAgent(BaseAgent):
    name = "News/Event Reader"
    system_instruction = (
        "You are a financial news analyst for Indian equities. For each stock you "
        "are given deduplicated real headline clusters, with how many independent "
        "sources reported each. Extract what actually happened, classify the event "
        "type, judge sentiment, and assess how material the news appears. If there "
        "is no real news for a stock, say so plainly rather than inventing an event."
    )

    @traceable(run_type="chain", name="News/Event Reader")
    def run(self, headlines_by_symbol: dict) -> dict:
        """headlines_by_symbol: {symbol: [real headline, ...]}. One batched call.

        Real news headlines are external, untrusted text — a manipulated
        or adversarially-crafted headline is a real (if unlikely) risk for
        a model that reads them. Each headline is sanitized individually
        (see data/text_safety.py) before it's clustered, and the whole
        assembled block is wrapped with an unforgeable delimiter before it
        reaches the prompt — a headline containing literal frame-marker
        text can't close the untrusted block early."""
        blocks = []
        for symbol, headlines in headlines_by_symbol.items():
            clean = [sanitize_for_prompt(h, max_len=300, source="headlines") for h in headlines]
            clusters = _dedupe(clean)
            if not clusters:
                blocks.append(f"{symbol}: (no recent news found)")
                continue
            lines = [
                f'  - "{c["text"]}" ({c["count"]} source{"s" if c["count"] > 1 else ""})'
                for c in clusters
            ]
            blocks.append(f"{symbol}:\n" + "\n".join(lines))

        headlines_block = wrap_untrusted(
            "\n\n".join(blocks), max_len=8000, source="headlines",
        )
        prompt = (
            "Real, deduplicated news clusters for each stock:\n\n" + headlines_block +
            '\n\nFor EACH symbol, reply as JSON: {"SYM": {'
            '"sentiment": "positive/neutral/negative", '
            '"event_type": "earnings/contract/management_change/regulatory/legal/'
            'product/rating_change/macro/none/other", '
            '"materiality": "high/medium/low", '
            '"summary": "plain-English 1-2 sentence summary"}, ...}'
        )
        result = self.ask_json(prompt)
        missing = [s for s in headlines_by_symbol if s not in result]
        if missing:
            raise RuntimeError(f"[{self.name}] response missing symbols: {missing}")
        for sym, r in result.items():
            r["symbol"] = sym
        return result
