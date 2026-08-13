"""
sentiment_agent.py  —  "The News Reader"
========================================
Reads recent headlines and filings for a stock and decides whether the
surrounding mood is positive, neutral or negative. Produces a 0-10 score.
"""

from agents.base_agent import BaseAgent


class SentimentAgent(BaseAgent):
    name = "Sentiment Agent"
    system_instruction = (
        "You are a market sentiment analyst for Indian stocks. You read news "
        "headlines and company filings and judge the tone. You give a sentiment "
        "score from 0 (very negative) to 10 (very positive) and a one-line summary."
    )

    def run(self, symbol: str, headlines: list) -> dict:
        joined = "\n".join(f"- {h}" for h in headlines) if headlines else "- (no recent news)"
        prompt = (
            f"Company: {symbol}\nRecent headlines:\n{joined}\n\n"
            "Judge the overall sentiment. Reply as JSON: "
            '{"score": <0-10>, "summary": "one line"}'
        )
        result = self.ask_json(prompt)
        if result and "score" in result:
            return {"symbol": symbol, **result}

        # ---- Fallback: keyword scan ----
        positive = ("surge", "profit", "beats", "record", "growth", "upgrade",
                    "wins", "order", "expansion", "strong")
        negative = ("fall", "loss", "probe", "fraud", "downgrade", "weak",
                    "cut", "decline", "penalty", "resign")
        text = " ".join(headlines).lower()
        pos = sum(text.count(w) for w in positive)
        neg = sum(text.count(w) for w in negative)
        score = 5 + pos - neg
        score = max(0, min(10, score))
        summary = (
            "Positive news flow" if score >= 7
            else "Mixed / quiet" if score >= 4
            else "Negative news flow"
        )
        return {"symbol": symbol, "score": score, "summary": summary}
