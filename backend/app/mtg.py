"""Martingale (MTG) ladders on top of a strategy's trades - for showing and costing them honestly, not for promising anything.

The rule (same on every pair/timeframe): a signal fires when candle t closes; the trade enters at the open of candle t+1 and settles at its close.
If it LOSES, MTG-1 is the next candle (t+2): same direction, stake x`mult`; if that loses too, MTG-2 is candle t+3 with stake x`mult`^2, and so on up
to `steps`. A win at any step recovers all earlier losses (plus whatever the stake multiple leaves over). A draw refunds that stake and ends the ladder.

Two honesty rules:
* a signal that fires while a ladder is still running cannot be taken (you are busy), so it is flagged `mtg_skip` and left out of the totals;
* a ladder whose next candle has not been stored yet is `open` and is left out of the profit figures instead of being guessed.

MTG does not change the odds of any single candle. It trades frequent small wins for rare large losses, so the summary reports the worst ladder,
the biggest stake and the deepest drawdown next to the profit.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ladder(cmap: dict[int, tuple[float, float]], t: int, d: int, tf: int, first_result: str, payout: float, steps: int, mult: float) -> dict:
    """cmap: candle time -> (open, close). Returns {outcome, steps:[{k,t,result,stake}], pl, stake_max, end_t} (pl in units of the first stake)."""
    p = payout / 100.0
    if first_result != "loss":
        return {"outcome": first_result, "steps": [], "pl": p if first_result == "win" else 0.0, "stake_max": 1.0, "end_t": t + tf}
    spent, done, stake_max = 1.0, [], 1.0
    for k in range(1, steps + 1):
        ct = t + (k + 1) * tf
        c = cmap.get(ct)
        if c is None:                                                  # the next candle is not stored (yet)
            return {"outcome": "open", "steps": done, "pl": 0.0, "stake_max": stake_max, "end_t": ct}
        stake = mult ** k
        stake_max = stake
        move = (c[1] - c[0]) * d
        res = "win" if move > 0 else "loss" if move < 0 else "draw"
        done.append({"k": k, "t": ct, "result": res, "stake": round(stake, 3)})
        if res == "win":
            return {"outcome": f"mtg{k}", "steps": done, "pl": stake * p - spent, "stake_max": stake_max, "end_t": ct}
        if res == "draw":
            return {"outcome": "draw", "steps": done, "pl": -spent, "stake_max": stake_max, "end_t": ct}
        spent += stake
    return {"outcome": "lost", "steps": done, "pl": -spent, "stake_max": stake_max, "end_t": done[-1]["t"] if done else t + tf}


def apply(trades: list[dict], cmap: dict[int, tuple[float, float]], tf: int, payout: float, steps: int, mult: float) -> dict:
    """Attach a ladder to every losing trade (`t["mtg"]`) and flag signals that fired mid-ladder (`t["mtg_skip"]`); returns the summary."""
    busy_until = -1
    taken = []
    for tr in sorted(trades, key=lambda x: x["t"]):
        if tr["t"] < busy_until:
            tr["mtg_skip"] = True
            continue
        lad = ladder(cmap, tr["t"], tr["dir"], tf, tr["result"], payout, steps, mult)
        busy_until = lad["end_t"]
        if tr["result"] == "loss":
            tr["mtg"] = lad
        taken.append((tr, lad))
    return summarize(taken, len(trades), payout, steps, mult)


def summarize(taken: list[tuple[dict, dict]], n_all: int, payout: float, steps: int, mult: float) -> dict:
    p = payout / 100.0
    out = {"steps": steps, "mult": mult, "payout": payout, "signals": n_all, "taken": len(taken), "skipped": n_all - len(taken),
           "first_wins": 0, "first_losses": 0, "first_draws": 0, "rescued": {f"mtg{k}": 0 for k in range(1, steps + 1)}, "lost_ladders": 0, "open": 0,
           "ladder_draws": 0, "ladder_pl": 0.0, "flat_pl": 0.0, "rescue_rate": None, "worst_ladder": 0.0, "stake_max": 1.0, "max_drawdown": 0.0,
           "longest_lost_run": 0, "units_at_risk": 1.0 + sum(mult ** k for k in range(1, steps + 1))}
    cum = peak = 0.0
    run = 0
    for tr, lad in taken:
        r = tr["result"]
        out["first_wins"] += r == "win"
        out["first_losses"] += r == "loss"
        out["first_draws"] += r == "draw"
        o = lad["outcome"]
        if o == "open":
            out["open"] += 1
            continue
        out["ladder_pl"] += lad["pl"]
        out["flat_pl"] += p if r == "win" else -1.0 if r == "loss" else 0.0
        out["worst_ladder"] = min(out["worst_ladder"], lad["pl"])
        out["stake_max"] = max(out["stake_max"], lad["stake_max"])
        if o in out["rescued"]:
            out["rescued"][o] += 1
        elif o == "lost":
            out["lost_ladders"] += 1
        elif o == "draw" and r == "loss":
            out["ladder_draws"] += 1
        run = run + 1 if o == "lost" else 0
        out["longest_lost_run"] = max(out["longest_lost_run"], run)
        cum += lad["pl"]
        peak = max(peak, cum)
        out["max_drawdown"] = max(out["max_drawdown"], peak - cum)
    resolved_losses = out["first_losses"] - out["open"]                 # open ladders have no outcome yet
    if resolved_losses > 0:
        out["rescue_rate"] = sum(out["rescued"].values()) / resolved_losses
    for k in ("ladder_pl", "flat_pl", "worst_ladder", "max_drawdown", "units_at_risk"):
        out[k] = round(out[k], 3)
    return out


# ----------------------------------------------------------------------------------------------- ladders over a whole table of trades
def add_ladders(conn, tr: pd.DataFrame, steps: int, mult: float, cache: dict | None = None) -> pd.DataFrame:
    """Adds mtg_pl (net units of the first stake: a ladder's result, or the plain trade's), mtg_skip (signal fired mid-ladder - cannot be taken),
    mtg_outcome and mtg_stake. Ladders are tracked per pair+timeframe, exactly like the chart (apply() above).
    `cache` (optional dict) keeps each pair's candle map between calls, so many strategies can share one read of the candles."""
    tr = tr.copy()
    pl = pd.Series(np.nan, index=tr.index)
    skip = pd.Series(False, index=tr.index)
    outcome = pd.Series("", index=tr.index, dtype=object)
    stake = pd.Series(1.0, index=tr.index)
    for (sym, tf), g in tr.groupby(["symbol", "tf"]):
        key = (sym, int(tf))
        if cache is not None and key in cache:
            cmap = cache[key]
        else:
            cmap = {ts: (o, c) for ts, o, c in conn.execute("SELECT ts, o, c FROM candles WHERE symbol=? AND tf=?", (sym, int(tf)))}
            if cache is not None:
                cache[key] = cmap
        recs = [{"t": int(t), "dir": int(d), "result": r, "_i": i} for i, t, d, r in zip(g.index, g["t"], g["dir"], g["result"])]
        payout = float(g["payout"].iloc[0])
        apply(recs, cmap, int(tf), payout, steps, mult)
        for rec in recs:
            i = rec["_i"]
            if rec.get("mtg_skip"):
                skip[i] = True
                continue
            lad = rec.get("mtg")
            if lad is None:                                           # a win or a draw on the first trade: no ladder
                pl[i], outcome[i] = (payout / 100.0 if rec["result"] == "win" else 0.0), rec["result"]
            else:
                outcome[i], stake[i] = lad["outcome"], lad["stake_max"]
                pl[i] = np.nan if lad["outcome"] == "open" else lad["pl"]      # an unfinished ladder has no result yet: not guessed
    tr["mtg_pl"], tr["mtg_skip"], tr["mtg_outcome"], tr["mtg_stake"] = pl, skip, outcome, stake
    return tr


