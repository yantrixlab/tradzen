"""Writes confluence strategies as spec/1 JSON files in examples/: the Heikin-Ashi Continuation signal AND the MACD Histogram Cross signal must agree before
a signal counts ("a strong PUT" = both say PUT; "a strong CALL" = both say CALL).

  .venv\\Scripts\\python tools\\make_confluence_specs.py [out_dir] [--with-baselines]

The two built-in signals fire on different candles (the MACD flip is a one-candle event, the Heikin-Ashi trend candle can repeat), so there are three
ways to ask for agreement - tried on purpose as a small, fixed set:
  cf1  both fire on the SAME candle                                   (strictest, fewest signals)
  cf2  the MACD flip happened within the last 3 candles and the Heikin-Ashi trend candle is there now
  cf3  the MACD histogram is on the right side of zero (momentum agrees) and the Heikin-Ashi trend candle is there now      (loosest)
--with-baselines also writes each built-in signal alone, to compare against.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app.builtins import BUILTINS  # noqa: E402

EXAMPLES = HERE.parent.parent / "examples"

IND = [{"id": "ha", "type": "heikin"}, {"id": "macd", "type": "macd", "fast": 12, "slow": 26, "signal": 9}]
HA_CALL, HA_PUT = "ha.green and ha.green[1] and ha.nolow", "ha.red and ha.red[1] and ha.nohigh"


def spec(name, call, put):
    return {"engine": "spec/1", "kind": "factors", "timeframes": [60, 300, 900], "scope": {"symbols": None, "market": None},
            "gates": {"min_candles": 60, "min_agree": 1, "max_opposing_weight": 100, "min_strength": 0, "tier_a": None}, "expiry_candles": 1,
            "indicators": IND, "factors": [{"id": "confluence", "name": name, "weight": 100, "kind": "expr", "call": call, "put": put, "vote": 1}],
            "filters": [{"gap": 3, "type": "one_per_run"}, {"type": "min_payout", "value": 80}]}


CANDIDATES = {
    "cf1-same-candle": spec("Heikin-Ashi continuation + MACD flip on the same candle",
                            f"cross_above(macd.hist, 0) and {HA_CALL}", f"cross_below(macd.hist, 0) and {HA_PUT}"),
    "cf2-macd-flip-within-3": spec("Heikin-Ashi continuation now + MACD flip within the last 3 candles",
                                   f"count(cross_above(macd.hist, 0), 3) >= 1 and {HA_CALL}", f"count(cross_below(macd.hist, 0), 3) >= 1 and {HA_PUT}"),
    "cf3-macd-side": spec("Heikin-Ashi continuation now + MACD histogram on the same side of zero",
                          f"macd.hist > 0 and {HA_CALL}", f"macd.hist < 0 and {HA_PUT}"),
}


def baselines() -> dict:
    out = {}
    for key in ("macd-cross", "heikin-ashi-trend"):
        b = next(x for x in BUILTINS if x["key"] == key)
        out[f"base-{key}"] = {**b["spec"], "filters": [{"type": "min_payout", "value": 80}]}
    return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = Path(args[0]) if args else EXAMPLES
    out.mkdir(parents=True, exist_ok=True)
    items = dict(CANDIDATES)
    if "--with-baselines" in sys.argv:
        items.update(baselines())
    for key, s in items.items():
        (out / f"{key}.json").write_text(json.dumps(s, indent=2))
        print("wrote", out / f"{key}.json")
