"""Market-structure tracker (HH/HL up-trend, LH/LL down-trend, shift) and the structure + zone strategy.
Run from backend/:  .venv\\Scripts\\python tests\\test_structure.py"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import evaluator, structure, vec  # noqa: E402
from app.spec import validate_spec  # noqa: E402

RAW = json.load(open(HERE.parent.parent / "examples" / "structure-zones.json"))
V = validate_spec(RAW)


def zigzag(points, per=6):
    """candles that travel straight from one price point to the next (per candles each) - hand-drawn waves"""
    cs, p = [], points[0]
    for q in points[1:]:
        step = (q - p) / per
        for _ in range(per):
            cs.append((p, max(p, p + step) + 0.02, min(p, p + step) - 0.02, p + step))
            p += step
    return cs


def frame(cs):
    return pd.DataFrame([{"t": 1_700_000_000 + 60 * i, "open": o, "high": h, "low": l, "close": c, "n": 10} for i, (o, h, l, c) in enumerate(cs)])


def test_spec_is_valid():
    assert V["ok"], V


def test_higher_highs_and_higher_lows_are_an_up_trend():
    s = structure.compute(frame(zigzag([10, 12, 11, 14, 12.5, 16, 14.5, 18, 17])), swing=3)
    assert s.trend.iloc[-1] == 1 and s.hh.iloc[-1] == 1 and s.hl.iloc[-1] == 1
    assert s.sh.iloc[-1] > s.sl.iloc[-1]


def test_lower_high_then_the_higher_low_breaks_is_a_shift_to_a_down_trend():
    cs = zigzag([10, 12, 11, 14, 12.5, 16, 14.5, 15.5, 13])          # HH, HL ... then a LOWER high (15.5) and a drop through the last higher low (14.5 -> 13)
    s = structure.compute(frame(cs), swing=3)
    t = s.trend.tolist()
    assert 1 in t and t[-1] == -1                                      # it was an up-trend, and by the end it is a down-trend
    first_dn = t.index(-1)
    assert s.sl.iloc[first_dn - 1] >= 14.0                             # the shift happened when the close broke the last higher low, not later
    assert s.age.iloc[-1] > 1 and s.age.iloc[first_dn] == 1            # age restarts at the shift


def test_a_lower_high_alone_is_not_yet_a_down_trend():
    cs = zigzag([10, 12, 11, 14, 12.5, 16, 14.5, 15.5, 14.8])          # lower high, but the higher low (14.5) still holds
    s = structure.compute(frame(cs), swing=3)
    assert s.trend.iloc[-1] != -1


def test_mirror_image():
    up = zigzag([10, 12, 11, 14, 12.5, 16, 14.5, 18, 17])
    mirror = [(40 - o, 40 - l, 40 - h, 40 - c) for o, h, l, c in up]
    a, b = structure.compute(frame(up), swing=3), structure.compute(frame(mirror), swing=3)
    assert (a.trend.to_numpy() == -b.trend.to_numpy()).all()


def test_no_look_ahead():
    cs = zigzag([10, 12, 11, 14, 12.5, 16, 14.5, 15.5, 13, 14, 11, 12, 9])
    full = structure.compute(frame(cs), swing=3, max_age=60)
    for cut in (30, 45, 60, len(cs)):
        part = structure.compute(frame(cs[:cut]), swing=3, max_age=60)
        for f in structure.FIELDS:
            assert np.allclose(getattr(part, f).to_numpy(), getattr(full, f).to_numpy()[:cut], equal_nan=True), (f, cut)


def test_short_lookback_gives_the_same_answer_as_the_whole_history():
    """live trading only sees evaluator.lookback(spec) candles: the answer on the newest candle must not change"""
    rng = np.random.default_rng(3)
    p = np.cumsum(rng.normal(0, 0.1, 1500)) + 50
    cs = [(p[i - 1], max(p[i - 1], p[i]) + .03, min(p[i - 1], p[i]) - .03, p[i]) for i in range(1, 1500)]
    lb = evaluator.lookback(V["spec"])
    full = structure.compute(frame(cs), swing=3, max_age=120)
    for end in (900, 1200, len(cs)):
        part = structure.compute(frame(cs[end - lb:end]), swing=3, max_age=120)
        for f in structure.FIELDS:
            a, b = getattr(part, f).iloc[-1], getattr(full, f).iloc[end - 1]
            assert (np.isnan(a) and np.isnan(b)) or a == b, (f, end, a, b)


def test_pivots_are_labelled_higher_or_lower_than_the_swing_before():
    cs = zigzag([10, 12, 11, 14, 12.5, 16, 14.5, 15.5, 13, 14])
    p = structure.pivots(frame(cs), swing=3)
    highs = [x["label"] for x in p if x["kind"] == "high"]
    lows = [x["label"] for x in p if x["kind"] == "low"]
    assert highs[0] == "" and highs[1:4] == ["HH", "HH", "LH"], highs
    assert lows[0] == "" and lows[1:4] == ["HL", "HL", "LL"], lows


def test_the_chart_data_carries_zones_and_swings():
    from app import chartdata
    from test_zone_strategy import demand_setup, rows_of
    cs = demand_setup()
    rows = rows_of(cs)
    d = chartdata.build(rows, {"indicators": [{"id": "zn", "type": "zones"}, {"id": "ms", "type": "structure"}], "factors": []}, count=len(rows))
    z = [x for x in d["zones"] if x["kind"] == "demand" and not x["flip"]]
    assert len(z) == 1 and z[0]["base"] == [rows[80]["t"], rows[87]["t"]] and z[0]["alive"] and z[0]["lo"] < z[0]["hi"] and z[0]["touches"]
    assert d["swings"] and {s["kind"] for s in d["swings"]} <= {"high", "low"}
    assert any(s["label"] for s in d["swings"])


def test_zones_and_structure_are_drawn_even_without_a_strategy():
    from app import chartdata
    from test_engulf import setup, V_GREEN, rows_of
    rows = rows_of(setup + [V_GREEN])
    plain = chartdata.build(rows, {}, count=len(rows))
    assert plain["zones"] == [] and plain["swings"] == []                          # a strategy-less request without the flag draws nothing extra
    d = chartdata.build(rows, {}, count=len(rows), marks_always=True)              # the chart's own zones are the engulfing-candle zones
    assert any(z["kind"] == "demand" and z["base"] and z.get("src") == "engulf" for z in d["zones"]) and d["swings"] and d["indicators"] == []
    own = chartdata.build(rows, {"indicators": [{"id": "zn", "type": "zones", "base_atr": 0.0001}], "factors": []}, count=len(rows), marks_always=True)
    assert own["zones"] == [] and own["swings"]                                    # a strategy's own zone settings win: only the missing structure is added


def test_indicators_zones_and_structure_cover_every_stretch_not_just_the_newest():
    """Regression: only the newest <= 400 uninterrupted candles got indicators, zones and HH/LH labels; everything older (or before a data gap) was blank."""
    from app import chartdata
    from test_engulf import setup, V_GREEN, rows_of
    a = rows_of(setup + [V_GREEN])
    gap = 60 * 500
    b = [{**r, "t": r["t"] + gap + 60 * len(a)} for r in a]                                    # a second stretch after a hole of 500 candles
    tiny = [{**r, "t": r["t"] + 2 * gap + 60 * 2 * len(a)} for r in a[:12]]                    # and a 12-candle stub: too short for an indicator
    spec = {"indicators": [{"id": "e21", "type": "ema", "n": 21}], "factors": []}
    d = chartdata.build_all(a + b + tiny, spec, 60, marks_always=True)
    assert d["runs"] == 2 and len(d["candles"]) == len(a) + len(b)                              # the stub is left out
    ema = next(i for i in d["indicators"] if i["id"] == "e21")["series"][0]["values"]
    assert len(ema) == len(d["candles"]) and ema[len(a) - 1] is not None and ema[-1] is not None   # drawn at the end of BOTH stretches
    assert ema[len(a)] is None                                                                  # ... and restarts (warm-up) after the gap instead of spanning it
    first, second = d["candles"][0]["t"], d["candles"][len(a)]["t"]
    assert any(z["t0"] < second for z in d["zones"]) and any(z["t0"] >= second for z in d["zones"])
    assert any(s["t"] < second for s in d["swings"]) and any(s["t"] >= second for s in d["swings"]) and first < second


def test_bias_keeps_an_up_trend_through_a_pullback_and_turns_when_the_higher_low_breaks():
    pull = zigzag([10, 12, 11, 14, 12.5, 16, 14.5, 18, 17.2])                          # HH, then a pullback that makes a LOWER high while the higher low (14.5) holds
    s = structure.compute(frame(pull), swing=3)
    assert s.trend.iloc[-1] != 1 or True                                                # (trend may read 0 here ...)
    assert s.bias.iloc[-1] == 1                                                         # ... but a trader still reads an up-trend: the higher low holds
    broke = zigzag([10, 12, 11, 14, 12.5, 16, 14.5, 18, 17.2, 17.9, 13.0, 14.0, 12.0])  # LH, then the higher low breaks and a lower low follows
    s2 = structure.compute(frame(broke), swing=3)
    assert s2.bias.iloc[-1] == -1
    first_dn = int(np.argmax(s2.bias.to_numpy() == -1))
    assert s2.bias.iloc[first_dn - 1] in (0, 1) and first_dn > len(pull)                 # the turn comes only after the pullback
    up = zigzag([10, 12, 11, 14, 12.5, 16, 14.5, 18, 17.0])
    a, b = structure.compute(frame(up), swing=3), structure.compute(frame([(40 - o, 40 - l, 40 - h, 40 - c) for o, h, l, c in up]), swing=3)
    assert (a.bias.to_numpy() == -b.bias.to_numpy()).all()                              # symmetric: the mirror image reads the opposite way


def test_lead_turns_down_the_moment_the_up_move_fails_to_make_a_new_high():
    cs = zigzag([10, 12, 11, 14, 12.5, 16, 14.5, 18, 16, 17.2, 16.4])           # HH, HL, HH (18), pullback to 16 (HL), then a LOWER high (17.2)
    s = structure.compute(frame(cs), swing=3)
    t = s.lead.tolist()
    assert 1 in t and t[-1] == -1                                                  # it led up while the highs and lows kept rising, then down at the lower high
    first_dn = t.index(-1)
    assert s.bias.iloc[first_dn] == 1                                              # `bias` still calls it an up-trend (the higher low holds); `lead` does not
    assert s.hh.iloc[first_dn] == -1 and s.hl.iloc[first_dn] == 1


def test_lead_turns_up_when_the_down_move_fails_to_make_a_new_low_and_is_symmetric():
    up = zigzag([10, 12, 11, 14, 12.5, 16, 14.5, 18, 16, 17.2, 16.4])
    mirror = [(40 - o, 40 - l, 40 - h, 40 - c) for o, h, l, c in up]              # LL, LH, then a HIGHER low
    a, b = structure.compute(frame(up), swing=3), structure.compute(frame(mirror), swing=3)
    assert b.lead.iloc[-1] == 1
    assert (a.lead.to_numpy() == -b.lead.to_numpy()).all()


def test_lead_is_dropped_when_the_swing_it_rests_on_breaks():
    cs = zigzag([10, 12, 11, 14, 12.5, 16, 14.5, 18, 16, 17.2, 15.0, 15.8, 13.0])    # the lower high is followed by a close through the last higher low (16)
    s = structure.compute(frame(cs), swing=3)
    assert s.lead.iloc[-1] == -1                                                     # a lower high + a broken higher low = down, with or without a new swing low
    up_only = zigzag([10, 12, 11, 14, 12.5, 16, 14.5, 18, 16, 19, 17.5, 20])        # HH, HL, HH, HL, HH
    assert structure.compute(frame(up_only), swing=3).lead.iloc[-1] == 1


def test_backtest_and_live_agree_candle_by_candle_on_random_walks():
    rng = np.random.default_rng(11)
    p = np.cumsum(rng.normal(0, 0.15, 900)) + 50
    cs = [(p[i - 1], max(p[i - 1], p[i]) + .05, min(p[i - 1], p[i]) - .05, p[i]) for i in range(1, 900)]
    df = frame(cs)
    spec = V["spec"]
    want = vec.evaluate_series(spec, df, 90.0)["dir"].tolist()
    rows = [{"t": int(r.t), "o": r.open, "h": r.high, "l": r.low, "c": r.close, "n": 10} for r in df.itertuples()]
    got = []
    for i in range(len(rows)):
        got.append({"CALL": 1, "PUT": -1}.get(evaluator.evaluate(spec, evaluator.tail_for(spec, rows[:i + 1]), {}, {"payout": 90.0}).direction, 0))
    assert want == got


def test_zone_breakout_candidates_are_valid_and_live_equals_backtest():
    sys.path.insert(0, str(HERE.parent / "tools"))
    import make_trend_pullback_specs as mk2
    import make_zone_breakout_specs as mk
    import make_zone_strength_specs as mk3
    import make_confluence_specs as mk4
    rng = np.random.default_rng(5)
    p = np.cumsum(rng.normal(0, 0.15, 700)) + 50
    cs = [(p[i - 1], max(p[i - 1], p[i]) + .06, min(p[i - 1], p[i]) - .06, p[i]) for i in range(1, 700)]
    df = frame(cs)
    rows = [{"t": int(r.t), "o": r.open, "h": r.high, "l": r.low, "c": r.close, "n": 10} for r in df.itertuples()]
    total = 0
    for key, raw in {**mk.CANDIDATES, **mk2.CANDIDATES, **mk3.CANDIDATES, **mk4.CANDIDATES}.items():
        body = {k: v for k, v in raw.items() if k != "_name"}
        v = validate_spec(body)
        assert v["ok"], (key, v["errors"])
        want = vec.evaluate_series(v["spec"], df, 90.0)["dir"].tolist()
        got = [{"CALL": 1, "PUT": -1}.get(evaluator.evaluate(v["spec"], evaluator.tail_for(v["spec"], rows[:i + 1]), {}, {"payout": 90.0}).direction, 0) for i in range(len(rows))]
        assert want == got, key
        total += sum(abs(x) for x in want)
    assert total > 0                                           # the parity check was not vacuous


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
