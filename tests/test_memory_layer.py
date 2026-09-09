"""
test_memory_layer.py — the multi-type memory framework.

Real behaviour under test: build_delta() (short-term, vs the single last
call) and consolidate_trend() (across the last N calls) are genuinely
different things — a trend needs at least 2 records and correctly
classifies a real metric as trending up/down/flat from its own first and
last real values, not an LLM's paraphrase.

Runs against an isolated temp store (monkeypatched), never the real
memory/_store data.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

import memory.memory_layer as ml
from memory.memory_layer import MemoryLayer


@pytest.fixture()
def isolated_memory(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(ml, "STORE_DIR", str(tmp))
    monkeypatch.setattr(ml, "JSON_PATH", str(tmp / "memory.json"))
    mem = MemoryLayer()
    yield mem
    shutil.rmtree(tmp, ignore_errors=True)


def test_build_delta_with_no_prior_context(isolated_memory):
    assert "no prior context" in isolated_memory.build_delta("NEWSTOCK", 100, {})


def test_build_delta_compares_against_the_single_last_call(isolated_memory):
    isolated_memory.store("X", {"price": 100.0, "fundamental_score": 5, "technical_score": 5, "risk_score": 5, "action": "hold"})
    delta = isolated_memory.build_delta("X", 110.0, {"fundamental_score": 5, "technical_score": 6, "risk_score": 5})
    assert "100.00 -> 110.00" in delta
    assert "+10.0%" in delta


def test_consolidate_trend_needs_at_least_two_records(isolated_memory):
    isolated_memory.store("Y", {"price": 100.0, "fundamental_score": 5, "technical_score": 5, "risk_score": 5, "action": "hold"})
    assert isolated_memory.consolidate_trend("Y") == ""


def test_consolidate_trend_detects_a_real_upward_price_trend(isolated_memory):
    for price in (100, 105, 110, 120):
        isolated_memory.store("Z", {"price": price, "fundamental_score": 5, "technical_score": 5, "risk_score": 5, "action": "hold"})
    trend = isolated_memory.consolidate_trend("Z")
    assert "price trend: up" in trend
    assert "100.00 -> ₹120.00" in trend


def test_consolidate_trend_detects_flat_when_change_is_small(isolated_memory):
    for price in (100, 100.2, 99.9, 100.1):
        isolated_memory.store("FLAT", {"price": price, "fundamental_score": 5, "technical_score": 5, "risk_score": 5, "action": "hold"})
    trend = isolated_memory.consolidate_trend("FLAT")
    assert "price trend: flat" in trend


def test_consolidate_trend_detects_accelerating_momentum(isolated_memory):
    """Real richer context than a plain first-to-last comparison: the
    second half of the history moved much more than the first half."""
    for price in (100, 105, 110, 130):
        isolated_memory.store("ACCEL", {"price": price, "fundamental_score": 5, "technical_score": 5, "risk_score": 5, "action": "hold"})
    trend = isolated_memory.consolidate_trend("ACCEL")
    assert "momentum: accelerating" in trend


def test_consolidate_trend_detects_decelerating_momentum(isolated_memory):
    for price in (100, 120, 125, 128):
        isolated_memory.store("DECEL", {"price": price, "fundamental_score": 5, "technical_score": 5, "risk_score": 5, "action": "hold"})
    trend = isolated_memory.consolidate_trend("DECEL")
    assert "momentum: decelerating" in trend


def test_consolidate_trend_detects_a_reversal_a_first_to_last_comparison_would_miss(isolated_memory):
    """The classic case this feature exists for: overall change looks
    small (100 -> 90 over 4 calls), but the real shape is a climb THEN a
    sharp reversal — invisible to build_delta() or a plain endpoint trend."""
    for price in (100, 110, 120, 90):
        isolated_memory.store("REV", {"price": price, "fundamental_score": 5, "technical_score": 5, "risk_score": 5, "action": "hold"})
    trend = isolated_memory.consolidate_trend("REV")
    assert "reversed direction" in trend


def test_consolidate_trend_omits_momentum_with_fewer_than_four_records(isolated_memory):
    for price in (100, 105, 110):
        isolated_memory.store("SHORT", {"price": price, "fundamental_score": 5, "technical_score": 5, "risk_score": 5, "action": "hold"})
    trend = isolated_memory.consolidate_trend("SHORT")
    assert "momentum" not in trend


def test_store_repairs_a_decorated_numeric_string_instead_of_rejecting(isolated_memory):
    """Real repair, not just validate-then-reject: a price/score given as
    a decorated string ("₹1,292.50") has an unambiguous real value —
    strip the decoration and store it as a real number, don't refuse a
    genuinely usable record just because upstream formatting added a
    currency symbol."""
    isolated_memory.store("REPAIRED", {
        "price": "₹1,292.50", "fundamental_score": "7", "technical_score": 6, "risk_score": 4, "action": "hold",
    })
    delta = isolated_memory.build_delta("REPAIRED", 1300.0, {})
    assert "1292.50 -> 1300.00" in delta


def test_store_refuses_a_malformed_record(isolated_memory):
    """Typed contract (schemas.MemoryRecord): a record with the wrong
    type for a score is rejected loudly at write time, rather than
    silently corrupting every future build_delta()/consolidate_trend()
    read of this symbol."""
    with pytest.raises(RuntimeError):
        isolated_memory.store("BAD", {"price": 100.0, "fundamental_score": "high", "action": "hold"})
    assert isolated_memory.build_delta("BAD", 100.0, {}) == "BAD: no prior context."


def test_goal_status_empty_when_no_prior_record_exists(isolated_memory):
    assert isolated_memory.goal_status("NEWSTOCK", 100) == ""


def test_goal_status_empty_when_prior_records_carry_no_target_or_invalidation(isolated_memory):
    """A symbol WITH history but never shortlisted (no target_price/
    invalidation stored) has no open goal — not an error, just nothing
    to resurface, same as build_delta()'s "no prior context" bootstrap."""
    isolated_memory.store("X", {"price": 100.0, "fundamental_score": 5, "technical_score": 5, "risk_score": 5, "action": "not_shortlisted"})
    assert isolated_memory.goal_status("X", 105) == ""


