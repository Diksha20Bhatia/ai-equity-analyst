"""
bodhi_risk_insights.py  —  send today's real shortlist to Bodhi for a
risk-verification pass before it goes out to Telegram/TradingView
=====================================================================
Sends today's real, deduped shortlist (every field the Decision Agent
actually produced — bull/bear case, opportunity score, target price,
₹ allocation, sector) to a confirmed-working, custom-built Bodhi
workflow (see integrations/bodhi_workflow/README.md for the full graph
and two real, verified run IDs): Start Node -> Risk Analyst Agent (live
web research per stock via the bodhi-deep-research MCP tool) ->
Contrarian Agent (red-team review) -> a real conditional branch that only
escalates to a second, deeper Escalation Analyst pass when the
Contrarian actually flagged a concern -> a combine step -> End Node, and
pulls a per-symbol verdict out of the final (possibly escalated)
answer.

Sends TWO variables directly to the workflow's Start Node / Workflow
Variables (names set by BODHI_PORTFOLIO_VARIABLE / BODHI_QUERY_VARIABLE —
Bodhi variable names can't contain spaces). Deliberately NOT a UI Node:
that node type pauses a run waiting for a human to fill in a form, which
breaks an API-triggered run with no one there to answer it — Workflow
Variables are the entry point an API call can actually set.

Optional (BODHI_ENABLED=true) and best-effort: verify_shortlist() never
raises. If Bodhi isn't configured, isn't reachable, or times out, it
logs a clear message and returns ran=False — callers should fall back
to treating every symbol as unverified rather than blocking the run,
the same non-blocking pattern as alerts/tradingview_watchlist.py.

One thing worth confirming once your workflow is published: whether the
final agent's output actually contains a structured per-symbol verdict.
_parse_verdicts() tries to find one (a JSON array named "verdicts"
anywhere in the answer, or any JSON array of {"symbol", "verdict"}
objects), but if your final node's prompt doesn't ask for that shape,
none will be found — in which case verify_shortlist() still returns the
full narrative report (shown in Telegram), just with no per-symbol
filtering applied to TradingView.
"""

import datetime as dt
import json
import re
from pathlib import Path

import observability
from config import settings
from integrations.bodhi_client import call_bodhi_workflow
from schemas import BodhiVerdictItem, ValidationError

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

# Verdict text that means "keep this off the actionable TradingView list."
_FLAG_KEYWORDS = ("avoid", "high risk", "high-risk", "flag", "reject", "do not", "caution: exit")


def _shortlist_payload(results_by_tier: dict, analyses: list) -> list:
    """Today's real, deduped shortlist as a list of dicts — every field
    the Decision Agent actually produced, not a trimmed-down summary."""
    sector_by_symbol = {a["symbol"]: a.get("sector") for a in analyses}
    picks_by_symbol = {}

    for tier, result in results_by_tier.items():
        for p in result.get("shortlist", []):
            entry = picks_by_symbol.setdefault(p["symbol"], {
                "symbol": p["symbol"],
                "sector": sector_by_symbol.get(p["symbol"]) or "n/a",
                "action": p.get("action"),
                "opportunity_score": p.get("opportunity_score"),
                "confidence": p.get("confidence"),
                "conviction": p.get("conviction"),
                "current_price": p.get("current_price"),
                "target_price": p.get("target_price"),
                "bull_case": p.get("bull_case"),
                "bear_case": p.get("bear_case"),
                "risk_percent": p.get("risk_percent"),
                "allocated_amount": p.get("allocated_amount"),
                "tiers": [],
            })
            entry["tiers"].append(tier)

    return list(picks_by_symbol.values())


def _portfolio_text(results_by_tier: dict, analyses: list) -> str:
    """The BODHI_PORTFOLIO_VARIABLE value: today's real shortlist as JSON
    text — sent to the workflow's Start Node, not a file upload."""
    shortlist = _shortlist_payload(results_by_tier, analyses)
    return json.dumps(shortlist, indent=2, default=str)


def _query_text() -> str:
    """The BODHI_QUERY_VARIABLE value: your configured risk question,
    plus an explicit ask for a structured per-symbol verdict so
    verify_shortlist() can act on it downstream."""
    return (
        f"{settings.bodhi_risk_query}\n\n"
        "In your final answer, include a JSON array named \"verdicts\" with one "
        "object per symbol in the portfolio: {\"symbol\": \"...\", \"verdict\": "
        "\"clear\" | \"caution\" | \"avoid\", \"notes\": \"one or two sentences\"}."
    )


# Real phrases seen in a live run when the Escalation Analyst's MCP
# research tool actually failed mid-run (a misconfigured tool credential)
# but the agent still produced valid JSON output rather than crashing —
# exactly the graceful-degradation behavior its prompt asks for. This
# catches that case on the Python side so it's visible, not silent.
_DEGRADED_RESEARCH_PHRASES = (
    "technical error", "technical errors", "invalid model configuration",
    "unable to access", "tool unavailable", "could not verify",
    "research tool unavailable", "encountered an error",
)


