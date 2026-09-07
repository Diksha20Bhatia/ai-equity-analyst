"""
bodhi_risk_insights.py  —  send today's real shortlist to Bodhi for a
per-symbol risk-verification pass before it goes out to Telegram/TradingView
=====================================================================
Runs the Bodhi workflow ONCE PER SHORTLISTED SYMBOL — not once for the
whole portfolio blob. Each run gets its own Bodhi run ID, its own
Investmentportfolio variable holding exactly ONE stock's data, and
produces exactly one structured decision:

    {"decisions": [{"symbol", "decision": "proceed"|"blocked",
                     "reason", "research_confidence": "full"|"partial"}]}

Why per-symbol instead of one call for the whole shortlist: review
decisions in this use case are made at the symbol level (one stock's
risk doesn't change because it happened to be listed next to another
one), and a separate run per symbol gives a separate, independently
auditable Bodhi run ID per decision — you can open Bodhi's run history
and find exactly which run produced the verdict on any one stock. This
is done here, in the calling loop, rather than with a native Bodhi
Repeat/Sub-Workflow node — see integrations/bodhi_workflow/README.md for
why (short version: never having seen a real exported Repeat/Sub-Workflow
node, guessing its schema risks handing over something that silently
misbehaves; looping the same confirmed-working single-stock workflow from
here is the same result with zero schema risk).

A symbol whose Bodhi run fails or times out does NOT block the other
symbols — each one is attempted independently and gets its own
status ("ok" / "timeout" / "error"). A failed/timed-out symbol is
reported as decision "unverified", which alerts/approval_queue.py treats
as "needs a human look", distinct from a real "blocked" verdict.

Optional (BODHI_ENABLED=true) and best-effort at the whole-shortlist
level: verify_shortlist() never raises. If Bodhi isn't configured at
all, it returns ran=False immediately with no per-symbol calls made.
"""

import datetime as dt
import json
import re
from pathlib import Path

import observability
from config import settings
from data.text_safety import sanitize_for_prompt
from integrations.bodhi_client import call_bodhi_workflow

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

# Legacy verdict text (old workflow versions, or a model that ignores the
# new schema) that means "keep this off the actionable TradingView list" —
# used only as a fallback when a run's answer has no parseable "decisions".
_BLOCK_KEYWORDS = ("avoid", "high risk", "high-risk", "flag", "reject", "do not", "caution: exit")


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


def _query_text() -> str:
    """The BODHI_QUERY_VARIABLE value — sent unchanged on every per-symbol
    run. Explicitly asks for the structured decision schema the Contrarian
    step (and, on the escalation branch, the Escalation Analyst step)
    closes with, so this app can parse a real per-symbol business result
    instead of only narrative text."""
    return (
        f"{settings.bodhi_risk_query}\n\n"
        "In your final answer, include a JSON block, nothing after it, of the form:\n"
        '{"decisions": [{"symbol": "...", "decision": "proceed" | "blocked", '
        '"reason": "one or two sentences, cite the specific evidence", '
        '"research_confidence": "full" | "partial"}]}\n'
        "Use \"research_confidence\": \"partial\" if your research tool returned "
        "incomplete, stale, or no results for this stock — say so plainly rather "
        "than filling the gap with a guess."
    )


def _parse_decisions(answer_text: str) -> list | None:
    """Best-effort: find a JSON array of {"symbol", "decision", ...} objects
    anywhere in Bodhi's answer text (the new schema). Falls back to the
    older {"verdicts": [...]} schema (clear/caution/avoid) for a workflow
    that hasn't been rebuilt with the new prompts yet, mapping avoid/caution
    verdicts onto "blocked" and clear onto "proceed". Returns a list of
    decision dicts, or None if nothing parseable was found."""
    candidates = re.findall(r"\[[^\[\]]*\{.*?\}[^\[\]]*\]", answer_text, re.DOTALL)
    candidates += re.findall(r"\{.*\}", answer_text, re.DOTALL)

    for raw in candidates:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue

        if isinstance(parsed, dict) and isinstance(parsed.get("decisions"), list):
            out = []
            for item in parsed["decisions"]:
                if isinstance(item, dict) and item.get("symbol") and item.get("decision"):
                    out.append({
                        "symbol": item["symbol"],
                        "decision": str(item["decision"]).lower(),
                        "reason": str(item.get("reason", "")),
                        "research_confidence": str(item.get("research_confidence", "full")),
                    })
            if out:
                return out

        items = parsed.get("verdicts") if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else None)
        if isinstance(items, list):
            out = []
            for item in items:
                if isinstance(item, dict) and item.get("symbol") and item.get("verdict"):
                    verdict = str(item["verdict"]).lower()
                    blocked = verdict == "avoid" or any(kw in verdict for kw in _BLOCK_KEYWORDS)
                    out.append({
                        "symbol": item["symbol"],
                        "decision": "blocked" if blocked else "proceed",
                        "reason": str(item.get("notes", "")),
                        "research_confidence": "full",
                    })
            if out:
                return out

    return None


