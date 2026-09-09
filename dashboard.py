"""
dashboard.py  —  the cockpit
============================
A Streamlit dashboard for AI Equity Analyst. Run it with:

    uv run streamlit run dashboard.py

It lets you:
  - Build your investor profile — upload a PDF/XLSX document and/or fill in
    a dropdown form (your form answers always win; the document just fills
    in anything the form doesn't cover)
  - Browse today's (or any past) shortlist: opportunity score, confidence,
    bull/bear case, portfolio fit, target price, suggested quantity
  - See real current price, performance and a price chart per analysed
    stock, plus the real news events behind each pick
  - Edit UNIVERSE / TOP_N in .env and trigger a fresh run.py analysis
    without leaving the browser

Bodhi/Telegram credentials are intentionally NOT editable here — they're
secrets, not day-to-day knobs, and stay in .env only.
"""

import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from data import market_data  # noqa: E402
from profile.profile_loader import load_profile  # noqa: E402
from alerts import approval_gate  # noqa: E402
import observability  # noqa: E402

OUTPUT_DIR = ROOT / "output"
ENV_PATH = ROOT / ".env"
PROFILE_DIR = ROOT / "profile" / "_dashboard"
PROFILE_JSON_PATH = PROFILE_DIR / "profile.json"

SECTOR_OPTIONS = [
    "IT", "Banking", "Financial Services", "Insurance", "Pharma", "FMCG",
    "Auto", "Capital Goods", "Energy", "Metals & Mining", "Realty",
    "Telecom", "Infrastructure", "Consumer Durables", "Chemicals",
    "Media & Entertainment", "Tobacco", "Alcohol",
]

# Trading-terminal dark palette — up/down are the two colors that actually
# carry meaning (gain/loss), everything else is neutral chrome.
COLOR_BG = "#0d1117"
COLOR_PANEL = "#161b22"
COLOR_PANEL_ALT = "#1c2129"
COLOR_BORDER = "#2a2f3a"
COLOR_TEXT = "#e6edf3"
COLOR_BLUE = "#2f81f7"
COLOR_ORANGE = "#f0883e"
COLOR_YELLOW = "#e3b341"
COLOR_GOOD = "#26d07c"       # gains / accumulate / high conviction
COLOR_WARNING = "#e3b341"    # hold / neutral
COLOR_CRITICAL = "#f85149"   # losses / avoid / poor fit
COLOR_GRID = "#21262d"
COLOR_MUTED = "#8b949e"
COLOR_SURFACE = COLOR_PANEL

SCORE_COLORS = {"Fundamental": COLOR_BLUE, "Technical": COLOR_ORANGE, "Risk": COLOR_YELLOW}


def inject_terminal_css():
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {{
        font-family: 'Inter', -apple-system, sans-serif;
    }}

    /* Monospace everywhere a real number shows up — ticker digits should
       not visually jitter as they change width. */
    div[data-testid="stMetricValue"],
    div[data-testid="stMetricDelta"],
    code, .terminal-mono {{
        font-family: 'JetBrains Mono', 'SF Mono', monospace !important;
    }}

    div[data-testid="stMetricValue"] {{
        font-size: 1.35rem;
        font-weight: 700;
    }}
    div[data-testid="stMetricLabel"] {{
        color: {COLOR_MUTED};
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
    }}

    /* Sidebar section headers → terminal-style uppercase labels */
    section[data-testid="stSidebar"] h2 {{
        font-size: 0.78rem !important;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: {COLOR_MUTED} !important;
        border-bottom: 1px solid {COLOR_BORDER};
        padding-bottom: 6px;
        margin-top: 4px !important;
    }}
    section[data-testid="stSidebar"] {{
        background: {COLOR_PANEL};
        border-right: 1px solid {COLOR_BORDER};
    }}

    /* Cards (st.container(border=True)) → trading-ticket look */
    div[data-testid="stVerticalBlockBorderWrapper"] {{
        background: {COLOR_PANEL};
        border: 1px solid {COLOR_BORDER} !important;
        border-radius: 6px;
    }}

    div[data-testid="stExpander"] {{
        background: {COLOR_PANEL};
        border: 1px solid {COLOR_BORDER} !important;
        border-radius: 6px;
    }}

    /* Top ticker strip */
    .ticker-strip {{
        display: flex;
        gap: 0;
        overflow-x: auto;
        border: 1px solid {COLOR_BORDER};
        border-radius: 6px;
        background: {COLOR_PANEL};
        margin-bottom: 1rem;
    }}
    .ticker-chip {{
        flex: 0 0 auto;
        padding: 10px 18px;
        border-right: 1px solid {COLOR_BORDER};
        font-family: 'JetBrains Mono', monospace;
        white-space: nowrap;
    }}
    .ticker-chip:last-child {{ border-right: none; }}
    .ticker-symbol {{
        font-weight: 700;
        font-size: 0.85rem;
        letter-spacing: 0.02em;
        color: {COLOR_TEXT};
        margin-right: 8px;
    }}
    .ticker-price {{ color: {COLOR_TEXT}; margin-right: 8px; }}

    /* Market status chip in the top bar */
    .market-status {{
        display: inline-flex;
        align-items: center;
        gap: 6px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.78rem;
        padding: 4px 12px;
        border-radius: 20px;
        border: 1px solid {COLOR_BORDER};
        color: {COLOR_MUTED};
    }}
    .dot {{
        width: 8px; height: 8px; border-radius: 50%;
        display: inline-block;
    }}

    /* Card header symbol — big ticker-style type */
    .ticket-symbol {{
        font-family: 'JetBrains Mono', monospace;
        font-size: 1.5rem;
        font-weight: 700;
        letter-spacing: 0.01em;
        color: {COLOR_TEXT};
    }}

    hr {{ border-color: {COLOR_BORDER} !important; }}

    /* Sequential pipeline steps in the sidebar */
    .step-row {{
        display: flex;
        align-items: center;
        gap: 10px;
        margin: 6px 0 2px 0;
    }}
    .step-badge {{
        flex: 0 0 auto;
        width: 22px; height: 22px;
        border-radius: 50%;
        background: {COLOR_BLUE}22;
        border: 1px solid {COLOR_BLUE}80;
        color: {COLOR_BLUE};
        display: flex; align-items: center; justify-content: center;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.72rem;
        font-weight: 700;
    }}
    .step-title {{
        font-weight: 600;
        font-size: 0.9rem;
        color: {COLOR_TEXT};
    }}
    .step-connector {{
        width: 1px;
        height: 12px;
        background: {COLOR_BORDER};
        margin-left: 11px;
    }}
    section[data-testid="stSidebar"] .stButton button {{
        border: 1px solid {COLOR_BORDER};
        font-weight: 600;
    }}
    </style>
    """, unsafe_allow_html=True)


def _step_header(n: int, title: str):
    st.sidebar.markdown(
        f"<div class='step-row'><span class='step-badge'>{n}</span>"
        f"<span class='step-title'>{title}</span></div>",
        unsafe_allow_html=True,
    )


# ----------------------------------------------------------------------
# .env read/write
# ----------------------------------------------------------------------
def read_env() -> dict:
    values = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            k, v = stripped.split("=", 1)
            values[k.strip()] = v.strip()
    return values


def write_env(updates: dict) -> None:
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    seen = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        new_lines.append(line)
    for key, val in updates.items():
        if key not in seen:
            new_lines.append(f"{key}={val}")
    ENV_PATH.write_text("\n".join(new_lines) + "\n")


# ----------------------------------------------------------------------
# Output run files
# ----------------------------------------------------------------------
def list_runs() -> list:
    return sorted(OUTPUT_DIR.glob("analysis_*.json"), reverse=True)


def list_intraday_runs() -> list:
    return sorted(OUTPUT_DIR.glob("intraday_scan_*.json"), reverse=True)


def load_run(path: Path) -> dict:
    return json.loads(path.read_text())


# ----------------------------------------------------------------------
# Real run-progress + error extraction from a subprocess's actual output —
# not a generic "see log above", an operator gets the real step-by-step
# progress and the real failure reason surfaced directly.
# ----------------------------------------------------------------------
_STEP_RE = re.compile(r"^\s*STEP\s+(\d+)\s+—\s+(.+?)\s*$", re.MULTILINE)


def _completed_steps(log_text: str) -> list:
    """Every "STEP N — Title" banner actually printed, in order — real
    progress evidence from the run's own output, not a guessed progress bar."""
    return [(int(n), title) for n, title in _STEP_RE.findall(log_text or "")]


