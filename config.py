"""
config.py
=========
Loads settings from the .env file and builds ONE shared Gemini client that
every agent reuses.

It understands three login modes (GOOGLE_AUTH_MODE):
    adc   -> Google Cloud / Vertex AI  (Application Default Credentials)
    token -> Google AI Studio API key
    auto  -> try adc first, fall back to token

If no valid Gemini credentials are found, the client is set to None. The
agents notice this and switch to a simple rule-based fallback so the whole
pipeline still runs end-to-end (useful for a first test with no keys).
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

        self.data_mode = os.getenv("DATA_MODE", "demo").strip().lower()
        self.top_n = int(os.getenv("TOP_N", "3"))
        # Either an index keyword (NIFTY50 / NIFTY500) or a comma-separated
        # list of NSE symbols — resolved to an actual symbol list by
        # data.nse_universe.resolve_universe().
        self.universe = os.getenv("UNIVERSE", "NIFTY50").strip()

        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.profile_path = os.getenv("PROFILE_PATH", "").strip()

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
