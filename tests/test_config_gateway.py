"""
test_config_gateway.py — config.call_llm()'s typed error classification
and response-shape validation (schemas.GatewayChatCompletion).

Real behaviour under test: a 401/403 from Bodhi's LLM Gateway is an
identity/permissions problem, not a transient failure — it must raise
BodhiGatewayAuthError immediately, with exactly one HTTP attempt, never
retried. A 2xx response that doesn't match the documented OpenAI-
compatible chat/completions shape must raise BodhiGatewayShapeError
rather than a raw KeyError. A well-formed response must parse cleanly
and return (text, usage). No real network call anywhere — requests.post
is mocked throughout.
"""

import requests

import config
import observability
from config import (
    BodhiGatewayAuthError,
    BodhiGatewayBudgetExceededError,
    BodhiGatewayShapeError,
    call_llm,
)


class _FakeResponse:
    def __init__(self, status_code, json_body=None, text=""):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text or (str(json_body) if json_body else "")

    def json(self):
        return self._json_body


def _configure(monkeypatch):
    monkeypatch.setattr(config.settings, "bodhi_llm_gateway_token", "test-token")
    monkeypatch.setattr(config.settings, "bodhi_llm_gateway_url", "https://fake-gateway.example")
    monkeypatch.setattr(config.settings, "bodhi_llm_model", "test-model")


def test_403_raises_auth_error_with_exactly_one_attempt(monkeypatch):
    _configure(monkeypatch)
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append(1)
        return _FakeResponse(403, text="explicit deny in an identity-based policy")

    monkeypatch.setattr(requests, "post", fake_post)

    try:
        call_llm("hello")
        assert False, "expected BodhiGatewayAuthError"
    except BodhiGatewayAuthError as e:
        assert "403" in str(e)
    assert len(calls) == 1  # never retried


def test_401_also_raises_auth_error(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(401, text="unauthorized"))
    try:
        call_llm("hello")
        assert False, "expected BodhiGatewayAuthError"
    except BodhiGatewayAuthError:
        pass


def test_malformed_2xx_body_raises_shape_error(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(200, json_body={"unexpected": "shape"}))
    try:
        call_llm("hello")
        assert False, "expected BodhiGatewayShapeError"
    except BodhiGatewayShapeError:
        pass


def test_well_formed_response_returns_text_and_usage(monkeypatch):
    _configure(monkeypatch)
    body = {
        "choices": [{"message": {"content": '{"ok": true}'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(200, json_body=body))

    text, usage = call_llm("hello")
    assert text == '{"ok": true}'
    assert usage["total_tokens"] == 15


def test_token_budget_disabled_by_default_logs_nothing(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(config.settings, "bodhi_llm_token_budget_warning", 0)
    body = {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 999999}}
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(200, json_body=body))

    call_llm("hello")
    assert observability._FAILURE_LOG.exists() is False


def test_token_budget_warning_logs_a_visible_event_but_never_blocks(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(config.settings, "bodhi_llm_token_budget_warning", 1000)
    body = {"choices": [{"message": {"content": "still returns fine"}}], "usage": {"total_tokens": 5000}}
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(200, json_body=body))

    text, usage = call_llm("hello")  # never raises, never blocks
    assert text == "still returns fine"
    assert usage["total_tokens"] == 5000

    logged = observability._FAILURE_LOG.read_text()
    assert "bodhi_token_budget_exceeded" in logged


def test_enforced_budget_refuses_an_oversized_prompt_before_sending(monkeypatch):
    """Real enforcement, not a warning: the call is refused and NOTHING
    is sent to the gateway — requests.post must never even be called."""
    _configure(monkeypatch)
    monkeypatch.setattr(config.settings, "bodhi_llm_token_budget_limit", 10)  # ~10 tokens
    monkeypatch.setattr(
        requests, "post",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("requests.post should never be called")),
    )

    huge_prompt = "x" * 1000  # ~250 estimated tokens, way over the limit of 10
    try:
        call_llm(huge_prompt)
        assert False, "expected BodhiGatewayBudgetExceededError"
    except BodhiGatewayBudgetExceededError as e:
        assert "10" in str(e)


def test_enforced_budget_allows_a_call_within_limit(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(config.settings, "bodhi_llm_token_budget_limit", 100000)
    body = {"choices": [{"message": {"content": "fine"}}], "usage": {}}
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(200, json_body=body))

    text, _ = call_llm("a short prompt")
    assert text == "fine"


def test_enforced_budget_disabled_by_default(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(config.settings, "bodhi_llm_token_budget_limit", 0)
    body = {"choices": [{"message": {"content": "fine"}}], "usage": {}}
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(200, json_body=body))

    text, _ = call_llm("x" * 100000)  # would exceed any real budget, but enforcement is off
    assert text == "fine"


def test_per_agent_model_override_is_used_in_the_real_request(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(config.settings, "bodhi_llm_model_overrides", {"Orchestrator": "fast-model-v1"})
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["model"] = json["model"]
        return _FakeResponse(200, json_body={"choices": [{"message": {"content": "ok"}}], "usage": {}})

    monkeypatch.setattr(requests, "post", fake_post)

    call_llm("hello", agent="Orchestrator")
    assert captured["model"] == "fast-model-v1"


def test_agent_without_an_override_uses_the_default_model(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(config.settings, "bodhi_llm_model_overrides", {"Orchestrator": "fast-model-v1"})
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["model"] = json["model"]
        return _FakeResponse(200, json_body={"choices": [{"message": {"content": "ok"}}], "usage": {}})

    monkeypatch.setattr(requests, "post", fake_post)

    call_llm("hello", agent="Decision Agent")
    assert captured["model"] == "test-model"  # the configured default, untouched


def test_parse_model_overrides_handles_real_and_malformed_entries():
    parsed = config.Settings._parse_model_overrides(
        "Orchestrator=fast-model-v1, Bull Case Agent=fast-model-v1,malformed,=novalue,noname="
    )
    assert parsed == {"Orchestrator": "fast-model-v1", "Bull Case Agent": "fast-model-v1"}


def test_parse_model_overrides_empty_string_gives_empty_dict():
    assert config.Settings._parse_model_overrides("") == {}


def test_retryable_5xx_is_retried_then_succeeds(monkeypatch):
    _configure(monkeypatch)
    responses = [
        _FakeResponse(503, text="temporarily unavailable"),
        _FakeResponse(200, json_body={"choices": [{"message": {"content": "ok"}}], "usage": {}}),
    ]
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return responses[len(calls) - 1]

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(config.time, "sleep", lambda *_: None)  # skip real backoff delay

    text, _ = call_llm("hello")
    assert text == "ok"
    assert len(calls) == 2
