"""examples/supply-demand-zones.json on hand-drawn charts: fresh strong zone + confirmation + trend, and every reason NOT to trade.
Run from backend/:  .venv\\Scripts\\python tests\\test_zone_strategy.py"""
import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import evaluator, vec, zones  # noqa: E402
from app.spec import validate_spec  # noqa: E402

RAW = json.load(open(HERE.parent.parent / "examples" / "supply-demand-zones.json"))
SPEC = validate_spec(RAW)["spec"]
ZP = {k: v for k, v in SPEC["indicators"][0].items() if k not in ("id", "type")}
K = 40.0


def cd(o, c, w=0.03):
    return (o, max(o, c) + w, min(o, c) - w, c)


def trend(n=80, p0=5.0, step=0.1):
    out, p = [], p0
    for _ in range(n):
        out.append(cd(p, p + step, 0.02))
        p += step
    return out


def rows_of(cs):
    return [{"t": 1_700_000_000 + 60 * i, "o": o, "h": h, "l": l, "c": c, "n": 10} for i, (o, h, l, c) in enumerate(cs)]


def frame(cs):
    return pd.DataFrame([{"t": r["t"], "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"], "n": 10} for r in rows_of(cs)])


def dirs(spec, cs):
    r = rows_of(cs)
    return [evaluator.evaluate(spec, r[:i + 1], {}, {"payout": 90.0}).direction for i in range(len(r))]


def signals(spec, cs):
    return [(i, d) for i, d in enumerate(dirs(spec, cs)) if d != "SKIP"]


def mirror(cs):
    return [(K - o, K - l, K - h, K - c) for o, h, l, c in cs]


def without(spec, *types):
    s = copy.deepcopy(spec)
    s["filters"] = [f for f in s["filters"] if f["type"] not in types]
    return s


def edit(spec, old, new):
    s = copy.deepcopy(spec)
    f = s["factors"][0]
    f["call"], f["put"] = f["call"].replace(old, new), f["put"].replace(old, new)
    return s


B = trend()[-1][3]                                                   # top of the up-trend: 13.0


def base(b=B):
    return [cd(b, b + .03), cd(b + .03, b), cd(b, b + .04), cd(b + .04, b + .01), cd(b + .01, b + .05), cd(b + .05, b + .02), cd(b + .02, b + .04), cd(b + .04, b + .02)]


def departure_and_run(b=B):
    return [cd(b + .02, b + .70, .03), cd(b + .70, b + 1.0), cd(b + 1.0, b + 1.3), cd(b + 1.3, b + 1.5)]


def pullback_into_zone(b=B, depth=0.10):
    return [cd(b + 1.5, b + 1.2), cd(b + 1.2, b + .8), cd(b + .8, b + .45), cd(b + .45, b + depth)]


def confirmation(b=B):
    return [cd(b + .08, b + .55, .02)]


def demand_setup():
    return trend() + base() + departure_and_run() + pullback_into_zone() + confirmation()


# ------------------------------------------------------------------ the full idea
def test_fresh_strong_demand_zone_with_confirmation_in_an_uptrend_gives_one_call():
    cs = demand_setup()
    assert signals(SPEC, cs) == [(len(cs) - 1, "CALL")]                       # only the confirmation candle - nothing during the base, the run-up or the pullback


def test_the_mirror_chart_is_a_supply_zone_in_a_downtrend_and_gives_one_put():
    cs = mirror(demand_setup())
    assert signals(SPEC, cs) == [(len(cs) - 1, "PUT")]


def test_no_confirmation_no_trade():
    cs = trend() + base() + departure_and_run() + pullback_into_zone()                                   # price is IN the zone, but no confirming candle yet
    assert signals(SPEC, cs) == []
    for conf in (cd(B + .30, B + .12, .02),                                                              # a red candle
                 cd(B + .08, B + .20, .02),                                                              # green but does not clear the previous high
                 (B + .08, B + .60, B + .05, B + .20)):                                                  # long upper wick, small body
        assert signals(SPEC, cs + [conf]) == [], conf


# ------------------------------------------------------------------ what makes a zone worth trading
def test_a_broken_zone_is_not_traded():
    cs = trend() + base() + departure_and_run() + pullback_into_zone(depth=-0.55) + [cd(B - 0.50, B + 0.10, .02)]       # price smashed through the zone first
    assert signals(SPEC, cs) == []


