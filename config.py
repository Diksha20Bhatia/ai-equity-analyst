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
from schemas import GatewayChatCompletion, ValidationError

load_dotenv()  # read .env into environment variables


class BodhiGatewayError(RuntimeError):
    """Base class for every real Bodhi LLM Gateway failure — callers
    that don't care about the distinction can just catch this."""


class BodhiGatewayAuthError(BodhiGatewayError):
    """A real 401/403 from the gateway — an identity/permissions
    problem (wrong token, expired token, an explicit deny in an
    identity-based policy), never a code bug. Never retried: retrying
    an auth failure just repeats the same denial three times."""


class BodhiGatewayShapeError(BodhiGatewayError):
    """The gateway answered (2xx) but the body isn't the OpenAI-compatible
    chat/completions shape config.call_llm() needs — see
    schemas.GatewayChatCompletion. Distinct from an auth or network
    failure: the gateway is reachable and authenticated, but something
    about the response itself (a different backend behind it, a
    malformed proxy response) doesn't match the documented contract."""


class BodhiGatewayUnavailableError(BodhiGatewayError):
    """Network failure, timeout, or a retryable 5xx/429 that never
    recovered after every retry attempt was exhausted."""


class BodhiGatewayBudgetExceededError(BodhiGatewayError):
    """A real, ENFORCED cost/token budget — the call is refused BEFORE
    it's sent, not just flagged after the fact. Distinct from
    BODHI_LLM_TOKEN_BUDGET_WARNING (soft, logs after a successful call):
    this one is a hard ceiling checked against the prompt's own real
    size before spending anything. Never triggers a fallback to a
    different model or backend — this app has exactly one AI backend by
    design — the call is simply refused, the same as any other
    fail-closed error in this file."""


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
        # Deliberate per-agent model selection, not one fixed model for
        # every call regardless of task. Format: "Agent Name=model-id,
        # Other Agent=model-id" (agent names match BaseAgent.name, e.g.
        # "Orchestrator", "Bull Case Agent"). The real reasoning: a
        # classification-shaped task (the Orchestrator's retry-or-finish
        # routing decision, temperature=0.1, one short JSON field) doesn't
        # need the same model as a task requiring genuine synthesis across
        # conflicting real signals (the Decision Agent's adjudication) —
        # routing the former to a smaller/faster model, once you know
        # which ones your Bodhi account exposes (GET /v1/models), is a
        # deliberate choice, not an accidental downgrade. Empty by
        # default — every agent uses BODHI_LLM_MODEL — since the actual
        # available model catalog is account-specific.
        self.bodhi_llm_model_overrides = self._parse_model_overrides(
            os.getenv("BODHI_LLM_MODEL_OVERRIDES", "")
        )
        # Optional cost governance, two tiers:
        #  - BODHI_LLM_TOKEN_BUDGET_WARNING: soft — warn (never block)
        #    when a call's REPORTED usage crosses this, after it already
        #    succeeded.
        #  - BODHI_LLM_TOKEN_BUDGET_LIMIT: hard — an ENFORCED ceiling on
        #    the prompt's own ESTIMATED size (chars // 4, a documented
        #    approximation, not a real tokenizer — good enough to catch
        #    an accidentally-huge prompt, e.g. a shortlist's data
        #    duplicated into it by a bug), checked and refused BEFORE the
        #    call is ever sent. Both 0 (default) — disabled. Neither ever
        #    triggers a fallback to a different model/backend.
        self.bodhi_llm_token_budget_warning = int(os.getenv("BODHI_LLM_TOKEN_BUDGET_WARNING", "0"))
        self.bodhi_llm_token_budget_limit = int(os.getenv("BODHI_LLM_TOKEN_BUDGET_LIMIT", "0"))

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

        # Optional: a real TOOL the Decision Agent calls mid-reasoning — a
        # live TradingView Desktop quote per stock it's about to adjudicate,
        # shown to the LLM alongside its other data (see
        # integrations/tradingview_quote.py). Separate from
        # TRADINGVIEW_WATCHLIST_ENABLED above, which is a one-way push fired
        # AFTER a human approves, not something an agent calls while reasoning.
        self.tradingview_quote_enabled = _get_bool("TRADINGVIEW_QUOTE_ENABLED", False)

        # Optional: a SECOND, distinct agent tool call — TradingView's
        # symbol-search REST endpoint (a different transport from the
        # live quote above: no Desktop CDP round-trip, <1s typical) used
        # to verify a shortlisted ticker actually resolves to a real NSE
        # equity before the Decision Agent reasons about it. See
        # integrations/tradingview_quote.py::verify_symbol().
        self.tradingview_symbol_search_enabled = _get_bool("TRADINGVIEW_SYMBOL_SEARCH_ENABLED", False)

        # Optional: a THIRD, independent symbol-verification path — a
        # real local MCP server (integrations/mcp_server.py), reached
        # over real stdio MCP transport (genuinely different from
        # TradingView's CDP/REST calls above and Bodhi's own HTTP-based
        # bodhi-deep-research tool). No external app dependency (unlike
        # TradingView Desktop) — see integrations/mcp_pipeline_client.py.
        self.mcp_verification_enabled = _get_bool("MCP_VERIFICATION_ENABLED", False)

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

    @staticmethod
    def _parse_model_overrides(raw: str) -> dict:
        """"Agent Name=model-id,Other=model-id" -> {"Agent Name": "model-id", ...}.
        Malformed entries (no "=", empty name/model) are skipped, never
        raised — a typo in this optional setting must never stop the app
        from starting."""
        overrides = {}
        for entry in raw.split(","):
            entry = entry.strip()
            if not entry or "=" not in entry:
                continue
            name, _, model = entry.partition("=")
            name, model = name.strip(), model.strip()
            if name and model:
                overrides[name] = model
        return overrides


