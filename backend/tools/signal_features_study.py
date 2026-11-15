"""Does anything that can be seen BEFORE a signal separate its winners from its losers? (buying / selling pressure, an over-extended move, momentum ...)

  .venv\\Scripts\\python tools\\signal_features_study.py ..\\examples\\ez1-engulf-zone.json --min-payout 80 --tf 60

Read-only. Backtests the spec on the stored candles, then for every trade measures features of the candles before the signal, each SIGNED by the trade's
direction (+ = already moved / pressed in the direction of the trade), and compares winners with losers on the older 70 % (where a pattern is looked for)
and on the newest 30 % (where it has to show up again). A feature only counts if it is significant on the older part (|z| > 3, a hurdle that allows for
the dozen features tried) AND has the same sign and |z| > 1.5 on the newest part.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import backtest, indicators as ind  # noqa: E402
from app.spec import validate_spec  # noqa: E402
import json  # noqa: E402

DB = Path(__file__).resolve().parent.parent / "data" / "signals.db"


def features(conn, tr: pd.DataFrame, tf: int) -> pd.DataFrame:
    rows = []
    for sym, g in tr.groupby("symbol"):
        q = pd.DataFrame(conn.execute("SELECT ts, o, h, l, c FROM candles WHERE symbol=? AND tf=? ORDER BY ts", (sym, tf)).fetchall(), columns=["t", "open", "high", "low", "close"]).astype(float)
        o, h, l, c = (q[k].to_numpy() for k in ("open", "high", "low", "close"))
        atr = ind.atr(q["high"], q["low"], q["close"], 14).to_numpy()
        rsi = ind.rsi(q["close"], 14).to_numpy()
        hi20, lo20 = pd.Series(h).rolling(20).max().to_numpy(), pd.Series(l).rolling(20).min().to_numpy()
        rng = np.where(h - l > 0, h - l, np.nan)
        clv = ((c - l) - (h - c)) / rng                                   # +1 = closed at the high, -1 = at the low
        pos = {int(t): i for i, t in enumerate(q["t"].to_numpy())}
        for r in g.itertuples():
            i = pos.get(int(r.t))
            if i is None or i < 25 or not np.isfinite(atr[i]) or atr[i] <= 0:
                continue
            d = int(r.dir)
            streak = 0
            while streak < 20 and d * (c[i - streak] - o[i - streak]) > 0:
                streak += 1
            rows.append({
                "t": r.t, "win": 1 if r.result == "win" else 0,
                "sig_body_atr": d * (c[i] - o[i]) / atr[i],                          # how big the signal candle itself is
                "streak_in_dir": streak,                                              # consecutive candles already in the trade's direction
                "clv5_dir": d * np.nanmean(clv[i - 4:i + 1]),                         # close location over the last 5 candles = buying pressure for a CALL, selling for a PUT
                "clv5_prev": d * np.nanmean(clv[i - 5:i]),                            # the same, before the signal candle
                "body5_dir": d * np.sum(c[i - 4:i + 1] - o[i - 4:i + 1]) / atr[i],    # net signed bodies of the last 5 candles
                "move5_dir": d * (c[i] - c[i - 5]) / atr[i],                          # distance already travelled in the trade's direction
                "move15_dir": d * (c[i] - c[i - 15]) / atr[i],
                "rsi_dir": d * (rsi[i] - 50),                                         # + = RSI already stretched in the trade's direction
                "room_to_extreme": ((hi20[i] - c[i]) if d > 0 else (c[i] - lo20[i])) / atr[i],       # ATRs left to the 20-candle high / low
                "wick_against": ((h[i] - max(o[i], c[i])) if d > 0 else (min(o[i], c[i]) - l[i])) / rng[i],   # rejection wick at the end of the signal candle
                "range_atr": (h[i] - l[i]) / atr[i],
                "prev_body_dir": d * (c[i - 1] - o[i - 1]) / atr[i],                  # the candle before: with the trade (+) or the pullback (-)
            })
    return pd.DataFrame(rows).sort_values("t").reset_index(drop=True)


def corr_z(x, y):
    x = pd.Series(x).rank().to_numpy()
    r = np.corrcoef(x, np.asarray(y, float))[0, 1]
    return r, r * np.sqrt(len(x) - 2) / np.sqrt(max(1e-9, 1 - r * r))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--min-payout", type=float, default=80)
    ap.add_argument("--tf", type=int, default=60)
    a = ap.parse_args()
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=60)
    payouts = {s: p for s, p in conn.execute("SELECT symbol, payout FROM instruments WHERE payout IS NOT NULL")}
    v = validate_spec(json.load(open(a.spec)))
    if not v["ok"]:
        sys.exit(f"invalid spec: {v['errors']}")
    tr = backtest.compute_trades(conn, v["spec"], {"symbols": [s for s, p in payouts.items() if p >= a.min_payout], "tfs": [a.tf]}, payouts)
    tr = tr[tr["result"].isin(["win", "loss"])].reset_index(drop=True)
    f = features(conn, tr, a.tf)
    cut = int(len(f) * 0.7)
    fit, val = f.iloc[:cut], f.iloc[cut:]
    print(f"{len(f)} trades, win rate {f['win'].mean() * 100:.1f}%  (older 70 %: {fit['win'].mean() * 100:.1f}%, newest 30 %: {val['win'].mean() * 100:.1f}%)\n")
    print(f"{'feature':16s} {'older corr':>10s} {'z':>6s} | {'newest corr':>11s} {'z':>6s}  same sign   win % by older-part quintile, low -> high")
    hits = []
    for k in [c for c in f.columns if c not in ("t", "win")]:
        rf, zf = corr_z(fit[k], fit["win"])
        rv, zv = corr_z(val[k], val["win"])
        edges = np.nanquantile(fit[k], [0.2, 0.4, 0.6, 0.8])
        bf = np.digitize(fit[k], edges)
        wf = [round(fit["win"][bf == b].mean() * 100) if (bf == b).sum() else None for b in range(5)]
        same = np.sign(rf) == np.sign(rv)
        print(f"{k:16s} {rf:+10.3f} {zf:+6.1f} | {rv:+11.3f} {zv:+6.1f}  {'yes' if same else 'NO ':9s}   {wf}")
        if abs(zf) > 3.0 and same and abs(zv) > 1.5:
            hits.append(k)
    print("\nfeatures that hold up (|z|>3 on the older part, same sign and |z|>1.5 on the newest):", hits or "none")


if __name__ == "__main__":
    main()
