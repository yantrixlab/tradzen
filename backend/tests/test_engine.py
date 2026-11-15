"""Offline tests (no network). Run from backend/:  .venv\\Scripts\\python tests\\test_engine.py"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from app import indicators as ind  # noqa: E402
from app import strategy  # noqa: E402
from app.candles import aggregate, infer_tf, rows_from_history  # noqa: E402
from app.engine import Engine  # noqa: E402


def walk(n, seed, start=1.2, step=0.0004, t0=1_700_000_040, tf=60):
    rnd = random.Random(seed)
    rows, price = [], start
    for i in range(n):
        o = price
        pts = [o + rnd.gauss(0, step) for _ in range(12)]
        c = pts[-1]
        rows.append({"t": t0 + i * tf, "o": o, "h": max(pts + [o]), "l": min(pts + [o]), "c": c, "n": 12})
        price = c
    return rows


def mirror(rows):
    m = 2.0
    return [{"t": r["t"], "o": m - r["o"], "h": m - r["l"], "l": m - r["h"], "c": m - r["c"], "n": r["n"]} for r in rows]


def test_indicators():
    import pandas as pd
    up = pd.Series(np.linspace(1, 2, 60))
    assert ind.rsi(up).iloc[-1] > 99
    lo, mid, hi = ind.bollinger(pd.Series(np.random.default_rng(1).normal(100, 1, 80)))
    assert (hi.iloc[-1] > mid.iloc[-1] > lo.iloc[-1])


def test_strategy_symmetry():
    for seed in range(25):
        rows = walk(150, seed)
        a, b = strategy.evaluate(rows), strategy.evaluate(mirror(rows))
        assert abs(a.score + b.score) < 1e-6, (seed, a.score, b.score)
        flip = {"CALL": "PUT", "PUT": "CALL", "SKIP": "SKIP"}
        assert flip[a.direction] == b.direction, (seed, a.direction, b.direction)


def test_strategy_warmup_and_gates():
    r = strategy.evaluate(walk(30, 1))
    assert r.direction == "SKIP" and "warming" in r.reason
    seen = {strategy.evaluate(walk(150, s)).direction for s in range(60)}
    assert "SKIP" in seen and ("CALL" in seen or "PUT" in seen), seen


def test_history_helpers():
    raw = [[1000 + 60 * i, 1.0, 1.1, 1.2, 0.9, 5, 1000 + 60 * i + 59] for i in range(10)]
    rows = rows_from_history(raw)
    assert rows[0]["o"] == 1.0 and rows[0]["c"] == 1.1 and rows[0]["h"] == 1.2 and rows[0]["l"] == 0.9  # o, CLOSE, high, low
    assert infer_tf([r["t"] for r in rows]) == 60
    base = 1_700_000_100 // 300 * 300
    one = [{"t": base + 60 * i, "o": 1 + i, "h": 2 + i, "l": 0 + i, "c": 1.5 + i, "n": 1} for i in range(12)]
    agg = aggregate(one, 60, 300, ref_now=base + 10_000)
    assert len(agg) == 2 and agg[0]["o"] == 1 and agg[0]["c"] == 1.5 + 4 and agg[0]["h"] == 6 and agg[0]["l"] == 0


def feed_ticks(eng, sym, start_ts, n, seed=7, dt=5.0, price=1.2):
    rnd = random.Random(seed)
    ts = start_ts
    for _ in range(n):
        price += rnd.gauss(0, 0.0003)
        eng.ingest({"items": [{"k": "t", "d": [[sym, ts, price, 0]]}]})
        ts += dt
    return ts


def test_engine_end_to_end():
    eng = Engine(":memory:")
    sym = "EURUSD_otc"
    t0 = 1_700_000_040
    hist = walk(120, 3, t0=t0 - 120 * 60)
    raw = [[r["t"], r["o"], r["c"], r["h"], r["l"], r["n"], r["t"] + 59.0] for r in hist]
    lastc = hist[-1]["c"]
    raw.append([t0, lastc, lastc, lastc, lastc, 3, t0 + 3.0])         # the candle still forming when the page loaded
    eng.ingest({"items": [{"k": "i", "list": [[sym, "EUR/USD (OTC)", "currency", 85, True]]},
                          {"k": "h", "asset": sym, "rows": raw}]})
    assert len(eng.series[(sym, 60)].rows) == 120
    assert len(eng.series[(sym, 300)].rows) >= 20 and (sym, 900) in eng.series      # derived by aggregation
    feed_ticks(eng, sym, t0 + 4, 12 * 90)                                            # 90 minutes of 5 s ticks
    st = eng.state(sym, 60)
    assert st["selected"]["symbol"] == sym and st["selected"]["analysis"]["candles"] >= 100
    sigs = [s for s in eng.signals if s["symbol"] == sym and s["tf"] == 60]
    assert len(sigs) >= 80, len(sigs)
    keys = [(s["symbol"], s["tf"], s["basis_t"]) for s in eng.signals]
    assert len(keys) == len(set(keys)), "duplicate signals"
    settled = [s for s in eng.signals if s["status"] in ("win", "loss", "draw")]
    assert settled, "nothing settled"
    for s in settled:
        row = eng.series[(s["symbol"], s["tf"])].rows[s["target_t"]]
        up = row["c"] > row["o"]
        exp = "draw" if row["c"] == row["o"] else "win" if (s["direction"] == "CALL") == up else "loss"
        assert s["status"] == exp, (s["status"], exp)
    stats = eng.stats()
    assert stats["total"] == stats["calls"] + stats["puts"] and stats["wins"] + stats["losses"] == stats["settled"]
    assert stats["breakeven"] and abs(stats["breakeven"] - 1 / 1.85) < 1e-9


def test_tick_outage_discards_incomplete_candles():
    eng = Engine(":memory:")
    b0 = 1_700_000_040
    ts = b0 + 0.5
    while ts <= b0 + 130:                       # minutes 0 and 1 complete, minute 2 cut off at +10 s
        eng.on_tick("X_otc", ts, 1.0 + (ts - b0) * 1e-5)
        ts += 1.0
    ts = b0 + 6 * 60 + 5                        # resume 4 minutes later, 5 s into minute 6
    while ts <= b0 + 9 * 60:
        eng.on_tick("X_otc", ts, 1.2)
        ts += 1.0
    have = set(eng.series[("X_otc", 60)].rows)
    assert b0 in have and b0 + 60 in have, have                        # complete candles kept
    assert b0 + 120 not in have, "stale half-built candle must be discarded"
    assert b0 + 360 not in have, "first candle after an outage began mid-candle"
    assert b0 + 420 in have and b0 + 480 in have, have                 # normal again
    gap = eng.series[("X_otc", 60)].last_gap(60)
    assert gap and gap["after"] == b0 + 60 and gap["before"] == b0 + 420 and gap["missing"] == 5, gap


def test_stale_seeded_forming_candle_is_discarded():
    eng = Engine(":memory:")
    t = 1_700_000_040
    rows = [[t - 60 * i, 1.0, 1.0, 1.01, 0.99, 20, t - 60 * i + 59.0] for i in range(10, 0, -1)]
    rows.append([t, 1.0, 1.0, 1.0, 1.0, 3, t + 4.0])             # forming at page load, last tick at +4 s
    eng.ingest({"items": [{"k": "h", "asset": "Y_otc", "rows": rows}]})
    eng.on_tick("Y_otc", t + 300, 1.1)                           # ticks only resume 5 minutes later
    assert t not in eng.series[("Y_otc", 60)].rows, "seeded forming candle with a missing tail must not be stored"


def test_bridged_tail():
    from app.candles import Series
    tf, t0 = 60, 1_700_000_040

    def build(missing):
        s = Series()
        for i in range(100):
            if i not in missing:
                s.put({"t": t0 + i * tf, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.0, "n": 5})
        return s

    rows, syn = build({50, 51}).bridged_tail(tf)
    assert len(rows) == 100 and sum(1 for r in rows if r.get("syn")) == 2 and not syn
    assert [r["t"] for r in rows] == sorted(r["t"] for r in rows)
    assert build({96, 97}).bridged_tail(tf)[1] is True                    # synthetic candle among the last 8
    rows, syn = build(set(range(40, 50))).bridged_tail(tf)                # 10 missing: too big, run restarts after it
    assert len(rows) == 50 and not syn and rows[0]["t"] == t0 + 50 * tf


def test_signal_tiers():
    seen = set()
    for seed in range(120):
        r = strategy.evaluate(walk(150, seed))
        seen.add(r.tier)
        assert (r.direction == "SKIP") == (r.tier == ""), (r.direction, r.tier)
        if r.tier == "A":
            assert r.agree >= 3 and r.strength >= 40
        if r.tier == "B":
            assert r.agree >= 2 and r.strength >= 30
    assert "B" in seen, seen


def test_short_gap_is_bridged_and_signals_pause():
    eng = Engine(":memory:")
    sym, t0 = "G_otc", 1_700_000_040
    hist = walk(120, 11, t0=t0 - 120 * 60)
    raw = [[r["t"], r["o"], r["c"], r["h"], r["l"], r["n"], r["t"] + 59.0] for r in hist]
    raw.append([t0, hist[-1]["c"], hist[-1]["c"], hist[-1]["c"], hist[-1]["c"], 3, t0 + 3.0])
    eng.ingest({"items": [{"k": "h", "asset": sym, "rows": raw}]})
    price, ts = 1.2, t0 + 4.0
    rnd = random.Random(2)
    gap_start = t0 + 12 * 60 + 30                                  # outage starts mid-minute 12, lasts 150 s
    while ts < t0 + 30 * 60:
        if gap_start <= ts < gap_start + 150:
            ts += 1.0
            continue
        price += rnd.gauss(0, 0.0003)
        eng.on_tick(sym, ts, price)
        ts += 1.0
    assert any(e["e"] == "tick-gap" for e in eng.feed_events)
    a = eng.analyze(sym, 60)
    assert a["candles"] >= 130, a["candles"]                      # history + bridged run, not restarted at ~15
    after_gap = [s for s in eng.signals if s["symbol"] == sym and s["tf"] == 60 and
                 t0 + 15 * 60 <= s["basis_t"] <= t0 + 21 * 60]   # last synthetic candle is #14 => paused through #21
    assert after_gap and all(s["direction"] == "SKIP" for s in after_gap), [s["direction"] for s in after_gap]
    assert any("gap" in s["reason"] for s in after_gap)


def test_alert_events_and_ingest_response():
    eng = Engine(":memory:")
    sym, t0 = "E_otc", 1_700_000_040
    hist = walk(120, 3, t0=t0 - 120 * 60)
    raw = [[r["t"], r["o"], r["c"], r["h"], r["l"], r["n"], r["t"] + 59.0] for r in hist]
    raw.append([t0, hist[-1]["c"], hist[-1]["c"], hist[-1]["c"], hist[-1]["c"], 3, t0 + 3.0])
    eng.ingest({"items": [{"k": "i", "list": [[sym, "EUR/USD (OTC)", "currency", 85, True]]}, {"k": "h", "asset": sym, "rows": raw}]})
    first = eng.ingest({"items": []})
    assert first["events"] == [] and first["seq"] == 0            # first contact never replays
    ts, price, rnd, got, after = t0 + 4.0, 1.2, random.Random(9), [], first["seq"]
    while ts < t0 + 240 * 60:
        price += rnd.gauss(0, 0.0004)
        r = eng.ingest({"items": [{"k": "t", "d": [[sym, ts, price, 0]]}], "after_seq": after})
        got += r["events"]
        after = r["seq"]
        ts += 5.0
    kinds = {e["kind"] for e in got}
    assert kinds == {"signal", "result"}, kinds
    seqs = [e["seq"] for e in got]
    assert seqs == sorted(set(seqs)), "events must be delivered once, in order"
    sig = next(e for e in got if e["kind"] == "signal")
    for k in ("id", "name", "direction", "tier", "strength", "payout", "breakeven", "expire_t", "factors", "tier_stats", "pair_stats"):
        assert k in sig, k
    assert sig["name"] == "EUR/USD (OTC)" and sig["expire_t"] == sig["target_t"] + 60 and abs(sig["breakeven"] - 1 / 1.85) < 1e-9
    res = next(e for e in got if e["kind"] == "result" and e["id"] == sig["id"])
    assert res["status"] in ("win", "loss", "draw") and res["exit"] is not None
    assert eng.events_since(eng.seq + 5) == [], "stale cursor (backend restarted) must not replay"


def test_partial_first_candle_is_not_used():
    eng = Engine(":memory:")
    base = 1_700_000_040
    eng.on_tick("X_otc", base + 30, 1.0)       # joined mid-minute
    eng.on_tick("X_otc", base + 61, 1.1)       # closes the partial candle
    assert (("X_otc", 60) not in eng.series) or not eng.series[("X_otc", 60)].rows


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all backend tests passed")
