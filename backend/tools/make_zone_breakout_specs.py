"""Writes the supply/demand + market-structure strategies that trade BOTH zone reactions and zone breakouts, as spec/1 JSON files in examples/.

  .venv\\Scripts\\python tools\\make_zone_breakout_specs.py [out_dir]

Every file is one hypothesis (kept few on purpose: each extra variant that is tried makes a lucky result more likely, and the sweep tool charges for it).
"""
import json
import sys
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parent.parent.parent / "examples"

IND = [
    {"id": "ms", "type": "structure", "swing": 3, "max_age": 120},
    {"id": "a14", "type": "atr", "n": 14},
    {"id": "zn", "type": "zones", "atr_n": 14, "base_atr": 2.5, "base_len": 8, "brk_atr": 0.3, "max_age": 250, "max_zones": 10, "memory": 3, "move_atr": 1},
]

# --- the three set-ups, each as (call, put) -------------------------------------------------------------------------------------------------------
# 1) REACTION: a strong, fresh zone is visited and a confirmation candle rejects it (the original idea). `trend` is added per variant.
REACT = (
    "zn.dz_since <= 2 and zn.dz_tests <= 2 and zn.dz_move >= 1.2 and zn.room_up >= 1.5 and green and body > 0.5 * range and close > high[1] and close > zn.dz_hi and low >= zn.dz_lo",
    "zn.sz_since <= 2 and zn.sz_tests <= 2 and zn.sz_move >= 1.2 and zn.room_dn >= 1.5 and red and body < -0.5 * range and close < low[1] and close < zn.sz_lo and high <= zn.sz_hi",
)
# 2) BREAKOUT: the candle before was at / inside a zone that price had visited; this candle closes clear through it (0.3 ATR) with a full-bodied candle
#    that closes near its extreme (confirmation) - momentum continuation, no hindsight: only the previous candle's zone and this candle's close are used.
BREAK = (
    "zn.sz_since[1] <= 1 and zn.sz_tests[1] >= 1 and close[1] <= zn.sz_hi[1] and close > zn.sz_hi[1] + 0.3 * a14 and green and body > 0.6 * range and upper_wick < 0.2 * range",
    "zn.dz_since[1] <= 1 and zn.dz_tests[1] >= 1 and close[1] >= zn.dz_lo[1] and close < zn.dz_lo[1] - 0.3 * a14 and red and body < -0.6 * range and lower_wick < 0.2 * range",
)
# 3) FLIP RETEST: a broken zone changed role; price comes back to the same band from the other side and is rejected (role reversal).
FLIP = (
    "zn.dz_flip == 1 and zn.dz_since <= 2 and zn.dz_tests <= 1 and green and body > 0.5 * range and close > high[1] and close > zn.dz_hi",
    "zn.sz_flip == 1 and zn.sz_since <= 2 and zn.sz_tests <= 1 and red and body < -0.5 * range and close < low[1] and close < zn.sz_lo",
)


def both(setup, trend_call="", trend_put=""):
    c, p = setup
    return (f"{trend_call} and {c}" if trend_call else c), (f"{trend_put} and {p}" if trend_put else p)


def spec(name, factors, filters=None, tfs=(60, 300, 900)):
    return {
        "engine": "spec/1", "kind": "factors", "timeframes": list(tfs), "scope": {"symbols": None, "market": None},
        "gates": {"min_candles": 60, "min_agree": 1, "max_opposing_weight": 100, "min_strength": 0, "tier_a": None}, "expiry_candles": 1,
        "indicators": IND, "factors": factors,
        "filters": filters if filters is not None else [{"gap": 3, "type": "one_per_run"}, {"type": "min_payout", "value": 80}],
        "_name": name,
    }


def factor(fid, name, setup, weight=100):
    return {"id": fid, "name": name, "weight": weight, "kind": "expr", "call": setup[0], "put": setup[1], "vote": 1}


UP, DN = "ms.trend == 1", "ms.trend == -1"
NOT_DN, NOT_UP = "ms.trend >= 0", "ms.trend <= 0"

CANDIDATES = {
    # name: spec
    "zb-break-structure": spec("Zone breakout + structure", [factor("brk", "Break of a visited zone, with the structure", both(BREAK, NOT_DN, NOT_UP))]),
    "zb-break-plain": spec("Zone breakout (no structure rule)", [factor("brk", "Break of a visited zone", BREAK)]),
    "zb-flip-structure": spec("Flip retest + structure", [factor("flip", "Role-reversal retest of a broken zone, with the structure", both(FLIP, NOT_DN, NOT_UP))]),
    "zb-react-loose-structure": spec("Zone reaction, structure not against", [factor("react", "Reaction at a strong zone, structure not against", both(REACT, NOT_DN, NOT_UP))]),
    "zb-all-in-one": spec("Zones: reaction + breakout + flip retest (structure not against)", [
        factor("react", "Reaction at a strong fresh zone", both(REACT, NOT_DN, NOT_UP)),
        factor("brk", "Break of a visited zone", both(BREAK, NOT_DN, NOT_UP)),
        factor("flip", "Role-reversal retest", both(FLIP, NOT_DN, NOT_UP))]),
}

if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else EXAMPLES
    out.mkdir(parents=True, exist_ok=True)
    for key, s in CANDIDATES.items():
        body = {k: v for k, v in s.items() if k != "_name"}
        (out / f"{key}.json").write_text(json.dumps(body, indent=2))
        print("wrote", out / f"{key}.json", "-", s["_name"])
