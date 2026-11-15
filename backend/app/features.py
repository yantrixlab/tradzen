"""Context snapshot stored with every issued signal, so losses can be explained later (and so the same table can feed ML).

compute(rows, ctx) is pure: rows = chronological closed candles [{t,o,h,l,c,n}], ctx = facts only the engine knows
(direction, payout, sentiment, ...). Values are plain JSON numbers (None when an indicator is still warming up).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import indicators as ind


def _f(x, nd: int = 6):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else round(x, nd)


def session_of(hour_utc: int) -> str:
    if hour_utc < 7:
        return "asia"
    if hour_utc < 13:
        return "europe"
    if hour_utc < 16:
        return "eu_us_overlap"
    if hour_utc < 21:
        return "us"
    return "late"


def compute(rows: list[dict], ctx: dict) -> dict:
    return finalize(base_features(rows), ctx)


def finalize(base: dict, ctx: dict) -> dict:
    """Merge the direction-independent snapshot with the facts of one specific signal."""
    out = dict(base)
    out.update(ctx)
    d_sign = 1 if ctx.get("direction") == "CALL" else -1 if ctx.get("direction") == "PUT" else 0
    out["trend_align"] = d_sign * base["ema_stack"]              # +1 with the EMA trend, -1 against it, 0 mixed
    pdi, mdi = base.get("plus_di"), base.get("minus_di")
    out["with_di"] = (1 if (pdi > mdi) == (d_sign > 0) else -1) if pdi is not None and mdi is not None else 0
    return out


def base_features(rows: list[dict]) -> dict:
    """Direction-independent part: one pass per candle, shared by every strategy that signals on it."""
    df = pd.DataFrame(rows)
    o, h, l, c, n = (df[k].astype(float) for k in ("o", "h", "l", "c", "n"))
    last = len(df) - 1
    close = c.iloc[-1]

    rsi14, rsi2 = ind.rsi(c, 14), ind.rsi(c, 2)
    k, d = ind.stochastic(h, l, c)
    cci = ind.cci(h, l, c)
    macd_line, macd_sig, macd_hist = ind.macd(c)
    lo, mid, up = ind.bollinger(c)
    bw = (up - lo) / mid
    atr = ind.atr(h, l, c, 14)
    adx, pdi, mdi = ind.adx(h, l, c, 14)
    e9, e21, e50 = ind.ema(c, 9), ind.ema(c, 21), ind.ema(c, 50)

    a = atr.iloc[-1]
    atr_ratio = a / atr.iloc[-50:].mean() if len(atr.dropna()) >= 10 else float("nan")
    bw_hist = bw.iloc[-100:].dropna()
    bw_pctile = float((bw_hist <= bw.iloc[-1]).mean() * 100) if len(bw_hist) >= 20 else float("nan")

    body = (c - o).iloc[-1]
    rng = h.iloc[-1] - l.iloc[-1]
    upper = h.iloc[-1] - max(o.iloc[-1], c.iloc[-1])
    lower = min(o.iloc[-1], c.iloc[-1]) - l.iloc[-1]
    colors = np.sign((c - o).to_numpy())
    streak = 0
    if colors[-1] != 0:
        for v in colors[::-1]:
            if v == colors[-1]:
                streak += 1
            else:
                break
        streak *= int(colors[-1])
    hi20, lo20 = h.iloc[-20:].max(), l.iloc[-20:].min()
    ema_stack = 0
    if not any(math.isnan(x) for x in (e9.iloc[-1], e21.iloc[-1], e50.iloc[-1])):
        ema_stack = 1 if e9.iloc[-1] > e21.iloc[-1] > e50.iloc[-1] else -1 if e9.iloc[-1] < e21.iloc[-1] < e50.iloc[-1] else 0

    def ret(m):
        return (close / c.iloc[-1 - m] - 1) * 100 if last >= m else float("nan")

    t = int(rows[-1]["t"])
    dt = datetime.fromtimestamp(t, tz=timezone.utc)
    tick_avg = n.iloc[-50:].mean()

    out = {
        "rsi14": _f(rsi14.iloc[-1], 2), "rsi2": _f(rsi2.iloc[-1], 2), "stoch_k": _f(k.iloc[-1], 2), "stoch_d": _f(d.iloc[-1], 2),
        "cci20": _f(cci.iloc[-1], 1), "macd": _f(macd_line.iloc[-1], 8), "macd_signal": _f(macd_sig.iloc[-1], 8), "macd_hist": _f(macd_hist.iloc[-1], 8),
        "bb_pctb": _f((close - lo.iloc[-1]) / (up.iloc[-1] - lo.iloc[-1]), 3) if up.iloc[-1] != lo.iloc[-1] else None,
        "bb_bw": _f(bw.iloc[-1], 6), "bb_bw_pctile": _f(bw_pctile, 1),
        "atr14": _f(a, 8), "atr_ratio": _f(atr_ratio, 3),
        "adx14": _f(adx.iloc[-1], 1), "plus_di": _f(pdi.iloc[-1], 1), "minus_di": _f(mdi.iloc[-1], 1),
        "ema9": _f(e9.iloc[-1], 6), "ema21": _f(e21.iloc[-1], 6), "ema50": _f(e50.iloc[-1], 6), "ema_stack": ema_stack,
        "ema21_slope_pct": _f((e21.iloc[-1] / e21.iloc[-6] - 1) * 100, 5) if last >= 6 and not math.isnan(e21.iloc[-6]) else None,
        "dist_hi20_atr": _f((close - hi20) / a, 2) if a else None, "dist_lo20_atr": _f((close - lo20) / a, 2) if a else None,
        "body_ratio": _f(abs(body) / rng, 3) if rng else None, "upper_wick_ratio": _f(upper / rng, 3) if rng else None,
        "lower_wick_ratio": _f(lower / rng, 3) if rng else None, "candle_color": int(np.sign(body)),
        "streak": streak, "last5": "".join("G" if v > 0 else "R" if v < 0 else "D" for v in colors[-5:]),
        "ret1_pct": _f(ret(1), 4), "ret5_pct": _f(ret(5), 4), "ret15_pct": _f(ret(15), 4),
        "hour_utc": dt.hour, "weekday": dt.weekday(), "session": session_of(dt.hour),
        "basis_ticks": int(n.iloc[-1]), "tick_ratio": _f(n.iloc[-1] / tick_avg, 2) if tick_avg else None,
    }
    return out
