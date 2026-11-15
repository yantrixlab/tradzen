"""Phase C (part 2): backtester - settlement rules, statistics, planted edge vs pure noise, persistence, background jobs.
Run from backend/:  .venv\\Scripts\\python tests\\test_backtest.py"""
import json
import random
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app.backtest import BacktestError, compute_trades  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.spec import validate_spec  # noqa: E402
from app.stats_util import summarize, wilson  # noqa: E402
from test_engine import walk  # noqa: E402

T0 = 1_700_000_040
STREAK = validate_spec(next(b for b in __import__("app.builtins", fromlist=["BUILTINS"]).BUILTINS if b["key"] == "candle-streak-reversal")["spec"])["spec"]


def put_candles(eng: Engine, sym: str, rows: list[dict], tf: int = 60) -> None:
    eng.db.executemany("INSERT OR REPLACE INTO candles (symbol, tf, ts, o, h, l, c, ticks, source) VALUES (?,?,?,?,?,?,?,?,?)",
                       [(sym, tf, r["t"], r["o"], r["h"], r["l"], r["c"], r.get("n", 10), "test") for r in rows])
    eng.db.commit()


def streak_series(n: int, seed: int, reversal_prob: float, t0: int = T0, tf: int = 60) -> list[dict]:
    """Random colours, except that after a run of 4+ same-coloured candles the next one is the opposite colour with `reversal_prob`."""
    rnd, price, run, last, rows = random.Random(seed), 1.2, 0, 0, []
    for i in range(n):
        if abs(run) >= 4 and rnd.random() < reversal_prob:
            color = -last
        elif abs(run) >= 4:
            color = last
        else:
            color = 1 if rnd.random() < 0.5 else -1
        size = abs(rnd.gauss(0.0004, 0.0002)) + 1e-5
        o, c = price, price + color * size
        rows.append({"t": t0 + i * tf, "o": o, "h": max(o, c) + 1e-5, "l": min(o, c) - 1e-5, "c": c, "n": 10})
        run = run + color if run * color > 0 else color
        last, price = color, c
    return rows


# ------------------------------------------------------------------ statistics
def test_summarize_math():
    t = pd.DataFrame({"t": [1, 2, 3, 4, 5, 6], "result": ["win", "loss", "loss", "win", "draw", "win"],
                      "profit": [0.85, -1, -1, 0.85, 0, 0.85], "payout": [85] * 6, "strength": [50] * 6})
    s = summarize(t)
    assert (s["wins"], s["losses"], s["draws"], s["settled"]) == (3, 2, 1, 5)
    assert abs(s["accuracy"] - 0.6) < 1e-9 and abs(s["breakeven"] - 1 / 1.85) < 1e-9
    assert abs(s["profit"] - 0.55) < 1e-9 and abs(s["expectancy"] - 0.55 / 6) < 1e-4
    assert abs(s["profit_factor"] - (3 * 0.85) / 2) < 1e-3
    assert s["longest_losing_streak"] == 2 and s["longest_winning_streak"] == 1
    assert abs(s["max_drawdown"] - 2.0) < 1e-9                    # equity 0.85 -> -0.15 -> -1.15 : peak 0.85, trough -1.15
    lo, hi = wilson(60, 100)
    assert s["ci_low"] < s["accuracy"] < s["ci_high"] and 0.49 < lo < 0.51 and 0.68 < hi < 0.70
    assert summarize(t.iloc[0:0])["trades"] == 0


# ------------------------------------------------------------------ settlement rules
def test_entry_at_next_open_and_no_trade_on_last_candle():
    eng = Engine(":memory:", seed_builtins=False)
    rows = streak_series(300, 5, 0.5)
    for i in (150, 151, 152):                       # a hand-made doji (close == open) after a green candle to exercise 'draw'
        pass
    put_candles(eng, "T_otc", rows)
    spec = validate_spec({"factors": [{"id": "g", "weight": 100, "kind": "expr", "call": "green", "put": "red"}], "gates": {"min_candles": 20}})["spec"]
    tr = compute_trades(eng.db, spec, {}, {})
    by_t = {r["t"]: r for r in rows}
    assert len(tr) > 100
    for r in tr.itertuples():
        nxt = by_t[r.t + 60]                                        # the settling candle must exist => the last candle never trades
        assert r.entry == nxt["o"] and r.exit == nxt["c"]
        won = (nxt["c"] - nxt["o"]) * r.dir > 0
        assert r.result == ("win" if won else "loss" if (nxt["c"] - nxt["o"]) * r.dir < 0 else "draw")
        assert r.profit == (0.85 if r.result == "win" else -1.0 if r.result == "loss" else 0.0)
    assert rows[-1]["t"] not in set(tr["t"]) and tr["t"].min() >= T0 + 19 * 60, "warm-up respected"


