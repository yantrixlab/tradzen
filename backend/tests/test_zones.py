"""Supply/demand zone tracker on hand-drawn charts like the ones traders mark up by hand.
Run from backend/:  .venv\\Scripts\\python tests\\test_zones.py"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import indreg, zones  # noqa: E402
from app.spec import validate_spec  # noqa: E402

P = dict(base_len=6, base_atr=2.5, move_atr=1.0, brk_atr=0.3, memory=3, max_age=400, max_zones=10, atr_n=14)


def candle(o, c, wick=0.04):
    return (o, max(o, c) + wick, min(o, c) - wick, c)


def df_of(cs):
    return pd.DataFrame([{"t": 1_700_000_000 + 60 * i, "open": o, "high": h, "low": l, "close": c, "n": 10} for i, (o, h, l, c) in enumerate(cs)])


def noise(n, level, amp=0.12):
    """a sideways market: candles bouncing +-amp around `level`, ATR about 0.2"""
    out = []
    for i in range(n):
        o = level + (amp if i % 2 else -amp) * 0.5
        c = level + (-amp if i % 2 else amp) * 0.5
        out.append(candle(o, c, 0.04))
    return out


def rally(start, steps, size=0.45):
    out, p = [], start
    for _ in range(steps):
        out.append(candle(p, p + size, 0.03))
        p += size
    return out


def dem_chart():
    """lead-in, a tight BASE around 10.0 (the zone), a strong departure up, a run-up, then a pullback back into the zone"""
    cs = noise(30, 10.6, 0.4)                                 # lively lead-in so ATR is ~0.4
    cs += [candle(10.00, 10.03, 0.05), candle(10.03, 10.00, 0.05), candle(10.00, 10.04, 0.05), candle(10.04, 10.01, 0.05),
           candle(10.01, 10.05, 0.05), candle(10.05, 10.02, 0.05)]            # base: 9.95 - 10.10
    cs += [candle(10.02, 10.75, 0.04)]                         # strong departure candle: closes far above the base
    cs += rally(10.75, 4, 0.35)
    return cs


def test_a_demand_zone_is_born_from_a_base_and_a_strong_departure():
    cs = dem_chart()
    z = zones.compute(df_of(cs), **P)
    base_end = 35                                              # index of the last base candle (30 lead-in + 6 base - 1)
    born = base_end + 1
    # nothing yet before the departure; the zone is not "touched" until price comes back
    assert z.dz_hi.iloc[:born + 5].isna().all()
    hi_w = max(c[1] for c in cs[30:36]); lo_w = min(c[2] for c in cs[30:36])
    # price now comes back into the zone
    pull = [candle(11.5, 11.1, 0.03), candle(11.1, 10.6, 0.03), candle(10.6, 10.2, 0.03), candle(10.2, 10.08, 0.03)]
    z2 = zones.compute(df_of(cs + pull), **P)
    last = len(cs) + len(pull) - 1
    assert z2.dz_lo.iloc[last] == lo_w and z2.dz_hi.iloc[last] == hi_w                      # the zone is exactly the base's band
    assert z2.dz_tests.iloc[last] == 1 and z2.dz_since.iloc[last] == 0 and z2.dz_flip.iloc[last] == 0
    assert z2.dz_move.iloc[last] > 1.0 and z2.dz_age.iloc[last] == last - born              # a strong departure, and the zone's age in candles


def test_visits_are_counted_and_a_zone_weakens():
    cs = dem_chart() + [candle(11.5, 11.1), candle(11.1, 10.6), candle(10.6, 10.2), candle(10.2, 10.08)]             # visit 1
    cs += [candle(10.08, 10.5), candle(10.5, 10.9), candle(10.9, 11.3), candle(11.3, 11.6), candle(11.6, 11.7)]       # leaves upward
    cs += [candle(11.7, 11.2), candle(11.2, 10.6), candle(10.6, 10.2), candle(10.2, 10.07)]                         # visit 2
    z = zones.compute(df_of(cs), **P)
    assert z.dz_tests.iloc[len(cs) - 1] == 2
    # staying inside the zone for several candles is still ONE visit
    cs2 = cs + [candle(10.07, 10.04), candle(10.04, 10.08), candle(10.08, 10.05)]
    assert zones.compute(df_of(cs2), **P).dz_tests.iloc[len(cs2) - 1] == 2


def test_a_broken_demand_zone_flips_into_supply():
    cs = dem_chart() + [candle(11.5, 11.1), candle(11.1, 10.6), candle(10.6, 10.2), candle(10.2, 10.08)]
    cs += [candle(10.08, 9.55, 0.03)]                          # a close well BELOW the zone (9.95): demand is broken
    z = zones.compute(df_of(cs), **P)
    b = len(cs) - 1
    assert z.room_dn.iloc[b] == zones.NONE_NEAR and 0 < z.room_up.iloc[b] < 99                 # no demand left below; the same band now sits ABOVE price as supply
    # price rallies back UP into the old demand band from below: it now acts as SUPPLY (flip = 1), and it is on its first visit
    cs += [candle(9.55, 9.75), candle(9.75, 9.95), candle(9.95, 10.05)]
    z = zones.compute(df_of(cs), **P)
    e = len(cs) - 1
    assert z.sz_flip.iloc[e] == 1 and z.sz_tests.iloc[e] == 1 and z.sz_since.iloc[e] == 0
    assert abs(z.sz_lo.iloc[e] - min(c[2] for c in cs[30:36])) < 1e-9
    assert z.dz_hi.iloc[e] != z.dz_hi.iloc[e]                                                  # no demand zone left here


def test_room_to_the_next_zone():
    cs = dem_chart()
    z = zones.compute(df_of(cs), **P)
    last = len(cs) - 1                                          # price is far above the demand zone, no supply anywhere
    assert z.room_up.iloc[last] == zones.NONE_NEAR and 0 < z.room_dn.iloc[last] < 20
    cs2 = cs + [candle(11.5, 11.1), candle(11.1, 10.6), candle(10.6, 10.2), candle(10.2, 10.08)]
    assert zones.compute(df_of(cs2), **P).room_dn.iloc[len(cs2) - 1] == 0.0                     # inside the zone: no room below at all
    # a supply zone formed above price: room_up becomes a real distance
    sup = noise(8, 13.0, 0.05) + [candle(13.0, 12.2, 0.04)]
    sup_cs = dem_chart() + sup
    price_below = sup_cs + [candle(12.2, 11.4), candle(11.4, 11.0)]
    r = zones.compute(df_of(price_below), **P).room_up.iloc[len(price_below) - 1]
    assert 0 < r < 99                                                                                                 # supply sits above price, a measurable distance away


def busy_chart(seed=3, n=1400):
    """a market that keeps forming bases and leaving them in either direction, so many zones overlap, flip and expire"""
    rnd = np.random.default_rng(seed)
    p, cs = 100.0, []
    while len(cs) < n:
        for _ in range(int(rnd.integers(6, 12))):                          # a base: quiet, tight candles
            o = p + rnd.normal(0, 0.03)
            c = o + rnd.normal(0, 0.03)
            cs.append(candle(o, c, 0.02)); p = c
        leg = rnd.choice([-1.0, 1.0]) * rnd.uniform(0.5, 1.5)              # a departure and a run
        for _ in range(int(rnd.integers(2, 6))):
            c = p + leg / 3 + rnd.normal(0, 0.05)
            cs.append(candle(p, c, 0.03)); p = c
        for _ in range(int(rnd.integers(0, 5))):                           # sometimes a pullback toward the base
            c = p - leg / 5 + rnd.normal(0, 0.05)
            cs.append(candle(p, c, 0.03)); p = c
    return cs[:n]


def test_a_window_of_memory_plus_margin_gives_exactly_the_full_history_answer():
    """Regression: live trading only sees recent candles, a backtest sees everything. They must agree, so a zone's life may depend only on
    the candles since its own birth (no suppression of overlapping zones, no eviction)."""
    from app import evaluator
    cs = busy_chart()
    df = df_of(cs)
    full = zones.compute(df, **P)
    need = evaluator.lookback({"indicators": [{"type": "zones", **P}]})
    assert need == P["max_age"] + P["base_len"] + 230
    n_zone_candles = int(full.dz_hi.notna().sum() + full.sz_hi.notna().sum())
    assert n_zone_candles > 100                                            # the chart really is full of zones
    for end in range(need + 50, len(cs), 23):
        win = zones.compute(df.iloc[end - need + 1:end + 1].reset_index(drop=True), **P)
        for f in zones.FIELDS:
            a, b = full.__dict__[f].iloc[end], win.__dict__[f].iloc[-1]
            ok = (np.isnan(a) and np.isnan(b)) or abs(a - b) < 1e-9
            assert ok, (f, end, a, b)


def test_many_zones_are_handled_and_stay_finite():
    cs = noise(40, 10.0, 0.5)
    for _ in range(25):                                         # many bases and departures
        cs += [candle(10.0, 10.02), candle(10.02, 10.0), candle(10.0, 10.03), candle(10.03, 10.0), candle(10.0, 10.02), candle(10.02, 10.01)] + [candle(10.01, 10.9, 0.03)] + [candle(10.9, 10.0, 0.03)]
    z = zones.compute(df_of(cs), **{**P, "max_zones": 4})
    assert np.isfinite(z.room_dn.to_numpy()).all()              # never crashes, never produces NaN distances


def test_no_look_ahead():
    cs = dem_chart() + [candle(11.5, 11.1), candle(11.1, 10.6), candle(10.6, 10.2), candle(10.2, 10.08), candle(10.08, 10.4)]
    full = zones.compute(df_of(cs), **P)
    for cut in (38, 42, 48, len(cs) - 2):
        part = zones.compute(df_of(cs[:cut]), **P)
        for f in zones.FIELDS:
            a, b = full.__dict__[f].iloc[:cut].to_numpy(), part.__dict__[f].to_numpy()
            assert np.array_equal(a, b, equal_nan=True), (f, cut)                       # what the tracker said at candle i never changes when more candles arrive


def test_registered_as_an_indicator_and_validated_like_the_others():
    assert indreg.fields("zones") == zones.FIELDS and indreg.defaults("zones")["base_len"] == 8
    s = {"engine": "spec/1", "kind": "factors", "timeframes": [60], "indicators": [{"id": "zn", "type": "zones", "base_len": 10, "move_atr": 1.5}],
         "factors": [{"id": "f", "name": "f", "weight": 100, "kind": "expr", "call": "zn.dz_since <= 2 and zn.dz_tests <= 2 and green", "put": None}],
         "gates": {"min_candles": 60, "min_agree": 1}, "filters": [], "expiry_candles": 1}
    v = validate_spec(s)
    assert v["ok"], v["errors"]
    assert v["spec"]["indicators"][0]["base_atr"] == 2.5 and v["spec"]["indicators"][0]["base_len"] == 10
    bad = dict(s, indicators=[{"id": "zn", "type": "zones", "nope": 1}])
    assert not validate_spec(bad)["ok"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
