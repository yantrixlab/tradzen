"""Looking at and removing the stored candles (admin only): what is in the candle store per pair / timeframe / where it came from, whether a stretch of it looks wrong, and a deletion
that cannot happen by accident.

  inventory()  per (pair, timeframe, kind of source): how many candles, from / to, how many are flat (open = high = low = close, a tick-less or interpolated candle)
               kinds: tick (built from the live price feed), history (the Quotex chart's own history), contrib (shared by another account), yahoo (the real-market feed behind
               the <PAIR>_mkt pairs), derived (5m / 15m candles built from the 1m ones), other
  check()      one series examined candle by candle: broken shapes (high below the body ...), spikes (a range many times the pair's usual one), jumps between a candle's close and
               the next open, flat candles - with the worst examples, so a bad feed shows up as a list of times to look at
  delete()     the removal. A dry run (the default) only counts. A real one writes every removed candle to a gzip CSV first (data/deleted/), removes them (and the 5m / 15m candles
               built from removed 1m ones when asked), and reloads the live memory of the affected series. Removing "everything of every pair" is refused: it takes a filter.
"""
from __future__ import annotations

import csv
import gzip
import time
from pathlib import Path

import numpy as np

KINDS = ("tick", "history", "contrib", "yahoo", "derived", "other")
_KIND_SQL = ("CASE WHEN source LIKE 'tick%' THEN 'tick' WHEN source LIKE 'contrib:%' THEN 'contrib' WHEN source LIKE 'yahoo%' THEN 'yahoo' "
             "WHEN source IN ('history', 'derived') THEN source ELSE 'other' END")
SPIKE_X = 6.0            # a range this many times the pair's usual one is a spike
JUMP_X = 8.0             # an open this many usual ranges away from the previous close is a jump
WORST = 15


def _kind_filter(kind: str | None) -> tuple[str, list]:
    if not kind:
        return "", []
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {list(KINDS)}")
    return f" AND {_KIND_SQL} = ?", [kind]


def inventory(engine) -> list[dict]:
    engine._flush(force=True)
    rows = engine.db.execute(
        f"SELECT symbol, tf, {_KIND_SQL} AS kind, COUNT(*), MIN(ts), MAX(ts), SUM(o = h AND h = l AND l = c) FROM candles GROUP BY symbol, tf, kind ORDER BY symbol, tf, kind").fetchall()
    return [{"symbol": s, "tf": tf, "kind": k, "candles": n, "first": t0, "last": t1, "flat": int(flat or 0), "flat_share": round((flat or 0) / n, 4) if n else 0.0}
            for s, tf, k, n, t0, t1, flat in rows]


def check(engine, symbol: str, tf: int, kind: str | None = None, limit: int = 30000) -> dict:
    """The newest `limit` candles of one series, examined. Nothing is changed."""
    engine._flush(force=True)
    kf, kp = _kind_filter(kind)
    rows = engine.db.execute(f"SELECT ts, o, h, l, c FROM candles WHERE symbol=? AND tf=?{kf} ORDER BY ts DESC LIMIT ?", (symbol, tf, *kp, limit)).fetchall()[::-1]
    out: dict = {"symbol": symbol, "tf": tf, "kind": kind, "examined": len(rows)}
    if len(rows) < 5:
        out["note"] = "too few candles to judge"
        return out
    a = np.array(rows, float)
    t, o, h, l, c = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]
    rng = h - l
    usual = float(np.median(rng[rng > 0])) if (rng > 0).any() else 0.0
    out["usual_range"] = usual
    bad_shape = (h < np.maximum(o, c) * (1 - 1e-9)) | (l > np.minimum(o, c) * (1 + 1e-9)) | ~np.isfinite(a[:, 1:]).all(axis=1) | (a[:, 1:] <= 0).any(axis=1)
    flat = (o == h) & (h == l) & (l == c)
    out["broken_shape"] = int(bad_shape.sum())
    out["flat"] = int(flat.sum())
    out["flat_share"] = round(float(flat.mean()), 4)
    ex = lambda i: {"t": int(t[i]), "o": float(o[i]), "h": float(h[i]), "l": float(l[i]), "c": float(c[i])}      # noqa: E731
    if usual > 0:
        spike = rng > SPIKE_X * usual
        ratio = rng / usual
        jump = np.zeros(len(a), bool)
        jump[1:] = np.abs(o[1:] - c[:-1]) > JUMP_X * usual
        contiguous = np.zeros(len(a), bool)
        contiguous[1:] = (t[1:] - t[:-1]) == tf
        jump &= contiguous
        out["spikes"] = int(spike.sum())
        out["jumps"] = int(jump.sum())
        out["worst_spikes"] = [{**ex(i), "times_usual": round(float(ratio[i]), 1)} for i in np.argsort(-ratio)[:WORST] if spike[i]]
        out["worst_jumps"] = [{**ex(i), "gap_from_prev_close": float(abs(o[i] - c[i - 1])), "times_usual": round(float(abs(o[i] - c[i - 1]) / usual), 1)}
                              for i in np.argsort(-np.where(jump, np.abs(o - np.concatenate(([c[0]], c[:-1]))), 0))[:WORST] if jump[i]]
    out["worst_broken"] = [ex(i) for i in np.flatnonzero(bad_shape)[:WORST]]
    bad = int(bad_shape.sum()) + out.get("spikes", 0) + out.get("jumps", 0)
    out["verdict"] = ("looks clean" if bad == 0 and out["flat_share"] < 0.05 else
                      "many flat candles: a feed that stored one price per candle" if out["flat_share"] >= 0.3 else
                      "suspect candles found: look at the examples")
    return out


