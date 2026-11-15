"""Supply / demand zone tracker - the zones a trader draws as rectangles, found and followed automatically.

How a zone is born (only from candles that have CLOSED, so it is known at the moment it forms - no hindsight):
  * BASE: the last `base_len` candles trade in a tight band (their high-low range is at most `base_atr` x ATR);
  * DEPARTURE: a strong candle then closes clear of that band - up by at least `move_atr` x ATR = DEMAND zone, down = SUPPLY zone.
    (the departure may be the next candle or the one after). The zone is the base's high-low band.

What happens to a zone afterwards (this is what makes it strong or weak):
  * TOUCH: price trades back into the band. Each separate visit counts once (`tests`): a zone on its 1st-2nd visit is fresh/strong, a 4th visit is weak.
  * BREAK: a candle closes `brk_atr` x ATR beyond the far side. A broken demand zone is gone as support and becomes a SUPPLY zone on the same band
    (and a broken supply zone becomes demand): the role flip you drew, with `flip` = 1 on the new zone.
  * zones expire `max_age` candles after their base formed (a flipped zone keeps its original birth); at each candle the `max_zones` newest zones are the ones considered.
    Nothing is ever suppressed or evicted, so a zone's life depends only on the candles since its own birth.
    Because of that, what the tracker says at a candle depends only on the last `max_age` + `base_len` + ~230 (ATR warm-up) candles, so live trading
    (given that many candles) and a backtest over the whole history give exactly the same answer.

Fields (all Series aligned to the candles; NaN where nothing applies):
  dz_* / sz_*   the demand / supply zone price touched within the last `memory` candles (the newest touch wins):
                lo, hi (the band), tests (visits so far, this one included), age (candles since it formed), flip (1 = it used to be the opposite kind),
                since (candles since the last touch; 0 = touching now), move (size of its departure in ATRs - how explosive the zone was)
  tight, dep, run   how STRONG the zone is, from the candles alone (this feed has no usable volume: ticks are ~80 on every candle whatever the move):
                tight = the base's high-low range in ATRs (small = a compressed base); dep = body / range of the departure's best candle (1 = decisive, no wicks);
                run = the furthest price has travelled away from the zone since it formed, in ATRs, up to the candle before this one (how far the move really went)
  room_up       distance in ATRs from the close up to the nearest supply zone above (0 = inside one, 99 = none)
  room_dn       distance in ATRs from the close down to the nearest demand zone below (0 = inside one, 99 = none)
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from . import indicators as ind

NONE_NEAR = 99.0
FIELDS = ["dz_lo", "dz_hi", "dz_tests", "dz_age", "dz_flip", "dz_since", "dz_move", "dz_tight", "dz_dep", "dz_run",
          "sz_lo", "sz_hi", "sz_tests", "sz_age", "sz_flip", "sz_since", "sz_move", "sz_tight", "sz_dep", "sz_run", "room_up", "room_dn"]


def _shift(a: np.ndarray, k: int) -> np.ndarray:
    out = np.full(len(a), np.nan)
    if k < len(a):
        out[k:] = a[: len(a) - k]
    return out


def _departures(o, h, l, c, a, base_len: int, base_atr: float, move_atr: float):
    """Per candle i: the base band + departure size of a zone that is born when candle i closes (NaN = none). Vectorised."""
    hi_w = pd.Series(h).rolling(base_len).max().to_numpy()
    lo_w = pd.Series(l).rolling(base_len).min().to_numpy()
    tight = ((hi_w - lo_w) <= base_atr * a).astype(float)
    tight[np.isnan(hi_w) | np.isnan(a)] = np.nan
    n = len(c)
    d_lo, d_hi, d_mv = (np.full(n, np.nan) for _ in range(3))      # demand: band low, band high, departure
    s_lo, s_hi, s_mv = (np.full(n, np.nan) for _ in range(3))
    d_d, s_d = np.full(n, np.nan), np.full(n, np.nan)               # candles between the end of the base and the departure candle (for drawing the base)
    for d in (2, 1):                                                # d = 1 overwrites d = 2: the cleanest (immediate) departure wins
        hj, lj, tj = _shift(hi_w, d), _shift(lo_w, d), _shift(tight, d) == 1
        with np.errstate(invalid="ignore"):
            up = tj & (c >= hj + move_atr * a) & (c > o)
            dn = tj & (c <= lj - move_atr * a) & (c < o)
            if d == 2:                                              # the candle in between must already have left the band, but not by a full departure
                c1, a1 = _shift(c, 1), _shift(a, 1)
                up &= (c1 > hj) & (c1 < hj + move_atr * a1)
                dn &= (c1 < lj) & (c1 > lj - move_atr * a1)
        d_lo[up], d_hi[up], d_mv[up] = lj[up], hj[up], ((c - hj) / a)[up]
        s_lo[dn], s_hi[dn], s_mv[dn] = lj[dn], hj[dn], ((lj - c) / a)[dn]
        d_d[up], s_d[dn] = d, d
    return d_lo, d_hi, d_mv, s_lo, s_hi, s_mv, d_d, s_d


def compute(df: pd.DataFrame, base_len: int = 8, base_atr: float = 2.5, move_atr: float = 1.0, brk_atr: float = 0.3, memory: int = 3,
            max_age: int = 250, max_zones: int = 10, atr_n: int = 14, log: list | None = None) -> SimpleNamespace:
    """`log` (optional list): filled with one record per zone life (for drawing); it changes nothing about the result."""
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    n = len(c)
    a = ind.atr(df["high"], df["low"], df["close"], int(atr_n)).to_numpy(float)
    base_len, memory, max_age, max_zones = int(base_len), int(memory), int(max_age), int(max_zones)
    d_lo, d_hi, d_mv, s_lo, s_hi, s_mv, d_dl, s_dl = _departures(o, h, l, c, a, base_len, base_atr, move_atr)

    out = {k: np.full(n, np.nan) for k in FIELDS}
    out["room_up"][:] = NONE_NEAR
    out["room_dn"][:] = NONE_NEAR

    def record(kind, lo, hi, start, base, dep, flip, move):
        if log is None:
            return None
        rec = {"kind": "demand" if kind == 1 else "supply", "lo": float(lo), "hi": float(hi), "start": int(start), "end": None, "broken": False, "flip": bool(flip),
               "base": base, "dep": dep, "move": round(float(move), 2), "tests": 0, "touches": []}
        log.append(rec)
        return rec

    def dep_body(i, dl):
        """How decisive the departure was: the biggest body / range ratio among its candles (1 = a full-bodied candle with no wicks)."""
        best = 0.0
        for j in range(i - dl + 1, i + 1):
            rng = h[j] - l[j]
            best = max(best, abs(c[j] - o[j]) / rng if rng > 0 else 0.0)
        return best

    # zone = [kind (+1 demand / -1 supply), lo, hi, tests, inside, born, flip, last_touch, move, log record, tight (base range / ATR), dep (departure body ratio),
    #         ext (the furthest price has run away from the zone, in price units, over the candles BEFORE the current one)]
    zones: list[list] = []
    for i in range(n):
        ai = a[i]
        if np.isnan(ai) or ai <= 0:
            continue
        # 1) what did this candle do to the zones already known?
        flipped = []
        for z in zones[:]:
            kind, lo, hi = z[0], z[1], z[2]
            if (kind == 1 and c[i] < lo - brk_atr * ai) or (kind == -1 and c[i] > hi + brk_atr * ai):          # broken: the zone changes role
                zones.remove(z)
                if z[9] is not None:
                    z[9]["end"], z[9]["broken"] = i, True
                flipped.append([-kind, lo, hi, 0, False, z[5], 1, -10 ** 9, z[8], record(-kind, lo, hi, i, None, None, True, z[8]), z[10], z[11], 0.0])   # keeps the ORIGINAL birth: every zone dies `max_age` after
                continue                                                                  # its base formed, so nothing here depends on candles older than that (live = backtest)
            touch = l[i] <= hi if kind == 1 else h[i] >= lo
            if touch:
                if not z[4]:
                    z[3] += 1                                                                                   # a new visit
                    if z[9] is not None:
                        z[9]["tests"] = z[3]
                        z[9]["touches"].append(i)
                z[4], z[7] = True, i
            else:
                z[4] = False
        zones.extend(flipped)
        # 2) a zone born on this candle (price has not visited it yet)
        if not np.isnan(d_lo[i]):
            dl = int(d_dl[i])
            zones.append([1, d_lo[i], d_hi[i], 0, False, i, 0, -10 ** 9, d_mv[i],
                          record(1, d_lo[i], d_hi[i], i - dl - base_len + 1, [i - dl - base_len + 1, i - dl], [i - dl + 1, i], False, d_mv[i]),
                          (d_hi[i] - d_lo[i]) / ai, dep_body(i, dl), 0.0])
        if not np.isnan(s_lo[i]):
            dl = int(s_dl[i])
            zones.append([-1, s_lo[i], s_hi[i], 0, False, i, 0, -10 ** 9, s_mv[i],
                          record(-1, s_lo[i], s_hi[i], i - dl - base_len + 1, [i - dl - base_len + 1, i - dl], [i - dl + 1, i], False, s_mv[i]),
                          (s_hi[i] - s_lo[i]) / ai, dep_body(i, dl), 0.0])
        for z in zones:
            if i - z[5] > max_age and z[9] is not None:
                z[9]["end"] = i
        zones[:] = [z for z in zones if i - z[5] <= max_age]
        # 3) what a strategy may look at on this candle: the `max_zones` newest zones (chosen now, never deleted, so a zone's life depends only on
        #    the candles since its own birth)
        live = sorted(zones, key=lambda z: z[5])[-max_zones:] if len(zones) > max_zones else zones
        for kind, pre in ((1, "dz"), (-1, "sz")):
            ctx = [z for z in live if z[0] == kind and i - z[7] <= memory]
            if ctx:
                z = max(ctx, key=lambda z: (z[7], -z[3]))
                out[pre + "_lo"][i], out[pre + "_hi"][i], out[pre + "_tests"][i] = z[1], z[2], z[3]
                out[pre + "_age"][i], out[pre + "_flip"][i], out[pre + "_since"][i], out[pre + "_move"][i] = i - z[5], z[6], i - z[7], z[8]
                out[pre + "_tight"][i], out[pre + "_dep"][i], out[pre + "_run"][i] = z[10], z[11], z[12] / ai
        up = [(0.0 if z[1] <= c[i] <= z[2] else (z[1] - c[i]) / ai) for z in live if z[0] == -1 and z[2] >= c[i]]
        dn = [(0.0 if z[1] <= c[i] <= z[2] else (c[i] - z[2]) / ai) for z in live if z[0] == 1 and z[1] <= c[i]]
        if up:
            out["room_up"][i] = min(up)
        if dn:
            out["room_dn"][i] = min(dn)
        for z in zones:                                                  # AFTER this candle's answer: how far has price run away from each zone, up to and including this candle
            z[12] = max(z[12], (h[i] - z[2]) if z[0] == 1 else (z[1] - l[i]))
    return SimpleNamespace(**{k: pd.Series(v, df.index) for k, v in out.items()})


def lifecycle(df: pd.DataFrame, **params) -> list[dict]:
    """Every zone that existed in `df`, for drawing: kind, band (lo/hi), first candle (start, the base's first candle for a fresh zone), end (None = still alive),
    the base candles [first, last], the departure candles, visits (`touches` = the candles where a new visit began), strength (`move` in ATRs) and flip/broken flags."""
    log: list[dict] = []
    compute(df, log=log, **params)
    return log