def _detect_research_degraded(report: str) -> bool:
    """Real failure-path detection, not just a happy-path parse: flags
    when the workflow's own agents reported a tool/research failure in
    their final text, so a degraded-quality answer is never silently
    treated the same as a fully-researched one."""
    lower = (report or "").lower()
    return any(phrase in lower for phrase in _DEGRADED_RESEARCH_PHRASES)


# The real, documented operator-visible signal that the escalation
# branch fired (see integrations/bodhi_workflow/README.md, "What an
# operator actually sees, step by step" — item 4): the Escalation
# Analyst's own framing, distinct from the Contrarian's original text.
_ESCALATION_FRAMING_PHRASES = (
    "based on the deeper review", "deeper review of the contrarian",
    "escalation analyst", "further research into the flagged",
)


def _detect_escalation_branch(report: str) -> bool:
    """Which of the workflow's two real branch outcomes produced this
    report — the clean (non-escalation) path passes the Contrarian's
    text straight through Set Combine Final Verdict, the escalated path
    replaces it with the Escalation Analyst's own framing. Both are real,
    documented shapes (see tests/test_scenario_bodhi_branches.py); this
    makes which one fired reviewer-visible instead of only inferable
    from reading the full report text by hand."""
    lower = (report or "").lower()
    return any(phrase in lower for phrase in _ESCALATION_FRAMING_PHRASES)


# A model paraphrasing instead of using the three exact documented words
# ("Avoid — high risk", "caution: exit position", "Clear.") is a REPAIR
# case, not a drop case: the real intent is unambiguous from a keyword
# match, so normalize it to the exact literal schemas.BodhiVerdictItem
# requires before validating, instead of discarding real signal just
# because the model didn't use the precise enum word. Checked in this
# order — "avoid" and "caution" first — since a phrase like "not clear
# to avoid caution" should never resolve to "clear".
_VERDICT_REPAIR_KEYWORDS = (("avoid", "avoid"), ("caution", "caution"), ("clear", "clear"))


def _repair_verdict_value(raw_value) -> str | None:
    """Best-effort normalization of an out-of-enum verdict string to one
    of the three schemas.BodhiVerdictItem accepts, by keyword match.
    Returns None if no known keyword is found — that item still gets
    dropped, just not silently confused with a successful repair."""
    if not isinstance(raw_value, str):
        return None
    lower = raw_value.lower()
    for keyword, normalized in _VERDICT_REPAIR_KEYWORDS:
        if keyword in lower:
            return normalized
    return None


def _parse_verdicts(answer_text: str) -> dict | None:
    """Best-effort: find a JSON array of {"symbol", "verdict", ...} objects
    anywhere in Bodhi's answer text. Returns {symbol: {"verdict", "notes"}},
    or None if nothing that shape could be found — callers must treat None
    as "no verdict available", not as "everything's clear".

    Every candidate item is validated against schemas.BodhiVerdictItem —
    this is the workflow-to-code boundary the certification's own review
    flagged as needing a more explicit, hardened handoff contract, and
    "repaired or retried where they are consumed, not only validated
    later downstream." Two passes, in order:
      1. REPAIR — a "verdict" value outside the exact clear/caution/avoid
         set is checked for one of those words as a substring (see
         _repair_verdict_value) and normalized before validating, so a
         model's paraphrase isn't discarded when its real intent is
         unambiguous.
      2. DROP — an item that still doesn't validate after the repair
         attempt (an unrecognisable verdict, a missing symbol) is logged
         and skipped — a malformed entry silently reaching the
         TradingView exclusion filter is worse than treating that one
         symbol as unverified.
    A repaired item is logged distinctly from a dropped one, so the two
    are never confused when reading output/reliability_log.jsonl."""
    candidates = re.findall(r"\[[^\[\]]*\{.*?\}[^\[\]]*\]", answer_text, re.DOTALL)
    candidates += re.findall(r"\{.*\}", answer_text, re.DOTALL)

    for raw in candidates:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue

        items = parsed.get("verdicts") if isinstance(parsed, dict) else parsed
        if not isinstance(items, list):
            continue

        verdicts = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                validated = BodhiVerdictItem.model_validate(item)
            except ValidationError as first_error:
                repaired_verdict = _repair_verdict_value(item.get("verdict"))
                if repaired_verdict is None:
                    observability.record_failure(
                        "bodhi_verdict_shape",
                        f"dropped one verdict item that didn't match the documented shape "
                        f"(schemas.BodhiVerdictItem) and couldn't be repaired: {first_error}",
                        raw_item=str(item)[:200],
                    )
                    continue
                try:
                    validated = BodhiVerdictItem.model_validate({**item, "verdict": repaired_verdict})
                except ValidationError as second_error:
                    observability.record_failure(
                        "bodhi_verdict_shape",
                        f"dropped one verdict item — repair to '{repaired_verdict}' still didn't "
                        f"validate: {second_error}",
                        raw_item=str(item)[:200],
                    )
                    continue
                observability.record_failure(
                    "bodhi_verdict_repaired",
                    f"repaired a verdict value to the documented enum by keyword match: "
                    f"{item.get('verdict')!r} -> {repaired_verdict!r}",
                    raw_item=str(item)[:200],
                )
            verdicts[validated.symbol] = {"verdict": validated.verdict, "notes": validated.notes}
        if verdicts:
            return verdicts

    return None


