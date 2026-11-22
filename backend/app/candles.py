"""Candle series, tick->candle building (by SERVER timestamps), history inference and aggregation."""
from __future__ import annotations

from collections import Counter

TFS = (60, 300, 900)
MAX_ROWS = 1500
# Markets that are only SHOWN (the crypto candles from Binance, see cryptofeed.py): their symbols end like this. They are charted and locked like any pair, but no strategy runs on them,
# no signal, alert or settlement comes from them, and the AI, the Edge Lab, the research and the sweeps do not learn from them.
DISPLAY_SUFFIXES = ("_bn",)


def is_display_only(sym: str | None) -> bool:
    return bool(sym) and sym.endswith(DISPLAY_SUFFIXES)


def bucket(ts: float, tf: int) -> int:
    return int(ts // tf * tf)


class Series:
    """Closed candles of one (symbol, timeframe), keyed by open time. Idempotent merging."""

    def __init__(self) -> None:
        self.rows: dict[int, dict] = {}

    def put(self, row: dict) -> None:
        old = self.rows.get(row["t"])
        if old is None or row.get("n", 0) >= old.get("n", 0):
            self.rows[row["t"]] = row
        if len(self.rows) > MAX_ROWS:
            for k in sorted(self.rows)[: len(self.rows) - MAX_ROWS]:
                del self.rows[k]

    def contiguous_tail(self, tf: int) -> list[dict]:
        """Newest run of candles with no missing bucket (indicators must not span gaps)."""
        times = sorted(self.rows)
        if not times:
            return []
        start = len(times) - 1
        while start > 0 and times[start] - times[start - 1] == tf:
            start -= 1
        return [self.rows[t] for t in times[start:]]

    def bridged_tail(self, tf: int, max_fill: int = 3) -> tuple[list[dict], bool]:
        """Newest candles, bridging holes of <= max_fill candles with interpolated flat 'synthetic' candles
        (n=0, syn=True). Returns (rows, recent_synthetic) where recent_synthetic = a synthetic candle is among
        the last 8 - callers must not issue signals then."""
        times = sorted(self.rows)
        if not times:
            return [], False
        rev = [self.rows[times[-1]]]
        for i in range(len(times) - 1, 0, -1):
            cur, prev = self.rows[times[i]], self.rows[times[i - 1]]
            missing = (times[i] - times[i - 1]) // tf - 1
            if missing > max_fill:
                break
            for j in range(missing, 0, -1):                 # newest synthetic first (we are building in reverse)
                px = prev["c"] + (cur["o"] - prev["c"]) * j / (missing + 1)
                rev.append({"t": times[i - 1] + tf * j, "o": px, "h": px, "l": px, "c": px, "n": 0, "syn": True})
            rev.append(prev)
        rows = rev[::-1]
        return rows, any(r.get("syn") for r in rows[-8:])

    def bridged_all(self, tf: int, max_fill: int = 3) -> list[dict]:
        """The WHOLE series in time order, holes of <= max_fill candles bridged with flat synthetic candles (as bridged_tail does); bigger holes stay (they split
        the series into uninterrupted stretches). For charts, which draw indicators over every stretch."""
        times = sorted(self.rows)
        out: list[dict] = []
        for i, t in enumerate(times):
            cur = self.rows[t]
            if i:
                prev = self.rows[times[i - 1]]
                missing = (t - times[i - 1]) // tf - 1
                if 0 < missing <= max_fill:
                    for j in range(1, missing + 1):
                        px = prev["c"] + (cur["o"] - prev["c"]) * j / (missing + 1)
                        out.append({"t": times[i - 1] + tf * j, "o": px, "h": px, "l": px, "c": px, "n": 0, "syn": True})
            out.append(cur)
        return out

    def last_gap(self, tf: int) -> dict | None:
        """Most recent hole in the series: {'after': t, 'before': t, 'missing': n candles}."""
        times = sorted(self.rows)
        for i in range(len(times) - 1, 0, -1):
            if times[i] - times[i - 1] != tf:
                return {"after": times[i - 1], "before": times[i], "missing": (times[i] - times[i - 1]) // tf - 1}
        return None

    def tail(self, k: int) -> list[dict]:
        return [self.rows[t] for t in sorted(self.rows)[-k:]]


def infer_tf(times: list[int]) -> int | None:
    ts = sorted(set(times))
    diffs = [b - a for a, b in zip(ts, ts[1:]) if b > a]
    return Counter(diffs).most_common(1)[0][0] if diffs else None


def aggregate(rows: list[dict], src_tf: int, dst_tf: int, ref_now: float) -> list[dict]:
    """Complete dst candles built from consecutive src candles (all sub-candles present, bucket already ended)."""
    if dst_tf % src_tf:
        return []
    need = dst_tf // src_tf
    groups: dict[int, list[dict]] = {}
    for r in sorted(rows, key=lambda x: x["t"]):
        groups.setdefault(bucket(r["t"], dst_tf), []).append(r)
    out = []
    for t0, g in groups.items():
        if len(g) != need or t0 + dst_tf > ref_now:
            continue
        if any(b["t"] - a["t"] != src_tf for a, b in zip(g, g[1:])):
            continue
        out.append({"t": t0, "o": g[0]["o"], "h": max(x["h"] for x in g), "l": min(x["l"] for x in g),
                    "c": g[-1]["c"], "n": sum(x.get("n", 0) for x in g)})
    return out


def rows_from_history(raw_rows: list[list]) -> list[dict]:
    """[time, open, close, high, low, ticks, lastTick] (platform order: open, CLOSE, high, low)."""
    out = []
    for r in raw_rows:
        try:
            out.append({"t": int(r[0]), "o": float(r[1]), "c": float(r[2]), "h": float(r[3]), "l": float(r[4]),
                        "n": int(r[5]) if len(r) > 5 and r[5] is not None else 0,
                        "last": float(r[6]) if len(r) > 6 and r[6] is not None else None})
        except (TypeError, ValueError, IndexError):
            continue
    return out
