"""Engulfing-candle supply / demand zones - the zones a trader marks by hand on a trending chart, found and followed automatically.

How a zone is born (only from candles that have CLOSED - no hindsight):
  DEMAND  a RED candle is ENGULFED by one green candle, or by a run of up to `max_run` green candles: the run starts at / below the red close and closes above
          the red HIGH, and its size is at least `min_body` ATR. The zone is the full high-low range of the engulfed red candle - where the sellers were absorbed.
  SUPPLY  the mirror: a green candle engulfed by one red candle / a run of red candles; the zone is the green candle's high-low range.
  (the zone is born on the candle that completes the engulf; it is marked from the engulfed candle onward. Zones are marked wherever this happens - the market
   structure (higher highs / lower lows, see structure.py) decides whether a STRATEGY trades them: a demand zone in an up-trend, a supply zone in a down-trend.
   A supply zone from the top of an up-move is therefore already there, waiting, when the trend turns down.)

What happens to it afterwards:
  VISIT   the candles while price trades inside the band are one visit (`visit` = 1 for the first one, `nth` counts its candles: a pullback candle can touch the
          zone and the strong confirming candle can follow inside it). A zone is used up when a visit ENDS and it has had `visits` visits (default 1: it only
          works the first time price comes back, then "the zone will not work").
  BREAK   a close `brk_atr` ATR beyond the far side kills the zone.
  EXPIRY  a zone is forgotten `max_age` candles after it formed.
The answer at a candle depends only on the last max_age + trend_age + 2 x swing + ~230 candles (zone life, the structure behind it and the ATR warm-up),
so live trading (given that many candles) and a backtest over the whole history agree exactly.

Fields (Series aligned to the candles; NaN where nothing applies). dz_* = a DEMAND zone price is trading into on this very candle, sz_* = a SUPPLY zone:
  lo, hi    the band            visit  which visit this candle belongs to (1 = the first time back)   nth   which candle of that visit (1 = the one that touched the zone)
  age       candles since the zone formed
  body      the engulfing move's size in ATRs (how strong the push away was)                      run   how many candles the engulfing move took (1 = one candle)
  room_up   ATRs from the close up to the nearest supply zone still alive above (0 = inside one, 99 = none);  room_dn  the same down to the nearest demand zone
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from . import indicators as ind
from . import structure

NONE_NEAR = 99.0
FIELDS = ["dz_lo", "dz_hi", "dz_visit", "dz_nth", "dz_age", "dz_body", "dz_run", "sz_lo", "sz_hi", "sz_visit", "sz_nth", "sz_age", "sz_body", "sz_run", "room_up", "room_dn"]


def compute(df: pd.DataFrame, swing: int = 3, trend_age: int = 120, max_age: int = 120, min_body: float = 0.5, max_run: int = 3, visits: int = 1,
            brk_atr: float = 0.3, atr_n: int = 14, log: list | None = None) -> SimpleNamespace:
    """`log` (optional list): one record per zone life, for drawing; it changes nothing about the result."""
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    n = len(c)
    a = ind.atr(df["high"], df["low"], df["close"], int(atr_n)).to_numpy(float)
    trend = structure.compute(df, swing=swing, max_age=trend_age).trend.to_numpy(float)
    max_age, max_run, visits = int(max_age), max(1, int(max_run)), max(1, int(visits))
    out = {k: np.full(n, np.nan) for k in FIELDS}
    out["room_up"][:] = NONE_NEAR
    out["room_dn"][:] = NONE_NEAR
    # zone = dict: kind (+1 demand / -1 supply), lo, hi, born, visits, inside, body, run, rec
    zones: list[dict] = []

    def finish(z, i, why):
        if z["rec"] is not None:
            z["rec"]["end"], z["rec"]["why"] = i, why

    for i in range(n):
        ai = a[i]
        if np.isnan(ai) or ai <= 0:
            continue
        # 1) what did this candle do to the zones already known?
        touched = []
        for z in zones[:]:
            kind, lo, hi = z["kind"], z["lo"], z["hi"]
            if (kind == 1 and c[i] < lo - brk_atr * ai) or (kind == -1 and c[i] > hi + brk_atr * ai):
                zones.remove(z)
                finish(z, i, "broken")
                continue
            if i - z["born"] > max_age:
                zones.remove(z)
                finish(z, i, "expired")
                continue
            if (l[i] <= hi) if kind == 1 else (h[i] >= lo):
                if not z["inside"]:
                    z["visits"] += 1                                                  # price came back into the zone: a new visit starts
                    z["nth"] = 0
                    if z["rec"] is not None:
                        z["rec"]["touches"].append(i)
                z["inside"] = True
                z["nth"] += 1
                z["last"] = i
                touched.append(z)
            else:
                if z["inside"] and z["visits"] >= visits:                             # the visit is over and the zone has had all its visits: it no longer works
                    zones.remove(z)
                    finish(z, z["last"], "used")
                    continue
                z["inside"] = False
        # 2) what a strategy may look at: the newest zone of each kind that this candle trades into. EVERY candle of a visit answers (a pullback candle can touch the
        #    zone and the strong confirming candle can follow inside it); `nth` counts the candles of the visit
        for kind, pre in ((1, "dz"), (-1, "sz")):
            ts = [z for z in touched if z["kind"] == kind]
            if ts:
                z = max(ts, key=lambda z: z["born"])
                out[pre + "_lo"][i], out[pre + "_hi"][i], out[pre + "_visit"][i], out[pre + "_nth"][i] = z["lo"], z["hi"], z["visits"], z["nth"]
                out[pre + "_age"][i], out[pre + "_body"][i], out[pre + "_run"][i] = i - z["born"], z["body"], z["run"]
        # 3) a zone born on this candle: a candle (or short run) engulfs the opposite candle before it. Zones are marked wherever that happens; the structure
        #    trend decides whether a strategy trades them (a supply zone from the top of an up-move is ready when the trend turns down).
        body = c[i] - o[i]
        if i >= 1 and abs(body) > 0.5 * (h[i] - l[i]):
            kind = 1 if body > 0 else -1
            same = (lambda j: c[j] > o[j]) if kind == 1 else (lambda j: c[j] < o[j])          # a candle of the engulfing colour
            m = 1
            while m < max_run and i - m >= 1 and same(i - m):                                 # the engulfing run: this candle and the candles of its colour right before it
                m += 1
            j = i - m                                                                          # the candle before the run: the one being engulfed
            if j >= 0 and not same(j) and c[j] != o[j]:
                first = i - m + 1
                run_end_before = c[first:i].max() if kind == 1 and m > 1 else c[first:i].min() if m > 1 else None
                beyond = (c[i] > h[j]) if kind == 1 else (c[i] < l[j])                       # the run closes entirely beyond the engulfed candle
                starts_at = (o[first] <= c[j] + 0.1 * ai) if kind == 1 else (o[first] >= c[j] - 0.1 * ai)
                first_completion = m == 1 or ((run_end_before <= h[j]) if kind == 1 else (run_end_before >= l[j]))       # the candle that completes the engulf, not a later one
                strong = abs(c[i] - o[first]) >= min_body * ai
                if beyond and starts_at and first_completion and strong:
                    lo, hi = float(l[j]), float(h[j])                                          # the zone: the full high-low range of the ENGULFED candle
                    rec = None
                    if log is not None:
                        rec = {"kind": "demand" if kind == 1 else "supply", "lo": lo, "hi": hi, "start": j, "base": [j, j], "dep": [first, i], "end": None,
                               "why": None, "move": round(abs(c[i] - o[first]) / ai, 2), "touches": [], "run": m, "with_trend": bool(trend[i] == kind)}
                        log.append(rec)
                    zones.append({"kind": kind, "lo": lo, "hi": hi, "born": i, "visits": 0, "inside": False, "nth": 0, "last": -1, "body": abs(c[i] - o[first]) / ai, "run": m, "rec": rec})
        # 5) room to the nearest opposing zone that is still alive
        up = [(0.0 if z["lo"] <= c[i] <= z["hi"] else (z["lo"] - c[i]) / ai) for z in zones if z["kind"] == -1 and z["hi"] >= c[i]]
        dn = [(0.0 if z["lo"] <= c[i] <= z["hi"] else (c[i] - z["hi"]) / ai) for z in zones if z["kind"] == 1 and z["lo"] <= c[i]]
        if up:
            out["room_up"][i] = min(up)
        if dn:
            out["room_dn"][i] = min(dn)
    return SimpleNamespace(**{k: pd.Series(v, df.index) for k, v in out.items()})


def lifecycle(df: pd.DataFrame, **params) -> list[dict]:
    """Every zone life in `df`, for drawing: kind, band, the engulfed candles (`base`), the engulfing candle (`dep`), where it ended (`end`, None = alive; `why`:
    used / broken / expired), the candle of each visit (`touches`), the engulfing candle's size in ATRs (`move`) and how many candles were engulfed (`run`)."""
    log: list[dict] = []
    compute(df, log=log, **params)
    return log