def test_a_weak_zone_visited_too_often_is_not_traded():
    visit = lambda: [cd(B + .9, B + .5), cd(B + .5, B + .2), cd(B + .2, B + .10)]            # noqa: E731  a further visit to the zone
    leave = lambda: [cd(B + .10, B + .5), cd(B + .5, B + .9)]                                # noqa: E731
    third = trend() + base() + departure_and_run() + pullback_into_zone() + leave() + visit() + leave() + visit() + confirmation()
    z = zones.compute(frame(third), **ZP)
    last = len(third) - 1
    assert z.dz_tests.iloc[last] == 3                                                        # the 3rd visit: a weak, used-up zone
    got = [i for i, _ in signals(SPEC, third)]
    assert last not in got and len(got) == 2                                                 # the confirmations after visits 1 and 2 traded; the one after visit 3 did not
    relaxed = edit(SPEC, "zn.dz_tests <= 2", "zn.dz_tests <= 3")                           # ... it is the strength rule that blocks it, nothing else
    assert [i for i, _ in signals(relaxed, third)] == got + [last]
    second = trend() + base() + departure_and_run() + pullback_into_zone() + leave() + visit() + confirmation()
    assert [i for i, _ in signals(SPEC, second)][-1] == len(second) - 1                      # the SECOND visit is still fine


def test_a_zone_that_did_not_leave_explosively_is_not_traded():
    gentle = [cd(B + .02 + .04 * k, B + .06 + .04 * k, .02) for k in range(8)]                 # drifts away in small steps: never a strong departure
    cs = trend() + base() + gentle + [cd(B + .34, B + .24), cd(B + .24, B + .15), cd(B + .15, B + .10)] + confirmation()
    z = zones.compute(frame(cs), **ZP)
    assert z.dz_hi.isna().all()                                                                # no demand zone is ever created from a drift
    assert signals(SPEC, cs) == []


def test_room_to_the_next_zone():
    """Rule: do not buy a demand zone that sits right under a supply zone. The same chart passes or fails depending only on the room rule."""
    cs = demand_setup()
    z = zones.compute(frame(cs), **ZP)
    room = z.room_up.iloc[-1]
    assert room == zones.NONE_NEAR                                                                       # open sky above: nothing to run into
    # put a supply zone just above the demand zone: a tight base then a drop, before the pullback
    b = B
    supply = [cd(b + 1.5, b + 1.52), cd(b + 1.52, b + 1.5), cd(b + 1.5, b + 1.53), cd(b + 1.53, b + 1.5), cd(b + 1.5, b + 1.52), cd(b + 1.52, b + 1.5), cd(b + 1.5, b + 1.51),
              cd(b + 1.51, b + 1.5), cd(b + 1.5, b + 0.55, .03)]
    cs2 = trend() + base() + departure_and_run() + supply + [cd(b + .55, b + .3), cd(b + .3, b + .10)] + confirmation()
    z2 = zones.compute(frame(cs2), **ZP)
    r = float(z2.room_up.iloc[-1])
    assert 0 < r < 99, r                                                                                 # a real supply zone now sits above price
    assert signals(edit(SPEC, "zn.room_up >= 1.5", f"zn.room_up >= {r - 0.4:.3f}"), cs2) == [(len(cs2) - 1, "CALL")]          # enough room: trade
    assert signals(edit(SPEC, "zn.room_up >= 1.5", f"zn.room_up >= {r + 0.4:.3f}"), cs2) == []                              # too close to supply: pass


# ------------------------------------------------------------------ direction and structure
def test_only_with_the_trend():
    """The same demand-zone shape forming inside a DOWN-trend: the pattern alone would buy it, the trend rule refuses it."""
    down = [(K - o, K - l, K - h, K - c) for o, h, l, c in trend()]
    b = down[-1][3]
    cs = down + base(b) + departure_and_run(b) + pullback_into_zone(b) + confirmation(b)
    assert [d for _, d in signals(without(SPEC, "trend_alignment"), cs)] == ["CALL"]
    assert signals(SPEC, cs) == []
    up = trend()
    b = up[-1][3]
    sup = mirror(base(b) + departure_and_run(b) + pullback_into_zone(b) + confirmation(b))
    shift = b - sup[0][0]
    cs2 = up + [(o + shift, h + shift, l + shift, c + shift) for o, h, l, c in sup]
    assert [d for _, d in signals(without(SPEC, "trend_alignment"), cs2)] == ["PUT"] and signals(SPEC, cs2) == []


