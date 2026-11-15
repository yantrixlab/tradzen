"""Scroll-back history: batches older than what is stored must be saved, derived into 5m/15m, de-duplicated and exportable."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.engine import Engine  # noqa: E402

SYM = "GBPNZD_otc"
NOW = 1_794_400_000 // 900 * 900           # a 15-minute boundary


def raw(t0: int, n: int, tf: int = 60, price: float = 1.9) -> list[list]:
    """[time, open, close, high, low, ticks, lastTick] in platform order."""
    rows = []
    for i in range(n):
        o = price + 0.0001 * (i % 7)
        c = o + 0.00005
        rows.append([t0 + i * tf, o, c, c + 0.0002, o - 0.0002, 20, t0 + i * tf + tf - 1])
    return rows


def make() -> Engine:
    e = Engine(Path(tempfile.mkdtemp()) / "t.db")
    e.on_tick(SYM, NOW + 5.0, 1.9)                           # live ticks are flowing, so every history candle up to NOW is closed
    return e


def send(e: Engine, rows: list[list], asset: str = SYM) -> dict:
    e.ingest({"items": [{"k": "h", "asset": asset, "rows": rows}]})
    e._flush(force=True)
    return e.ingest_stats["hist"][-1]


def count(e: Engine, tf: int, src: str | None = None) -> int:
    q, a = "SELECT COUNT(*) FROM candles WHERE symbol=? AND tf=?", [SYM, tf]
    if src:
        q, a = q + " AND source=?", a + [src]
    return e.db.execute(q, a).fetchone()[0]


def test_scroll_back_far_beyond_the_in_memory_tail():
    e = make()
    send(e, raw(NOW - 300 * 60, 300))                       # the page's first load: last 300 one-minute candles
    assert count(e, 60) == 300
    far = NOW - 5000 * 60                                   # user scrolls back ~3.5 days: 2000 older candles, aligned to 15 min
    far -= far % 900
    note = send(e, raw(far, 2000))
    assert note["result"] == "seeded" and note["new"] == 2000, note
    assert count(e, 60) == 2300
    # 2000 min = 133 full 15m buckets + a partial one; the old tail-900 rule would have derived only the newest ~60
    assert count(e, 900, "derived") >= 130, count(e, 900, "derived")
    assert count(e, 300, "derived") >= 395, count(e, 300, "derived")


def test_overlapping_batches_report_only_new_rows():
    e = make()
    send(e, raw(NOW - 600 * 60, 300))
    note = send(e, raw(NOW - 700 * 60, 200))                # 100 of these overlap the first batch
    assert note["new"] == 100, note
    assert count(e, 60) == 400
    again = send(e, raw(NOW - 700 * 60, 200))
    assert again["new"] == 0, again


def test_unusual_timeframes_are_stored_but_not_traded():
    e = make()
    note = send(e, raw(NOW - 100 * 30, 80, tf=30))
    assert note["result"] == "stored (not traded)", note
    assert count(e, 30) == 80
    assert (SYM, 30) not in e.series
    bad = send(e, raw(NOW - 100 * 45, 20, tf=45))
    assert bad["result"].startswith("ignored"), bad


def test_gaps_and_export():
    e = make()
    rows = raw(NOW - 100 * 60, 100)
    del rows[40:45]                                          # five missing candles
    send(e, rows)
    g = e.gaps(SYM, 60)
    assert g["gaps"] == 1 and g["missing"] == 5 and g["holes"][0]["missing"] == 5, g
    out = list(e.export_rows(SYM, 60, None, None))
    assert len(out) == 95 and out[0][2] < out[-1][2]
    since = list(e.export_rows(SYM, 60, NOW - 10 * 60, None))
    assert all(r[2] >= NOW - 10 * 60 for r in since) and 0 < len(since) <= 10


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
