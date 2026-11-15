"""Candle-sequence patterns: the user's 'hammer sweep' idea, its mirror, validation, and agreement between evaluator / vectorised backtest / chart.
Run from backend/:  .venv\\Scripts\\python tests\\test_patterns.py"""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import chartdata, evaluator, patterns, spec as spec_mod, vec  # noqa: E402

T0 = 1_700_000_000


def rows_from(cands):
    return [{"t": T0 + i * 60, "o": o, "h": h, "l": l, "c": c, "n": 10} for i, (o, h, l, c) in enumerate(cands)]


FLAT = [(100.0, 100.1, 99.9, 100.0)] * 8
UP = [(100, 101.2, 99.9, 101), (101, 102.2, 100.9, 102), (102, 103.2, 101.9, 103), (103, 104.2, 102.9, 104)]   # green, each high higher
HAMMER = (104.0, 104.5, 102.5, 104.4)       # green, long lower wick, low below the previous low (102.9)
SWEEP = (103.8, 103.9, 102.4, 102.6)        # red, low below the hammer's low (102.5)


def mirror(cands):
    return [(200 - o, 200 - l, 200 - h, 200 - c) for o, h, l, c in cands]


BUY = {"steps": [
    {"label": "Uptrend", "color": "green", "shape": "any", "min": 3, "max": 6, "rels": [{"prop": "high", "op": ">", "ref": "prev", "refprop": "high", "scope": "each"}]},
    {"label": "Hammer", "color": "green", "shape": "hammer", "min": 1, "max": 1, "rels": [{"prop": "low", "op": "<", "ref": "prev", "refprop": "low", "scope": "each"}]},
    {"label": "Sweep", "color": "red", "shape": "any", "min": 1, "max": 3, "rels": [{"prop": "low", "op": "<", "ref": 1, "refprop": "low", "scope": "any"}]},
]}
SELL = {"steps": [
    {"label": "Downtrend", "color": "red", "shape": "any", "min": 3, "max": 6, "rels": [{"prop": "low", "op": "<", "ref": "prev", "refprop": "low", "scope": "each"}]},
    {"label": "Shooting star", "color": "red", "shape": "star", "min": 1, "max": 1, "rels": [{"prop": "high", "op": ">", "ref": "prev", "refprop": "high", "scope": "each"}]},
    {"label": "Sweep", "color": "green", "shape": "any", "min": 1, "max": 3, "rels": [{"prop": "high", "op": ">", "ref": 1, "refprop": "high", "scope": "any"}]},
]}


def run(cands, pattern):
    df = evaluator.build_df(rows_from(cands))
    return patterns.match(df, evaluator.base_env(df), patterns.validate(pattern)[0]).to_numpy()


def test_users_hammer_sweep_buy():
    cands = FLAT + UP + [HAMMER, SWEEP]
    hit = run(cands, BUY)
    assert hit[-1] and hit.sum() == 1, hit
    # each ingredient matters
    assert not run(FLAT + UP[2:] + [HAMMER, SWEEP], BUY)[-1], "needs at least 3 uptrend candles"
    assert not run(FLAT + UP + [(104.0, 104.5, 103.0, 104.4), SWEEP], BUY)[-1], "hammer low must be below the previous candle's low"
    assert not run(FLAT + UP + [(104.0, 105.5, 102.5, 104.4), SWEEP], BUY)[-1], "that is not a hammer (long upper wick)"
    assert not run(FLAT + UP + [HAMMER, (103.8, 103.9, 102.6, 102.7)], BUY)[-1], "the red candle must trade below the hammer's low"
    assert not run(FLAT + UP + [HAMMER, (102.6, 103.9, 102.4, 103.8)], BUY)[-1], "the sweep candle must be red"
    broken = [UP[0], UP[1], (102, 102.1, 101.9, 103), UP[3]]                    # third candle's high is lower than the second's
    assert not run(FLAT + broken + [HAMMER, SWEEP], BUY)[-1], "uptrend needs higher highs"


def test_two_red_candles_and_longer_trend_also_match():
    more = [(96, 97.2, 95.9, 97), (97, 98.2, 96.9, 98), (98, 99.2, 97.9, 99), (99, 100.2, 98.9, 100)]
    assert run(FLAT + more + UP + [HAMMER, (103.8, 103.9, 103.0, 103.1), SWEEP], BUY)[-1]      # 8 greens is more than the 6 allowed, still 6 of them qualify
    two_red = [(103.8, 103.9, 103.0, 103.1), (103.1, 103.2, 102.4, 102.5)]
    assert run(FLAT + UP + [HAMMER] + two_red, BUY)[-1], "second red candle sweeps the hammer's low (scope: any)"
    four_red = two_red + [(102.5, 102.6, 102.3, 102.4), (102.4, 102.5, 102.2, 102.3)]
    assert not run(FLAT + UP + [HAMMER] + four_red, BUY)[-1], "at most 3 sweep candles are allowed after the hammer"


