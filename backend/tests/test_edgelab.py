"""Edge Lab: every condition is valid spec syntax, pure noise yields NO EDGE (with a concrete best-effort strategy built and tested anyway), a planted habit yields EDGE FOUND,
and an edge creates paper strategies + redesigned challengers.  Run from backend/:  .venv\\Scripts\\python tests\\test_edgelab.py"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from app import battery, edgelab as E  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.spec import validate_spec  # noqa: E402
from test_backtest import put_candles  # noqa: E402
from test_momentum_ai import waves  # noqa: E402

CFG = {"global": False, "ledger": False, "final_max": 2, "redesign_max": 2}


def _engine(persist, pairs=4, n=6000, seed=20):
    eng = Engine(":memory:", seed_builtins=True)
    for i in range(pairs):
        sym = f"P{i}_otc"
        eng.instruments[sym] = {"symbol": sym, "name": sym, "payout": 88, "open": True, "market": "OTC"}
        put_candles(eng, sym, waves(n, seed + i, persist))
    return eng


def test_every_condition_is_valid_strategy_syntax_in_both_directions():
    for call, put in ((a[2], a[3] or a[2]) for a in E.ATOMS):
        v = validate_spec(battery.spec_from_expr(call, put, [60], 80))
        assert v["ok"], (call, put, v["errors"][:2])
    assert len({a[0] for a in E.ATOMS}) == len(E.ATOMS)


def test_pure_noise_finds_no_edge_but_still_builds_and_tests_a_strategy():
    eng = _engine(0.5)
    res = E.run(eng.db, CFG)
    assert res["outcome"]["status"] == "no_edge", res["outcome"]
    assert res["funnel"]["rules_scored"] > 1000 and res["funnel"]["kept_after_discovery"] == 30
    assert len(res["candidates"]) == 1 and res["candidates"][0]["best_effort"] and res["candidates"][0]["battery"]["verdict"] == "FAIL"
    assert res["strategy_spec"] and res["redesign"] == []                              # nothing is redesigned without an edge


def test_a_real_habit_is_found_confirmed_and_passes_the_full_battery():
    eng = _engine(0.72)
    res = E.run(eng.db, CFG)
    assert res["outcome"]["status"] == "edge_found", (res["outcome"], res["funnel"])
    best = next(c for c in res["candidates"] if c["rank"] == res["outcome"]["best"])
    assert best["battery"]["verdict"] == "PASS" and best["confirm"]["ok"] and best["battery"]["newest"]["win"] > 0.65
    assert not best["best_effort"] and res["funnel"]["confirmed"] >= 1


def test_an_edge_creates_a_paper_strategy_and_redesigned_challengers_but_noise_creates_nothing():
    eng = _engine(0.72)
    lab = E.EdgeLab(eng)
    lab.start(CFG, sync=True)
    rid = lab.runs()[0]["id"]
    assert lab.runs()[0]["outcome"] == "edge_found"
    before = len(eng.repo.list())
    made = lab.apply(rid)["created"]
    assert len(made) >= 1 and len(eng.repo.list()) == before + len(made)
    assert eng.repo.get(made[0])["status"] == "paper" and "EDGE LAB" in eng.repo.get(made[0])["name"]
    assert lab.get(rid)["applied"] is True
    noise = E.EdgeLab(_engine(0.5))
    noise.start(CFG, sync=True)
    try:
        noise.apply(noise.runs()[0]["id"])
        raise AssertionError("must refuse")
    except ValueError as e:
        assert "no edge" in str(e)


def test_redesign_adds_the_edge_as_one_more_required_agreement():
    eng = _engine(0.5, pairs=1, n=800)
    spec = eng.repo.get(eng.strategy_id)["spec"]
    new = E.with_edge(spec, "green", "red")
    assert new["gates"]["min_agree"] == spec["gates"]["min_agree"] + 1 and new["gates"]["max_opposing_weight"] == 0
    assert new["factors"][-1]["id"] == "edge_rule" and validate_spec(new)["ok"]
    assert spec["gates"]["min_agree"] != new["gates"]["min_agree"]                     # the original is untouched


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
