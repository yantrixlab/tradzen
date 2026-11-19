"""How good is the chart's "who is in control" reading? Measured on the stored candles, nothing assumed.

Run from backend/:  .venv\\Scripts\\python tools\\marketread_eval.py [pairs] [candles per pair] [tf]

For every candle the reading (a score from -100 sellers to +100 buyers, see app/marketread.py) is compared with what the market did NEXT:
  next candle     did the next candle close in the direction the reading favoured?
  next 5 / 10     did price end up in that direction 5 / 10 candles later?
  1 ATR first     starting from this candle, which comes first within 15 candles: price 1 ATR in the favoured direction, or 1 ATR against it?
  after a break   after a CONFIRMED CHoCH / BOS: does price travel 1.5 ATR in the break's direction before it closes back through the broken level?
Each is reported by how strong the reading was (|score|), with the sample size and a 95% interval, because a number on a small sample means nothing.
The candles are split in time: the weights in marketread.WEIGHTS were chosen on the first 60% of each pair, the numbers printed are for the last 40% they never saw.
"""
import math
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import numpy as np  # noqa: E402

from app import evaluator, marketread, vec  # noqa: E402

DB = HERE.parent / "data" / "signals.db"
BUCKETS = [(0, 15), (15, 35), (35, 60), (60, 101)]


def wilson(k: int, n: int) -> tuple[float, float, float]:
    if n == 0:
        return 0.0, 0.0, 0.0
    p, z = k / n, 1.96
    d = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, mid - half, mid + half


def load(conn, symbol: str, tf: int, limit: int):
    rows = conn.execute("SELECT ts,o,h,l,c FROM candles WHERE symbol=? AND tf=? ORDER BY ts DESC LIMIT ?", (symbol, tf, limit)).fetchall()[::-1]
    return [{"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4]} for r in rows]


def first_passage(c, h, l, a, i, d, up=1.0, dn=1.0, span=15):
    """+1 if price gets `up` ATR in direction d first, -1 if `dn` ATR against it first, 0 if neither within `span` candles."""
    ref = c[i]
    for x in range(i + 1, min(len(c), i + 1 + span)):
        gain = (h[x] - ref) if d > 0 else (ref - l[x])
        loss = (ref - l[x]) if d > 0 else (h[x] - ref)
        g, b = gain >= up * a[i], loss >= dn * a[i]
        if g and b:
            return 0
        if g:
            return 1
        if b:
            return -1
    return 0


def evaluate(pairs: int, per: int, tf: int, part: str = "test") -> dict:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    # only pairs whose candles have a real high-low range: the "_mkt" pairs are stored as one price per candle (open = high = low = close), which has no pressure to read
    syms = [r[0] for r in conn.execute("SELECT symbol FROM candles WHERE tf=? GROUP BY symbol HAVING COUNT(*) >= ? AND AVG(h > l) > 0.7 ORDER BY symbol", (tf, per))][:pairs]
    acc = {k: {b: [0, 0] for b in BUCKETS} for k in ("next", "h5", "h10", "atr1")}
    brk = {"strong": [0, 0], "weak": [0, 0]}
    cand = 0
    for sym in syms:
        rows = load(conn, sym, tf, per)
        ts = np.array([r["t"] for r in rows], dtype=np.int64)
        for a0, b0 in vec.contiguous_runs(ts, tf):
            if b0 - a0 < 300:
                continue
            df = evaluator.build_df(rows[a0:b0])
            an = marketread.analyze(df)
            o, h, l, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
            a, score, n = an["atr"], an["flow"]["score"], len(c)
            cut = int(n * 0.6)
            rng = range(max(cut, 100), n - 16) if part == "test" else range(100, min(cut, n - 16))
            for i in rng:
                s = score[i]
                d = 1 if s > 0 else -1
                b = next(bk for bk in BUCKETS if bk[0] <= abs(s) < bk[1])
                for key, ok in (("next", (c[i + 1] - c[i]) * d > 0), ("h5", (c[i + 5] - c[i]) * d > 0), ("h10", (c[i + 10] - c[i]) * d > 0)):
                    acc[key][b][1] += 1
                    acc[key][b][0] += bool(ok)
                fp = first_passage(c, h, l, a, i, d)
                if fp != 0:
                    acc["atr1"][b][1] += 1
                    acc["atr1"][b][0] += fp > 0
                cand += 1
            for e in an["structure"]["events"]:
                if e["i"] not in rng or e["i"] + 40 >= n:
                    continue
                d, lv = e["dir"], e["level"]
                res = 0
                for x in range(e["i"] + 1, min(n, e["i"] + 41)):
                    if (c[x] - lv) * d < 0:
                        res = -1                                     # closed back through the broken level
                        break
                    if ((h[x] - lv) if d > 0 else (lv - l[x])) >= 1.5 * a[e["i"]]:
                        res = 1
                        break
                if res:
                    brk["strong" if e["strong"] else "weak"][1] += 1
                    brk["strong" if e["strong"] else "weak"][0] += res > 0
    return {"pairs": len(syms), "tf": tf, "candles": cand, "acc": acc, "brk": brk, "part": part}


def show(r: dict) -> None:
    print(f"{r['part']} part of {r['pairs']} pairs, tf {r['tf']}s, {r['candles']:,} candles read")
    names = {"next": "next candle closes with the reading", "h5": "price 5 candles later is with the reading", "h10": "price 10 candles later is with the reading",
             "atr1": "1 ATR in its favour before 1 ATR against (15 candles)"}
    for k, by in r["acc"].items():
        ok_all, n_all = sum(v[0] for v in by.values()), sum(v[1] for v in by.values())
        print(f"\n  {names[k]}   (all strengths: {ok_all / max(n_all, 1) * 100:.1f}% of {n_all:,})")
        for b, (ok, n) in by.items():
            p, lo, hi = wilson(ok, n)
            print(f"    |score| {b[0]:>3}-{min(b[1], 100):<3}  {p * 100:5.1f}%  [{lo * 100:4.1f} - {hi * 100:4.1f}]  n={n:,}")
    print("\n  after a break of structure: 1.5 ATR in its direction before a close back through the level")
    for k, (ok, n) in r["brk"].items():
        p, lo, hi = wilson(ok, n)
        print(f"    {k:<7} {p * 100:5.1f}%  [{lo * 100:4.1f} - {hi * 100:4.1f}]  n={n:,}")


if __name__ == "__main__":
    pairs = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    per = int(sys.argv[2]) if len(sys.argv) > 2 else 6000
    tf = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    show(evaluate(pairs, per, tf))