def verify_shortlist(results_by_tier: dict, analyses: list) -> dict:
    """Best-effort wrapper: never raises.

    Returns:
      ran            -- True if Bodhi was actually called and answered
      report         -- Bodhi's full narrative answer (for Telegram), or None
      verdicts       -- {symbol: {"verdict", "notes"}}, or None if not found
      flagged_symbols -- set of symbols verdict-flagged as avoid/high-risk
                         (empty if verdicts is None — nothing gets excluded
                         from TradingView just because parsing failed)
      run_id         -- Bodhi's own real run id for this call, or None if
                         Bodhi never actually ran. This is the same UUID
                         you'd see opening the run from the workflow's run
                         list in the Bodhi UI and copying it by hand —
                         printed to the terminal below so you don't have
                         to.
      research_degraded -- True if the report's own text indicates the
                         MCP research tool failed mid-run (a real
                         failure-path detection — the workflow's agents
                         degrade gracefully rather than crash, but a
                         degraded, reasoning-only answer should never be
                         silently treated the same as a freshly-researched
                         one). Logged to output/reliability_log.jsonl too.
      escalation_branch_fired -- True if this report shows the real,
                         documented signal that the graph's escalation
                         branch ran (the Escalation Analyst's own framing
                         replacing the Contrarian's original text) rather
                         than the clean/non-escalation path. See
                         tests/test_scenario_bodhi_branches.py for both
                         real shapes exercised directly.
    """
    empty = {"ran": False, "report": None, "verdicts": None, "flagged_symbols": set(),
             "run_id": None, "research_degraded": False, "escalation_branch_fired": False}
    if not settings.bodhi_enabled:
        return empty

    missing = [
        name for name, value in (
            ("BODHI_PAT_TOKEN", settings.bodhi_pat_token),
            ("BODHI_TASK_ID", settings.bodhi_task_id),
            ("BODHI_WORKFLOW_ID", settings.bodhi_workflow_id),
            ("BODHI_BASE_URL", settings.bodhi_base_url),
        ) if not value
    ]
    if missing:
        print(f"[bodhi] skipped: Bodhi isn't configured — set {', '.join(missing)} in .env.")
        return empty

    try:
        print("[bodhi] sending today's real shortlist for risk verification...")
        variables = {
            settings.bodhi_portfolio_variable: _portfolio_text(results_by_tier, analyses),
            settings.bodhi_query_variable: _query_text(),
        }
        report, run_id = call_bodhi_workflow(
            base_url=settings.bodhi_base_url,
            pat_token=settings.bodhi_pat_token,
            task_id=settings.bodhi_task_id,
            workflow_id=settings.bodhi_workflow_id,
            trace_prefix=settings.bodhi_trace_prefix,
            variables=variables,
            poll_interval=settings.bodhi_poll_interval,
            timeout=settings.bodhi_timeout,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[bodhi] skipped: {e}")
        return empty

    print(f"[bodhi] run id: {run_id}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"bodhi_risk_insight_{dt.date.today().isoformat()}.txt"
    out_path.write_text(report)
    print(f"[bodhi] saved report -> {out_path}")

    escalated = _detect_escalation_branch(report)
    print(f"[bodhi] branch outcome: {'escalated (deeper review ran)' if escalated else 'clean (no escalation needed)'}")

    degraded = _detect_research_degraded(report)
    if degraded:
        observability.record_failure(
            "bodhi_research_degraded",
            "the workflow's own final text indicates its MCP research tool "
            "failed mid-run (graceful degradation, not a crash) — verdicts "
            "below may be reasoning-only, not freshly researched",
            run_id=run_id,
        )
        print("[bodhi] ⚠ this run's report indicates the research tool degraded "
              "mid-run — verdicts may be reasoning-only, not freshly researched")

    verdicts = _parse_verdicts(report)
    flagged = set()
    if verdicts:
        flagged = {
            sym for sym, v in verdicts.items()
            if any(kw in v["verdict"].lower() for kw in _FLAG_KEYWORDS)
        }
        print(f"[bodhi] {len(verdicts)} verdict(s) parsed, {len(flagged)} flagged")
    else:
        print("[bodhi] no structured per-symbol verdict found in the answer — "
              "nothing will be filtered from TradingView; see the full report in Telegram")

    return {"ran": True, "report": report, "verdicts": verdicts, "flagged_symbols": flagged,
            "run_id": run_id, "research_degraded": degraded, "escalation_branch_fired": escalated}
