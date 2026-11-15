"""Plain pandas/numpy indicators (no TA-Lib dependency)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std(ddof=0)
    return mid - k * sd, mid, mid + k * sd


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0).where(gain.notna())


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k: int = 14, smooth: int = 3, d: int = 3):
    ll = low.rolling(k).min()
    hh = high.rolling(k).max()
    raw = 100 * (close - ll) / (hh - ll).replace(0, np.nan)
    kk = raw.rolling(smooth).mean()
    return kk, kk.rolling(d).mean()


def cci(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 20) -> pd.Series:
    tp = (high + low + close) / 3
    ma = tp.rolling(n).mean()
    md = tp.rolling(n).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - ma) / (0.015 * md.replace(0, np.nan))


def ema(close: pd.Series, n: int) -> pd.Series:
    return close.ewm(span=n, adjust=False, min_periods=n).mean()


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14):
    """Wilder ADX with +DI / -DI."""
    up, dn = high.diff(), -low.diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=high.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=high.index)
    a = atr(high, low, close, n)
    pdi = 100 * plus.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / a
    mdi = 100 * minus.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean(), pdi, mdi


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return line, sig, line - sig


def swing_points(values: np.ndarray, kind: str, left: int = 2, right: int = 2) -> list[int]:
    """Indices of local minima ('low') or maxima ('high'); a swing needs `right` bars after it."""
    out = []
    for i in range(left, len(values) - right):
        w = values[i - left: i + right + 1]
        if np.isnan(w).any():
            continue
        if kind == "low" and values[i] == w.min() and (w == values[i]).sum() == 1:
            out.append(i)
        if kind == "high" and values[i] == w.max() and (w == values[i]).sum() == 1:
            out.append(i)
    return out
