"""Weak high / weak low tracker: a shooting star (long UPPER wick) makes its high a "weak high", a hammer (long LOWER wick) makes its low a "weak low"; then follow what
price does on its way back to that level, candle by candle. Colour of the setup candle does not matter. The expression language can only look a fixed number of candles back,
so the memory of "the last star and where it is now" lives here and the strategy reads it through fields.

A STAR (mirror for a hammer) is a candle with: the upper wick at least `wick` of its range and not shorter than the lower wick, a body of at most `body` of its range, a range
of at least `min_atr` ATR, and its high within `loc_atr` ATR of the highest high of the last `loc_n` candles (it sits at a top). It arms the level = its high. The setup stays
alive for `max_wait` candles and ends sooner when a candle closes under the star's low (the move it warned of has happened). A newer star at or above the level replaces it,
unless price is already closing beyond the level (a breakout in progress keeps its level).

Fields (hi_* = the weak high above a star; lo_* = the weak low under a hammer; NaN while nothing is armed). Every value is what is known at the CLOSE of that candle:
  hi_lvl     the level (the star's high)
  hi_new     1 on the candle that formed the star
  hi_run     candles since the star (0 = the star itself)
  hi_touch   1 once any candle since the star has traded up to the level (a wick counts)
  hi_closes  candles in a row, ending now, that CLOSED above the level (0 = price is below it or only wicked into it)
  hi_dist    ATRs between the close and the level (positive = still below the level; for lo_*: positive = still above it), rounded to 0.01
Nothing here looks at later candles, so live trading and a backtest read the same values."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from . import indicators as ind

SIDE_FIELDS = ["lvl", "new", "run", "touch", "closes", "dist"]
FIELDS = [f"{side}_{f}" for side in ("hi", "lo") for f in SIDE_FIELDS]
DEFAULTS = {"atr_n": 14, "wick": 0.4, "body": 0.4, "min_atr": 0.3, "loc_n": 10, "loc_atr": 0.25, "max_wait": 6}


def _side(H: np.ndarray, L: np.ndarray, O: np.ndarray, C: np.ndarray, A: np.ndarray, p: dict) -> dict:
    """The weak-high machine on arrays where 'up' is toward the level. (The weak low runs the same code on the mirrored prices.)"""
    n = len(C)
    out = {f: np.full(n, np.nan) for f in SIDE_FIELDS}
    armed = False
    s = 0
    level = s_low = 0.0
    touch = closes = 0
    loc_n, max_wait = int(p["loc_n"]), int(p["max_wait"])
    for i in range(n):
        if armed:                                              # carry the setup through this candle
            if i - s > max_wait:
                armed = False
            else:
                if H[i] >= level:
                    touch = 1
                closes = closes + 1 if C[i] > level else 0
                if C[i] < s_low:                               # closed under the star's low: what the star warned of has happened
                    armed = False
        new = 0
        a = A[i]
        if a == a:                                             # ATR known
            rng = H[i] - L[i]
            if rng > 0 and rng >= p["min_atr"] * a:
                upper, lower, body = H[i] - max(O[i], C[i]), min(O[i], C[i]) - L[i], abs(C[i] - O[i])
                if upper >= p["wick"] * rng and upper >= lower and body <= p["body"] * rng \
                        and H[i] >= H[max(0, i - loc_n + 1):i + 1].max() - p["loc_atr"] * a:
                    if not armed or (H[i] >= level and closes == 0):
                        armed, s, level, s_low, touch, closes, new = True, i, H[i], L[i], 0, 0, 1
        if armed:
            out["lvl"][i], out["new"][i], out["run"][i], out["touch"][i], out["closes"][i] = level, new, i - s, touch, closes
            out["dist"][i] = round((level - C[i]) / a, 2) if a == a and a > 0 else np.nan
        else:
            out["new"][i] = new
    return out


def compute(df: pd.DataFrame, atr_n: int = 14, wick: float = 0.4, body: float = 0.4, min_atr: float = 0.3, loc_n: int = 10, loc_atr: float = 0.25,
            max_wait: int = 6) -> SimpleNamespace:
    p = {"wick": wick, "body": body, "min_atr": min_atr, "loc_n": loc_n, "loc_atr": loc_atr, "max_wait": max_wait}
    O, H, L, C = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    A = ind.atr(df["high"], df["low"], df["close"], int(atr_n)).to_numpy(float)
    hi = _side(H, L, O, C, A, p)
    lo = _side(-L, -H, -O, -C, A, p)                           # the mirror image: a hammer's low is a star's high upside down
    lo["lvl"] = -lo["lvl"]                                     # the level back in real prices
    out = {f"hi_{k}": v for k, v in hi.items()} | {f"lo_{k}": v for k, v in lo.items()}
    return SimpleNamespace(**{f: pd.Series(out[f], df.index) for f in FIELDS})
