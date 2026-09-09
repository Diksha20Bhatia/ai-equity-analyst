"""
test_bodhi_risk_insights.py — real failure-path detection for the MCP
research call, on the Python side.

Real behaviour under test: when the Bodhi workflow's own agents report
their research tool failed mid-run (graceful degradation, not a crash —
seen in a real live run of this exact workflow), this project detects
that from the report text and flags it, rather than silently treating a
degraded, reasoning-only answer the same as a freshly-researched one.
"""

from integrations.bodhi_risk_insights import (
    _detect_escalation_branch,
    _detect_research_degraded,
    _parse_verdicts,
)

# Captured verbatim (symbols/notes trimmed) from a real live run of the
# CURRENT 13-node graph (run id 5ca730af-ee54-4a77-af3a-df5ea10d1a67):
# the Escalation Analyst wrapped its answer in a markdown fence with
# prose before AND after the JSON, despite its prompt explicitly asking
# it not to — a real deviation, not a synthetic edge case.
REAL_FENCE_WRAPPED_ESCALATED_REPORT = (
    "Based on the deeper review analysis, here are the final verdicts in the required JSON format:\n\n"
    "```json\n"
    "{\n"
    '  "verdicts": [\n'
    '    {"symbol": "RELIANCE", "verdict": "caution", "notes": "Crude oil volatility remains a live risk."},\n'
    '    {"symbol": "ICICIBANK", "verdict": "avoid", "notes": "Position sizing inconsistency is critical."}\n'
    "  ]\n"
    "}\n"
    "```\n\n"
    "**Summary:**\n"
    "- **RELIANCE**: Assigned \"caution\" verdict due to commodity exposure risks\n"
    "- **ICICIBANK**: Assigned \"avoid\" verdict primarily due to severe position sizing mismatch\n"
)


def test_detects_a_real_degraded_research_phrase():
    report = (
        "I attempted to run the deeper review using the deep research "
        "tool, but encountered technical errors (invalid model "
        "configuration). Based on the available information..."
    )
    assert _detect_research_degraded(report) is True


def test_clean_research_backed_report_is_not_flagged():
    report = (
        "Based on real-time research into RELIANCE's refining margins "
        "and current crude oil pricing, the Contrarian Agent's concerns "
        "appear overstated given Q2 GRM expansion."
    )
    assert _detect_research_degraded(report) is False


def test_handles_empty_report():
    assert _detect_research_degraded("") is False
    assert _detect_research_degraded(None) is False


def test_parse_verdicts_extracts_a_well_formed_array():
    report = 'Some narrative text.\n{"verdicts": [{"symbol": "TCS", "verdict": "clear", "notes": "solid"}, ' \
             '{"symbol": "ICICIBANK", "verdict": "caution", "notes": "watch"}]}\nMore text.'
    verdicts = _parse_verdicts(report)
    assert verdicts["TCS"] == {"verdict": "clear", "notes": "solid"}
    assert verdicts["ICICIBANK"]["verdict"] == "caution"


def test_parse_verdicts_drops_a_malformed_item_but_keeps_the_valid_ones():
    """Real hardening of the Bodhi workflow-to-code boundary
    (schemas.BodhiVerdictItem): one item with a verdict value outside the
    documented clear/caution/avoid set must never silently reach the
    TradingView exclusion filter — it's dropped, the rest of the array is
    still used."""
    report = (
        '{"verdicts": ['
        '{"symbol": "TCS", "verdict": "clear", "notes": "ok"}, '
        '{"symbol": "BADCO", "verdict": "sell immediately!!"}'
        ']}'
    )
    verdicts = _parse_verdicts(report)
    assert "TCS" in verdicts
    assert "BADCO" not in verdicts


def test_parse_verdicts_returns_none_when_nothing_valid_is_found():
    assert _parse_verdicts("just a narrative paragraph, no JSON at all") is None


def test_parse_verdicts_repairs_a_paraphrased_verdict_instead_of_dropping_it():
    """Real repair, not just validate-then-drop: a model that paraphrases
    instead of using the exact documented word ("Avoid — high risk
    exposure" instead of "avoid") has its real, unambiguous intent
    recovered by keyword match rather than being discarded."""
    report = (
        '{"verdicts": ['
        '{"symbol": "TCS", "verdict": "clear", "notes": "ok"}, '
        '{"symbol": "JIOFIN", "verdict": "Avoid - high risk exposure", "notes": "regulatory overhang"}'
        ']}'
    )
    verdicts = _parse_verdicts(report)
    assert verdicts["JIOFIN"]["verdict"] == "avoid"
    assert verdicts["JIOFIN"]["notes"] == "regulatory overhang"


def test_parse_verdicts_handles_a_real_fence_wrapped_report_with_prose_padding():
    """Regression test for a real live deviation (see module docstring
    above): the Escalation Analyst's prompt explicitly forbids markdown
    fences, and it used one anyway, with prose both before and after the
    JSON. Tolerant regex extraction must still find the real verdicts —
    proven against the actual captured text, not a synthetic stand-in."""
    verdicts = _parse_verdicts(REAL_FENCE_WRAPPED_ESCALATED_REPORT)
    assert verdicts["RELIANCE"]["verdict"] == "caution"
    assert verdicts["ICICIBANK"]["verdict"] == "avoid"
    assert "Crude oil volatility" in verdicts["RELIANCE"]["notes"]


def test_detect_escalation_branch_matches_the_real_captured_framing():
    assert _detect_escalation_branch(REAL_FENCE_WRAPPED_ESCALATED_REPORT) is True


def test_parse_verdicts_still_drops_a_truly_unrecognisable_verdict():
    report = (
        '{"verdicts": ['
        '{"symbol": "TCS", "verdict": "clear", "notes": "ok"}, '
        '{"symbol": "BADCO", "verdict": "strong buy!!"}'
        ']}'
    )
    verdicts = _parse_verdicts(report)
    assert "BADCO" not in verdicts
    assert "TCS" in verdicts