_RETRYABLE_STATUS = (429, 500, 502, 503, 504)


def _warn_if_over_token_budget(usage: dict, agent: str) -> None:
    """Soft cost-governance signal, not a hard cap: if
    BODHI_LLM_TOKEN_BUDGET_WARNING is set (>0) and this call's real
    reported total_tokens crosses it, log it as a visible event — never
    blocks the call (it already succeeded) and never triggers any
    fallback route, since this app has exactly one AI backend by
    design. See _enforce_token_budget() below for the hard version,
    checked BEFORE the call is sent."""
    budget = settings.bodhi_llm_token_budget_warning
    if not budget:
        return
    total = usage.get("total_tokens")
    if total is not None and total > budget:
        observability.record_failure(
            "bodhi_token_budget_exceeded",
            f"a real call used {total} tokens, over the configured warning budget of {budget} "
            f"— call still succeeded, this is a visibility signal only, nothing was blocked",
            agent=agent, total_tokens=total, budget=budget,
        )


def _estimate_tokens(text: str) -> int:
    """Deliberately simple: ~4 characters per token, a widely-used rough
    approximation (not tied to any one model's real tokenizer, since
    Bodhi can route to different backends). Documented as an estimate,
    not precise — good enough to catch a prompt that's an order of
    magnitude too large (e.g. a shortlist's data accidentally duplicated
    into it), which is the actual failure mode this guards against."""
    return len(text) // 4


def _enforce_token_budget(prompt: str, system_instruction: str, agent: str) -> None:
    """Real ENFORCEMENT, not just a warning: if
    BODHI_LLM_TOKEN_BUDGET_LIMIT is set (>0) and this call's ESTIMATED
    prompt size would exceed it, refuse the call before it's ever sent —
    raises BodhiGatewayBudgetExceededError. Checked against the prompt
    only (not the eventual completion, which isn't known until after the
    call — enforcing on the side you can actually know in advance)."""
    limit = settings.bodhi_llm_token_budget_limit
    if not limit:
        return
    estimated = _estimate_tokens(system_instruction) + _estimate_tokens(prompt)
    if estimated > limit:
        observability.record_failure(
            "bodhi_token_budget_enforced",
            f"refused a call: estimated ~{estimated} prompt tokens exceeds the enforced "
            f"limit of {limit} — nothing was sent to the gateway",
            agent=agent, estimated_tokens=estimated, limit=limit,
        )
        raise BodhiGatewayBudgetExceededError(
            f"Refusing to send this call: estimated ~{estimated} prompt tokens exceeds the "
            f"enforced BODHI_LLM_TOKEN_BUDGET_LIMIT of {limit}. Nothing was sent to the gateway."
        )


