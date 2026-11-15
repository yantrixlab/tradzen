"""The AI momentum strategy as an automatic signal source: it records a (paper) signal on every closed candle of a streaming pair once it has learned, never blocks the
candle handler while learning, waits out data gaps, and honours QXS_AI_MIN_GRADE.  Run from backend/:  .venv\\Scripts\\python tests\\test_momentum_auto.py"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from app import momentum_ai as M  # noqa: E402
from app.engine import Engine  # noqa: E402
from test_backtest import put_candles  # noqa: E402
from test_momentum_ai import T0, waves  # noqa: E402


def _engine():
    eng = Engine(":memory:", seed_builtins=True)
    eng.instruments["T_otc"] = {"symbol": "T_otc", "name": "T", "payout": 88, "open": True, "market": "OTC"}
    rows = waves(9300, 7, 0.5)
    put_candles(eng, "T_otc", rows[:9000])
    s = eng._series("T_otc", 60)
    for r in rows[8600:9000]:
        s.put(r)
    for st in eng.repo.list():                                # only the AI source is under test
        if st["key"] != M.AI_KEY:
            eng.set_strategy_status(st["id"], "paused")
    return eng, rows


def _ai_signals(eng):
    sid = next(s["id"] for s in eng.repo.list() if s["key"] == M.AI_KEY)
    return sid, [s for s in eng.signals if s["strategy_id"] == sid and s["direction"] != "SKIP"]


def test_the_ai_source_exists_as_a_paper_strategy_for_one_minute_candles():
    eng, _ = _engine()
    d = next(s for s in eng.repo.list() if s["key"] == M.AI_KEY)
    assert d["status"] == "paused" or d["status"] == "paper"
    spec = eng.repo.get(d["id"])["spec"]
    assert spec["timeframes"] == [60]


def test_every_closed_candle_gets_an_ai_signal_with_the_rule_in_its_factors():
    eng, rows = _engine()
    sid = next(s["id"] for s in eng.repo.list() if s["key"] == M.AI_KEY)
    eng.set_strategy_status(sid, "paper")
    for r in rows[9000:9004]:
        eng._series("T_otc", 60).put(r)
        eng._run_strategies("T_otc", 60, r)
    _, sigs = _ai_signals(eng)
    assert len(sigs) == 4, [s["reason"] for s in eng.signals if s["strategy_id"] == sid]
    s = sigs[-1]
    assert s["direction"] in ("CALL", "PUT") and s["mode"] == "paper" and s["factors"][0]["key"] == "ai_rule" and "unseen candles" in s["factors"][0]["note"]
    assert s["tier"] == "B" and "grade C" in s["reason"]                              # a fair coin: the AI's own test finds no edge and the record says so


def test_a_minimum_grade_silences_the_unproven_ai_and_a_data_gap_pauses_it():
    eng, rows = _engine()
    sid = next(s["id"] for s in eng.repo.list() if s["key"] == M.AI_KEY)
    eng.set_strategy_status(sid, "paper")
    os.environ["QXS_AI_MIN_GRADE"] = "B"
    try:
        r = rows[9000]
        eng._series("T_otc", 60).put(r)
        eng._run_strategies("T_otc", 60, r)
        assert _ai_signals(eng)[1] == []                                              # grade C is below B: no signal
    finally:
        os.environ.pop("QXS_AI_MIN_GRADE", None)
    res = M.auto_result(eng, "T_otc", 60, rows[8600:9001], syn=True)
    assert res.direction == "SKIP" and "gap" in res.reason


def test_a_pair_with_only_a_few_candles_after_a_gap_is_skipped_not_an_error():
    eng, rows = _engine()
    res = M.auto_result(eng, "T_otc", 60, rows[8995:9000], syn=False)               # 5 candles: used to crash the indicator arithmetic
    assert res.direction == "SKIP" and "needs 60" in res.reason
    fr = M.momentum_frame(__import__("test_momentum_ai").frame(rows[8990:8995]))     # and the measurements themselves work on tiny series
    assert len(fr) == 5


def test_while_the_model_is_still_learning_the_candle_handler_is_not_blocked():
    eng, rows = _engine()
    eng.db_path = "x:/not-a-real.db"                          # a file-database engine: learning must go to a background thread
    M._cache.clear()
    res = M.auto_result(eng, "T_otc", 60, rows[8600:9001], syn=False)
    assert res.direction == "SKIP" and "learning" in res.reason
    for _ in range(50):                                       # the worker fails to open the fake path and releases its slot
        if not M._building:
            break
        import time
        time.sleep(0.1)
    assert not M._building


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
