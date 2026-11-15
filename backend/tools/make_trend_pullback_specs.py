"""Writes strategies that trade WITH the market structure after a retracement (down-trend: lower highs / lower lows -> sell the resumption; up-trend: mirror),
as spec/1 JSON files, plus one diagnostic that just follows the structure every candle (to measure what the structure alone says about the next candle).

  .venv\\Scripts\\python tools\\make_trend_pullback_specs.py [out_dir]
"""
import json
import sys
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parent.parent.parent / "examples"

IND = [
    {"id": "ms", "type": "structure", "swing": 3, "max_age": 120},
    {"id": "e21", "type": "ema", "n": 21},
]


def spec(factors, filters=None, tfs=(60, 300, 900)):
    return {
        "engine": "spec/1", "kind": "factors", "timeframes": list(tfs), "scope": {"symbols": None, "market": None},
        "gates": {"min_candles": 60, "min_agree": 1, "max_opposing_weight": 100, "min_strength": 0, "tier_a": None}, "expiry_candles": 1,
        "indicators": IND, "factors": factors,
        "filters": filters if filters is not None else [{"gap": 3, "type": "one_per_run"}, {"type": "min_payout", "value": 80}],
    }


def factor(fid, name, call, put):
    return {"id": fid, "name": name, "weight": 100, "kind": "expr", "call": call, "put": put, "vote": 1}


PAY = [{"type": "min_payout", "value": 80}]

CANDIDATES = {
    # diagnostic: what does the structure alone say? (one signal on EVERY candle of a confirmed trend - not a strategy to trade)
    "tp-diagnostic-follow": spec([factor("follow", "Follow the structure on every candle (diagnostic)", "ms.trend == 1", "ms.trend == -1")], PAY),
    # retracement, then the move resumes: >= 2 counter-trend candles among the 5 before, the structure still intact, a full-bodied candle breaking the previous candle's extreme
    "tp-pullback-resume": spec([factor(
        "resume", "Structure trend, pullback, then the move resumes",
        "ms.trend == 1 and count(red, 5)[1] >= 2 and close[1] > ms.sl and green and body > 0.5 * range and close > high[1]",
        "ms.trend == -1 and count(green, 5)[1] >= 2 and close[1] < ms.sh and red and body < -0.5 * range and close < low[1]")]),
    # the same, and the pullback went back to the EMA21 (a deeper, "real" retracement)
    "tp-pullback-ema": spec([factor(
        "resume_ema", "Structure trend, pullback to the EMA21, then the move resumes",
        "ms.trend == 1 and lowest(low, 3)[1] <= e21[1] and close[1] > ms.sl and green and body > 0.5 * range and close > high[1]",
        "ms.trend == -1 and highest(high, 3)[1] >= e21[1] and close[1] < ms.sh and red and body < -0.5 * range and close < low[1]")]),
    # a new swing starts: in a down-trend whose last high was LOWER, a full-bodied close breaks the last swing low (and mirror for an up-trend with a higher low)
    "tp-swing-break": spec([factor(
        "swing_break", "Lower high, then the last swing low breaks (and mirror)",
        "ms.trend == 1 and ms.hl == 1 and close[1] <= ms.sh and close > ms.sh and green and body > 0.5 * range",
        "ms.trend == -1 and ms.hh == -1 and close[1] >= ms.sl and close < ms.sl and red and body < -0.5 * range")]),
}

if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else EXAMPLES
    out.mkdir(parents=True, exist_ok=True)
    for key, s in CANDIDATES.items():
        (out / f"{key}.json").write_text(json.dumps(s, indent=2))
        print("wrote", out / f"{key}.json")
