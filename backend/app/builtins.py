"""Built-in strategy library: widely used indicator techniques expressed as specs (see spec.py). They ship in *paper* mode - signals are
recorded and settled like any other, but no alerts fire until you promote a strategy to *live*. None is presumed profitable; the
dashboard measures all of them the same way.
"""
from __future__ import annotations

DEFAULT_KEY = "confluence-5"
ALL_TFS = [60, 300, 900]


def _single(indicators: list[dict], call: str | None, put: str | None, name: str, min_candles: int = 60, filters: list | None = None) -> dict:
    return {"engine": "spec/1", "kind": "factors", "timeframes": ALL_TFS, "indicators": indicators,
            "factors": [{"id": "signal", "name": name, "weight": 100, "kind": "expr", "call": call, "put": put}],
            "gates": {"min_candles": min_candles, "min_agree": 1}, "filters": filters or [], "expiry_candles": 1}

def _cover_reversal() -> dict:
    """Hammer / shooting star -> a weak level -> a run toward it -> a decisive candle back. The weak high (a star's high) / weak low (a hammer's low) and everything price does
    on its way back to it is tracked by the `weak` indicator (weaklevels.py: wk.hi_* / wk.lo_*); the colour of the setup candle does not matter. A signal needs ALL three factors on the
    candle that just closed (min_agree 3); [1] = the candle before it (the last candle of the run). Distances are in ATR, so it needs no per-pair number.
      1. SETUP  a star (hammer) at a top (bottom) armed a weak level and at least one candle has followed it.
      2. RUN    the last candle before the trigger ran toward the level (green for a star, red for a hammer) AND either price has not closed beyond the level (it stopped short or
                only wicked into it) OR it broke through and kept closing beyond it, at least 2 closes in a row.
                C. or the level was broken (a close beyond it at some point) and the first candle back the other way has already printed (wk.hi_back / wk.lo_back >= 1).
      3. TRIGGER a candle back the other way (any size for now) that closes past the previous candle's open - OR, after a break, the SECOND candle back the other way
                (closing beyond the first one's close): red candles break below the weak low, a green candle appears, the next green candle = CALL.
    Early alert (`heads_up`): one candle or more before the signal, when the setup is armed and price runs toward the level (within 1 ATR) or has broken it with 2+ closes."""
    ind = [{"id": "a", "type": "atr", "n": 14}, {"id": "wk", "type": "weak"}]
    star = {"setup": "wk.hi_lvl[1] > 0 and wk.hi_run[1] >= 1",
            "run": "(green[1] and (wk.hi_closes[1] == 0 or wk.hi_closes[1] >= 2)) or (wk.hi_broke[1] == 1 and wk.hi_back[1] >= 1)",
            "trig": "(red and wk.hi_back[1] == 0 and close < open[1]) or (wk.hi_back[1] >= 1 and red and close < close[1])"}
    ham = {"setup": "wk.lo_lvl[1] > 0 and wk.lo_run[1] >= 1",
           "run": "(red[1] and (wk.lo_closes[1] == 0 or wk.lo_closes[1] >= 2)) or (wk.lo_broke[1] == 1 and wk.lo_back[1] >= 1)",
           "trig": "(green and wk.lo_back[1] == 0 and close > open[1]) or (wk.lo_back[1] >= 1 and green and close > close[1])"}
    return {"engine": "spec/1", "kind": "factors", "timeframes": ALL_TFS, "indicators": ind,
            "factors": [
                {"id": "setup", "name": "Shooting star at a top / hammer at a bottom left a weak level", "weight": 34, "kind": "expr", "call": ham["setup"], "put": star["setup"]},
                {"id": "run", "name": "Price ran toward the level and stopped short, or broke it and kept closing beyond it, or broke it and has started to come back", "weight": 33, "kind": "expr",
                 "call": ham["run"], "put": star["run"]},
                {"id": "trigger", "name": "Candle back the other way (or, after a break, the second candle back)", "weight": 33, "kind": "expr",
                 "call": ham["trig"], "put": star["trig"]}],
            "gates": {"min_candles": 60, "min_agree": 3}, "filters": [], "expiry_candles": 1,
            "heads_up": {
                "title": "weak level ahead",
                "call": "wk.lo_lvl > 0 and wk.lo_run >= 1 and ((red and ((wk.lo_closes == 0 and wk.lo_dist <= 1) or wk.lo_closes >= 2)) or (wk.lo_broke == 1 and wk.lo_back >= 1))",
                "put": "wk.hi_lvl > 0 and wk.hi_run >= 1 and ((green and ((wk.hi_closes == 0 and wk.hi_dist <= 1) or wk.hi_closes >= 2)) or (wk.hi_broke == 1 and wk.hi_back >= 1))",
                "level": {"call": "wk.lo_lvl", "put": "wk.hi_lvl"},
                "text": {
                    "put": "A shooting star formed a weak high at {wk.hi_lvl} ({wk.hi_run} candles ago). Price is {wk.hi_dist} ATR under it (negative = above it) and has closed above it {wk.hi_closes} time(s) in a row. "
                           "If a red candle now closes below the previous candle's open (or, once it has broken the level, a second red candle in a row closes lower), the PUT signal follows.",
                    "call": "A hammer formed a weak low at {wk.lo_lvl} ({wk.lo_run} candles ago). Price is {wk.lo_dist} ATR over it (negative = below it) and has closed below it {wk.lo_closes} time(s) in a row. "
                            "If a green candle now closes above the previous candle's open (or, once it has broken the level, a second green candle in a row closes higher), the CALL signal follows."}}}


