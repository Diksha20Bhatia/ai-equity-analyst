"""
intraday_signals.py  —  pure math on today's 5-minute bars
============================================================
Zero network calls, zero AI. Same "code calculates" philosophy as
data/signals.py, just applied to same-day intraday bars instead of a year
of daily bars: VWAP, the opening range, volume-surge-vs-normal-pace, and
momentum since the open are all textbook mechanical formulas.
"""


def vwap(df):
    """Volume-weighted average price so far today, from real 5-min bars."""
    if df.empty:
        return None
    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    vol = df["Volume"]
    total_vol = float(vol.sum())
    if total_vol <= 0:
        return None
    return float((typical * vol).sum() / total_vol)


def opening_range(df, minutes: int = 15):
    """High/low of the first `minutes` of today's session — the classic
    opening-range-breakout (ORB) reference levels."""
    if df.empty:
        return None, None
    start = df.index[0]
    window = df[df.index <= start + _minutes_delta(minutes)]
    if window.empty:
        window = df.iloc[:1]
    return float(window["High"].max()), float(window["Low"].min())


def _minutes_delta(minutes: int):
    import datetime as dt
    return dt.timedelta(minutes=minutes)


def intraday_momentum_pct(df) -> float:
    """% move from today's open to the latest bar."""
    if df.empty:
        return 0.0
    day_open = float(df["Open"].iloc[0])
    last = float(df["Close"].iloc[-1])
    if day_open == 0:
        return 0.0
    return (last - day_open) / day_open * 100


def volume_surge_ratio(cum_volume: float, avg_daily_volume: float, elapsed_fraction: float):
    """
    How today's volume-so-far compares to a normal day's pace at this same
    point in the session. >1 means trading busier than usual for this time
    of day; <1 means quieter.
    """
    if not avg_daily_volume or elapsed_fraction <= 0:
        return None
    expected_by_now = avg_daily_volume * elapsed_fraction
    if expected_by_now <= 0:
        return None
    return cum_volume / expected_by_now
