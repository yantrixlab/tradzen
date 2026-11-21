"""The "Hammer / Star Cover Reversal" built-in and its early alert.

A shooting star (long upper wick, any colour) leaves a WEAK HIGH, a hammer (long lower wick) a WEAK LOW. After it price runs toward that level and either
  A. stops short of it (or only wicks into it), or
  B. breaks it and keeps closing beyond it (2+ closes in a row),
and then a decisive candle back the other way closes past the previous candle's open = the signal (PUT after a star, CALL after a hammer).
One candle or more earlier, when the run is under way (within 1 ATR of the level, or already beyond it with 2+ closes) the strategy sends a "get ready" alert.

Run from backend/:  .venv\\Scripts\\python tests\\test_cover_reversal.py"""
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app import evaluator, vec, weaklevels  # noqa: E402
from app.builtins import BUILTINS  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.spec import validate_spec  # noqa: E402

SPEC = validate_spec(next(b for b in BUILTINS if b["key"] == "cover-reversal")["spec"])["spec"]
T0 = 1_700_000_040
PIVOT = 100.0


def row(i, o, h, l, c):
    return {"t": T0 + 60 * i, "o": o, "h": h, "l": l, "c": c, "n": 60}


def downtrend(n=60, start=130.0):
    """n red candles, each about 0.7 tall (so ATR is about 0.7) walking down to ~100.5."""
    rows, p = [], start
    step = (start - 100.5) / n
    for i in range(n):
        o = p
        c = p - step
        rows.append(row(i, o, o + 0.1, c - 0.1, c))
        p = c
    return rows


def seq(*candles, base=None):
    rows = list(base or downtrend())
    for o, h, l, c in candles:
        rows.append(row(len(rows), o, h, l, c))
    return rows


def mirror(rows):
    """The same candles flipped upside down (an up-trend into a top)."""
    return [{**r, "o": 2 * PIVOT * 1.4 - r["o"], "h": 2 * PIVOT * 1.4 - r["l"], "l": 2 * PIVOT * 1.4 - r["h"], "c": 2 * PIVOT * 1.4 - r["c"]} for r in rows]


HAMMER = (100.15, 100.4, 99.4, 100.3)                # small green body (0.15), lower wick 0.75 (5x the body), upper wick 0.1: the weak low is 99.4
SWEEP = (100.4, 100.5, 99.3, 99.9)                   # red candle that wicks 0.1 under the weak low and closes back above it
SHORT = (100.7, 100.8, 100.3, 100.4)                 # red candle that runs toward the weak low and stops 0.9 short of it
TRIGGER = (99.9, 101.0, 99.85, 100.9)                # decisive green candle (body 1.0) closing above the previous candle's open
# a break-through: three red candles in a row closing under the weak low, each lower than the last
B1, B2, B3 = (100.3, 100.35, 99.1, 99.2), (99.2, 99.3, 98.7, 98.8), (98.8, 98.9, 98.3, 98.4)
B_TRIGGER = (98.4, 99.5, 98.35, 99.4)                # decisive green candle closing above the third candle's open (98.8)


def call_of(rows):
    return evaluator.evaluate(SPEC, rows, None, {}).direction


def alerts_of(rows):
    return evaluator.heads_up(SPEC, rows, None)


# ------------------------------------------------------------------------------------------------ the weak-level tracker
def test_a_hammer_arms_a_weak_low_and_follows_price_toward_it():
    w = weaklevels.compute(evaluator.build_df(seq(HAMMER, SHORT)))
    k, k1 = len(w.lo_lvl) - 2, len(w.lo_lvl) - 1
    assert w.lo_lvl[k] == 99.4 and w.lo_new[k] == 1 and w.lo_run[k] == 0, "the hammer's low is the level, on the hammer's own candle"
    assert w.lo_lvl[k1] == 99.4 and w.lo_run[k1] == 1 and w.lo_touch[k1] == 0 and w.lo_closes[k1] == 0
    assert 1.0 < w.lo_dist[k1] < 1.8, "about 1.3 ATR above the level"
    assert np.isnan(w.hi_lvl[k1]), "a hammer says nothing about a weak high"
    w = weaklevels.compute(evaluator.build_df(seq(HAMMER, SWEEP)))
    assert w.lo_touch.iloc[-1] == 1 and w.lo_closes.iloc[-1] == 0, "a wick into the level is a touch, not a close beyond it"
    w = weaklevels.compute(evaluator.build_df(seq(HAMMER, B1, B2, B3)))
    assert [w.lo_closes.iloc[-3], w.lo_closes.iloc[-2], w.lo_closes.iloc[-1]] == [1, 2, 3], "closes beyond the level are counted in a row"
    assert w.lo_dist.iloc[-1] < 0, "negative = beyond the level"


