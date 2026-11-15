"""Offline backtest of spec files over the stored candles (read-only: the database is opened read-only and nothing is written).

  .venv\\Scripts\\python tools\\bt_compare.py ..\\examples\\structure-zones.json ..\\examples\\supply-demand-zones.json --min-payout 80 --tfs 60

Prints, per spec: trades, win rate, break-even, the older 70 % vs the newest 30 % of trades, and the 1-step martingale net result.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import backtest, mtg  # noqa: E402
from app.spec import validate_spec  # noqa: E402

DB = Path(__file__).resolve().parent.parent / "data" / "signals.db"


def seg(t):
    n = len(t)
    if not n:
        return "0 trades"
    w = int((t["result"] == "win").sum())
    l = int((t["result"] == "loss").sum())
    pay = float(t["payout"].mean())
    be = 100 / (1 + pay / 100) if False else 100 / (1 + pay / 100)
    return f"{n:5d} trades  {100 * w / max(1, w + l):5.1f}% win  (break-even {be:4.1f}%)  net {t['profit'].sum():+7.1f}u"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("specs", nargs="+")
    ap.add_argument("--min-payout", type=float, default=80)
    ap.add_argument("--tfs", type=int, nargs="*", default=[60])
    ap.add_argument("--valid", type=float, default=0.3)
    ap.add_argument("--mtg", type=int, default=1, help="martingale steps shown next to the flat numbers (0 = off)")
    ap.add_argument("--mtg-mult", type=float, default=2.2)
    a = ap.parse_args()
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=60)
    payouts = {s: p for s, p in conn.execute("SELECT symbol, payout FROM instruments WHERE payout IS NOT NULL")}
    syms = [s for s, p in payouts.items() if p >= a.min_payout]
    for path in a.specs:
        raw = json.load(open(path))
        v = validate_spec(raw)
        if not v["ok"]:
            print(path, "INVALID", v["errors"])
            continue
        t = backtest.compute_trades(conn, v["spec"], {"symbols": syms, "tfs": a.tfs}, payouts)
        print(f"\n== {Path(path).name}  ({len(syms)} pairs paying >= {a.min_payout:.0f}%, tfs {a.tfs})")
        cache: dict = {}

        def line(label, part):
            s = seg(part)
            if a.mtg and len(part):
                m = mtg.run_summary(conn, part, a.mtg, a.mtg_mult, cache)
                acc = "  -  " if m["accuracy"] is None else f"{100 * m['accuracy']:4.1f}%"
                s += f"  | MTG-{a.mtg}: {acc} of {m['taken']}, net {m['net']:+7.1f}u (flat {m['flat_net']:+6.1f}u)"
            print(f"  {label}", s)

        line("all      ", t)
        if len(t):
            cut = backtest.split_time(t, a.valid)
            line("older 70%", t[t["t"] < cut])
            line("newest 30", t[t["t"] >= cut])
            for d, name in ((1, "CALL"), (-1, "PUT ")):
                line(f"{name}     ", t[t["dir"] == d])
            print(f"  trades per pair-day ~ {len(t) / max(1, t['symbol'].nunique()) / max(1, (t['t'].max() - t['t'].min()) / 86400):.2f}   pairs with a trade: {t['symbol'].nunique()}")
    conn.close()


if __name__ == "__main__":
    main()