def test_mirror_sell():
    cands = mirror(FLAT + UP + [HAMMER, SWEEP])
    # a shooting star is the mirror of a hammer: long UPPER wick, red
    hit = run(cands, SELL)
    assert hit[-1] and hit.sum() == 1
    assert not run(FLAT + UP + [HAMMER, SWEEP], SELL)[-1]


def test_validation_rejects_bad_patterns():
    bad = copy.deepcopy(BUY)
    bad["steps"][0]["color"] = "blue"
    assert patterns.validate(bad)[0] is None
    bad = copy.deepcopy(BUY)
    bad["steps"][2]["rels"][0]["ref"] = 5
    assert "EARLIER" in patterns.validate(bad)[1][0]
    bad = copy.deepcopy(BUY)
    bad["steps"][0].update(min=4, max=2)
    assert patterns.validate(bad)[0] is None
    assert patterns.validate({"steps": []})[0] is None and patterns.validate("x")[0] is None
    wide = {"steps": [{"color": "green", "min": 1, "max": 8}] * 4}
    assert "too many" in patterns.validate(wide)[1][0]


SPEC = {
    "engine": "spec/1", "kind": "factors", "timeframes": [60], "indicators": [],
    "factors": [{"id": "hs", "name": "Hammer sweep", "weight": 100, "kind": "pattern", "call": BUY, "put": SELL, "vote": 1}],
    "gates": {"min_candles": 20, "min_agree": 1, "max_opposing_weight": 100, "min_strength": 0, "tier_a": None},
}


def test_spec_evaluator_backtest_and_chart_agree():
    v = spec_mod.validate_spec(SPEC)
    assert v["ok"], v["errors"]
    norm = v["spec"]
    pre = [(100 + 0.01 * i, 100.1 + 0.01 * i, 99.9 + 0.01 * i, 100 + 0.01 * i) for i in range(30)]
    cands = pre + UP + [HAMMER, SWEEP]
    rows = rows_from(cands)
    assert evaluator.evaluate(norm, rows[:-2], {}, {}).direction == "SKIP"
    assert evaluator.evaluate(norm, rows[:-1], {}, {}).direction == "SKIP"
    res = evaluator.evaluate(norm, rows, {}, {})
    assert res.direction == "CALL" and "pattern matched" in res.factors[0]["note"]
    series = vec.evaluate_series(norm, evaluator.build_df(rows))
    assert list(series["dir"]).index(1) == len(rows) - 1 and (series["dir"] == 1).sum() == 1
    # the mirrored market gives the PUT
    assert evaluator.evaluate(norm, rows_from(mirror(cands)), {}, {}).direction == "PUT"
    # chart data: signal marker on the sweep candle and the matched candle roles
    d = chartdata.build(rows, SPEC, {}, 36, {"hs|call": BUY})
    assert [(s["i"], s["dir"]) for s in d["signals"]] == [(35, "CALL")]
    m = d["marks"]["hs|call"]
    assert m["hits"][35] and sum(m["hits"]) == 1
    spans = m["spans"]["35"]
    assert [s[0] for s in spans] == [0, 1, 2] and spans[1][1] == spans[1][2] == 34 and spans[2][1] == 35 and spans[0][2] == 33
    bad = chartdata.build(rows, SPEC, {}, 36, {"x": {"steps": [{"color": "blue"}]}})
    assert bad["marks"]["x"]["error"] and not bad["marks"]["x"]["hits"]


def test_spec_without_any_pattern_is_rejected_and_strings_stay_valid():
    broken = copy.deepcopy(SPEC)
    broken["factors"][0].update(call=None, put=None)
    assert not spec_mod.validate_spec(broken)["ok"]
    old = {**SPEC, "indicators": [{"id": "rsi14", "type": "rsi", "n": 14}],
           "factors": [{"id": "f", "name": "f", "weight": 100, "kind": "expr", "call": "rsi14 < 30", "vote": 1}]}
    assert spec_mod.validate_spec(old)["ok"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all pattern tests passed")
