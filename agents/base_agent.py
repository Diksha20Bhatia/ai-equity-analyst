"""
base_agent.py
=============
Every specialist agent inherits from BaseAgent.

It gives them ONE shared skill: "ask the model a question and get JSON
back". Every call goes through config.call_llm() — Bodhi's LLM Gateway,
the single governed entry point for AI in this app; no provider SDK is
called directly here, and there is no alternate backend. If the gateway
isn't configured or the call fails, this raises instead of silently
degrading — callers see the real error.

Every real model call is traced to LangSmith (observability.py) when
LANGSMITH_API_KEY is set — the actual prompt, response, latency, and real
token usage for every AI call in the whole run, nested under whichever
agent made it. If tracing isn't configured, @traceable is a no-op and
nothing here changes.
"""

import json
from config import call_llm
import observability
from observability import traceable


class BaseAgent:
    # Child classes override these two.
    name = "Base Agent"
    system_instruction = "You are a helpful financial analysis assistant."

    @traceable(run_type="llm", name="LLM call")
    def ask_json(self, prompt: str, temperature: float = 0.3) -> dict:
        """Send `prompt` to the active LLM backend and parse the reply as JSON."""
        text, usage = call_llm(
            prompt=prompt,
            system_instruction=self.system_instruction,
            temperature=temperature,
            json_mode=True,
            agent=self.name,
        )
        self._trace_call_details(usage)
        # Strip accidental ```json fences if the model adds them.
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{"):] if "{" in text else text
        return json.loads(text)

    def _trace_call_details(self, usage: dict) -> None:
        """
        Best-effort: label this LLM run with the calling agent's name and
        attach the real token counts the gateway reports, so LangSmith
        shows actual cost/usage per call — never lets a tracing hiccup
        break the real agent call it's wrapping.
        """
        if not observability.enabled:
            return
        try:
            from langsmith import get_current_run_tree
            run = get_current_run_tree()
            if not run:
                return
            run.name = f"{self.name} — Bodhi LLM Gateway call"
            if usage:
                run.metadata["usage_metadata"] = {
                    "input_tokens": usage.get("prompt_tokens"),
                    "output_tokens": usage.get("completion_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                }
        except Exception:  # noqa: BLE001
            pass

    # Child classes implement this.
    def run(self, *args, **kwargs):
        raise NotImplementedError
