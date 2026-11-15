"""Vectorised strategy evaluation + feature frame for backtests and loss analysis.

evaluate_series(spec, df) gives, for EVERY candle of a contiguous run, what evaluator.evaluate() would have decided with the data up to that candle.
Parity with the live path is covered by tests (tests/test_backtest.py). df columns: t, open, high, low, close, n (see evaluator.build_df).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import evaluator, expr, indreg, patterns
from . import indicators as ind

SIDE_THRESHOLD = evaluator.SIDE_THRESHOLD


# ----------------------------------------------------------------------------------------------- runs
def contiguous_runs(t: np.ndarray, tf: int) -> list[tuple[int, int]]:
    """[(start, end)) index ranges of candles with no missing bucket in between."""
    if len(t) == 0:
        return []
    breaks = np.flatnonzero(np.diff(t) != tf) + 1
    starts = np.r_[0, breaks]
    ends = np.r_[breaks, len(t)]
    return list(zip(starts.tolist(), ends.tolist()))


# ----------------------------------------------------------------------------------------------- fn factor votes (vectorised twins of strategy._*)
def _swing_flags(vals: np.ndarray, kind: str) -> np.ndarray:
    n = len(vals)
    out = np.zeros(n, bool)
    if n < 5:
        return out
    w = np.lib.stride_tricks.sliding_window_view(vals, 5)
    c = w[:, 2]
    ext = w.min(axis=1) if kind == "low" else w.max(axis=1)
    ok = (c == ext) & ((w == c[:, None]).sum(axis=1) == 1) & ~np.isnan(w).any(axis=1)
    out[2:n - 2] = ok
    return out


def vote_bollinger(df, n=20, k=2.0) -> np.ndarray:
    lo, _, up = ind.bollinger(df["close"], n, k)
    below, above, valid = df["low"] <= lo, df["high"] >= up, lo.notna()
    only_b, only_a = (below & ~above & valid).to_numpy(), (above & ~below & valid).to_numpy()
    c, lo_, up_ = df["close"].to_numpy(), lo.to_numpy(), up.to_numpy()
    v = np.zeros(len(df))
    v[only_b] = np.where(c[only_b] < lo_[only_b], 1.0, 0.7)
    v[only_a] = np.where(c[only_a] > up_[only_a], -1.0, -0.7)
    return v


def vote_rsi_divergence(df, n_rsi=14, lookback=45) -> np.ndarray:
    r = ind.rsi(df["close"], n_rsi).to_numpy()
    low, high = df["low"].to_numpy(), df["high"].to_numpy()
    lows, highs = np.flatnonzero(_swing_flags(low, "low")), np.flatnonzero(_swing_flags(high, "high"))
    out = np.zeros(len(df))
    for i in range(len(df)):
        start = max(0, i + 1 - lookback)
        a = lows[np.searchsorted(lows, start + 2, "left"):np.searchsorted(lows, i - 2, "right")]
        if len(a) >= 2 and i - a[-1] <= 8 and low[a[-1]] < low[a[-2]] and r[a[-1]] > r[a[-2]] and r[a[-2]] < 45:
            out[i] = 1.0
            continue
        h = highs[np.searchsorted(highs, start + 2, "left"):np.searchsorted(highs, i - 2, "right")]
        if len(h) >= 2 and i - h[-1] <= 8 and high[h[-1]] > high[h[-2]] and r[h[-1]] < r[h[-2]] and r[h[-2]] > 55:
            out[i] = -1.0
            continue
        if not np.isnan(r[i]):
            out[i] = 0.4 if r[i] <= 30 else -0.4 if r[i] >= 70 else 0.0
    return out


def vote_stochastic(df, k_len=14, smooth=3, d_len=3) -> np.ndarray:
    k, d = ind.stochastic(df["high"], df["low"], df["close"], k_len, smooth, d_len)
    k0, d0, k1, d1 = k, d, k.shift(1), d.shift(1)
    ready = (k.notna().cumsum() >= 3) & d0.notna() & d1.notna()
    bull = (k1 <= d1) & (k0 > d0) & (np.minimum(k1, k0) < 20)
    bear = (k1 >= d1) & (k0 < d0) & (np.maximum(k1, k0) > 80)
    v = np.select([bull, bear, k0 < 20, k0 > 80], [1.0, -1.0, 0.3, -0.3], default=0.0)
    return np.where(ready, v, 0.0)


def vote_cci(df, n=20) -> np.ndarray:
    c = ind.cci(df["high"], df["low"], df["close"], n)
    c0, c1 = c, c.shift(1)
    ready = (c.notna().cumsum() >= 3) & c1.notna()
    bull = (c1 < -100) & (c0 > c1)
    bear = (c1 > 100) & (c0 < c1)
    v = np.select([bull & (c0 > -100), bull, bear & (c0 < 100), bear], [1.0, 0.6, -1.0, -0.6], default=0.0)
    return np.where(ready, v, 0.0)


def vote_candlestick(df) -> np.ndarray:
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    rng, body = h - l, c - o
    ab = body.abs()
    upper, lower = h - np.maximum(o, c), np.minimum(o, c) - l
    win_lo, win_hi = l.rolling(20, min_periods=1).min(), h.rolling(20, min_periods=1).max()
    span = np.maximum(win_hi - win_lo, 1e-12)
    near_low, near_high = (l - win_lo) / span <= 0.25, (win_hi - h) / span <= 0.25
    prev_body = ab.shift(1)
    bull_e = (c.shift(1) < o.shift(1)) & (c > o) & (c >= o.shift(1)) & (o <= c.shift(1)) & (ab > prev_body)
    bear_e = (c.shift(1) > o.shift(1)) & (c < o) & (c <= o.shift(1)) & (o >= c.shift(1)) & (ab > prev_body)
    hammer = (lower >= 2 * ab) & (upper <= np.maximum(ab, 1e-12) * 0.6)
    star = (upper >= 2 * ab) & (lower <= np.maximum(ab, 1e-12) * 0.6)
    bull, bear = bull_e | hammer, bear_e | star
    v = np.select([bull & near_low, bull, bear & near_high, bear], [1.0, 0.5, -1.0, -0.5], default=0.0)
    flat = (rng <= 0) | (ab / rng.replace(0, np.nan) < 0.1)
    return np.where(flat.fillna(True).to_numpy(), 0.0, v)


FN_VEC = {"bollinger_bounce": vote_bollinger, "rsi_divergence": vote_rsi_divergence, "stochastic_cross": vote_stochastic,
          "cci_reversal": vote_cci, "candlestick": vote_candlestick}


# ----------------------------------------------------------------------------------------------- filters
def _filter_fail(f: dict, df: pd.DataFrame, direction: np.ndarray, cache: dict, payout: float | None) -> np.ndarray:
    typ, n = f["type"], len(df)
    fail = np.zeros(n, bool)
    if typ in ("adx_min", "adx_max"):
        v = indreg.compute(cache, df, "adx", {"n": f["n"]}).adx.to_numpy()
        with np.errstate(invalid="ignore"):
            fail = (v < f["value"]) if typ == "adx_min" else (v > f["value"])
        return fail & ~np.isnan(v)
    if typ in ("atr_ratio_min", "atr_ratio_max"):
        a = indreg.compute(cache, df, "atr", {"n": 14})
        ratio = (a / a.rolling(50, min_periods=1).mean()).to_numpy()
        ok = (a.notna().cumsum() >= 10).to_numpy()
        with np.errstate(invalid="ignore"):
            fail = (ratio < f["value"]) if typ == "atr_ratio_min" else (ratio > f["value"])
        return fail & ok & ~np.isnan(ratio)
    if typ == "hours":
        minute = ((df["t"].to_numpy() % 86400) // 60).astype(int)
        inc = np.zeros(n, bool)
        for w in f["include"]:
            inc |= np.array([evaluator._in_window(int(m), w) for m in minute])
        exc = np.zeros(n, bool)
        for w in f["exclude"]:
            exc |= np.array([evaluator._in_window(int(m), w) for m in minute])
        return (~inc if f["include"] else np.zeros(n, bool)) | exc
    if typ == "weekdays":
        wd = (((df["t"].to_numpy() // 86400) + 3) % 7).astype(int)
        return (~np.isin(wd, f["include"]) if f["include"] else np.zeros(n, bool)) | np.isin(wd, f["exclude"])
    if typ == "trend_alignment":
        e = [indreg.compute(cache, df, "ema", {"n": p}).to_numpy() for p in (9, 21, 50)]
        known = ~(np.isnan(e[0]) | np.isnan(e[1]) | np.isnan(e[2]))
        stack = np.where(e[0] > e[1], np.where(e[1] > e[2], 1, 0), np.where((e[0] < e[1]) & (e[1] < e[2]), -1, 0))
        align = stack * direction
        want = f["value"]
        bad = (align <= 0) if want == "with" else (align >= 0) if want == "against" else (align < 0) if want == "not_against" else (align > 0)
        return bad & known
    if typ == "min_payout":
        return np.full(n, payout is not None and payout < f["value"])
    if typ == "direction_only":
        return direction != (1 if f["value"] == "CALL" else -1)
    if typ == "one_per_run":
        # compare with the signals that reach this filter (not with the ones it removes), so a long run stays suppressed after its first signal
        for k in range(1, int(f["gap"]) + 1):
            prev = np.r_[np.zeros(k, direction.dtype), direction[:-k]] if len(direction) > k else np.zeros_like(direction)
            fail |= (direction != 0) & (direction == prev)
        return fail
    return fail


# ----------------------------------------------------------------------------------------------- evaluation
def evaluate_series(spec: dict, df: pd.DataFrame, payout: float | None = None) -> pd.DataFrame:
    """Columns: dir (+1 CALL, -1 PUT, 0 none), score, strength, agree, tier (0 none, 1 A, 2 B). Row i = decision after candle i closes."""
    n = len(df)
    cache: dict = {}
    env = None
    votes, weights = [], []
    total_w = float(sum(f["weight"] for f in spec["factors"])) or 1.0
    for f in spec["factors"]:
        if f["kind"] == "fn":
            v = FN_VEC[f["fn"]](df, **f["params"])
        elif f["kind"] == "pattern":
            env = env or evaluator._ind_env(spec, df, cache)
            c = patterns.match(df, env, f["call"]).to_numpy() if f.get("call") else np.zeros(n, bool)
            p = patterns.match(df, env, f["put"]).to_numpy() if f.get("put") else np.zeros(n, bool)
            v = np.where(c & ~p, f["vote"], np.where(p & ~c, -f["vote"], 0.0))
        else:
            env = env or evaluator._ind_env(spec, df, cache)
            c = expr.evaluate(f["call"], env, df.index).to_numpy() if f.get("call") else np.zeros(n, bool)
            p = expr.evaluate(f["put"], env, df.index).to_numpy() if f.get("put") else np.zeros(n, bool)
            v = np.where(c & ~p, f["vote"], np.where(p & ~c, -f["vote"], 0.0))
        votes.append(np.asarray(v, float))
        weights.append(f["weight"] * 100.0 / total_w)
    V, w = np.column_stack(votes), np.array(weights)
    score = (V * w).sum(axis=1)
    side = np.sign(score)
    active = np.abs(V) >= SIDE_THRESHOLD
    agree = (active & (np.sign(V) == side[:, None]) & (side[:, None] != 0)).sum(axis=1)
    opposing = ((active & (np.sign(V) == -side[:, None]) & (side[:, None] != 0)) * w).sum(axis=1)
    strength = np.minimum(100, np.round(np.abs(score))).astype(int)
    g = spec["gates"]
    ok = (side != 0) & (agree >= g["min_agree"]) & (opposing <= g["max_opposing_weight"]) & (strength >= g["min_strength"])
    ok &= np.arange(n) >= int(g["min_candles"]) - 1
    direction = (side * ok).astype(int)
    ta = g["tier_a"]
    tier = np.where(direction == 0, 0, 1 if ta is None else np.where((agree >= ta["agree"]) & (strength >= ta["strength"]), 1, 2))
    for f in spec["filters"]:
        bad = _filter_fail(f, df, direction, cache, payout) & (direction != 0)
        direction = np.where(bad, 0, direction)
        tier = np.where(bad, 0, tier)
    return pd.DataFrame({"dir": direction, "score": np.round(score, 1), "strength": strength, "agree": agree, "tier": tier}, index=df.index)


# ----------------------------------------------------------------------------------------------- feature frame (vectorised twin of features.base_features)
SESSION_EDGES = [(7, "asia"), (13, "europe"), (16, "eu_us_overlap"), (21, "us"), (24, "late")]


def featurize(df: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    k, d = ind.stochastic(h, l, c)
    lo, mid, up = ind.bollinger(c)
    bw = (up - lo) / mid
    atr = ind.atr(h, l, c, 14)
    adx, pdi, mdi = ind.adx(h, l, c, 14)
    e9, e21, e50 = ind.ema(c, 9), ind.ema(c, 21), ind.ema(c, 50)
    _, _, macd_hist = ind.macd(c)
    rng, body = h - l, c - o
    hour = ((df["t"] % 86400) // 3600).astype(int)
    stack = np.where((e9 > e21) & (e21 > e50), 1, np.where((e9 < e21) & (e21 < e50), -1, 0))
    stack = np.where(e9.isna() | e21.isna() | e50.isna(), 0, stack)
    session = np.select([hour < s for s, _ in SESSION_EDGES], [name for _, name in SESSION_EDGES], default="late")
    hi20, lo20 = h.rolling(20, min_periods=1).max(), l.rolling(20, min_periods=1).min()
    colors = np.sign(body.to_numpy())
    f = pd.DataFrame({
        "rsi14": ind.rsi(c, 14), "rsi2": ind.rsi(c, 2), "stoch_k": k, "stoch_d": d, "cci20": ind.cci(h, l, c), "macd_hist": macd_hist,
        "bb_pctb": (c - lo) / (up - lo).replace(0, np.nan), "bb_bw": bw,
        "bb_bw_pctile": bw.rolling(100, min_periods=20).apply(lambda w: (w <= w[-1]).mean() * 100, raw=True),
        "atr14": atr, "atr_ratio": atr / atr.rolling(50, min_periods=1).mean(), "adx14": adx, "plus_di": pdi, "minus_di": mdi,
        "ema_stack": stack, "ema21_slope_pct": (e21 / e21.shift(5) - 1) * 100,
        "dist_hi20_atr": (c - hi20) / atr, "dist_lo20_atr": (c - lo20) / atr,
        "body_ratio": body.abs() / rng.replace(0, np.nan), "upper_wick_ratio": (h - np.maximum(o, c)) / rng.replace(0, np.nan),
        "lower_wick_ratio": (np.minimum(o, c) - l) / rng.replace(0, np.nan), "candle_color": colors.astype(int),
        "streak": evaluator._streak(colors).astype(int), "ret1_pct": (c / c.shift(1) - 1) * 100, "ret5_pct": (c / c.shift(5) - 1) * 100,
        "ret15_pct": (c / c.shift(15) - 1) * 100, "hour_utc": hour, "weekday": (((df["t"] // 86400) + 3) % 7).astype(int), "session": session,
    }, index=df.index)
    return f