def test_gaps_split_runs_and_trades_never_straddle_them():
    eng = Engine(":memory:", seed_builtins=False)
    a = streak_series(200, 1, 0.5)
    b = streak_series(200, 2, 0.5, t0=T0 + 260 * 60)               # 60 missing candles in between
    put_candles(eng, "G_otc", a + b)
    tr = compute_trades(eng.db, STREAK, {}, {})
    have = {r["t"] for r in a + b}
    assert len(tr) > 0 and all((t + 60) in have for t in tr["t"]), "every trade needs its settling candle"
    assert not any(T0 + 199 * 60 <= t < T0 + 260 * 60 for t in tr["t"]), "nothing may trade on the candle before the hole"


# ------------------------------------------------------------------ edge vs noise
def test_planted_edge_is_found_and_noise_is_not():
    eng = Engine(":memory:", seed_builtins=False)
    put_candles(eng, "EDGE_otc", streak_series(6000, 11, 0.9))
    put_candles(eng, "NOISE_otc", streak_series(6000, 12, 0.5))
    edge = compute_trades(eng.db, STREAK, {"symbols": ["EDGE_otc"]}, {})
    noise = compute_trades(eng.db, STREAK, {"symbols": ["NOISE_otc"]}, {})
    se, sn = summarize(edge), summarize(noise)
    assert se["settled"] > 100 and se["accuracy"] > 0.8 and se["ci_low"] > se["breakeven"] + 0.1 and se["expectancy"] > 0.3, se
    assert sn["settled"] > 100 and sn["ci_low"] < 0.5 < sn["ci_high"], sn
    assert sn["expectancy"] < 0.05, "no edge at an 85 % payout must not look profitable"


# ------------------------------------------------------------------ runs, persistence, jobs
def test_run_is_persisted_and_reloadable():
    eng = Engine(":memory:", seed_builtins=False)
    put_candles(eng, "RUN_otc", streak_series(4000, 21, 0.9))
    run_id = eng.backtests.start({"spec": STREAK, "label": "unit", "valid_fraction": 0.3}, sync=True)
    run = eng.backtests.get_run(run_id)
    assert run["status"] == "done" and run["n_trades"] > 50 and run["progress"] == 1
    res = run["result"]
    assert res["train"]["trades"] + res["valid"]["trades"] == res["summary"]["trades"] == run["n_trades"]
    assert res["split_t"] is not None and res["by_direction"] and res["by_tier"] and res["summary"]["equity"]
    page = eng.backtests.trades_page(run_id, limit=5)
    assert page["total"] == run["n_trades"] and len(page["rows"]) == 5
    df = eng.backtests.load_trades(run_id)
    assert len(df) == run["n_trades"] and {"adx14", "atr_ratio", "hour_utc", "session", "trend_align", "streak"} <= set(df.columns)
    assert eng.backtests.list_runs()[0]["id"] == run_id
    assert eng.backtests.delete_run(run_id) and eng.backtests.get_run(run_id) is None
    assert eng.db.execute("SELECT COUNT(*) FROM backtest_trades").fetchone()[0] == 0


def test_bad_requests_are_rejected():
    eng = Engine(":memory:", seed_builtins=True)
    for cfg in ({}, {"spec": {"factors": []}}, {"strategy_id": 99999}, {"strategy_id": next(s["id"] for s in eng.repo.list() if s["key"] == "consensus-3")},
                {"spec": STREAK, "valid_fraction": 1.5}):
        try:
            eng.backtests.start(cfg, sync=True)
        except BacktestError:
            continue
        raise AssertionError(f"accepted {cfg}")
    put_candles(eng, "X_otc", streak_series(300, 1, 0.5))
    run = eng.backtests.get_run(eng.backtests.start({"spec": STREAK, "tfs": [900]}, sync=True))
    assert run["status"] == "error" and "timeframes" in run["error"]
    run = eng.backtests.get_run(eng.backtests.start({"spec": STREAK, "symbols": ["NOPE_otc"]}, sync=True))
    assert run["status"] == "error" and "no stored candles" in run["error"]


def test_background_job_on_a_file_database():
    with tempfile.TemporaryDirectory() as d:
        eng = Engine(Path(d) / "bt.db", seed_builtins=False)
        for i in range(3):
            put_candles(eng, f"J{i}_otc", streak_series(3000, 30 + i, 0.8))
        run_id = eng.backtests.start({"spec": STREAK})                  # not sync: runs in a thread with its own connection
        deadline = time.time() + 60
        while time.time() < deadline:
            run = eng.backtests.get_run(run_id)
            if run["status"] != "running":
                break
            time.sleep(0.2)
        assert run["status"] == "done", run
        assert run["n_trades"] > 100 and set(run["result"]["by_symbol"]) == {"J0_otc", "J1_otc", "J2_otc"}
        eng.close()


def test_strategy_id_runs_use_the_stored_version():
    eng = Engine(":memory:", seed_builtins=True)
    put_candles(eng, "S_otc", streak_series(3000, 41, 0.8))
    sid = next(s["id"] for s in eng.repo.list() if s["key"] == "candle-streak-reversal")
    run = eng.backtests.get_run(eng.backtests.start({"strategy_id": sid, "symbols": ["S_otc"]}, sync=True))
    assert run["status"] == "done" and run["strategy_id"] == sid and run["strategy_version"] == 1 and run["strategy"].startswith("Candle Streak")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all backtest tests passed")
