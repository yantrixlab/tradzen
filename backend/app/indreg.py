"""Indicator registry used by declarative strategies. Each indicator is computed from a candle DataFrame
(columns t, open, high, low, close, n) and returns a Series (single value) or a namespace of Series (named fields).
Results are cached per (type, params) so several strategies on the same candle share the work.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pandas as pd

from . import indicators as ind
from . import engulf as engulf_mod
from . import structure as structure_mod
from . import weaklevels as weaklevels_mod
from . import zones as zones_mod


def _supertrend(df, n=10, mult=3.0):
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    a = ind.atr(df["high"], df["low"], df["close"], n).to_numpy()
    hl2 = (h + l) / 2
    ub, lb = hl2 + mult * a, hl2 - mult * a
    size = len(c)
    fub, flb, line, direction = (np.full(size, np.nan) for _ in range(4))
    start = int(np.argmax(~np.isnan(a))) if (~np.isnan(a)).any() else size
    for i in range(start, size):
        if i == start:
            fub[i], flb[i], direction[i] = ub[i], lb[i], 1
        else:
            fub[i] = ub[i] if (ub[i] < fub[i - 1] or c[i - 1] > fub[i - 1]) else fub[i - 1]
            flb[i] = lb[i] if (lb[i] > flb[i - 1] or c[i - 1] < flb[i - 1]) else flb[i - 1]
            if direction[i - 1] == -1:
                direction[i] = 1 if c[i] > fub[i] else -1
            else:
                direction[i] = -1 if c[i] < flb[i] else 1
        line[i] = flb[i] if direction[i] == 1 else fub[i]
    idx = df.index
    return SimpleNamespace(line=pd.Series(line, idx), dir=pd.Series(direction, idx))


def _psar(df, step=0.02, maximum=0.2):
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    size = len(h)
    sar, direction = np.full(size, np.nan), np.full(size, np.nan)
    if size < 3:
        return SimpleNamespace(sar=pd.Series(sar, df.index), dir=pd.Series(direction, df.index))
    up = h[1] >= h[0]
    ep, af = (h[0], step) if up else (l[0], step)
    cur = l[0] if up else h[0]
    for i in range(1, size):
        cur = cur + af * (ep - cur)
        if up:
            cur = min(cur, l[i - 1], l[i - 2] if i >= 2 else l[i - 1])
            if l[i] < cur:
                up, cur, ep, af = False, ep, l[i], step
            elif h[i] > ep:
                ep, af = h[i], min(af + step, maximum)
        else:
            cur = max(cur, h[i - 1], h[i - 2] if i >= 2 else h[i - 1])
            if h[i] > cur:
                up, cur, ep, af = True, ep, h[i], step
            elif l[i] < ep:
                ep, af = l[i], min(af + step, maximum)
        sar[i], direction[i] = cur, 1 if up else -1
    return SimpleNamespace(sar=pd.Series(sar, df.index), dir=pd.Series(direction, df.index))


def _heikin(df, tol=1e-9):
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    ha_c = (o + h + l + c) / 4
    ha_o = np.empty_like(ha_c)
    ha_o[0] = (o[0] + c[0]) / 2
    for i in range(1, len(c)):
        ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2
    ha_h, ha_l = np.maximum.reduce([h, ha_o, ha_c]), np.minimum.reduce([l, ha_o, ha_c])
    idx = df.index
    S = lambda x: pd.Series(x, idx)  # noqa: E731
    eps = np.abs(ha_c) * tol
    return SimpleNamespace(o=S(ha_o), h=S(ha_h), l=S(ha_l), c=S(ha_c), green=S(ha_c > ha_o), red=S(ha_c < ha_o),
                           nolow=S((ha_o - ha_l) <= eps), nohigh=S((ha_h - ha_o) <= eps))


def _bollinger(df, n=20, k=2.0):
    lo, mid, up = ind.bollinger(df["close"], n, k)
    bw = (up - lo) / mid
    pct = bw.rolling(100, min_periods=30).apply(lambda w: (w <= w[-1]).mean() * 100, raw=True)
    return SimpleNamespace(lower=lo, mid=mid, upper=up, bw=bw, pctb=(df["close"] - lo) / (up - lo).replace(0, np.nan), pctile=pct)


# type -> (builder(df, **params), default params, fields or None for a plain Series)
REGISTRY = {
    "sma": (lambda df, n: df["close"].rolling(n).mean(), {"n": 20}, None),
    "ema": (lambda df, n: ind.ema(df["close"], n), {"n": 20}, None),
    "rsi": (lambda df, n: ind.rsi(df["close"], n), {"n": 14}, None),
    "atr": (lambda df, n: ind.atr(df["high"], df["low"], df["close"], n), {"n": 14}, None),
    "cci": (lambda df, n: ind.cci(df["high"], df["low"], df["close"], n), {"n": 20}, None),
    "williams": (lambda df, n: -100 * (df["high"].rolling(n).max() - df["close"]) / (df["high"].rolling(n).max() - df["low"].rolling(n).min()).replace(0, np.nan),
                 {"n": 14}, None),
    "roc": (lambda df, n: (df["close"] / df["close"].shift(n) - 1) * 100, {"n": 10}, None),
    "bollinger": (_bollinger, {"n": 20, "k": 2.0}, ["lower", "mid", "upper", "bw", "pctb", "pctile"]),
    "stoch": (lambda df, k, smooth, d: SimpleNamespace(**dict(zip(("k", "d"), ind.stochastic(df["high"], df["low"], df["close"], k, smooth, d)))),
              {"k": 14, "smooth": 3, "d": 3}, ["k", "d"]),
    "macd": (lambda df, fast, slow, signal: SimpleNamespace(**dict(zip(("line", "signal", "hist"), ind.macd(df["close"], fast, slow, signal)))),
             {"fast": 12, "slow": 26, "signal": 9}, ["line", "signal", "hist"]),
    "adx": (lambda df, n: SimpleNamespace(**dict(zip(("adx", "pdi", "mdi"), ind.adx(df["high"], df["low"], df["close"], n)))),
            {"n": 14}, ["adx", "pdi", "mdi"]),
    "supertrend": (_supertrend, {"n": 10, "mult": 3.0}, ["line", "dir"]),
    "psar": (_psar, {"step": 0.02, "maximum": 0.2}, ["sar", "dir"]),
    "heikin": (_heikin, {"tol": 1e-9}, ["o", "h", "l", "c", "green", "red", "nolow", "nohigh"]),
    "donchian": (lambda df, n: SimpleNamespace(upper=df["high"].rolling(n).max(), lower=df["low"].rolling(n).min(),
                                               mid=(df["high"].rolling(n).max() + df["low"].rolling(n).min()) / 2), {"n": 20}, ["upper", "lower", "mid"]),
    # supply/demand zones: found from a tight base + strong departure, then followed (visits, break, role flip, room to the next zone) - see zones.py
    "zones": (zones_mod.compute, {"base_len": 8, "base_atr": 2.5, "move_atr": 1.0, "brk_atr": 0.3, "memory": 3, "max_age": 250, "max_zones": 10, "atr_n": 14},
              zones_mod.FIELDS),
    # market structure: swing highs/lows -> up-trend (HH+HL), down-trend (LH+LL), structure shift - see structure.py
    "structure": (structure_mod.compute, {"swing": 3, "max_age": 120}, structure_mod.FIELDS),
    # weak high / weak low left by a shooting star / hammer and what price does on its way back to it - see weaklevels.py
    "weak": (weaklevels_mod.compute, weaklevels_mod.DEFAULTS, weaklevels_mod.FIELDS),
    # engulfing-candle supply/demand zones, used once, in the direction of the structure - see engulf.py
    "engulf": (engulf_mod.compute, {"swing": 3, "trend_age": 120, "max_age": 120, "min_body": 0.5, "max_run": 3, "visits": 1, "brk_atr": 0.3, "atr_n": 14}, engulf_mod.FIELDS),
}


def fields(type_: str) -> list[str] | None:
    return REGISTRY[type_][2]


def defaults(type_: str) -> dict:
    return dict(REGISTRY[type_][1])


def compute(cache: dict, df: pd.DataFrame, type_: str, params: dict | None = None):
    if type_ not in REGISTRY:
        raise ValueError(f"unknown indicator type '{type_}'")
    builder, dflt, _ = REGISTRY[type_]
    p = {**dflt, **(params or {})}
    key = (type_, json.dumps(p, sort_keys=True), len(df), int(df["t"].iloc[-1]))
    if key not in cache:
        cache[key] = builder(df, **p)
    return cache[key]
