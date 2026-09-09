"""
test_schemas.py — the typed contracts at every agent-to-agent and
workflow-to-code boundary (schemas.py). Each model here gates a real
handoff point elsewhere in the codebase — see config.py (Gateway),
agents/bull_case_agent.py + bear_case_agent.py, agents/orchestrator_agent.py
(Decision), and integrations/bodhi_risk_insights.py (Bodhi verdicts).
"""

import pytest

from schemas import (
    BearCaseItem,
    BodhiVerdictItem,
    BullCaseItem,
    DecisionResult,
    GatewayChatCompletion,
    McpVerifySymbolInput,
    MemoryRecord,
    ValidationError,
)


def test_gateway_response_accepts_the_documented_shape():
    parsed = GatewayChatCompletion.model_validate({
        "choices": [{"message": {"content": "hello"}}],
        "usage": {"total_tokens": 42},
    })
    assert parsed.choices[0].message.content == "hello"


def test_gateway_response_rejects_missing_choices():
    with pytest.raises(ValidationError):
        GatewayChatCompletion.model_validate({"usage": {}})


def test_bull_case_item_rejects_out_of_range_strength():
    with pytest.raises(ValidationError):
        BullCaseItem.model_validate({"bull_case": "x", "bull_target_price": 100, "bull_strength": 150})


def test_bull_case_item_accepts_valid_shape():
    item = BullCaseItem.model_validate({"bull_case": "x", "bull_target_price": 100, "bull_strength": 80})
    assert item.bull_strength == 80


def test_bear_case_item_rejects_empty_case_text():
    with pytest.raises(ValidationError):
        BearCaseItem.model_validate({"bear_case": "", "bear_severity": 40})


def test_decision_result_rejects_invalid_action():
    with pytest.raises(ValidationError):
        DecisionResult.model_validate({"shortlist": [{
            "symbol": "TCS", "opportunity_score": 80, "confidence": 70,
            "action": "buy",  # not one of accumulate/hold/avoid
            "bull_case": "x", "bear_case": "y", "thesis": "z",
            "target_price": 100, "portfolio_fit": "good",
        }]})


def test_decision_result_accepts_valid_shortlist():
    result = DecisionResult.model_validate({"shortlist": [{
        "symbol": "TCS", "opportunity_score": 80, "confidence": 70,
        "action": "accumulate", "bull_case": "x", "bear_case": "y", "thesis": "z",
        "target_price": 100, "portfolio_fit": "good",
    }]})
    assert result.shortlist[0].symbol == "TCS"


def test_bodhi_verdict_item_rejects_unrecognised_verdict_value():
    with pytest.raises(ValidationError):
        BodhiVerdictItem.model_validate({"symbol": "TCS", "verdict": "sell now!!"})


def test_bodhi_verdict_item_accepts_documented_values():
    for v in ("clear", "caution", "avoid"):
        assert BodhiVerdictItem.model_validate({"symbol": "TCS", "verdict": v}).verdict == v


def test_memory_record_rejects_wrong_type_for_a_score():
    with pytest.raises(ValidationError):
        MemoryRecord.model_validate({"date": "2026-09-08", "fundamental_score": "high"})


def test_memory_record_accepts_a_real_shaped_record():
    rec = MemoryRecord.model_validate({
        "date": "2026-09-08", "price": 1292.0, "fundamental_score": 7,
        "technical_score": 6, "risk_score": 4, "action": "moderate:accumulate",
    })
    assert rec.price == 1292.0


def test_memory_record_accepts_a_persisted_goal():
    """target_price/invalidation — memory/memory_layer.py::goal_status()'s
    persisted-goal-tracking fields, carried forward from the Decision
    Agent's own real shortlist output."""
    rec = MemoryRecord.model_validate({
        "date": "2026-09-08", "price": 1292.0, "fundamental_score": 7,
        "technical_score": 6, "risk_score": 4, "action": "moderate:accumulate",
        "target_price": 1450.0, "invalidation": "a break below the 50-day moving average",
    })
    assert rec.target_price == 1450.0
    assert rec.invalidation == "a break below the 50-day moving average"


def test_memory_record_defaults_the_goal_fields_to_none_when_absent():
    rec = MemoryRecord.model_validate({"date": "2026-09-08", "action": "not_shortlisted"})
    assert rec.target_price is None
    assert rec.invalidation is None


def test_mcp_verify_symbol_input_repairs_a_decorated_symbol():
    """The real input-side repair a model-invoked tool call needs: a
    prefixed/lowercase/whitespace-padded symbol has an unambiguous real
    ticker underneath."""
    repaired = McpVerifySymbolInput.repair("nse:reliance")
    assert repaired.symbol == "RELIANCE"

    repaired = McpVerifySymbolInput.repair(" Tcs \n")
    assert repaired.symbol == "TCS"


def test_mcp_verify_symbol_input_refuses_when_nothing_usable_remains():
    assert McpVerifySymbolInput.repair("@@@---") is None
    assert McpVerifySymbolInput.repair("") is None
    assert McpVerifySymbolInput.repair(None) is None
