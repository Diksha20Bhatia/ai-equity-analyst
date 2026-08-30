"""
config.py
=========
Loads settings from the .env file and builds ONE shared Gemini client that
every agent reuses.

It understands three login modes (GOOGLE_AUTH_MODE):
    adc   -> Google Cloud / Vertex AI  (Application Default Credentials)
    token -> Google AI Studio API key
    auto  -> try adc first, fall back to token

If no valid Gemini credentials are found, the client is set to None. Agents
require a live client — they raise a clear error rather than degrading to
rule-based logic, so a broken setup is never silently masked.
"""

import os
from dotenv import load_dotenv

load_dotenv()  # read .env into environment variables


def _get_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "y")


class Settings:
    """Plain container for every setting the app needs."""

    def __init__(self) -> None:
        self.auth_mode = os.getenv("GOOGLE_AUTH_MODE", "auto").strip().lower()
        self.use_vertex = _get_bool("GOOGLE_GENAI_USE_VERTEXAI", True)
        self.project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
        self.location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1").strip()
        self.api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        self.access_token = os.getenv("GOOGLE_ACCESS_TOKEN", "").strip()
        self.model = os.getenv("GOOGLE_MODEL", "gemini-3.5-flash").strip()

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

        # Optional: send each run's real shortlist to a Bodhi workflow (e.g.
        # a Risk Insights Generator) for an extra AI-driven risk analysis on
        # top of your own — see integrations/bodhi_risk_insights.py.
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

    # A key is "placeholder" if it still looks like the sample text.
    def _api_key_looks_real(self) -> bool:
        return bool(self.api_key) and "replace-with" not in self.api_key


def build_client(settings: Settings):
    """
    Return (client, mode_used).

    client    : a google-genai Client, or None if no credentials worked.
    mode_used : "adc", "token", or "none" — just for logging.
    """
    try:
        from google import genai
    except ImportError:
        print("[config] google-genai not installed. Run: pip install google-genai")
        return None, "none"

    want = settings.auth_mode

    def try_adc():
        if not settings.use_vertex or not settings.project:
            return None
        try:
            client = genai.Client(
                vertexai=True,
                project=settings.project,
                location=settings.location,
            )
            return client
        except Exception as e:  # noqa: BLE001
            print(f"[config] Vertex/ADC login failed: {e}")
            return None

    def try_token():
        if not settings._api_key_looks_real():
            return None
        try:
            return genai.Client(api_key=settings.api_key)
        except Exception as e:  # noqa: BLE001
            print(f"[config] API-key login failed: {e}")
            return None

    if want == "adc":
        c = try_adc()
        return (c, "adc") if c else (None, "none")

    if want == "token":
        c = try_token()
        return (c, "token") if c else (None, "none")

    # auto
    c = try_adc()
    if c:
        return c, "adc"
    c = try_token()
    if c:
        return c, "token"
    return None, "none"


# Build these once at import time so every module shares them.
settings = Settings()
client, auth_used = build_client(settings)
