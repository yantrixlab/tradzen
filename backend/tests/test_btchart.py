"""Backtest-lab charts: candles + the strategy's trades for one pair, live replay vs a stored run must agree."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from app.backtest import BacktestError  # noqa: E402
from app.engine import Engine  # noqa: E402
from test_backtest import put_candles, streak_series  # noqa: E402


def make():
    eng = Engine(":memory:", seed_builtins=True)
    put_candles(eng, "S_otc", streak_series(3000, 41, 0.8))
    sid = next(s["id"] for s in eng.repo.list() if s["key"] == "candle-streak-reversal")
    return eng, sid


def test_candles_only():
    eng, _ = make()
    d = eng.backtests.chart("S_otc", 60)
    assert d["total"] == 3000 and len(d["candles"]) == 3000 and d["trades"] == [] and d["strategy"] is None
    assert d["candles"][0][0] < d["candles"][-1][0] and len(d["candles"][0]) == 5       # t, o, h, l, c
    assert len(eng.backtests.chart("S_otc", 60, limit=500)["candles"]) == 500          # newest 500 only
    assert eng.backtests.chart("NOPE", 60)["candles"] == []


def test_live_replay_matches_a_stored_run():
    eng, sid = make()
    live = eng.backtests.chart("S_otc", 60, strategy_id=sid, limit=20000)
    assert len(live["trades"]) > 50 and live["strategy"].startswith("Candle Streak") and live["payout"]
    run_id = eng.backtests.start({"strategy_id": sid, "symbols": ["S_otc"], "tfs": [60]}, sync=True)
    stored = eng.backtests.chart("S_otc", 60, run_id=run_id, limit=20000)
    key = lambda t: (t["t"], t["dir"], t["result"])  # noqa: E731
    assert [key(t) for t in live["trades"]] == [key(t) for t in stored["trades"]]
    assert stored["split_t"] is not None and live["split_t"] is None                     # only a stored run has a validation split
    t = live["trades"][0]
    assert t["result"] in ("win", "loss", "draw") and t["dir"] in (-1, 1) and t["entry"] and t["exit"]


def test_trades_are_limited_to_the_loaded_window():
    eng, sid = make()
    d = eng.backtests.chart("S_otc", 60, strategy_id=sid, limit=400)
    t0 = d["candles"][0][0]
    assert d["trades"] and all(t["t"] >= t0 for t in d["trades"])


def test_errors_and_notes():
    eng, sid = make()
    d = eng.backtests.chart("S_otc", 900, strategy_id=sid)                               # no stored 15m candles
    assert d["candles"] == []
    try:
        eng.backtests.chart("S_otc", 60, run_id=9999)
        raise AssertionError("expected an error")
    except BacktestError:
        pass


def test_chart_rows_come_from_history_not_only_from_memory():
    """Regression: the indicator chart read only the candles the server held in memory, so any pair that was not streaming could not be charted."""
    eng, _ = make()
    assert len(eng._series("S_otc", 60).rows) == 0                         # nothing streamed: memory is empty ...
    rows, syn = eng.chart_rows("S_otc", 60)
    assert len(rows) == 600 and not syn and rows[0]["t"] < rows[-1]["t"]     # ... yet 600 stored candles are available for the chart
    assert eng.chart_rows("NOPE", 60)[0] == []
    eng._series("S_otc", 60).put({"t": rows[-1]["t"], "o": 9.0, "h": 9.5, "l": 8.5, "c": 9.2, "n": 99})       # a fresher candle in memory wins
    assert eng.chart_rows("S_otc", 60)[0][-1]["o"] == 9.0


def test_chart_rows_all_returns_more_than_the_live_memory_cap():
    """Regression: the chart's indicators stopped after the newest 1500 candles (the live series' cap) although 4000 were loaded and drawn."""
    eng, _ = make()
    rows = eng.chart_rows_all("S_otc", 60, 2500)
    assert len(rows) == 2500 and rows[0]["t"] < rows[-1]["t"] and all(rows[i + 1]["t"] - rows[i]["t"] == 60 for i in range(len(rows) - 1))
    assert eng.chart_rows_all("NOPE", 60, 100) == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
