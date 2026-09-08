"""
config.py
=========
Loads settings from the .env file. Every real AI call in this app goes
through call_llm() below — a plain REST call to Bodhi's LLM Gateway, an
OpenAI-compatible endpoint. This is the ONLY model-calling path in the
codebase: no provider SDK (google-genai, openai, anthropic) is imported
anywhere, and there is no fallback or alternate route — Bodhi's gateway is
the single, governed entry point for every model call this app makes.

If the gateway isn't configured, call_llm() raises a clear error rather
than silently degrading — a broken setup is never masked.
"""

import os
import time

import requests
from dotenv import load_dotenv

import observability

load_dotenv()  # read .env into environment variables


def _get_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "y")


class Settings:
    """Plain container for every setting the app needs."""

    def __init__(self) -> None:
        # Bodhi's LLM Gateway — the one and only AI entry point every agent
        # call in this app goes through. See https://developer.psbodhi.live.
        self.bodhi_llm_gateway_url = os.getenv(
            "BODHI_LLM_GATEWAY_URL", "https://bodhi-llm-gateway.sandbox.psbodhi.live"
        ).strip().rstrip("/")
        self.bodhi_llm_gateway_token = os.getenv("BODHI_LLM_GATEWAY_TOKEN", "").strip()
        self.bodhi_llm_trace_user_id = os.getenv("BODHI_LLM_TRACE_USER_ID", "").strip()
        self.bodhi_llm_model = os.getenv("BODHI_LLM_MODEL", "gemini-2.5-pro").strip()

        self.top_n = int(os.getenv("TOP_N", "3"))
        # Either an index keyword (NIFTY50 / NIFTY500) or a comma-separated
        # list of NSE symbols — resolved to an actual symbol list by
        # data.nse_universe.resolve_universe().
        self.universe = os.getenv("UNIVERSE", "NIFTY50").strip()

        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.profile_path = os.getenv("PROFILE_PATH", "").strip()

        # Optional: push each run's shortlist into your real TradingView
        # watchlist (local-only, no credentials — see alerts/tradingview_watchlist.py).
        self.tradingview_watchlist_enabled = _get_bool("TRADINGVIEW_WATCHLIST_ENABLED", False)
        self.tradingview_cli_path = os.path.expanduser(
            os.getenv("TRADINGVIEW_CLI_PATH", "~/mcp-servers/tradingview-mcp/src/cli/index.js")
        )

        # Optional: send each run's real shortlist to a Bodhi Workflow
        # Builder flow (risk research + contrarian challenge) for an extra
        # AI-driven risk review on top of your own — see
        # integrations/bodhi_risk_insights.py and
        # integrations/bodhi_workflow/ for the exported workflow itself.
        self.bodhi_enabled = _get_bool("BODHI_ENABLED", False)
        self.bodhi_base_url = os.getenv("BODHI_BASE_URL", "").strip().rstrip("/")
        self.bodhi_pat_token = os.getenv("BODHI_PAT_TOKEN", "").strip()
        self.bodhi_task_id = os.getenv("BODHI_TASK_ID", "").strip()
        self.bodhi_workflow_id = os.getenv("BODHI_WORKFLOW_ID", "").strip()
        # Your Bodhi workflow's entry-point variable names (Start Node /
        # Workflow Variables — referenced in node prompts as {{$var[Name]}}).
        # Defaults match the exported workflow in integrations/bodhi_workflow/.
        self.bodhi_portfolio_variable = os.getenv("BODHI_PORTFOLIO_VARIABLE", "Investmentportfolio").strip()
        self.bodhi_query_variable = os.getenv("BODHI_QUERY_VARIABLE", "Query_for_risk_assessment").strip()
        self.bodhi_poll_interval = float(os.getenv("BODHI_POLL_INTERVAL", "5"))
        self.bodhi_timeout = float(os.getenv("BODHI_TIMEOUT", "300"))
        self.bodhi_trace_prefix = os.getenv("BODHI_TRACE_PREFIX", "ai-equity-analyst").strip()
        self.bodhi_risk_query = os.getenv(
            "BODHI_RISK_QUERY",
            "Assess this portfolio's exposure to current geopolitical conflicts, crude "
            "oil prices, US inflation and Fed policy, US-India tariffs, USD/INR moves, "
            "FII/DII flows, RBI policy, and global recession risk. Flag sector "
            "concentration and give a contrarian view where relevant.",
        ).strip()


_RETRYABLE_STATUS = (429, 500, 502, 503, 504)


def call_llm(prompt: str, system_instruction: str = "", temperature: float = 0.3,
             json_mode: bool = True) -> tuple:
    """The one AI entry point every agent in this app calls. A plain REST
    request to Bodhi's LLM Gateway (OpenAI-compatible chat/completions) —
    never a direct provider SDK, and never a fallback to one. Retries
    transient failures (429/5xx) with exponential backoff; fails fast on a
    real 4xx (bad request/auth).

    Returns (text, usage) — usage is the gateway's real reported token
    counts (prompt_tokens/completion_tokens/total_tokens), used by
    BaseAgent to attach real cost data to every traced call.

    Raises RuntimeError if the gateway isn't configured, or if every retry
    attempt is exhausted — this app never silently falls back to a
    fabricated answer, or to a different model backend, when the AI path
    is broken."""
    if not settings.bodhi_llm_gateway_token:
        raise RuntimeError(
            "Bodhi LLM Gateway is not configured — set BODHI_LLM_GATEWAY_TOKEN "
            "(and BODHI_LLM_TRACE_USER_ID) in .env."
        )

    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    body = {"model": settings.bodhi_llm_model, "messages": messages, "temperature": temperature}
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    headers = {
        "Content-Type": "application/json",
        "Authorization": settings.bodhi_llm_gateway_token,
    }
    if settings.bodhi_llm_trace_user_id:
        headers["trace_user_id"] = settings.bodhi_llm_trace_user_id

    url = f"{settings.bodhi_llm_gateway_url}/v1/chat/completions"

    last_error = None
    for attempt in range(1, 4):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=90)
        except requests.RequestException as e:
            last_error = f"network error: {e}"
            observability.record_failure("bodhi_llm_gateway", last_error, attempt=attempt, model=settings.bodhi_llm_model)
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))
                continue
            raise RuntimeError(f"Bodhi LLM Gateway unreachable after 3 attempts: {e}") from e

        if resp.status_code < 400:
            data = resp.json()
            try:
                text = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as e:
                observability.record_failure("bodhi_llm_gateway", f"unexpected response shape: {data}")
                raise RuntimeError(f"Bodhi LLM Gateway returned an unexpected response shape: {data}") from e
            return text.strip(), data.get("usage", {})

        last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
        if resp.status_code in _RETRYABLE_STATUS and attempt < 3:
            observability.record_failure(
                "bodhi_llm_gateway", last_error, attempt=attempt,
                status_code=resp.status_code, model=settings.bodhi_llm_model,
            )
            time.sleep(2 ** (attempt - 1))
            continue

        observability.record_failure(
            "bodhi_llm_gateway", resp.text[:300], attempt=attempt,
            status_code=resp.status_code, model=settings.bodhi_llm_model,
        )
        raise RuntimeError(f"Bodhi LLM Gateway call failed ({resp.status_code}): {resp.text[:500]}")

    raise RuntimeError(f"Bodhi LLM Gateway failed after 3 attempts: {last_error}")


# Build once at import time so every module shares it.
settings = Settings()
