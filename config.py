"""
config.py
=========
Loads settings from the .env file. Every real AI call in this app goes
through call_llm() below.

By default, call_llm() talks to **Google Gemini** directly (via the
google-genai SDK, supporting both plain API-key auth and Vertex AI auth).
Set USE_BODHI_LLM_GATEWAY=true in .env to route every call through
**Bodhi's LLM Gateway** instead — a plain REST call to an OpenAI-compatible
endpoint, no provider SDK involved. Only one backend is active per run,
chosen once at startup; callers of call_llm() never need to know which one
answered.

If the active backend isn't configured, call_llm() raises a clear error
rather than silently degrading — a broken setup is never masked.
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
        # Which backend call_llm() uses. False (default) -> Google Gemini
        # direct. True -> Bodhi's LLM Gateway.
        self.use_bodhi_llm_gateway = _get_bool("USE_BODHI_LLM_GATEWAY", False)

        # --- Google Gemini (default LLM backend) ---
        # GOOGLE_AUTH_MODE decides HOW we log in to Gemini:
        #   auto  = try Google ADC / Vertex AI first, then fall back to API key
        #   adc   = use Google ADC / Vertex AI only  (recommended for Cloud)
        #   token = use Google AI Studio API key only (easiest to start)
        self.google_auth_mode = os.getenv("GOOGLE_AUTH_MODE", "auto").strip().lower()
        # TRUE -> route calls through Vertex AI (needs a Google Cloud project)
        # FALSE -> route calls through Google AI Studio (needs an API key)
        self.google_genai_use_vertexai = _get_bool("GOOGLE_GENAI_USE_VERTEXAI", False)
        self.google_cloud_project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
        self.google_cloud_location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1").strip()
        self.google_api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        # Optional custom bearer token if you route Vertex calls via your own
        # gateway, instead of application-default credentials.
        self.google_access_token = os.getenv("GOOGLE_ACCESS_TOKEN", "").strip()
        self.google_model = os.getenv("GOOGLE_MODEL", "gemini-2.5-pro").strip()

        # --- Bodhi's LLM Gateway (optional LLM backend) ---
        # Only used when USE_BODHI_LLM_GATEWAY=true. See https://developer.psbodhi.live.
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
        # Independent of USE_BODHI_LLM_GATEWAY above — this is a separate
        # Bodhi workflow call, not the LLM backend choice.
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

_gemini_client = None  # built lazily, once, the first time Gemini is actually called


def _build_vertex_client():
    """ADC / Vertex AI routing — needs a Google Cloud project. Uses
    application-default credentials unless GOOGLE_ACCESS_TOKEN overrides
    them with a pre-fetched bearer token."""
    from google import genai

    if not settings.google_cloud_project:
        raise RuntimeError(
            "GOOGLE_CLOUD_PROJECT is required for Vertex AI / ADC auth "
            "(GOOGLE_AUTH_MODE=adc/auto with GOOGLE_GENAI_USE_VERTEXAI=true)."
        )
    client_kwargs = {
        "vertexai": True,
        "project": settings.google_cloud_project,
        "location": settings.google_cloud_location,
    }
    if settings.google_access_token:
        import google.auth.credentials

        class _StaticTokenCredentials(google.auth.credentials.Credentials):
            """Wraps a pre-fetched access token as static, non-refreshing credentials."""

            def refresh(self, request):
                pass

        creds = _StaticTokenCredentials()
        creds.token = settings.google_access_token
        client_kwargs["credentials"] = creds
    return genai.Client(**client_kwargs)


def _build_api_key_client():
    """Google AI Studio routing — needs a plain API key."""
    from google import genai

    if not settings.google_api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY is required for Gemini token auth mode — get one at "
            "https://aistudio.google.com/apikey, set it in .env, or switch to "
            "GOOGLE_AUTH_MODE=adc (Vertex AI), or route through Bodhi instead "
            "(USE_BODHI_LLM_GATEWAY=true)."
        )
    return genai.Client(api_key=settings.google_api_key)


def _build_gemini_client():
    mode = settings.google_auth_mode

    if mode == "adc":
        return _build_vertex_client()

    if mode == "token":
        return _build_api_key_client()

    # auto (default): try ADC/Vertex first if configured for it, fall back
    # to the API key.
    if settings.google_genai_use_vertexai and settings.google_cloud_project:
        try:
            return _build_vertex_client()
        except Exception as e:  # noqa: BLE001
            observability.record_failure("gemini_client_init", f"Vertex AI init failed, falling back to API key: {e}")
    return _build_api_key_client()


def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = _build_gemini_client()
    return _gemini_client


def _call_gemini(prompt: str, system_instruction: str, temperature: float, json_mode: bool) -> tuple:
    from google.genai import types

    client = _get_gemini_client()
    config_kwargs = {"temperature": temperature}
    if system_instruction:
        config_kwargs["system_instruction"] = system_instruction
    if json_mode:
        config_kwargs["response_mime_type"] = "application/json"

    last_error = None
    for attempt in range(1, 4):
        try:
            response = client.models.generate_content(
                model=settings.google_model,
                contents=prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )
        except Exception as e:  # noqa: BLE001
            status = getattr(e, "code", None) or getattr(e, "status_code", None)
            last_error = str(e)
            observability.record_failure("gemini_call", last_error, attempt=attempt, model=settings.google_model)
            if status in _RETRYABLE_STATUS and attempt < 3:
                time.sleep(2 ** (attempt - 1))
                continue
            raise RuntimeError(f"Gemini call failed: {last_error}") from e

        text = (getattr(response, "text", None) or "").strip()
        if not text:
            observability.record_failure("gemini_call", "empty response text", attempt=attempt, model=settings.google_model)
            raise RuntimeError("Gemini returned an empty response.")

        usage_meta = getattr(response, "usage_metadata", None)
        usage = {}
        if usage_meta is not None:
            usage = {
                "prompt_tokens": getattr(usage_meta, "prompt_token_count", None),
                "completion_tokens": getattr(usage_meta, "candidates_token_count", None),
                "total_tokens": getattr(usage_meta, "total_token_count", None),
            }
        return text, usage

    raise RuntimeError(f"Gemini call failed after 3 attempts: {last_error}")


def _call_bodhi_llm_gateway(prompt: str, system_instruction: str, temperature: float, json_mode: bool) -> tuple:
    """A plain REST request to Bodhi's LLM Gateway (OpenAI-compatible
    chat/completions), never a provider SDK. Retries transient failures
    (429/5xx) with exponential backoff; fails fast on a real 4xx (bad
    request/auth)."""
    if not settings.bodhi_llm_gateway_token:
        raise RuntimeError(
            "Bodhi LLM Gateway is not configured — set BODHI_LLM_GATEWAY_TOKEN "
            "(and BODHI_LLM_TRACE_USER_ID) in .env, or set USE_BODHI_LLM_GATEWAY=false "
            "to use Gemini instead."
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


def call_llm(prompt: str, system_instruction: str = "", temperature: float = 0.3,
             json_mode: bool = True) -> tuple:
    """The one AI entry point every agent in this app calls.

    Routes to Google Gemini by default. Set USE_BODHI_LLM_GATEWAY=true in
    .env to route every call through Bodhi's LLM Gateway instead. Only one
    backend runs per process — chosen once, at import time, from settings.

    Returns (text, usage) — usage is a dict with prompt_tokens/
    completion_tokens/total_tokens, normalised the same way regardless of
    which backend answered.

    Raises RuntimeError if the active backend isn't configured, or every
    retry attempt is exhausted — this app never silently falls back to a
    fabricated answer when the AI path is broken."""
    if settings.use_bodhi_llm_gateway:
        return _call_bodhi_llm_gateway(prompt, system_instruction, temperature, json_mode)
    return _call_gemini(prompt, system_instruction, temperature, json_mode)


# Build once at import time so every module shares it.
settings = Settings()