def _extract_real_error(log_text: str) -> str:
    """The single most useful line from a failed run: the last real
    exception message, if a Python traceback is present, else the last
    non-empty line of output. Never the whole log dump."""
    text = log_text or ""
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return "(no output captured)"
    for line in reversed(lines):
        stripped = line.strip()
        if any(stripped.startswith(exc) for exc in (
            "RuntimeError", "ValueError", "TypeError", "KeyError",
            "FileNotFoundError", "ConnectionError", "TimeoutError",
        )) or (": " in stripped and stripped.split(":")[0].endswith("Error")):
            return stripped
    return lines[-1]


def _stream_subprocess_output(cmd: list, cwd, result: dict):
    """A real generator for st.write_stream(): yields each real line of
    `cmd`'s stdout/stderr AS IT ARRIVES — genuine incremental streaming
    WITH BACK-PRESSURE. Streamlit's write_stream pulls from this
    generator only as fast as it renders; nothing here buffers the
    whole log in memory before the first line is shown, unlike the
    earlier version of this function (a poll-and-full-redraw loop that
    re-rendered a growing string on every subprocess line). A generator
    can't also `return` a value the normal way, so `result` (a plain
    dict the caller owns) is where the real exit code, full log, and
    parsed STEP banners land once the subprocess actually exits —
    read it only AFTER the stream is fully consumed."""
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    log_lines = []
    for line in proc.stdout:
        log_lines.append(line)
        yield line
    proc.wait()
    result["returncode"] = proc.returncode
    result["full_log"] = "".join(log_lines)
    result["steps"] = _completed_steps(result["full_log"])


def _run_with_live_progress(cmd: list, cwd, placeholder, running_caption: str) -> tuple:
    """Runs `cmd` with REAL Streamlit streaming (st.write_stream) — the
    process's real stdout/stderr renders incrementally as it's actually
    produced, with genuine back-pressure, not a spinner that gives no
    idea which of ~10 real steps is currently running and not a
    poll-driven full-redraw. Returns (returncode, full_log, steps) — the
    same shape callers previously got from subprocess.run() +
    _completed_steps(), so nothing downstream needs to change."""
    result: dict = {}
    with placeholder.container():
        st.caption(f"⏳ {running_caption}")
        st.write_stream(_stream_subprocess_output(cmd, cwd, result))
    placeholder.empty()
    return result["returncode"], result["full_log"], result["steps"]


# ----------------------------------------------------------------------
# Real market data (cached briefly so reruns don't hammer Yahoo/Google)
# ----------------------------------------------------------------------
@st.cache_data(ttl=300)
def cached_stats(symbol: str) -> dict:
    return market_data.get_market_stats(symbol)


@st.cache_data(ttl=300)
def cached_history(symbol: str):
    return market_data.get_price_history(symbol, period="6mo")


# ----------------------------------------------------------------------
# Charts
# ----------------------------------------------------------------------
def price_chart(symbol: str, hist) -> go.Figure:
    """A real candlestick + volume chart — the actual OHLCV history
    already fetched, not a re-derived line — laid out the way a trading
    terminal shows a stock: candles on top, volume underneath, shared
    x-axis, no range slider clutter."""
    from plotly.subplots import make_subplots

    up = hist["Close"] >= hist["Open"]
    vol_colors = [COLOR_GOOD if u else COLOR_CRITICAL for u in up]

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.72, 0.28], vertical_spacing=0.02,
    )
    fig.add_trace(go.Candlestick(
        x=hist.index, open=hist["Open"], high=hist["High"], low=hist["Low"], close=hist["Close"],
        increasing_line_color=COLOR_GOOD, increasing_fillcolor=COLOR_GOOD,
        decreasing_line_color=COLOR_CRITICAL, decreasing_fillcolor=COLOR_CRITICAL,
        name=symbol, showlegend=False,
    ), row=1, col=1)
    fig.add_trace(go.Bar(
        x=hist.index, y=hist["Volume"], marker_color=vol_colors, marker_line_width=0,
        showlegend=False, hovertemplate="Vol: %{y:,.0f}<extra></extra>",
    ), row=2, col=1)

    fig.update_layout(
        template="plotly_dark",
        height=380,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis_rangeslider_visible=False,
        plot_bgcolor=COLOR_SURFACE,
        paper_bgcolor=COLOR_SURFACE,
        font=dict(family="JetBrains Mono, monospace", color=COLOR_MUTED, size=11),
        showlegend=False,
        hovermode="x unified",
    )
    fig.update_xaxes(showgrid=False, color=COLOR_MUTED)
    fig.update_yaxes(gridcolor=COLOR_GRID, color=COLOR_MUTED, row=1, col=1, title_text="₹")
    fig.update_yaxes(gridcolor=COLOR_GRID, color=COLOR_MUTED, row=2, col=1, title_text="Vol")
    return fig


