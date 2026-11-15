"""Clear-all for the backtest lab: removes every run and its trades, nothing else, and refuses while a run is going.
Run from backend/:  .venv\\Scripts\\python tests\\test_backtest_clear.py"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from app.engine import Engine  # noqa: E402
from test_backtest import put_candles, streak_series  # noqa: E402


def _engine_with_runs():
    eng = Engine(":memory:")
    put_candles(eng, "T_otc", streak_series(900, 3, 0.5, 1_700_000_000, 60))
    for _ in range(2):
        eng.backtests.start({"strategy_id": eng.strategy_id, "tfs": [60]}, sync=True)
    return eng


def test_clear_all_deletes_every_run_and_trade_but_nothing_else():
    eng = _engine_with_runs()
    q = lambda sql: eng.db.execute(sql).fetchone()[0]  # noqa: E731
    candles, strategies = q("SELECT COUNT(*) FROM candles"), q("SELECT COUNT(*) FROM strategies")
    assert q("SELECT COUNT(*) FROM backtest_runs") == 2 and q("SELECT COUNT(*) FROM backtest_trades") > 0
    out = eng.backtests.clear_all()
    assert out["runs"] == 2 and out["trades"] > 0
    assert q("SELECT COUNT(*) FROM backtest_runs") == 0 and q("SELECT COUNT(*) FROM backtest_trades") == 0
    assert q("SELECT COUNT(*) FROM candles") == candles and q("SELECT COUNT(*) FROM strategies") == strategies
    assert eng.backtests.list_runs(50) == []
    assert eng.backtests.clear_all() == {"runs": 0, "trades": 0, "insights": 0}              # clearing nothing is fine


def test_clear_all_is_refused_while_a_backtest_is_running():
    eng = _engine_with_runs()
    eng.db.execute("UPDATE backtest_runs SET status='running' WHERE id=(SELECT MIN(id) FROM backtest_runs)")
    eng.db.commit()
    try:
        eng.backtests.clear_all()
        raise AssertionError("should have refused")
    except ValueError as e:
        assert "still running" in str(e)
    assert eng.db.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0] == 2


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