def run_summary(conn, trades: pd.DataFrame, steps: int = 1, mult: float = 2.2, cache: dict | None = None) -> dict:
    """The few numbers a run's row shows for martingale: accuracy WITH the re-entry, and how many trades were won only by the re-entry.

    A signal counts as a win if the first trade won OR any re-entry won; a loss if every step lost; draws and unfinished ladders are left out of the
    accuracy (like the plain accuracy ignores draws). Signals that fired while a ladder was running cannot be taken and are not counted."""
    if trades.empty:
        return table_summary(trades, steps, mult)
    return table_summary(add_ladders(conn, trades, steps, mult, cache), steps, mult)


def table_summary(t: pd.DataFrame, steps: int, mult: float) -> dict:
    """The summary of a table that already has its ladders (add_ladders): see run_summary."""
    out = {"steps": steps, "mult": mult, "signals": int(len(t)), "taken": 0, "skipped": 0, "wins": 0, "rescued": 0, "lost": 0, "draws": 0, "open": 0,
           "accuracy": None, "net": 0.0, "flat_net": 0.0}
    if t.empty:
        return out
    out["skipped"] = int(t["mtg_skip"].sum())
    u = t[~t["mtg_skip"]]
    out["taken"] = int(len(u))
    oc = u["mtg_outcome"]
    out["wins"], out["lost"] = int((oc == "win").sum()), int((oc == "lost").sum())
    out["rescued"] = int(oc.str.startswith("mtg").sum())              # first trade lost, a re-entry won
    out["draws"], out["open"] = int((oc == "draw").sum()), int((oc == "open").sum())
    decided = out["wins"] + out["rescued"] + out["lost"]
    out["accuracy"] = (out["wins"] + out["rescued"]) / decided if decided else None
    done = u[u["mtg_pl"].notna()]
    out["net"], out["flat_net"] = round(float(done["mtg_pl"].sum()), 3), round(float(done["profit"].sum()), 3)
    return out


def signals_summary(conn, payouts: dict, default_payout: float = 85.0, steps: int = 1, mult: float = 2.2) -> dict[int, dict]:
    """strategy_id -> one-step-MTG summary of that strategy's real settled signals (the live/paper forward results, not a backtest).

    A signal fires when candle `basis_t` closes, enters at the next candle and settles there; the re-entry is the candle after - the same rule as everywhere else
    (see ladder()). Pending and void signals are left out. Strategies without any settled signal are absent."""
    rows = conn.execute("SELECT strategy_id, symbol, tf, basis_t, direction, status FROM signals "
                        "WHERE direction IN ('CALL','PUT') AND status IN ('win','loss','draw') ORDER BY strategy_id, symbol, tf, basis_t").fetchall()
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["strategy_id", "symbol", "tf", "t", "direction", "result"])
    df["dir"] = np.where(df["direction"] == "CALL", 1, -1)
    df["payout"] = df["symbol"].map(lambda s: float(payouts.get(s) or default_payout))
    df["profit"] = np.where(df["result"] == "win", df["payout"] / 100.0, np.where(df["result"] == "loss", -1.0, 0.0))
    cache: dict = {}
    out: dict[int, dict] = {}
    for sid, g in df.groupby("strategy_id"):
        out[int(sid)] = run_summary(conn, g.reset_index(drop=True), steps, mult, cache)
    return out