def score_chart(analysis: dict) -> go.Figure:
    labels = ["Fundamental", "Technical", "Risk"]
    values = [
        analysis["fundamental"]["score"],
        analysis["technical"]["score"],
        analysis["risk"]["risk_score"],
    ]
    colors = [SCORE_COLORS[l] for l in labels]
    fig = go.Figure(go.Bar(
        x=labels, y=values,
        marker=dict(color=colors),
        hovertemplate="%{x}: %{y}/10<extra></extra>",
    ))
    fig.update_layout(
        template="plotly_dark",
        height=220,
        margin=dict(l=10, r=10, t=10, b=10),
        yaxis=dict(range=[0, 10], gridcolor=COLOR_GRID, color=COLOR_MUTED),
        xaxis=dict(color=COLOR_MUTED),
        plot_bgcolor=COLOR_SURFACE,
        paper_bgcolor=COLOR_SURFACE,
        font=dict(family="JetBrains Mono, monospace", color=COLOR_MUTED, size=11),
        showlegend=False,
    )
    return fig


def _badge(text: str, color: str) -> str:
    return (
        f"<span style='background:{color}1f;color:{color};padding:3px 10px;"
        f"border:1px solid {color}55;border-radius:3px;font-weight:600;"
        f"font-size:0.72rem;letter-spacing:0.04em;font-family:\"JetBrains Mono\",monospace'>"
        f"{text.upper()}</span>"
    )


def _pct_span(pct) -> str:
    """A colored, monospace +/- percentage — the ticker-tape convention."""
    if pct is None:
        return "<span style='color:#8b949e'>—</span>"
    color = COLOR_GOOD if pct >= 0 else COLOR_CRITICAL
    arrow = "▲" if pct >= 0 else "▼"
    return (
        f"<span style='color:{color};font-family:\"JetBrains Mono\",monospace;"
        f"font-weight:600'>{arrow} {pct:+.2f}%</span>"
    )


def market_status_html() -> str:
    """Real IST clock check (weekday + 09:15–15:30) — cosmetic status chip,
    not a data source; NSE holidays aren't accounted for."""
    now_utc = dt.datetime.now(dt.timezone.utc)
    ist = now_utc + dt.timedelta(hours=5, minutes=30)
    is_weekday = ist.weekday() < 5
    minutes = ist.hour * 60 + ist.minute
    open_now = is_weekday and (9 * 60 + 15) <= minutes <= (15 * 60 + 30)
    color = COLOR_GOOD if open_now else COLOR_MUTED
    label = "NSE OPEN" if open_now else "NSE CLOSED"
    return (
        f"<span class='market-status'><span class='dot' style='background:{color}'></span>"
        f"{label} · {ist.strftime('%H:%M')} IST</span>"
    )


CONVICTION_ICON = {"HIGH": "🔥", "LOW": "👀"}


def conviction_badge(conviction: str) -> str:
    color = {"HIGH": COLOR_GOOD, "LOW": COLOR_MUTED}.get(conviction, COLOR_MUTED)
    icon = CONVICTION_ICON.get(conviction, "")
    return _badge(f"{icon} {conviction} conviction", color)


def action_badge(action: str) -> str:
    color = {"accumulate": COLOR_GOOD, "hold": COLOR_WARNING, "avoid": COLOR_CRITICAL}.get(
        (action or "").lower(), COLOR_MUTED
    )
    return _badge(action or "n/a", color)


def portfolio_fit_badge(fit: str) -> str:
    color = {"good": COLOR_GOOD, "neutral": COLOR_WARNING, "poor": COLOR_CRITICAL}.get(
        (fit or "").lower(), COLOR_MUTED
    )
    return _badge(f"portfolio fit: {fit or 'n/a'}", color)


