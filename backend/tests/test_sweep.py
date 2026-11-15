"""Walk-forward sweeps: statistics, verdict rules, planted edge vs pure noise, grid expansion, persistence and the API.
Run from backend/:  .venv\\Scripts\\python tests\\test_sweep.py"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import pandas as pd  # noqa: E402

from app import sweep  # noqa: E402
from app.backtest import BacktestError  # noqa: E402
from app.engine import Engine  # noqa: E402
from test_backtest import put_candles, streak_series  # noqa: E402

CFG = {**sweep.DEFAULTS, "min_trades": 100, "min_fit_trades": 50}


def seg(settled, acc, be=0.5263):
    wins = round(settled * acc)
    z, p = sweep._p_above(wins, settled, be)
    return {"trades": settled, "settled": settled, "wins": wins, "accuracy": wins / settled if settled else None, "breakeven": be, "z": z, "p": p}


def make(reversal: float, pairs=("A_otc", "B_otc", "C_otc"), n=4000):
    eng = Engine(":memory:", seed_builtins=True)
    for i, p in enumerate(pairs):
        put_candles(eng, p, streak_series(n, 100 + i, reversal))
    sid = next(s["id"] for s in eng.repo.list() if s["key"] == "candle-streak-reversal")
    return eng, sid


# ------------------------------------------------------------------ statistics and verdicts
def test_break_even_p_value_and_needed_trades():
    z, p = sweep._p_above(550, 1000, 1 / 1.9)                     # 55 % at 90 % payout (break-even 52.6 %)
    assert 1.4 < z < 1.6 and 0.05 < p < 0.08
    z0, p0 = sweep._p_above(526, 1000, 1 / 1.9)
    assert abs(z0) < 0.1 and 0.45 < p0 < 0.55
    assert sweep.trades_needed(0.50, 0.526) is None
    n = sweep.trades_needed(0.55, 0.526)
    assert 2300 < n < 3200                                      # a 2.4-point edge needs thousands of trades to confirm
    assert sweep.noise_max_z(21) > 2.0 > sweep.noise_max_z(2) - 1


def test_segment_stats_uses_the_trades_own_payout():
    t = pd.DataFrame({"t": range(10), "result": ["win"] * 6 + ["loss"] * 4, "profit": [0.9] * 6 + [-1.0] * 4, "payout": [90.0] * 10})
    s = sweep.segment_stats(t)
    assert s["settled"] == 10 and abs(s["accuracy"] - 0.6) < 1e-9 and abs(s["breakeven"] - 1 / 1.9) < 1e-9
    assert s["exp_ci_low"] < s["expectancy"] < s["exp_ci_high"]
    assert sweep.segment_stats(t.iloc[0:0])["accuracy"] is None


def test_verdict_rules():
    j = lambda fit, hold, tried=1: sweep.judge(fit, hold, tried, CFG)[0]  # noqa: E731
    assert j(seg(0, 0.5) | {"accuracy": None}, seg(0, 0.5) | {"accuracy": None}) == "no_trades"
    assert j(seg(20, 0.7), seg(500, 0.7)) == "need_data"                                  # fit too small to say anything
    assert j(seg(500, 0.50), seg(500, 0.60)) == "no_edge"                                 # fit did not beat break-even
    assert j(seg(500, 0.58), seg(40, 0.7)) == "need_data"                                 # holdout too small
    assert j(seg(500, 0.58), seg(500, 0.50)) == "overfit"                                 # edge vanished
    assert j(seg(500, 0.58), seg(500, 0.62)) == "pass"
    assert j(seg(500, 0.58), seg(500, 0.545)) == "promising"                              # both above break-even, not significant
    assert j(seg(500, 0.58), seg(500, 0.62), tried=100000) == "promising"                 # same data, but 100k things were tried
    v, why, adj = sweep.judge(seg(500, 0.58), seg(500, 0.62), 4, CFG)
    assert adj <= 4 * seg(500, 0.62)["p"] + 1e-12 and "4 candidates" in why


# ------------------------------------------------------------------ planted edge vs noise
def test_planted_edge_passes_and_noise_does_not():
    eng, sid = make(0.85)
    res = sweep.run_sweep(eng.db, sweep.expand_candidates([{"strategy_id": sid}], eng.sweeps._spec_of), {}, {**CFG, "tfs": [60]})
    c = res["candidates"][0]
    assert c["verdict"] == "pass", c["reason"]
    assert c["fit"]["accuracy"] > 0.7 and c["holdout"]["accuracy"] > 0.7 and c["adj_p"] < 0.05
    assert c["pairs_tested"] >= 2 and c["pairs_above_breakeven"] == c["pairs_tested"]

    eng2, sid2 = make(0.5)
    res2 = sweep.run_sweep(eng2.db, sweep.expand_candidates([{"strategy_id": sid2}], eng2.sweeps._spec_of), {}, {**CFG, "tfs": [60]})
    assert res2["candidates"][0]["verdict"] != "pass", res2["candidates"][0]["reason"]


def test_split_is_chronological_and_shared():
    eng, sid = make(0.85)
    res = sweep.run_sweep(eng.db, sweep.expand_candidates([{"strategy_id": sid}], eng.sweeps._spec_of), {}, {**CFG, "tfs": [60]})
    first, last = eng.db.execute("SELECT MIN(ts), MAX(ts) FROM candles").fetchone()
    assert abs((res["split_t"] - first) / (last - first) - 0.7) < 0.02                    # newest 30 % is the holdout


def test_more_candidates_make_the_bar_harder():
    eng, sid = make(0.6)
    one = sweep.run_sweep(eng.db, sweep.expand_candidates([{"strategy_id": sid}], eng.sweeps._spec_of), {}, {**CFG, "tfs": [60]})
    many = sweep.run_sweep(eng.db, sweep.expand_candidates([{"strategy_id": sid}], eng.sweeps._spec_of), {}, {**CFG, "tfs": [60]},
                           prior_hashes={f"other{i}" for i in range(500)})
    a, b = one["candidates"][0]["adj_p"], many["candidates"][0]["adj_p"]
    assert b >= a and many["tried_total"] == 501 and many["noise_max_z"] > one["noise_max_z"]


# ------------------------------------------------------------------ grids and errors
def test_grid_expansion_and_bad_paths():
    eng, sid = make(0.5)
    c = sweep.expand_candidates([{"strategy_id": sid, "grid": {"gates.min_candles": [60, 80], "expiry_candles": [1]}}], eng.sweeps._spec_of)
    assert len(c) == 2 and c[0]["params"] == {"gates.min_candles": 60, "expiry_candles": 1} and "gates.min_candles=80" in c[1]["name"]
    assert c[0]["spec"]["gates"]["min_candles"] == 60 and c[1]["spec"]["gates"]["min_candles"] == 80
    bad = sweep.expand_candidates([{"strategy_id": sid, "grid": {"gates.nope": [1]}}], eng.sweeps._spec_of)
    assert bad[0]["spec"] is None and "no such field" in bad[0]["error"]
    missing = sweep.expand_candidates([{"strategy_id": 99999}], eng.sweeps._spec_of)
    assert missing[0]["error"] and missing[0]["spec"] is None
    try:
        sweep.expand_candidates([{"strategy_id": sid, "grid": {"gates.min_candles": list(range(60, 400))}}], eng.sweeps._spec_of)
        raise AssertionError("expected the candidate limit")
    except BacktestError:
        pass


# ------------------------------------------------------------------ persistence + API
def test_runner_persists_and_counts_tried_across_sweeps():
    eng, sid = make(0.85)
    cfg = {"candidates": [{"strategy_id": sid}], "tfs": [60], "min_trades": 100, "min_fit_trades": 50}
    r1 = eng.sweeps.get(eng.sweeps.start(cfg, sync=True))
    assert r1["status"] == "done" and r1["result"]["tried_total"] == 1
    cfg2 = {"candidates": [{"strategy_id": sid, "grid": {"gates.min_candles": [60, 70, 80]}}], "tfs": [60], "min_trades": 100, "min_fit_trades": 50}
    r2 = eng.sweeps.get(eng.sweeps.start(cfg2, sync=True))
    assert r2["result"]["tried_now"] == 3 and r2["result"]["tried_total"] == 3            # min_candles=60 is the same hypothesis as sweep 1's; 70 and 80 are new
    r3 = eng.sweeps.get(eng.sweeps.start(cfg2, sync=True))                                # re-running identical candidates adds nothing to the luck counter
    assert r3["result"]["tried_total"] == 3 and r3["result"]["noise_max_z"] == r2["result"]["noise_max_z"]
    assert r3["result"]["candidates"][0]["adj_p"] == r2["result"]["candidates"][0]["adj_p"]
    cfg3 = {"candidates": [{"strategy_id": sid, "grid": {"gates.min_candles": [90]}}], "tfs": [60], "min_trades": 100, "min_fit_trades": 50}
    assert eng.sweeps.get(eng.sweeps.start(cfg3, sync=True))["result"]["tried_total"] == 4  # a genuinely new variant does
    cfg4 = {**cfg, "tfs": [60, 300]}
    assert eng.sweeps.get(eng.sweeps.start(cfg4, sync=True))["result"]["tried_total"] == 5  # same strategy on other timeframes is a different hypothesis
    assert len(eng.sweeps.list()) == 5 and eng.sweeps.list()[0]["id"] > eng.sweeps.list()[1]["id"]            # newest first
    assert eng.sweeps.delete(r1["id"]) and eng.sweeps.get(r1["id"]) is None


def test_min_payout_filters_pairs():
    eng, sid = make(0.85)
    cands = sweep.expand_candidates([{"strategy_id": sid}], eng.sweeps._spec_of)
    pay = {"A_otc": 92.0, "B_otc": 60.0, "C_otc": 88.0}
    every = sweep.run_sweep(eng.db, cands, pay, {**CFG, "tfs": [60]})
    only = sweep.run_sweep(eng.db, cands, pay, {**CFG, "tfs": [60], "min_payout": 85})
    assert every["config"]["pairs_used"] is None and only["config"]["pairs_used"] == 2 and only["config"]["min_payout"] == 85
    assert only["candidates"][0]["holdout"]["settled"] < every["candidates"][0]["holdout"]["settled"]
    assert abs(only["candidates"][0]["holdout"]["breakeven"] - (0.5 * (1 / 1.92 + 1 / 1.88))) < 0.005         # b/e reflects only the pairs kept
    assert every["candidates"][0]["holdout"]["breakeven"] > only["candidates"][0]["holdout"]["breakeven"]      # the 60 % pair raised the bar
    try:
        sweep.run_sweep(eng.db, cands, pay, {**CFG, "tfs": [60], "min_payout": 99})
        raise AssertionError("expected: no pair pays that much")
    except BacktestError:
        pass


def test_all_strategies_and_errors_do_not_abort():
    eng, _ = make(0.5, pairs=("A_otc",), n=1500)
    r = eng.sweeps.get(eng.sweeps.start({"all_strategies": True, "tfs": [60], "min_trades": 50, "min_fit_trades": 20}, sync=True))
    names = [c["name"] for c in r["result"]["candidates"]]
    assert r["status"] == "done" and len(names) >= 10 and all(c["verdict"] in sweep.VERDICT_ORDER for c in r["result"]["candidates"])
    assert sorted(c["rank"] for c in r["result"]["candidates"]) == list(range(1, len(names) + 1))
    for cfg in ({"candidates": []}, {"candidates": [{"strategy_id": 1}], "holdout_fraction": 1.5}):
        try:
            eng.sweeps.start(cfg, sync=True)
            raise AssertionError("expected an error")
        except BacktestError:
            pass
    empty = Engine(":memory:", seed_builtins=True)                                        # no candles at all: reported as a failed run
    rid = empty.sweeps.start({"all_strategies": True, "tfs": [60]}, sync=True)
    assert empty.sweeps.get(rid)["status"] == "error"


# ------------------------------------------------------------------ martingale scoring
def _mtg_frame(rows):
    """rows: (t, result, profit, mtg_pl, outcome, skip, stake)"""
    return pd.DataFrame(rows, columns=["t", "result", "profit", "mtg_pl", "mtg_outcome", "mtg_skip", "mtg_stake"])


def test_mtg_segment_math_by_hand():
    nan = float("nan")
    f = _mtg_frame([(1, "win", 0.85, 0.85, "win", False, 1.0),
                    (2, "loss", -1.0, 2.2 * 0.85 - 1, "mtg1", False, 2.2),
                    (3, "loss", -1.0, -8.04, "lost", False, 4.84),
                    (4, "win", 0.85, 0.0, "", True, 1.0),                      # fired mid-ladder: not taken
                    (5, "loss", -1.0, nan, "open", False, 2.2)])               # unfinished ladder: not guessed
    s = sweep.segment_stats_mtg(f)
    assert s["settled"] == 3 and s["skipped"] == 1 and s["trades"] == 5 and s["open_ladders"] == 1
    assert abs(s["mean"] - (0.85 + 0.87 - 8.04) / 3) < 1e-9 and s["worst"] == -8.04 and s["lost_ladders"] == 1 and s["recovered"] == 1
    assert abs(s["flat_mean"] - (0.85 - 1 - 1) / 3) < 1e-9 and s["stake_max"] == 4.84
    assert s["z"] < 0 and s["p"] > 0.5                                         # a losing ladder average: no evidence of an edge
    assert sweep.segment_stats_mtg(f.iloc[0:0])["settled"] == 0
    assert sweep.trades_needed_mtg(0.1, 1.0) > 500 and sweep.trades_needed_mtg(-0.1, 1.0) is None


def test_mtg_verdict_rules_and_hashes():
    def sg(n, mean, p=0.5):
        return {"settled": n, "mean": mean, "p": p, "z": 0.0}
    j = lambda fit, hold, tried=1: sweep.judge_mtg(fit, hold, tried, CFG)[0]  # noqa: E731
    assert j({"settled": 0, "mean": None, "p": None}, {"settled": 0, "mean": None, "p": None}) == "no_trades"
    assert j(sg(20, 0.5), sg(500, 0.5, 0.001)) == "need_data"
    assert j(sg(500, -0.1), sg(500, 0.4, 0.001)) == "no_edge"
    assert j(sg(500, 0.1), sg(50, 0.4, 0.001)) == "need_data"
    assert j(sg(500, 0.1), sg(500, -0.2)) == "overfit"
    assert j(sg(500, 0.1), sg(500, 0.2, 0.001)) == "pass"
    assert j(sg(500, 0.1), sg(500, 0.2, 0.001), tried=1000) == "promising"
    spec = {"engine": "spec/1"}
    assert sweep.spec_hash(spec, [60]) == sweep.spec_hash(spec, [60], (0, 2.2))           # flat hypotheses keep their identity
    assert sweep.spec_hash(spec, [60], (2, 2.2)) != sweep.spec_hash(spec, [60])            # MTG is a different way of trading it
    assert sweep.spec_hash(spec, [60], (2, 2.2)) != sweep.spec_hash(spec, [60], (2, 2.0))


def test_mtg_sweep_planted_edge_and_noise():
    eng, sid = make(0.85)
    cands = sweep.expand_candidates([{"strategy_id": sid}], eng.sweeps._spec_of)
    flat = sweep.run_sweep(eng.db, cands, {}, {**CFG, "tfs": [60]})
    res = sweep.run_sweep(eng.db, cands, {}, {**CFG, "tfs": [60], "mtg_steps": 2, "mtg_mult": 2.2})
    c = res["candidates"][0]
    assert res["config"]["mtg_steps"] == 2 and c["verdict"] == "pass", c["reason"]
    assert c["fit"]["mean"] > 0 and c["holdout"]["mean"] > 0 and c["holdout"]["lost_ladders"] >= 0 and c["holdout"]["worst"] <= 0
    assert c["flat_holdout"]["accuracy"] == flat["candidates"][0]["holdout"]["accuracy"]    # same trades, flat numbers kept for comparison
    h = c["holdout"]
    assert h["settled"] + h["skipped"] + h["open_ladders"] == h["trades"] and h["open_ladders"] >= 0       # every signal accounted for exactly once
    assert c["spec_hash"] != flat["candidates"][0]["spec_hash"] and c["pairs_above_breakeven"] == c["pairs_tested"] >= 2

    eng2, sid2 = make(0.5)
    n = sweep.run_sweep(eng2.db, sweep.expand_candidates([{"strategy_id": sid2}], eng2.sweeps._spec_of), {}, {**CFG, "tfs": [60], "mtg_steps": 2})
    nc = n["candidates"][0]
    assert nc["verdict"] != "pass" and nc["holdout"]["mean"] < 0.15 and nc["holdout"]["worst"] <= -8.0 + 1e-9 and nc["holdout"]["max_drawdown"] > 8
    try:
        sweep.run_sweep(eng2.db, sweep.expand_candidates([{"strategy_id": sid2}], eng2.sweeps._spec_of), {}, {**CFG, "tfs": [60], "mtg_steps": 9})
        raise AssertionError("expected an error")
    except BacktestError:
        pass


def test_mtg_rerun_not_double_counted_but_flat_vs_mtg_are_two_hypotheses():
    eng, sid = make(0.6)
    base = {"candidates": [{"strategy_id": sid}], "tfs": [60], "min_trades": 100, "min_fit_trades": 50}
    assert eng.sweeps.get(eng.sweeps.start(base, sync=True))["result"]["tried_total"] == 1
    m = {**base, "mtg_steps": 2, "mtg_mult": 2.2}
    assert eng.sweeps.get(eng.sweeps.start(m, sync=True))["result"]["tried_total"] == 2          # flat and MTG differ
    assert eng.sweeps.get(eng.sweeps.start(m, sync=True))["result"]["tried_total"] == 2          # the same MTG sweep again adds nothing


def test_api():
    import os
    import tempfile

    from fastapi.testclient import TestClient
    with tempfile.TemporaryDirectory() as d:
        os.environ["QXS_DB"] = str(Path(d) / "sweep_api.db")
        sys.modules.pop("app.main", None)
        import app.main as main
        with TestClient(main.app) as c:
            for i, p in enumerate(("API1_otc", "API2_otc")):
                put_candles(main.engine, p, streak_series(4000, 200 + i, 0.85))
            sid = next(s["id"] for s in c.get("/api/strategies").json() if s["key"] == "candle-streak-reversal")
            r = c.post("/api/sweeps", json={"candidates": [{"strategy_id": sid}], "tfs": [60], "min_trades": 100, "min_fit_trades": 50, "sync": True})
            assert r.status_code == 200 and r.json()["status"] == "done", r.text
            run = r.json()
            assert run["result"]["candidates"][0]["verdict"] == "pass" and run["result"]["tried_total"] == 1
            assert c.get("/api/sweeps").json()[0]["id"] == run["id"]
            assert c.get(f"/api/sweeps/{run['id']}").json()["result"]["split_t"] == run["result"]["split_t"]
            m = c.post("/api/sweeps", json={"candidates": [{"strategy_id": sid}], "tfs": [60], "min_trades": 100, "min_fit_trades": 50, "mtg_steps": 2, "mtg_mult": 2.2, "sync": True})
            assert m.status_code == 200 and m.json()["result"]["config"]["mtg_steps"] == 2 and m.json()["result"]["candidates"][0]["flat_holdout"], m.text
            assert c.post("/api/sweeps", json={"candidates": [{"strategy_id": sid}], "mtg_steps": 9}).status_code == 422
            assert c.post("/api/sweeps", json={"candidates": []}).status_code == 400
            assert c.get("/api/sweeps/99999").status_code == 404
            assert c.delete("/api/sweeps/99999").status_code == 404
            assert c.delete(f"/api/sweeps/{run['id']}").status_code == 200


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
