"""Supply/demand strategy (examples/supply-demand-zone.json) replayed on hand-drawn charts through the real validator + evaluator.
Run from backend/:  .venv\\Scripts\\python tests\\test_supply_demand.py"""
import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app import evaluator, vec  # noqa: E402
from app.spec import validate_spec  # noqa: E402
import pandas as pd  # noqa: E402

RAW = json.load(open(HERE.parent.parent / "examples" / "supply-demand-zone.json"))
SPEC = validate_spec(RAW)["spec"]
K = 20.0                                               # mirror constant: price p becomes K - p (an up-market becomes a down-market)


def trend_prefix(n=75, start=1.0, step=0.1):
    """a steady, clean up-trend (EMA 9 > 21 > 50 by the end)"""
    out, p = [], start
    for _ in range(n):
        out.append((p, p + step + 0.02, p - 0.02, p + step))
        p += step
    return out


def mirror(candles):
    return [(K - o, K - l, K - h, K - c) for o, h, l, c in candles]


def zone_demand(top, pull_low=7.02, pull_high=7.22, conf=(7.04, 7.26, 7.03, 7.25), base=(7.00, 7.06, 6.96, 7.01)):
    """base (zone 6.96-7.06) -> strong rally to 7.40 -> two-candle pullback into the zone -> confirmation candle"""
    return [base,
            (7.01, 7.42, 7.00, 7.40),                    # long green: closes above the zone
            (7.40, 7.41, 7.19, 7.20),                    # pullback 1 (red), lower high than the rally's high
            (7.20, pull_high, pull_low, 7.05),           # pullback 2: low reaches back into the zone
            conf]


def rows_of(candles):
    return [{"t": 1_700_000_000 + 60 * i, "o": o, "h": h, "l": l, "c": c, "n": 10} for i, (o, h, l, c) in enumerate(candles)]


def directions(spec, candles):
    rows = rows_of(candles)
    return [evaluator.evaluate(spec, rows[:i + 1], {}, {"payout": 90.0}).direction for i in range(len(rows))]


def last(spec, candles):
    return directions(spec, candles)[-1]


def no_filters(spec):
    s = copy.deepcopy(spec)
    s["filters"] = []
    return s


def test_demand_zone_gives_buy_only_on_the_confirmation_candle():
    d = directions(SPEC, trend_prefix() + zone_demand(0))
    assert d[-1] == "CALL" and all(x == "SKIP" for x in d[:-1]), [i for i, x in enumerate(d) if x != "SKIP"]


def test_supply_zone_is_the_mirror_and_gives_sell():
    d = directions(SPEC, mirror(trend_prefix() + zone_demand(0)))
    assert d[-1] == "PUT" and all(x == "SKIP" for x in d[:-1])


def test_no_signal_when_a_rule_is_missing():
    base = trend_prefix()
    assert last(SPEC, base + zone_demand(0)) == "CALL"                                                        # control: the complete setup fires
    assert last(SPEC, base + zone_demand(0, pull_low=7.10)) == "SKIP"                                         # the pullback never reaches the zone
    assert last(SPEC, base + zone_demand(0, pull_low=6.90)) == "SKIP"                                         # price broke THROUGH the zone: the zone failed
    assert last(SPEC, base + zone_demand(0, conf=(7.04, 7.30, 7.00, 7.10))) == "SKIP"                         # no confirmation: closes inside the pullback, long wicks
    assert last(SPEC, base + zone_demand(0, conf=(7.10, 7.26, 7.03, 7.15))) == "SKIP"                         # closes up but does not clear the pullback's high
    assert last(SPEC, base + zone_demand(0, conf=(7.25, 7.26, 7.03, 7.04))) == "SKIP"                         # a red candle is not a buy confirmation
    # the strong move away from the zone must really be strong: a feeble rally is not a demand zone
    weak = zone_demand(0)
    weak[1] = (7.01, 7.09, 7.00, 7.08)
    assert last(SPEC, base + weak) == "SKIP"


