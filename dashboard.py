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

Gemini/Telegram credentials are intentionally NOT editable here — they're
secrets, not day-to-day knobs, and stay in .env only.
"""

import datetime as dt
import html
import json
import subprocess
import sys
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from data import market_data  # noqa: E402
from profile.profile_loader import load_profile  # noqa: E402

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
        "① and ② run automatically. Nothing is sent externally yet — the "
        "shortlist lands in the ✅ Approvals tab for a human decision before "
        "Telegram/TradingView (③/④ below are manual re-send tools, not the "
        "normal path)."
    )

    _step_header(1, "Run full swing analysis")
    if st.sidebar.button("▶ Run swing analysis now", use_container_width=True, key="btn_swing"):
        # Always save whatever the profile widgets currently show, right
        # before running — so this run can NEVER silently use a stale
        # profile just because "Save profile" wasn't clicked separately.
        _write_profile(_profile_from_widget_state())
        with st.spinner("Running the full agent pipeline..."):
            proc = subprocess.run(
                ["uv", "run", "python", "run.py"],
                cwd=ROOT, capture_output=True, text=True,
            )
        with st.sidebar.expander("Run log", expanded=proc.returncode != 0):
            st.code((proc.stdout or "") + (proc.stderr or ""), language="text")
        if proc.returncode == 0:
            st.sidebar.success("Run complete.")
            st.cache_data.clear()
            st.rerun()
        else:
            st.sidebar.error("Run failed — see log above.")

    st.sidebar.markdown("<div class='step-connector'></div>", unsafe_allow_html=True)
    _step_header(2, "Bodhi risk verification")
    st.sidebar.caption("Optional (BODHI_ENABLED=true) — an independent second opinion that gates step ④.")
    if st.sidebar.button("🧠 Get Bodhi Risk Insight", use_container_width=True, key="btn_bodhi"):
        with st.spinner("Sending today's shortlist to Bodhi and waiting for its analysis..."):
            proc = subprocess.run(
                ["uv", "run", "python", "integrations/run_bodhi_risk_insight.py"],
                cwd=ROOT, capture_output=True, text=True,
            )
        with st.sidebar.expander("Bodhi run log", expanded=True):
            st.code((proc.stdout or "") + (proc.stderr or ""), language="text")
        if proc.returncode == 0:
            st.sidebar.success("Bodhi Risk Insight received — see the log above, or Telegram.")
        else:
            st.sidebar.error("Bodhi call failed — see log above.")

    st.sidebar.markdown("<div class='step-connector'></div>", unsafe_allow_html=True)
    _step_header(3, "Send Telegram alert")
    st.sidebar.caption(
        "New shortlists now go through the ✅ Approvals tab first — use this "
        "only to manually re-send an already-approved shortlist."
    )
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
    _step_header(4, "Push to TradingView")
    st.sidebar.caption(
        "New shortlists now go through the ✅ Approvals tab first — use this "
        "only to manually re-push an already-approved shortlist. Needs "
        "TRADINGVIEW_WATCHLIST_ENABLED=true and Desktop running with its debug "
        "port on."
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
    _step_header(5, "Update price tracker")
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

    if st.sidebar.button("📈 Quality report — is it working?", use_container_width=True, key="btn_quality"):
        with st.spinner("Computing real win-rate from tracked history..."):
            proc = subprocess.run(
                ["uv", "run", "python", "tracker/quality_report.py"],
                cwd=ROOT, capture_output=True, text=True,
            )
        with st.sidebar.expander("Quality report", expanded=True):
            st.code((proc.stdout or "") + (proc.stderr or ""), language="text")

    if st.sidebar.button("🧾 Collect Bodhi run evidence", use_container_width=True, key="btn_evidence"):
        with st.spinner("Reading real Bodhi run history from telemetry..."):
            proc = subprocess.run(
                ["uv", "run", "python", "integrations/bodhi_workflow/collect_run_evidence.py"],
                cwd=ROOT, capture_output=True, text=True,
            )
        with st.sidebar.expander("Bodhi run evidence", expanded=True):
            st.code((proc.stdout or "") + (proc.stderr or ""), language="text")
            st.caption("Also saved to integrations/bodhi_workflow/RUN_EVIDENCE.md")

    if st.sidebar.button("🛠 Ops report — what ran, what broke", use_container_width=True, key="btn_ops"):
        with st.spinner("Reading local telemetry..."):
            proc = subprocess.run(
                ["uv", "run", "python", "tracker/ops_report.py"],
                cwd=ROOT, capture_output=True, text=True,
            )
        with st.sidebar.expander("Ops report", expanded=True):
            st.code((proc.stdout or "") + (proc.stderr or ""), language="text")

    if st.sidebar.button("🔎 Retrieval eval — is memory useful?", use_container_width=True, key="btn_reteval"):
        with st.spinner("Scoring retrieval against the real indexed corpus..."):
            proc = subprocess.run(
                ["uv", "run", "python", "memory/retrieval_eval.py"],
                cwd=ROOT, capture_output=True, text=True,
            )
        with st.sidebar.expander("Retrieval eval", expanded=True):
            st.code((proc.stdout or "") + (proc.stderr or ""), language="text")

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


def render_release_rules(record: dict):
    """The business rules that decide what may be pushed externally, shown
    per symbol with each rule's real pass/fail — evaluated live from
    contracts.py, so what's displayed is exactly what will gate the push,
    not a description of it written somewhere else."""
    import contracts

    plan = contracts.release_plan(
        record.get("results_by_tier", {}),
        record.get("bodhi_decisions"),
        record.get("bodhi_ran", False),
    )
    if not plan["detail"]:
        return

    st.markdown("**📋 Release rules** — what will actually be pushed, and why")
    st.caption(
        f"{len(plan['allowed'])} of {len(plan['detail'])} symbol(s) currently pass all rules. "
        f"Rules and thresholds live in `contracts.py`."
    )

    for symbol, evaluation in plan["detail"].items():
        verdict = "✅ releasable" if evaluation["allowed"] else "⛔ held back"
        with st.expander(f"{symbol} — {verdict}", expanded=not evaluation["allowed"]):
            for rule in evaluation["rules"]:
                mark = "✅" if rule["passed"] else "❌"
                st.markdown(
                    f"{mark} **{rule['id']} · {rule['name']}**  \n"
                    f"<span style='color:{COLOR_MUTED};font-size:0.85rem'>"
                    f"{html.escape(str(rule['detail']))}</span>",
                    unsafe_allow_html=True,
                )


def render_approvals_tab():
    """The human checkpoint before anything reaches Telegram/TradingView.
    Every swing run now saves its shortlist here instead of sending it
    automatically — see alerts/approval_queue.py."""
    from alerts import approval_queue

    records = [(p, approval_queue.load(p)) for p in approval_queue.list_queue()]
    pending = [(p, r) for p, r in records if r["status"] == "pending"]
    decided = [(p, r) for p, r in records if r["status"] != "pending"]

    if not pending:
        st.info("No shortlists awaiting approval right now.")
    else:
        st.warning(
            f"**{len(pending)} shortlist(s) awaiting review** — nothing below has "
            "been sent to Telegram or pushed to TradingView yet."
        )

    for path, record in pending:
        with st.container(border=True):
            st.markdown(f"### {record['run_date']}  ·  saved {record['created_at']}")
            render_ticker_strip(record["results_by_tier"])
            for tier, result in record["results_by_tier"].items():
                render_tier_result(tier, result, multi_tier=len(record["results_by_tier"]) > 1)

            if record.get("bodhi_ran") and record.get("bodhi_decisions"):
                st.markdown("**🧠 Bodhi per-symbol decisions** — one independent Bodhi run per stock")
                decision_style = {
                    "proceed": ("✅", COLOR_GOOD, "PROCEED — goes to TradingView"),
                    "blocked": ("🚫", COLOR_CRITICAL, "BLOCKED — excluded from TradingView"),
                    "unverified": ("❔", COLOR_WARNING, "UNVERIFIED — review didn't complete, excluded from TradingView"),
                }
                for d in record["bodhi_decisions"]:
                    icon, color, label = decision_style.get(d["decision"], ("", COLOR_MUTED, d["decision"].upper()))
                    conf = f" (research: {html.escape(str(d['research_confidence']))})" if d.get("research_confidence") else ""
                    run_id = d.get("bodhi_run_id")
                    run_id_html = (
                        f"<br><span style='color:{COLOR_MUTED};font-size:0.75rem;font-family:\"JetBrains Mono\",monospace'>"
                        f"Bodhi run: {html.escape(str(run_id))}</span>"
                        if run_id else
                        f"<br><span style='color:{COLOR_CRITICAL};font-size:0.75rem'>no Bodhi run id captured</span>"
                    )
                    # Escaped, not interpolated raw: `reason` originates from
                    # Bodhi's web research, so it is externally-authored text
                    # being placed inside an unsafe_allow_html block.
                    st.markdown(
                        f"<div style='margin-bottom:4px'><b>{html.escape(str(d['symbol']))}</b>&nbsp;"
                        f"{_badge(f'{icon} {label}', color)}{conf}<br>"
                        f"<span style='color:{COLOR_MUTED};font-size:0.85rem'>"
                        f"{html.escape(str(d.get('reason', '')))}</span>{run_id_html}</div>",
                        unsafe_allow_html=True,
                    )
            elif record.get("bodhi_ran"):
                st.caption("Bodhi ran but returned no structured per-symbol decision — see the full report after approving.")

            render_release_rules(record)

            c1, c2 = st.columns(2)
            if c1.button("✅ Approve & send", key=f"approve_{path}", use_container_width=True):
                with st.spinner("Sending to Telegram and pushing to TradingView..."):
                    try:
                        approval_queue.approve(str(path))
                        st.success("Approved and sent.")
                    except Exception as e:
                        st.error(f"Approval failed: {e}")
                st.rerun()
            if c2.button("❌ Reject", key=f"reject_{path}", use_container_width=True):
                approval_queue.reject(str(path))
                st.warning("Rejected — nothing was sent.")
                st.rerun()

    if decided:
        with st.expander(f"Run history — past decisions ({len(decided)})"):
            for path, record in decided:
                icon = "✅" if record["status"] == "approved" else "❌"
                who_when = (
                    f"by {record.get('approved_by', '?')} at {record.get('approved_at', '?')}"
                    if record["status"] == "approved"
                    else f"at {record.get('rejected_at', '?')}"
                )
                bodhi_decisions = record.get("bodhi_decisions") or []
                if bodhi_decisions:
                    n_proceed = sum(1 for d in bodhi_decisions if d["decision"] == "proceed")
                    n_blocked = sum(1 for d in bodhi_decisions if d["decision"] == "blocked")
                    n_unverified = sum(1 for d in bodhi_decisions if d["decision"] == "unverified")
                    bodhi_summary = f" · 🧠 ✅{n_proceed} 🚫{n_blocked} ❔{n_unverified}"
                else:
                    bodhi_summary = ""
                st.write(f"{icon} **{record['run_date']}** — {record['status']} ({who_when}){bodhi_summary}")
                if record["status"] == "approved" and record.get("tv_symbols_sent") is not None:
                    st.caption(f"Pushed to TradingView: {', '.join(record['tv_symbols_sent']) or '(none)'}")


def render_ops_tab():
    """Runtime evidence: what actually ran, how long it took, what it cost,
    what broke, what the retrieval layer is doing, and what prompt-safety
    caught. All read from output/metrics.jsonl + output/reliability_log.jsonl,
    which are written on EVERY run with no configuration — this panel is
    never empty because someone forgot to set up a tracing service."""
    import observability as obs

    metrics = obs.read_metrics()
    failures = obs.read_failures()

    if not metrics:
        st.info(
            "No telemetry recorded yet. Run the swing pipeline once "
            "(**▶ Run swing analysis now**) and this fills in — no setup required."
        )
    else:
        runs = {}
        for m in metrics:
            runs.setdefault(m.get("run_id", "adhoc"), []).append(m)

        llm_calls = [m for m in metrics if m.get("event") == "llm_call"]
        safety_events = [m for m in metrics if m.get("event") == "safety_event"]
        retrievals = [m for m in metrics if m.get("event") == "kb_retrieval"]
        total_tokens = sum(m.get("total_tokens") or 0 for m in llm_calls)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Runs recorded", len(runs))
        c2.metric("LLM calls", len(llm_calls))
        c3.metric("Tokens used", f"{total_tokens:,}" if total_tokens else "—")
        c4.metric("Failures logged", len(failures))

        c5, c6, c7, c8 = st.columns(4)
        errored = [m for m in llm_calls if m.get("status") == "error"]
        c5.metric("LLM errors", len(errored))
        latencies = [m["duration_ms"] for m in llm_calls if m.get("duration_ms")]
        c6.metric("Median LLM latency",
                   f"{sorted(latencies)[len(latencies) // 2] / 1000:.1f}s" if latencies else "—")
        c7.metric("Retrievals", len(retrievals))
        c8.metric("Prompt-safety hits", len(safety_events),
                   help="Injection-like spans neutralised in untrusted text before it "
                        "reached a prompt. Non-zero is not necessarily an attack — "
                        "financial headlines legitimately contain phrases like 'act as'.")

        st.divider()
        st.markdown("#### ⏱ Per-agent latency and token cost")
        by_agent = {}
        for m in llm_calls:
            agent = m.get("agent", "unknown")
            entry = by_agent.setdefault(agent, {"calls": 0, "ms": 0, "tokens": 0, "errors": 0})
            entry["calls"] += 1
            entry["ms"] += m.get("duration_ms") or 0
            entry["tokens"] += m.get("total_tokens") or 0
            if m.get("status") == "error":
                entry["errors"] += 1
        if by_agent:
            st.dataframe(
                [
                    {
                        "Agent": agent,
                        "Calls": v["calls"],
                        "Errors": v["errors"],
                        "Avg latency": f"{v['ms'] / v['calls'] / 1000:.1f}s" if v["calls"] else "—",
                        "Total tokens": f"{v['tokens']:,}" if v["tokens"] else "—",
                    }
                    for agent, v in sorted(by_agent.items())
                ],
                use_container_width=True, hide_index=True,
            )

        if safety_events:
            st.divider()
            st.markdown("#### 🛡 Prompt-safety events")
            st.caption(
                "Every instruction-like span neutralised in untrusted text before it "
                "reached a model — see `data/text_safety.py`."
            )
            for e in safety_events[-15:]:
                findings = ", ".join(str(f) for f in (e.get("findings") or [])[:5])
                st.markdown(
                    f"<span style='color:{COLOR_MUTED};font-size:0.85rem'>"
                    f"{html.escape(str(e.get('timestamp', '')))} · "
                    f"<b>{html.escape(str(e.get('source', '?')))}</b> · "
                    f"{e.get('finding_count', 0)} span(s): "
                    f"{html.escape(findings)}</span>",
                    unsafe_allow_html=True,
                )

        if failures:
            st.divider()
            st.markdown("#### ⚠ Recent failures")
            for f in failures[-15:]:
                st.markdown(
                    f"<span style='color:{COLOR_MUTED};font-size:0.85rem'>"
                    f"{html.escape(str(f.get('timestamp', '')))} · "
                    f"<b>{html.escape(str(f.get('component', '?')))}</b> — "
                    f"{html.escape(str(f.get('detail', ''))[:200])}</span>",
                    unsafe_allow_html=True,
                )

    # ------------------------------------------------------------------
    st.divider()
    st.markdown("#### 🔎 Knowledge retrieval")
    col_a, col_b = st.columns([1, 1])
    with col_a:
        try:
            from memory.knowledge_base import KnowledgeBase
            stats = KnowledgeBase().stats()
            if stats.get("available"):
                st.write(
                    f"**{stats.get('chunks', 0)}** indexed chunks · "
                    f"**{stats.get('distinct_symbols', 0)}** symbols · "
                    f"**{stats.get('distinct_sectors', 0)}** sectors"
                )
                st.caption(
                    f"Sections: {stats.get('by_section', {})} · "
                    f"range {stats.get('earliest')} → {stats.get('latest')} · "
                    f"distance space: {stats.get('distance_space')}"
                )
            else:
                st.caption("chromadb unavailable — retrieval disabled.")
        except Exception as e:  # noqa: BLE001
            st.caption(f"Knowledge base unavailable: {e}")
    with col_b:
        eval_path = OUTPUT_DIR / "retrieval_eval.json"
        if eval_path.exists():
            try:
                report = json.loads(eval_path.read_text())
                if report.get("evaluable_queries"):
                    st.write(
                        f"**hit@{report['k']}** {report['hit_rate_at_k']:.0%} · "
                        f"**P@{report['k']}** {report['precision_at_k']:.0%} · "
                        f"**MRR** {report['mrr']:.2f}"
                    )
                    baseline = report.get("random_baseline_precision") or 0
                    if baseline:
                        st.caption(
                            f"vs {baseline:.0%} random baseline "
                            f"({report['precision_at_k'] / baseline:.1f}x better than chance), "
                            f"over {report['evaluable_queries']} evaluated queries"
                        )
                else:
                    st.caption(report.get("summary", "Not enough history to evaluate yet."))
            except (json.JSONDecodeError, OSError, KeyError, ZeroDivisionError):
                st.caption("Retrieval eval file present but unreadable.")
        else:
            st.caption("No retrieval eval yet — run the button in the sidebar.")

    # ------------------------------------------------------------------
    st.divider()
    st.markdown("#### 📑 Pipeline contracts")
    st.caption(
        "What each pipeline is guaranteed to take in, produce, and which states it "
        "can be in — read live from `contracts.py`, the same definitions the code "
        "validates against at runtime."
    )
    import contracts

    for contract in contracts.ALL_CONTRACTS:
        with st.expander(contract.name):
            st.write(contract.purpose)
            st.markdown("**Inputs**")
            for k, v in contract.inputs.items():
                st.markdown(f"- `{k}` — {v}")
            st.markdown("**Guaranteed outputs**")
            for k, v in contract.outputs.items():
                st.markdown(f"- `{k}` — {v}")
            st.markdown("**States**")
            for k, v in contract.states.items():
                st.markdown(f"- `{k}` — {v}")
            st.markdown(f"**External effects:** {contract.external_effects}")

    with st.expander("Release rules and thresholds"):
        st.markdown(
            f"- **Minimum confidence to release:** {contracts.MIN_RELEASE_CONFIDENCE}%\n"
            f"- **Stances that can never be released:** {', '.join(contracts.BLOCKING_ACTIONS)}\n"
            f"- **Bodhi clearance required when Bodhi ran:** "
            f"{contracts.REQUIRE_BODHI_WHEN_ENABLED}\n"
        )
        st.caption("Change these in `contracts.py` and the gate, the logs and this panel all move together.")


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

    from alerts import approval_queue
    pending_count = sum(
        1 for p in approval_queue.list_queue() if approval_queue.load(p)["status"] == "pending"
    )
    approvals_label = f"✅ Approvals ({pending_count})" if pending_count else "✅ Approvals"

    swing_tab, approvals_tab, intraday_tab, ops_tab = st.tabs(
        ["📊 Swing shortlist", approvals_label, "⚡ Intraday scan", "🛠 Ops & evidence"]
    )
    with swing_tab:
        render_swing_tab()
    with approvals_tab:
        render_approvals_tab()
    with intraday_tab:
        render_intraday_tab()
    with ops_tab:
        render_ops_tab()


if __name__ == "__main__":
    main()