def test_a_broken_demand_zone_becomes_supply_and_gives_a_put_on_the_retest():
    """Your top-of-chart picture: demand fails, structure turns down, and the same band now acts as resistance."""
    b = B
    drop = [cd(b + .1, b - .8, .03)]                                                       # closes well below the zone: demand is broken
    slide = [cd(b - .8, b - 1.0), cd(b - 1.0, b - .9), cd(b - .9, b - 1.1), cd(b - 1.1, b - 1.0)]
    retest = [cd(b - 1.0, b - .55), cd(b - .55, b - .15), cd(b - .15, b + .02)]            # rallies back up into the old band, from below
    conf = [cd(b + .01, b - .45, .02)]                                                     # strong red candle rejecting the band: closes below the zone and the previous low
    cs = trend() + base() + departure_and_run() + pullback_into_zone() + drop + slide + retest + conf
    z = zones.compute(frame(cs), **ZP)
    assert z.sz_flip.iloc[len(cs) - 1] == 1 and z.sz_tests.iloc[len(cs) - 1] == 1          # the old demand band, now supply, on its first visit
    s = without(SPEC, "trend_alignment")                                                   # (a trend this young has not turned the EMA stack yet: judged without it here)
    assert signals(s, cs) == [(len(cs) - 1, "PUT")]


def test_backtest_and_live_give_the_same_answer_candle_by_candle():
    cs = demand_setup() + mirror(demand_setup())                                  # a buy setup, then (after a price jump) its mirror
    df = frame(cs)
    want = vec.evaluate_series(SPEC, df, 90.0)["dir"].tolist()
    got = [{"CALL": 1, "PUT": -1}.get(d, 0) for d in dirs(SPEC, cs)]
    assert want == got and sum(abs(x) for x in got) >= 1
    nf = without(SPEC, "trend_alignment", "one_per_run")
    assert vec.evaluate_series(nf, df, 90.0)["dir"].tolist() == [{"CALL": 1, "PUT": -1}.get(d, 0) for d in dirs(nf, cs)]


def test_zone_lifecycle_for_the_chart_names_the_candles_that_made_the_zone():
    cs = demand_setup()
    log = []
    with_log = zones.compute(frame(cs), log=log, **ZP)
    plain = zones.compute(frame(cs), **ZP)
    for f in zones.FIELDS:                                                           # recording changes nothing about the result
        assert np.array_equal(getattr(with_log, f).to_numpy(), getattr(plain, f).to_numpy(), equal_nan=True)
    dz = [r for r in zones.lifecycle(frame(cs), **ZP) if r["kind"] == "demand" and not r["flip"]]
    assert len(dz) == 1
    r = dz[0]
    start = len(trend())                                                            # the base is the 8 candles right after the up-trend
    assert r["base"] == [start, start + 7] and r["start"] == start
    assert r["dep"][0] == start + 8 and r["dep"][1] >= r["dep"][0] and r["end"] is None and not r["broken"]
    assert r["tests"] == 1 and len(r["touches"]) == 1 and r["touches"][0] > r["dep"][1]            # one visit: the pullback into the band
    assert r["lo"] < r["hi"] and r["move"] >= 1.0
    broken = trend() + base() + departure_and_run() + pullback_into_zone(depth=-0.55) + [cd(B - 0.50, B + 0.10, .02)]
    kinds = [(x["kind"], x["flip"], x["broken"]) for x in zones.lifecycle(frame(broken), **ZP)]
    assert ("demand", False, True) in kinds and ("supply", True, False) in kinds       # the broken demand ends, and the same band lives on as supply


def test_zone_strength_fields_measure_the_candles_not_volume():
    cs = demand_setup()
    z = zones.compute(frame(cs), **ZP)
    last = len(cs) - 1
    a = float(z.dz_hi.iloc[last] - z.dz_lo.iloc[last]) / float(z.dz_tight.iloc[last])                 # tight = base range / ATR  ->  the ATR it used
    assert 0.2 < float(z.dz_tight.iloc[last]) < 2.5 and a > 0                                         # a compressed base
    assert float(z.dz_dep.iloc[last]) > 0.8                                                           # the departure candle was full-bodied
    assert float(z.dz_run.iloc[last]) > 4.0                                                           # price ran ~1.5 up from a ~0.05 base: many ATRs, measured BEFORE the confirmation candle
    weak = trend() + base() + [cd(B + .02 + .04 * k, B + .06 + .04 * k, .02) for k in range(8)]
    assert zones.compute(frame(weak), **ZP).dz_run.isna().all()                                       # no zone from a drift: nothing to measure
    short = trend() + base() + departure_and_run()[:1] + pullback_into_zone() + confirmation()         # a zone whose run stopped after its first candle: a smaller run
    zs = zones.compute(frame(short), **ZP)
    assert float(zs.dz_run.dropna().iloc[-1]) < float(z.dz_run.iloc[last])


def test_payout_and_run_filters():
    cs = demand_setup()
    assert evaluator.evaluate(SPEC, rows_of(cs), {}, {"payout": 70.0}).direction == "SKIP"            # below 80 %
    assert evaluator.evaluate(SPEC, rows_of(cs), {}, {"payout": 90.0}).direction == "CALL"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
