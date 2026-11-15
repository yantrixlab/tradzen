"""5-factor confluence strategy (weights from the original product):
Bollinger bounce 30 | RSI divergence 25 | Stochastic cross 20 | CCI extreme+reversal 15 | Candlestick 10.

Each factor votes in [-1, +1] (+ = CALL/buy, - = PUT/sell). score = sum(vote * weight) in [-100, 100].
A signal is only issued when >= MIN_AGREE factors point the same way, the opposing weight is small and the
score is strong enough; otherwise SKIP. `strength` is a heuristic 0-100, NOT a win probability - real
accuracy comes from settled signals (see engine stats).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import indicators as ind

MIN_CANDLES = 60
MIN_AGREE = 2                  # tier B and above
TIER_A_AGREE = 3               # tier A = the original strict rule: >= 3 factors and strength >= 40
TIER_A_STRENGTH = 40
MAX_OPPOSING_WEIGHT = 25
MIN_STRENGTH = 30
WEIGHTS = {"bollinger": 30, "rsi_divergence": 25, "stochastic": 20, "cci": 15, "candlestick": 10}
LABELS = {"bollinger": "Bollinger Band Bounce", "rsi_divergence": "RSI Divergence", "stochastic": "Stochastic %K/%D Cross",
          "cci": "CCI Extreme + Reversal", "candlestick": "Candlestick Pattern"}


@dataclass
class Result:
    direction: str            # CALL | PUT | SKIP
    score: float              # -100..100
    strength: int             # 0..100
    agree: int
    factors: list[dict] = field(default_factory=list)
    reason: str = ""
    tier: str = ""            # "A" strict (>=3 factors, strength >= 40) | "B" looser (2 factors) | "" for SKIP


def _bollinger(df: pd.DataFrame, n: int = 20, k: float = 2.0) -> tuple[float, str]:
    lo, _, up = ind.bollinger(df["close"], n, k)
    l, h, c = df["low"].iloc[-1], df["high"].iloc[-1], df["close"].iloc[-1]
    if np.isnan(lo.iloc[-1]):
        return 0.0, "warming up"
    below, above = l <= lo.iloc[-1], h >= up.iloc[-1]
    if below and not above:
        return (1.0, "closed outside lower band") if c < lo.iloc[-1] else (0.7, "wick touched lower band")
    if above and not below:
        return (-1.0, "closed outside upper band") if c > up.iloc[-1] else (-0.7, "wick touched upper band")
    return 0.0, "inside bands"


def _rsi_divergence(df: pd.DataFrame, n_rsi: int = 14, lookback: int = 45) -> tuple[float, str]:
    r = ind.rsi(df["close"], n_rsi).to_numpy()
    n = len(df)
    window = slice(max(0, n - lookback), n)
    low, high = df["low"].to_numpy(), df["high"].to_numpy()
    base = window.start
    lows = [i + base for i in ind.swing_points(low[window], "low")]
    highs = [i + base for i in ind.swing_points(high[window], "high")]
    if len(lows) >= 2:
        a, b = lows[-2], lows[-1]
        if n - 1 - b <= 8 and low[b] < low[a] and r[b] > r[a] and r[a] < 45:
            return 1.0, "bullish divergence (lower low, higher RSI low)"
    if len(highs) >= 2:
        a, b = highs[-2], highs[-1]
        if n - 1 - b <= 8 and high[b] > high[a] and r[b] < r[a] and r[a] > 55:
            return -1.0, "bearish divergence (higher high, lower RSI high)"
    if not np.isnan(r[-1]):
        if r[-1] <= 30:
            return 0.4, f"RSI oversold {r[-1]:.0f}, no divergence"
        if r[-1] >= 70:
            return -0.4, f"RSI overbought {r[-1]:.0f}, no divergence"
    return 0.0, "no divergence"


def _stochastic(df: pd.DataFrame, k_len: int = 14, smooth: int = 3, d_len: int = 3) -> tuple[float, str]:
    k, d = ind.stochastic(df["high"], df["low"], df["close"], k_len, smooth, d_len)
    if len(k.dropna()) < 3 or np.isnan(d.iloc[-1]) or np.isnan(d.iloc[-2]):
        return 0.0, "warming up"
    k1, k0, d1, d0 = k.iloc[-2], k.iloc[-1], d.iloc[-2], d.iloc[-1]
    if k1 <= d1 and k0 > d0 and min(k1, k0) < 20:
        return 1.0, f"%K crossed above %D in oversold zone ({k0:.0f})"
    if k1 >= d1 and k0 < d0 and max(k1, k0) > 80:
        return -1.0, f"%K crossed below %D in overbought zone ({k0:.0f})"
    if k0 < 20:
        return 0.3, f"oversold ({k0:.0f}), no cross yet"
    if k0 > 80:
        return -0.3, f"overbought ({k0:.0f}), no cross yet"
    return 0.0, f"neutral ({k0:.0f})"


def _cci(df: pd.DataFrame, n: int = 20) -> tuple[float, str]:
    c = ind.cci(df["high"], df["low"], df["close"], n)
    if len(c.dropna()) < 3:
        return 0.0, "warming up"
    c1, c0 = c.iloc[-2], c.iloc[-1]
    if c1 < -100 and c0 > c1:
        return (1.0, f"CCI back above -100 ({c0:.0f})") if c0 > -100 else (0.6, f"CCI {c0:.0f} turning up from extreme")
    if c1 > 100 and c0 < c1:
        return (-1.0, f"CCI back below +100 ({c0:.0f})") if c0 < 100 else (-0.6, f"CCI {c0:.0f} turning down from extreme")
    return 0.0, f"CCI {c0:.0f}"


def _candlestick(df: pd.DataFrame) -> tuple[float, str]:
    o, h, l, c = (df[x].to_numpy() for x in ("open", "high", "low", "close"))
    rng = h[-1] - l[-1]
    if rng <= 0:
        return 0.0, "flat candle"
    body = abs(c[-1] - o[-1])
    upper, lower = h[-1] - max(o[-1], c[-1]), min(o[-1], c[-1]) - l[-1]
    win_lo, win_hi = l[-20:].min(), h[-20:].max()
    span = max(win_hi - win_lo, 1e-12)
    near_low, near_high = (l[-1] - win_lo) / span <= 0.25, (win_hi - h[-1]) / span <= 0.25
    if body / rng < 0.1:
        return 0.0, "doji (indecision)"
    prev_body = abs(c[-2] - o[-2])
    bull_engulf = c[-2] < o[-2] and c[-1] > o[-1] and c[-1] >= o[-2] and o[-1] <= c[-2] and body > prev_body
    bear_engulf = c[-2] > o[-2] and c[-1] < o[-1] and c[-1] <= o[-2] and o[-1] >= c[-2] and body > prev_body
    hammer = lower >= 2 * body and upper <= max(body, 1e-12) * 0.6
    star = upper >= 2 * body and lower <= max(body, 1e-12) * 0.6
    if bull_engulf or hammer:
        name = "bullish engulfing" if bull_engulf else "hammer / pin bar"
        return (1.0, f"{name} at recent low") if near_low else (0.5, f"{name} (mid-range)")
    if bear_engulf or star:
        name = "bearish engulfing" if bear_engulf else "shooting star / pin bar"
        return (-1.0, f"{name} at recent high") if near_high else (-0.5, f"{name} (mid-range)")
    return 0.0, "no pattern"


FACTORS = {"bollinger": _bollinger, "rsi_divergence": _rsi_divergence, "stochastic": _stochastic,
           "cci": _cci, "candlestick": _candlestick}

# primitives a declarative spec can reference with {"kind": "fn", "fn": <name>, "params": {...}}; each returns (vote -1..1, note)
FN_FACTORS = {
    "bollinger_bounce": (_bollinger, {"n": 20, "k": 2.0}),
    "rsi_divergence": (_rsi_divergence, {"n_rsi": 14, "lookback": 45}),
    "stochastic_cross": (_stochastic, {"k_len": 14, "smooth": 3, "d_len": 3}),
    "cci_reversal": (_cci, {"n": 20}),
    "candlestick": (_candlestick, {}),
}


def candles_to_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows).rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"})
    return df[["t", "open", "high", "low", "close"]].astype(float).reset_index(drop=True)


def evaluate(rows: list[dict]) -> Result:
    if len(rows) < MIN_CANDLES:
        return Result("SKIP", 0.0, 0, 0, [], f"warming up ({len(rows)}/{MIN_CANDLES} candles)")
    df = candles_to_df(rows)
    factors, score = [], 0.0
    for key, fn in FACTORS.items():
        try:
            vote, note = fn(df)
        except Exception as e:  # one broken factor must not kill the signal loop
            vote, note = 0.0, f"error: {e}"
        factors.append({"key": key, "name": LABELS[key], "weight": WEIGHTS[key], "vote": round(float(vote), 2), "note": note})
        score += vote * WEIGHTS[key]
    side = 1 if score > 0 else -1 if score < 0 else 0
    agree = sum(1 for f in factors if side and abs(f["vote"]) >= 0.3 and np.sign(f["vote"]) == side)
    opposing = sum(f["weight"] for f in factors if side and abs(f["vote"]) >= 0.3 and np.sign(f["vote"]) == -side)
    strength = int(min(100, round(abs(score))))
    direction, reason, tier = "SKIP", "", ""
    if not side:
        reason = "no factor active"
    elif agree < MIN_AGREE:
        reason = f"only {agree} factor(s) agree (need {MIN_AGREE})"
    elif opposing > MAX_OPPOSING_WEIGHT:
        reason = f"conflicting factors (weight {opposing})"
    elif strength < MIN_STRENGTH:
        reason = f"score too weak ({strength})"
    else:
        direction = "CALL" if side > 0 else "PUT"
        tier = "A" if agree >= TIER_A_AGREE and strength >= TIER_A_STRENGTH else "B"
    return Result(direction, round(float(score), 1), strength, agree, factors, reason, tier)