def test_only_real_pin_bars_arm_a_level():
    """The setup must be a hammer / shooting star: a wick at least 1.5x the body, a clearly shorter wick on the other side, and no doji with long wicks on both sides."""
    def arms(c):
        w = weaklevels.compute(evaluator.build_df(seq(c, SWEEP)))
        return w.lo_lvl.iloc[-2] == c[2]
    assert arms(HAMMER) and arms((100.35, 100.4, 99.4, 100.2)), "green or red body, both fine"
    assert not arms((100.0, 100.4, 99.6, 100.3)), "lower wick 0.4 against a body of 0.3: only 1.33x, under the 1.5x rule"
    assert arms((100.0, 100.4, 99.5, 100.3)), "lower wick 0.5 against a body of 0.3: 1.67x, over the 1.5x rule"
    assert arms((99.72, 100.34, 98.99, 100.2)), "the user's candle: wick about 1.5x the body (0.73 against 0.48), a small wick (0.14) on the other side"
    assert not arms((100.2, 100.8, 99.4, 100.3)), "a long wick on both sides is a spinning top, not a pin bar"
    assert arms((100.25, 100.45, 99.45, 100.27)), "a tiny body with ONE long wick (gravestone / dragonfly style) is a real pin bar"
    assert not arms((100.25, 100.8, 99.7, 100.27)), "a doji with long wicks on BOTH sides is avoided"
    assert not arms((100.3, 100.4, 99.4, 100.3)), "a perfectly flat body (open = close) is still not counted"
    star = mirror(seq(HAMMER))[-1]
    w = weaklevels.compute(evaluator.build_df(mirror(seq(HAMMER, SWEEP))))
    assert w.hi_lvl.iloc[-2] == star["h"], "the same strictness on the star side"


def test_a_tiny_bodied_star_then_a_short_green_then_a_red_below_its_low_is_the_put():
    """The user's screenshot: a long upper wick on a tiny body (about 6 % of the range), a green that stays well under the star's high, then one red that closes under the star's low."""
    def up(n=60, end=99.9):
        rows, p = [], end - 0.5 * n * 0.5
        for i in range(n):
            o, c = p, p + 0.25
            rows.append(row(i, o, c + 0.1, o - 0.1, c))
            p = c
        return rows
    star, green = (100.06, 100.94, 100.0, 100.0), (100.0, 100.42, 99.95, 100.4)
    for red in ((100.45, 100.47, 99.0, 99.1), (100.42, 100.43, 99.9, 99.92)):          # a big red, and a small one: the size does not matter
        assert call_of(seq(star, green, red, base=up())) == "PUT"
    low = lambda r: weaklevels.compute(evaluator.build_df(seq(star, green, r, base=up()))).hi_lvl.iloc[-2]
    assert low((100.45, 100.47, 99.0, 99.1)) == 100.94, "the weak high is the star's high"
    mirrored = mirror(seq(star, green, (100.45, 100.47, 99.0, 99.1), base=up()))
    assert call_of(mirrored) == "CALL", "the same shape upside down is a hammer and a CALL"


def test_the_first_colour_change_is_the_signal_if_it_has_a_real_body():
    run, back1 = (100.5, 100.55, 99.9, 100.0), (100.0, 100.2, 99.95, 100.15)
    assert call_of(seq(HAMMER, run)) == "SKIP", "the run alone is not a signal"
    assert call_of(seq(HAMMER, run, back1)) == "CALL", "the first green after the red run, closing above the previous close: no second candle needed"
    assert call_of(mirror(seq(HAMMER, run, back1))) == "PUT"
    assert call_of(seq(HAMMER, run, (100.0, 100.3, 99.7, 100.01))) == "SKIP", "a flat candle never triggers"
    assert call_of(seq(HAMMER, run, (100.0, 100.3, 99.7, 100.05))) == "SKIP", "a body under 30 % of the range is not a real body"
    assert call_of(seq(HAMMER, run, (99.7, 99.98, 99.65, 99.95))) == "SKIP", "it must close beyond the previous candle's close"
    assert call_of(seq(HAMMER, run, back1, (100.15, 100.45, 100.1, 100.35))) == "SKIP", "one signal per setup: the second green is not another one"
    assert call_of(seq(HAMMER, run, (100.0, 100.3, 99.7, 100.01), (100.01, 100.4, 100.0, 100.35))) == "CALL", "a flat candle between the run and the turn is ignored: the first REAL candle back is the signal"


