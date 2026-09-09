"""
test_verification_guardrail.py — containment for a confidently WRONG
answer (safety/verification_guardrail.py), distinct from the Orchestrator's
retry loop (which only helps when re-reasoning could plausibly change the
outcome — this doesn't apply when the underlying symbol itself is what
two independent tools couldn't confirm).
"""

import observability
from safety.verification_guardrail import contain_unverifiable_picks

TV_FLAG = "TradingView could not confirm this ticker as a real NSE equity"
MCP_FLAG = "MCP tool (local NIFTY50 universe check) could not confirm this ticker"


def _pick(symbol="FAKESTOCK", action="accumulate", confidence=85):
    return {"symbol": symbol, "action": action, "confidence": confidence}


def _analysis(symbol="FAKESTOCK", flags=None):
    return {"symbol": symbol, "data_quality": flags or []}


def test_overrides_a_confident_pick_when_both_independent_tools_failed():
    results_by_tier = {"moderate": {"shortlist": [_pick()]}}
    analyses = [_analysis(flags=[TV_FLAG, MCP_FLAG])]

    overridden = contain_unverifiable_picks(results_by_tier, analyses)

    pick = results_by_tier["moderate"]["shortlist"][0]
    assert overridden == 1
    assert pick["action"] == "avoid"
    assert pick["confidence"] <= 20
    assert pick["conviction"] == "LOW"
    assert pick["guardrail_override"] is True
    assert "guardrail" in pick["conviction_note"].lower()


def test_does_not_override_when_only_tradingview_flag_is_present():
    """The narrow trigger is deliberate: one tool's failure is already
    shown to the Decision Agent as context to weigh itself — only BOTH
    together bypass its judgment."""
    results_by_tier = {"moderate": {"shortlist": [_pick()]}}
    analyses = [_analysis(flags=[TV_FLAG])]

    overridden = contain_unverifiable_picks(results_by_tier, analyses)

    assert overridden == 0
    assert results_by_tier["moderate"]["shortlist"][0]["action"] == "accumulate"


def test_does_not_override_when_only_mcp_flag_is_present():
    results_by_tier = {"moderate": {"shortlist": [_pick()]}}
    analyses = [_analysis(flags=[MCP_FLAG])]

    assert contain_unverifiable_picks(results_by_tier, analyses) == 0


def test_does_not_override_a_clean_pick():
    results_by_tier = {"moderate": {"shortlist": [_pick()]}}
    analyses = [_analysis(flags=[])]

    assert contain_unverifiable_picks(results_by_tier, analyses) == 0


def test_never_raises_confidence_that_was_already_below_the_cap():
    results_by_tier = {"moderate": {"shortlist": [_pick(confidence=10)]}}
    analyses = [_analysis(flags=[TV_FLAG, MCP_FLAG])]

    contain_unverifiable_picks(results_by_tier, analyses)
    assert results_by_tier["moderate"]["shortlist"][0]["confidence"] == 10


def test_overrides_the_same_symbol_independently_across_multiple_tiers():
    results_by_tier = {
        "conservative": {"shortlist": [_pick(action="hold")]},
        "aggressive": {"shortlist": [_pick(action="accumulate")]},
    }
    analyses = [_analysis(flags=[TV_FLAG, MCP_FLAG])]

    overridden = contain_unverifiable_picks(results_by_tier, analyses)

    assert overridden == 2
    assert results_by_tier["conservative"]["shortlist"][0]["action"] == "avoid"
    assert results_by_tier["aggressive"]["shortlist"][0]["action"] == "avoid"


def test_only_the_flagged_symbol_is_touched_not_the_whole_shortlist():
    results_by_tier = {"moderate": {"shortlist": [_pick(symbol="GOOD", action="accumulate"),
                                                   _pick(symbol="FAKESTOCK", action="accumulate")]}}
    analyses = [_analysis(symbol="GOOD", flags=[]), _analysis(symbol="FAKESTOCK", flags=[TV_FLAG, MCP_FLAG])]

    contain_unverifiable_picks(results_by_tier, analyses)

    picks_by_symbol = {p["symbol"]: p for p in results_by_tier["moderate"]["shortlist"]}
    assert picks_by_symbol["GOOD"]["action"] == "accumulate"
    assert picks_by_symbol["FAKESTOCK"]["action"] == "avoid"


def test_a_real_override_is_logged_to_the_reliability_log():
    results_by_tier = {"moderate": {"shortlist": [_pick()]}}
    analyses = [_analysis(flags=[TV_FLAG, MCP_FLAG])]

    contain_unverifiable_picks(results_by_tier, analyses)

    logged = observability._FAILURE_LOG.read_text()
    assert "reliability_guardrail_unverifiable_pick" in logged
    assert "FAKESTOCK" in logged
