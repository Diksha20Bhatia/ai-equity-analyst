"""
test_scenario_bodhi_branches.py — verify_shortlist() against BOTH real
branch outcomes of the Bodhi Workflow Builder graph
(integrations/bodhi_workflow/workflow-AI-Equity-Analyst-846bb836-ab0a-4b48-9226-1253617a3699.json),
not just the successful matched run the certification's own analyzer
already confirms from the exported graph.

The workflow itself can't be re-run from a unit test (it's a live Bodhi
call), but its two real, documented output shapes CAN be exercised here
by mocking integrations.bodhi_client.call_bodhi_workflow() to return
each one verbatim — the CLEAN (non-escalation) shape, where
Set Combine Final Verdict passes the Contrarian's original text straight
through, and the ESCALATED shape, where it's the Escalation Analyst's
deeper-review text instead (see integrations/bodhi_workflow/README.md's
"branch contract" section for the exact fields each shape carries).
"""

from unittest.mock import patch

from integrations.bodhi_risk_insights import verify_shortlist

RESULTS_BY_TIER = {"moderate": {"shortlist": [{"symbol": "ICICIBANK"}, {"symbol": "JIOFIN"}]}}
ANALYSES = [{"symbol": "ICICIBANK", "sector": "Financials"}, {"symbol": "JIOFIN", "sector": "Financials"}]

# The CLEAN path: Contrarian's own text passes straight through
# Set Combine Final Verdict — no escalation framing, no degraded-tool phrasing.
CLEAN_REPORT = (
    'Based on real-time research into ICICIBANK and JIOFIN, neither raises material '
    'concern at current levels.\n'
    '{"verdicts": [{"symbol": "ICICIBANK", "verdict": "clear", "notes": "solid fundamentals"}, '
    '{"symbol": "JIOFIN", "verdict": "clear", "notes": "no red flags"}]}'
)

# The ESCALATED path: opens with the Escalation Analyst's own framing
# (the real, documented operator-visible signal the branch fired) and
# includes a degraded-research phrase from a real live run's tool failure.
ESCALATED_REPORT = (
    'Based on the deeper review of the Contrarian Agent\'s flagged concerns, I attempted '
    'to run further research but encountered technical errors (invalid model configuration). '
    'Reasoning from the available information instead:\n'
    '{"verdicts": [{"symbol": "ICICIBANK", "verdict": "caution", "notes": "rate sensitivity"}, '
    '{"symbol": "JIOFIN", "verdict": "avoid", "notes": "unresolved regulatory overhang"}]}'
)


def _mock_settings(monkeypatch):
    import config
    monkeypatch.setattr(config.settings, "bodhi_enabled", True)
    monkeypatch.setattr(config.settings, "bodhi_pat_token", "test-token")
    monkeypatch.setattr(config.settings, "bodhi_task_id", "test-task")
    monkeypatch.setattr(config.settings, "bodhi_workflow_id", "test-workflow")
    monkeypatch.setattr(config.settings, "bodhi_base_url", "https://fake-bodhi.example")


def test_clean_non_escalation_branch_outcome(monkeypatch, tmp_path):
    _mock_settings(monkeypatch)
    import integrations.bodhi_risk_insights as bri
    monkeypatch.setattr(bri, "OUTPUT_DIR", tmp_path)
    with patch.object(bri, "call_bodhi_workflow", return_value=(CLEAN_REPORT, "run-clean-001")):
        result = verify_shortlist(RESULTS_BY_TIER, ANALYSES)

    assert result["ran"] is True
    assert result["research_degraded"] is False
    assert result["escalation_branch_fired"] is False
    assert result["verdicts"]["ICICIBANK"]["verdict"] == "clear"
    assert result["flagged_symbols"] == set()  # nothing flagged — TradingView push unaffected


def test_escalated_branch_outcome_with_degraded_research(monkeypatch, tmp_path):
    _mock_settings(monkeypatch)
    import integrations.bodhi_risk_insights as bri
    monkeypatch.setattr(bri, "OUTPUT_DIR", tmp_path)
    with patch.object(bri, "call_bodhi_workflow", return_value=(ESCALATED_REPORT, "run-escalated-002")):
        result = verify_shortlist(RESULTS_BY_TIER, ANALYSES)

    assert result["ran"] is True
    assert result["escalation_branch_fired"] is True
    # Real failure-path detection: the escalation branch's own text signals
    # its research tool degraded mid-run — must be surfaced, not silently
    # treated the same as the clean path above.
    assert result["research_degraded"] is True
    assert result["verdicts"]["JIOFIN"]["verdict"] == "avoid"
    # "avoid" is the one verdict value that excludes a symbol from TradingView.
    assert result["flagged_symbols"] == {"JIOFIN"}


def test_both_branches_produce_a_real_run_id_for_operator_lookup(monkeypatch, tmp_path):
    """Both shapes must carry Bodhi's real run id through — the same UUID
    an operator would open in Bodhi's own run list — regardless of which
    branch fired."""
    _mock_settings(monkeypatch)
    import integrations.bodhi_risk_insights as bri
    monkeypatch.setattr(bri, "OUTPUT_DIR", tmp_path)

    with patch.object(bri, "call_bodhi_workflow", return_value=(CLEAN_REPORT, "run-clean-001")):
        clean = verify_shortlist(RESULTS_BY_TIER, ANALYSES)
    with patch.object(bri, "call_bodhi_workflow", return_value=(ESCALATED_REPORT, "run-escalated-002")):
        escalated = verify_shortlist(RESULTS_BY_TIER, ANALYSES)

    assert clean["run_id"] == "run-clean-001"
    assert escalated["run_id"] == "run-escalated-002"
