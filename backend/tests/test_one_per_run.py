"""'one_per_run' filter: only the first signal of a back-to-back run is kept - same answer in the backtester and in live evaluation.
Run from backend/:  .venv\\Scripts\\python tests\\test_one_per_run.py"""
import copy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import evaluator, vec  # noqa: E402
from app.backtest import compute_trades  # noqa: E402
from app.builtins import BUILTINS  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.spec import validate_spec  # noqa: E402
from test_backtest import put_candles, streak_series  # noqa: E402

STREAK = next(b for b in BUILTINS if b["key"] == "candle-streak-reversal")["spec"]


def with_filter(gap=None, extra=None):
    s = copy.deepcopy(STREAK)
    s["filters"] = list(extra or []) + ([{"type": "one_per_run"}] if gap is None else [{"type": "one_per_run", "gap": gap}])
    return validate_spec(s)


def test_validation():
    ok = with_filter()
    assert ok["ok"] and ok["spec"]["filters"] == [{"type": "one_per_run", "gap": 1}]       # the default gap is 1
    assert with_filter(3)["ok"] and with_filter(5)["ok"]
    for bad in (0, 6, 1.5, True, "2", -1):
        v = with_filter(bad)
        assert not v["ok"] and any("one_per_run" in e and "gap" in e for e in v["errors"]), (bad, v["errors"])
    s = copy.deepcopy(STREAK)
    s["filters"] = [{"type": "one_per_run", "value": 1}]
    assert not validate_spec(s)["ok"]                                                         # unknown parameter is reported


def fail(direction, gap):
    d = np.array(direction)
    return vec._filter_fail({"type": "one_per_run", "gap": gap}, pd.DataFrame({"t": range(len(d))}), d, {}, None).astype(int).tolist()


def test_vector_rule_by_hand():
    d = [0, 1, 1, 1, 0, 1, -1, -1, 1, 0, 0, 1]
    assert fail(d, 1) == [0, 0, 1, 1, 0, 0, 0, 1, 0, 0, 0, 0]          # 2nd/3rd of the first run and 2nd of the SELL run; an opposite signal starts a new run
    assert fail(d, 2) == [0, 0, 1, 1, 0, 1, 0, 1, 0, 0, 0, 0]          # a one-candle hole no longer ends the run (index 5 is within 2 of index 3)
    assert fail(d, 3)[11] == 1                                          # ... and a two-candle hole is still bridged at gap 3 (index 8 -> 11 is 3 apart)
    assert fail([1], 1) == [0] and fail([], 1) == [] and fail([1, 1], 5) == [0, 1]


def _series_rows(eng_rows):
    return [{"t": r["t"], "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"], "n": r.get("n", 10)} for r in eng_rows]


def test_live_and_backtest_agree_candle_by_candle():
    rows = _series_rows(streak_series(420, 9, 0.5))
    df = pd.DataFrame([{"t": r["t"], "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"], "n": r["n"]} for r in rows])
    for gap in (None, 2):
        spec = with_filter(gap)["spec"]
        assert vec.evaluate_series(spec, df, 85.0)["dir"].abs().sum() > 5                    # the test really exercises signals
        want = vec.evaluate_series(spec, df, 85.0)["dir"].to_numpy()
        got = np.array([{"CALL": 1, "PUT": -1}.get(evaluator.evaluate(spec, rows[:i + 1], {}, {}).direction, 0) for i in range(len(rows))])
        assert (want == got).all(), (gap, np.flatnonzero(want != got)[:5])
    # without the filter the same series has back-to-back signals; with it the run starts survive
    plain = vec.evaluate_series(validate_spec(STREAK)["spec"], df, 85.0)["dir"].to_numpy()
    kept = vec.evaluate_series(with_filter()["spec"], df, 85.0)["dir"].to_numpy()
    starts = (plain != 0) & (plain != np.r_[0, plain[:-1]])
    assert (kept != 0).sum() == starts.sum() < (plain != 0).sum() and ((kept != 0) <= (plain != 0)).all()


def test_filter_order_matters_and_reason_is_readable():
    rows = _series_rows(streak_series(420, 9, 0.5))
    spec = with_filter()["spec"]
    reasons = {evaluator.evaluate(spec, rows[:i + 1], {}, {}).reason for i in range(60, len(rows))}
    assert any("one signal per run" in r for r in reasons), reasons
    # an earlier filter that removes the run's first signal lets the next one through (the filter judges only what reaches it)
    hours_block = {"type": "hours", "include": [], "exclude": ["00:00-23:59"]}
    s = with_filter(extra=[hours_block])["spec"]
    assert all(evaluator.evaluate(s, rows[:i + 1], {}, {}).direction == "SKIP" for i in range(60, 200))


def test_backtest_trades_drop_to_run_starts():
    eng = Engine(":memory:", seed_builtins=False)
    put_candles(eng, "S_otc", streak_series(3000, 41, 0.5))
    plain = compute_trades(eng.db, validate_spec(STREAK)["spec"], {"tfs": [60]}, {})
    one = compute_trades(eng.db, with_filter()["spec"], {"tfs": [60]}, {})
    assert 0 < len(one) < len(plain)
    plain = plain.sort_values("t").reset_index(drop=True)
    starts = plain[(plain.t.diff() != 60) | (plain.dir != plain.dir.shift())]
    assert sorted(one.t.tolist()) == sorted(starts.t.tolist())                               # exactly the first signal of each run


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
