"""Sentiment collection + study: cadence, coverage, no look-ahead, planted relationship vs noise.
Run from backend/:  .venv\\Scripts\\python tests\\test_sentiment.py"""
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from app import engine as engine_mod  # noqa: E402
from app import sentiment  # noqa: E402
from app.engine import Engine  # noqa: E402

T0 = 1_700_000_040


def fill(eng: Engine, sym: str, n: int, related: float | None, seed: int = 1, tf: int = 60):
    """n candles; a sentiment reading 10 s before each opens. related=p: candle closes up with prob p when sentiment >= 50, 1-p otherwise."""
    rnd = random.Random(seed)
    candles, sents = [], []
    for i in range(n):
        t = T0 + i * tf
        pct = rnd.uniform(0, 100)
        up = (rnd.random() < related) == (pct >= 50) if related is not None else rnd.random() < 0.5
        o = 1.0 + i * 1e-5
        c = o + (1e-4 if up else -1e-4)
        candles.append((sym, tf, t, o, max(o, c), min(o, c), c, 10, "test"))
        sents.append((sym, t - 10, pct))
    eng.db.executemany("INSERT OR REPLACE INTO candles (symbol, tf, ts, o, h, l, c, ticks, source) VALUES (?,?,?,?,?,?,?,?,?)", candles)
    eng.db.executemany("INSERT OR REPLACE INTO sentiment (symbol, ts, pct) VALUES (?,?,?)", sents)
    eng.db.commit()


def test_cadence_stores_at_most_one_reading_per_interval():
    eng = Engine(":memory:", seed_builtins=False)
    clock = [1_000_000.0]
    real = engine_mod.time.time
    engine_mod.time.time = lambda: clock[0]
    try:
        for step in range(13):                                                  # a reading every 5 s for a minute
            clock[0] = 1_000_000.0 + step * 5
            eng.ingest({"items": [{"k": "s", "d": [["AAA_otc", 40 + step], ["BBB_otc", 60]]}]})
        eng._flush(force=True)
    finally:
        engine_mod.time.time = real
    n = eng.db.execute("SELECT COUNT(*) FROM sentiment WHERE symbol='AAA_otc'").fetchone()[0]
    assert n == 4                                                              # t=0, 20, 40, 60  (every SENTIMENT_EVERY seconds)
    assert eng.sentiment["AAA_otc"]["pct"] == 52 and engine_mod.SENTIMENT_EVERY == 20.0   # the live view always holds the newest value


def test_planted_relationship_is_found_and_noise_is_not():
    eng = Engine(":memory:", seed_builtins=False)
    for i, sym in enumerate(("A_otc", "B_otc", "C_otc")):
        fill(eng, sym, 3000, 0.62, seed=10 + i)
    s = sentiment.study(eng.db)
    assert s["matched"] == 9000 and s["pairs"] == 3 and len(s["buckets"]) == 5
    low, high = s["buckets"][0], s["buckets"][-1]
    assert low["up_rate"] < 0.42 and high["up_rate"] > 0.58 and abs(low["z"]) > 5 and abs(high["z"]) > 5
    assert low["stable"] and high["stable"] and s["edge_found"] is True

    noise = Engine(":memory:", seed_builtins=False)
    for i, sym in enumerate(("A_otc", "B_otc", "C_otc")):
        fill(noise, sym, 3000, None, seed=20 + i)
    ns = sentiment.study(noise.db)
    assert ns["edge_found"] is False and all(abs(b["z"]) < 3 for b in ns["buckets"])


def test_thin_data_never_claims_an_edge():
    eng = Engine(":memory:", seed_builtins=False)
    fill(eng, "A_otc", 300, 0.9)                                               # a strong effect, but only ~60 candles per range
    s = sentiment.study(eng.db)
    assert s["edge_found"] is False and all(not b["enough"] for b in s["buckets"]) and "fewer than" in s["note"]


def test_no_look_ahead():
    eng = Engine(":memory:", seed_builtins=False)
    eng.db.execute("INSERT INTO candles (symbol, tf, ts, o, h, l, c, ticks, source) VALUES ('X_otc', 60, ?, 1.0, 1.1, 0.9, 1.05, 5, 't')", (T0,))
    eng.db.execute("INSERT INTO sentiment VALUES ('X_otc', ?, 90.0)", (T0 + 5,))       # arrives AFTER the candle opened: must not count
    eng.db.commit()
    assert sentiment.study(eng.db)["matched"] == 0
    eng.db.execute("INSERT INTO sentiment VALUES ('X_otc', ?, 10.0)", (T0 - 40,))     # 40 s before: older than the 30 s tolerance
    eng.db.commit()
    assert sentiment.study(eng.db)["matched"] == 0
    eng.db.execute("INSERT INTO sentiment VALUES ('X_otc', ?, 20.0)", (T0 - 12,))     # 12 s before: usable, and it is the newest before the open
    eng.db.commit()
    s = sentiment.study(eng.db)
    assert s["matched"] == 1 and s["buckets"][1]["n"] == 1                              # the 20 % reading, not the later 90 % one


def test_payout_filter_and_empty_states():
    eng = Engine(":memory:", seed_builtins=False)
    assert sentiment.study(eng.db)["note"] == "No sentiment stored yet."
    fill(eng, "GOOD_otc", 500, None)
    fill(eng, "POOR_otc", 500, None, seed=3)
    pay = {"GOOD_otc": 90.0, "POOR_otc": 50.0}
    assert sentiment.study(eng.db, payouts=pay, min_payout=80)["pairs"] == 1
    assert sentiment.study(eng.db, payouts=pay)["pairs"] == 2
    only_sent = Engine(":memory:", seed_builtins=False)
    only_sent.db.execute("INSERT INTO sentiment VALUES ('Z_otc', ?, 50)", (T0,))
    only_sent.db.commit()
    assert "none lines up" in sentiment.study(only_sent.db)["note"]


def test_coverage():
    eng = Engine(":memory:", seed_builtins=False)
    now = time.time()
    eng.instruments["LIVE_otc"] = {"symbol": "LIVE_otc", "name": "Live", "payout": 90, "open": True, "market": "OTC", "category": ""}
    eng.instruments["QUIET_otc"] = {"symbol": "QUIET_otc", "name": "Quiet", "payout": 85, "open": True, "market": "OTC", "category": ""}
    eng.instruments["NONE_otc"] = {"symbol": "NONE_otc", "name": "None", "payout": 85, "open": True, "market": "OTC", "category": ""}
    eng.db.executemany("INSERT INTO sentiment VALUES (?,?,?)", [("LIVE_otc", int(now - k * 20), 50.0) for k in range(5)] + [("QUIET_otc", int(now - 3600), 40.0)])
    eng.db.commit()
    c = sentiment.coverage(eng.db, now, eng.instruments, 600)
    assert c["pairs_total"] == 3 and c["pairs_with_data"] == 2 and c["pairs_receiving"] == 1 and c["total_samples"] == 6
    by = {p["symbol"]: p for p in c["pairs"]}
    assert by["LIVE_otc"]["receiving"] and by["LIVE_otc"]["recent"] == 5 and not by["QUIET_otc"]["receiving"] and by["QUIET_otc"]["recent"] == 0
    assert by["NONE_otc"]["samples"] == 0 and by["NONE_otc"]["age_s"] is None and c["pairs"][0]["symbol"] == "LIVE_otc"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