def test_goal_status_reports_a_real_open_target_not_yet_reached(isolated_memory):
    isolated_memory.store("RELIANCE", {
        "price": 2800.0, "fundamental_score": 7, "technical_score": 6, "risk_score": 4,
        "action": "moderate:accumulate", "target_price": 3000.0,
        "invalidation": "a break below the 200-day moving average",
    })
    status = isolated_memory.goal_status("RELIANCE", 2900.0)
    assert "₹3000.00" in status or "3,000.00" in status or "3000.00" in status
    assert "away from it" in status
    assert "2800.00 -> ₹2900.00" in status
    assert "+3.6%" in status
    assert "200-day moving average" in status


def test_goal_status_flags_when_the_real_price_has_reached_or_passed_the_target(isolated_memory):
    isolated_memory.store("TCS", {
        "price": 3500.0, "fundamental_score": 7, "technical_score": 6, "risk_score": 3,
        "action": "moderate:accumulate", "target_price": 3700.0, "invalidation": "guidance cut",
    })
    status = isolated_memory.goal_status("TCS", 3750.0)
    assert "REACHED OR PASSED" in status


def test_goal_status_looks_back_further_than_the_default_trend_window(isolated_memory):
    """A goal set well before the last 5 calls (consolidate_trend's
    default window) is still open and must still be found — that's the
    whole point of a separate, longer lookback for goal tracking."""
    isolated_memory.store("OLDGOAL", {
        "price": 500.0, "fundamental_score": 5, "technical_score": 5, "risk_score": 5,
        "action": "moderate:accumulate", "target_price": 600.0, "invalidation": "margin compression",
    })
    for price in (505, 510, 515, 520, 525, 530, 535):  # 7 more calls with no target/invalidation
        isolated_memory.store("OLDGOAL", {
            "price": price, "fundamental_score": 5, "technical_score": 5, "risk_score": 5,
            "action": "not_shortlisted",
        })
    status = isolated_memory.goal_status("OLDGOAL", 540.0)
    assert "margin compression" in status
    assert "600.00" in status


def test_goal_status_finds_the_most_recent_open_goal_when_several_exist(isolated_memory):
    isolated_memory.store("MULTI", {
        "price": 100.0, "fundamental_score": 5, "technical_score": 5, "risk_score": 5,
        "action": "moderate:accumulate", "target_price": 110.0, "invalidation": "first goal",
    })
    isolated_memory.store("MULTI", {
        "price": 108.0, "fundamental_score": 5, "technical_score": 5, "risk_score": 5,
        "action": "moderate:accumulate", "target_price": 130.0, "invalidation": "second, newer goal",
    })
    status = isolated_memory.goal_status("MULTI", 120.0)
    assert "second, newer goal" in status
    assert "first goal" not in status


def test_consolidate_trend_is_per_symbol_isolated(isolated_memory):
    isolated_memory.store("A", {"price": 100, "fundamental_score": 5, "technical_score": 5, "risk_score": 5, "action": "hold"})
    isolated_memory.store("A", {"price": 200, "fundamental_score": 5, "technical_score": 5, "risk_score": 5, "action": "hold"})
    isolated_memory.store("B", {"price": 50, "fundamental_score": 5, "technical_score": 5, "risk_score": 5, "action": "hold"})
    isolated_memory.store("B", {"price": 51, "fundamental_score": 5, "technical_score": 5, "risk_score": 5, "action": "hold"})

    trend_a = isolated_memory.consolidate_trend("A")
    trend_b = isolated_memory.consolidate_trend("B")
    assert "up" in trend_a
    assert "50.00" in trend_b and "100.00" not in trend_b
