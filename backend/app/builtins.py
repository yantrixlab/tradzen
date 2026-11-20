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
    """Hammer / star, a candle that covers it, then a decisive candle back the other way (the signal is on the candle that closes it). Three conditions that must ALL hold on the
    candle that just closed (min_agree 3); everything is measured in ATR or as a share of the candle's own range, so it needs no per-pair number. [2] = the setup candle,
    [1] = the covering candle, no index = the trigger candle that just closed. LEVEL = the setup's extreme is within 0.25 ATR of the lowest low (highest high) of the last 10 candles."""
    ind = [{"id": "a", "type": "atr", "n": 14}]
    setup_call = "lower_wick[2] >= 0.4 * range[2] and lower_wick[2] >= upper_wick[2] and abs(body[2]) <= 0.4 * range[2] and range[2] >= 0.3 * a[2] and low[2] <= lowest(low, 10)[2] + 0.25 * a[2]"
    setup_put = "upper_wick[2] >= 0.4 * range[2] and upper_wick[2] >= lower_wick[2] and abs(body[2]) <= 0.4 * range[2] and range[2] >= 0.3 * a[2] and high[2] >= highest(high, 10)[2] - 0.25 * a[2]"
    cover = "min(high[1], high[2]) - max(low[1], low[2]) >= 0.5 * range[2]"
    trig_call = "green and body >= 0.5 * a and body >= abs(body[1]) and close > open[1]"
    trig_put = "red and body <= -0.5 * a and -body >= abs(body[1]) and close < open[1]"
    return {"engine": "spec/1", "kind": "factors", "timeframes": ALL_TFS, "indicators": ind,
            "factors": [
                {"id": "setup", "name": "Hammer at a bottom / star at a top", "weight": 34, "kind": "expr", "call": setup_call, "put": setup_put},
                {"id": "cover", "name": "Covered by a candle of the opposite colour (at least half the setup)", "weight": 33, "kind": "expr",
                 "call": "red[1] and " + cover, "put": "green[1] and " + cover},
                {"id": "trigger", "name": "Decisive candle back the other way, closing past the covering candle's open", "weight": 33, "kind": "expr",
                 "call": trig_call, "put": trig_put}],
            "gates": {"min_candles": 60, "min_agree": 3}, "filters": [], "expiry_candles": 1}


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
     "description": "A hammer (small body, long lower wick) at the bottom of the last 10 candles, a red candle covering at least half of it, then a decisive green candle closing above "
                    "the red candle's open = CALL on that close. The mirror (a star at the top, a green cover, a decisive red candle) = PUT. Sizes are in ATR, so it fits any pair and timeframe.",
     "spec": _cover_reversal()},

    {"key": "consensus-3", "name": "Consensus (3 strategies agree)", "status": "paper", "tags": ["meta"],
     "description": "Signals only when at least 3 active strategies point the same way on the same candle and none disagree.",
     "spec": {"engine": "spec/1", "kind": "meta", "timeframes": ALL_TFS, "consensus": {"members": "all_active", "min_agree": 3}}},
]