def test_the_users_hammer_case_two_reds_then_the_first_green():
    red1, red2 = (100.7, 100.75, 100.45, 100.5), (100.5, 100.55, 100.4, 100.45)      # two red candles that try to push down and stop short of the weak low
    g1 = (100.45, 100.6, 100.43, 100.55)
    assert call_of(seq(HAMMER, red1, red2)) == "SKIP"
    assert call_of(seq(HAMMER, red1, red2, g1)) == "CALL", "the first green after the red run is the CALL"
    assert call_of(mirror(seq(HAMMER, red1, red2, g1))) == "PUT"


def test_the_users_top_a_star_two_greens_over_its_high_a_doji_then_the_first_red():
    """Picture: a shooting star (tiny green body, long upper wick), a tall green and a doji with a long top wick both above the star's high, then a big red: the PUT is on that first red."""
    def up(n=60, end=99.0):
        rows, p = [], end - 0.25 * n
        for i in range(n):
            o, c = p, p + 0.25
            rows.append(row(i, o, c + 0.1, o - 0.1, c))
            p = c
        return rows
    star, g1 = (100.0, 100.74, 100.0, 100.08), (100.1, 100.9, 100.05, 100.85)
    red1, red2 = (100.88, 100.9, 100.0, 100.1), (100.1, 100.15, 99.6, 99.7)
    for name, doji in (("flat", (100.85, 101.2, 100.84, 100.85)), ("tiny red", (100.86, 101.2, 100.84, 100.85)), ("tiny green", (100.85, 101.2, 100.84, 100.86))):
        assert call_of(seq(star, g1, doji, red1, base=up())) == "PUT", name + " doji before the first red"
        assert call_of(seq(star, g1, doji, red1, red2, base=up())) == "SKIP", "one signal per setup: the second red is the entry candle, not another signal"
    assert call_of(seq(star, g1, red1, base=up())) == "PUT"


def test_the_setup_ends_when_it_is_overtaken_or_goes_stale():
    up_first = weaklevels.compute(evaluator.build_df(seq(HAMMER, (100.3, 101.2, 100.25, 101.1))))
    assert up_first.lo_lvl.iloc[-1] == 99.4, "a push up before any run toward the level does not cancel the weak low: it is still a weak low"
    turn = weaklevels.compute(evaluator.build_df(seq(HAMMER, SHORT, (100.4, 101.4, 100.35, 101.3), (101.3, 101.4, 101.0, 101.1))))
    assert turn.lo_lvl.iloc[-2] == 99.4 and np.isnan(turn.lo_lvl.iloc[-1]), "the turn candle is the last one the setup reports; after it the setup is spent"
    stale = weaklevels.compute(evaluator.build_df(seq(HAMMER, *[(100.4, 100.55, 100.3, 100.5)] * 12)))
    assert np.isnan(stale.lo_lvl.iloc[-1]), "not followed up within max_wait candles"


def test_colour_and_location_do_not_matter_only_a_real_wick_counts():
    red_hammer = (100.35, 100.4, 99.4, 100.2)
    assert call_of(seq(red_hammer, SWEEP, TRIGGER)) == "CALL"
    plain = (100.2, 100.45, 100.15, 100.4)               # no long wick
    assert call_of(seq(plain, SWEEP, TRIGGER)) == "SKIP"
    base = downtrend()
    base[-8] = row(len(base) - 8, 99.0, 99.2, 97.9, 98.0)   # a much lower low a few candles earlier: the hammer is NOT at the bottom
    assert call_of(seq(HAMMER, SWEEP, TRIGGER, base=base)) == "CALL", "where it forms does not matter: a pin bar on a pullback inside a swing still counts (the user's UKBrent_otc case)"
    near = weaklevels.compute(evaluator.build_df(seq(HAMMER, SWEEP, base=base)), loc_atr=0.25)
    assert np.isnan(near.lo_lvl.iloc[-2]), "the old 'only at an extreme' rule is still there as a parameter (loc_atr)"


