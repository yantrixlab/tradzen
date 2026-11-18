"""The hole before the running candle: Quotex's history answer holds server-aggregated candles that trail real time by a few minutes PLUS the raw ticks of those minutes.
The extension turns those ticks into candle rows (hook.js, candlesFromTicks); the engine must then show NO missing candles between the aggregated history and the live ticks -
the case of the screenshot (Quotex draws every candle up to the running one, Tradzen said "3 candles missing")."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.engine import Engine  # noqa: E402

SYM = "CHFJPY"
T0 = 1_794_400_020 // 60 * 60            # the running candle (23:38 in the screenshot)
PX = 189.0


def agg(t0: int, n: int) -> list[list]:
    """Server-aggregated rows [time, open, close, high, low, ticks, lastTick]."""
    return [[t0 + i * 60, PX + i * 0.01, PX + i * 0.01 + 0.005, PX + i * 0.01 + 0.012, PX + i * 0.01 - 0.003, 140, t0 + i * 60 + 59.5] for i in range(n)]


def from_ticks(t0: int, whole: int, running_secs: float = 9.0) -> list[list]:
    """What hook.js builds from the `history` ticks: `whole` finished minutes from t0 and the running minute after them."""
    rows = []
    for i in range(whole):
        b = t0 + i * 60
        rows.append([b, PX + 0.5 + i * 0.01, PX + 0.51 + i * 0.01, PX + 0.53 + i * 0.01, PX + 0.49 + i * 0.01, 141, b + 59.6])
    b = t0 + whole * 60
    rows.append([b, PX + 0.6, PX + 0.61, PX + 0.62, PX + 0.59, int(running_secs * 2.4), b + running_secs])
    return rows


def make() -> Engine:
    return Engine(Path(tempfile.mkdtemp()) / "t.db")


def send(e: Engine, rows: list[list]) -> dict:
    e.ingest({"items": [{"k": "h", "asset": SYM, "rows": rows}]})
    e._flush(force=True)
    return e.ingest_stats["hist"][-1]


def live(e: Engine, start: float, secs: float) -> None:
    n = int(secs * 2)
    for i in range(n):
        e.on_tick(SYM, start + i * 0.5, PX + 0.6 + (i % 7) * 0.001)


def stored(e: Engine, tf: int = 60) -> list[int]:
    return [r[0] for r in e.db.execute("SELECT ts FROM candles WHERE symbol=? AND tf=? ORDER BY ts", (SYM, tf))]


def test_without_the_ticks_the_last_three_candles_are_missing():
    """The old behaviour, kept as the reproduction: the aggregated history ends 4 minutes before the running candle."""
    e = make()
    send(e, agg(T0 - 40 * 60, 37) + [[T0, PX, PX, PX, PX, 1, T0 + 1.0]])        # candles up to T0-4 minutes, then the running one
    live(e, T0 + 2.0, 8)
    chart = e.chart_of(SYM, 60)
    assert chart["forming"]["t"] == T0
    assert (chart["forming"]["t"] - chart["candles"][-1]["t"]) // 60 - 1 == 3, "the chart draws '3 candles missing' before the running candle"


def test_history_ticks_close_the_hole_before_the_running_candle():
    e = make()
    rows = agg(T0 - 40 * 60, 37) + from_ticks(T0 - 3 * 60, 3)                # aggregated up to T0-4 min; the 3 minutes after it come from the ticks, then the running one
    note = send(e, rows)
    live(e, T0 + 10.0, 8)                                                     # the live stream carries on from the history
    chart = e.chart_of(SYM, 60)
    assert chart["gap"] is None, chart["gap"]
    times = [c["t"] for c in chart["candles"]]
    assert times == list(range(T0 - 40 * 60, T0, 60)), "every minute up to the running one is a candle"
    assert chart["forming"]["t"] == T0
    assert note["result"] == "seeded" and note["new"] == 40, note
    assert stored(e) == list(range(T0 - 40 * 60, T0, 60)), "stored == shown"


def test_the_running_candle_continues_from_the_history_ticks():
    e = make()
    send(e, agg(T0 - 10 * 60, 7) + from_ticks(T0 - 3 * 60, 3, running_secs=9.0))
    live(e, T0 + 9.5, 10)
    f = e.chart_of(SYM, 60)["forming"]
    assert f["t"] == T0 and f["o"] == PX + 0.6, "the open comes from the history ticks, not from the first live tick"
    assert f["n"] > 21


def test_live_candle_joined_mid_minute_is_completed_by_the_history_ticks():
    e = make()
    live(e, T0 + 40.0, 6)                                                     # the tab was opened late in the minute: the live candle is partial
    send(e, agg(T0 - 10 * 60, 7) + from_ticks(T0 - 3 * 60, 3, running_secs=39.0))
    chart = e.chart_of(SYM, 60)
    assert chart["gap"] is None
    assert chart["forming"]["o"] == PX + 0.6, "the open of the running candle is now known"


def test_a_candle_the_server_already_has_is_not_replaced():
    e = make()
    send(e, agg(T0 - 10 * 60, 7) + [[T0, PX, PX, PX, PX, 1, T0 + 1.0]])
    before = e.db.execute("SELECT o, h, l, c, ticks FROM candles WHERE symbol=? AND tf=60 AND ts=?", (SYM, T0 - 10 * 60)).fetchone()
    bad = from_ticks(T0 - 10 * 60, 1)                                         # same minute again, with fewer ticks and other prices
    bad[0][5] = 5
    send(e, bad + agg(T0 - 4 * 60, 1) + from_ticks(T0 - 3 * 60, 3))
    assert e.db.execute("SELECT o, h, l, c, ticks FROM candles WHERE symbol=? AND tf=60 AND ts=?", (SYM, T0 - 10 * 60)).fetchone() == before


def test_higher_timeframes_are_built_over_the_filled_hole():
    e = make()
    t5 = (T0 - 40 * 60) // 900 * 900 + 900                                    # a 15-minute boundary
    n = (T0 - 3 * 60 - t5) // 60
    send(e, agg(t5, n) + from_ticks(T0 - 3 * 60, 3))
    assert stored(e, 300) == list(range(t5, T0 - T0 % 300, 300)), stored(e, 300)       # every 5-minute candle that is whole and finished
    assert not [h for h in e.gaps(SYM, 60)["holes"]], "no hole in the stored minutes"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