def test_trend_filter_only_trades_with_the_market():
    down_market = mirror(trend_prefix())                                   # a clean DOWN-trend, then a demand-shaped setup appears
    setup = zone_demand(0)
    base_level = down_market[-1][3]                                        # last close of the down-trend
    shift = base_level - 7.0
    in_down = [(o + shift, h + shift, l + shift, c + shift) for o, h, l, c in setup]
    assert last(no_filters(SPEC), down_market + in_down) == "CALL"         # the pattern alone would buy a demand zone inside a down-trend ...
    assert last(SPEC, down_market + in_down) == "SKIP"                     # ... the trend filter refuses it: uptrades only in an uptrend
    # and a supply-shaped setup inside an up-trend is refused as well
    up_market = trend_prefix()
    sup = mirror(zone_demand(0))
    shift = up_market[-1][3] - sup[0][3]
    in_up = [(o + shift, h + shift, l + shift, c + shift) for o, h, l, c in sup]
    assert last(no_filters(SPEC), up_market + in_up) == "PUT" and last(SPEC, up_market + in_up) == "SKIP"


def test_payout_filter():
    rows = rows_of(trend_prefix() + zone_demand(0))
    assert evaluator.evaluate(SPEC, rows, {}, {"payout": 90.0}).direction == "CALL"
    assert evaluator.evaluate(SPEC, rows, {}, {"payout": 70.0}).direction == "SKIP"                        # below 80 %: not worth trading


def test_backtest_and_live_agree():
    candles = trend_prefix() + zone_demand(0) + trend_prefix(40, 7.3, 0.05) + mirror(zone_demand(0))
    rows = rows_of(candles)
    df = pd.DataFrame([{"t": r["t"], "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"], "n": r["n"]} for r in rows])
    want = vec.evaluate_series(SPEC, df, 90.0)["dir"].tolist()
    got = [{"CALL": 1, "PUT": -1}.get(x, 0) for x in directions(SPEC, candles)]
    assert want == got and sum(abs(x) for x in got) >= 1


VARIANTS = {p.stem: validate_spec(json.load(open(p)))["spec"] for p in sorted((HERE.parent.parent / "examples" / "supply-demand").glob("*.json"))}


def test_every_looser_variant_is_valid_and_still_fires_on_the_clean_setup():
    assert sorted(VARIANTS) == ["sd1-loose-confirmation", "sd2-any-base", "sd3-strong-move", "sd4-all-loose", "sd5-all-loose-no-counter-trend"]
    for name, spec in VARIANTS.items():
        assert spec is not None, name
        assert last(spec, trend_prefix() + zone_demand(0)) == "CALL", name
        assert last(spec, mirror(trend_prefix() + zone_demand(0))) == "PUT", name


def test_looser_variants_keep_the_core_rules():
    """Loosening is allowed to add signals, never to drop the zone, the return into it, the confirmation direction, or the trend."""
    base = trend_prefix()
    broken = zone_demand(0, pull_low=6.90)                                         # price smashed through the zone
    untouched = zone_demand(0, pull_low=7.10)                                      # price never came back to the zone
    red_confirm = zone_demand(0, conf=(7.25, 7.26, 7.03, 7.04))                    # confirmation candle is the wrong colour
    no_clear = zone_demand(0, conf=(7.10, 7.26, 7.03, 7.15))                       # closes up but not beyond the pullback
    for name, spec in VARIANTS.items():
        for label, setup in (("broken zone", broken), ("never returned", untouched), ("wrong colour", red_confirm), ("no break of pullback", no_clear)):
            assert last(spec, base + setup) == "SKIP", (name, label)
        assert last(spec, mirror(base + broken)) == "SKIP", (name, "supply broken")
    # counter-trend setups are refused by every variant, including the one that allows sideways markets
    down_market = mirror(trend_prefix())
    shift = down_market[-1][3] - 7.0
    in_down = [(o + shift, h + shift, l + shift, c + shift) for o, h, l, c in zone_demand(0)]
    for name, spec in VARIANTS.items():
        assert last(spec, down_market + in_down) == "SKIP", name


def test_looser_confirmation_accepts_a_candle_the_strict_version_rejects():
    base = trend_prefix()
    plain_confirm = (7.04, 7.36, 7.02, 7.24)            # green, closes above the pullback high (7.22); body 0.20 / range 0.34 = 59% -> not 'strong'
    assert last(SPEC, base + zone_demand(0, conf=plain_confirm)) == "SKIP"
    assert last(VARIANTS["sd1-loose-confirmation"], base + zone_demand(0, conf=plain_confirm)) == "CALL"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
