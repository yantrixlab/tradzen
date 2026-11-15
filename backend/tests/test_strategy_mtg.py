"""One-step martingale column of the Strategies page: computed from each strategy's real settled signals, cached, refreshed in the background.
Run from backend/:  .venv\\Scripts\\python tests\\test_strategy_mtg.py"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from app import engine as engine_mod  # noqa: E402
from app import mtg  # noqa: E402
from app.engine import Engine  # noqa: E402
from test_backtest import put_candles  # noqa: E402

T0 = 1_700_000_040
TF = 60
UP, DOWN, FLAT = (1.0, 1.1), (1.1, 1.0), (1.0, 1.0)


def make():
    """candle k opens at T0 + k*60. Shapes: 1 up, 2 down, 4 up, 5 up, 7 down, 10 flat, everything else flat."""
    eng = Engine(":memory:", seed_builtins=False)
    shape = {1: UP, 2: DOWN, 4: UP, 5: UP, 7: DOWN, 10: FLAT, 13: UP, 14: UP, 16: DOWN}
    rows = []
    for k in range(0, 24):
        o, c = shape.get(k, FLAT)
        rows.append({"t": T0 + k * TF, "o": o, "h": max(o, c) + 0.01, "l": min(o, c) - 0.01, "c": c, "n": 10})
    put_candles(eng, "H_otc", rows)
    return eng


def add(eng, sid, k, direction, status):
    """a signal issued when candle k closes (basis_t = candle k's open time), entering candle k+1"""
    eng.db.execute("INSERT INTO signals (created, symbol, tf, basis_t, target_t, direction, status, strategy_id) VALUES (?,?,?,?,?,?,?,?)",
                   (T0 + k * TF, "H_otc", TF, T0 + k * TF, T0 + (k + 1) * TF, direction, status, sid))
    eng.db.commit()


def test_summary_by_hand_and_per_strategy():
    eng = make()
    # strategy 1: SELLs. k=0: entry c1 up = loss, c2 down = RESCUED.  k=3: c4 up = loss, c5 up = LOST.  k=6: c7 down = win.  k=9: c10 flat = draw.
    for k, st in ((0, "loss"), (3, "loss"), (6, "win"), (9, "draw")):
        add(eng, 1, k, "PUT", st)
    add(eng, 1, 12, "PUT", "pending")                                  # not settled yet: left out
    add(eng, 1, 15, "SKIP", "loss")                                    # not a trade: left out
    add(eng, 1, 18, "PUT", "void")                                     # voided: left out
    # strategy 2 is separate: a BUY at k=12 enters c13 (up = win)
    add(eng, 2, 12, "CALL", "win")
    out = mtg.signals_summary(eng.db, {"H_otc": 90.0})
    s1, s2 = out[1], out[2]
    assert (s1["taken"], s1["skipped"], s1["wins"], s1["rescued"], s1["lost"], s1["draws"], s1["open"]) == (4, 0, 1, 1, 1, 1, 0)
    assert abs(s1["accuracy"] - 2 / 3) < 1e-9                         # (1 win + 1 rescued) / 3 decided; the draw is left out
    assert abs(s1["net"] - ((2.2 * 0.9 - 1) + (-3.2) + 0.9 + 0.0)) < 1e-3
    assert (s2["taken"], s2["wins"], s2["accuracy"]) == (1, 1, 1.0)
    assert set(out) == {1, 2}                                          # strategies without a settled signal are absent
    assert mtg.signals_summary(make().db, {}) == {}


def test_a_signal_inside_a_running_ladder_cannot_be_taken_and_one_cache_serves_all():
    eng = make()
    add(eng, 1, 0, "PUT", "loss")                                      # its ladder uses candle 2
    add(eng, 1, 1, "PUT", "win")                                       # fires while that ladder is running -> skipped
    add(eng, 2, 0, "PUT", "loss")                                      # another strategy is unaffected
    add(eng, 2, 1, "PUT", "win")
    out = mtg.signals_summary(eng.db, {})
    assert out[1]["taken"] == 1 and out[1]["skipped"] == 1 and out[1]["rescued"] == 1 and out[1]["signals"] == 2
    assert out[2] == out[1]                                            # same signals, same answer: independent per strategy, shared candle cache
    low = mtg.signals_summary(eng.db, {"H_otc": 77.0})
    assert low[1]["net"] < out[1]["net"]                               # a lower payout pays less for the re-entry's win ...
    assert low[1]["rescued"] == out[1]["rescued"] and low[1]["accuracy"] == out[1]["accuracy"]      # ... but never changes the counts


def test_strategies_list_carries_it_and_refreshes_only_when_stale():
    eng = make()
    eng.repo.ensure_builtins(include_extra=True)
    assert len(eng.repo.list()) > 3
    sid = eng.repo.list()[0]["id"]
    for k, st in ((0, "loss"), (3, "loss"), (6, "win")):
        add(eng, sid, k, "PUT", st)
    row = next(r for r in eng.strategy_summary() if r["id"] == sid)
    assert row["mtg1"]["taken"] == 3 and abs(row["mtg1"]["accuracy"] - 2 / 3) < 1e-9 and row["mtg1_pending"] is False
    other = next(r for r in eng.strategy_summary() if r["id"] != sid)
    assert other["mtg1"] is None                                       # no settled signals: nothing to show (the page prints a dash)
    stamp = eng._mtg_at
    add(eng, sid, 12, "PUT", "loss")                                   # a new settled signal ...
    again = next(r for r in eng.strategy_summary() if r["id"] == sid)
    assert eng._mtg_at == stamp and again["mtg1"]["taken"] == 3         # ... is not picked up on every poll (cached)
    eng._mtg_at = time.time() - engine_mod.MTG_TTL - 1                 # the cache goes stale
    fresh = next(r for r in eng.strategy_summary() if r["id"] == sid)
    assert fresh["mtg1"]["taken"] == 4


def test_api_exposes_it():
    import os
    import tempfile

    from fastapi.testclient import TestClient
    with tempfile.TemporaryDirectory() as d:
        os.environ["QXS_DB"] = str(Path(d) / "smtg.db")
        os.environ["QXS_RESEARCH_HOUR"] = "off"
        sys.modules.pop("app.main", None)
        import app.main as main
        with TestClient(main.app) as c:
            first = c.get("/api/strategies").json()
            assert all("mtg1" in s and "mtg1_pending" in s for s in first)
        os.environ.pop("QXS_RESEARCH_HOUR", None)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
