"""Trader sentiment (Quotex's "% of traders choosing up") - how well we are collecting it and whether it says anything about the next candle.

Sentiment is the one input that is not derived from the price series, so it is the best remaining lead. The study below is deliberately plain:
for every candle, take the newest sentiment sample known BEFORE the candle opened (no look-ahead), bucket it, and see how often the candle closed up.
It reports the margin of error, and whether the two halves of the data agree, so a lucky bucket is not mistaken for an edge.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

EDGES = (0.0, 20.0, 40.0, 60.0, 80.0, 100.01)
RECEIVING_S = 180                    # a pair counts as "receiving" if its newest sample is younger than this
SAMPLES_FOR_3_POINTS = 1100          # per bucket: about what it takes to see a 3-point shift from 50 % (95 % interval half-width ~3 points)


def coverage(conn, now: float, instruments: dict, window: int = 600) -> dict:
    rows = {s: (n, t0, t1, recent) for s, n, t0, t1, recent in conn.execute(
        "SELECT symbol, COUNT(*), MIN(ts), MAX(ts), SUM(ts >= ?) FROM sentiment GROUP BY symbol", (now - window,))}
    pairs = []
    for sym in sorted(set(instruments) | set(rows)):
        inst = instruments.get(sym) or {}
        n, t0, t1, recent = rows.get(sym, (0, None, None, 0))
        age = None if t1 is None else max(0, int(now - t1))
        pairs.append({"symbol": sym, "name": inst.get("name") or sym, "market": inst.get("market") or ("OTC" if sym.endswith("_otc") else "LIVE"),
                      "payout": inst.get("payout"), "open": bool(inst.get("open", True)), "samples": int(n), "first": t0, "last": t1, "age_s": age,
                      "recent": int(recent or 0), "receiving": age is not None and age <= RECEIVING_S})
    pairs.sort(key=lambda p: (not p["receiving"], -p["samples"], p["symbol"]))
    total = sum(p["samples"] for p in pairs)
    first = min((p["first"] for p in pairs if p["first"]), default=None)
    return {"now": now, "window_s": window, "total_samples": total, "pairs_total": len(pairs), "pairs_with_data": sum(1 for p in pairs if p["samples"]),
            "pairs_receiving": sum(1 for p in pairs if p["receiving"]), "span_hours": round((now - first) / 3600, 1) if first else 0.0,
            "samples_last_window": sum(p["recent"] for p in pairs), "pairs": pairs}


def _z(p: float, n: int) -> float:
    return (p - 0.5) / math.sqrt(0.25 / n) if n else 0.0


def study(conn, tf: int = 60, tolerance: int = 30, payouts: dict | None = None, min_payout: float | None = None, edges: tuple = EDGES) -> dict:
    """P(candle closes up | crowd sentiment before it opened). Sentiment must be at most `tolerance` seconds older than the candle's open."""
    sent = pd.DataFrame(conn.execute("SELECT symbol, ts, pct FROM sentiment").fetchall(), columns=["symbol", "sts", "pct"])
    out: dict = {"tf": tf, "tolerance_s": tolerance, "min_payout": min_payout, "samples_total": int(len(sent)), "matched": 0, "pairs": 0, "buckets": [],
                 "edge_found": False, "split_t": None, "note": None, "needed_per_bucket": SAMPLES_FOR_3_POINTS}
    if sent.empty:
        out["note"] = "No sentiment stored yet."
        return out
    parts = []
    for sym, g in sent.groupby("symbol"):
        if min_payout and float((payouts or {}).get(sym) or 0) < min_payout:
            continue
        c = pd.DataFrame(conn.execute("SELECT ts, o, c FROM candles WHERE symbol=? AND tf=? ORDER BY ts", (sym, tf)).fetchall(), columns=["ts", "o", "c"])
        if c.empty:
            continue
        m = pd.merge_asof(c, g.sort_values("sts")[["sts", "pct"]], left_on="ts", right_on="sts", direction="backward", tolerance=tolerance)
        m = m.dropna(subset=["pct"])
        if len(m):
            m["symbol"] = sym
            parts.append(m)
    if not parts:
        out["note"] = "Sentiment samples exist, but none lines up with a stored candle yet (candles and sentiment must overlap in time)."
        return out
    d = pd.concat(parts, ignore_index=True)
    d["up"] = np.sign(d["c"] - d["o"])
    d = d[d["up"] != 0].sort_values("ts")
    cut = float(d["ts"].quantile(0.5))
    out.update(matched=int(len(d)), pairs=int(d["symbol"].nunique()), split_t=int(cut))
    for lo, hi in zip(edges, edges[1:]):
        b = d[(d["pct"] >= lo) & (d["pct"] < hi)]
        n = len(b)
        row = {"lo": lo, "hi": min(hi, 100.0), "n": int(n), "up_rate": None, "z": None, "first_half": None, "second_half": None, "n_first": 0, "n_second": 0,
               "enough": n >= SAMPLES_FOR_3_POINTS, "stable": None}
        if n:
            p = float((b["up"] > 0).mean())
            a, z2 = b[b["ts"] < cut], b[b["ts"] >= cut]
            row.update(up_rate=p, z=_z(p, n), n_first=int(len(a)), n_second=int(len(z2)),
                       first_half=float((a["up"] > 0).mean()) if len(a) else None, second_half=float((z2["up"] > 0).mean()) if len(z2) else None)
            if row["first_half"] is not None and row["second_half"] is not None:
                row["stable"] = (row["first_half"] - 0.5) * (row["second_half"] - 0.5) > 0          # same side of 50 % in both halves
        out["buckets"].append(row)
    # an "edge" needs the shift to be significant, enough data AND the same direction in both halves; never claimed from a thin bucket
    out["edge_found"] = any(r["enough"] and r["z"] is not None and abs(r["z"]) >= 3 and r["stable"] for r in out["buckets"])
    thin = [r for r in out["buckets"] if not r["enough"]]
    out["note"] = (f"{len(thin)} of {len(out['buckets'])} sentiment ranges have fewer than {SAMPLES_FOR_3_POINTS} matched candles - too few to see a 3-point shift. "
                   "Keep collecting." if thin else None)
    return out
