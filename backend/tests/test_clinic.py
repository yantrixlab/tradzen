"""Phase C (part 3): loss analysis - findings, false-discovery control, validation, suggestions, 'try it', similar signals, API.
Run from backend/:  .venv\\Scripts\\python tests\\test_clinic.py"""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import clinic  # noqa: E402
from app.builtins import BUILTINS  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.spec import validate_spec  # noqa: E402
from app.vec import SESSION_EDGES  # noqa: E402
from test_backtest import STREAK, put_candles, streak_series  # noqa: E402
from test_runner import session  # noqa: E402

T0 = 1_700_000_040


def make_trades(n: int, seed: int, bad_adx: bool = False, early_only: bool = False, base: float = 0.55) -> pd.DataFrame:
    rnd = np.random.default_rng(seed)
    adx, hour = rnd.uniform(5, 50, n), rnd.integers(0, 24, n)
    direction, stack = rnd.choice([1, -1], n), rnd.choice([-1, 0, 1], n)
    p = np.full(n, base)
    if bad_adx:
        mask = adx >= 30
        if early_only:
            mask &= np.arange(n) < n * 0.6
        p[mask] = 0.35
    win = rnd.random(n) < p
    session_ = np.select([hour < s for s, _ in SESSION_EDGES], [name for _, name in SESSION_EDGES], default="late")
    return pd.DataFrame({
        "symbol": rnd.choice(["A_otc", "B_otc", "C_otc"], n), "tf": 60, "t": T0 + np.arange(n) * 60, "dir": direction,
        "tier": rnd.choice(["A", "B"], n), "strength": rnd.integers(30, 100, n), "agree": rnd.integers(1, 5, n), "score": 0.0,
        "entry": 1.0, "exit": 1.0, "result": np.where(win, "win", "loss"), "profit": np.where(win, 0.85, -1.0), "payout": 85.0,
        "adx14": adx, "atr_ratio": rnd.uniform(0.4, 2.0, n), "hour_utc": hour, "weekday": rnd.integers(0, 7, n), "session": session_,
        "ema_stack": stack, "trend_align": direction * stack, "streak": rnd.integers(-5, 6, n), "bb_pctb": rnd.uniform(-0.2, 1.2, n),
        "rsi14": rnd.uniform(10, 90, n), "stoch_k": rnd.uniform(0, 100, n), "body_ratio": rnd.uniform(0, 1, n), "bb_bw_pctile": rnd.uniform(0, 100, n),
    })


# ------------------------------------------------------------------ findings
def test_a_planted_weakness_is_found_validated_and_made_actionable():
    res = clinic.analyze(make_trades(8000, 1, bad_adx=True))
    assert res["tests"] > 60 and res["baseline"]["fit"]["acc"] is not None and res["split_t"]
    sig = [f for f in res["findings"] if f["significant"]]
    adx = [f for f in sig if f["dimension"] == "adx" and f["direction"] == "worse"]
    assert adx, [(f["dimension"], f["bucket"]) for f in sig]
    top = adx[0]
    assert top["acc"] < 0.42 and top["q"] < 0.001 and top["validation"]["holds"] is True
    assert top["suggestion"] and top["suggestion"]["filter"]["type"] == "adx_max" and top["suggestion"]["filter"]["value"] in (30.0, 40.0)
    dims = {d["key"]: d for d in res["dimensions"]}
    assert {"adx", "hour", "session", "direction", "trend_align"} <= set(dims)
    assert any(b["bucket"] == "30 - 40" and b["all"]["acc"] < 0.42 for b in dims["adx"]["buckets"])


def test_noise_does_not_produce_findings():
    runs_with_hits = 0
    for seed in range(10):
        res = clinic.analyze(make_trades(6000, 100 + seed, base=0.5))
        assert res["tests"] > 60
        runs_with_hits += any(f["significant"] for f in res["findings"])
    assert runs_with_hits <= 3, f"{runs_with_hits}/10 pure-noise runs produced 'significant' findings (FDR is 10 %)"


def test_an_effect_that_vanishes_on_new_data_fails_validation():
    res = clinic.analyze(make_trades(9000, 2, bad_adx=True, early_only=True))
    adx = [f for f in res["findings"] if f["significant"] and f["dimension"] == "adx" and f["direction"] == "worse"]
    assert adx, "the early weakness exists in the fit slice"
    assert all(f["validation"]["holds"] is False for f in adx), [f["validation"] for f in adx]


def test_small_samples_are_not_tested():
    res = clinic.analyze(make_trades(300, 3, bad_adx=True), min_n=30)
    assert all(f["n"] >= 30 for f in res["findings"])
    assert clinic.analyze(pd.DataFrame())["findings"] == []


