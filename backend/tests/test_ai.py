"""AI advisor: it must find skill where skill really exists, and say 'no proven skill' where it does not - and never confirm anything it has not proven.
Run from backend/:  .venv\\Scripts\\python tests\\test_ai.py"""
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402

from app import ai  # noqa: E402
from app.engine import Engine  # noqa: E402
from test_backtest import put_candles  # noqa: E402

T0 = 1_700_000_000
FAST = {"folds": 3, "rounds": 40, "train_cap": 20_000, "min_rows": 3000, "min_test": 2000, "embargo_rows": 100, "min_cell": 60}


def candles(n, seed, planted: bool, t0=T0):
    """Candles one minute apart. planted=True: the next candle goes UP with 72 % if the current one closed in the top half of its range (a real, learnable habit);
    planted=False: a fair coin."""
    rnd, price, rows, clv = random.Random(seed), 100.0, [], 0.0
    for i in range(n):
        up = (rnd.random() < (0.72 if clv > 0 else 0.28)) if planted else rnd.random() < 0.5
        size = abs(rnd.gauss(0.10, 0.04)) + 0.01
        o = price
        c = o + size if up else o - size
        hi, lo = max(o, c) + rnd.random() * 0.08, min(o, c) - rnd.random() * 0.08
        rows.append({"t": t0 + 60 * i, "o": o, "h": hi, "l": lo, "c": c, "n": 80})
        clv = ((c - lo) - (hi - c)) / (hi - lo)
        price = c
    return rows


def advisor_with(rows, sym="T_otc"):
    eng = Engine(":memory:")
    eng.instruments[sym] = {"symbol": sym, "name": sym, "payout": 90, "open": True, "market": "OTC"}
    put_candles(eng, sym, rows)
    return eng


def test_it_finds_a_real_habit_and_proves_it():
    eng = advisor_with(candles(7000, 1, planted=True))
    eng.ai.train(FAST, sync=True)
    rep = eng.ai.report
    assert rep and rep["verdict"] == "proven" and rep["auc"] > 0.6 and rep["auc_lo"] > 0.5, rep and (rep["verdict"], rep["auc"])
    assert rep["auc_control"] < 0.55 and all(f["auc"] > 0.5 for f in rep["folds"])               # the shuffled-label control stays at chance
    assert eng.ai.usable()
    for r in candles(120, 5, planted=True, t0=T0 + 60 * 7000):                                    # live: stream fresh candles for the same pair
        eng._series("T_otc", 60).put(r)
    rd = eng.ai.read("T_otc", 60)
    assert rd["available"] and rd["usable"] and 0.0 < rd["p_up"] < 1.0
    last = eng._series("T_otc", 60).tail(1)[0]
    top_half = ((last["c"] - last["l"]) - (last["h"] - last["c"])) > 0
    assert (rd["p_up"] > 0.5) == top_half                                                           # it learned the habit: closed high -> expects up
    side = "CALL" if top_half else "PUT"
    assert eng.ai.confirm(side, rd)["label"] == "confirmed" and eng.ai.confirm("PUT" if top_half else "CALL", rd)["label"] == "against"


def test_on_a_fair_coin_it_says_no_proven_skill_and_confirms_nothing():
    eng = advisor_with(candles(7000, 2, planted=False))
    eng.ai.train(FAST, sync=True)
    rep = eng.ai.report
    assert rep["verdict"] in ("no_skill", "weak") and rep["auc"] < 0.52 and rep["verdict"] != "proven", (rep["verdict"], rep["auc"])
    assert not eng.ai.usable()
    for r in candles(120, 6, planted=False, t0=T0 + 60 * 7000):
        eng._series("T_otc", 60).put(r)
    rd = eng.ai.read("T_otc", 60)
    assert rd["available"] and not rd["usable"] and "chance band" in (rd["note"] or "")
    assert eng.ai.confirm("CALL", rd)["label"] == "unproven" and eng.ai.confirm("PUT", rd)["label"] == "unproven"        # it will not confirm or veto anything


def test_not_trained_and_not_enough_data_are_reported_plainly():
    eng = advisor_with(candles(500, 3, planted=True))
    rd = eng.ai.read("T_otc", 60)
    assert not rd["available"] and "No model trained" in rd["note"]
    eng.ai.train(FAST, sync=True)
    assert eng.ai.model is None and "collect more history" in (eng.ai.state["error"] or "")


def test_the_live_record_scores_every_call_against_the_candle_that_followed():
    eng = advisor_with(candles(7000, 4, planted=True))
    eng.ai.train(FAST, sync=True)
    live = candles(160, 8, planted=True, t0=T0 + 60 * 7000)
    for r in live[:125]:
        eng._series("T_otc", 60).put(r)                                                            # history the pair already streamed
    for r in live[125:155]:
        eng._series("T_otc", 60).put(r)
        eng.last_tick["T_otc"] = {"recv": time.time(), "price": r["c"], "ts": time.time()}
        eng.ai.tick()                                                                              # one call is logged per newly closed candle
    put_candles(eng, "T_otc", live[:156])                                                           # the stored candles reach one candle further: the last call can be scored
    eng.ai.tick()
    rec = eng.ai.live_record()
    n_logged = eng.db.execute("SELECT COUNT(*) FROM ai_log").fetchone()[0]
    assert n_logged == 30 and rec["n"] >= 28 and rec["acc"] is not None and 0.0 <= rec["acc"] <= 1.0
    eng.ai.tick()
    assert eng.db.execute("SELECT COUNT(*) FROM ai_log").fetchone()[0] == 30                          # the same candle is never logged twice
    # the live record can overrule the validation: a model that keeps being wrong stops confirming
    eng.db.execute("UPDATE ai_log SET p_up = 0.9, outcome = 0")
    eng.db.executemany("INSERT INTO ai_log (symbol, tf, candle_t, p_up, outcome) VALUES ('X_otc', 60, ?, 0.9, 0)", [(i,) for i in range(400)])
    eng.db.commit()
    assert eng.ai.live_record()["ci_hi"] < 0.5 and not eng.ai.usable()


def test_the_reliability_map_flags_nothing_on_noise_and_something_where_skill_is_planted():
    noise = advisor_with(candles(7000, 9, planted=False))
    noise.ai.train(FAST, sync=True)
    m = noise.ai.report["map"]
    assert m["tested"] >= 15 and not m["favour"] and not m["avoid"]                                   # nothing stands out after correcting for the cells tried
    real = advisor_with(candles(7000, 10, planted=True))
    real.ai.train(FAST, sync=True)
    m2 = real.ai.report["map"]
    assert m2["confident_win"] > 0.6 and any(c["dim"] == "last candle" for c in m2["favour"])        # the habit is found: confident calls win, in a named situation


def test_features_use_only_the_past():
    rows = candles(400, 11, planted=True)
    import pandas as pd
    from app import evaluator
    full = ai.candle_features(evaluator.build_df(rows))
    part = ai.candle_features(evaluator.build_df(rows[:300]))
    a, b = full.iloc[:300].to_numpy(float), part.to_numpy(float)
    both = np.isfinite(a) & np.isfinite(b)
    assert np.allclose(a[both], b[both], atol=1e-6)                                                    # candle i looks the same whether or not later candles exist
    assert list(full.columns) == ai.feature_columns()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

