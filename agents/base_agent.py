"""
base_agent.py
=============
Every specialist agent inherits from BaseAgent.

It gives them ONE shared skill: "ask Gemini a question and get JSON back".
If Gemini isn't configured or the call fails, this raises instead of
silently degrading — callers see the real error.
"""

import json
from config import client, settings


class BaseAgent:
    # Child classes override these two.
    name = "Base Agent"
    system_instruction = "You are a helpful financial analysis assistant."

    def ask_json(self, prompt: str, temperature: float = 0.3) -> dict:
        """Send `prompt` to Gemini and parse the reply as JSON."""
        if client is None:
            raise RuntimeError(
                f"[{self.name}] Gemini is not configured. Check GOOGLE_AUTH_MODE, "
                "GOOGLE_API_KEY, or your Google Cloud ADC login in .env."
            )

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

    # Child classes implement this.
    def run(self, *args, **kwargs):
        raise NotImplementedError
