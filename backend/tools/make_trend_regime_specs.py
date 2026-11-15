"""Writes "trade only when the market is trending, never when it is sideways" strategies as spec/1 JSON files in examples/.

  REGIME   TRENDING UP    EMA9 > EMA21 > EMA50, ADX at least `adx` (trend has strength) and the EMAs are fanned out (EMA9-EMA21 gap >= `gap` x ATR: not tangled)
           TRENDING DOWN  the mirror - and the early SHIFT into a down-trend counts too (EMA9 under EMA21, a lower high on the structure, price under EMA21, ADX rising)
           SIDEWAYS       anything else (low ADX, EMAs tangled / flat) -> NO SIGNAL. This is the main rule: the strategy is silent in a range.
  SIGNAL   inside a trending regime a signal on (nearly) every candle that keeps the trend going - far more frequent than a pullback strategy:
           tr1 every candle that closes on the trend side of the EMA9
           tr2 the same, but only after a counter-trend candle (a pullback that resumes)
           tr3 tr1 with a stricter trend (ADX 28, wider gap)
           tr4 tr1 + the shift into a down-trend (lower high, EMA9 crosses under EMA21) as its own PUT rule

  .venv\\Scripts\\python tools\\make_trend_regime_specs.py [out_dir]
"""
import json
import sys
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parent.parent.parent / "examples"

IND = [{"id": "e9", "type": "ema", "n": 9}, {"id": "e21", "type": "ema", "n": 21}, {"id": "e50", "type": "ema", "n": 50}, {"id": "a14", "type": "atr", "n": 14},
       {"id": "ad", "type": "adx", "n": 14}, {"id": "ms", "type": "structure", "swing": 3, "max_age": 120}]


def regime(adx=22, gap=0.3):
    up = f"e9 > e21 and e21 > e50 and ad.adx >= {adx} and (e9 - e21) >= {gap} * a14"
    dn = f"e9 < e21 and e21 < e50 and ad.adx >= {adx} and (e21 - e9) >= {gap} * a14"
    return up, dn


def shift_down(adx=18):
    """Turning into a down-trend: still above the slow EMA50 maybe, but the structure made a lower high, EMA9 is under EMA21, price under EMA21 and the trend strength is rising."""
    return f"e9 < e21 and ms.hh == -1 and close < e21 and ad.adx >= {adx} and ad.adx > ad.adx[3]"


def spec(factors):
    return {"engine": "spec/1", "kind": "factors", "timeframes": [60, 300, 900], "scope": {"symbols": None, "market": None},
            "gates": {"min_candles": 60, "min_agree": 1, "max_opposing_weight": 100, "min_strength": 0, "tier_a": None}, "expiry_candles": 1,
            "indicators": IND, "factors": factors, "filters": [{"type": "min_payout", "value": 80}]}


def factor(fid, name, call, put, weight=100):
    return {"id": fid, "name": name, "weight": weight, "kind": "expr", "call": call, "put": put, "vote": 1}


def build(adx=22, gap=0.3, resume=False, shift=False):
    up, dn = regime(adx, gap)
    call = f"{up} and close > e9"
    put = f"{dn} and close < e9"
    if resume:
        call += " and red[1]"
        put += " and green[1]"
    fs = [factor("trend", "Trending market, trade with the trend (silent when sideways)", call, put)]
    if shift:
        fs.append(factor("shift", "Shift into a down-trend", "0 > 1", f"{shift_down()} and close < e9"))
    return spec(fs)


CANDIDATES = {
    "tr1-trend-follow": build(),
    "tr2-trend-pullback-resume": build(resume=True),
    "tr3-strong-trend-only": build(adx=28, gap=0.5),
    "tr4-trend-and-shift-down": build(shift=True),
}

if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else EXAMPLES
    out.mkdir(parents=True, exist_ok=True)
    for key, s in CANDIDATES.items():
        (out / f"{key}.json").write_text(json.dumps(s, indent=2))
        print("wrote", out / f"{key}.json")
