"""Market structure tracker - higher highs / higher lows (up-trend) and lower highs / lower lows (down-trend), worked out candle by candle.

A SWING HIGH (low) is a candle whose high (low) is the extreme of the `swing` candles on each side of it. It is only known `swing` candles later,
when the candles to its right have closed - so nothing here looks into the future.

From the last two swing highs (sh1 = newest, sh2 = the one before) and the last two swing lows (sl1, sl2), at every candle:
  UP   (trend = +1): higher low (sl1 > sl2) AND higher high (sh1 > sh2, or the close has already broken above sh1), and the higher low still holds (close >= sl1)
  DOWN (trend = -1): lower high (sh1 < sh2) AND lower low (sl1 < sl2, or the close has already broken below sl1 = the old higher low failed),
                     and the lower high still holds (close <= sh1)
  anything else: trend = 0 (range / unclear / structure just broken but not yet confirmed)
So a strategy that only buys demand zones when trend = 1 and only sells supply zones when trend = -1 trades with the structure; the moment the last higher low is
broken after a lower high, the trend flips to -1 by itself (the "market structure shift").

Only swings from the last `max_age` candles count (an older swing says nothing about now). Because of that the answer at a candle depends only on the
last 2 x max_age + 2 x swing candles, so live trading (given that many candles) and a backtest over the whole history agree exactly.

Fields (Series aligned to the candles):
  trend  +1 / 0 / -1 as above
  bias   +1 while the last HIGHER LOW still holds (a pullback with a lower high is still an up-trend until that low breaks), -1 while the last LOWER HIGH
         holds (and the higher low does not), else 0 - the reading for trading pullbacks into zones: it flips to -1 the moment the higher low breaks
  age    candles the current trend value has lasted (capped at max_age) - small = a fresh shift
  sh, sl price of the newest swing high / low still inside `max_age` (NaN = none)
  hh     +1 if the newest swing high is higher than the one before, -1 if lower, 0 if unknown
  hl     +1 if the newest swing low is higher than the one before, -1 if lower, 0 if unknown
  lead   +1 / -1 / 0: which way the structure LEADS, read swing by swing the moment each swing is confirmed (stricter and faster than `bias`, which keeps
         an up-trend alive through a lower-high pullback until the higher low breaks). Replayed over the swings of the last `max_age` candles:
           a higher high -> +1, a lower low -> -1 (once confirmed, see below);
           a LOWER high  -> -1: the up-move failed to make a new high. Counts as a failure only if the high before it was itself a higher high (it was
                            running); after a failed bounce (LL, LH, HL, LH) a further lower high is just a pullback and the +1 stays;
           a HIGHER low  -> +1: the down-move failed to make a new low. Same rule mirrored: a higher low straight after a lower high (HH, HL, LH, HL) does NOT
                            undo the -1 - the highs are still failing - it is a range that has to break.
         So HH, HL, then a lower high reads -1 at once, and LL, LH, then a higher low reads +1 - but only while the swing that protects the move still holds.
         A REVERSAL needs proof: after an up-trend the FIRST lower low does not turn it down - demand zones keep being bought until a candle closes below
         that lower low (or a second lower low forms); after a down-trend the first higher high does not turn it up until a candle closes above it (or a
         second higher high forms). With no history in the window one swing decides, as before.
         A failed high / low (the lower-high / higher-low case) is void again as soon as price closes back through it.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

FIELDS = ["trend", "bias", "age", "sh", "sl", "hh", "hl", "lead"]


def pivots(df: pd.DataFrame, swing: int = 3) -> list[dict]:
    """The swing highs and lows of `df` for drawing, each labelled against the previous swing of its kind: HH / LH (high higher / lower), HL / LL (low higher / lower).
    Same swings `compute` uses (a swing is only listed once `swing` candles after it have closed)."""
    h, l = df["high"].to_numpy(float), df["low"].to_numpy(float)
    k, out = max(1, int(swing)), []
    last_h = last_l = None
    for j in range(k, len(h) - k):
        if h[j] > h[j - k:j].max() and h[j] >= h[j + 1:j + k + 1].max():
            out.append({"i": j, "kind": "high", "price": float(h[j]), "label": "" if last_h is None else "HH" if h[j] > last_h else "LH" if h[j] < last_h else "EH"})
            last_h = h[j]
        if l[j] < l[j - k:j].min() and l[j] <= l[j + 1:j + k + 1].min():
            out.append({"i": j, "kind": "low", "price": float(l[j]), "label": "" if last_l is None else "HL" if l[j] > last_l else "LL" if l[j] < last_l else "EL"})
            last_l = l[j]
    return out


def compute(df: pd.DataFrame, swing: int = 3, max_age: int = 120) -> SimpleNamespace:
    h, l, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
    n = len(c)
    k, max_age = max(1, int(swing)), int(max_age)
    out = {f: np.full(n, np.nan) for f in FIELDS}
    highs: list[tuple[int, float]] = []          # confirmed swing highs (candle index, price), oldest first
    lows: list[tuple[int, float]] = []
    trend = np.zeros(n)
    for i in range(n):
        j = i - k                                # the candle that has just become confirmable
        if j >= k:
            left_h, right_h = h[j - k:j], h[j + 1:i + 1]
            left_l, right_l = l[j - k:j], l[j + 1:i + 1]
            if h[j] > left_h.max() and h[j] >= right_h.max():
                highs.append((j, h[j]))
            if l[j] < left_l.min() and l[j] <= right_l.min():
                lows.append((j, l[j]))
        hv = [p for idx, p in highs[-4:] if i - idx <= max_age]
        lv = [p for idx, p in lows[-4:] if i - idx <= max_age]
        sh1, sh2 = (hv[-1], hv[-2]) if len(hv) >= 2 else (hv[-1] if hv else np.nan, np.nan)
        sl1, sl2 = (lv[-1], lv[-2]) if len(lv) >= 2 else (lv[-1] if lv else np.nan, np.nan)
        out["sh"][i], out["sl"][i] = sh1, sl1
        out["hh"][i] = 0 if np.isnan(sh2) else (1 if sh1 > sh2 else -1 if sh1 < sh2 else 0)
        out["hl"][i] = 0 if np.isnan(sl2) else (1 if sl1 > sl2 else -1 if sl1 < sl2 else 0)
        up = out["hl"][i] == 1 and (out["hh"][i] == 1 or c[i] > sh1) and c[i] >= sl1
        dn = out["hh"][i] == -1 and (out["hl"][i] == -1 or c[i] < sl1) and c[i] <= sh1
        trend[i] = 1 if up and not dn else -1 if dn and not up else 0
        # bias: how a trader reads it while the trend is still ALIVE. An up-trend lasts while the last higher low holds (a pullback that makes a lower high is
        # still an up-trend until that higher low breaks); a down-trend lasts while the last lower high holds. When both hold (a pullback with a higher low AND a
        # lower high), the trend that came BEFORE decides: the high before the latest one was a higher high -> up; the low before the latest one was a lower low -> down.
        # (decided from the NEWEST swing: a new lower low -> down while price stays under the last high; a new higher high -> up while price stays over the last low;
        #  a pullback swing keeps the trend that was running until its own protecting swing breaks)
        newest_high = bool(highs) and bool(lows) and highs[-1][0] > lows[-1][0]
        hh_, hl_ = out["hh"][i], out["hl"][i]
        b = 0
        if not (np.isnan(sh1) or np.isnan(sl1)) and hh_ != 0 and hl_ != 0 and hv and lv:
            up_ctx = len(hv) >= 3 and hv[-2] > hv[-3]                 # the high before the latest one was a higher high: an up-trend was running
            dn_ctx = len(lv) >= 3 and lv[-2] < lv[-3]                 # the low before the latest one was a lower low: a down-trend was running
            if newest_high:
                if hh_ == 1:
                    b = 1 if c[i] >= sl1 else 0
                else:                                                  # a lower high: a pullback inside an up-trend while the higher low holds, else a down-trend
                    b = 1 if (hl_ == 1 and c[i] >= sl1 and not dn_ctx) else -1 if c[i] <= sh1 else 0
            else:
                if hl_ == -1:
                    b = -1 if c[i] <= sh1 else 0
                else:                                                  # a higher low: a bounce inside a down-trend while the lower high holds, else an up-trend
                    b = -1 if (hh_ == -1 and c[i] <= sh1 and not up_ctx) else 1 if c[i] >= sl1 else 0
        out["bias"][i] = b
        # lead: replay the swings of the last `max_age` candles, oldest first (see the docstring)
        swings = sorted([(idx, 0, p) for idx, p in highs[-4:] if i - idx <= max_age] + [(idx, 1, p) for idx, p in lows[-4:] if i - idx <= max_age])
        s, last, lab = 0, [None, None], [0, 0]                        # state; previous price of each kind (0 = high, 1 = low); how that previous swing compared with the one before it
        hhc = llc = 0                                                 # higher highs since the last lower low / lower lows since the last higher high
        hh_px = ll_px = None                                          # the newest of those higher highs / lower lows: the level a close has to break to confirm the shift
        failed = False                                                # s was set by a failed high / low (not yet confirmed by a break)
        for _, kind, p in swings:
            now = 0 if last[kind] is None else (1 if p > last[kind] else -1 if p < last[kind] else 0)
            if kind == 0:
                if now == 1:                                            # a higher high; after a down-move it takes TWO of them (or a close above the first) to call it up; with no history it takes one
                    hhc, llc, hh_px, ll_px = hhc + 1, 0, p, None
                    if s != -1 or hhc >= 2:
                        s, failed = 1, False
                elif now == -1 and s == 1 and lab[0] == 1 and llc == 0:   # the up-move failed to make a new high while the last higher low still holds
                    s, failed = -1, True
            else:
                if now == -1:                                           # a lower low; after an up-move it takes TWO of them (or a close below the first) to call it down
                    llc, hhc, ll_px, hh_px = llc + 1, 0, p, None
                    if s != 1 or llc >= 2:
                        s, failed = -1, False
                elif now == 1 and s == -1 and lab[1] == -1 and hhc == 0:  # the down-move failed to make a new low while the last lower high still holds
                    s, failed = 1, True
            last[kind], lab[kind] = p, now
        if ll_px is not None and c[i] < ll_px and s != -1:              # the recent lower low is broken and a candle closed below it: down
            s, failed = -1, False
        elif hh_px is not None and c[i] > hh_px and s != 1:             # the recent higher high is broken and a candle closed above it: up
            s, failed = 1, False
        if failed and hv and lv:                                        # a failed high / low is void once price closes back through it
            if s == -1 and c[i] > hv[-1]:
                s = 1
            elif s == 1 and c[i] < lv[-1]:
                s = -1
        out["lead"][i] = s
    run = 0
    for i in range(n):                           # how long the current value has lasted (capped, so it too depends only on a bounded window)
        run = min(run + 1, max_age) if i and trend[i] == trend[i - 1] else 1
        out["age"][i] = run
    out["trend"][:] = trend
    return SimpleNamespace(**{f: pd.Series(v, df.index) for f, v in out.items()})
