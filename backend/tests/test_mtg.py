"""Martingale ladders: hand-checked numbers, skipped signals, open ladders, and agreement with the chart endpoint.
Run from backend/:  .venv\\Scripts\\python tests\\test_mtg.py"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from app import mtg  # noqa: E402
from app.backtest import BacktestError  # noqa: E402
from app.engine import Engine  # noqa: E402
from test_backtest import put_candles, streak_series  # noqa: E402

TF = 60
UP, DOWN, FLAT = (1.0, 1.1), (1.1, 1.0), (1.0, 1.0)          # (open, close)


def cmap(*seq):
    """candle at time k*60 for each (open, close) in seq"""
    return {i * TF: c for i, c in enumerate(seq)}


def trade(t, d, res):
    return {"t": t, "dir": d, "result": res}


def test_ladder_outcomes_by_hand():
    pay, p = 85.0, 0.85
    # SELL signalled on candle 0; candle 1 (entry) rises = loss; candle 2 falls = MTG-1 win
    lad = mtg.ladder(cmap(UP, UP, DOWN, UP), 0, -1, TF, "loss", pay, 2, 2.2)
    assert lad["outcome"] == "mtg1" and [s["t"] for s in lad["steps"]] == [120] and abs(lad["pl"] - (2.2 * p - 1)) < 1e-9
    # candle 2 rises too, candle 3 falls = MTG-2 win
    lad = mtg.ladder(cmap(UP, UP, UP, DOWN), 0, -1, TF, "loss", pay, 2, 2.2)
    assert lad["outcome"] == "mtg2" and abs(lad["pl"] - (2.2 ** 2 * p - (1 + 2.2))) < 1e-9 and abs(lad["stake_max"] - 4.84) < 1e-9
    # all three lose
    lad = mtg.ladder(cmap(UP, UP, UP, UP), 0, -1, TF, "loss", pay, 2, 2.2)
    assert lad["outcome"] == "lost" and abs(lad["pl"] + 8.04) < 1e-9 and lad["end_t"] == 180
    # a draw refunds that stake and ends the ladder
    lad = mtg.ladder(cmap(UP, UP, FLAT, DOWN), 0, -1, TF, "loss", pay, 2, 2.2)
    assert lad["outcome"] == "draw" and abs(lad["pl"] + 1.0) < 1e-9 and len(lad["steps"]) == 1
    # the next candle is not stored yet -> open, not guessed
    lad = mtg.ladder(cmap(UP, UP), 0, -1, TF, "loss", pay, 2, 2.2)
    assert lad["outcome"] == "open" and lad["pl"] == 0.0
    # a won or drawn first trade needs no ladder
    assert mtg.ladder(cmap(DOWN, DOWN), 0, -1, TF, "win", pay, 2, 2.2)["pl"] == p
    assert mtg.ladder(cmap(FLAT, FLAT), 0, -1, TF, "draw", pay, 2, 2.2)["pl"] == 0.0
    # BUY works the same way mirrored; steps=0 means a loss is simply lost
    assert mtg.ladder(cmap(DOWN, DOWN, UP), 0, 1, TF, "loss", pay, 2, 2.2)["outcome"] == "mtg1"
    assert mtg.ladder(cmap(UP, UP, DOWN), 0, -1, TF, "loss", pay, 0, 2.2)["outcome"] == "lost"


def test_signals_inside_a_ladder_are_skipped_not_double_counted():
    candles = cmap(UP, UP, DOWN, DOWN, UP, UP, DOWN)
    trades = [trade(0, -1, "loss"),      # SELL on candle 0: entry c1 rises = loss -> ladder uses c2 (falls = MTG-1 win)
              trade(60, -1, "win"),      # fires while the ladder is running -> cannot be taken
              trade(120, -1, "win"),     # fires when candle 2 (the MTG candle) closes -> free again
              ]
    s = mtg.apply(trades, candles, TF, 85.0, 2, 2.2)
    assert trades[1].get("mtg_skip") and not trades[0].get("mtg_skip") and not trades[2].get("mtg_skip")
    assert trades[0]["mtg"]["outcome"] == "mtg1" and "mtg" not in trades[2]
    assert s["signals"] == 3 and s["taken"] == 2 and s["skipped"] == 1 and s["rescued"] == {"mtg1": 1, "mtg2": 0}
    assert abs(s["ladder_pl"] - ((2.2 * 0.85 - 1) + 0.85)) < 1e-3                         # ladder win + a plain win
    assert abs(s["flat_pl"] - (-1 + 0.85)) < 1e-3                                           # like for like: only the trades that could be taken
    assert s["rescue_rate"] == 1.0 and s["lost_ladders"] == 0


def test_summary_risk_numbers():
    # two lost ladders in a row, then a win: worst ladder, biggest stake, drawdown, longest run
    c = {}
    for k in range(0, 40):
        c[k * TF] = UP                                                                      # price only rises, so every SELL loses
    c[30 * TF] = DOWN
    trades = [trade(0, -1, "loss"), trade(240, -1, "loss"), trade(480, -1, "loss")]
    s = mtg.apply(trades, c, TF, 85.0, 2, 2.2)
    assert s["lost_ladders"] == 3 and s["longest_lost_run"] == 3 and abs(s["worst_ladder"] + 8.04) < 1e-9
    assert abs(s["ladder_pl"] + 3 * 8.04) < 1e-2 and abs(s["max_drawdown"] - 3 * 8.04) < 1e-2 and abs(s["stake_max"] - 4.84) < 1e-9
    assert s["units_at_risk"] == 8.04 and s["rescue_rate"] == 0.0
    assert mtg.apply([], {}, TF, 85.0, 2, 2.2)["taken"] == 0


def test_chart_endpoint_matches_the_brute_force_ladder():
    eng = Engine(":memory:", seed_builtins=True)
    put_candles(eng, "S_otc", streak_series(3000, 41, 0.5))
    sid = next(s["id"] for s in eng.repo.list() if s["key"] == "candle-streak-reversal")
    off = eng.backtests.chart("S_otc", 60, strategy_id=sid, limit=20000)
    assert "mtg" not in off and all("mtg" not in t for t in off["trades"])                  # off by default: nothing changes
    on = eng.backtests.chart("S_otc", 60, strategy_id=sid, limit=20000, mtg_steps=2, mtg_mult=2.2)
    assert [t["t"] for t in on["trades"]] == [t["t"] for t in off["trades"]]                # same trades either way
    cm = {r[0]: r for r in on["candles"]}
    s = on["mtg"]
    assert s["signals"] == len(on["trades"]) and s["taken"] + s["skipped"] == s["signals"] and s["steps"] == 2 and s["mult"] == 2.2
    checked = 0
    for t in on["trades"]:
        lad = t.get("mtg")
        if not lad:
            continue
        assert t["result"] == "loss" and not t.get("mtg_skip")
        k = 0
        for st in lad["steps"]:
            k += 1
            o, c = cm[st["t"]][1], cm[st["t"]][4]
            assert st["t"] == t["t"] + (k + 1) * 60 and st["result"] == ("win" if (c - o) * t["dir"] > 0 else "loss" if (c - o) * t["dir"] < 0 else "draw")
        assert lad["outcome"] in ("mtg1", "mtg2", "lost", "draw", "open")
        checked += 1
    assert checked > 20 and s["first_losses"] >= checked
    assert abs(s["ladder_pl"] - sum(t["mtg"]["pl"] if t.get("mtg") else t["profit"] for t in on["trades"] if not t.get("mtg_skip") and not (t.get("mtg") or {}).get("outcome") == "open")) < 0.01
    try:
        eng.backtests.chart("S_otc", 60, strategy_id=sid, mtg_steps=2, mtg_mult=0.5)
        raise AssertionError("expected an error")
    except BacktestError:
        pass


def _mini_run():
    """Candles + trades by hand: SELLs at 0, 3, 6, 9 (candle times k*60 after T0).
    0: entry c1 up = LOSS, c2 down = MTG-1 WIN.   3: c4 up = LOSS, c5 up = MTG lost.   6: c7 down = WIN.   9: c10 flat = DRAW."""
    import pandas as pd
    from test_backtest import T0
    eng = Engine(":memory:", seed_builtins=False)
    shape = {1: UP, 2: DOWN, 4: UP, 5: UP, 7: DOWN, 10: FLAT}
    rows = []
    for k in range(0, 14):
        o, c = shape.get(k, (1.0, 1.0))
        rows.append({"t": T0 + k * TF, "o": o, "h": max(o, c) + 0.01, "l": min(o, c) - 0.01, "c": c, "n": 10})
    put_candles(eng, "H_otc", rows)
    tr = pd.DataFrame({"symbol": "H_otc", "tf": 60, "t": [T0 + 0, T0 + 3 * TF, T0 + 6 * TF, T0 + 9 * TF], "dir": -1, "result": ["loss", "loss", "win", "draw"],
                       "profit": [-1.0, -1.0, 0.85, 0.0], "payout": 85.0})
    return eng, tr


def test_run_summary_by_hand():
    eng, tr = _mini_run()
    s = mtg.run_summary(eng.db, tr, 1, 2.2)
    assert (s["taken"], s["skipped"], s["wins"], s["rescued"], s["lost"], s["draws"], s["open"]) == (4, 0, 1, 1, 1, 1, 0)
    assert abs(s["accuracy"] - 2 / 3) < 1e-9                                                   # (1 win + 1 rescued) of 3 decided; the draw is left out
    assert abs(s["net"] - ((2.2 * 0.85 - 1) + (-3.2) + 0.85 + 0.0)) < 1e-3 and abs(s["flat_net"] - (-1 - 1 + 0.85)) < 1e-9
    assert s["steps"] == 1 and s["mult"] == 2.2
    # a re-entry can also fire into a signal: a signal inside the ladder window is skipped, not double counted
    import pandas as pd
    from test_backtest import T0
    busy = pd.concat([tr.iloc[:1], pd.DataFrame({"symbol": "H_otc", "tf": 60, "t": [T0 + 1 * TF], "dir": -1, "result": ["win"], "profit": [0.85], "payout": 85.0})], ignore_index=True)
    b = mtg.run_summary(eng.db, busy, 1, 2.2)
    assert b["signals"] == 2 and b["taken"] == 1 and b["skipped"] == 1 and b["rescued"] == 1
    z = mtg.run_summary(eng.db, tr.iloc[0:0], 1, 2.2)
    assert z["taken"] == 0 and z["accuracy"] is None


def test_new_runs_carry_it_and_old_runs_are_backfilled():
    eng = Engine(":memory:", seed_builtins=True)
    put_candles(eng, "S_otc", streak_series(3000, 41, 0.5))
    sid = next(s["id"] for s in eng.repo.list() if s["key"] == "candle-streak-reversal")
    run_id = eng.backtests.start({"strategy_id": sid, "symbols": ["S_otc"], "tfs": [60]}, sync=True)
    row = next(r for r in eng.backtests.list_runs() if r["id"] == run_id)
    m = row["mtg1"]
    assert m and not row["mtg1_pending"] and m["steps"] == 1 and m["mult"] == 2.2 and m["taken"] + m["skipped"] == m["signals"] == row["n_trades"]
    # the run's number is the same one the chart shows for that pair with one MTG step
    ch = eng.backtests.chart("S_otc", 60, run_id=run_id, limit=20000, mtg_steps=1, mtg_mult=2.2)["mtg"]
    assert (m["taken"], m["skipped"], m["rescued"], m["lost"]) == (ch["taken"], ch["skipped"], ch["rescued"]["mtg1"], ch["lost_ladders"])
    assert abs(m["net"] - ch["ladder_pl"]) < 0.01 and abs(m["accuracy"] - (m["wins"] + m["rescued"]) / (m["wins"] + m["rescued"] + m["lost"])) < 1e-12
    assert m["accuracy"] > row["accuracy"]                                                    # a one-step re-entry always lifts the win rate ...
    assert m["net"] < m["flat_net"] + 5                                                       # ... but this fair-coin strategy gains nothing real from it

    # an older run (stored before the column existed): shown as pending, then filled in
    import json
    res = json.loads(eng.db.execute("SELECT result FROM backtest_runs WHERE id=?", (run_id,)).fetchone()[0])
    res.pop("mtg1")
    eng.db.execute("UPDATE backtest_runs SET result=? WHERE id=?", (json.dumps(res), run_id))
    eng.db.commit()
    first = next(r for r in eng.backtests.list_runs() if r["id"] == run_id)
    assert first["mtg1_pending"] and first["mtg1"] is None                                     # this call notices the gap and starts the backfill
    again = next(r for r in eng.backtests.list_runs() if r["id"] == run_id)
    assert not again["mtg1_pending"] and again["mtg1"] == m                                    # the backfill reproduces the original numbers exactly
    assert eng.backtests.backfill_mtg() == 0                                                  # nothing left to do, so it never repeats the work


def test_api_fields():
    import os
    import tempfile

    from fastapi.testclient import TestClient
    with tempfile.TemporaryDirectory() as d:
        os.environ["QXS_DB"] = str(Path(d) / "mtg_api.db")
        sys.modules.pop("app.main", None)
        import app.main as main
        with TestClient(main.app) as c:
            put_candles(main.engine, "API_otc", streak_series(3000, 71, 0.5))
            sid = next(s["id"] for s in c.get("/api/strategies").json() if s["key"] == "candle-streak-reversal")
            r = c.post("/api/backtests/chart", json={"symbol": "API_otc", "tf": 60, "strategy_id": sid, "mtg_steps": 2})
            assert r.status_code == 200 and r.json()["mtg"]["mult"] == 2.2 and r.json()["mtg"]["steps"] == 2
            assert "mtg" not in c.post("/api/backtests/chart", json={"symbol": "API_otc", "tf": 60, "strategy_id": sid}).json()
            assert c.post("/api/backtests/chart", json={"symbol": "API_otc", "strategy_id": sid, "mtg_steps": 9}).status_code == 422


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