# ------------------------------------------------------------------------------------------------ the signal
def test_a_run_that_stops_short_or_wicks_into_the_level_then_a_decisive_candle_is_a_signal():
    assert call_of(seq(HAMMER, SWEEP, TRIGGER)) == "CALL", "the user's first picture: wicks under the weak low, closes back, decisive green candle"
    assert call_of(seq(HAMMER, SHORT, (100.4, 101.4, 100.35, 101.3))) == "CALL", "stopped 0.9 short of the level: the green candle does not have to reach anything"
    assert call_of(mirror(seq(HAMMER, SHORT, (100.4, 101.4, 100.35, 101.3)))) == "PUT", "the shooting star mirror"
    assert call_of(mirror(seq(HAMMER, SWEEP, TRIGGER))) == "PUT"
    two = seq(HAMMER, SHORT, (100.4, 100.45, 100.2, 100.25), TRIGGER)   # a second small red candle in the run
    assert call_of(two) == "CALL"


def test_a_break_through_that_keeps_closing_beyond_then_the_first_candle_back_is_a_signal():
    assert call_of(seq(HAMMER, B1, B2, B3, B_TRIGGER)) == "CALL", "three closes under the weak low, then the first green"
    assert call_of(mirror(seq(HAMMER, B1, B2, B3, B_TRIGGER))) == "PUT"
    assert call_of(seq(HAMMER, B1, B2, (98.8, 98.85, 98.65, 98.7), (98.7, 99.8, 98.65, 99.7))) == "CALL"
    assert call_of(seq(HAMMER, B1, (99.2, 100.3, 99.15, 100.2))) == "CALL", "one close beyond the level, then green: the first candle back is still the signal"


def test_every_missing_piece_means_no_signal():
    assert call_of(seq(HAMMER, SWEEP, (99.9, 100.2, 99.85, 100.0))) == "SKIP", "a weak trigger (body under 30 % of its range) is not a signal"
    assert call_of(seq(HAMMER, SWEEP, (99.9, 100.5, 99.3, 99.4))) == "SKIP", "a red trigger is not a call"
    assert call_of(seq(HAMMER, SWEEP, (99.3, 99.88, 99.25, 99.85))) == "SKIP", "a green that closes under the previous close is no turn"
    assert call_of(seq(HAMMER, (99.9, 100.5, 99.3, 100.4), TRIGGER)) == "SKIP", "a green candle after a hammer is not a run toward its low"
    assert call_of(seq(HAMMER, TRIGGER)) == "SKIP", "no run candle between the hammer and the decisive candle"
    assert call_of(seq(HAMMER, *[(100.4, 100.55, 100.3, 100.5)] * 12, TRIGGER)) == "SKIP", "the hammer is stale"


def test_it_fits_any_price_scale():
    for k in (1 / 1000, 20):
        rows = [{**r, "o": r["o"] * k, "h": r["h"] * k, "l": r["l"] * k, "c": r["c"] * k} for r in seq(HAMMER, SWEEP, TRIGGER)]
        assert call_of(rows) == "CALL", "nothing in the rule is a fixed price distance"


def test_live_and_backtest_agree_on_every_candle():
    rows = seq(HAMMER, SHORT, (100.4, 101.4, 100.35, 101.3), (101.3, 101.4, 99.9, 100.0), HAMMER, B1, B2, B3, B_TRIGGER)
    rows += mirror(seq(HAMMER, SWEEP, TRIGGER))[-3:]
    df = evaluator.build_df(rows)
    res = vec.evaluate_series(SPEC, df)
    for i in range(SPEC["gates"]["min_candles"], len(rows)):
        live = evaluator.evaluate(SPEC, rows[:i + 1], None, {}).direction
        v = int(res["dir"].iloc[i])
        assert (v == 1 and live == "CALL") or (v == -1 and live == "PUT") or (v == 0 and live == "SKIP"), (i, live, v)
    assert (res["dir"] != 0).any(), "the sequence does contain a signal"


