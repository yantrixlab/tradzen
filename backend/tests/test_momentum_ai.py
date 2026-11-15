"""AI-built momentum strategy: initial point without look-ahead, a learned rule that is tested on unseen candles, honest grading, and the generator's 'ai' scope.
Run from backend/:  .venv\\Scripts\\python tests\\test_momentum_ai.py"""
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import generator, momentum_ai as M  # noqa: E402
from app.engine import Engine  # noqa: E402
from test_backtest import put_candles  # noqa: E402

T0 = 1_699_999_980                                            # a whole minute


def waves(n, seed, persist=0.5):
    """A price made of legs: after the first candle of a leg, each next candle continues it with probability `persist` (0.5 = a fair coin)."""
    rnd, price, rows, d = random.Random(seed), 100.0, [], 1
    for i in range(n):
        if rnd.random() > persist:
            d = -d
        step = abs(rnd.gauss(0.10, 0.04)) + 0.02
        o = price
        c = o + d * step
        rows.append({"t": T0 + 60 * i, "o": o, "h": max(o, c) + rnd.random() * 0.03, "l": min(o, c) - rnd.random() * 0.03, "c": c, "n": 60})
        price = c
    return rows


def frame(rows):
    return pd.DataFrame(rows).rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"}).astype(float)


def test_the_initial_point_is_a_confirmed_swing_and_uses_only_the_past():
    rows = waves(500, 1, 0.6)
    full = M.momentum_frame(frame(rows))
    part = M.momentum_frame(frame(rows[:350]))
    a, b = full.iloc[:350].to_numpy(float), part.to_numpy(float)
    both = np.isfinite(a) & np.isfinite(b)
    assert np.allclose(a[both], b[both], atol=1e-9), "a candle's momentum state must not change when later candles exist"
    last = full.iloc[300]
    assert last["d"] in (-1, 1) and 0 <= last["origin_i"] <= 300 - M.SW + 0 and last["leg_len"] == 300 - last["origin_i"]


def test_up_leg_starts_at_a_swing_low_and_down_leg_at_a_swing_high():
    fr = M.momentum_frame(frame(waves(400, 2, 0.7)))
    d = frame(waves(400, 2, 0.7))
    for i in range(80, 400, 17):
        r = fr.iloc[i]
        if r["d"] == 1:
            assert abs(r["origin_price"] - d["low"].iloc[int(r["origin_i"])]) < 1e-12
        elif r["d"] == -1:
            assert abs(r["origin_price"] - d["high"].iloc[int(r["origin_i"])]) < 1e-12


def test_on_a_fair_coin_no_rule_is_proven_and_the_grade_is_c():
    eng = Engine(":memory:")
    eng.instruments["T_otc"] = {"symbol": "T_otc", "name": "T", "payout": 88, "open": True, "market": "OTC"}
    put_candles(eng, "T_otc", waves(9000, 3, 0.5))
    m = M.learn(eng.db, 60, {"T_otc": 88})
    assert m["ok"] and all(lf["verdict"] != "proven" for lf in m["leaves"]), [(lf["rule"], lf["acc_valid"]) for lf in m["leaves"]]


def test_when_momentum_really_persists_the_rule_is_found_and_proven_on_unseen_candles():
    eng = Engine(":memory:")
    eng.instruments["T_otc"] = {"symbol": "T_otc", "name": "T", "payout": 88, "open": True, "market": "OTC"}
    put_candles(eng, "T_otc", waves(12000, 4, 0.72))              # a leg continues 72 % of the time: a real, learnable habit
    m = M.learn(eng.db, 60, {"T_otc": 88}, breakeven=0.53)
    assert m["ok"] and any(lf["verdict"] == "proven" and lf["side"] == "follow" for lf in m["leaves"]), [(lf["side"], lf["acc_valid"], lf["verdict"]) for lf in m["leaves"]]
    big = max(m["leaves"], key=lambda lf: lf["n_valid"])
    assert big["acc_valid"] > 0.65 and big["side"] == "follow"


def test_too_little_history_is_reported_plainly():
    eng = Engine(":memory:")
    put_candles(eng, "T_otc", waves(700, 5))
    m = M.learn(eng.db, 60, {})
    assert not m["ok"] and "collect more history" in m["why"]


def test_the_generator_ai_scope_builds_a_strategy_names_the_initial_point_and_grades_honestly():
    eng = Engine(":memory:")
    eng.instruments["T_otc"] = {"symbol": "T_otc", "name": "T", "payout": 88, "open": True, "market": "OTC"}
    rows = waves(9200, 6, 0.5)
    put_candles(eng, "T_otc", rows[:9000])
    s = eng._series("T_otc", 60)
    for r in rows[8700:9000]:
        s.put(r)
    entry = rows[8999]["t"] + 60
    out = generator.generate(eng, "T_otc", 60, entry, scope="ai", save=False, now=entry + 3)
    assert out["ai"]["model"]["rows"] > 4000 and out["blocked"] is None, out["blocked"]
    assert out["verdict"] in ("CALL", "PUT") and out["grade"] == "C"                      # a fair coin: the AI's own test finds no edge, and says so
    assert out["ai"]["read"]["origin"]["candles_ago"] >= 0 and out["ai"]["read"]["leaf"]["rule"]
    assert any("NO proven edge" in w for w in out["warnings"])
    b = generator.generate(eng, "T_otc", 60, entry, scope="ai", min_grade="B", save=False, now=entry + 3)
    assert b["verdict"] == "NO TRADE" and "below your minimum" in b["blocked"]             # asking for B+ refuses an unproven situation


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
