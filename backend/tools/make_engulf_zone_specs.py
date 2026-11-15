"""Writes the engulfing-candle supply / demand strategies as spec/1 JSON files in examples/.

  DEMAND  a red candle engulfed by a green candle (or a short run of green candles): the zone is the red candle's high-low range. Traded in an UP-trend
          (market structure: higher highs and higher lows): when price comes back into the zone and a strong green candle forms there -> CALL, again and again
          (every new pullback makes a new zone) until the structure turns.
  SUPPLY  a green candle engulfed by red; traded once the structure has turned DOWN (a lower high, then the last higher low breaks): price comes back up into
          the zone and a red candle forms there -> PUT, again and again until the structure turns.
  Each zone works for ONE visit (the candles while price is inside it) and is then used up; a close through it kills it.

  .venv\\Scripts\\python tools\\make_engulf_zone_specs.py [out_dir]

A few variants on purpose (each extra one that is tried makes a lucky result more likely): the idea as described, the pullback / back-to-back candle that
leads into the zone, the same with a weakening approach, and only strong engulfing moves.
"""
import json
import sys
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parent.parent.parent / "examples"

CALL = "ms.bias == 1 and eg.dz_visit == 1 and green and body > 0.5 * range and close > eg.dz_lo"
PUT = "ms.bias == -1 and eg.sz_visit == 1 and red and body < -0.5 * range and close < eg.sz_hi"


def spec(name, call=CALL, put=PUT, eg=None):
    ind = [{"id": "ms", "type": "structure", "swing": 3, "max_age": 120},
           {"id": "eg", "type": "engulf", "swing": 3, "trend_age": 120, "max_age": 120, "min_body": 0.5, "max_run": 3, "visits": 1, "brk_atr": 0.3, **(eg or {})}]
    return {"engine": "spec/1", "kind": "factors", "timeframes": [60, 300, 900], "scope": {"symbols": None, "market": None},
            "gates": {"min_candles": 60, "min_agree": 1, "max_opposing_weight": 100, "min_strength": 0, "tier_a": None}, "expiry_candles": 1,
            "indicators": ind,
            "factors": [{"id": "engulf_zone", "name": name, "weight": 100, "kind": "expr", "call": call, "put": put, "vote": 1}],
            "filters": [{"gap": 3, "type": "one_per_run"}, {"type": "min_payout", "value": 80}]}


CANDIDATES = {
    "ez1-engulf-zone": spec("Engulfing-candle zones with the structure: strong candle on the first visit"),
    # the candle that leads into the zone is the pullback: red into a demand zone, green into a supply zone ("back-to-back green-red")
    "ez2-pullback-confirm": spec("Engulfing-candle zones, pullback candle then the strong confirming candle",
                                 call=f"{CALL} and red[1]", put=f"{PUT} and green[1]"),
    # ... and the pullback is losing strength: each approach candle smaller than the one before
    "ez3-weakening-approach": spec("Engulfing-candle zones, pullback losing strength then the confirming candle",
                                   call=f"{CALL} and red[1] and abs(body[1]) < abs(body[2])", put=f"{PUT} and green[1] and abs(body[1]) < abs(body[2])"),
    "ez4-strong-engulf": spec("Engulfing-candle zones, only strong engulfing moves (1+ ATR)",
                              call=f"eg.dz_body >= 1.0 and {CALL}", put=f"eg.sz_body >= 1.0 and {PUT}"),
}

if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else EXAMPLES
    out.mkdir(parents=True, exist_ok=True)
    for key, s in CANDIDATES.items():
        (out / f"{key}.json").write_text(json.dumps(s, indent=2))
        print("wrote", out / f"{key}.json")
