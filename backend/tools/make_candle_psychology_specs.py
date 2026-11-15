"""Writes the "a signal on every candle, read from candle psychology" strategies as spec/1 JSON files in examples/.

  Every closed candle is read the way a price-action trader reads it - who won the candle, how decisively, who was rejected - and the next candle is called:
    REJECTION   long lower wick (buyers rejected the low) -> CALL;  long upper wick (sellers rejected the high) -> PUT
    ENGULFING   a candle that swallows the previous one -> follow it
    CONTROL     a body that fills most of the range and closes at the extreme -> the winner keeps control, follow it
    EXHAUSTION  after a run of same-colour candles the push weakens (shrinking bodies / a wick against the run) -> the opposite side
    FALLBACK    nothing special on the candle -> lean with the colour of the last candle (variants: with it / against it)
  Variants: cp1 reads every candle (the fallback guarantees a signal), cp2 only when a real pattern is there, cp3 = cp1 but never against the market structure.

  .venv\\Scripts\\python tools\\make_candle_psychology_specs.py [out_dir]

Honest expectation: the candles of a 1-minute chart are very close to a fair coin, so these are tested (tools/bt_compare.py) and reported as they come out.
"""
import json
import sys
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parent.parent.parent / "examples"

REJECT_UP = "hammer and lower_wick >= 0.5 * range"
REJECT_DN = "star and upper_wick >= 0.5 * range"
CONTROL_UP = "green and body >= 0.6 * range and close >= low + 0.8 * range"
CONTROL_DN = "red and -body >= 0.6 * range and close <= low + 0.2 * range"
EXHAUST_UP = "red[1] and red[2] and red and abs(body) < abs(body[1]) and lower_wick > abs(body)"          # sellers pushing three candles, the push weakens and buyers show up
EXHAUST_DN = "green[1] and green[2] and green and abs(body) < abs(body[1]) and upper_wick > abs(body)"


def factor(fid, name, call, put, weight):
    return {"id": fid, "name": name, "weight": weight, "kind": "expr", "call": call, "put": put, "vote": 1}


def spec(factors, bias=False, min_agree=1):
    ind = [{"id": "ms", "type": "structure", "swing": 3, "max_age": 120}] if bias else []
    if bias:                                                              # never trade against the market structure
        for f in factors:
            f["call"] = f"({f['call']}) and ms.bias >= 0"
            f["put"] = f"({f['put']}) and ms.bias <= 0"
    return {"engine": "spec/1", "kind": "factors", "timeframes": [60, 300, 900], "scope": {"symbols": None, "market": None},
            "gates": {"min_candles": 60, "min_agree": min_agree, "max_opposing_weight": 100, "min_strength": 0, "tier_a": None}, "expiry_candles": 1,
            "indicators": ind, "factors": factors, "filters": [{"type": "min_payout", "value": 80}]}


def patterns():
    return [factor("rejection", "Rejection wick (hammer / shooting star)", REJECT_UP, REJECT_DN, 30),
            factor("engulf", "Engulfing candle", "bull_engulf", "bear_engulf", 25),
            factor("control", "Close control (strong body closing at the extreme)", CONTROL_UP, CONTROL_DN, 20),
            factor("exhaustion", "Exhaustion after a run", EXHAUST_UP, EXHAUST_DN, 25)]


def fallback(follow=True):
    return factor("last_colour", "Lean with the last candle's colour" if follow else "Lean against the last candle's colour",
                  "green" if follow else "red", "red" if follow else "green", 10)


CANDIDATES = {
    "cp1-every-candle": spec(patterns() + [fallback(True)]),
    "cp1b-every-candle-fade": spec(patterns() + [fallback(False)]),
    "cp2-patterns-only": spec(patterns()),
    "cp3-every-candle-with-structure": spec(patterns() + [fallback(True)], bias=True),
}

if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else EXAMPLES
    out.mkdir(parents=True, exist_ok=True)
    for key, s in CANDIDATES.items():
        (out / f"{key}.json").write_text(json.dumps(s, indent=2))
        print("wrote", out / f"{key}.json")
