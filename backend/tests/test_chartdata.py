import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import chartdata  # noqa: E402


def rows(n=300, seed=3):
    rng = np.random.default_rng(seed)
    px, out = 1.1, []
    for i in range(n):
        o = px
        px = max(0.5, px + rng.normal(0, 0.0008))
        h, l = max(o, px) + abs(rng.normal(0, 0.0003)), min(o, px) - abs(rng.normal(0, 0.0003))
        out.append({"t": 1_700_000_000 + i * 60, "o": o, "h": h, "l": l, "c": px, "n": 20})
    return out


SPEC = {
    "engine": "spec/1", "kind": "factors", "timeframes": [60],
    "indicators": [{"id": "rsi14", "type": "rsi", "n": 14}, {"id": "bb", "type": "bollinger", "n": 20, "k": 2}],
    "factors": [{"id": "f1", "name": "RSI", "weight": 100, "kind": "expr", "call": "rsi14 < 45", "put": "rsi14 > 55", "vote": 1}],
    "gates": {"min_candles": 60, "min_agree": 1, "max_opposing_weight": 100, "min_strength": 0, "tier_a": None},
}


def test_build_plots_indicators_marks_and_signals():
    broken = {**SPEC, "indicators": SPEC["indicators"] + [{"id": "bad", "type": "nope"}]}
    assert chartdata.build(rows(), broken, {}, 50)["signals"] == []   # an invalid spec still charts, just without markers
    d = chartdata.build(rows(), SPEC, {"a": "rsi14 < 45", "b": "close < bb.lower", "c": "nope > 1"}, count=100)
    assert len(d["candles"]) == 100
    ids = {i["id"]: i for i in d["indicators"]}
    assert set(ids) == {"rsi14", "bb"}                                  # the malformed indicator is dropped, not fatal
    assert any("bad" in e for e in chartdata.build(rows(), broken, {}, 50)["errors"])
    assert ids["rsi14"]["panel"] == "osc" and ids["bb"]["panel"] == "price"
    assert all(len(s["values"]) == 100 for i in d["indicators"] for s in i["series"])
    assert len(d["marks"]["a"]["hits"]) == 100 and d["marks"]["a"]["error"] is None
    assert d["marks"]["c"]["error"] and d["marks"]["c"]["hits"] == []   # one bad clause never fails the request
    # every CALL marker sits on a candle where the same condition is true
    calls = [s["i"] for s in d["signals"] if s["dir"] == "CALL"]
    assert calls and all(d["marks"]["a"]["hits"][i] for i in calls)
    assert any(s["dir"] == "PUT" for s in d["signals"]) and set(calls).isdisjoint(s["i"] for s in d["signals"] if s["dir"] == "PUT")


def test_signals_match_the_real_evaluator_on_the_last_candle():
    from app import evaluator, spec as spec_mod
    r = rows(250, seed=9)
    norm = spec_mod.validate_spec(SPEC)["spec"]
    for cut in (200, 220, 240, 250):
        d = chartdata.build(r[:cut], SPEC, {}, count=60)
        real = evaluator.evaluate(norm, r[:cut], {}, {}).direction
        mine = next((s["dir"] for s in d["signals"] if s["i"] == 59), "SKIP")
        assert mine == real, (cut, mine, real)


def test_half_written_spec_still_returns_candles():
    d = chartdata.build(rows(), {"indicators": [{"id": "x", "type": "ema", "n": 20}], "factors": []}, {"m": "close > x"}, 80)
    assert len(d["candles"]) == 80 and d["indicators"][0]["id"] == "x" and d["signals"] == [] and d["note"]
    assert d["marks"]["m"]["error"] is None


def test_too_few_candles():
    try:
        chartdata.build(rows(10), SPEC, {}, 50)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "candles" in str(e)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all chartdata tests passed")
