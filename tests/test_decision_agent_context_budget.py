"""
test_decision_agent_context_budget.py — the explicit, ENFORCED context
budget on what enters the Decision Agent's prompt per stock.

Real behaviour under test: a per-field ceiling (retrieved KB notes,
memory delta/trend lines) is actually cut, with a visible marker, not
just described as a good idea — a real constraint (this call is batched
across the whole watchlist, so any unbounded per-stock field multiplies)
enforced at the point content is assembled, before any call is made.
"""

from agents.decision_agent import DecisionAgent, _budget_trim, _CONTEXT_BUDGET_CHARS_PER_FIELD


def test_short_text_passes_through_unchanged():
    text = "a short, real memory delta line"
    assert _budget_trim(text) == text


def test_long_text_is_actually_cut_with_a_visible_marker():
    text = "x" * (_CONTEXT_BUDGET_CHARS_PER_FIELD + 500)
    trimmed = _budget_trim(text)
    assert len(trimmed) < len(text)
    assert "trimmed for context budget" in trimmed


def test_trim_respects_a_custom_limit():
    trimmed = _budget_trim("y" * 100, max_chars=20)
    assert len(trimmed) <= 20 + len(" …[trimmed for context budget]")
    assert trimmed.startswith("y" * 20)


ANALYSES = [{
    "symbol": "RELIANCE", "sector": "Energy",
    "fundamental": {"score": 7, "verdict": "solid"},
    "technical": {"score": 6, "read": "uptrend"},
    "risk": {"risk_score": 4, "flags": []},
    "event": {"sentiment": "positive", "event_type": "earnings", "materiality": "medium", "summary": "beat"},
    "research_note": "anomaly",
    "market_stats": {"current_price": 2900},
    "data_quality": [],
}]
PROFILE = {"risk_appetite": "moderate", "capital_numeric": 100000, "max_position_pct": 20}
MARKET_CTX = {"nifty_pct_change": 0.5, "nifty_trend": "up", "nifty_volatility_pct": 12}


def test_a_real_oversized_memory_delta_is_trimmed_inside_the_actual_prompt():
    """Not just a unit test of the helper in isolation — proves the
    budget is really wired into _build_prompt()'s own assembly, using
    the same real deltas run.py would pass in."""
    agent = DecisionAgent()
    huge_delta = "RELIANCE: " + "very long real historical detail " * 50
    prompt = agent._build_prompt(
        ANALYSES, PROFILE, MARKET_CTX, memory_deltas=[huge_delta], top_n=3,
    )
    assert "trimmed for context budget" in prompt
    assert huge_delta not in prompt  # the untrimmed original never reaches the prompt


# ----------------------------------------------------------------------
# The budget covers EVERY variable-length field that enters the prompt,
# not just retrieved KB notes and memory deltas — the real gap a partial
# cap would leave: an LLM-authored field (bull/bear case, key risk, an
# event summary, a research note) is exactly as unbounded in principle as
# a memory delta, and multiplies the same way across a batched watchlist.
# ----------------------------------------------------------------------
HUGE = "a real, long piece of text that keeps going " * 40  # ~1,880 chars, well over the 500-char cap


def test_an_oversized_bull_and_bear_case_are_both_trimmed():
    agent = DecisionAgent()
    prompt = agent._build_prompt(
        ANALYSES, PROFILE, MARKET_CTX, memory_deltas=[], top_n=3,
        bull_analysis={"RELIANCE": {"bull_strength": 80, "bull_case": HUGE, "bull_target_price": 3000}},
        bear_analysis={"RELIANCE": {"bear_severity": 50, "bear_case": HUGE, "key_risk": HUGE}},
    )
    assert HUGE not in prompt
    assert "trimmed for context budget" in prompt


def test_an_oversized_event_summary_is_trimmed():
    agent = DecisionAgent()
    analyses = [dict(ANALYSES[0], event={**ANALYSES[0]["event"], "summary": HUGE})]
    prompt = agent._build_prompt(analyses, PROFILE, MARKET_CTX, memory_deltas=[], top_n=3)
    assert HUGE not in prompt
    assert "trimmed for context budget" in prompt


def test_an_oversized_research_note_is_trimmed():
    agent = DecisionAgent()
    analyses = [dict(ANALYSES[0], research_note=HUGE)]
    prompt = agent._build_prompt(analyses, PROFILE, MARKET_CTX, memory_deltas=[], top_n=3)
    assert HUGE not in prompt


def test_an_oversized_profile_constraint_is_trimmed():
    """Not LLM output — investor-typed free text — but still unbounded
    length entering the same batched prompt, so it gets the same cap."""
    agent = DecisionAgent()
    profile = {**PROFILE, "constraints": HUGE}
    prompt = agent._build_prompt(ANALYSES, profile, MARKET_CTX, memory_deltas=[], top_n=3)
    assert HUGE not in prompt


def test_an_oversized_retry_feedback_is_trimmed():
    agent = DecisionAgent()
    prompt = agent._build_prompt(
        ANALYSES, PROFILE, MARKET_CTX, memory_deltas=[], top_n=3, feedback=HUGE,
    )
    assert HUGE not in prompt


def test_a_persisted_goal_status_is_surfaced_and_trimmed():
    """goal_status is attached to the analysis dict by run.py (from
    memory/memory_layer.py::goal_status()) — proves _build_prompt()
    actually surfaces it, labelled, and still under the same cap."""
    agent = DecisionAgent()
    analyses = [dict(ANALYSES[0], goal_status="On 2026-09-01 this stock was given a target price of ₹3,000.")]
    prompt = agent._build_prompt(analyses, PROFILE, MARKET_CTX, memory_deltas=[], top_n=3)
    assert "Persisted goal from a prior call" in prompt
    assert "₹3,000" in prompt

    huge_goal = dict(ANALYSES[0], goal_status=HUGE)
    prompt2 = agent._build_prompt([huge_goal], PROFILE, MARKET_CTX, memory_deltas=[], top_n=3)
    assert HUGE not in prompt2
    assert "trimmed for context budget" in prompt2


def test_no_goal_block_when_there_is_no_persisted_goal():
    """A stock with no open goal (goal_status is falsy/absent) gets no
    goal section at all — same "don't invent it" discipline as every
    other optional block in this prompt (live quote, verify, retrieval)."""
    agent = DecisionAgent()
    prompt = agent._build_prompt(ANALYSES, PROFILE, MARKET_CTX, memory_deltas=[], top_n=3)
    assert "Persisted goal from a prior call" not in prompt
