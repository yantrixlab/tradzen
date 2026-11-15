"""Writes a ladder of strategies that trade a reaction at a supply / demand zone, identical except for how STRONG the zone has to be (measured from the candles:
run = how far price travelled away from the zone, tight = how compressed the base was, dep = how decisive the departure candle was).
If "stronger zone -> better reaction" is true, accuracy rises step by step along the ladder; if it is false, it does not.

  .venv\\Scripts\\python tools\\make_zone_strength_specs.py [out_dir]
"""
import json
import sys
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parent.parent.parent / "examples"

IND = [{"id": "zn", "type": "zones", "atr_n": 14, "base_atr": 2.5, "base_len": 8, "brk_atr": 0.3, "max_age": 250, "max_zones": 10, "memory": 3, "move_atr": 1}]

CALL = ("zn.dz_since <= 2 and zn.dz_tests <= 2 and zn.room_up >= 1.5 and green and body > 0.5 * range and close > high[1] and close > zn.dz_hi and low >= zn.dz_lo")
PUT = ("zn.sz_since <= 2 and zn.sz_tests <= 2 and zn.room_dn >= 1.5 and red and body < -0.5 * range and close < low[1] and close < zn.sz_lo and high <= zn.sz_hi")


def strength(side: str, move=1.0, run=None, tight=None, dep=None) -> str:
    p = "dz" if side == "call" else "sz"
    parts = [f"zn.{p}_move >= {move}"]
    if run is not None:
        parts.append(f"zn.{p}_run >= {run}")
    if tight is not None:
        parts.append(f"zn.{p}_tight <= {tight}")
    if dep is not None:
        parts.append(f"zn.{p}_dep >= {dep}")
    return " and ".join(parts)


def spec(name, **kw):
    f = {"id": "zone_reaction", "name": name, "weight": 100, "kind": "expr", "vote": 1,
         "call": f"{strength('call', **kw)} and {CALL}", "put": f"{strength('put', **kw)} and {PUT}"}
    return {"engine": "spec/1", "kind": "factors", "timeframes": [60, 300, 900], "scope": {"symbols": None, "market": None},
            "gates": {"min_candles": 60, "min_agree": 1, "max_opposing_weight": 100, "min_strength": 0, "tier_a": None}, "expiry_candles": 1,
            "indicators": IND, "factors": [f], "filters": [{"gap": 3, "type": "one_per_run"}, {"type": "min_payout", "value": 80}]}


CANDIDATES = {
    "zs0-any-zone": spec("Zone reaction (any zone that left by 1 ATR)"),
    "zs1-run-2.5": spec("Zone reaction, price ran 2.5+ ATR away", run=2.5),
    "zs2-run-4": spec("Zone reaction, price ran 4+ ATR away", run=4),
    "zs3-tight-decisive": spec("Zone reaction, tight base + decisive departure", tight=1.5, dep=0.6),
    "zs4-strongest": spec("Zone reaction, strongest zones only", move=1.5, run=4, tight=1.5, dep=0.6),
}

if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else EXAMPLES
    out.mkdir(parents=True, exist_ok=True)
    for key, s in CANDIDATES.items():
        (out / f"{key}.json").write_text(json.dumps(s, indent=2))
        print("wrote", out / f"{key}.json")