def _review_one_symbol(pick: dict) -> dict:
    """Runs the Bodhi workflow for exactly one stock and returns a
    structured, predictable result — never raises. `status` tells the
    caller whether the research/review actually completed:
      - "ok"      -- Bodhi answered; `decision` reflects its real verdict
      - "timeout" -- Bodhi didn't answer in time; `decision` is "unverified"
      - "error"   -- Bodhi call failed outright; `decision` is "unverified"
    "unverified" is deliberately a THIRD state, not folded into "blocked"
    or "proceed" — a symbol Bodhi never actually reviewed should never look
    identical to one it explicitly cleared or blocked."""
    symbol = pick["symbol"]
    variables = {
        settings.bodhi_portfolio_variable: json.dumps(pick, indent=2, default=str),
        settings.bodhi_query_variable: _query_text(),
    }
    try:
        report, client_reported_partial, run_id = call_bodhi_workflow(
            base_url=settings.bodhi_base_url,
            pat_token=settings.bodhi_pat_token,
            task_id=settings.bodhi_task_id,
            workflow_id=settings.bodhi_workflow_id,
            trace_prefix=f"{settings.bodhi_trace_prefix}-{symbol}",
            variables=variables,
            poll_interval=settings.bodhi_poll_interval,
            timeout=settings.bodhi_timeout,
        )
    except TimeoutError as e:
        run_id = getattr(e, "bodhi_run_id", None)
        print(f"[bodhi] {symbol}: timed out — {e} (run id: {run_id})")
        return {"symbol": symbol, "status": "timeout", "decision": "unverified",
                "reason": "Bodhi did not respond within the configured timeout.",
                "research_confidence": None, "report": None, "bodhi_run_id": run_id}
    except Exception as e:  # noqa: BLE001
        run_id = getattr(e, "bodhi_run_id", None)
        print(f"[bodhi] {symbol}: failed — {e} (run id: {run_id})")
        return {"symbol": symbol, "status": "error", "decision": "unverified",
                "reason": f"Bodhi call failed: {e}",
                "research_confidence": None, "report": None, "bodhi_run_id": run_id}

    # Bodhi's answer is built partly from live web research — externally
    # authored text this app then stores, displays, and forwards. It is
    # sanitised on the way IN for the same reason headlines are: a crawled
    # page can carry an injection payload, and "it came from our own
    # workflow" is not the same as "it is trusted content".
    report = sanitize_for_prompt(report, max_len=20000, source="bodhi_research")

    parsed = _parse_decisions(report)
    if parsed:
        d = parsed[0]
        # The client-level "partial" signal (Bodhi itself reported a
        # degraded run) overrides the model's own self-assessment — the
        # model can't know its research tool failed if it wasn't told, but
        # Bodhi's run status is ground truth for that.
        confidence = "partial" if client_reported_partial else d["research_confidence"]
        return {"symbol": symbol, "status": "ok", "decision": d["decision"],
                "reason": d["reason"], "research_confidence": confidence,
                "report": report, "bodhi_run_id": run_id}

    print(f"[bodhi] {symbol}: ran but no structured decision found in the answer (run id: {run_id})")
    return {"symbol": symbol, "status": "ok", "decision": "unverified",
            "reason": "Bodhi answered but the response had no parseable decision JSON.",
            "research_confidence": None, "report": report, "bodhi_run_id": run_id}


