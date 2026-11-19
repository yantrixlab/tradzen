"""The chart's market read: swings down to single candles, BOS / CHoCH with confirmation, swing / swing-engulf zones, and who is in control - on hand-drawn candles.
Run from backend/:  .venv\\Scripts\\python tests\\test_marketread.py"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import chartdata, marketread as mr  # noqa: E402

W = 0.05                                                   # wick on each side of a candle


def cd(o, c, w=W):
    return (o, max(o, c) + w, min(o, c) - w, c)


def frame(cs):
    return pd.DataFrame([{"t": 1_700_000_000 + 60 * i, "open": o, "high": h, "low": l, "close": c, "n": 10} for i, (o, h, l, c) in enumerate(cs)])


def rows_of(cs):
    return [{"t": 1_700_000_000 + 60 * i, "o": o, "h": h, "l": l, "c": c, "n": 10} for i, (o, h, l, c) in enumerate(cs)]


def mirror(cs, k=100.0):
    return [(k - o, k - l, k - h, k - c) for o, h, l, c in cs]


def noise(n=60, start=50.0, seed=1):
    """a quiet random walk to warm the ATR up before the hand-drawn part"""
    rng = np.random.default_rng(seed)
    p, out = start, []
    for _ in range(n):
        q = p + rng.normal(0, 0.1)
        out.append((p, max(p, q) + 0.04, min(p, q) - 0.04, q))
        p = q
    return out, p


def labels(an, kind):
    return [s["label"] for s in an["swings"] if s["kind"] == kind]


# one-candle pull-backs inside an up-trend: every red candle is a higher low, every green push a higher high (what the chart used to leave unmarked)
def stair_up(p0=50.0):
    cs, p = [], p0
    for _ in range(5):
        cs.append(cd(p, p + 1.0))
        cs.append(cd(p + 1.0, p + 1.8))                    # push on
        cs.append(cd(p + 1.8, p + 1.3))                    # a single red pull-back candle
        p += 0.5                                           # the next leg starts higher: the low is higher
    return cs, p


def test_single_candle_pullbacks_are_marked_as_higher_lows_and_higher_highs():
    base, p = noise()
    st, _ = stair_up(p)
    an = mr.analyze(frame(base + st))
    tail_highs, tail_lows = labels(an, "high")[-4:], labels(an, "low")[-4:]
    assert tail_highs == ["HH"] * 4 and tail_lows == ["HL"] * 4, (tail_highs, tail_lows)
    assert an["structure"]["trend"][-1] == 1


def test_swings_alternate_and_a_swing_is_known_only_after_the_next_candle():
    base, p = noise()
    st, _ = stair_up(p)
    an = mr.analyze(frame(base + st))
    kinds = [s["kind"] for s in an["swings"]]
    assert all(a != b for a, b in zip(kinds, kinds[1:]))
    df = frame(base + st)
    h = df["high"].to_numpy()
    for s in an["swings"]:
        if s["kind"] == "high":
            assert h[s["i"]] >= h[s["i"] - 1] and h[s["i"]] >= h[min(len(h) - 1, s["i"] + 1)]


def test_a_close_through_the_protected_low_is_a_change_of_direction_a_wick_is_not():
    base, p = noise()
    st, q = stair_up(p)
    held = mr.analyze(frame(base + st))
    prot = held["structure"]["prot"][-1]
    assert not np.isnan(prot)
    wick = (q, q + 0.1, prot - 0.3, q + 0.05)              # a wick below the protected low that closes back above it: a sweep, not a change
    an = mr.analyze(frame(base + st + [wick, cd(q + 0.05, q + 0.4)]))
    assert an["structure"]["trend"][-1] == 1 and not [e for e in an["structure"]["events"] if e["type"] == "CHoCH" and e["dir"] == -1 and e["i"] >= len(base) + len(st)]
    drop = cd(q, prot - 0.8)                               # a candle that CLOSES below it, with a real body: a confirmed change
    an2 = mr.analyze(frame(base + st + [drop]))
    ev = [e for e in an2["structure"]["events"] if e["type"] == "CHoCH" and e["dir"] == -1]
    assert ev and ev[-1]["strong"] and ev[-1]["i"] == len(base) + len(st) and an2["structure"]["trend"][-1] == -1
    assert an2["flow"]["score"][-1] < an["flow"]["score"][-1]                           # the reading follows


def test_a_close_above_the_last_high_in_an_up_trend_is_a_continuation():
    base, p = noise()
    st, _ = stair_up(p)
    an = mr.analyze(frame(base + st))
    bos = [e for e in an["structure"]["events"] if e["type"] == "BOS" and e["dir"] == 1]
    assert bos and an["structure"]["streak"][-1] >= 2


# --- zones
def leg_then_engulf(reds, greens, p=60.0):
    """`reds` small red candles in a row, then `greens` green candles; the last one closes above the highest high of the red leg"""
    cs = [cd(p, p - 0.2)]
    for k in range(reds):
        o = p - 0.2 - 0.25 * k
        cs.append(cd(o, o - 0.25))
    low = p - 0.2 - 0.25 * reds
    top = max(c[1] for c in cs[1:])
    step = (top + 0.5 - low) / greens
    for k in range(greens):
        o = low + step * k
        cs.append(cd(o, o + step))
    return cs


def test_a_swing_of_red_candles_engulfed_by_green_candles_is_a_demand_zone_of_the_whole_swing():
    base, p = noise()
    cs = base + leg_then_engulf(3, 2, p)
    an = mr.analyze(frame(cs))
    z = [x for x in an["zones"] if x["src"] == "swing_engulf" and x["kind"] == "demand" and x["dep"][1] == len(cs) - 1]       # (the random walk before it has zones of its own)
    assert z, [x["src"] for x in an["zones"]]
    df = frame(cs)
    leg = df.iloc[z[0]["base"][0]: z[0]["base"][1] + 1]
    assert z[0]["base"][1] == len(base) + 3 and z[0]["base"][1] - z[0]["base"][0] + 1 >= 4                  # the whole red swing, however far back it began
    atr = float(an["atr"][z[0]["dep"][0]])
    assert abs(z[0]["lo"] - leg["low"].min()) < 1e-9 and z[0]["hi"] <= leg["high"].max() + 1e-9           # from the low of the swing up ...
    assert abs(z[0]["hi"] - min(leg["high"].max(), leg["low"].min() + 2.5 * atr)) < 1e-9                    # ... to its high (a very long swing is trimmed to 2.5 ATR where it turned)
    assert z[0]["end"] is None and z[0]["tag"].startswith("SWING")


def test_the_mirror_is_a_supply_zone():
    base, p = noise()
    cs = mirror(base + leg_then_engulf(3, 2, p))
    an = mr.analyze(frame(cs))
    assert [x for x in an["zones"] if x["src"] == "swing_engulf" and x["kind"] == "supply" and x["dep"][1] == len(cs) - 1]
    assert not [x for x in an["zones"] if x["src"] == "swing_engulf" and x["kind"] == "demand" and x["dep"][1] == len(cs) - 1]


def test_an_opposite_candle_inside_the_engulfing_run_means_no_swing_engulf_zone():
    base, p = noise()
    seq = leg_then_engulf(3, 3, p)
    n_red = 1 + 3
    broken = seq[:n_red + 1] + [cd(seq[n_red + 1][3], seq[n_red + 1][3] - 0.1)] + seq[n_red + 1:]       # a red candle between the green ones of the engulfing run
    an = mr.analyze(frame(base + broken))
    assert not [x for x in an["zones"] if x["src"] == "swing_engulf" and x["kind"] == "demand" and x["base"][1] == len(base) + 3]       # the red swing was not engulfed in one unbroken run


def test_a_zone_ends_when_a_candle_closes_through_it():
    base, p = noise()
    cs = base + leg_then_engulf(3, 2, p)
    an = mr.analyze(frame(cs))
    z = [x for x in an["zones"] if x["src"] == "swing_engulf" and x["kind"] == "demand" and x["dep"][1] == len(cs) - 1][0]
    down = cs + [cd(cs[-1][3], z["lo"] - 0.6)]
    an2 = mr.analyze(frame(down))
    z2 = [x for x in an2["zones"] if x["src"] == "swing_engulf" and x["kind"] == "demand" and x["lo"] == z["lo"]][0]
    assert z2["end"] == len(down) - 1


def test_zones_sit_only_on_swings_and_engulfs_not_on_every_engulfing_candle():
    """On a quiet random walk the old chart marked a zone on nearly every engulfing candle; the market read keeps only those at a swing / a strong move."""
    from app import engulf
    base, _ = noise(900, seed=7)
    df = frame(base)
    every = [r for r in engulf.lifecycle(df)]
    an = mr.analyze(df)
    assert 0 < len(an["zones"]) < len(every)
    assert {z["src"] for z in an["zones"]} <= {"swing", "swing_engulf", "engulf", "base"}
    assert all(z["tag"] for z in an["zones"])


# --- who is in control
def test_buyers_dominate_an_up_trend_and_sellers_its_mirror():
    base, p = noise()
    st, _ = stair_up(p)
    up = mr.analyze(frame(base + st))
    dn = mr.analyze(frame(mirror(base + st)))
    assert up["flow"]["score"][-1] > 20 and dn["flow"]["score"][-1] < -20
    assert abs(up["flow"]["score"][-1] + dn["flow"]["score"][-1]) < 12                       # (nearly) symmetric
    s = mr.summary(up, frame(base + st))
    assert s["side"] == 1 and s["buyers"] > 55 and abs(s["buyers"] + s["sellers"] - 100) < 0.2 and s["reasons"]


def test_the_reading_never_looks_ahead():
    """Cutting the history at any candle gives the same reading on that candle (the structure, the zones and the score use only closed candles)."""
    base, _ = noise(700, seed=3)
    df = frame(base)
    full = mr.analyze(df)
    for cut in (300, 450, 600):
        part = mr.analyze(df.iloc[:cut].reset_index(drop=True))
        assert np.allclose(part["flow"]["score"][60:cut - 4], full["flow"]["score"][60:cut - 4], atol=1e-6), cut
        assert np.array_equal(part["structure"]["trend"][60:cut - 4], full["structure"]["trend"][60:cut - 4]), cut


def test_the_chart_carries_swings_breaks_zones_and_the_buyers_vs_sellers_pane():
    base, p = noise()
    st, _ = stair_up(p)
    rows = rows_of(base + st)
    d = chartdata.build(rows, {}, count=len(rows), marks_always=True)
    assert d["swings"] and any(s["label"] == "HL" for s in d["swings"]) and all("major" in s for s in d["swings"])
    assert d["breaks"] and {b["type"] for b in d["breaks"]} <= {"BOS", "CHoCH"} and all(b["t0"] <= b["t1"] for b in d["breaks"])
    pane = [i for i in d["indicators"] if i["id"] == "flow_auto"]
    assert len(pane) == 1 and len(pane[0]["series"][0]["values"]) == len(d["candles"]) and pane[0]["range"] == [-100, 100]
    f = d["flow"]
    assert f["buyers"] + f["sellers"] == 100 or abs(f["buyers"] + f["sellers"] - 100) < 0.2
    assert {c["key"] for c in f["components"]} == set(mr.WEIGHTS) and f["accuracy"]
    plain = chartdata.build(rows, {}, count=len(rows))
    assert plain["flow"] is None and plain["breaks"] == []                                     # without the flag the chart draws nothing extra


def test_a_strategys_own_zone_indicator_keeps_its_zones_but_the_swings_are_always_the_market_reads():
    base, p = noise()
    st, _ = stair_up(p)
    rows = rows_of(base + st)
    own = chartdata.build(rows, {"indicators": [{"id": "zn", "type": "zones"}, {"id": "ms", "type": "structure"}], "factors": []}, count=len(rows), marks_always=True)
    assert all(z["id"] != "smart" for z in own["zones"]) and own["swings"] and all(s["id"] == "smart" for s in own["swings"])
    assert own["flow"] is not None                                                              # the reading is always added


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all market read tests passed")
