"""
intraday_data.py  —  real same-day price/volume data for the intraday scanner
================================================================================
Uses yfinance's 5-minute bars for the CURRENT trading session (or the most
recently completed one if fetched after hours). No synthetic bars, no
simulated ticks — if Yahoo has no intraday bars for a symbol today
(illiquid stock, API hiccup, market not yet open), that symbol is skipped
and logged, the same data-quality-gate pattern as data/market_data.py.

NSE cash session is 09:15-15:30 IST (375 minutes) — used to work out how far
through the day we are, so volume can be compared to a normal day's pace at
the SAME point in the session rather than to a full day's total.

Objective data-quality checks (stale bars, zero volume, a single bar moving
>10% with no volume behind it) are done here in plain code — no ambiguity,
no AI needed. A stock with an unusually large, otherwise-clean overnight gap
is instead queued for agents/intraday_quality_agent.py's batched judgment
call, since "is this gap a real corporate action or a bad print" genuinely
needs a read of the pattern, not a fixed threshold.
"""

from data import intraday_signals as isig

_SESSION_MINUTES = 375  # 09:15 - 15:30 IST

# A gap this large vs the real previous close is unusual enough for a
# liquid NSE name that it's worth a second look before scoring off it —
# either real news, or (rarely) a corporate action / bad print. Purely
# mechanical checks below catch the "bad print" case with certainty;
# anything past this threshold that ISN'T caught mechanically gets queued
# for the batched Data Sentinel judgment call instead of guessed at here.
_UNUSUAL_GAP_PCT = 8.0


def _mechanical_quality_flags(df) -> list:
    """
    100% deterministic checks — no ambiguity, so no AI needed. Anything
    flagged here is objectively bad data, not a judgment call.
    """
    flags = []
    closes = df["Close"]
    run = 1
    for i in range(1, len(closes)):
        if closes.iloc[i] == closes.iloc[i - 1]:
            run += 1
            if run >= 3:
                flags.append("stale bars — no price movement for 3+ consecutive 5-min bars")
                break
        else:
            run = 1

    if (df["Volume"] == 0).any():
        flags.append("one or more bars with zero volume")

    avg_vol = float(df["Volume"].mean()) or 1.0
    for _, bar in df.iterrows():
        if bar["Open"] and abs(bar["Close"] - bar["Open"]) / bar["Open"] > 0.10 and bar["Volume"] < avg_vol * 0.1:
            flags.append("a single 5-min bar moved >10% on almost no volume — likely a bad print")
            break

    return flags


def _fetch_intraday_bundle(symbol: str) -> dict:
    import yfinance as yf

    ticker = yf.Ticker(f"{symbol}.NS")
    df = ticker.history(period="1d", interval="5m")
    if df.empty:
        # Market may be closed with no bars cached under "1d" — fall back to
        # the most recently completed session within the last few days.
        df = ticker.history(period="5d", interval="5m")
        if df.empty:
            raise RuntimeError(f"no intraday bars for {symbol}.NS")
        last_day = df.index[-1].date()
        df = df[df.index.date == last_day]

    day_open = float(df["Open"].iloc[0])
    day_high = float(df["High"].max())
    day_low = float(df["Low"].min())
    last_price = float(df["Close"].iloc[-1])
    cum_volume = float(df["Volume"].sum())

    or_high, or_low = isig.opening_range(df, minutes=15)
    vwap_val = isig.vwap(df)
    momentum_pct = isig.intraday_momentum_pct(df)

    elapsed_minutes = (df.index[-1] - df.index[0]).total_seconds() / 60 + 5
    elapsed_fraction = min(elapsed_minutes / _SESSION_MINUTES, 1.0)

    info = ticker.info
    avg_daily_volume = info.get("averageVolume")
    surge_ratio = isig.volume_surge_ratio(cum_volume, avg_daily_volume, elapsed_fraction)

    prev_close = info.get("previousClose")
    gap_pct = round((day_open - prev_close) / prev_close * 100, 2) if prev_close else None

    mechanical_flags = _mechanical_quality_flags(df)
    unusual_gap = gap_pct is not None and abs(gap_pct) >= _UNUSUAL_GAP_PCT and not mechanical_flags

    return {
        "symbol": symbol,
        "as_of": df.index[-1].isoformat(),
        "prev_close": round(prev_close, 2) if prev_close else None,
        "gap_pct": gap_pct,
        "day_open": round(day_open, 2),
        "day_high": round(day_high, 2),
        "day_low": round(day_low, 2),
        "last_price": round(last_price, 2),
        "momentum_pct": round(momentum_pct, 2),
        "vwap": round(vwap_val, 2) if vwap_val else None,
        "above_vwap": bool(vwap_val and last_price > vwap_val),
        "opening_range_high": round(or_high, 2) if or_high else None,
        "opening_range_low": round(or_low, 2) if or_low else None,
        "broke_range_high": bool(or_high and last_price > or_high),
        "broke_range_low": bool(or_low and last_price < or_low),
        "volume_surge_ratio": round(surge_ratio, 1) if surge_ratio else None,
        "session_bars": len(df),
        # Mechanical (code-certain) quality flags — a non-empty list here
        # means EXCLUDE this stock from scoring, no AI judgment needed.
        "mechanical_flags": mechanical_flags,
        # A real but unexplained gap that passed the mechanical checks —
        # queued for the batched Data Sentinel call to weigh in on.
        "needs_gap_review": unusual_gap,
        # Kept transiently for the narrator/pattern agent (top-N stocks
        # only) — stripped before saving output JSON, same pattern as
        # market_data.py's close_series.
        "bars": df[["Open", "High", "Low", "Close", "Volume"]],
    }


def bars_to_records(df) -> list:
    """Compact {time, open, high, low, close, volume} records for a bundle's
    transient 'bars' DataFrame — used by the narrator/pattern agent, which
    reads the real bar-by-bar shape for its top-N picks only."""
    return [
        {
            "time": ts.strftime("%H:%M"),
            "open": float(row["Open"]), "high": float(row["High"]),
            "low": float(row["Low"]), "close": float(row["Close"]),
            "volume": float(row["Volume"]),
        }
        for ts, row in df.iterrows()
    ]


def get_intraday_bundles(symbols: list) -> dict:
    """
    Return {symbol: intraday_bundle} for every symbol real 5-minute bars
    could be fetched for today. A bad ticker or a transient Yahoo failure is
    skipped and logged — resilient to one bad symbol, same as
    market_data.get_bundles().
    """
    out = {}
    for sym in symbols:
        try:
            out[sym] = _fetch_intraday_bundle(sym)
        except Exception as e:  # noqa: BLE001
            print(f"[intraday] skipping {sym}: {e}")
    return out
