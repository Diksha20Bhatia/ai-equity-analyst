"""
contracts.py  —  the executable contract for every pipeline in this app
=====================================================================
What each pipeline is guaranteed to take in, what it is guaranteed to
produce, which states a shortlist can be in, and the named business rules
that decide whether a symbol is allowed to reach an external channel.

This exists so those answers live in RUNNABLE CODE rather than only in
README prose:

  - run.py and intraday_run.py validate their own output against the
    contract before writing it, so a pipeline that silently stops
    producing a promised field fails loudly instead of quietly.
  - alerts/approval_queue.py calls evaluate_release() to decide what may
    be pushed — the rules below are the real gate, not a description of
    one written somewhere else.
  - dashboard.py renders both the contract and the per-symbol rule
    outcomes, so an operator can see exactly which rule blocked what,
    without reading any documentation.

Change a threshold here and the behaviour, the enforcement and the UI all
move together.
"""

from dataclasses import dataclass, field

# ---------------------------------------------------------------------
# Tunable business thresholds — the only place these numbers exist.
# ---------------------------------------------------------------------
MIN_RELEASE_CONFIDENCE = 50      # a pick the model itself isn't ~half sure of
                                  # doesn't reach a live watchlist
BLOCKING_ACTIONS = {"avoid"}      # stances that can never be released
REQUIRE_BODHI_WHEN_ENABLED = True  # if Bodhi ran, only "proceed" is releasable


# ---------------------------------------------------------------------
# Approval states — the complete, closed set. Nothing else is valid.
# ---------------------------------------------------------------------
APPROVAL_STATES = {
    "pending": "Computed and recorded, but NOT sent anywhere. Awaiting a human decision.",
    "approved": "A human released it: Telegram alert sent, releasable symbols pushed to TradingView.",
    "rejected": "A human declined it. Nothing was sent externally; the internal record is still kept.",
}

# Per-symbol review states produced by the Bodhi workflow.
REVIEW_STATES = {
    "proceed": "Bodhi reviewed the symbol and found nothing worth blocking on.",
    "blocked": "Bodhi (after escalation, if it was triggered) says no. Excluded from release.",
    "unverified": "Bodhi's review did not complete (timeout/error/unparseable). Never treated as a clearance.",
}


@dataclass
class PipelineContract:
    name: str
    purpose: str
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    states: dict = field(default_factory=dict)
    external_effects: str = "none"


SWING = PipelineContract(
    name="Swing analysis (run.py)",
    purpose="Produce a risk-tier-specific shortlist of NSE swing candidates from real market data, "
            "reviewed by Bodhi and held for human approval before any external delivery.",
    inputs={
        "UNIVERSE": "NIFTY50 / NIFTY500 / comma-separated symbols (.env)",
        "TOP_N": "how many picks per risk tier (.env)",
        "investor profile": "risk appetites, capital, horizon, sector preferences/exclusions, max position %",
    },
    outputs={
        "output/analysis_<DATE>.json": "profile, market_context, analyses[], results_by_tier{}",
        "output/portfolio_tracker.xlsx": "one dated sheet per run (selected + watchlist)",
        "output/approvals/pending_<DATE>.json": "the shortlist + Bodhi decisions, awaiting a human",
        "output/metrics.jsonl": "always-on per-run, per-agent, per-LLM-call telemetry",
    },
    states=APPROVAL_STATES,
    external_effects="NONE at run time. Telegram/TradingView happen only on human approval.",
)

INTRADAY = PipelineContract(
    name="Intraday scan (intraday_run.py)",
    purpose="Mechanical same-day opening-range / VWAP / volume-surge scan. Zero AI calls in the "
            "scan itself; only meaningful during market hours (09:15-15:30 IST).",
    inputs={"UNIVERSE": "same universe setting as the swing pipeline (.env)"},
    outputs={
        "output/intraday_scan_<TIMESTAMP>.json": "picks[] with direction, conviction, entry, stop, target",
        "output/intraday_tracker.xlsx": "one dated sheet per scan",
    },
    states={"n/a": "The intraday scan does not use the approval queue — it is a live, "
                    "self-expiring signal, not a held recommendation."},
    external_effects="Telegram alert sent directly (short-lived intraday signal).",
)

BODHI_REVIEW = PipelineContract(
    name="Bodhi risk review (integrations/bodhi_risk_insights.py)",
    purpose="Independent per-symbol risk review on the Bodhi platform: one workflow run per "
            "shortlisted symbol, each producing a structured decision.",
    inputs={
        "Investmentportfolio": "ONE stock's JSON object per run (start-node workflow variable)",
        "Query_for_risk_assessment": "the risk question + the required decision schema",
    },
    outputs={
        "decisions[]": '{"symbol", "status": ok|timeout|error, "decision": proceed|blocked|unverified, '
                        '"reason", "research_confidence": full|partial}',
        "output/bodhi_risk_insight_<DATE>.txt": "the narrative report for all symbols",
    },
    states=REVIEW_STATES,
    external_effects="Calls the external Bodhi platform. Never blocks the run: a failed symbol "
                      "becomes 'unverified', it does not abort the pipeline.",
)