def _review_whole_portfolio(shortlist: list) -> list:
    """Platform-native loop mode (BODHI_PER_SYMBOL_RUNS=false): ONE workflow
    run for the entire shortlist, with Bodhi's own Repeat node iterating the
    array and its End node returning one decision per symbol.

    Same return shape as _review_one_symbol(), one entry per symbol, so
    nothing downstream needs to know which mode produced it. A symbol the
    workflow didn't return a decision for is reported "unverified" rather
    than dropped — a missing decision must never look like an absent
    problem."""
    variables = {
        settings.bodhi_portfolio_variable: json.dumps(shortlist, indent=2, default=str),
        settings.bodhi_query_variable: _query_text(),
    }
    symbols = [p["symbol"] for p in shortlist]

    try:
        report, client_reported_partial, run_id = call_bodhi_workflow(
            base_url=settings.bodhi_base_url,
            pat_token=settings.bodhi_pat_token,
            task_id=settings.bodhi_task_id,
            workflow_id=settings.bodhi_workflow_id,
            trace_prefix=f"{settings.bodhi_trace_prefix}-portfolio",
            variables=variables,
            poll_interval=settings.bodhi_poll_interval,
            timeout=settings.bodhi_timeout,
        )
    except TimeoutError as e:
        run_id = getattr(e, "bodhi_run_id", None)
        print(f"[bodhi] portfolio run timed out — {e} (run id: {run_id})")
        return [{"symbol": s, "status": "timeout", "decision": "unverified",
                  "reason": "Bodhi did not respond within the configured timeout.",
                  "research_confidence": None, "report": None, "bodhi_run_id": run_id} for s in symbols]
    except Exception as e:  # noqa: BLE001
        run_id = getattr(e, "bodhi_run_id", None)
        print(f"[bodhi] portfolio run failed — {e} (run id: {run_id})")
        return [{"symbol": s, "status": "error", "decision": "unverified",
                  "reason": f"Bodhi call failed: {e}",
                  "research_confidence": None, "report": None, "bodhi_run_id": run_id} for s in symbols]

    print(f"[bodhi] portfolio run id: {run_id}  <- this is the single run that drove "
          f"the platform's Repeat loop for all {len(symbols)} symbol(s)")
    report = sanitize_for_prompt(report, max_len=40000, source="bodhi_research")
    parsed = _parse_decisions(report) or []
    by_symbol = {d["symbol"]: d for d in parsed}

    out = []
    for symbol in symbols:
        d = by_symbol.get(symbol)
        if not d:
            out.append({"symbol": symbol, "status": "ok", "decision": "unverified",
                         "reason": "The workflow returned no decision for this symbol.",
                         "research_confidence": None, "report": report, "bodhi_run_id": run_id})
            continue
        confidence = "partial" if client_reported_partial else d["research_confidence"]
        out.append({"symbol": symbol, "status": "ok", "decision": d["decision"],
                     "reason": d["reason"], "research_confidence": confidence,
                     "report": report, "bodhi_run_id": run_id})
    return out


def verify_shortlist(results_by_tier: dict, analyses: list) -> dict:
    """Best-effort wrapper: never raises, even if every symbol's call fails.

    Returns:
      ran              -- True if Bodhi is configured and at least one
                           per-symbol call was attempted
      decisions        -- list of per-symbol result dicts (see
                           _review_one_symbol), one per shortlisted symbol
      flagged_symbols  -- set of symbols with decision == "blocked"
                           (excluded from the TradingView push)
      unverified_symbols -- set of symbols with decision == "unverified"
                           (not blocked, not cleared — Bodhi's review of
                           them didn't actually complete; shown separately
                           in the dashboard so a human knows to look closer)
      report           -- full narrative text, all symbols concatenated
                           and labelled, for the Telegram follow-up message
    """
    empty = {"ran": False, "decisions": [], "flagged_symbols": set(),
              "unverified_symbols": set(), "report": None}
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

    shortlist = _shortlist_payload(results_by_tier, analyses)
    if not shortlist:
        return empty

    if settings.bodhi_per_symbol_runs:
        print(f"[bodhi] reviewing {len(shortlist)} symbol(s), one Bodhi run each...")
        decisions = [_review_one_symbol(pick) for pick in shortlist]
    else:
        print(f"[bodhi] reviewing {len(shortlist)} symbol(s) in ONE run "
              "(workflow's own Repeat node drives the loop)...")
        decisions = _review_whole_portfolio(shortlist)

    flagged = {d["symbol"] for d in decisions if d["decision"] == "blocked"}
    unverified = {d["symbol"] for d in decisions if d["decision"] == "unverified"}
    ok_count = sum(1 for d in decisions if d["status"] == "ok")
    print(f"[bodhi] {ok_count}/{len(decisions)} symbol(s) reviewed successfully — "
          f"{len(flagged)} blocked, {len(unverified)} unverified")

    run_ids = sorted({d["bodhi_run_id"] for d in decisions if d.get("bodhi_run_id")})
    if run_ids:
        print(f"[bodhi] real run id(s) from this review — this is your execution "
              f"evidence: {', '.join(run_ids)}")
    # Recorded per-symbol, not just as a count: this is what
    # integrations/bodhi_workflow/collect_run_evidence.py reads back to build
    # a real, regeneratable evidence file from actual telemetry rather than a
    # manually-copied note.
    for d in decisions:
        observability.record_metric(
            "bodhi_run", symbol=d["symbol"], bodhi_run_id=d.get("bodhi_run_id"),
            status=d["status"], decision=d["decision"],
            mode="per_symbol" if settings.bodhi_per_symbol_runs else "platform_loop",
        )
    observability.record_metric(
        "bodhi_review", mode="per_symbol" if settings.bodhi_per_symbol_runs else "platform_loop",
        symbols=len(decisions), ok=ok_count, blocked=len(flagged), unverified=len(unverified),
        run_ids=run_ids,
    )

    report_parts = [
        f"*{d['symbol']}* — {d['decision'].upper()} ({d['status']})\n{d['reason']}"
        for d in decisions
    ]
    report = "\n\n".join(report_parts)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"bodhi_risk_insight_{dt.date.today().isoformat()}.txt"
    out_path.write_text(report)
    print(f"[bodhi] saved report -> {out_path}")

    return {"ran": True, "decisions": decisions, "flagged_symbols": flagged,
            "unverified_symbols": unverified, "report": report}
