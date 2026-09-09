"""
test_approval_gate.py — the pause-and-resume publish gate.

Real behaviour under test: a saved run starts pending; approve() moves it
to approved/ and it can never be actioned twice; reject() moves it to
rejected/ with a reason; and a run that was never approved must not be
visible to anything that only looks at the approved/ directory.

Runs entirely against a temp directory (monkeypatched onto the module),
never the real output/ tree.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from alerts import approval_gate


@pytest.fixture()
def isolated_dirs(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(approval_gate, "PENDING_DIR", tmp / "pending_approval")
    monkeypatch.setattr(approval_gate, "APPROVED_DIR", tmp / "approved")
    monkeypatch.setattr(approval_gate, "REJECTED_DIR", tmp / "rejected")
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


def test_save_pending_creates_a_file_and_it_is_listed(isolated_dirs):
    path = approval_gate.save_pending({"date": "2026-09-08", "alert_text": "x"})
    assert path.exists()
    assert approval_gate.list_pending() == [path]
    assert approval_gate.list_approved() == []


def test_approve_moves_out_of_pending_into_approved(isolated_dirs):
    path = approval_gate.save_pending({"date": "2026-09-08", "alert_text": "x"})
    record = approval_gate.approve(path)

    assert not path.exists()
    assert approval_gate.list_pending() == []
    assert len(approval_gate.list_approved()) == 1
    assert "approved_at" in record


def test_reject_moves_out_of_pending_with_a_reason(isolated_dirs):
    path = approval_gate.save_pending({"date": "2026-09-08", "alert_text": "x"})
    record = approval_gate.reject(path, reason="looked wrong")

    assert not path.exists()
    assert approval_gate.list_pending() == []
    assert approval_gate.list_approved() == []
    assert record["rejection_reason"] == "looked wrong"
    assert len(approval_gate.list_rejected()) == 1


def test_a_run_can_only_be_approved_once(isolated_dirs):
    path = approval_gate.save_pending({"date": "2026-09-08", "alert_text": "x"})
    approval_gate.approve(path)

    # The file no longer exists at the original pending path — a second
    # approve() attempt against the same path must fail loudly, not
    # silently re-approve or duplicate the record.
    with pytest.raises(FileNotFoundError):
        approval_gate.approve(path)


def test_unapproved_run_is_invisible_to_list_approved(isolated_dirs):
    approval_gate.save_pending({"date": "2026-09-08", "alert_text": "never approved"})
    assert approval_gate.list_approved() == []


def test_bodhi_research_degraded_flag_survives_the_full_pending_to_approved_transition(isolated_dirs):
    """A real end-to-end field, not just a top-level dict key: a run
    where Bodhi's escalation branch reported its research tool degraded
    mid-run must carry that flag through save_pending -> load -> approve
    unchanged, since approve_run.py._summarize() and the dashboard's
    Review & Approve section both read it to warn a human before they
    publish."""
    path = approval_gate.save_pending({
        "date": "2026-09-08", "alert_text": "x", "bodhi_ran": True,
        "bodhi_research_degraded": True, "tv_symbols": ["ICICIBANK"],
        "results_by_tier": {"moderate": {"shortlist": [{"symbol": "ICICIBANK"}]}},
    })
    pending_record = approval_gate.load(path)
    assert pending_record["bodhi_research_degraded"] is True

    approved_record = approval_gate.approve(path)
    assert approved_record["bodhi_research_degraded"] is True

    import approve_run
    summary = approve_run._summarize(approved_record)
    assert "research tool degraded mid-run" in summary


def test_clean_bodhi_run_never_shows_a_degraded_warning(isolated_dirs):
    path = approval_gate.save_pending({
        "date": "2026-09-08", "alert_text": "x", "bodhi_ran": True,
        "bodhi_research_degraded": False, "results_by_tier": {},
    })
    record = approval_gate.load(path)

    import approve_run
    summary = approve_run._summarize(record)
    assert "degraded" not in summary