# ------------------------------------------------------------------ suggestions and candidates
def test_suggestions_map_to_valid_filters():
    cases = [("adx", "30 - 40", {"lo": 30.0, "hi": 40.0, "idx": 4, "total": 6}, "adx_max", 30.0),
             ("adx", "0 - 15", {"lo": 0.0, "hi": 15.0, "idx": 0, "total": 6}, "adx_min", 15.0),
             ("atr_ratio", ">= 1.5", {"lo": 1.5, "hi": 1000.0, "idx": 3, "total": 4}, "atr_ratio_max", 1.5),
             ("hour", "13", {}, "hours", None), ("weekday", "4", {}, "weekdays", None), ("session", "asia", {}, "hours", None),
             ("trend_align", "-1", {}, "trend_alignment", "not_against"), ("trend_align", "+1", {}, "trend_alignment", "not_with"),
             ("direction", "CALL", {}, "direction_only", "PUT")]
    base = validate_spec(next(b for b in BUILTINS if b["key"] == "bollinger-reversion")["spec"])["spec"]
    for dim, bucket, meta, typ, value in cases:
        s = clinic.suggest(dim, bucket, {bucket: meta}, worse=True)
        assert s and s["filter"]["type"] == typ and (value is None or s["filter"]["value"] == value), (dim, bucket, s)
        cand = clinic.candidate_spec(base, s["filter"])
        assert any(f["type"] == typ for f in cand["filters"])
    assert clinic.suggest("adx", "30 - 40", {"30 - 40": {"lo": 30, "hi": 40, "idx": 4, "total": 6}}, worse=False) is None
    assert clinic.suggest("symbol", "A_otc", {}, worse=True) is None and clinic.suggest("rsi", "0 - 30", {"0 - 30": {"lo": 0, "hi": 30}}, True) is None


def test_candidate_spec_merges_filters():
    base = validate_spec({"indicators": [], "factors": [{"id": "g", "weight": 100, "kind": "expr", "call": "green"}],
                          "filters": [{"type": "adx_max", "value": 35}, {"type": "hours", "exclude": ["01:00-02:00"]}]})["spec"]
    c = clinic.candidate_spec(base, {"type": "adx_max", "value": 30})
    assert [f["value"] for f in c["filters"] if f["type"] == "adx_max"] == [30]
    c = clinic.candidate_spec(c, {"type": "hours", "exclude": ["13:00-14:00"]})
    assert next(f for f in c["filters"] if f["type"] == "hours")["exclude"] == ["01:00-02:00", "13:00-14:00"]
    assert len(base["filters"]) == 2, "the original spec must not be modified"
    try:
        clinic.candidate_spec(base, {"type": "hours", "exclude": ["nonsense"]})
    except ValueError:
        pass
    else:
        raise AssertionError("invalid candidate accepted")


def test_compare_verdicts():
    t = make_trades(8000, 5, bad_adx=True)
    split = int(t["t"].quantile(0.7))
    good = clinic.compare(t, t[t["adx14"] < 30], split)
    assert good["verdict"] == "holds" and good["valid"]["candidate"]["expectancy"] > good["valid"]["base"]["expectancy"] + 0.1, good
    rnd = clinic.compare(t, t.sample(frac=0.6, random_state=1), split)
    assert rnd["verdict"] in ("noise", "weak"), rnd
    assert clinic.compare(t, t.head(20), split)["verdict"] == "too_few"
    assert clinic.compare(t, t, None)["verdict"] == "inconclusive"