BUILTINS: list[dict] = [
    {"key": DEFAULT_KEY, "name": "Confluence 5-Factor", "status": "live", "tags": ["confluence", "reversal"],
     "description": "Bollinger bounce (30), RSI divergence (25), Stochastic cross (20), CCI reversal (15) and candlestick pattern (10) vote; "
                    "2+ factors agree = tier B, 3+ and strength 40+ = tier A.",
     "spec": {"engine": "spec/1", "kind": "factors", "timeframes": ALL_TFS, "indicators": [],
              "factors": [
                  {"id": "bollinger", "name": "Bollinger Band Bounce", "weight": 30, "kind": "fn", "fn": "bollinger_bounce", "params": {"n": 20, "k": 2.0}},
                  {"id": "rsi_divergence", "name": "RSI Divergence", "weight": 25, "kind": "fn", "fn": "rsi_divergence", "params": {"n_rsi": 14, "lookback": 45}},
                  {"id": "stochastic", "name": "Stochastic %K/%D Cross", "weight": 20, "kind": "fn", "fn": "stochastic_cross", "params": {}},
                  {"id": "cci", "name": "CCI Extreme + Reversal", "weight": 15, "kind": "fn", "fn": "cci_reversal", "params": {"n": 20}},
                  {"id": "candlestick", "name": "Candlestick Pattern", "weight": 10, "kind": "fn", "fn": "candlestick", "params": {}}],
              "gates": {"min_candles": 60, "min_agree": 2, "max_opposing_weight": 25, "min_strength": 30, "tier_a": {"agree": 3, "strength": 40}},
              "filters": [], "expiry_candles": 1}},

    {"key": "bollinger-reversion", "name": "Bollinger Reversion", "status": "paper", "tags": ["reversal", "bands"],
     "description": "Price closed outside the 20/2 Bollinger band and the next candle closes back inside: mean-reversion entry.",
     "spec": _single([{"id": "bb", "type": "bollinger", "n": 20, "k": 2.0}],
                     "close[1] < bb.lower[1] and close >= bb.lower", "close[1] > bb.upper[1] and close <= bb.upper", "Band re-entry")},

    {"key": "rsi-extremes", "name": "RSI Extremes", "status": "paper", "tags": ["reversal", "oscillator"],
     "description": "RSI(14) leaves oversold (crosses up through 30) = CALL, leaves overbought (crosses down through 70) = PUT.",
     "spec": _single([{"id": "rsi14", "type": "rsi", "n": 14}], "cross_above(rsi14, 30)", "cross_below(rsi14, 70)", "RSI exit from extreme")},

    {"key": "rsi2-connors", "name": "RSI(2) Pullback (Connors)", "status": "paper", "tags": ["pullback", "trend"],
     "description": "Buy a very short-term dip (RSI(2) < 10) above the 200-SMA, sell a spike (RSI(2) > 90) below it. Needs 210 candles.",
     "spec": _single([{"id": "rsi2", "type": "rsi", "n": 2}, {"id": "sma200", "type": "sma", "n": 200}],
                     "rsi2 < 10 and close > sma200", "rsi2 > 90 and close < sma200", "RSI(2) pullback", min_candles=210)},

    {"key": "stochastic-reversal", "name": "Stochastic Reversal", "status": "paper", "tags": ["reversal", "oscillator"],
     "description": "%K crosses %D inside the oversold (<25) or overbought (>75) zone of Stochastic(14,3,3).",
     "spec": _single([{"id": "st", "type": "stoch", "k": 14, "smooth": 3, "d": 3}],
                     "cross_above(st.k, st.d) and st.k < 25", "cross_below(st.k, st.d) and st.k > 75", "Stochastic zone cross")},

    {"key": "macd-cross", "name": "MACD Histogram Cross", "status": "paper", "tags": ["trend", "momentum"],
     "description": "MACD(12,26,9) histogram flips sign: momentum turns up (CALL) or down (PUT).",
     "spec": _single([{"id": "macd", "type": "macd", "fast": 12, "slow": 26, "signal": 9}],
                     "cross_above(macd.hist, 0)", "cross_below(macd.hist, 0)", "MACD histogram flip")},

    {"key": "ema-cross-trend", "name": "EMA 9/21 Cross + Trend", "status": "paper", "tags": ["trend"],
     "description": "EMA 9 crosses EMA 21 in the direction of the EMA 50 slope.",
     "spec": _single([{"id": "ema9", "type": "ema", "n": 9}, {"id": "ema21", "type": "ema", "n": 21}, {"id": "ema50", "type": "ema", "n": 50}],
                     "cross_above(ema9, ema21) and ema50 > ema50[3]", "cross_below(ema9, ema21) and ema50 < ema50[3]", "EMA cross with slope filter")},

    {"key": "supertrend-flip", "name": "Supertrend Flip", "status": "paper", "tags": ["trend"],
     "description": "Supertrend(10, 3) changes direction.",
     "spec": _single([{"id": "st", "type": "supertrend", "n": 10, "mult": 3.0}],
                     "st.dir == 1 and st.dir[1] == -1", "st.dir == -1 and st.dir[1] == 1", "Supertrend flip")},

    {"key": "psar-ema", "name": "Parabolic SAR + EMA200", "status": "paper", "tags": ["trend"],
     "description": "Parabolic SAR flips in the direction of the 200-EMA. Needs 210 candles.",
     "spec": _single([{"id": "sar", "type": "psar", "step": 0.02, "maximum": 0.2}, {"id": "ema200", "type": "ema", "n": 200}],
                     "sar.dir == 1 and sar.dir[1] == -1 and close > ema200", "sar.dir == -1 and sar.dir[1] == 1 and close < ema200",
                     "SAR flip with trend", min_candles=210)},

    {"key": "adx-di-trend", "name": "ADX / DI Trend", "status": "paper", "tags": ["trend"],
     "description": "ADX above 25 (a real trend) and the +DI/-DI lines cross in the trend's direction.",
     "spec": _single([{"id": "adx14", "type": "adx", "n": 14}],
                     "adx14.adx > 25 and cross_above(adx14.pdi, adx14.mdi)", "adx14.adx > 25 and cross_above(adx14.mdi, adx14.pdi)", "DI cross in a trend")},

    {"key": "heikin-ashi-trend", "name": "Heikin-Ashi Continuation", "status": "paper", "tags": ["trend", "price action"],
     "description": "Two consecutive Heikin-Ashi candles of the same colour, the latest with no opposite wick (strong trend candle).",
     "spec": _single([{"id": "ha", "type": "heikin"}], "ha.green and ha.green[1] and ha.nolow", "ha.red and ha.red[1] and ha.nohigh", "Heikin-Ashi trend candle")},

    {"key": "engulfing-pinbar-sr", "name": "Engulfing / Pin Bar at Support-Resistance", "status": "paper", "tags": ["price action", "reversal"],
     "description": "Bullish engulfing or hammer near the 20-candle low (CALL), bearish engulfing or shooting star near the 20-candle high (PUT).",
     "spec": _single([{"id": "atr14", "type": "atr", "n": 14}],
                     "(bull_engulf or hammer) and low <= lowest(low, 20)[1] + 0.3 * atr14",
                     "(bear_engulf or star) and high >= highest(high, 20)[1] - 0.3 * atr14", "Reversal candle at level")},

    {"key": "donchian-breakout", "name": "Donchian Range Breakout", "status": "paper", "tags": ["breakout"],
     "description": "Close beyond the previous 20-candle high/low while volatility is not dead (ATR ratio >= 0.8).",
     "spec": _single([{"id": "don", "type": "donchian", "n": 20}], "close > don.upper[1]", "close < don.lower[1]", "20-candle breakout",
                     filters=[{"type": "atr_ratio_min", "value": 0.8}])},

    {"key": "bb-squeeze-breakout", "name": "Bollinger Squeeze Breakout", "status": "paper", "tags": ["breakout", "volatility"],
     "description": "Band width was in its lowest 20 % of the last 100 candles, then price closes outside a band.",
     "spec": _single([{"id": "bb", "type": "bollinger", "n": 20, "k": 2.0}],
                     "bb.pctile[1] <= 20 and close > bb.upper", "bb.pctile[1] <= 20 and close < bb.lower", "Squeeze breakout", min_candles=130)},

    {"key": "trend-pullback", "name": "Trend Pullback (3 confirmations)", "status": "paper", "tags": ["trend", "pullback"],
     "description": "EMA21 vs EMA50 gives the trend, RSI shows a pullback (below/above 50) and Stochastic turns back with the trend.",
     "spec": _single([{"id": "ema21", "type": "ema", "n": 21}, {"id": "ema50", "type": "ema", "n": 50}, {"id": "rsi14", "type": "rsi", "n": 14},
                      {"id": "st", "type": "stoch", "k": 14, "smooth": 3, "d": 3}],
                     "ema21 > ema50 and rsi14 < 50 and cross_above(st.k, st.d) and st.k < 50",
                     "ema21 < ema50 and rsi14 > 50 and cross_below(st.k, st.d) and st.k > 50", "Pullback turn")},

    {"key": "candle-streak-reversal", "name": "Candle Streak Reversal (hypothesis)", "status": "paper", "tags": ["hypothesis", "otc"],
     "description": "After 4 or more candles of the same colour bet on the opposite colour. A popular OTC idea - included to be tested, not assumed.",
     "spec": _single([], "streak <= -4", "streak >= 4", "Streak of 4+", filters=[])},

    {"key": "ai-momentum", "name": "AI MOMENTUM (auto - builds its own rule)", "status": "paper", "tags": ["ai", "momentum"],
     "description": "No preset rules. On every closed 1-minute candle the AI finds where the current momentum started, applies the rule it learned from history for that kind of "
                    "situation (tested on unseen candles) and signals follow / fade. Its signals are produced by app/momentum_ai.py - the placeholder rule below never fires. "
                    "Grade C = its own test found no edge: this is recorded to MEASURE it, not as a recommendation.",
     "spec": {**_single([], "0 > 1", "0 > 1", "AI-built rule (see app/momentum_ai.py)"), "timeframes": [60]}},

    {"key": "cover-reversal", "name": "Hammer / Star Cover Reversal", "status": "paper", "tags": ["price action", "reversal"],
     "description": "A shooting star (long upper wick, any colour) leaves a weak high; price runs up toward it - stopping short of it, or breaking it and closing above it again and again - "
                    "and then a decisive red candle closes back = PUT on that close. The mirror with a hammer (long lower wick) and its weak low = CALL. Before the signal, a 'get ready' alert "
                    "names the pair, the expected direction and the level as soon as price runs toward the level. Sizes are in ATR, so it fits any pair and timeframe.",
     "spec": _cover_reversal()},

    {"key": "consensus-3", "name": "Consensus (3 strategies agree)", "status": "paper", "tags": ["meta"],
     "description": "Signals only when at least 3 active strategies point the same way on the same candle and none disagree.",
     "spec": {"engine": "spec/1", "kind": "meta", "timeframes": ALL_TFS, "consensus": {"members": "all_active", "min_agree": 3}}},
]