def _where(symbol: str, tf: int | None, kind: str | None, since: int | None, until: int | None, with_derived: bool) -> tuple[str, list]:
    """WHERE clause of the removal. With `with_derived` and tf == 60 the 5m / 15m candles that overlap the removed stretch go too (the source filter then names the 1m candles)."""
    parts: list[str] = []
    params: list = []
    if symbol != "*":
        parts.append("symbol = ?")
        params.append(symbol)

    def stretch(t_tf: int | None) -> tuple[list[str], list]:
        sub: list[str] = []
        sp: list = []
        if t_tf:
            sub.append("tf = ?")
            sp.append(t_tf)
        if since is not None:
            sub.append("ts >= ?")
            sp.append(since - (t_tf - 60) if t_tf and t_tf > 60 else since)        # a 5m / 15m candle that starts before `since` still overlaps it
        if until is not None:
            sub.append("ts <= ?")
            sp.append(until)
        return sub, sp

    if with_derived and tf == 60:
        ors: list[str] = []
        for t in (60, 300, 900):
            sub, sp = stretch(t)
            if t == 60 and kind:
                sub.append(f"{_KIND_SQL} = ?")
                sp.append(kind)
            ors.append("(" + " AND ".join(sub) + ")")
            params += sp
        parts.append("(" + " OR ".join(ors) + ")")
    else:
        sub, sp = stretch(tf)
        parts += sub
        params += sp
        if kind:
            parts.append(f"{_KIND_SQL} = ?")
            params.append(kind)
    return " AND ".join(parts) or "1=1", params


def delete(engine, archive_dir: Path, symbol: str, tf: int | None = None, kind: str | None = None, since: int | None = None, until: int | None = None,
           with_derived: bool = True, dry_run: bool = True) -> dict:
    """Count (dry_run) or remove the candles matching the filter. `symbol` = '*' means every pair and then needs another filter."""
    if not symbol:
        raise ValueError("name a pair (or * for every pair together with another filter)")
    if symbol == "*" and not (tf or kind or since is not None or until is not None):
        raise ValueError("every candle of every pair cannot be removed in one go: add a timeframe, a source or a time range (or use System -> Import data to replace the database)")
    if tf is not None and tf not in (60, 300, 900):
        raise ValueError("timeframe must be 60, 300 or 900")
    if since is not None and until is not None and since > until:
        raise ValueError("'from' is after 'to'")
    engine._flush(force=True)
    where, params = _where(symbol, tf, kind, since, until, with_derived)
    groups = engine.db.execute(f"SELECT symbol, tf, {_KIND_SQL} AS kind, COUNT(*) FROM candles WHERE {where} GROUP BY symbol, tf, kind ORDER BY symbol, tf", params).fetchall()
    total = sum(g[3] for g in groups)
    res: dict = {"dry_run": dry_run, "total": total, "groups": [{"symbol": s, "tf": t, "kind": k, "candles": n} for s, t, k, n in groups][:200],
                 "refetch": any(s.endswith("_mkt") for s, *_ in groups)}
    if dry_run or total == 0:
        return res
    archive_dir.mkdir(parents=True, exist_ok=True)
    name = f"candles-{time.strftime('%Y%m%d-%H%M%S')}-{(symbol if symbol != '*' else 'all').replace('/', '_')[:30]}.csv.gz"
    path = archive_dir / name
    with gzip.open(path, "wt", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "tf", "time", "open", "high", "low", "close", "ticks", "source"])
        for row in engine.db.execute(f"SELECT symbol, tf, ts, o, h, l, c, ticks, source FROM candles WHERE {where} ORDER BY symbol, tf, ts", params):
            w.writerow(row)
    keys = {(s, t) for s, t, _, _ in groups}
    cur = engine.db.execute(f"DELETE FROM candles WHERE {where}", params)
    engine.db.commit()
    for key in keys:                                                   # the live memory of those series is rebuilt from what is left
        engine.series.pop(key, None)
        for ts, o, h, l, c, n in engine.db.execute("SELECT ts, o, h, l, c, ticks FROM candles WHERE symbol=? AND tf=? ORDER BY ts DESC LIMIT 800", key).fetchall():
            engine._series(key[0], key[1]).put({"t": ts, "o": o, "h": h, "l": l, "c": c, "n": n or 0})
    res.update(removed=cur.rowcount, archive=name)
    return res
