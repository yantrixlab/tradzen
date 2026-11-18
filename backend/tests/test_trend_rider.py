"""examples/trend-rider-engulf.json: engulfing-candle zones traded only the way the market structure LEADS (ms.lead).
The case that motivated `lead`: higher high, higher low, then a LOWER high - the up-move failed, so a green candle at the demand zone must NOT be bought;
the supply zone at the lower high is waited for and a red candle in it is sold. The mirror (down-move failing -> higher low) works the other way round.
Run from backend/:  .venv\\Scripts\\python tests\\test_trend_rider.py"""
import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import evaluator, vec  # noqa: E402
from app.spec import validate_spec  # noqa: E402
from test_structure import zigzag  # noqa: E402

RAW = json.load(open(HERE.parent.parent / "examples" / "trend-rider-engulf.json"))
SPEC = validate_spec(RAW)["spec"]
_old = copy.deepcopy(RAW)
_old["factors"][0]["call"] = _old["factors"][0]["call"].replace("ms.lead", "ms.bias")
_old["factors"][0]["put"] = _old["factors"][0]["put"].replace("ms.lead", "ms.bias")
BIAS_SPEC = validate_spec(_old)["spec"]                  # the earlier version: an up-trend lives on through a lower-high pullback


def cd(o, c, w=0.03):
    return (o, max(o, c) + w, min(o, c) - w, c)


def mirror(cs):
    return [(40 - o, 40 - l, 40 - h, 40 - c) for o, h, l, c in cs]


def rows_of(cs):
    return [{"t": 1_700_000_000 + 60 * i, "o": o, "h": h, "l": l, "c": c, "n": 10} for i, (o, h, l, c) in enumerate(cs)]


def dirs(spec, cs):
    r = rows_of(cs)
    return [evaluator.evaluate(spec, evaluator.tail_for(spec, r[:i + 1]), {}, {"payout": 90.0}).direction for i in range(len(r))]


def signals(spec, cs):
    return [(i, d) for i, d in enumerate(dirs(spec, cs)) if d != "SKIP"]


def failed_high_chart():
    cs = zigzag([6, 8, 7, 10], per=7) + zigzag([10, 12, 11, 14, 12.5, 16, 14.5, 18, 16.4], per=6)     # up-trend: HH, HL ... HH (18), pull back to 16.4
    cs += [cd(16.4, 16.15), cd(16.1, 16.75)]                                  # a red candle engulfed by a green one: demand zone ~16.1-16.45 (and the higher low)
    cs += [cd(16.75, 17.0), cd(17.0, 17.2), cd(17.25, 16.7)]                  # a green candle engulfed by a big red one: supply zone ~16.97-17.23 = the LOWER high (17.28 < 18)
    cs += [cd(16.7, 16.5), cd(16.5, 16.35), cd(16.3, 16.42)]                  # price comes back to the demand zone and a green candle forms in it  <- must not be bought
    cs += [cd(16.42, 16.6), cd(16.6, 16.85), cd(16.85, 17.08)]                # two green candles climb into the supply zone
    cs += [cd(17.1, 16.9)]                                                    # a red candle in the supply zone  <- sold
    cs += [cd(16.9, 16.6), cd(16.6, 16.3)]
    return cs


WRONG_CALL, PUT_AT = 76, 80


def test_spec_is_valid_and_uses_no_indicator_conditions():
    v = validate_spec(RAW)
    assert v["ok"], v
    assert {i["type"] for i in SPEC["indicators"]} == {"structure", "engulf"} and SPEC["filters"][0]["type"] == "one_per_run"
    text = SPEC["factors"][0]["call"] + SPEC["factors"][0]["put"]
    for banned in ("ema", "rsi", "adx", "macd", "bb.", "stoch", "cci", "atr"):
        assert banned not in text


def test_the_earlier_version_bought_the_demand_zone_after_the_lower_high():
    assert (WRONG_CALL, "CALL") in signals(BIAS_SPEC, failed_high_chart())


def test_after_a_lower_high_no_call_is_given_and_the_supply_zone_gives_the_put():
    cs = failed_high_chart()
    assert signals(SPEC, cs) == [(PUT_AT, "PUT")]


def test_the_mirror_chart_is_a_down_move_that_failed_to_make_a_new_low_and_gives_a_call():
    cs = mirror(failed_high_chart())
    assert signals(SPEC, cs) == [(PUT_AT, "CALL")]                          # LL, LH, then a HIGHER low: no put at the supply zone, the demand zone is bought
    assert (WRONG_CALL, "PUT") in signals(BIAS_SPEC, cs)


def test_a_clean_up_trend_still_buys_its_demand_zone():
    cs = zigzag([6, 8, 7, 10], per=7) + zigzag([10, 12, 11, 14, 12.5, 16, 14.5, 18, 16.4], per=6)
    cs += [cd(16.4, 16.15), cd(16.1, 16.75), cd(16.75, 17.4), cd(17.4, 18.6), cd(18.6, 19.0)]       # demand zone, then a new HIGHER high (19)
    cs += [cd(19.0, 18.5), cd(18.5, 18.0), cd(18.0, 17.5), cd(17.5, 17.0), cd(17.0, 16.6), cd(16.6, 16.3)]   # pullback to the demand zone
    cs += [cd(16.3, 16.55)]                                                   # a green candle in the zone
    got = signals(SPEC, cs)
    assert got and all(d == "CALL" for _, d in got) and got[-1][0] == len(cs) - 1, got


def first_lower_low_chart():
    cs = zigzag([6, 8, 7, 10], per=7) + zigzag([10, 12, 11, 14, 12.5, 16, 14.5, 18, 14.6], per=6)       # a long up-trend to 18
    cs += [cd(14.6, 14.25), cd(14.2, 14.7)]                                   # red candle engulfed by green: demand zone ~14.2-14.65; its low (~14.17) is the FIRST lower low (< 14.5)
    cs += [cd(14.7, 15.1), cd(15.1, 15.5), cd(15.5, 15.3), cd(15.3, 15.0), cd(15.0, 14.7), cd(14.7, 14.45), cd(14.45, 14.35)]
    cs += [cd(14.3, 14.5)]                                                    # back in the demand zone: a green candle  <- bought, the up-trend is not over yet
    cs += [cd(14.5, 14.8), cd(14.8, 15.0)]
    return cs


def test_the_first_lower_low_after_an_up_trend_does_not_turn_the_strategy_to_puts():
    cs = first_lower_low_chart()
    assert signals(SPEC, cs) == [(78, "CALL")]                               # no put until a candle closes below that lower low


def test_the_mirror_first_higher_high_after_a_down_trend_does_not_turn_the_strategy_to_calls():
    assert signals(SPEC, mirror(first_lower_low_chart())) == [(78, "PUT")]


def test_backtest_and_live_agree_candle_by_candle_on_random_walks():
    rng = np.random.default_rng(21)
    p = np.cumsum(rng.normal(0, 0.15, 800)) + 50
    cs = [(p[i - 1], max(p[i - 1], p[i]) + .05, min(p[i - 1], p[i]) - .05, p[i]) for i in range(1, 800)]
    df = pd.DataFrame([{"t": r["t"], "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"], "n": 10} for r in rows_of(cs)])
    want = vec.evaluate_series(SPEC, df, 90.0)["dir"].tolist()
    got = [{"CALL": 1, "PUT": -1}.get(d, 0) for d in dirs(SPEC, cs)]
    assert want == got and any(want)                                          # not vacuous


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
