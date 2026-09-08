"""
bodhi_risk_insights.py  —  send today's real shortlist to Bodhi for a
risk-verification pass before it goes out to Telegram/TradingView
=====================================================================
Sends today's real, deduped shortlist (every field the Decision Agent
actually produced — bull/bear case, opportunity score, target price,
₹ allocation, sector) to a confirmed-working, custom-built Bodhi
workflow: Start Node -> Risk Analyst Agent (does live web research per
stock via the bodhi-deep-research MCP tool) -> Contrarian Agent (red-team
review of the Risk Analyst's findings) -> End Node, and pulls a
per-symbol verdict out of the Contrarian's answer.

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

from config import settings
from integrations.bodhi_client import call_bodhi_workflow

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


def _parse_verdicts(answer_text: str) -> dict | None:
    """Best-effort: find a JSON array of {"symbol", "verdict", ...} objects
    anywhere in Bodhi's answer text. Returns {symbol: {"verdict", "notes"}},
    or None if nothing that shape could be found — callers must treat None
    as "no verdict available", not as "everything's clear"."""
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
            if isinstance(item, dict) and item.get("symbol") and item.get("verdict"):
                verdicts[item["symbol"]] = {
                    "verdict": str(item["verdict"]),
                    "notes": str(item.get("notes", "")),
                }
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
    """
    empty = {"ran": False, "report": None, "verdicts": None, "flagged_symbols": set(), "run_id": None}
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

    return {"ran": True, "report": report, "verdicts": verdicts, "flagged_symbols": flagged, "run_id": run_id}
