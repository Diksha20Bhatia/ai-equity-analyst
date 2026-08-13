"""
base_agent.py
=============
Every specialist agent inherits from BaseAgent.

It gives them ONE shared skill: "ask Gemini a question and get JSON back".
If Gemini is not configured, `ask_json` returns None so the child agent can
fall back to simple rule-based logic instead of crashing.
"""

import json
from config import client, settings


class BaseAgent:
    # Child classes override these two.
    name = "Base Agent"
    system_instruction = "You are a helpful financial analysis assistant."

    def ask_json(self, prompt: str, temperature: float = 0.3):
        """
        Send `prompt` to Gemini and parse the reply as JSON.
        Returns a dict on success, or None if Gemini is unavailable / errored.
        """
        if client is None:
            return None  # no credentials -> caller uses fallback

        try:
            from google.genai import types

            response = client.models.generate_content(
                model=settings.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=self.system_instruction,
                    temperature=temperature,
                    response_mime_type="application/json",
                ),
            )
            text = (response.text or "").strip()
            # Strip accidental ```json fences if the model adds them.
            if text.startswith("```"):
                text = text.strip("`")
                text = text[text.find("{"):] if "{" in text else text
            return json.loads(text)
        except Exception as e:  # noqa: BLE001
            print(f"[{self.name}] Gemini call failed, using fallback. ({e})")
            return None

    # Child classes implement this.
    def run(self, *args, **kwargs):
        raise NotImplementedError
