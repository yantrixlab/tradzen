"""Nightly research check: dataset (no look-ahead), statistics, verdict gates, planted pattern vs pure noise, control, schedule, storage, API.
Run from backend/:  .venv\\Scripts\\python tests\\test_research.py"""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402

from app import minigbm, research  # noqa: E402
from app.backtest import BacktestError  # noqa: E402
from app.engine import Engine  # noqa: E402
from test_backtest import put_candles  # noqa: E402

T0 = 1_700_000_040


def candles(n: int, seed: int, rule=None, t0: int = T0) -> list[dict]:
    """Random-walk 1m candles. rule(prev_dirs) -> P(next candle up); None = a fair coin (no information at all)."""
    rnd = np.random.default_rng(seed)
    price, dirs, rows = 100.0, [], []
    for i in range(n):
        p_up = 0.5 if rule is None else rule(dirs)
        up = rnd.random() < p_up
        size = abs(rnd.normal(0.07, 0.04)) + 0.005
        o, c = price, price + (size if up else -size)
        rows.append({"t": t0 + i * 60, "o": o, "h": max(o, c) + abs(rnd.normal(0, 0.02)), "l": min(o, c) - abs(rnd.normal(0, 0.02)), "c": c, "n": 10})
        dirs.append(1 if up else -1)
        price = c
    return rows


def planted(dirs):                       # the last two candles both up -> 70 % up; both down -> 30 %; otherwise a coin flip (a strong, simple pattern)
    return 0.7 if dirs[-2:] == [1, 1] else 0.3 if dirs[-2:] == [-1, -1] else 0.5


def make(rule, seed=1, pairs=3, n=14000):
    eng = Engine(":memory:", seed_builtins=False)
    for i in range(pairs):
        put_candles(eng, f"P{i}_otc", candles(n, seed + i, rule))
    return eng


# ------------------------------------------------------------------ dataset
def test_window_features_have_no_look_ahead():
    rows = candles(200, 3)
    o, h, l, c, t = (np.array([r[k] for r in rows], float) for k in ("o", "h", "l", "c", "t"))
    X, y, tt = research.window_features(o, h, l, c, t, 4)
    assert X.shape[1] == 4 * 4 + 2 and len(X) == len(y) == len(tt) and set(np.unique(y)) <= {0.0, 1.0}
    i = int(np.flatnonzero(tt == t[100])[0])
    o2, h2, l2, c2 = o.copy(), h.copy(), l.copy(), c.copy()
    for a in (o2, h2, l2, c2):
        a[102:] *= 3.0                                       # change everything from two candles ahead of candle 100
    X2, y2, tt2 = research.window_features(o2, h2, l2, c2, t, 4)
    j = int(np.flatnonzero(tt2 == t[100])[0])
    assert np.allclose(X[i], X2[j]) and y[i] == y2[j]        # the row for candle 100 uses candles <= 100 only; its label is candle 101
    assert y[i] == float(c[101] > o[101])
    assert np.isclose(X[i][3], 0.0) and np.isfinite(X).all()  # last candle's own close relative to itself is 0


def test_windows_never_span_a_data_gap():
    eng = Engine(":memory:", seed_builtins=False)
    a, b = candles(400, 5), candles(400, 6, t0=T0 + 400 * 60 + 3600)           # an hour missing in the middle
    put_candles(eng, "G_otc", a + b)
    X, y, t, pairs = research.build_dataset(eng.db, 10, 60, 100000)
    gap_lo, gap_hi = a[-1]["t"], b[0]["t"]
    assert pairs == 1 and len(y) > 600 and not any(gap_lo < v < gap_hi for v in t)
    first_b = t[t >= gap_hi].min()
    assert first_b >= gap_hi + (14 + 0) * 60                                    # after the gap the ATR / window warms up again from scratch


# ------------------------------------------------------------------ statistics and gates
def test_auc_and_stats():
    y = np.array([0, 0, 1, 1.0])
    assert minigbm.auc(np.array([0.1, 0.2, 0.8, 0.9]), y) == 1.0 and minigbm.auc(np.array([0.9, 0.8, 0.2, 0.1]), y) == 0.0
    rnd = np.random.default_rng(0)
    assert abs(minigbm.auc(rnd.random(20000), (rnd.random(20000) < 0.5).astype(float)) - 0.5) < 0.02
    s0 = research.auc_stats(0.5, 9000, 9000)
    assert abs(s0["z"]) < 1e-9 and abs(s0["p"] - 0.5) < 1e-9 and 0.007 < s0["band"] < 0.010      # ~ +-0.0084 chance band at 18k test rows
    s1 = research.auc_stats(0.53, 9000, 9000)
    assert s1["p"] < 1e-6 and s1["lo"] < 0.53 < s1["hi"]
    assert research.auc_stats(0.51, 100, 100)["p"] > 0.3                                        # the same edge in a tiny sample is nothing


def test_verdict_gates():
    cfg = {**research.DEFAULTS, "_k": 4}
    j = lambda a, p, n=20000: research.judge_model(a, p, n, cfg)[0]  # noqa: E731
    assert j(0.60, 0.0001, n=100) == "need_data"                    # a great number from a tiny test period is not believed
    assert j(0.55, 0.001) == "signal"
    assert j(0.55, 0.20) == "no_signal"                             # not significant once 4 models are accounted for
    assert j(0.505, 0.001) == "weak"                                # real but far too small to trade
    assert j(0.49, 0.001) == "no_signal" and j(0.50, 0.5) == "no_signal"


