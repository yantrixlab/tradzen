"""Writes the price-action / trend-following strategy specs to examples/price-action/*.json (import them in Strategies -> New -> JSON tab).

The list is FIXED before any of them was tested, so nothing was picked after seeing results. Every spec:
  * trades WITH the market direction (the opposite of the reversal strategies),
  * needs an ESTABLISHED trend before it signals (so a fresh direction change cannot flip the signal at once),
  * measures candle strength by body size against the candle's range and by where it closes,
  * ends with the one_per_run filter (no back-to-back repeats).

    .venv\\Scripts\\python tools\\make_price_action_specs.py          # writes the files and prints the list
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "examples" / "price-action"
INDICATORS = [{"id": "ema9", "type": "ema", "n": 9}, {"id": "ema21", "type": "ema", "n": 21}]


def up(n: int) -> str:        # established up-trend: fast EMA above slow EMA and price above the slow EMA for n candles in a row
    return f"count(ema9 > ema21 and close > ema21, {n}) >= {n}"


def down(n: int) -> str:
    return f"count(ema9 < ema21 and close < ema21, {n}) >= {n}"


def spec(name: str, call: str, put: str) -> dict:
    return {"engine": "spec/1", "kind": "factors", "timeframes": [60, 300, 900], "scope": {"symbols": None, "market": None},
            "indicators": INDICATORS,
            "factors": [{"id": "signal", "name": name, "weight": 100, "kind": "expr", "call": call, "put": put, "vote": 1}],
            "gates": {"min_candles": 60, "min_agree": 1, "max_opposing_weight": 100, "min_strength": 0, "tier_a": None},
            "filters": [{"type": "one_per_run", "gap": 2}], "expiry_candles": 1}


STRONG_UP = "green and body > 0.6 * range and upper_wick < 0.2 * range"            # strong candle: big body, closes near its high
STRONG_DN = "red and body < -0.6 * range and lower_wick < 0.2 * range"

SPECS: dict[str, tuple[str, dict]] = {
    "pa1-trend-strong-candle": ("Trend + strong candle (5-candle trend)",
        spec("Trend + strong candle", f"{up(5)} and {STRONG_UP}", f"{down(5)} and {STRONG_DN}")),
    "pa1b-trend-strong-candle-slow": ("Trend + strong candle (10-candle trend, slower to confirm a new direction)",
        spec("Trend + strong candle, slow confirm", f"{up(10)} and {STRONG_UP}", f"{down(10)} and {STRONG_DN}")),
    "pa2-pullback-resume": ("Pullback then resume: trend, one counter candle, then a strong candle that takes out its high/low",
        spec("Pullback then resume", f"{up(8)} and red[1] and green and body > 0.6 * range and close > high[1]",
             f"{down(8)} and green[1] and red and body < -0.6 * range and close < low[1]")),
    "pa3-structure-strength": ("Market structure: higher highs AND higher lows (3-candle blocks) + a firm candle",
        spec("Higher highs / higher lows", "lowest(low, 3) > lowest(low, 3)[3] and highest(high, 3) > highest(high, 3)[3] and green and body > 0.5 * range",
             "highest(high, 3) < highest(high, 3)[3] and lowest(low, 3) < lowest(low, 3)[3] and red and body < -0.5 * range")),
    "pa4-growing-thrust": ("Three same-colour candles with growing bodies, the last one taking out the previous extreme",
        spec("Growing thrust", "green[2] and green[1] and green and body > body[1] and body[1] > body[2] and close > high[1]",
             "red[2] and red[1] and red and body < body[1] and body[1] < body[2] and close < low[1]")),
    "pa5-range-breakout-strong": ("Close beyond the 10-candle high/low with a strong candle",
        spec("Range breakout, strong candle", "close > highest(high, 10)[1] and body > 0.6 * range", "close < lowest(low, 10)[1] and body < -0.6 * range")),
    "pa7-efficient-move": ("Efficient directional move: 5-candle net move is most of the candle bodies, last candle agrees",
        spec("Efficient move", "close > close[5] and abs(close - close[5]) > 3 * sma(abs(body), 5) and green",
             "close < close[5] and abs(close - close[5]) > 3 * sma(abs(body), 5) and red")),
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for key, (desc, sp) in SPECS.items():
        (OUT / f"{key}.json").write_text(json.dumps(sp, indent=2) + "\n", encoding="utf-8")
        print(f"{key:34s} {desc}")
    print(f"\nwritten to {OUT}")


if __name__ == "__main__":
    main()