# ------------------------------------------------------------------ live signals, similar, engine-level flows
def test_live_signals_analysis_similar_try_and_apply():
    eng = Engine(":memory:", seed_builtins=True)
    session(eng, minutes=420)
    for s in eng.repo.list():
        if s["status"] == "paper":
            eng.set_strategy_status(s["id"], "paper")
    live = clinic.trades_from_signals(eng)
    assert len(live) > 20 and {"dir", "result", "adx14", "trend_align", "session"} <= set(live.columns) and set(live["result"]) <= {"win", "loss", "draw"}
    res = eng.clinic_analyze("live", strategy_id=None, save=True)
    assert res["n_trades"] == len(live) and res["source"] == "live"
    sig = eng.db.execute("SELECT id FROM signals WHERE status IN ('win','loss') ORDER BY id DESC LIMIT 1").fetchone()[0]
    sim = eng.similar_signals(sig, k=10)
    assert sim and sim["neighbours"] and sim["summary"]["n"] > 0 and sim["features_used"], sim
    assert eng.similar_signals(999999) is None
    # try / apply with a hand-made suggestion on stored candles
    put_candles(eng, "TRY_otc", streak_series(4000, 61, 0.7))
    sid = next(s["id"] for s in eng.repo.list() if s["key"] == "candle-streak-reversal")
    sugg = {"filter": {"type": "direction_only", "value": "PUT"}, "text": "only take PUT signals"}
    out = eng.clinic_try(sid, sugg, {"symbols": ["TRY_otc"], "valid_fraction": 0.3})
    assert any(f["type"] == "direction_only" for f in out["candidate_spec"]["filters"])
    assert out["comparison"]["verdict"] in ("holds", "noise", "weak", "too_few", "inconclusive") and out["comparison"]["fit"]["base"]["trades"] > 50
    new_id = eng.clinic_apply(sid, sugg)
    d = eng.strategy_detail(new_id)
    assert d["kind"] == "custom" and d["status"] == "paper" and d["key"].startswith("candle-streak-reversal-f") and "only take PUT" in d["description"]
    assert any(f["type"] == "direction_only" for f in d["spec"]["filters"])
    assert eng.strategy_detail(sid)["spec"]["filters"] == [], "the original strategy is never modified"
    assert eng.strategy_detail(eng.clinic_apply(sid, sugg))["key"].endswith("f2"), "keys stay unique"
    # insights lifecycle
    eng.db.execute("INSERT INTO insights (created, source, strategy_id, dimension, bucket, n, acc, baseline, q_value, status) VALUES (1,'live',?,'adx','>= 30',50,0.4,0.55,0.01,'new')", (sid,))
    eng.db.commit()
    ins = eng.list_insights(strategy_id=sid, status="new")
    assert ins and eng.set_insight_status(ins[0]["id"], "dismissed") and not eng.list_insights(strategy_id=sid, status="new")
    try:
        eng.set_insight_status(ins[0]["id"], "bogus")
    except ValueError:
        pass
    else:
        raise AssertionError("bad status accepted")


def test_api_roundtrip():
    from fastapi.testclient import TestClient
    with tempfile.TemporaryDirectory() as d:
        os.environ["QXS_DB"] = str(Path(d) / "api2.db")
        sys.modules.pop("app.main", None)
        import app.main as main
        with TestClient(main.app) as c:
            put_candles(main.engine, "API_otc", streak_series(5000, 71, 0.85))
            sid = next(s["id"] for s in c.get("/api/strategies").json() if s["key"] == "candle-streak-reversal")
            r = c.post("/api/backtests", json={"strategy_id": sid, "sync": True, "label": "api", "symbols": ["API_otc"]})
            assert r.status_code == 200 and r.json()["status"] == "done", r.text
            run = r.json()
            assert run["result"]["summary"]["accuracy"] > 0.7 and run["n_trades"] > 100
            assert c.get("/api/backtests").json()[0]["id"] == run["id"]
            tp = c.get(f"/api/backtests/{run['id']}/trades", params={"limit": 3}).json()
            assert tp["total"] == run["n_trades"] and len(tp["rows"]) == 3
            assert c.post("/api/backtests", json={"spec": {"factors": []}, "sync": True}).status_code == 400
            assert c.get("/api/backtests/99999").status_code == 404
            cl = c.post("/api/clinic/analyze", json={"source": "backtest", "run_id": run["id"], "save": True})
            assert cl.status_code == 200 and cl.json()["n_trades"] == run["n_trades"] and "findings" in cl.json()
            assert c.post("/api/clinic/analyze", json={"source": "backtest", "run_id": 99999}).status_code == 400
            assert c.post("/api/clinic/analyze", json={"source": "nope"}).status_code == 400
            sugg = {"filter": {"type": "direction_only", "value": "CALL"}, "text": "only CALL"}
            tr = c.post("/api/clinic/try", json={"strategy_id": sid, "suggestion": sugg, "symbols": ["API_otc"]})
            assert tr.status_code == 200 and "comparison" in tr.json() and tr.json()["comparison"]["fit"]["base"]["trades"] > 50
            ap = c.post("/api/clinic/apply", json={"strategy_id": sid, "suggestion": sugg})
            assert ap.status_code == 200 and ap.json()["status"] == "paper" and ap.json()["kind"] == "custom"
            assert c.post("/api/clinic/apply", json={"strategy_id": sid, "suggestion": {"filter": {"type": "hours", "exclude": ["bad"]}}}).status_code == 400
            assert isinstance(c.get("/api/insights").json(), list)
            assert c.patch("/api/insights/99999", json={"status": "applied"}).status_code == 404
            assert c.patch("/api/insights/1", json={"status": "weird"}).status_code == 400
            assert c.get("/api/signals/99999/similar").status_code == 404
            assert c.delete(f"/api/backtests/{run['id']}").status_code == 200
        del os.environ["QXS_DB"]
        sys.modules.pop("app.main", None)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all clinic tests passed")
