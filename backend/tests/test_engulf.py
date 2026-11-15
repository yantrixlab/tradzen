"""Engulfing-candle supply / demand zones on hand-drawn charts: where the zone is, when it signals, when it is used up, and live == backtest.
Run from backend/:  .venv\\Scripts\\python tests\\test_engulf.py"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "tools"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import make_engulf_zone_specs as mk  # noqa: E402
from app import chartdata, engulf, evaluator, vec  # noqa: E402
from app.spec import validate_spec  # noqa: E402

K = 40.0


def short(key, visits=None):
    """a candidate with a shorter warm-up, so the hand-drawn charts (55 candles) are long enough"""
    raw = {**mk.CANDIDATES[key], "gates": {**mk.CANDIDATES[key]["gates"], "min_candles": 30}}
    if visits:
        raw["indicators"] = [{**i, "visits": visits} if i["type"] == "engulf" else i for i in raw["indicators"]]
        raw["factors"] = [{**f, "call": f["call"].replace("dz_visit == 1", f"dz_visit <= {visits}"), "put": f["put"].replace("sz_visit == 1", f"sz_visit <= {visits}")} for f in raw["factors"]]
    return validate_spec(raw)["spec"]


SPEC = short("ez1-engulf-zone")


def cd(o, c, w=0.03):
    return (o, max(o, c) + w, min(o, c) - w, c)


def zigzag(points, per=6):
    cs, p = [], points[0]
    for q in points[1:]:
        step = (q - p) / per
        for _ in range(per):
            cs.append((p, max(p, p + step) + 0.02, min(p, p + step) - 0.02, p + step))
            p += step
    return cs


def rows_of(cs):
    return [{"t": 1_700_000_000 + 60 * i, "o": o, "h": h, "l": l, "c": c, "n": 10} for i, (o, h, l, c) in enumerate(cs)]


def frame(cs):
    return pd.DataFrame([{"t": r["t"], "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"], "n": 10} for r in rows_of(cs)])


def mirror(cs):
    return [(K - o, K - l, K - h, K - c) for o, h, l, c in cs]


def signals(spec, cs):
    r = rows_of(cs)
    out = []
    for i in range(len(r)):
        d = evaluator.evaluate(spec, evaluator.tail_for(spec, r[:i + 1]), {}, {"payout": 90.0}).direction
        if d != "SKIP":
            out.append((i, d))
    return out


UP = zigzag([10, 12, 11, 14, 12.5, 16, 14.5, 18, 17.0])           # higher highs and higher lows, ending on a small pullback
RED = cd(17.0, 16.8)                                               # the red candle that gets engulfed: range 16.77 - 17.03
GREEN = cd(16.78, 17.5)                                            # a decisive green candle: opens at the red close, closes above the red HIGH
AWAY = [cd(17.5, 17.62), cd(17.62, 17.8), cd(17.8, 17.45), cd(17.45, 17.15)]       # price leaves, then comes back toward the zone without touching it
V_GREEN = (17.10, 17.37, 16.98, 17.35)                             # the visit: dips into the zone and closes strongly green inside / above it
LEAVE = cd(17.35, 17.6)                                            # a candle clear of the zone: the visit is over
setup = UP + [RED, GREEN] + AWAY
R = len(UP)                                                        # index of the red candle; R + 1 is the engulfing green candle


def zone_of(cs, kind="demand", at=None):
    return [r for r in engulf.lifecycle(frame(cs)) if r["kind"] == kind and (at is None or r["base"] == [at, at])]


def test_the_engulfed_red_candle_is_the_demand_zone():
    z = zone_of(setup, at=R)[-1]
    assert z["base"] == [R, R] and z["dep"] == [R + 1, R + 1] and z["run"] == 1             # the engulfed candle is marked; the engulfing candle is the departure
    assert abs(z["lo"] - RED[2]) < 1e-12 and abs(z["hi"] - RED[1]) < 1e-12                  # the FULL high-low range of the engulfed candle, wicks included
    assert z["end"] is None and z["with_trend"]
    f = engulf.compute(frame(setup))
    assert f.dz_visit.isna().all() and f.room_dn.iloc[-1] < engulf.NONE_NEAR                 # nobody has visited it yet; it is the nearest demand below


def test_several_candles_can_do_the_engulfing():
    g1, g2, g3 = cd(16.8, 16.95), cd(16.95, 16.99), cd(16.99, 17.5)                          # small greens that only together close above the red high
    cs = UP + [RED, g1, g2, g3]
    z = zone_of(cs, at=R)[-1]
    assert z["dep"] == [R + 1, R + 3] and z["run"] == 3 and abs(z["hi"] - RED[1]) < 1e-12
    too_long = UP + [RED, cd(16.8, 16.85), cd(16.85, 16.9), cd(16.9, 16.95), cd(16.95, 17.5)]  # four candles: more than max_run, the engulf is not recognised
    assert not zone_of(too_long, at=R)


def test_first_visit_with_a_strong_green_candle_gives_one_call_and_uses_the_zone_up():
    cs = setup + [V_GREEN]
    assert signals(SPEC, cs) == [(len(cs) - 1, "CALL")]                                      # nothing during the pullback, the engulf or the approach
    f = engulf.compute(frame(cs))
    assert f.dz_visit.iloc[-1] == 1 and f.dz_nth.iloc[-1] == 1 and abs(f.dz_lo.iloc[-1] - RED[2]) < 1e-12
    again = cs + [LEAVE, cd(17.6, 17.3), (17.25, 17.4, 16.95, 17.35)]                       # the visit ends, price returns a second time, a green candle shows: used up
    assert signals(SPEC, again) == [(len(cs) - 1, "CALL")]
    z = zone_of(again, at=R)[0]
    assert z["why"] == "used" and z["end"] == len(cs) - 1                                     # it ended on the last candle of its visit


def test_a_pullback_candle_touches_the_zone_and_the_strong_green_candle_after_it_confirms():
    """The usual picture: a red candle comes down INTO the zone, the next candle is a strong green candle inside it -> CALL on that one."""
    touch = (17.10, 17.12, 16.90, 16.95)                                                      # red: touches the zone, does not break it
    confirm = (16.95, 17.32, 16.92, 17.30)                                                    # strong green, still inside the zone
    cs = setup + [touch, confirm]
    assert signals(SPEC, cs) == [(len(cs) - 1, "CALL")]
    assert signals(short("ez2-pullback-confirm"), cs) == [(len(cs) - 1, "CALL")]              # ... and it is the "red then green" shape ez2 asks for
    f = engulf.compute(frame(cs))
    assert [int(f.dz_nth.iloc[-2]), int(f.dz_nth.iloc[-1])] == [1, 2] and f.dz_visit.iloc[-1] == 1     # one visit of two candles


def test_a_visit_without_a_confirming_candle_uses_the_zone_up_unless_more_visits_are_allowed():
    touch = (17.10, 17.12, 16.90, 16.95)
    out = (16.96, 17.4, 17.05, 17.1)                                                          # leaves the zone upward: weak, no confirmation inside it
    cs = setup + [touch, out, cd(17.1, 17.05, 0.0), V_GREEN]                                 # (small candles that engulf nothing, then the strong green candle again)
    assert signals(SPEC, cs) == []                                                           # the zone has had its (only) visit
    assert signals(short("ez1-engulf-zone", visits=2), cs) == [(len(cs) - 1, "CALL")]       # a second visit is allowed: the strong green candle counts


def test_a_zone_that_price_closes_through_is_dead():
    cs = UP + [RED, GREEN, cd(17.5, 17.62), cd(17.62, 16.2, .05), cd(16.2, 16.9), V_GREEN]
    assert signals(SPEC, cs) == []
    assert zone_of(cs, at=R)[0]["why"] == "broken"


def test_zones_are_marked_anywhere_but_traded_only_with_the_structure():
    down = mirror(UP)                                                                        # a down-trend: a green candle engulfing a red one still marks a demand zone ...
    lvl = K - 17.0
    cs = down + [cd(lvl, lvl - 0.2), cd(lvl - 0.22, lvl + 0.5)] + [cd(lvl + 0.5, lvl + 0.62), cd(lvl + 0.62, lvl + 0.8), cd(lvl + 0.8, lvl + 0.45), cd(lvl + 0.45, lvl + 0.15), (lvl + 0.1, lvl + 0.37, lvl - 0.02, lvl + 0.35)]
    z = [r for r in zone_of(cs) if r["start"] >= len(down)]
    assert z and not z[0]["with_trend"]                                                      # ... flagged as against the structure
    assert [s for s in signals(SPEC, cs) if s[1] == "CALL"] == []                           # and the strategy does not buy it in a down-trend
    wicky = UP + [RED, (16.80, 17.30, 16.70, 17.06)]                                         # closes above the red high, but a long wick and a small body: no zone
    assert not zone_of(wicky, at=R)


def test_the_mirror_chart_is_a_supply_zone_and_gives_one_put():
    cs = mirror(setup + [V_GREEN])
    assert signals(SPEC, cs) == [(len(cs) - 1, "PUT")]
    assert zone_of(cs, "supply", at=R)


def test_a_supply_zone_from_the_top_of_an_up_move_is_ready_when_the_structure_turns():
    """Up-trend, a red candle engulfs the last green candle at the top (a supply zone), price falls through the last higher low (the structure turns down),
    then rallies back into the zone and a red candle forms there -> PUT."""
    top = zigzag([10, 12, 11, 14, 12.5, 16, 14.5, 18], per=6)
    top = top[:-1] + [cd(17.7, 18.0)]                                                         # the last candle of the run-up: green
    engulf_red = cd(18.02, 17.4)                                                              # red: opens at the green close, closes below its low -> supply zone 17.67 - 18.03
    fall = [cd(17.4, 16.6), cd(16.6, 15.8), cd(15.8, 15.0), cd(15.0, 14.2), cd(14.2, 13.6), cd(13.6, 13.9), cd(13.9, 14.3)]       # breaks the higher low (14.5): structure turns down
    rally = [cd(14.3, 15.5), cd(15.5, 16.6), cd(16.6, 17.5)]                                  # back up toward the supply zone
    visit = (17.7, 17.8, 17.2, 17.25)                                                         # a strong red candle forms inside the zone
    cs = top + [engulf_red] + fall + rally + [visit]
    z = zone_of(cs, "supply", at=len(top) - 1)
    assert z and abs(z[0]["lo"] - top[-1][2]) < 1e-9 and abs(z[0]["hi"] - top[-1][1]) < 1e-9
    got = signals(SPEC, cs)
    assert got and got[-1] == (len(cs) - 1, "PUT"), got


def test_a_short_window_gives_exactly_the_full_history_answer():
    rng = np.random.default_rng(8)
    p = np.cumsum(rng.normal(0, 0.15, 1400)) + 50
    cs = [(p[i - 1], max(p[i - 1], p[i]) + .05, min(p[i - 1], p[i]) - .05, p[i]) for i in range(1, 1400)]
    lb = evaluator.lookback(SPEC)
    full = engulf.compute(frame(cs))
    for end in (800, 1100, len(cs)):
        part = engulf.compute(frame(cs[end - lb:end]))
        for f in engulf.FIELDS:
            a, b = getattr(part, f).iloc[-1], getattr(full, f).iloc[end - 1]
            assert (np.isnan(a) and np.isnan(b)) or a == b, (f, end, a, b)


def test_backtest_and_live_agree_candle_by_candle():
    rng = np.random.default_rng(21)
    p = np.cumsum(rng.normal(0, 0.15, 900)) + 50
    cs = [(p[i - 1], max(p[i - 1], p[i]) + .06, min(p[i - 1], p[i]) - .06, p[i]) for i in range(1, 900)]
    cs += setup + [V_GREEN]
    df, rows = frame(cs), rows_of(cs)
    total = 0
    for key, raw in mk.CANDIDATES.items():
        v = validate_spec(raw)
        assert v["ok"], (key, v["errors"])
        want = vec.evaluate_series(v["spec"], df, 90.0)["dir"].tolist()
        got = [{"CALL": 1, "PUT": -1}.get(evaluator.evaluate(v["spec"], evaluator.tail_for(v["spec"], rows[:i + 1]), {}, {"payout": 90.0}).direction, 0) for i in range(len(rows))]
        assert want == got, key
        total += sum(abs(x) for x in want)
    assert total > 0


def test_the_chart_draws_the_engulf_zone_with_its_engulfed_candle():
    cs = setup + [V_GREEN, LEAVE]
    rows = rows_of(cs)
    d = chartdata.build(rows, {}, count=len(rows), marks_always=True)                         # no strategy: the chart's own zones are the engulfing-candle zones
    z = [x for x in d["zones"] if x["kind"] == "demand" and x.get("src") == "engulf" and x["base"] == [rows[R]["t"]] * 2]
    assert z and z[0]["why"] == "used" and z[0]["dep"] == [rows[R + 1]["t"]] * 2 and z[0]["touches"] == [rows[len(setup)]["t"]] and z[0]["t1"] == rows[len(setup)]["t"]
    own = chartdata.build(rows, {"indicators": [{"id": "zn", "type": "zones"}], "factors": []}, count=len(rows), marks_always=True)
    assert not [x for x in own["zones"] if x.get("src") == "engulf"]                         # a strategy with its own zone indicator keeps its own


def test_a_long_history_draws_every_zone_not_only_the_newest_few():
    """Regression: the chart kept only the newest 40 zones per stretch, so everything older than ~500 candles showed no zones at all."""
    rng = np.random.default_rng(4)
    p = np.cumsum(rng.normal(0, 0.15, 3000)) + 50
    cs = [(p[i - 1], max(p[i - 1], p[i]) + .06, min(p[i - 1], p[i]) - .06, p[i]) for i in range(1, 3000)]
    rows = rows_of(cs)
    d = chartdata.build_all(rows, {}, 60, marks_always=True)
    z = [x for x in d["zones"] if x.get("src") == "engulf"]
    assert len(z) > 100                                                                       # many zones in 3000 candles ...
    early = [x for x in z if x["t0"] < rows[1000]["t"]]
    assert len(early) > 20, len(early)                                                         # ... including plenty in the oldest third
    assert len(z) == len([r for r in engulf.lifecycle(frame(cs)) if r["end"] is None or r["end"] > 0])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