# ------------------------------------------------------------------------------------------------ the early alert
def test_the_alert_comes_when_price_runs_toward_the_level_not_before():
    assert alerts_of(seq(HAMMER)) == [], "only the hammer so far: nothing to warn about yet"
    assert alerts_of(seq(HAMMER, SHORT)) == [], "a red candle toward the weak low that is still about 1.3 ATR away: too early"
    far = alerts_of(seq(HAMMER, (100.45, 100.5, 100.3, 100.35)))
    assert far == [], "still more than one ATR from the level: too early"
    near = alerts_of(seq(HAMMER, (100.3, 100.35, 99.9, 100.0)))
    assert len(near) == 1 and near[0]["direction"] == "CALL" and near[0]["level"] == 99.4
    assert "hammer" in near[0]["text"] and "99.4" in near[0]["text"] and "CALL" in near[0]["text"] and "{" not in near[0]["text"], "the placeholders are filled"
    pm = alerts_of(mirror(seq(HAMMER, (100.3, 100.35, 99.9, 100.0))))
    assert len(pm) == 1 and pm[0]["direction"] == "PUT" and "shooting star" in pm[0]["text"]


def test_the_alert_also_covers_a_break_through_but_only_after_two_closes():
    assert not [x for x in alerts_of(seq(HAMMER, B1)) if x["direction"] == "CALL"], "one close beyond the level is not yet a break-through"
    two = alerts_of(seq(HAMMER, B1, B2))
    assert [x["direction"] for x in two] == ["CALL"] and two[0]["level"] == 99.4


def test_no_alert_for_a_candle_that_ends_the_setup_or_runs_the_wrong_way():
    assert alerts_of(seq(HAMMER, (99.9, 100.5, 99.85, 100.45))) == [], "a green candle after a hammer is not a run toward the low"
    assert alerts_of(seq(HAMMER, (100.3, 101.2, 100.25, 101.1))) == [], "overtaken: no setup left to warn about"


# ------------------------------------------------------------------------------------------------ break, bounce (the user's two screenshots)
PAUSE = [(100.35, 100.5, 100.2, 100.3), (100.3, 100.45, 100.15, 100.35), (100.35, 100.45, 100.2, 100.25)]   # crosses that stay above the weak low
BIG_RED = (100.25, 100.3, 98.7, 98.9)                # the break of structure: closes under the weak low 99.4
G1 = (98.9, 99.3, 98.8, 99.15)                       # first green: closes under the level, body half the range


def test_red_break_then_the_first_green_is_the_call():
    assert call_of(seq(HAMMER, *PAUSE, BIG_RED)) == "SKIP"
    assert call_of(seq(HAMMER, *PAUSE, BIG_RED, G1)) == "CALL", "screenshot 1: break below the weak low, then the first green"
    assert call_of(mirror(seq(HAMMER, *PAUSE, BIG_RED, G1))) == "PUT", "and the mirror"
    r1, r2 = (100.25, 100.3, 99.2, 99.3), (99.3, 99.35, 98.5, 98.6)       # screenshot 2: two red candles close under the level, then green
    assert call_of(seq(HAMMER, *PAUSE, r1, r2, (98.6, 99.2, 98.55, 99.1))) == "CALL"
    assert call_of(seq(HAMMER, *PAUSE, BIG_RED, (98.9, 98.95, 98.4, 98.5), G1)) == "CALL", "more red candles after the break are fine: it is the first green after the LAST red"
    assert call_of(seq(HAMMER, *PAUSE, BIG_RED, G1, (99.15, 99.9, 99.1, 99.8))) == "SKIP", "one signal per setup"


def test_the_tracker_remembers_the_break_and_the_turn():
    w = weaklevels.compute(evaluator.build_df(seq(HAMMER, *PAUSE, BIG_RED, G1)))
    assert list(w.lo_broke.iloc[-2:]) == [1, 1] and list(w.lo_closes.iloc[-2:]) == [1, 2], "closes counts the candles in a row beyond the level, broke stays 1"
    assert list(w.lo_ret.iloc[-2:]) == [0, 1] and list(w.lo_ran.iloc[-2:]) == [1, 1], "the first colour change after the run is ret = 1"
    assert w.lo_ext.iloc[-1] == 100.4, "the far end of a hammer is its high"
    w2 = weaklevels.compute(evaluator.build_df(seq(HAMMER, *PAUSE, BIG_RED, G1, (99.15, 99.9, 99.1, 99.8))))
    assert np.isnan(w2.lo_lvl.iloc[-1]), "the setup is spent after the turn candle"
    assert w.lo_broke.iloc[-4] == 0 and w.lo_back.iloc[-4] == 0


def test_no_alert_on_the_turn_candle_itself():
    assert alerts_of(seq(HAMMER, *PAUSE, BIG_RED, G1)) == [], "the candle that turns gives the signal, not a warning"


