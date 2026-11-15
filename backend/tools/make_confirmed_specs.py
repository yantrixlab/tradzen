"""Writes the "confirmed" multi-confirmation strategies as spec/1 JSON files in examples/ - built to scale: every pair, every timeframe, a signal only when several
different readings of the same chart agree and none disagrees.

  Six confirmations, each a vote for the next candle:
    EMA stack   EMA9 > EMA21 > EMA50 (CALL) or the mirror          MACD       histogram above / below zero
    Structure   market structure bias up / down (HH+HL / LH+LL)     Control    the candle closed in the top / bottom 30 % of its range
    Momentum    RSI(14) above 55 / below 45                         Direction  +DI above / below -DI (ADX family)
  A signal needs `min_agree` of them on one side and ZERO against (max_opposing_weight 0).
    cs1  5 of 6      cs2  6 of 6      cs3  6 of 6 and a strong trend (ADX >= 25)

  .venv\\Scripts\\python tools\\make_confirmed_specs.py [out_dir]

tools/bt_compare.py and the study in the README show what the confirmations are worth: measured, not assumed.
"""
import json
import sys
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parent.parent.parent / "examples"

IND = [{"id": "e9", "type": "ema", "n": 9}, {"id": "e21", "type": "ema", "n": 21}, {"id": "e50", "type": "ema", "n": 50},
       {"id": "mc", "type": "macd", "fast": 12, "slow": 26, "signal": 9}, {"id": "r14", "type": "rsi", "n": 14},
       {"id": "ad", "type": "adx", "n": 14}, {"id": "ms", "type": "structure", "swing": 3, "max_age": 120}]


def factor(fid, name, call, put):
    return {"id": fid, "name": name, "weight": 16, "kind": "expr", "call": call, "put": put, "vote": 1}


FACTORS = [
    factor("ema_stack", "EMA 9 > 21 > 50", "e9 > e21 and e21 > e50", "e9 < e21 and e21 < e50"),
    factor("macd", "MACD histogram side", "mc.hist > 0", "mc.hist < 0"),
    factor("structure", "Market structure bias", "ms.bias == 1", "ms.bias == -1"),
    factor("control", "Close in the top / bottom 30% of the range", "close >= low + 0.7 * range", "close <= low + 0.3 * range"),
    factor("momentum", "RSI(14) momentum", "r14 > 55", "r14 < 45"),
    factor("direction", "+DI vs -DI", "ad.pdi > ad.mdi", "ad.pdi < ad.mdi"),
]


def spec(min_agree, adx=None):
    f = json.loads(json.dumps(FACTORS))
    if adx:                                                                      # a strong trend is one more condition on every vote
        for x in f:
            x["call"], x["put"] = f"({x['call']}) and ad.adx >= {adx}", f"({x['put']}) and ad.adx >= {adx}"
    return {"engine": "spec/1", "kind": "factors", "timeframes": [60, 300, 900], "scope": {"symbols": None, "market": None},
            "gates": {"min_candles": 60, "min_agree": min_agree, "max_opposing_weight": 0, "min_strength": 0, "tier_a": None}, "expiry_candles": 1,
            "indicators": IND, "factors": f, "filters": [{"type": "min_payout", "value": 85}]}


CANDIDATES = {"cs1-confirmed-5of6": spec(5), "cs2-confirmed-6of6": spec(6), "cs3-confirmed-6of6-strong-trend": spec(6, adx=25)}

if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else EXAMPLES
    out.mkdir(parents=True, exist_ok=True)
    for key, s in CANDIDATES.items():
        (out / f"{key}.json").write_text(json.dumps(s, indent=2))
        print("wrote", out / f"{key}.json")
