"""
test_dashboard_helpers.py — real run-progress and error-extraction logic.

Real behaviour under test: dashboard.py parses the actual STEP banner
lines run.py prints (not a fabricated progress bar), and extracts the
real exception message from a failed run's output rather than dumping
the whole log as the "error".
"""

import dashboard


SAMPLE_LOG_SUCCESS = """
================================================================
  STEP 1 — Real market context (Nifty 50)
================================================================
  Nifty 50: -0.5% today, trend=downtrend

================================================================
  STEP 5 — Decision Agent (orchestrated) — x 1 risk tier(s)
================================================================
  [moderate] 4 pick(s)
"""

SAMPLE_LOG_FAILURE = """
================================================================
  STEP 1 — Real market context (Nifty 50)
================================================================
  Nifty 50: -0.5% today

Traceback (most recent call last):
  File "run.py", line 90, in main
    raise RuntimeError("Bodhi LLM Gateway is not configured")
RuntimeError: Bodhi LLM Gateway is not configured
"""


def test_completed_steps_parses_real_step_banners():
    steps = dashboard._completed_steps(SAMPLE_LOG_SUCCESS)
    assert steps == [
        (1, "Real market context (Nifty 50)"),
        (5, "Decision Agent (orchestrated) — x 1 risk tier(s)"),
    ]


def test_completed_steps_empty_for_no_banners():
    assert dashboard._completed_steps("just some plain text, no banners") == []


def test_extract_real_error_finds_the_exception_line():
    error = dashboard._extract_real_error(SAMPLE_LOG_FAILURE)
    assert error == "RuntimeError: Bodhi LLM Gateway is not configured"


def test_extract_real_error_falls_back_to_last_line_when_no_exception():
    log = "line one\nline two\nfinal status line"
    assert dashboard._extract_real_error(log) == "final status line"


def test_extract_real_error_handles_empty_log():
    assert "no output" in dashboard._extract_real_error("").lower()


class _FakePlaceholder:
    """Stands in for st.sidebar.empty() — records nothing, just lets
    _run_with_live_progress's placeholder.container()/...empty() calls
    run without a real Streamlit script context."""
    def container(self):
        import contextlib
        return contextlib.nullcontext()

    def empty(self):
        pass


def test_run_with_live_progress_streams_real_step_banners_as_they_print():
    """Real behaviour under test: progress is read from the CHILD
    PROCESS's actual stdout as it's produced (a real subprocess, not a
    mock) — not assembled only after the process exits."""
    returncode, full_log, steps = dashboard._run_with_live_progress(
        ["python3", "-c", "print('STEP 1 — hi'); print('STEP 2 — bye')"],
        ".", _FakePlaceholder(), "testing",
    )
    assert returncode == 0
    assert steps == [(1, "hi"), (2, "bye")]
    assert "STEP 1 — hi" in full_log


def test_run_with_live_progress_reports_a_real_nonzero_exit_code():
    returncode, full_log, steps = dashboard._run_with_live_progress(
        ["python3", "-c", "import sys; print('STEP 1 — start'); sys.exit(1)"],
        ".", _FakePlaceholder(), "testing",
    )
    assert returncode == 1
    assert steps == [(1, "start")]
