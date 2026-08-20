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

# Palette (validated categorical/status hues — see the dataviz skill)
COLOR_BLUE = "#2a78d6"
COLOR_ORANGE = "#eb6834"
COLOR_YELLOW = "#eda100"
COLOR_GOOD = "#0ca30c"
COLOR_WARNING = "#fab219"
COLOR_CRITICAL = "#d03b3b"
COLOR_GRID = "#e1e0d9"
COLOR_MUTED = "#898781"
COLOR_SURFACE = "#fcfcfb"

SCORE_COLORS = {"Fundamental": COLOR_BLUE, "Technical": COLOR_ORANGE, "Risk": COLOR_YELLOW}


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
    fig = go.Figure(go.Scatter(
        x=hist.index, y=hist["Close"],
        mode="lines",
        line=dict(color=COLOR_BLUE, width=2),
        hovertemplate="%{x|%d %b %Y}<br>₹%{y:.2f}<extra></extra>",
    ))
    fig.update_layout(
        template="plotly_white",
        height=240,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(showgrid=False, color=COLOR_MUTED),
        yaxis=dict(showgrid=True, gridcolor=COLOR_GRID, color=COLOR_MUTED),
        plot_bgcolor=COLOR_SURFACE,
        paper_bgcolor=COLOR_SURFACE,
        showlegend=False,
    )
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
        template="plotly_white",
        height=220,
        margin=dict(l=10, r=10, t=10, b=10),
        yaxis=dict(range=[0, 10], gridcolor=COLOR_GRID, color=COLOR_MUTED),
        xaxis=dict(color=COLOR_MUTED),
        plot_bgcolor=COLOR_SURFACE,
        paper_bgcolor=COLOR_SURFACE,
        showlegend=False,
    )
    return fig


