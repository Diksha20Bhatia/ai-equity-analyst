"""
verification_guardrail.py  —  containment for a confidently WRONG answer,
not just a thrown exception
=============================================================================
Every other check in this codebase either stops the pipeline (a raised
error) or asks the LLM to try again (the Orchestrator's retry loop —
agents/orchestrator_agent.py). Neither one covers a real, different
failure mode: the Decision Agent can return a perfectly well-shaped,
internally-consistent, CONFIDENT recommendation for a symbol that two
independent, non-LLM tools have already flagged as unable to confirm as
a real, tradeable NSE equity at all. A retry doesn't help here — asking
the same model to reconsider the same (possibly wrong) symbol doesn't
change what TradingView's symbol search or the local MCP universe check
already found. This is a DETERMINISTIC, POST-HOC override instead: no
LLM call, can't be reasoned around by a differently-worded retry, and it
runs regardless of how confident or well-argued the original verdict was.

Deliberately narrow trigger — BOTH independent verification tools must
have actually run and both must have failed (see run.py's STEP 3 loop:
TradingView's REST symbol-search and the local-only MCP server, two
genuinely different transports checking the same claim two different
ways). Either flag alone is a real but softer signal already shown to
the Decision Agent as context (agents/decision_agent.py's `verify_txt`)
for it to weigh; both flags together — two independent tools, two
different mechanisms, same negative finding — is the specific, narrow,
checkable condition this guardrail contains automatically rather than
leaving to the model's own judgment.
"""

import observability

_TRADINGVIEW_FLAG = "TradingView could not confirm this ticker as a real NSE equity"
_MCP_FLAG = "MCP tool (local NIFTY50 universe check) could not confirm this ticker"
_CAPPED_CONFIDENCE = 20  # comfortably below CONVICTION_THRESHOLD (70), so conviction also flips to LOW


def contain_unverifiable_picks(results_by_tier: dict, analyses: list) -> int:
    """
    For every pick, across every risk tier, whose underlying analysis was
    flagged by BOTH independent verification tools: force action="avoid"
    and cap confidence at _CAPPED_CONFIDENCE, overriding whatever the
    Decision Agent itself said — real containment, not a suggestion. Also
    recomputes conviction/conviction_note from the capped confidence
    (agents/decision_agent.py's own derivation rule), so a viewer never
    sees a HIGH-conviction badge next to an overridden pick.

    Returns the number of picks actually overridden (0 in the ordinary
    case — this never fires unless both real checks genuinely failed on
    the same symbol), so a caller/test can assert the guardrail actually
    did something when it's supposed to, not just that it ran.
    """
    quality_by_symbol = {a["symbol"]: (a.get("data_quality") or []) for a in analyses}
    overridden = 0

    for tier, result in results_by_tier.items():
        for pick in result.get("shortlist", []):
            flags = quality_by_symbol.get(pick["symbol"], [])
            if _TRADINGVIEW_FLAG not in flags or _MCP_FLAG not in flags:
                continue  # the narrow trigger: BOTH must have failed, not just one

            original_action = pick.get("action")
            original_confidence = pick.get("confidence")
            pick["action"] = "avoid"
            pick["confidence"] = min(pick.get("confidence") or 0, _CAPPED_CONFIDENCE)
            pick["conviction"] = "LOW"
            pick["conviction_note"] = (
                "Overridden by a reliability guardrail: two independent, non-LLM tools "
                "(TradingView symbol search and a local MCP universe check) could not "
                "confirm this ticker is a real, tradeable NSE equity, so the AI's own "
                "recommendation was NOT trusted as-is regardless of its stated confidence."
            )
            pick["guardrail_override"] = True
            overridden += 1

            observability.record_failure(
                "reliability_guardrail_unverifiable_pick",
                f"{pick['symbol']} ({tier}): overrode a confident '{original_action}' "
                f"call (confidence {original_confidence}) to 'avoid' — both independent "
                f"verification tools failed to confirm this ticker",
                symbol=pick["symbol"], tier=tier,
                original_action=original_action, original_confidence=original_confidence,
            )

    return overridden