ALL_CONTRACTS = [SWING, INTRADAY, BODHI_REVIEW]


# ---------------------------------------------------------------------
# Output validation — a pipeline that stops producing a promised field
# should say so, not quietly hand back something smaller.
# ---------------------------------------------------------------------
def validate_swing_output(payload: dict) -> list:
    """Returns a list of human-readable contract violations ([] = valid)."""
    problems = []
    for key in ("profile", "market_context", "analyses", "results_by_tier"):
        if key not in payload:
            problems.append(f"missing top-level key '{key}'")

    if not isinstance(payload.get("analyses"), list) or not payload.get("analyses"):
        problems.append("'analyses' must be a non-empty list")

    results = payload.get("results_by_tier")
    if not isinstance(results, dict) or not results:
        problems.append("'results_by_tier' must be a non-empty dict keyed by risk tier")
    else:
        for tier, result in results.items():
            if not isinstance(result, dict):
                problems.append(f"tier '{tier}' result is not an object")
                continue
            if "shortlist" not in result:
                problems.append(f"tier '{tier}' is missing 'shortlist'")
                continue
            for pick in result.get("shortlist") or []:
                for required in ("symbol", "action", "confidence", "opportunity_score"):
                    if required not in pick:
                        problems.append(
                            f"tier '{tier}' pick {pick.get('symbol', '?')} missing '{required}'"
                        )
    return problems


# ---------------------------------------------------------------------
# Release rules — the real gate on external delivery.
# ---------------------------------------------------------------------
def evaluate_release(symbol: str, picks: list, bodhi_decision: dict = None,
                      bodhi_ran: bool = False) -> dict:
    """Decides whether `symbol` may be pushed to an external watchlist,
    and shows its working.

    picks           -- every shortlist entry for this symbol (one per risk tier)
    bodhi_decision  -- this symbol's entry from bodhi_result["decisions"], if any
    bodhi_ran       -- whether the Bodhi review actually ran this cycle

    Returns {"symbol", "allowed": bool, "rules": [{id, name, passed, detail}],
             "blocked_by": [rule ids]} — the rule list is rendered verbatim in
    the dashboard, so an operator sees why, not just what."""
    rules = []

    # R1 — independent review clearance
    if bodhi_ran:
        decision = (bodhi_decision or {}).get("decision", "unverified")
        passed = (decision == "proceed") if REQUIRE_BODHI_WHEN_ENABLED else True
        rules.append({
            "id": "R1", "name": "Independent Bodhi review cleared it",
            "passed": passed,
            "detail": f"Bodhi decision: {decision}"
                      + (f" — {(bodhi_decision or {}).get('reason', '')}" if bodhi_decision else ""),
        })
    else:
        rules.append({
            "id": "R1", "name": "Independent Bodhi review cleared it",
            "passed": True,
            "detail": "Bodhi review did not run this cycle — rule not applicable, nothing filtered.",
        })

    # R2 — no tier called it 'avoid'
    actions = [str(p.get("action", "")).lower() for p in picks]
    blocking = [a for a in actions if a in BLOCKING_ACTIONS]
    rules.append({
        "id": "R2", "name": "No risk tier marked it 'avoid'",
        "passed": not blocking,
        "detail": f"stances across tiers: {', '.join(actions) or 'none'}",
    })

    # R3 — the model's own confidence floor
    confidences = [p.get("confidence") for p in picks if isinstance(p.get("confidence"), (int, float))]
    best = max(confidences) if confidences else None
    rules.append({
        "id": "R3", "name": f"Confidence >= {MIN_RELEASE_CONFIDENCE}%",
        "passed": best is not None and best >= MIN_RELEASE_CONFIDENCE,
        "detail": f"highest confidence across tiers: {best if best is not None else 'not reported'}",
    })

    # R4 — it must have a real price; an unpriceable symbol can't be traded on
    priced = any(p.get("current_price") for p in picks)
    rules.append({
        "id": "R4", "name": "Real current price available",
        "passed": priced,
        "detail": "priced from live market data" if priced else "no current price could be fetched",
    })

    blocked_by = [r["id"] for r in rules if not r["passed"]]
    return {
        "symbol": symbol,
        "allowed": not blocked_by,
        "rules": rules,
        "blocked_by": blocked_by,
    }


def release_plan(results_by_tier: dict, bodhi_decisions: list, bodhi_ran: bool) -> dict:
    """Applies evaluate_release() to every shortlisted symbol. Returns
    {"allowed": [symbols], "held": [symbols], "detail": {symbol: evaluation}}."""
    picks_by_symbol = {}
    for result in (results_by_tier or {}).values():
        for pick in result.get("shortlist", []):
            picks_by_symbol.setdefault(pick["symbol"], []).append(pick)

    decision_by_symbol = {d["symbol"]: d for d in (bodhi_decisions or [])}

    detail, allowed, held = {}, [], []
    for symbol, picks in picks_by_symbol.items():
        evaluation = evaluate_release(
            symbol, picks, decision_by_symbol.get(symbol), bodhi_ran,
        )
        detail[symbol] = evaluation
        (allowed if evaluation["allowed"] else held).append(symbol)

    return {"allowed": sorted(allowed), "held": sorted(held), "detail": detail}
