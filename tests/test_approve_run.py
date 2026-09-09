"""
test_approve_run.py — the human-approval publish action, and its real
COORDINATED multi-channel delivery (Telegram + TradingView from ONE
call — Excel is written independently and unconditionally in run.py,
outside the approval gate).

Real behaviour under test: approve_run.py::_publish() is the single
function every approval path actually calls — the CLI's
`--latest --auto-approve` and the dashboard's "✅ Approve & publish"
button both go through it. A human takes ONE action; every configured
channel fires from it, not a series of separate manual re-send clicks
(alerts/trigger_telegram.py and alerts/trigger_tradingview.py are
after-the-fact RESEND tools for an already-approved run, not the primary
publish path this file tests).

Mocked only at the true I/O boundary each channel ultimately uses
(requests.post for Telegram, subprocess.run for the TradingView CLI) —
same mocking style as tests/test_config_gateway.py — so this proves the
real coordination, not just that each channel's own module works alone.
"""

import json

import requests

import approve_run
from alerts import tradingview_watchlist
from config import settings


class _FakeTelegramResponse:
    status_code = 200
    text = "ok"


class _FakeTvProc:
    def __init__(self, added, failed, results=None):
        self.returncode = 0
        self.stdout = json.dumps({"added": added, "failed": failed, "results": results or []})
        self.stderr = ""


def test_publish_sends_both_telegram_and_tradingview_from_one_call(monkeypatch):
    """The real coordination this criterion cares about: approving a run
    triggers every configured channel together, not one-at-a-time manual
    steps a human has to remember to click through separately."""
    monkeypatch.setattr(settings, "telegram_token", "fake-token")
    monkeypatch.setattr(settings, "telegram_chat_id", "fake-chat")
    monkeypatch.setattr(settings, "tradingview_watchlist_enabled", True)

    telegram_calls = []

    def fake_post(url, data, timeout):
        telegram_calls.append(data)
        return _FakeTelegramResponse()

    monkeypatch.setattr(requests, "post", fake_post)

    tv_calls = []

    def fake_run(cmd, capture_output, text, timeout):
        tv_calls.append(cmd)
        return _FakeTvProc(added=2, failed=0)

    monkeypatch.setattr(tradingview_watchlist.subprocess, "run", fake_run)

    record = {
        "alert_text": "*Today's Shortlist*\n\n1. RELIANCE",
        "bodhi_ran": False,
        "tv_symbols": ["RELIANCE", "TCS"],
    }
    approve_run._publish(record)

    assert len(telegram_calls) == 1  # exactly one Telegram send for the shortlist
    assert telegram_calls[0]["text"] == record["alert_text"]
    assert len(tv_calls) == 1  # exactly one TradingView push, same _publish() call
    assert "NSE:RELIANCE" in tv_calls[0]
    assert "NSE:TCS" in tv_calls[0]


def test_publish_also_sends_the_bodhi_report_as_a_second_telegram_message(monkeypatch):
    monkeypatch.setattr(settings, "telegram_token", "fake-token")
    monkeypatch.setattr(settings, "telegram_chat_id", "fake-chat")
    monkeypatch.setattr(settings, "tradingview_watchlist_enabled", False)

    telegram_calls = []

    def fake_post(url, data, timeout):
        telegram_calls.append(data)
        return _FakeTelegramResponse()

    monkeypatch.setattr(requests, "post", fake_post)

    record = {
        "alert_text": "*Today's Shortlist*\n\n1. RELIANCE",
        "bodhi_ran": True,
        "bodhi_report": "Risk Analyst: no material concerns.",
        "tv_symbols": [],
    }
    approve_run._publish(record)

    assert len(telegram_calls) == 2  # shortlist + Bodhi report, both real sends
    assert "Bodhi Risk Insight" in telegram_calls[1]["text"]
    assert "no material concerns" in telegram_calls[1]["text"]


def test_publish_skips_tradingview_when_no_candidates_survived_bodhis_filter(monkeypatch):
    """tv_symbols is already the POST-Bodhi-filter candidate list built
    in run.py — an empty list here means every candidate was flagged, not
    a bug, and _publish() must not call the watchlist push at all."""
    monkeypatch.setattr(settings, "telegram_token", "fake-token")
    monkeypatch.setattr(settings, "telegram_chat_id", "fake-chat")
    monkeypatch.setattr(settings, "tradingview_watchlist_enabled", True)

    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeTelegramResponse())
    tv_calls = []
    monkeypatch.setattr(tradingview_watchlist.subprocess, "run",
                         lambda *a, **k: tv_calls.append(1))

    approve_run._publish({"alert_text": "shortlist text", "bodhi_ran": False, "tv_symbols": []})
    assert tv_calls == []


def test_publish_warns_but_does_not_raise_when_telegram_is_not_configured(monkeypatch, capsys):
    """Real fallback: an unconfigured Telegram must never break the
    approval flow — it prints and continues, same philosophy as every
    other optional/best-effort integration in this codebase."""
    monkeypatch.setattr(settings, "telegram_token", "")
    monkeypatch.setattr(settings, "telegram_chat_id", "")

    approve_run._publish({"alert_text": "shortlist text", "bodhi_ran": False, "tv_symbols": []})
    assert "WARNING" in capsys.readouterr().out