def call_llm(prompt: str, system_instruction: str = "", temperature: float = 0.3,
             json_mode: bool = True, agent: str = None) -> tuple:
    """The one AI entry point every agent in this app calls. A plain REST
    request to Bodhi's LLM Gateway (OpenAI-compatible chat/completions) —
    never a direct provider SDK, and never a fallback to one. Retries
    transient failures (429/5xx) with exponential backoff; fails fast on a
    real 4xx (bad request/auth).

    `agent` (optional) is the calling agent's name — used for attribution
    in the persisted call log (output/llm_call_log.jsonl), for DELIBERATE
    per-agent model selection (BODHI_LLM_MODEL_OVERRIDES — see
    Settings.__init__), and to enforce/report the token budget below.

    Returns (text, usage) — usage is the gateway's real reported token
    counts (prompt_tokens/completion_tokens/total_tokens), used by
    BaseAgent to attach real cost data to every traced call.

    Raises RuntimeError if the gateway isn't configured, if the enforced
    token budget would be exceeded (BODHI_LLM_TOKEN_BUDGET_LIMIT, checked
    BEFORE anything is sent), or if every retry attempt is exhausted —
    this app never silently falls back to a fabricated answer, or to a
    different model backend, when the AI path is broken."""
    if not settings.bodhi_llm_gateway_token:
        raise BodhiGatewayError(
            "Bodhi LLM Gateway is not configured — set BODHI_LLM_GATEWAY_TOKEN "
            "(and BODHI_LLM_TRACE_USER_ID) in .env."
        )

    _enforce_token_budget(prompt, system_instruction, agent)
    model = settings.bodhi_llm_model_overrides.get(agent, settings.bodhi_llm_model)

    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    body = {"model": model, "messages": messages, "temperature": temperature}
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
    call_started = time.monotonic()
    for attempt in range(1, 4):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=90)
        except requests.RequestException as e:
            last_error = f"network error: {e}"
            observability.record_failure("bodhi_llm_gateway", last_error, attempt=attempt, model=model)
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))
                continue
            raise BodhiGatewayUnavailableError(
                f"Bodhi LLM Gateway unreachable after 3 attempts: {e}"
            ) from e

        if resp.status_code < 400:
            data = resp.json()
            try:
                parsed = GatewayChatCompletion.model_validate(data)
            except ValidationError as e:
                observability.record_failure(
                    "bodhi_llm_gateway", f"response failed the documented chat/completions "
                    f"contract (schemas.GatewayChatCompletion): {e}", model=model,
                )
                raise BodhiGatewayShapeError(
                    f"Bodhi LLM Gateway returned a 2xx response that doesn't match the expected "
                    f"OpenAI-compatible chat/completions shape: {e}"
                ) from e
            text = parsed.choices[0].message.content
            latency_ms = (time.monotonic() - call_started) * 1000
            usage = parsed.usage
            observability.record_call(
                backend="bodhi_llm_gateway", model=model,
                latency_ms=latency_ms, usage=usage, agent=agent, attempts=attempt,
            )
            _warn_if_over_token_budget(usage, agent)
            return text.strip(), usage

        last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"

        if resp.status_code in (401, 403):
            observability.record_failure(
                "bodhi_llm_gateway", resp.text[:300], attempt=attempt,
                status_code=resp.status_code, model=model,
            )
            raise BodhiGatewayAuthError(
                f"Bodhi LLM Gateway denied this request ({resp.status_code}) — an identity/"
                f"permissions issue (wrong or expired BODHI_LLM_GATEWAY_TOKEN, or an explicit "
                f"deny in an identity-based policy), not a code bug. Never retried, since "
                f"retrying repeats the same denial. {resp.text[:300]}"
            )

        if resp.status_code in _RETRYABLE_STATUS and attempt < 3:
            observability.record_failure(
                "bodhi_llm_gateway", last_error, attempt=attempt,
                status_code=resp.status_code, model=model,
            )
            time.sleep(2 ** (attempt - 1))
            continue

        observability.record_failure(
            "bodhi_llm_gateway", resp.text[:300], attempt=attempt,
            status_code=resp.status_code, model=model,
        )
        raise BodhiGatewayError(f"Bodhi LLM Gateway call failed ({resp.status_code}): {resp.text[:500]}")

    raise BodhiGatewayUnavailableError(f"Bodhi LLM Gateway failed after 3 attempts: {last_error}")


# Build once at import time so every module shares it.
settings = Settings()
