"""Weak high / weak low tracker: a shooting star (long UPPER wick) makes its high a "weak high", a hammer (long LOWER wick) makes its low a "weak low"; then follow what
price does on its way back to that level, candle by candle. Colour of the setup candle does not matter. The expression language can only look a fixed number of candles back,
so the memory of "the last star and where it is now" lives here and the strategy reads it through fields.

A STAR (mirror for a hammer) is a PIN BAR, nothing looser: the upper wick is at least `wick_x` times the body (default 2, "more than double"), at least `wick` of the range, and the
lower wick is at most `opp` of the upper wick (default half, so the other side is clearly shorter); the body is between `min_body` (default 1 % of the range: only a perfectly flat
candle is left out) and `body` (40 %) of the range. A tiny body with ONE long wick (a gravestone-style star) counts; a doji with long wicks on both sides never passes `opp`; the range is at least `min_atr` ATR. WHERE it forms does not matter by default (`loc_atr` 1000 = no location rule: a star on a pullback inside a down-swing counts just like one
at a top); set `loc_atr` small (0.25) to demand its high be within that many ATR of the highest high of the last `loc_n` candles. It arms the level = its high. The setup stays
alive for `max_wait` candles and ends sooner when the FIRST candle back (red after a green run) closes under the star's low coming from above it, or when the second candle back has printed (the move it
warned of has happened). A candle before the run started, or price that was already under the star's low, does not end it: a weak level stays a weak level. A newer star at or above the level replaces it,
unless price is already closing beyond the level (a breakout in progress keeps its level); a newer star of any height replaces an older one whose far end (its low) price has already
closed through.

Fields (hi_* = the weak high above a star; lo_* = the weak low under a hammer; NaN while nothing is armed). Every value is what is known at the CLOSE of that candle:
  hi_lvl     the level (the star's high)
  hi_new     1 on the candle that formed the star
  hi_run     candles since the star (0 = the star itself)
  hi_touch   1 once any candle since the star has traded up to the level (a wick counts)
  hi_closes  candles in a row, ending now, that CLOSED above the level (0 = price is below it or only wicked into it)
  hi_broke   1 once ANY candle since the star has closed above the level (it stays 1 even after price falls back under it: "the level was broken")
  hi_back    candles in a row, ending now, of the OPPOSITE colour to the run (red after a star), counted only after the level was broken: 1 = the first candle
             back, 2 = the second ... 0 = none yet or the run resumed
  hi_ran     1 once any candle of the run colour (green after a star) has printed since the star
  hi_ret     candles in a row, ending now, of the OPPOSITE colour (red after a star), counted once the run has started, broken level or not: 1 = the first candle
             back, 2 = the second. A flat candle (open = close) breaks the chain
  hi_ext     the far end of the star = its LOW (for lo_*: the hammer's HIGH). A close beyond it is a decisive move back
  hi_dist    ATRs between the close and the level (positive = still below the level; for lo_*: positive = still above it), rounded to 0.01
Nothing here looks at later candles, so live trading and a backtest read the same values."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from . import indicators as ind

SIDE_FIELDS = ["lvl", "new", "run", "touch", "closes", "broke", "back", "dist", "ran", "ret", "ext"]
FIELDS = [f"{side}_{f}" for side in ("hi", "lo") for f in SIDE_FIELDS]
DEFAULTS = {"atr_n": 14, "wick": 0.4, "body": 0.4, "min_atr": 0.3, "loc_n": 10, "loc_atr": 1000.0, "max_wait": 10, "wick_x": 2.0, "opp": 0.5, "min_body": 0.01}


def _side(H: np.ndarray, L: np.ndarray, O: np.ndarray, C: np.ndarray, A: np.ndarray, p: dict) -> dict:
    """The weak-high machine on arrays where 'up' is toward the level. (The weak low runs the same code on the mirrored prices.)"""
    n = len(C)
    out = {f: np.full(n, np.nan) for f in SIDE_FIELDS}
    armed = False
    s = 0
    level = s_low = 0.0
    touch = closes = broke = back = ran = ret = past = 0
    loc_n, max_wait = int(p["loc_n"]), int(p["max_wait"])
    for i in range(n):
        if armed:                                              # carry the setup through this candle
            if i - s > max_wait:
                armed = False
            else:
                if H[i] >= level:
                    touch = 1
                if broke:                                      # after the break: count the candles that come back against the run
                    back = back + 1 if C[i] < O[i] else 0
                closes = closes + 1 if C[i] > level else 0
                if closes:
                    broke = 1
                if C[i] < s_low:
                    past = 1                                   # price has already closed through the star's far end
                if C[i] > O[i]:                                # a candle of the run colour
                    ran, ret = 1, 0
                elif C[i] < O[i] and ran:                      # a candle back, after the run has started
                    ret += 1
                else:
                    ret = 0
                if ret >= 2 or (ret == 1 and i > 0 and C[i] < s_low <= C[i - 1]):   # the second candle back has printed, or the first one just CROSSED the star's low: the move it warned of has happened
                    armed = False
        new = 0
        a = A[i]
        if a == a:                                             # ATR known
            rng = H[i] - L[i]
            if rng > 0 and rng >= p["min_atr"] * a:
                upper, lower, body = H[i] - max(O[i], C[i]), min(O[i], C[i]) - L[i], abs(C[i] - O[i])
                if upper >= p["wick"] * rng and upper >= p["wick_x"] * body and lower <= p["opp"] * upper \
                        and p["min_body"] * rng <= body <= p["body"] * rng \
                        and H[i] >= H[max(0, i - loc_n + 1):i + 1].max() - p["loc_atr"] * a:
                    if not armed or (H[i] >= level and closes == 0) or past:
                        armed, s, level, s_low, touch, closes, broke, back, new, ran, ret, past = True, i, H[i], L[i], 0, 0, 0, 0, 1, 0, 0, 0
        if armed:
            out["lvl"][i], out["new"][i], out["run"][i], out["touch"][i], out["closes"][i] = level, new, i - s, touch, closes
            out["broke"][i], out["back"][i] = broke, back
            out["ran"][i], out["ret"][i], out["ext"][i] = ran, ret, s_low
            out["dist"][i] = round((level - C[i]) / a, 2) if a == a and a > 0 else np.nan
        else:
            out["new"][i] = new
    return out


def compute(df: pd.DataFrame, atr_n: int = 14, wick: float = 0.4, body: float = 0.4, min_atr: float = 0.3, loc_n: int = 10, loc_atr: float = 1000.0,
            max_wait: int = 10, wick_x: float = 2.0, opp: float = 0.5, min_body: float = 0.01) -> SimpleNamespace:
    p = {"wick": wick, "body": body, "min_atr": min_atr, "loc_n": loc_n, "loc_atr": loc_atr, "max_wait": max_wait, "wick_x": wick_x, "opp": opp, "min_body": min_body}
    O, H, L, C = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    A = ind.atr(df["high"], df["low"], df["close"], int(atr_n)).to_numpy(float)
    hi = _side(H, L, O, C, A, p)
    lo = _side(-L, -H, -O, -C, A, p)                           # the mirror image: a hammer's low is a star's high upside down
    lo["lvl"] = -lo["lvl"]                                     # the level back in real prices
    lo["ext"] = -lo["ext"]
    out = {f"hi_{k}": v for k, v in hi.items()} | {f"lo_{k}": v for k, v in lo.items()}
    return SimpleNamespace(**{f: pd.Series(out[f], df.index) for f in FIELDS})