def _badge(text: str, color: str) -> str:
    return (
        f"<span style='background:{color}22;color:{color};padding:2px 10px;"
        f"border-radius:4px;font-weight:600;font-size:0.8rem'>{text.upper()}</span>"
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

    risk_options = ["conservative", "moderate", "aggressive"]
    horizon_options = ["short", "medium", "long"]

    with st.sidebar.form("profile_form"):
        doc_default_risk = doc_profile.get("risk_appetite")
        risk_appetites = st.multiselect(
            "Risk appetite (pick one or more)", risk_options,
            default=[doc_default_risk] if doc_default_risk in risk_options else ["moderate"],
            help="Conservative = safety first, smaller bets, avoids volatile names. "
                 "Moderate = a balance of growth and safety. Aggressive = chases bigger "
                 "moves, accepts more ups and downs. Pick more than one (e.g. Conservative "
                 "+ Aggressive) to see a SEPARATE shortlist for each — useful for comparing "
                 "how cautious vs. bold the same market looks today.",
        )
        capital_numeric = st.number_input(
            "Capital available (₹)", min_value=0, step=10000,
            value=int(doc_profile.get("capital_numeric") or 0),
        )
        investment_horizon = st.selectbox(
            "Investment horizon", horizon_options,
            index=horizon_options.index(doc_profile.get("investment_horizon")) if doc_profile.get("investment_horizon") in horizon_options else 2,
        )
        sector_preferences = st.multiselect(
            "Preferred sectors (pick as many as you want)", SECTOR_OPTIONS,
            default=[s for s in doc_profile.get("sector_preferences", []) if s in SECTOR_OPTIONS],
        )
        sector_exclusions = st.multiselect(
            "Excluded sectors (pick as many as you want)", SECTOR_OPTIONS,
            default=[s for s in doc_profile.get("sector_exclusions", []) if s in SECTOR_OPTIONS],
        )
        max_position_pct = st.number_input(
            "Max position size per stock (%)", min_value=1, max_value=100,
            value=int(doc_profile.get("max_position_pct") or 15),
            help="The MOST of your capital you'll ever let ONE stock take, no matter "
                 "how good the idea looks. Example: with ₹30,000 capital and a 15% "
                 "limit, even your favourite pick is capped at ₹4,500 — so if that one "
                 "stock goes badly wrong, it can't take down your whole portfolio. "
                 "Lower % = safer, more spread out. Higher % = more concentrated bets.",
        )
        constraints = st.text_area(
            "Other constraints / notes (optional)", value=doc_profile.get("constraints", ""),
        )

        if st.form_submit_button("Save profile", use_container_width=True):
            merged = {
                "risk_appetites": risk_appetites or ["moderate"],
                "capital_available": doc_profile.get("capital_available") or f"₹{capital_numeric:,.0f}",
                "capital_numeric": capital_numeric or None,
                "max_position_pct": max_position_pct,
                "investment_horizon": investment_horizon,
                "sector_preferences": sector_preferences,
                "sector_exclusions": sector_exclusions,
                "constraints": constraints,
            }
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            PROFILE_JSON_PATH.write_text(json.dumps(merged, indent=2))
            write_env({"PROFILE_PATH": str(PROFILE_JSON_PATH)})
            st.sidebar.success("Profile saved — used by the next run.")


# ----------------------------------------------------------------------
# Sidebar — universe/top_n + run trigger
# ----------------------------------------------------------------------
def render_run_controls():
    st.sidebar.header("Scan settings")
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
    st.sidebar.header("Run")
    if st.sidebar.button("▶ Run swing analysis now", use_container_width=True):
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

    st.sidebar.divider()
    st.sidebar.header("Intraday")
    st.sidebar.caption(
        "Mechanical opening-range / VWAP scan, 0 AI calls — only meaningful "
        "while the market is open (09:15–15:30 IST)."
    )
    if st.sidebar.button("⚡ Run intraday scan now", use_container_width=True):
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
        badges = (
            f"{conviction_badge(pick.get('conviction', ''))} "
            f"{action_badge(pick.get('action', ''))} "
            f"{portfolio_fit_badge(pick.get('portfolio_fit', ''))}"
        )
        st.markdown(f"### {pick['symbol']}  &nbsp; {badges}", unsafe_allow_html=True)
        if pick.get("conviction_note"):
            st.caption(pick["conviction_note"])

        cols = st.columns(6)
        cols[0].metric("Opportunity", f"{pick.get('opportunity_score', '?')}/100")
        cols[1].metric("Confidence", f"{pick.get('confidence', '?')}%")
        if pick.get("current_price") is not None:
            cols[2].metric("Current price", f"₹{pick['current_price']:,.2f}")
        perf = pick.get("performance", {})
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


def render_swing_tab():
    runs = list_runs()
    if not runs:
        st.info("No swing analysis runs yet. Click **▶ Run swing analysis now** in the sidebar to generate one.")
        return

    labels = [p.stem.replace("analysis_", "") for p in runs]
    selected = st.selectbox("Run date", labels, index=0, key="swing_run_select")
    data = load_run(runs[labels.index(selected)])

    mkt = data.get("market_context", {})
    if mkt:
        st.caption(
            f"Nifty 50 that day: {mkt.get('nifty_pct_change', 0):+.1f}%, "
            f"trend={mkt.get('nifty_trend', 'unknown')}, "
            f"volatility={mkt.get('nifty_volatility_pct', 0)}% annualised"
        )

    render_glossary()

    # Back-compat: older runs saved a single "result", newer ones save
    # "results_by_tier" (one shortlist per selected risk appetite).
    results_by_tier = data.get("results_by_tier")
    if not results_by_tier:
        legacy = data.get("result")
        results_by_tier = {"moderate": legacy} if legacy else {}

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
            dir_color = {"bullish": COLOR_BLUE, "bearish": COLOR_CRITICAL}.get(p.get("direction"), COLOR_MUTED)
            conv_icon = CONVICTION_ICON.get(conv, "")
            conv_text = f"{conv_icon} {conv} conviction".strip()
            badges = (
                f"{_badge(conv_text, conv_color)} "
                f"{_badge(p.get('direction', '?'), dir_color)}"
            )
            st.markdown(f"### {p['symbol']}  &nbsp; {badges}", unsafe_allow_html=True)
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


def main():
    st.set_page_config(page_title="AI Equity Analyst", page_icon="📈", layout="wide")
    st.title("📈 AI Equity Analyst")
    st.caption(
        "⚠️ Not investment advice — AI-generated analysis for informational purposes "
        "only. Do your own research and consult a SEBI-registered advisor before investing."
    )

    render_profile_builder()
    st.sidebar.divider()
    render_run_controls()

    swing_tab, intraday_tab = st.tabs(["📊 Swing shortlist", "⚡ Intraday scan"])
    with swing_tab:
        render_swing_tab()
    with intraday_tab:
        render_intraday_tab()


if __name__ == "__main__":
    main()