def test_schedule_logic():
    import datetime as dt
    day = dt.datetime(2026, 5, 1, tzinfo=dt.timezone.utc).timestamp()
    at = lambda h: day + h * 3600  # noqa: E731
    nxt, due = research.next_and_due(at(2), 3, None)
    assert nxt == at(3) and not due                                  # before the hour: wait
    nxt, due = research.next_and_due(at(3) + 30, 3, at(-21))
    assert due and nxt == at(3) + 30                                  # at the hour, no run yet today
    nxt, due = research.next_and_due(at(10), 3, at(-21))
    assert due                                                        # the PC was off at 03:00: made up as soon as the backend runs
    nxt, due = research.next_and_due(at(10), 3, at(3) + 200)
    assert not due and nxt == at(3) + 86400                           # already ran today: next is tomorrow
    assert research.next_and_due(at(10), None, None) == (None, False)
    os.environ["QXS_RESEARCH_HOUR"] = "off"
    assert research.schedule_hour() is None
    os.environ["QXS_RESEARCH_HOUR"] = "27"
    assert research.schedule_hour() == 23
    os.environ["QXS_RESEARCH_HOUR"] = "x"
    assert research.schedule_hour() == 3
    os.environ.pop("QXS_RESEARCH_HOUR")


# ------------------------------------------------------------------ the check itself
def test_planted_pattern_is_found_and_noise_is_not():
    eng = make(planted)
    r = research.run_check(eng.db, {"windows": [3]})
    assert r["control"]["ok"] and r["data"]["pairs"] == 3 and r["data"]["test"] > 5000
    assert r["verdict"] == "signal", r["summary"]
    best = max(r["models"], key=lambda m: m["auc_test"])
    assert best["auc_test"] > 0.56 and best["p_adj"] < 0.001 and best["top10"]["win"] > 0.6

    noise = make(None, seed=40)
    n = research.run_check(noise.db, {"windows": [3]})
    assert n["control"]["ok"] and n["verdict"] == "no_signal", n["summary"]
    assert all(abs(m["auc_test"] - 0.5) < 0.03 for m in n["models"]) and all(m["verdict"] == "no_signal" for m in n["models"])
    assert max(m["auc_train"] for m in n["models"]) >= max(m["auc_test"] for m in n["models"]) - 0.01   # whatever it "learned" does not carry over


def test_control_stays_calibrated_when_the_data_has_real_structure():
    """Regression: the control once scored shuffled-label models on the REAL labels, so a healthy run on structured data was declared broken."""
    for seed in (1, 2, 3, 4):
        r = research.run_check(make(planted, seed=seed * 10, pairs=2, n=9000).db, {"windows": [3]})
        assert r["control"]["ok"] and abs(r["control"]["auc_test"] - 0.5) < 0.03 and r["verdict"] == "signal", (seed, r["control"], r["summary"])


def test_too_little_data():
    eng = make(None, pairs=1, n=800)
    try:
        research.run_check(eng.db, {"windows": [3]})
        raise AssertionError("expected an error")
    except BacktestError as e:
        assert "collect more history" in str(e)
    mid = make(None, pairs=1, n=6000)                               # enough to train, too few test candles to judge
    r = research.run_check(mid.db, {"windows": [3]})
    assert r["verdict"] == "need_data" and all(m["verdict"] == "need_data" for m in r["models"])
    for bad in ([1], [41], []):
        try:
            research.run_check(mid.db, {"windows": bad})
            raise AssertionError("expected an error")
        except BacktestError:
            pass


# ------------------------------------------------------------------ storage + API
def test_runner_storage_and_guard():
    eng = make(planted, pairs=2, n=9000)
    rid = eng.research.start({"windows": [3]}, source="manual", sync=True)
    run = eng.research.get(rid)
    assert run["status"] == "done" and run["verdict"] == "signal" and run["source"] == "manual" and run["result"]["tried"] == 2
    assert run["result"]["sentiment"] is not None and run["best_auc"] == run["result"]["best"]["auc_test"]
    lst = eng.research.list()
    assert lst[0]["id"] == rid and lst[0]["rows"] and lst[0]["summary"]
    eng.research.running_id = 99                                    # a run is "in progress"
    try:
        eng.research.start({}, sync=True)
        raise AssertionError("expected a refusal")
    except BacktestError as e:
        assert "already running" in str(e)
    eng.research.running_id = None
    os.environ["QXS_RESEARCH_HOUR"] = "off"
    assert eng.research.tick() is None and eng.research.status()["hour_utc"] is None
    os.environ.pop("QXS_RESEARCH_HOUR")
    bad = eng.research.start({"windows": [3], "min_rows": 10 ** 9}, sync=True)                  # a failed run is recorded, not raised
    assert eng.research.get(bad)["status"] == "error" and "collect more history" in eng.research.get(bad)["error"]
    assert eng.research.running_id is None


def test_api():
    from fastapi.testclient import TestClient
    with tempfile.TemporaryDirectory() as d:
        os.environ["QXS_DB"] = str(Path(d) / "research_api.db")
        os.environ["QXS_RESEARCH_HOUR"] = "off"
        sys.modules.pop("app.main", None)
        import app.main as main
        with TestClient(main.app) as c:
            s = c.get("/api/research/status").json()
            assert s["hour_utc"] is None and s["running"] is False and s["next_run"] is None
            put_candles(main.engine, "API_otc", candles(6000, 9, planted))
            r = c.post("/api/research/run", json={"windows": [3], "sync": True})
            assert r.status_code == 200 and r.json()["status"] == "done", r.text
            rid = r.json()["id"]
            assert c.get(f"/api/research/{rid}").json()["result"]["verdict"] in ("need_data", "no_signal", "weak", "signal")
            assert c.get("/api/research").json()[0]["id"] == rid
            assert c.get("/api/research/99999").status_code == 404
            assert c.post("/api/research/run", json={"windows": [1]}).status_code == 400
        os.environ.pop("QXS_RESEARCH_HOUR", None)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
