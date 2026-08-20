"""
signals.py  —  pure-math technical/risk indicators
====================================================
Every function here takes real price/volume history (from market_data.py)
and computes a real number with a plain formula. No LLM calls, ever — this
is exactly the "use code to calculate" half of the low-token architecture:
moving averages, RSI, volatility, beta, drawdown, ATR, relative strength.

Everything operates on a pandas Series of daily closes (and sometimes highs/
lows/volume) already fetched via yfinance.
"""

import math

import numpy as np


def pct_change(current: float, previous: float) -> float:
    return round((current - previous) / previous * 100, 2) if previous else 0.0


def sma(close, window: int) -> float:
    return float(close.tail(window).mean())


def rsi(close, window: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).tail(window).mean()
    loss = (-delta.clip(upper=0)).tail(window).mean()
    if not loss:
        return 70.0
    rs = gain / loss
    return round(100 - (100 / (1 + rs)), 1)


def atr(hist, window: int = 14) -> float:
    """Average True Range — typical daily trading range in price terms."""
    high, low, close = hist["High"], hist["Low"], hist["Close"]
    prev_close = close.shift(1)
    tr = np.maximum(high - low, np.maximum((high - prev_close).abs(), (low - prev_close).abs()))
    return round(float(tr.tail(window).mean()), 2)


def daily_returns(close):
    return close.pct_change().dropna()


def historical_volatility(close, window: int = 60) -> float:
    """Annualised volatility (%) from daily returns — 'how violently it usually moves'."""
    rets = daily_returns(close).tail(window)
    if len(rets) < 2:
        return 0.0
    return round(float(rets.std() * math.sqrt(252) * 100), 1)


def downside_volatility(close, window: int = 60) -> float:
    """Same as historical_volatility but only on negative-return days."""
    rets = daily_returns(close).tail(window)
    neg = rets[rets < 0]
    if len(neg) < 2:
        return 0.0
    return round(float(neg.std() * math.sqrt(252) * 100), 1)


def beta(stock_close, index_close, window: int = 120) -> float:
    """How aggressively the stock moves relative to the market (1.0 = same as market)."""
    stock_rets = daily_returns(stock_close).tail(window)
    index_rets = daily_returns(index_close).tail(window)
    n = min(len(stock_rets), len(index_rets))
    if n < 20:
        return 1.0
    s, i = stock_rets.tail(n).values, index_rets.tail(n).values
    var = np.var(i)
    if var == 0:
        return 1.0
    return round(float(np.cov(s, i)[0, 1] / var), 2)


def max_drawdown(close, window: int = None) -> float:
    """Worst peak-to-trough decline (%) over the window (or the whole series)."""
    series = close.tail(window) if window else close
    running_max = series.cummax()
    drawdown = (series - running_max) / running_max
    return round(float(drawdown.min() * 100), 1)


def relative_strength(stock_close, index_close, trading_days: int) -> float:
    """Stock's % return minus the index's % return over the same window."""
    s = stock_close.tail(min(trading_days, len(stock_close)))
    i = index_close.tail(min(trading_days, len(index_close)))
    stock_ret = pct_change(float(s.iloc[-1]), float(s.iloc[0]))
    index_ret = pct_change(float(i.iloc[-1]), float(i.iloc[0]))
    return round(stock_ret - index_ret, 2)


def gap_pct(hist) -> float:
    """Today's overnight gap: today's open vs yesterday's close, as %."""
    if len(hist) < 2:
        return 0.0
    today_open = float(hist["Open"].iloc[-1])
    prev_close = float(hist["Close"].iloc[-2])
    return pct_change(today_open, prev_close)


def volatility_anomaly_z(close, window: int = 60) -> float:
    """
    How many standard deviations today's move is from the stock's own normal
    daily move. A 4% move in a normally-quiet stock scores high; the same 4%
    in a naturally volatile stock scores low.
    """
    rets = daily_returns(close)
    if len(rets) < window + 1:
        return 0.0
    today = rets.iloc[-1]
    history = rets.tail(window + 1).iloc[:-1]
    std = history.std()
    if not std:
        return 0.0
    return round(float(today / std), 2)


def breakout_flags(close, hist) -> dict:
    high20 = float(close.tail(20).max())
    high52 = float(close.max())
    last = float(close.iloc[-1])
    return {
        "breakout_20d": bool(last >= high20 * 0.999),
        "near_52w_high": bool(last >= high52 * 0.95),
    }


def trend_structure(close) -> str:
    """Classify uptrend/recovery/downtrend from price vs 50DMA/200DMA."""
    last = float(close.iloc[-1])
    s50 = sma(close, 50)
    s200 = sma(close, 200) if len(close) >= 200 else sma(close, len(close))
    if last > s50 > s200:
        return "strong_uptrend"
    if last > s50 and last <= s200:
        return "possible_recovery"
    if last < s50 < s200:
        return "downtrend"
    return "sideways"


def historical_pe_context(close, trailing_eps: float, window: int = 252) -> dict:
    """
    Approximate historical P/E range: real historical prices divided by the
    CURRENT trailing EPS (yfinance has no free historical-EPS series, so this
    assumes EPS was roughly stable over the window — an approximation, not
    exact, and only computed when trailing_eps is real and positive).
    """
    if not trailing_eps or trailing_eps <= 0:
        return {}
    series = close.tail(min(window, len(close)))
    implied_pe = series / trailing_eps
    current_pe = float(implied_pe.iloc[-1])
    lo, hi = float(implied_pe.min()), float(implied_pe.max())
    if hi == lo:
        percentile = 50.0
    else:
        percentile = round((current_pe - lo) / (hi - lo) * 100, 1)
    return {
        "current_pe": round(current_pe, 1),
        "historical_low_pe": round(lo, 1),
        "historical_high_pe": round(hi, 1),
        "percentile_in_own_range": percentile,  # 0 = cheapest it's been, 100 = most expensive
    }