# ----------------------------------------------------------------------
# Sidebar — investor profile (upload and/or dropdown form)
# ----------------------------------------------------------------------
# NOTE: these widgets deliberately sit OUTSIDE an st.form. A form only
# syncs its widgets' values to session_state (and to disk) when its submit
# button is clicked — so a user who changes a field and clicks "Run"
# without also clicking "Save" would silently run against the PREVIOUS
# saved profile, with no warning. Plain widgets update live, so "Run" can
# always save exactly what's currently shown before triggering the run.
def _read_saved_profile() -> dict:
    if PROFILE_JSON_PATH.exists():
        try:
            return json.loads(PROFILE_JSON_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write_profile(profile: dict) -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_JSON_PATH.write_text(json.dumps(profile, indent=2))
    write_env({"PROFILE_PATH": str(PROFILE_JSON_PATH)})


def _profile_from_widget_state() -> dict:
    """The profile dict for whatever is CURRENTLY selected in the sidebar
    widgets right now — always live, never stale."""
    capital = st.session_state.get("profile_capital_numeric", 0)
    return {
        "risk_appetites": st.session_state.get("profile_risk_appetites") or ["moderate"],
        "capital_available": f"₹{capital:,.0f}",
        "capital_numeric": capital or None,
        "max_position_pct": st.session_state.get("profile_max_position_pct"),
        "investment_horizon": st.session_state.get("profile_investment_horizon"),
        "sector_preferences": st.session_state.get("profile_sector_preferences", []),
        "sector_exclusions": st.session_state.get("profile_sector_exclusions", []),
        "constraints": st.session_state.get("profile_constraints", ""),
    }


def render_profile_builder():
    st.sidebar.header("Investor Profile")
    uploaded = st.sidebar.file_uploader(
        "Upload a profile document (optional)",
        type=["pdf", "xlsx", "docx", "txt", "md", "html"],
        help="Optional. The dropdown fields below are always used too — "
             "your answers there take priority over anything in the document.",
    )

    doc_profile = {}
    if uploaded is not None:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = PROFILE_DIR / f"uploaded{Path(uploaded.name).suffix}"
        tmp_path.write_bytes(uploaded.getvalue())
        try:
            with st.spinner("Reading your profile document..."):
                doc_profile = load_profile(str(tmp_path))
            st.sidebar.success(f"Parsed {uploaded.name}")
        except Exception as e:
            st.sidebar.warning(f"Could not parse {uploaded.name}: {e}")

    # Defaults: a freshly uploaded doc wins (explicit new input this
    # session); otherwise fall back to whatever's actually SAVED on disk —
    # not a hardcoded guess — so the form reflects reality even after a
    # page refresh, instead of silently resetting to "moderate".
    base = doc_profile if doc_profile else _read_saved_profile()
    base_risk = base.get("risk_appetites") or ([base["risk_appetite"]] if base.get("risk_appetite") else [])

    risk_options = ["conservative", "moderate", "aggressive"]
    horizon_options = ["short", "medium", "long"]

    st.sidebar.multiselect(
        "Risk appetite (pick one or more)", risk_options,
        default=[r for r in base_risk if r in risk_options] or ["moderate"],
        key="profile_risk_appetites",
        help="Conservative = safety first, smaller bets, avoids volatile names. "
             "Moderate = a balance of growth and safety. Aggressive = chases bigger "
             "moves, accepts more ups and downs. Pick more than one (e.g. Conservative "
             "+ Aggressive) to see a SEPARATE shortlist for each — useful for comparing "
             "how cautious vs. bold the same market looks today.",
    )
    st.sidebar.number_input(
        "Capital available (₹)", min_value=0, step=10000,
        value=int(base.get("capital_numeric") or 0),
        key="profile_capital_numeric",
    )
    st.sidebar.selectbox(
        "Investment horizon", horizon_options,
        index=horizon_options.index(base.get("investment_horizon")) if base.get("investment_horizon") in horizon_options else 2,
        key="profile_investment_horizon",
    )
    st.sidebar.multiselect(
        "Preferred sectors (pick as many as you want)", SECTOR_OPTIONS,
        default=[s for s in base.get("sector_preferences", []) if s in SECTOR_OPTIONS],
        key="profile_sector_preferences",
    )
    st.sidebar.multiselect(
        "Excluded sectors (pick as many as you want)", SECTOR_OPTIONS,
        default=[s for s in base.get("sector_exclusions", []) if s in SECTOR_OPTIONS],
        key="profile_sector_exclusions",
    )
    st.sidebar.number_input(
        "Max position size per stock (%)", min_value=1, max_value=100,
        value=int(base.get("max_position_pct") or 15),
        key="profile_max_position_pct",
        help="The MOST of your capital you'll ever let ONE stock take, no matter "
             "how good the idea looks. Example: with ₹30,000 capital and a 15% "
             "limit, even your favourite pick is capped at ₹4,500 — so if that one "
             "stock goes badly wrong, it can't take down your whole portfolio. "
             "Lower % = safer, more spread out. Higher % = more concentrated bets.",
    )
    st.sidebar.text_area(
        "Other constraints / notes (optional)", value=base.get("constraints", ""),
        key="profile_constraints",
    )

    if st.sidebar.button("Save profile", use_container_width=True):
        _write_profile(_profile_from_widget_state())
        st.sidebar.success("Profile saved — used by the next run.")


# ----------------------------------------------------------------------
# Sidebar — universe/top_n + run trigger
# ----------------------------------------------------------------------
def render_run_controls():
    st.sidebar.header("Scan Settings")
    env = read_env()

    with st.sidebar.form("settings_form"):
        universe = st.text_input(
            "UNIVERSE", value=env.get("UNIVERSE", "NIFTY50"),
            help="NIFTY50, NIFTY500, or a comma-separated symbol list",
        )
        top_n = st.number_input("TOP_N", min_value=1, max_value=20, value=int(env.get("TOP_N", "3")))
        if st.form_submit_button("Save settings"):
            write_env({"UNIVERSE": universe, "TOP_N": str(top_n)})
            st.sidebar.success("Saved to .env")

    st.sidebar.divider()
    st.sidebar.header("Run Pipeline")
    st.sidebar.caption(
        "① runs the analysis, but stops there — nothing is sent anywhere. "
        "② is a real pause: you review and explicitly approve before "
        "anything can be published. Steps ④–⑥ only ever act on an "
        "already-approved run."
    )

    _step_header(1, "Run full swing analysis")
    if env.get("TRADINGVIEW_QUOTE_ENABLED", "").strip().lower() in ("1", "true", "yes"):
        st.sidebar.caption(
            "🛠 TRADINGVIEW_QUOTE_ENABLED is on — the Decision Agent will call "
            "a real live TradingView quote per stock while reasoning (~20s "
            "each, cached per symbol). This run will take noticeably longer."
        )
    if st.sidebar.button("▶ Run swing analysis now", use_container_width=True, key="btn_swing"):
        # Always save whatever the profile widgets currently show, right
        # before running — so this run can NEVER silently use a stale
        # profile just because "Save profile" wasn't clicked separately.
        _write_profile(_profile_from_widget_state())
        progress = st.sidebar.empty()
        returncode, full_log, steps = _run_with_live_progress(
            ["uv", "run", "python", "run.py"], ROOT, progress,
            "running the full agent pipeline — each step appears above as it completes...",
        )
        if returncode == 0:
            with st.sidebar.expander(f"✅ {len(steps)} step(s) completed", expanded=False):
                for n, title in steps:
                    st.write(f"✅ Step {n} — {title}")
                st.code(full_log, language="text")
            st.sidebar.success("Run complete — pending your approval below before anything is sent.")
            st.cache_data.clear()
            st.rerun()
        else:
            real_error = _extract_real_error(full_log)
            st.sidebar.error(f"Run failed at step {len(steps) + 1}: {real_error}")
            with st.sidebar.expander(f"Run log ({len(steps)} step(s) completed before failure)", expanded=True):
                for n, title in steps:
                    st.write(f"✅ Step {n} — {title}")
                st.write(f"❌ **{real_error}**")
                st.code(full_log, language="text")

    st.sidebar.markdown("<div class='step-connector'></div>", unsafe_allow_html=True)
    _step_header(2, "Review & Approve")
    st.sidebar.caption(
        "run.py never sends Telegram alerts or pushes TradingView itself — every "
        "finished shortlist stops here as a PENDING run. Nothing goes out until "
        "you explicitly approve THIS run."
    )
    pending = approval_gate.list_pending()
    if not pending:
        st.sidebar.info("No pending runs.")
    else:
        latest = pending[0]
        record = approval_gate.load(latest)
        with st.sidebar.expander(f"⏸ Pending: {latest.name}", expanded=True):
            st.caption(f"Created {record.get('created_at', '?')}")
            for tier, result in (record.get("results_by_tier") or {}).items():
                picks = result.get("shortlist", [])
                symbols = ", ".join(p["symbol"] for p in picks)
                st.write(f"**[{tier}]** {len(picks)} pick(s): {symbols or '(none)'}")
            tv_symbols = record.get("tv_symbols") or []
            st.write(f"TradingView candidates: {', '.join(tv_symbols) or '(none)'}")
            if record.get("bodhi_ran"):
                branch = "🔺 escalated (deeper review ran)" if record.get("bodhi_escalation_branch_fired") \
                    else "✅ clean (no escalation needed)"
                st.write(f"🧠 Bodhi risk verification ran — branch outcome: {branch}. "
                         f"Full report will be sent to Telegram too.")
                if record.get("bodhi_research_degraded"):
                    st.warning(
                        "⚠️ This run's Bodhi report indicates its research tool "
                        "degraded mid-run — verdicts may be reasoning-only, not "
                        "freshly researched. Review the full report before approving."
                    )

            col_approve, col_reject = st.columns(2)
            if col_approve.button("✅ Approve & publish", use_container_width=True, key="btn_approve"):
                with st.spinner("Publishing to Telegram/TradingView..."):
                    proc = subprocess.run(
                        ["uv", "run", "python", "approve_run.py", "--latest", "--auto-approve"],
                        cwd=ROOT, capture_output=True, text=True,
                    )
                approval_log = (proc.stdout or "") + (proc.stderr or "")
                with st.sidebar.expander("Approval log", expanded=proc.returncode != 0):
                    st.code(approval_log, language="text")
                if proc.returncode == 0:
                    st.sidebar.success("Approved and published.")
                    st.rerun()
                else:
                    st.sidebar.error(f"Approval failed: {_extract_real_error(approval_log)}")
            if col_reject.button("🚫 Reject", use_container_width=True, key="btn_reject"):
                approval_gate.reject(latest, reason="rejected from dashboard")
                st.sidebar.warning("Rejected — nothing was sent.")
                st.rerun()
        if len(pending) > 1:
            st.sidebar.caption(f"({len(pending) - 1} older pending run(s) — review with "
                                f"`uv run python approve_run.py --show <path>`)")

    st.sidebar.markdown("<div class='step-connector'></div>", unsafe_allow_html=True)
    _step_header(3, "Bodhi risk verification")
    st.sidebar.caption("Optional (BODHI_ENABLED=true) — an independent second opinion that gates step ④.")
    if st.sidebar.button("🧠 Get Bodhi Risk Insight", use_container_width=True, key="btn_bodhi"):
        progress = st.sidebar.empty()
        returncode, full_log, _ = _run_with_live_progress(
            ["uv", "run", "python", "integrations/run_bodhi_risk_insight.py"], ROOT, progress,
            "sending today's shortlist to Bodhi and polling for its analysis (typically 60-120s)...",
        )
        with st.sidebar.expander("Bodhi run log", expanded=True):
            st.code(full_log, language="text")
        if returncode == 0:
            st.sidebar.success("Bodhi Risk Insight received — see the log above, or Telegram.")
        else:
            st.sidebar.error("Bodhi call failed — see log above.")

    st.sidebar.markdown("<div class='step-connector'></div>", unsafe_allow_html=True)
    _step_header(4, "Send Telegram alert")
    st.sidebar.caption("Re-sends the most recently APPROVED shortlist to Telegram — refuses if nothing's approved yet.")
    if st.sidebar.button("📨 Send Telegram alert", use_container_width=True, key="btn_telegram"):
        with st.spinner("Sending the latest shortlist to Telegram..."):
            proc = subprocess.run(
                ["uv", "run", "python", "alerts/trigger_telegram.py"],
                cwd=ROOT, capture_output=True, text=True,
            )
        with st.sidebar.expander("Telegram send log", expanded=proc.returncode != 0):
            st.code((proc.stdout or "") + (proc.stderr or ""), language="text")
        if proc.returncode == 0:
            st.sidebar.success("Telegram alert sent.")
        else:
            st.sidebar.error("Telegram send failed — see log above.")

    st.sidebar.markdown("<div class='step-connector'></div>", unsafe_allow_html=True)
    _step_header(5, "Push to TradingView")
    st.sidebar.caption(
        "Pushes the most recently APPROVED shortlist to your real TradingView "
        "watchlist — refuses if nothing's approved yet. Needs "
        "TRADINGVIEW_WATCHLIST_ENABLED=true and Desktop running with its debug port on."
    )
    if st.sidebar.button("📺 Push to TradingView", use_container_width=True, key="btn_tv"):
        with st.spinner("Pushing symbols to your TradingView watchlist..."):
            proc = subprocess.run(
                ["uv", "run", "python", "alerts/trigger_tradingview.py"],
                cwd=ROOT, capture_output=True, text=True,
            )
        with st.sidebar.expander("TradingView push log", expanded=proc.returncode != 0):
            st.code((proc.stdout or "") + (proc.stderr or ""), language="text")
        if proc.returncode == 0:
            st.sidebar.success("Pushed to TradingView.")
        else:
            st.sidebar.error("TradingView push failed — see log above.")

    st.sidebar.markdown("<div class='step-connector'></div>", unsafe_allow_html=True)
    _step_header(6, "Update price tracker")
    st.sidebar.caption(
        "Adds today's real price to every past sheet in both "
        "portfolio_tracker.xlsx (swing) and intraday_tracker.xlsx "
        "(intraday), 0 AI calls."
    )
    if st.sidebar.button("📒 Update price tracker", use_container_width=True, key="btn_tracker"):
        with st.spinner("Fetching real prices for every past pick..."):
            proc = subprocess.run(
                ["uv", "run", "python", "tracker/price_tracker.py"],
                cwd=ROOT, capture_output=True, text=True,
            )
        with st.sidebar.expander("Tracker run log", expanded=proc.returncode != 0):
            st.code((proc.stdout or "") + (proc.stderr or ""), language="text")
        if proc.returncode == 0:
            st.sidebar.success("Price tracker updated.")
        else:
            st.sidebar.error("Price tracker failed — see log above.")

    st.sidebar.divider()
    st.sidebar.header("Intraday · live, separate")
    st.sidebar.caption(
        "Mechanical opening-range / VWAP scan, 0 AI calls — only meaningful "
        "while the market is open (09:15–15:30 IST)."
    )
    if st.sidebar.button("⚡ Run intraday scan now", use_container_width=True, key="btn_intraday"):
        with st.spinner("Scanning today's intraday bars..."):
            proc = subprocess.run(
                ["uv", "run", "python", "intraday_run.py"],
                cwd=ROOT, capture_output=True, text=True,
            )
        with st.sidebar.expander("Intraday run log", expanded=proc.returncode != 0):
            st.code((proc.stdout or "") + (proc.stderr or ""), language="text")
        if proc.returncode == 0:
            st.sidebar.success("Intraday scan complete.")
            st.cache_data.clear()
            st.rerun()
        else:
            st.sidebar.error("Intraday scan failed — see log above.")


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def render_stock_section(analysis: dict):
    symbol = analysis["symbol"]
    quality = analysis.get("data_quality") or []
    title = f"{symbol}" + (" ⚠" if quality else "")
    with st.expander(title, expanded=False):
        if quality:
            st.warning("Data quality: " + "; ".join(quality))

        col_a, col_b = st.columns([1, 1])
        with col_a:
            st.plotly_chart(score_chart(analysis), use_container_width=True, key=f"score_{symbol}")
        with col_b:
            try:
                stats = cached_stats(symbol)
                st.caption("Live — as of now, not frozen at the time of this run")
                m1, m2 = st.columns(2)
                m1.metric("Current price", f"₹{stats['current_price']:,.2f}")
                m2.metric("1-week change", f"{stats['1w']['change_pct']:+.1f}%")
                m3, m4 = st.columns(2)
                m3.metric("1-month change", f"{stats['1m']['change_pct']:+.1f}%")
                m4.metric("6-month change", f"{stats['6m']['change_pct']:+.1f}%")
            except Exception as e:
                st.warning(f"Live stats unavailable for {symbol}: {e}")

        try:
            hist = cached_history(symbol)
            st.plotly_chart(price_chart(symbol, hist), use_container_width=True, key=f"price_{symbol}")
        except Exception as e:
            st.warning(f"Price chart unavailable for {symbol}: {e}")

        st.markdown(f"**Why flagged by the Scout:** {analysis.get('research_note', '')}")
        st.markdown(f"**Fundamental:** {analysis['fundamental'].get('verdict', '')}")
        st.markdown(f"**Technical:** {analysis['technical'].get('read', '')}")
        flags = ", ".join(analysis["risk"].get("flags", []))
        st.markdown(f"**Risk flags:** {flags}")

        # Real memory & context — what this run's own memory layer saw
        # across past calls on this exact stock, not just today's
        # snapshot (memory/memory_layer.py::build_delta()/consolidate_trend()).
        memory_trend = analysis.get("memory_trend")
        memory_delta = analysis.get("memory_delta")
        if memory_trend or (memory_delta and "no prior context" not in memory_delta.lower()):
            with st.expander("🧠 Memory — what changed across past calls on this stock", expanded=False):
                if memory_delta:
                    st.markdown(f"**Since last call:** {memory_delta}")
                if memory_trend:
                    st.markdown(f"**Multi-call trend:** {memory_trend}")

        event = analysis.get("event", {})
        if event:
            st.markdown(
                f"**News event:** {event.get('sentiment', 'n/a')} sentiment, "
                f"{event.get('event_type', 'n/a')} (materiality: {event.get('materiality', 'n/a')}) — "
                f"{event.get('summary', '')}"
            )

        headlines = analysis.get("headlines", [])
        if headlines:
            with st.expander("Real headlines used for this analysis"):
                for h in headlines:
                    st.markdown(f"- {h}")


def render_shortlist_card(pick: dict):
    with st.container(border=True):
        perf = pick.get("performance", {})
        day_pct = perf.get("1w", {}).get("change_pct")  # nearest real % change we have

        header_l, header_r = st.columns([3, 2])
        with header_l:
            price_txt = f"₹{pick['current_price']:,.2f}" if pick.get("current_price") is not None else ""
            st.markdown(
                f"<span class='ticket-symbol'>{pick['symbol']}</span>"
                f"&nbsp;&nbsp;<span class='terminal-mono' style='font-size:1.15rem;color:{COLOR_TEXT}'>{price_txt}</span>"
                f"&nbsp;&nbsp;{_pct_span(day_pct)}",
                unsafe_allow_html=True,
            )
        with header_r:
            badges = (
                f"{action_badge(pick.get('action', ''))} "
                f"{conviction_badge(pick.get('conviction', ''))} "
                f"{portfolio_fit_badge(pick.get('portfolio_fit', ''))}"
            )
            st.markdown(f"<div style='text-align:right;padding-top:6px'>{badges}</div>", unsafe_allow_html=True)

        if pick.get("conviction_note"):
            st.caption(pick["conviction_note"])

        cols = st.columns(6)
        cols[0].metric("Opportunity", f"{pick.get('opportunity_score', '?')}/100")
        cols[1].metric("Confidence", f"{pick.get('confidence', '?')}%")
        if pick.get("current_price") is not None:
            cols[2].metric("Current price", f"₹{pick['current_price']:,.2f}")
        for col, key in zip(cols[3:6], ("1w", "1m", "6m")):
            if key in perf:
                col.metric(key, f"{perf[key]['change_pct']:+.1f}%")

        st.write(f"**Risk:** {pick.get('risk_percent', '?')}%")

        if pick.get("allocated_amount") is not None:
            shares_txt = f" (~{pick['allocated_shares']} shares)" if pick.get("allocated_shares") else ""
            st.success(f"💰 **Suggested allocation today: ₹{pick['allocated_amount']:,}{shares_txt}**")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**📈 Bull case**  \n{pick.get('bull_case', pick.get('thesis', ''))}")
        with c2:
            if pick.get("bear_case"):
                st.markdown(f"**📉 Bear case**  \n{pick['bear_case']}")

        if pick.get("near_term_outlook"):
            st.write(f"**Near-term outlook:** {pick['near_term_outlook']}")
        if pick.get("target_price") is not None:
            st.write(
                f"**Reference target price:** ₹{pick['target_price']:,.2f} "
                f"— {pick.get('target_price_rationale', '')}"
            )

        qty = pick.get("suggested_quantity", {})
        if qty.get("max") is not None:
            st.write(f"**Position-size cap:** {qty['min']}–{qty['max']} shares max")
            st.caption(qty.get("note", ""))
        elif qty.get("note"):
            st.caption(f"Position-size cap: n/a — {qty['note']}")

        if pick.get("portfolio_fit_note"):
            st.caption(f"Portfolio fit: {pick['portfolio_fit_note']}")
        if pick.get("invalidation"):
            st.caption(f"What would change this view: {pick['invalidation']}")
        st.caption(f"⚠ Key risk: {pick.get('key_risk', '')}")


def render_capital_summary(capital_summary: dict):
    if not capital_summary or capital_summary.get("total_allocated") is None:
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Total capital", f"₹{capital_summary['total_capital']:,}")
    c2.metric("Suggested to deploy", f"₹{capital_summary['total_allocated']:,}")
    c3.metric("Left in cash", f"₹{capital_summary['cash_remaining']:,}")


TIER_ICON = {"conservative": "🟢", "moderate": "🟡", "aggressive": "🔴"}


def render_glossary():
    with st.expander("ℹ️ What do these terms mean?"):
        st.markdown(
            "- **🔥 High conviction** — the AI is fairly confident in this call: the data "
            "lines up cleanly and nothing about it is flagged as thin or stale. "
            "**👀 Low conviction** — treat it as a watch-list idea, not a strong call; "
            "either the data has gaps, or the signals are mixed.\n"
            "- **Risk appetite (conservative / moderate / aggressive)** — how much "
            "short-term pain you can stomach for potentially bigger gains. Conservative "
            "sticks to safer, steadier names; aggressive is willing to ride out bigger "
            "swings for a shot at bigger moves. Pick more than one to compare shortlists "
            "side by side.\n"
            "- **Max position size per stock (%)** — the most of your capital that will "
            "EVER go into one single stock, however good it looks. E.g. 15% of ₹30,000 "
            "= at most ₹4,500 in any one name — this protects you if that one pick goes "
            "wrong.\n"
            "- **Suggested allocation today** — the actual ₹ split across today's whole "
            "shortlist, weighted so higher-conviction picks get a bigger share, capped "
            "at your max position size. Adds up to your capital (or less, if the caps "
            "bind).\n"
            "- **Opportunity vs. confidence** — opportunity is how good the setup looks; "
            "confidence is how sure the AI is about that read, given data quality. A "
            "great opportunity with thin data still gets flagged with lower confidence."
        )


def render_tier_result(tier: str, result: dict, multi_tier: bool):
    if multi_tier:
        st.markdown(f"#### {TIER_ICON.get(tier, '')} {tier.capitalize()} profile")

    shortlist = result.get("shortlist", [])
    if not shortlist:
        st.info("No shortlist for this profile in this run.")
        return

    render_capital_summary(result.get("capital_summary", {}))
    for pick in shortlist:
        render_shortlist_card(pick)

    if result.get("summary"):
        st.caption(result["summary"])


def render_ticker_strip(results_by_tier: dict):
    """A live watchlist ribbon — symbol, real current price, real % change
    — for every distinct symbol shortlisted in this run. Reuses the same
    cached_stats() call every stock section already makes, so this adds
    no extra network load beyond what's already fetched on expand."""
    symbols = []
    for result in results_by_tier.values():
        for pick in result.get("shortlist", []):
            if pick["symbol"] not in symbols:
                symbols.append(pick["symbol"])
    if not symbols:
        return

    chips = []
    for sym in symbols:
        try:
            stats = cached_stats(sym)
            price = f"₹{stats['current_price']:,.2f}"
            pct = stats.get("1w", {}).get("change_pct")
        except Exception:
            price, pct = "—", None
        chips.append(
            f"<div class='ticker-chip'><span class='ticker-symbol'>{sym}</span>"
            f"<span class='ticker-price'>{price}</span>{_pct_span(pct)}</div>"
        )
    st.markdown(f"<div class='ticker-strip'>{''.join(chips)}</div>", unsafe_allow_html=True)


def render_swing_tab():
    runs = list_runs()
    if not runs:
        st.info("No swing analysis runs yet. Click **▶ Run swing analysis now** in the sidebar to generate one.")
        return

    labels = [p.stem.replace("analysis_", "") for p in runs]
    selected = st.selectbox("Run date", labels, index=0, key="swing_run_select")
    data = load_run(runs[labels.index(selected)])

    # Back-compat: older runs saved a single "result", newer ones save
    # "results_by_tier" (one shortlist per selected risk appetite).
    results_by_tier = data.get("results_by_tier")
    if not results_by_tier:
        legacy = data.get("result")
        results_by_tier = {"moderate": legacy} if legacy else {}

    render_ticker_strip(results_by_tier)

    mkt = data.get("market_context", {})
    if mkt:
        st.caption(
            f"Nifty 50 that day: {mkt.get('nifty_pct_change', 0):+.1f}%, "
            f"trend={mkt.get('nifty_trend', 'unknown')}, "
            f"volatility={mkt.get('nifty_volatility_pct', 0)}% annualised"
        )

    render_glossary()

    st.subheader("Shortlist")
    if not results_by_tier:
        st.info("No shortlist in this run.")
    elif len(results_by_tier) == 1:
        render_tier_result(*next(iter(results_by_tier.items())), multi_tier=False)
    else:
        tier_tabs = st.tabs([f"{TIER_ICON.get(t, '')} {t.capitalize()}" for t in results_by_tier])
        for tab, (tier, result) in zip(tier_tabs, results_by_tier.items()):
            with tab:
                render_tier_result(tier, result, multi_tier=False)

    st.subheader("Per-stock analysis")
    for analysis in data.get("analyses", []):
        render_stock_section(analysis)

    profile = data.get("profile")
    if profile:
        with st.expander("Investor profile used for this run"):
            st.json(profile)


def render_intraday_tab():
    st.caption(
        "Mechanical opening-range / VWAP / volume-surge scan — 0 AI calls. "
        "🔥 HIGH CONVICTION = a real breakout with volume confirming it. "
        "👀 LOW CONVICTION = a name to watch, no confirmed breakout yet. "
        "Only meaningful while the market is open (09:15–15:30 IST)."
    )
    runs = list_intraday_runs()
    if not runs:
        st.info("No intraday scans yet. Click **⚡ Run intraday scan now** in the sidebar during market hours.")
        return

    labels = [p.stem.replace("intraday_scan_", "") for p in runs]
    selected = st.selectbox("Scan time", labels, index=0, key="intraday_run_select")
    data = load_run(runs[labels.index(selected)])
    picks = data.get("picks", [])
    if not picks:
        st.info("No picks in this scan.")

    for p in picks:
        with st.container(border=True):
            conv = p.get("conviction", "?")
            conv_color = COLOR_GOOD if conv == "HIGH" else COLOR_MUTED
            dir_color = {"bullish": COLOR_GOOD, "bearish": COLOR_CRITICAL}.get(p.get("direction"), COLOR_MUTED)
            conv_icon = CONVICTION_ICON.get(conv, "")
            conv_text = f"{conv_icon} {conv} conviction".strip()
            badges = (
                f"{_badge(conv_text, conv_color)} "
                f"{_badge(p.get('direction', '?'), dir_color)}"
            )
            st.markdown(
                f"<span class='ticket-symbol'>{p['symbol']}</span>&nbsp;&nbsp;{badges}",
                unsafe_allow_html=True,
            )
            st.caption(p.get("conviction_note", ""))
            if p.get("narrative"):
                st.write(p["narrative"])
            if p.get("pattern_tag"):
                st.caption(
                    f"Pattern: {p['pattern_tag'].replace('_', ' ')} "
                    f"(confidence: {p.get('pattern_confidence', '?')})"
                )
            st.write(p.get("read", ""))
            if p.get("entry") is not None:
                cols = st.columns(3)
                cols[0].metric("Entry", f"₹{p['entry']:.2f}")
                if p.get("stop_loss") is not None:
                    cols[1].metric("Stop loss", f"₹{p['stop_loss']:.2f}")
                if p.get("target") is not None:
                    cols[2].metric("Target", f"₹{p['target']:.2f}")


def render_activity_tab():
    """Real operator-visible history — recent swing runs and every real
    approval-gate state transition — so day-to-day operation doesn't
    require hunting through output/ by hand."""
    st.caption(
        "Recent swing runs, and every real approval-gate decision — "
        "pending, approved, and rejected — so you can see what happened "
        "without opening files by hand."
    )

    st.subheader("Recent swing runs")
    runs = list_runs()[:8]
    if not runs:
        st.info("No swing runs yet.")
    else:
        for path in runs:
            try:
                data = load_run(path)
            except Exception:
                continue
            date_label = path.stem.replace("analysis_", "")
            results_by_tier = data.get("results_by_tier") or {}
            tier_summaries = ", ".join(
                f"{tier}: {len(r.get('shortlist', []))} pick(s)"
                for tier, r in results_by_tier.items()
            ) or "no shortlist"
            st.write(f"📅 **{date_label}** — {tier_summaries}")

    st.divider()
    st.subheader("Approval history")
    col_pending, col_approved, col_rejected = st.columns(3)

    with col_pending:
        st.markdown("**⏸ Pending**")
        pending = approval_gate.list_pending()
        if not pending:
            st.caption("None")
        for path in pending[:5]:
            record = approval_gate.load(path)
            n_picks = sum(len(r.get("shortlist", [])) for r in (record.get("results_by_tier") or {}).values())
            st.write(f"{record.get('date', '?')} — {n_picks} pick(s)")

    with col_approved:
        st.markdown("**✅ Approved**")
        approved = approval_gate.list_approved()
        if not approved:
            st.caption("None")
        for path in approved[:5]:
            record = approval_gate.load(path)
            st.write(f"{record.get('date', '?')} — {record.get('approved_at', '?')[:16]}")

    with col_rejected:
        st.markdown("**🚫 Rejected**")
        rejected = approval_gate.list_rejected()
        if not rejected:
            st.caption("None")
        for path in rejected[:5]:
            record = approval_gate.load(path)
            reason = record.get("rejection_reason", "")
            st.write(f"{record.get('date', '?')} — {reason or '(no reason given)'}")

    st.divider()
    st.subheader("🛠 Real tool calls agents made mid-reasoning")
    st.caption(
        "Not the LLM calls themselves — real external tool I/O an agent "
        "triggered while reasoning, e.g. the Decision Agent's live "
        "TradingView quote (integrations/tradingview_quote.py). Read "
        "straight from output/tool_call_log.jsonl."
    )
    tool_calls = observability.read_recent("tool_calls", n=15)
    if not tool_calls:
        st.caption("None yet — enable TRADINGVIEW_QUOTE_ENABLED and run a swing analysis.")
    else:
        for c in tool_calls:
            icon = "✅" if c.get("success") else "❌"
            latency = c.get("latency_ms")
            latency_txt = f", {latency / 1000:.1f}s" if latency else ""
            st.write(
                f"{icon} `{c.get('tool')}` → **{c.get('target')}** "
                f"(by {c.get('agent', '?')}{latency_txt}) — {c.get('timestamp', '?')}"
            )

    st.divider()
    st.subheader("🔀 Orchestrator routing decisions")
    st.caption(
        "The decide → reflect → route retry loop's real branch decisions "
        "(agents/orchestrator_agent.py) — every time it accepted a "
        "Decision Agent output as-is vs. sent it back with feedback for "
        "another attempt, and why."
    )
    decisions = observability.read_recent("decisions", n=10)
    if not decisions:
        st.caption("None yet — run a swing analysis.")
    else:
        for d in decisions:
            icon = "🔁" if d.get("next_step") == "retry_decision" else "✅"
            n_issues = len(d.get("issues_found") or [])
            with st.expander(
                f"{icon} [{d.get('tier', '?')}] {d.get('next_step', '?')} "
                f"— {n_issues} issue(s) — {d.get('timestamp', '?')}",
                expanded=False,
            ):
                st.write(f"**Reason:** {d.get('reason', '?')}")
                if d.get("issues_found"):
                    st.write("**Issues found:**")
                    for i in d["issues_found"]:
                        st.write(f"- {i}")

    st.divider()
    st.subheader("🩹 Repairs, budget warnings, and real failures")
    st.caption(
        "output/reliability_log.jsonl, distinguishing REPAIRS (a malformed "
        "tool/verdict result the code fixed automatically instead of "
        "discarding — schemas.py) from real failures/warnings. Neither "
        "kind ever blocked the run that produced it."
    )
    events = observability.read_recent("failures", n=15)
    if not events:
        st.caption("None yet — run a swing analysis.")
    else:
        for e in events:
            component = e.get("component", "?")
            if "repaired" in component:
                icon = "🩹"
            elif component == "bodhi_token_budget_exceeded":
                icon = "💰"
            else:
                icon = "⚠️"
            st.write(f"{icon} `{component}` — {e.get('detail', '?')} ({e.get('timestamp', '?')})")


def main():
    st.set_page_config(page_title="AI Equity Analyst", page_icon="📈", layout="wide")
    inject_terminal_css()

    title_col, status_col = st.columns([4, 1])
    with title_col:
        st.markdown(
            "<span style='font-family:\"JetBrains Mono\",monospace;font-size:1.7rem;"
            "font-weight:700;letter-spacing:0.01em'>📈 AI EQUITY ANALYST</span>",
            unsafe_allow_html=True,
        )
    with status_col:
        st.markdown(f"<div style='text-align:right;padding-top:10px'>{market_status_html()}</div>", unsafe_allow_html=True)
    st.caption(
        "⚠️ Not investment advice — AI-generated analysis for informational purposes "
        "only. Do your own research and consult a SEBI-registered advisor before investing."
    )

    render_profile_builder()
    st.sidebar.divider()
    render_run_controls()

    swing_tab, intraday_tab, activity_tab = st.tabs(
        ["📊 Swing shortlist", "⚡ Intraday scan", "📜 Activity"]
    )
    with swing_tab:
        render_swing_tab()
    with intraday_tab:
        render_intraday_tab()
    with activity_tab:
        render_activity_tab()


if __name__ == "__main__":
    main()
