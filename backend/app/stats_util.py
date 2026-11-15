"""Shared statistics: Wilson interval, trade-table summaries (accuracy, expectancy, drawdown, streaks, equity curve)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = wins / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return centre - half, centre + half


def _streak(flags: np.ndarray) -> int:
    best = run = 0
    for f in flags:
        run = run + 1 if f else 0
        best = max(best, run)
    return best


def summarize(t: pd.DataFrame, equity_points: int = 400) -> dict:
    """t columns: t, result ('win'|'loss'|'draw'), profit (units of stake), payout (percent), strength."""
    n = len(t)
    wins = int((t["result"] == "win").sum())
    losses = int((t["result"] == "loss").sum())
    draws = n - wins - losses
    settled = wins + losses
    out = {"trades": n, "wins": wins, "losses": losses, "draws": draws, "settled": settled, "accuracy": None, "ci_low": None, "ci_high": None,
           "breakeven": None, "expectancy": None, "profit": 0.0, "profit_factor": None, "max_drawdown": 0.0, "longest_losing_streak": 0,
           "longest_winning_streak": 0, "avg_strength": None, "equity": []}
    if not n:
        return out
    t = t.sort_values("t")
    out["avg_strength"] = round(float(t["strength"].mean()), 1) if "strength" in t else None
    if settled:
        out["accuracy"] = wins / settled
        out["ci_low"], out["ci_high"] = wilson(wins, settled)
        ap = float(t.loc[t["result"].isin(["win", "loss"]), "payout"].mean()) / 100
        out["breakeven"] = 1 / (1 + ap)
    profit = t["profit"].to_numpy(float)
    out["profit"] = round(float(profit.sum()), 4)
    out["expectancy"] = round(float(profit.mean()), 5)
    gains, loss_sum = profit[profit > 0].sum(), -profit[profit < 0].sum()
    out["profit_factor"] = round(float(gains / loss_sum), 3) if loss_sum > 0 else None
    cum = np.cumsum(profit)
    out["max_drawdown"] = round(float((np.maximum.accumulate(np.r_[0.0, cum])[1:] - cum).max()), 4)
    res = t["result"].to_numpy()
    out["longest_losing_streak"] = _streak(res == "loss")
    out["longest_winning_streak"] = _streak(res == "win")
    step = max(1, len(cum) // equity_points)
    ts = t["t"].to_numpy()
    out["equity"] = [[int(ts[i]), round(float(cum[i]), 4)] for i in range(0, len(cum), step)]
    if (len(cum) - 1) % step:
        out["equity"].append([int(ts[-1]), round(float(cum[-1]), 4)])
    return out