# ------------------------------------------------------------------------------------------------ the engine sends it, once, only when live
def _engine(status="live"):
    eng = Engine(":memory:", seed_builtins=True)
    sid = next(s["id"] for s in eng.repo.list() if s["key"] == "cover-reversal")
    eng.set_strategy_status(sid, status)
    return eng, sid


def test_the_engine_sends_one_get_ready_per_setup_and_only_for_a_live_strategy():
    rows = seq(HAMMER, (100.3, 100.35, 99.9, 100.0))
    eng, sid = _engine()
    eng.instruments["T_otc"] = {"name": "Test pair (OTC)", "market": "OTC", "payout": 85}
    defs = [eng.strategies[sid]]
    eng._heads_up(defs, {}, "T_otc", 60, rows[-1], rows)
    evs = [e for e in eng.notify_log if e["kind"] == "headsup"]
    assert len(evs) == 1
    e = evs[0]
    assert e["direction"] == "CALL" and e["symbol"] == "T_otc" and e["strategy_id"] == sid and e["lvl"] == 99.4 and e["sound"] == "shead" and e["level"] == "warn"
    assert "Test pair (OTC)" in e["title"] and "CALL" in e["title"] and "hammer" in e["message"] and "not a signal" in e["message"]
    assert {"Pair", "Expect", "Level"} <= {c["l"] for c in e["chips"]} and e["entry_t"] == rows[-1]["t"] + 60 and e.get("aud") is not None
    rows2 = rows + [row(len(rows), 100.0, 100.05, 99.7, 99.75)]       # the next candle, same setup, same level
    eng._heads_up(defs, {}, "T_otc", 60, rows2[-1], rows2)
    assert len([x for x in eng.notify_log if x["kind"] == "headsup"]) == 1, "the same setup does not warn twice"
    other = seq((100.15, 100.4, 99.2, 100.3), (100.3, 100.35, 99.8, 99.9))      # a new hammer, a new level
    eng._heads_up(defs, {}, "T_otc", 60, other[-1], other)
    assert len([x for x in eng.notify_log if x["kind"] == "headsup"]) == 2, "a new level warns again"
    paper, psid = _engine("paper")
    paper._heads_up([paper.strategies[psid]], {}, "T_otc", 60, rows[-1], rows)
    assert not [x for x in paper.notify_log if x["kind"] == "headsup"], "a paper strategy never alerts"
    off, osid = _engine()
    off.alerts_enabled = False
    off._heads_up([off.strategies[osid]], {}, "T_otc", 60, rows[-1], rows)
    assert not [x for x in off.notify_log if x["kind"] == "headsup"], "the master alert switch silences it too"


def test_a_signal_this_candle_replaces_the_warning():
    rows = seq(HAMMER, SWEEP, TRIGGER)
    eng, sid = _engine()
    res = evaluator.evaluate(SPEC, rows, None, {})
    assert res.direction == "CALL"
    eng._heads_up([eng.strategies[sid]], {"cover-reversal": (eng.strategies[sid], res)}, "T_otc", 60, rows[-1], rows)
    assert not [x for x in eng.notify_log if x["kind"] == "headsup"]


# ------------------------------------------------------------------------------------------------ the spec accepts / rejects heads_up
def test_the_spec_checks_the_heads_up_block():
    base = next(b for b in BUILTINS if b["key"] == "cover-reversal")["spec"]
    assert validate_spec(base)["ok"]
    for bad in ({"put": "nothing.here > 1"}, {"put": "wk.hi_lvl > 0", "text": {"put": "level {wk.nope}"}}, {"put": "wk.hi_lvl > 0", "level": {"put": "wk"}}, {"title": "x"}, {"put": "close > 1", "bogus": 1}):
        r = validate_spec({**base, "heads_up": bad})
        assert not r["ok"], bad
    r = validate_spec({**base, "heads_up": None})
    assert r["ok"] and "heads_up" not in r["spec"]


def test_the_example_json_is_the_builtin():
    import json
    raw = json.load(open(HERE.parent.parent / "examples" / "cover-reversal.json", encoding="utf-8"))
    r = validate_spec(raw)
    assert r["ok"], r["errors"]
    assert {**r["spec"], "scope": None} == {**SPEC, "scope": None} or r["spec"] == SPEC, "examples/cover-reversal.json must be what the built-in strategy runs"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all cover reversal tests passed")
