"""
base_agent.py
=============
Every specialist agent inherits from BaseAgent.

It gives them ONE shared skill: "ask Gemini a question and get JSON back".
If Gemini isn't configured or the call fails, this raises instead of
silently degrading — callers see the real error.

Every real Gemini call is traced to LangSmith (observability.py) when
LANGSMITH_API_KEY is set — the actual prompt, response, latency, and real
token usage for every AI call in the whole run, nested under whichever
agent made it. If tracing isn't configured, @traceable is a no-op and
nothing here changes.
"""

import json
from config import client, settings
import observability
from observability import traceable


class BaseAgent:
    # Child classes override these two.
    name = "Base Agent"
    system_instruction = "You are a helpful financial analysis assistant."

    @traceable(run_type="llm", name="Gemini call")
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
        self._trace_call_details(response)
        text = (response.text or "").strip()
        # Strip accidental ```json fences if the model adds them.
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{"):] if "{" in text else text
        return json.loads(text)

    def _trace_call_details(self, response) -> None:
        """
        Best-effort: label this LLM run with the calling agent's name and
        attach the real token counts Gemini reports, so LangSmith shows
        actual cost/usage per call — never lets a tracing hiccup break the
        real agent call it's wrapping.
        """
        if not observability.enabled:
            return
        try:
            from langsmith import get_current_run_tree
            run = get_current_run_tree()
            if not run:
                return
            run.name = f"{self.name} — Gemini call"
            usage = getattr(response, "usage_metadata", None)
            if usage:
                run.metadata["usage_metadata"] = {
                    "input_tokens": getattr(usage, "prompt_token_count", None),
                    "output_tokens": getattr(usage, "candidates_token_count", None),
                    "total_tokens": getattr(usage, "total_token_count", None),
                }
        except Exception:  # noqa: BLE001
            pass

    # Child classes implement this.
    def run(self, *args, **kwargs):
        raise NotImplementedError
