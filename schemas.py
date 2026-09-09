"""
schemas.py  —  typed contracts at every agent-to-agent and
workflow-to-code boundary in this app
=====================================================================
Every place an LLM's raw JSON reply, or Bodhi's raw workflow output,
gets consumed downstream used to mean "trust the dict, and let a
KeyError surface later if it's wrong." These pydantic models make each
of those boundaries an explicit, validated contract instead:

  - GatewayChatCompletion — the shape config.call_llm() expects back
    from Bodhi's LLM Gateway (OpenAI-compatible chat/completions).
  - BullCaseItem / BearCaseItem — one symbol's entry in the Bull/Bear
    Case Agents' {SYMBOL: {...}} replies.
  - DecisionPick / DecisionResult — the Decision Agent's shortlist
    output, validated at agents/orchestrator_agent.py's reflect step so
    a shape violation becomes a normal retry-with-feedback, the same
    path as any other quality issue — never a raw crash downstream in
    _suggest_quantity/_allocate_capital.
  - BodhiVerdictItem — one entry in the Bodhi Workflow Builder's
    per-symbol verdict array (integrations/bodhi_risk_insights.py),
    validated so a malformed item is dropped with a logged reason
    instead of silently passed through to the TradingView filter.
  - MemoryRecord — one structured record persisted by
    memory/memory_layer.py, validated before it's written so a bad
    write can't quietly corrupt the durable trend history.

A validation failure is never treated as "close enough" anywhere these
are used — the caller either rejects the specific bad item (dropping it
with a reason) or turns it into an actionable issue for the existing
retry loop. Nothing here invents a fallback value.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError

__all__ = [
    "ValidationError",
    "GatewayChatCompletion",
    "BullCaseItem",
    "BearCaseItem",
    "DecisionPick",
    "DecisionResult",
    "BodhiVerdictItem",
    "MemoryRecord",
    "LiveQuoteResult",
    "SymbolSearchResult",
    "McpSymbolVerification",
    "McpVerifySymbolInput",
]


# ---------------------------------------------------------------------
# Bodhi LLM Gateway — config.call_llm()'s response contract
# ---------------------------------------------------------------------
class _GatewayMessage(BaseModel):
    content: str


class _GatewayChoice(BaseModel):
    message: _GatewayMessage


class GatewayChatCompletion(BaseModel):
    """The minimum real shape config.call_llm() needs back from an
    OpenAI-compatible chat/completions response — at least one choice
    with a non-empty message content. `usage` is reported by the
    gateway for cost tracking but isn't required by every backend, so
    it's optional rather than a hard validation failure."""
    choices: list[_GatewayChoice] = Field(min_length=1)
    usage: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------
# Bull Case / Bear Case Agents — agents/bull_case_agent.py, bear_case_agent.py
# ---------------------------------------------------------------------
class BullCaseItem(BaseModel):
    bull_case: str = Field(min_length=1)
    bull_target_price: float
    bull_strength: float = Field(ge=0, le=100)


class BearCaseItem(BaseModel):
    bear_case: str = Field(min_length=1)
    bear_severity: float = Field(ge=0, le=100)
    key_risk: Optional[str] = None
    bear_target_price: Optional[float] = None


# ---------------------------------------------------------------------
# Decision Agent — agents/decision_agent.py, checked in
# agents/orchestrator_agent.py::_check_decision_quality()
# ---------------------------------------------------------------------
class DecisionPick(BaseModel):
    symbol: str = Field(min_length=1)
    opportunity_score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=100)
    action: Literal["accumulate", "hold", "avoid"]
    bull_case: str = Field(min_length=1)
    bear_case: str = Field(min_length=1)
    thesis: str = Field(min_length=1)
    target_price: float
    target_price_rationale: str = ""
    near_term_outlook: str = ""
    portfolio_fit: Literal["good", "neutral", "poor"]
    portfolio_fit_note: str = ""
    invalidation: str = ""
    key_risk: str = ""


class DecisionResult(BaseModel):
    shortlist: list[DecisionPick]
    summary: str = ""


# ---------------------------------------------------------------------
# Bodhi Workflow Builder verdicts — integrations/bodhi_risk_insights.py
# ---------------------------------------------------------------------
class BodhiVerdictItem(BaseModel):
    symbol: str = Field(min_length=1)
    verdict: Literal["clear", "caution", "avoid"]
    notes: str = ""


# ---------------------------------------------------------------------
# TradingView tool calls — integrations/tradingview_quote.py. Two
# distinct model-facing tool contracts, one per real primitive: a live
# quote (Chrome DevTools Protocol) and a symbol-search verification
# (TradingView's own REST endpoint) — different transports, different
# shapes, both validated before an agent's prompt ever sees them.
# ---------------------------------------------------------------------
class LiveQuoteResult(BaseModel):
    last: float
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    volume: Optional[float] = None

    model_config = {"extra": "ignore"}  # the raw CLI reply carries extra fields (symbol, time, ...) we don't need


class SymbolSearchResult(BaseModel):
    verified: bool
    description: Optional[str] = None
    exchange: Optional[str] = None


# ---------------------------------------------------------------------
# integrations/mcp_server.py's verify_nse_symbol Tool — a real MCP
# tools/call result, consumed via integrations/mcp_pipeline_client.py.
# A THIRD, independent symbol-verification path (after TradingView's
# CDP live-quote and REST symbol-search) — this one over real stdio MCP
# transport, checked against this project's own NIFTY50 universe data.
# ---------------------------------------------------------------------
class McpSymbolVerification(BaseModel):
    symbol: str = Field(min_length=1)
    verified: bool
    universe_size: int = Field(ge=0)


class McpVerifySymbolInput(BaseModel):
    """The INPUT contract for the same tool — validated (and, where
    possible, repaired) BEFORE the real MCP round-trip is made, not only
    after. A model-invoked tool call is a real handoff boundary in both
    directions; this project's earlier typed contracts (BullCaseItem,
    DecisionResult, BodhiVerdictItem, LiveQuoteResult) only ever
    validated what came BACK from a call."""
    symbol: str = Field(min_length=1, max_length=32)

    @classmethod
    def repair(cls, raw_symbol) -> "McpVerifySymbolInput | None":
        """Best-effort repair for the one real malformed-input shape
        this tool can actually receive: a symbol with stray whitespace,
        lowercase, or non-alphanumeric decoration (e.g. "nse:reliance",
        " Reliance \\n"). Returns None only if nothing usable remains
        after stripping — never silently sends a garbage argument."""
        if not isinstance(raw_symbol, str):
            return None
        import re
        cleaned = re.sub(r"[^A-Za-z0-9]", "", raw_symbol.split(":")[-1]).upper()
        if not cleaned:
            return None
        try:
            return cls(symbol=cleaned)
        except ValidationError:
            return None


# ---------------------------------------------------------------------
# Memory records — memory/memory_layer.py::store()
# ---------------------------------------------------------------------
class MemoryRecord(BaseModel):
    date: str = Field(min_length=1)
    price: Optional[float] = None
    fundamental_score: Optional[float] = None
    technical_score: Optional[float] = None
    risk_score: Optional[float] = None
    action: Optional[str] = None
    # Persisted GOAL tracking (memory/memory_layer.py::goal_status()): the
    # Decision Agent's own real target/invalidation for this call, carried
    # forward so a LATER run can resurface it — not re-derived, not
    # invented, the same value the agent actually gave at the time.
    target_price: Optional[float] = None
    invalidation: Optional[str] = None

    model_config = {"extra": "allow"}  # tolerate future fields, don't silently drop them
