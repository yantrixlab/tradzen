"""The "Hammer / Star Cover Reversal" built-in: a hammer (small body, long lower wick) at the bottom of the last 10 candles, a RED candle covering at least half of it, then a decisive
GREEN candle closing above the red candle's open = CALL on that close; the mirror (star at a top, green cover, decisive red candle) = PUT. The sequences below are the shapes of the
user's four screenshots. Run from backend/:  .venv\\Scripts\\python tests\\test_cover_reversal.py"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app import evaluator  # noqa: E402
from app.builtins import BUILTINS  # noqa: E402
from app.spec import validate_spec  # noqa: E402

SPEC = validate_spec(next(b for b in BUILTINS if b["key"] == "cover-reversal")["spec"])["spec"]
T0 = 1_700_000_040
PIVOT = 100.0


def row(i, o, h, l, c):
    return {"t": T0 + 60 * i, "o": o, "h": h, "l": l, "c": c, "n": 60}


def downtrend(n=60, start=130.0):
    """n red candles, each about 1.0 tall (so ATR is about 1) walking down to ~100.5."""
    rows, p = [], start
    step = (start - 100.5) / n
    for i in range(n):
        o = p
        c = p - step
        rows.append(row(i, o, o + 0.1, c - 0.1, c))
        p = c
    return rows


def with_pattern(setup, cover, trigger, base=None):
    rows = list(base or downtrend())
    for k, (o, h, l, c) in enumerate((setup, cover, trigger)):
        rows.append(row(len(rows), o, h, l, c))
    return rows


def mirror(rows):
    """The same candles flipped upside down (an up-trend into a top)."""
    return [{**r, "o": 2 * PIVOT * 1.4 - r["o"], "h": 2 * PIVOT * 1.4 - r["l"], "l": 2 * PIVOT * 1.4 - r["h"], "c": 2 * PIVOT * 1.4 - r["c"]} for r in rows]


# the user's shape: hammer (small green body, long lower wick) at the low, red candle covering it, big green candle closing above the red candle's open
SETUP = (100.2, 100.4, 99.4, 100.3)
COVER = (100.4, 100.5, 99.3, 99.9)
TRIGGER = (99.9, 101.0, 99.85, 100.9)


def call_of(rows):
    return evaluator.evaluate(SPEC, rows, None, {}).direction


def test_the_user_shape_is_a_call_and_the_mirror_is_a_put():
    rows = with_pattern(SETUP, COVER, TRIGGER)
    assert call_of(rows) == "CALL"
    assert call_of(mirror(rows)) == "PUT"


def test_a_red_hammer_and_a_doji_setup_count_too():
    red_hammer = (100.3, 100.4, 99.4, 100.2)                    # red body: the user's third picture has red small candles with long wicks
    assert call_of(with_pattern(red_hammer, COVER, TRIGGER)) == "CALL"
    doji = (100.25, 100.45, 99.45, 100.27)                      # a spinning top with both wicks, the lower longer
    assert call_of(with_pattern(doji, COVER, TRIGGER)) == "CALL"


def test_the_cover_may_be_partial():
    partial = (100.55, 100.6, 99.8, 100.0)                      # covers the upper half of the hammer's range only (overlap 0.6 of 1.0)
    assert call_of(with_pattern(SETUP, partial, TRIGGER)) == "CALL"
    barely = (100.7, 100.8, 100.3, 100.4)                       # touches under half of the hammer: not a cover
    assert call_of(with_pattern(SETUP, barely, (100.4, 101.4, 100.35, 101.3))) == "SKIP"


def test_the_trigger_may_close_below_the_hammers_high():
    tall_hammer = (100.2, 100.9, 99.4, 100.3)                   # a long upper wick too: its high is above the trigger's close
    trig = (99.9, 100.7, 99.85, 100.6)                          # closes above the red candle's open (100.4) but below the hammer's high (100.9)
    assert call_of(with_pattern(tall_hammer, COVER, trig)) == "CALL"


def test_every_missing_piece_means_no_signal():
    base = downtrend()
    assert call_of(with_pattern(SETUP, COVER, (99.9, 100.2, 99.85, 100.0))) == "SKIP", "a weak trigger (smaller than half an ATR) is not decisive"
    assert call_of(with_pattern(SETUP, COVER, (99.9, 100.5, 99.3, 99.4))) == "SKIP", "a red trigger is not a call"
    assert call_of(with_pattern(SETUP, COVER, (99.9, 100.45, 99.85, 100.35))) == "SKIP", "closing below the covering candle's open does not reclaim it"
    assert call_of(with_pattern(SETUP, (99.9, 100.5, 99.3, 100.4), TRIGGER)) == "SKIP", "a green cover candle is no cover"
    no_wick = (100.2, 100.45, 100.15, 100.4)                    # a plain candle, no long lower wick
    assert call_of(with_pattern(no_wick, COVER, TRIGGER)) == "SKIP"
    mid_range = list(base)
    mid_range[-8] = row(len(base) - 8, 99.0, 99.2, 97.9, 98.0)  # a lower low a few candles before the hammer: it is NOT at the bottom of the last 10 candles
    assert call_of(with_pattern(SETUP, COVER, TRIGGER, mid_range)) == "SKIP"


def test_it_fits_any_price_scale():
    scaled = [{**r, "o": r["o"] / 1000, "h": r["h"] / 1000, "l": r["l"] / 1000, "c": r["c"] / 1000} for r in with_pattern(SETUP, COVER, TRIGGER)]
    assert call_of(scaled) == "CALL", "nothing in the rule is a fixed price distance (a 0.0001-scale pair works like a 100-scale one)"
    big = [{**r, "o": r["o"] * 20, "h": r["h"] * 20, "l": r["l"] * 20, "c": r["c"] * 20} for r in with_pattern(SETUP, COVER, TRIGGER)]
    assert call_of(big) == "CALL"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all cover reversal tests passed")
