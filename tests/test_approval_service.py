"""
test_approval_service.py — the approval/publish path exposed as a real,
independently-deployable HTTP microservice (service/approval_service.py).

Real behaviour under test: every endpoint is exercised over an actual
HTTP request/response cycle (FastAPI's TestClient, real ASGI routing —
not calling the Python functions directly), against the same
alerts.approval_gate state machine tests/test_approval_gate.py already
covers, isolated to a temp directory the same way. Telegram/TradingView
are mocked only at the true I/O boundary (requests.post / subprocess.run)
— same pattern as tests/test_approve_run.py — so approving over HTTP is
proven to trigger the exact same real multi-channel publish, not a
separate, un-synced implementation.
"""

import shutil
import tempfile
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient

from alerts import approval_gate, tradingview_watchlist
from config import settings
from service.approval_service import app

client = TestClient(app)


class _FakeTelegramResponse:
    status_code = 200
    text = "ok"


class _FakeTvProc:
    returncode = 0
    stdout = '{"added": 1, "failed": 0, "results": []}'
    stderr = ""


@pytest.fixture()
def isolated_dirs(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(approval_gate, "PENDING_DIR", tmp / "pending_approval")
    monkeypatch.setattr(approval_gate, "APPROVED_DIR", tmp / "approved")
    monkeypatch.setattr(approval_gate, "REJECTED_DIR", tmp / "rejected")
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


def _save_pending(**overrides) -> str:
    record = {
        "date": "2026-09-08", "alert_text": "*Shortlist*\n\n1. RELIANCE",
        "bodhi_ran": False, "tv_symbols": [],
        "results_by_tier": {"moderate": {"shortlist": [{"symbol": "RELIANCE"}]}},
    }
    record.update(overrides)
    path = approval_gate.save_pending(record)
    return path.stem  # the run_id this service's API uses


def test_health_check():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_list_pending_returns_a_real_saved_run(isolated_dirs):
    run_id = _save_pending()
    resp = client.get("/pending")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["run_id"] == run_id
    assert body[0]["picks_by_tier"] == {"moderate": 1}


def test_get_one_pending_run_returns_the_full_record(isolated_dirs):
    run_id = _save_pending()
    resp = client.get(f"/pending/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["alert_text"] == "*Shortlist*\n\n1. RELIANCE"


def test_get_unknown_run_id_is_a_real_404(isolated_dirs):
    resp = client.get("/pending/run_2099-01-01_000000")
    assert resp.status_code == 404


def test_malformed_run_id_is_rejected_before_touching_the_filesystem(isolated_dirs):
    """Security boundary: a run_id is used to build a filesystem path
    (PENDING_DIR / f"{run_id}.json") — anything not matching the exact
    real filename shape (validated by regex BEFORE path-building) must
    be refused, not silently path-joined. Proves a traversal attempt
    can't escape output/pending_approval/."""
    for bad_run_id in ("..%2F..%2Fetc%2Fpasswd", "run_2026-01-01_000000_extra", "not-a-run-id"):
        resp = client.get(f"/pending/{bad_run_id}")
        assert resp.status_code in (400, 404)  # never a raw filesystem error, never a 200


def test_reject_moves_the_run_and_records_the_reason(isolated_dirs):
    run_id = _save_pending()
    resp = client.post(f"/pending/{run_id}/reject", json={"reason": "looked wrong"})
    assert resp.status_code == 200
    assert resp.json()["reason"] == "looked wrong"

    assert client.get("/pending").json() == []
    rejected = client.get("/rejected").json()
    assert len(rejected) == 1
    assert rejected[0]["run_id"] == run_id


def test_approve_publishes_to_telegram_and_tradingview_then_moves_to_approved(isolated_dirs, monkeypatch):
    """The real point of this service: approving OVER HTTP triggers the
    exact same coordinated multi-channel publish as the CLI/dashboard
    (approve_run.py::_publish() — see tests/test_approve_run.py), not a
    parallel, could-drift-out-of-sync reimplementation."""
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
        return _FakeTvProc()

    monkeypatch.setattr(tradingview_watchlist.subprocess, "run", fake_run)

    run_id = _save_pending(tv_symbols=["RELIANCE"])
    resp = client.post(f"/pending/{run_id}/approve")

    assert resp.status_code == 200
    assert resp.json()["status"] == "approved_and_published"
    assert len(telegram_calls) == 1
    assert len(tv_calls) == 1

    assert client.get("/pending").json() == []
    approved = client.get("/approved").json()
    assert len(approved) == 1
    assert approved[0]["run_id"] == run_id


def test_a_run_cannot_be_approved_twice_over_http(isolated_dirs, monkeypatch):
    monkeypatch.setattr(settings, "telegram_token", "")
    monkeypatch.setattr(settings, "telegram_chat_id", "")
    monkeypatch.setattr(settings, "tradingview_watchlist_enabled", False)

    run_id = _save_pending()
    first = client.post(f"/pending/{run_id}/approve")
    assert first.status_code == 200

    second = client.post(f"/pending/{run_id}/approve")
    assert second.status_code == 404  # already moved out of pending — can't double-publish
