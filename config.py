"""
config.py
=========
Loads settings from the .env file, and is the ONE place every AI-calling
agent goes through: call_bodhi_llm() sends every model request through
Bodhi's LLM Gateway (an OpenAI-compatible endpoint, so no provider SDK is
called directly) — see agents/base_agent.py and profile/profile_loader.py,
the only two callers.

If the gateway isn't configured, call_bodhi_llm() raises a clear error
rather than degrading to rule-based logic or a direct provider call, so a
broken setup is never silently masked.
"""

import os
from dotenv import load_dotenv

load_dotenv()  # read .env into environment variables


def _get_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "y")


class Settings:
    """Plain container for every setting the app needs."""

    def __init__(self) -> None:
        # Bodhi LLM Gateway — the ONE governed entry point every AI call in
        # this app goes through (OpenAI-compatible /v1/chat/completions).
        # See https://developer.psbodhi.live (Bodhi LLM Gateway docs) for
        # the current model list and your own API token.
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

        # Optional: send each run's real shortlist to a Bodhi workflow (Risk
        # Analyst + Contrarian agents) for an independent second opinion
        # that gates the TradingView push — see integrations/bodhi_risk_insights.py.
        # A separate concern from the LLM Gateway above: this triggers a
        # whole Bodhi WORKFLOW (its own agents, its own tools), not a single
        # chat completion.
        self.bodhi_enabled = _get_bool("BODHI_ENABLED", False)
        self.bodhi_base_url = os.getenv("BODHI_BASE_URL", "").strip().rstrip("/")
        self.bodhi_pat_token = os.getenv("BODHI_PAT_TOKEN", "").strip()
        self.bodhi_task_id = os.getenv("BODHI_TASK_ID", "").strip()
        self.bodhi_workflow_id = os.getenv("BODHI_WORKFLOW_ID", "").strip()
        # Your Bodhi workflow's entry-point variable names (Start Node /
        # Workflow Variables — referenced in node prompts as {{$var[Name]}}).
        # Defaults match a confirmed-working workflow; Bodhi variable names
        # can't contain spaces, hence the run-together/underscore casing.
        self.bodhi_portfolio_variable = os.getenv("BODHI_PORTFOLIO_VARIABLE", "Investmentportfolio").strip()
        self.bodhi_query_variable = os.getenv("BODHI_QUERY_VARIABLE", "Query_for_risk_assessment").strip()
        # How the per-symbol loop is driven. Two supported shapes, both
        # producing the same decisions[] result:
        #   true  (default) -- this app calls the workflow ONCE PER SYMBOL.
        #                      Works with any single-stock workflow, and gives
        #                      one Bodhi run id per decision.
        #   false           -- ONE call with the whole shortlist as a JSON
        #                      array; the workflow's own Repeat node iterates
        #                      it. Needs the expanded workflow in
        #                      integrations/bodhi_workflow/ to be built and
        #                      verified in the canvas first.
        self.bodhi_per_symbol_runs = _get_bool("BODHI_PER_SYMBOL_RUNS", True)
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


def call_bodhi_llm(prompt: str, system_instruction: str = "", temperature: float = 0.3,
                    json_mode: bool = True) -> tuple:
    """
    The ONE place every AI call in this app goes through — Bodhi's LLM
    Gateway, an OpenAI-compatible /v1/chat/completions endpoint. No
    provider SDK (google-genai, openai, anthropic, ...) is called directly
    anywhere else in this codebase.

    Returns (text, usage) — usage is the gateway's real
    {"prompt_tokens", "completion_tokens", "total_tokens"} for cost/token
    tracking. Raises a clear RuntimeError if the gateway isn't configured
    or the call fails — never falls back to a direct provider call.
    """
    if not settings.bodhi_llm_gateway_token:
        raise RuntimeError(
            "Bodhi LLM Gateway is not configured. Set BODHI_LLM_GATEWAY_TOKEN "
            "(and BODHI_LLM_TRACE_USER_ID) in .env — see .env.example."
        )

    import time
    import requests
    import observability

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
    max_attempts = 3
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=120)
        except requests.RequestException as e:
            last_error = f"network error: {e}"
            observability.record_failure("bodhi_llm_gateway", last_error, attempt=attempt, model=settings.bodhi_llm_model)
            if attempt < max_attempts:
                time.sleep(2 ** (attempt - 1))  # 1s, 2s
                continue
            raise RuntimeError(f"Bodhi LLM Gateway unreachable after {max_attempts} attempts: {e}") from e

        # 5xx and 429 are transient — worth a retry. 4xx (auth, bad
        # request) won't fix itself by retrying, so fail fast instead.
        if resp.status_code == 200:
            break
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_attempts:
            last_error = f"{resp.status_code}: {resp.text[:300]}"
            observability.record_failure(
                "bodhi_llm_gateway", last_error, attempt=attempt,
                status_code=resp.status_code, model=settings.bodhi_llm_model,
            )
            time.sleep(2 ** (attempt - 1))
            continue

        # Either a non-retryable 4xx, or a retryable status on the final
        # attempt — either way, nothing left to do but fail loudly.
        observability.record_failure(
            "bodhi_llm_gateway", resp.text[:300], attempt=attempt,
            status_code=resp.status_code, model=settings.bodhi_llm_model,
        )
        raise RuntimeError(f"Bodhi LLM Gateway call failed ({resp.status_code}): {resp.text[:500]}")

    data = resp.json()
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        observability.record_failure("bodhi_llm_gateway", f"unexpected response shape: {data}")
        raise RuntimeError(
            f"Bodhi LLM Gateway returned an unexpected response shape: {data}"
        ) from e

    return text.strip(), data.get("usage", {})


# Build once at import time so every module shares them.
settings = Settings()
