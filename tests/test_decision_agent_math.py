"""
test_decision_agent_math.py — the plain-Python money math.

The Decision Agent never invents share quantities or a capital split —
_suggest_quantity() and _allocate_capital() are deterministic formulas.
Real behaviour under test: position sizing tightens for risky stocks,
respects the investor's own max_position_pct cap, and the whole
shortlist's capital allocation never exceeds stated capital or a single
stock's position cap, with real values, not mocks.
"""

from agents.decision_agent import DecisionAgent


def test_suggest_quantity_returns_none_without_capital_profile():
    result = DecisionAgent._suggest_quantity(2900, {}, risk_percent=20)
    assert result["max"] is None
    assert "profile" in result["note"]


def test_suggest_quantity_caps_high_risk_stock_more_tightly():
    profile = {"capital_numeric": 100000, "max_position_pct": 20, "risk_appetite": "moderate"}
    low_risk = DecisionAgent._suggest_quantity(100, profile, risk_percent=10)
    high_risk = DecisionAgent._suggest_quantity(100, profile, risk_percent=70)
    assert high_risk["max"] < low_risk["max"]


def test_suggest_quantity_never_exceeds_max_position_pct_of_capital():
    profile = {"capital_numeric": 100000, "max_position_pct": 15, "risk_appetite": "aggressive"}
    result = DecisionAgent._suggest_quantity(50, profile, risk_percent=5)
    max_investment = result["max"] * 50
    assert max_investment <= 100000 * 0.15 + 1e-6


def test_allocate_capital_never_exceeds_stated_capital():
    shortlist = [
        {"symbol": "A", "opportunity_score": 90, "current_price": 100},
        {"symbol": "B", "opportunity_score": 60, "current_price": 200},
        {"symbol": "C", "opportunity_score": 30, "current_price": 50},
    ]
    summary = DecisionAgent._allocate_capital(shortlist, capital=100000, max_position_pct=50)
    assert summary["total_allocated"] <= 100000
    assert sum(p["allocated_amount"] for p in shortlist) == summary["total_allocated"]


def test_allocate_capital_respects_per_stock_position_cap():
    shortlist = [{"symbol": "A", "opportunity_score": 100, "current_price": 100}]
    summary = DecisionAgent._allocate_capital(shortlist, capital=100000, max_position_pct=10)
    # One dominant pick still can't exceed its own 10% cap even though
    # it's the only name in the shortlist.
    assert shortlist[0]["allocated_amount"] <= 10000 + 1


def test_allocate_capital_higher_opportunity_gets_bigger_share():
    shortlist = [
        {"symbol": "HIGH", "opportunity_score": 90, "current_price": 100},
        {"symbol": "LOW", "opportunity_score": 20, "current_price": 100},
    ]
    DecisionAgent._allocate_capital(shortlist, capital=100000, max_position_pct=80)
    assert shortlist[0]["allocated_amount"] > shortlist[1]["allocated_amount"]


def test_allocate_capital_handles_missing_profile_gracefully():
    shortlist = [{"symbol": "A", "opportunity_score": 50, "current_price": 100}]
    summary = DecisionAgent._allocate_capital(shortlist, capital=None, max_position_pct=None)
    assert summary["total_allocated"] is None
