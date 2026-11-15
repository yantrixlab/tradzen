"""Phase A tests: migrations, candle persistence, signal feature snapshots, outcomes, notes, queries.
Run from backend/:  .venv\\Scripts\\python tests\\test_foundation.py"""
import json
import random
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from app.db import MIGRATIONS, open_db, schema_version  # noqa: E402
from app.engine import Engine  # noqa: E402
from test_engine import walk  # noqa: E402

SYM, T0 = "F_otc", 1_700_000_040


def run_session(eng: Engine, minutes: int = 240, seed: int = 3, start_hist: bool = True) -> None:
    if start_hist:
        hist = walk(120, seed, t0=T0 - 120 * 60)
        raw = [[r["t"], r["o"], r["c"], r["h"], r["l"], r["n"], r["t"] + 59.0] for r in hist]
        raw.append([T0, hist[-1]["c"], hist[-1]["c"], hist[-1]["c"], hist[-1]["c"], 3, T0 + 3.0])
        eng.ingest({"items": [{"k": "i", "list": [[SYM, "EUR/USD (OTC)", "currency", 85, True]]}, {"k": "h", "asset": SYM, "rows": raw}]})
    ts, price, rnd = T0 + 4.0, 1.2, random.Random(9)
    while ts < T0 + minutes * 60:
        price += rnd.gauss(0, 0.0004)
        eng.ingest({"items": [{"k": "t", "d": [[SYM, ts, price, 0]]}]})
        ts += 5.0


def test_legacy_db_is_migrated():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "legacy.db"
        con = sqlite3.connect(path)
        con.execute("""CREATE TABLE signals (id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, symbol TEXT, tf INTEGER, basis_t INTEGER,
            target_t INTEGER, direction TEXT, score REAL, strength INTEGER, agree INTEGER, factors TEXT, reason TEXT, entry REAL,
            status TEXT, exit REAL, sentiment REAL)""")
        con.execute("INSERT INTO signals (created, symbol, tf, basis_t, target_t, direction, score, strength, agree, factors, reason, entry, status, exit, sentiment)"
                    " VALUES (1,'X_otc',60,100,160,'PUT',-50,50,3,'[]','',1.2,'win',1.19,40)")
        con.execute("INSERT INTO signals (created, symbol, tf, basis_t, target_t, direction, score, strength, agree, factors, reason, entry, status, exit, sentiment)"
                    " VALUES (2,'X_otc',60,160,220,'SKIP',0,0,0,'[]','none',1.2,'skip',NULL,NULL)")
        con.commit()
        con.close()
        eng = Engine(path, seed_builtins=False)
        assert len(eng.signals) == 2 and eng.signals[0]["tier"] == "A" and eng.signals[0]["status"] == "win"
        assert eng.signals[0]["strategy_id"] == eng.strategy_id and eng.signals[0]["mode"] == "live"
        assert schema_version(eng.db) == MIGRATIONS[-1][0]
        eng.close()
        eng2 = Engine(path, seed_builtins=False)                                   # reopening is idempotent
        assert eng2.db.execute("SELECT COUNT(*) FROM strategies").fetchone()[0] == 1
        # v1 = descriptive spec from the migration, v2 = the executable spec; reopening must not add a v3
        assert eng2.db.execute("SELECT COUNT(*) FROM strategy_versions").fetchone()[0] == 2
        assert eng2.strategy_version == 2 and eng2.default_def["spec"]["factors"][0]["id"] == "bollinger"
        assert len(eng2.signals) == 2
        eng2.close()


def test_candles_persist_and_reload():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "p.db"
        a = Engine(path, seed_builtins=False)
        run_session(a, minutes=60)
        a._log("test-event", "hello")
        n_mem = len(a.series[(SYM, 60)].rows)
        a.close()
        b = Engine(path, seed_builtins=False)
        assert len(b.series[(SYM, 60)].rows) >= n_mem - 2, (len(b.series[(SYM, 60)].rows), n_mem)
        assert b.analyze(SYM, 60)["candles"] >= 60, "analysis must be warm right after a restart"
        assert (SYM, 300) in b.series and b.series[(SYM, 300)].rows
        assert b.instruments[SYM]["payout"] == 85 and b.instruments[SYM]["market"] == "OTC"
        assert any(e["e"] == "test-event" for e in b.feed_events)
        cov = {(c["symbol"], c["tf"]): c for c in b.coverage()}
        assert cov[(SYM, 60)]["completeness"] >= 0.97 and cov[(SYM, 60)]["missing"] <= 3, cov[(SYM, 60)]
        b.close()


def test_features_and_outcomes():
    eng = Engine(":memory:")
    run_session(eng)
    eng._flush(force=True)
    issued = [s for s in eng.signals if s["direction"] != "SKIP"]
    assert len(issued) >= 5, len(issued)
    n_feat = eng.db.execute("SELECT COUNT(*) FROM signal_features").fetchone()[0]
    assert n_feat == len(issued), (n_feat, len(issued))
    sid = issued[len(issued) // 2]["id"]
    f = json.loads(eng.db.execute("SELECT features FROM signal_features WHERE signal_id=?", (sid,)).fetchone()[0])
    for k in ("rsi14", "rsi2", "stoch_k", "cci20", "macd_hist", "bb_pctb", "bb_bw_pctile", "atr14", "atr_ratio", "adx14", "plus_di",
              "ema_stack", "dist_hi20_atr", "body_ratio", "streak", "last5", "ret5_pct", "hour_utc", "weekday", "session",
              "trend_align", "factor_votes", "payout", "direction", "concurrent"):
        assert k in f, k
    assert 0 <= f["rsi14"] <= 100 and f["atr14"] > 0 and 0 <= f["hour_utc"] < 24 and f["session"] in ("asia", "europe", "eu_us_overlap", "us", "late")
    assert len(f["last5"]) == 5 and f["trend_align"] in (-1, 0, 1)
    rows = eng.db.execute("SELECT o.result, o.margin_atr, o.near_miss, s.status FROM signal_outcomes o JOIN signals s ON s.id = o.signal_id").fetchall()
    assert rows, "no outcomes stored"
    for result, margin, near, status in rows:
        assert result == status and margin is not None
        if result == "win":
            assert margin > 0 and not near
        elif result == "loss":
            assert margin < 0
            assert bool(near) == (margin > -0.25)
        else:
            assert margin == 0
    settled = sum(1 for s in issued if s["status"] in ("win", "loss", "draw"))
    assert len(rows) == settled, (len(rows), settled)


def test_notes_detail_and_query():
    eng = Engine(":memory:")
    run_session(eng)
    eng._flush(force=True)
    loss = next(s for s in reversed(eng.signals) if s["status"] == "loss")
    note = eng.add_note(loss["id"], ["News Spike", "against trend", "news spike", ""], "price jumped on headline")
    assert note and note["tags"] == ["against trend", "news spike"], note
    d = eng.signal_detail(loss["id"])
    assert d["signal"]["status"] == "loss" and d["features"]["direction"] == loss["direction"]
    assert d["outcome"]["result"] == "loss" and d["outcome"]["margin_atr"] < 0
    assert d["notes"][0]["note"] == "price jumped on headline" and len(d["candles"]) >= 20
    assert any(c["t"] == loss["target_t"] for c in d["candles"]), "candle after the signal must be included"
    q = eng.query_signals(status="loss")
    assert q["total"] == sum(1 for s in eng.signals if s["status"] == "loss") and all(r["status"] == "loss" for r in q["rows"])
    assert all(r["adx"] is not None for r in q["rows"][:5])
    assert eng.query_signals(direction="CALL")["total"] == eng.stats()["calls"]
    assert eng.query_signals(include_skips=True)["total"] == len(eng.signals)
    assert len(eng.query_signals(limit=3)["rows"]) == 3
    # sorting orders the WHOLE result, not just the visible page; missing values go last; ties keep newest first
    total = eng.query_signals()["total"]
    asc = eng.query_signals(limit=1000, sort="strength", order="asc")["rows"]
    desc = eng.query_signals(limit=1000, sort="strength", order="desc")["rows"]
    assert len(asc) == total and [r["strength"] for r in asc] == sorted(r["strength"] for r in asc) and [r["strength"] for r in desc] == sorted((r["strength"] for r in desc), reverse=True)
    pages = [r["id"] for off in range(0, total, 3) for r in eng.query_signals(limit=3, offset=off, sort="strength", order="desc")["rows"]]
    assert pages == [r["id"] for r in desc], "paging through a sorted table must walk the same global order"
    assert [r["id"] for r in eng.query_signals(limit=1000, sort="nonsense")["rows"]] == [r["id"] for r in eng.query_signals(limit=1000)["rows"]]      # unknown column: default order
    by_pair = [r["symbol"] for r in eng.query_signals(limit=1000, sort="pair", order="asc")["rows"]]
    assert by_pair == sorted(by_pair)
    by_margin = [r["margin_atr"] for r in eng.query_signals(limit=1000, sort="margin", order="desc")["rows"]]
    known = [m for m in by_margin if m is not None]
    assert known == sorted(known, reverse=True) and by_margin[:len(known)] == known                                  # pending signals (no margin yet) come last
    # one pair, or several pairs at once (comma separated); the martingale summary follows the same filter
    syms = sorted({s["symbol"] for s in eng.signals if s["direction"] != "SKIP"})
    pairs = eng.signal_pairs()
    assert {p["symbol"] for p in pairs} >= set(syms) and sum(p["n"] for p in pairs) == eng.query_signals()["total"] and pairs == sorted(pairs, key=lambda p: (-p["n"], p["symbol"]))
    sid0 = next(iter({s["strategy_id"] for s in eng.signals if s["direction"] != "SKIP"}))
    mine = eng.signal_pairs(sid0)
    assert sum(p["n"] for p in mine) == eng.query_signals(strategy_id=sid0)["total"] and all(p["n"] > 0 for p in mine)            # a strategy's own pairs and counts
    one = eng.query_signals(symbol=syms[0], limit=1000)
    assert one["total"] == sum(1 for s in eng.signals if s["symbol"] == syms[0] and s["direction"] != "SKIP") and {r["symbol"] for r in one["rows"]} == {syms[0]}
    if len(syms) > 1:
        two = eng.query_signals(symbol=f"{syms[0]}, {syms[1]}", limit=1000)
        assert two["total"] == one["total"] + eng.query_signals(symbol=syms[1])["total"] and {r["symbol"] for r in two["rows"]} == {syms[0], syms[1]}
        assert two["mtg"]["settled"] <= two["total"] and two["mtg"]["settled"] >= one["mtg"]["settled"]
    assert eng.query_signals(symbol=" , ")["total"] == eng.query_signals()["total"]                                          # nothing usable picked = no filter
    # the strategy page: plain / one-step / two-step martingale accuracy overall, by timeframe and by tier
    sid = next(s["id"] for s in eng.repo.list() if any(x["strategy_id"] == s["id"] for x in eng.signals))
    det = eng.strategy_detail(sid)
    m = det["mtg"]
    assert m["all"]["settled"] == det["stats"]["wins"] + det["stats"]["losses"] + det["stats"]["draws"]
    assert sum(g["settled"] for g in m["by_tf"].values()) == m["all"]["settled"] and sum(g["settled"] for g in m["by_tier"].values()) == m["all"]["settled"]
    assert abs(m["all"]["flat_accuracy"] - det["stats"]["accuracy"]) < 1e-9
    assert m["all"]["m2"]["accuracy"] >= m["all"]["m1"]["accuracy"] >= m["all"]["flat_accuracy"] - 1e-9          # a re-entry can only add wins to the percentage
    assert m["all"]["m1"]["rescued"] + m["all"]["m1"]["lost"] <= m["all"]["losses"] and m["all"]["m1"]["taken"] + m["all"]["m1"]["skipped"] == m["all"]["settled"]
    # martingale outcome of every row + the summary over the whole filter (same rule as mtg.py: the re-entry is the candle after the losing one)
    res = eng.query_signals(limit=1000)
    summ = res["mtg"]
    settled = [r for r in res["rows"] if r["status"] in ("win", "loss", "draw")]
    assert summ["settled"] == len(settled) and all(r["mtg1"] and r["mtg2"] for r in settled)
    assert all(r["mtg1"] is None and r["mtg2"] is None for r in res["rows"] if r["status"] not in ("win", "loss", "draw"))
    assert all(r["mtg1"]["outcome"] == "win" for r in settled if r["status"] == "win" and not r["mtg1"]["skip"])           # a first-trade win needs no re-entry
    assert summ["m2"]["accuracy"] >= summ["m1"]["accuracy"] >= 0 and summ["m1"]["taken"] + summ["m1"]["skipped"] == summ["settled"]
    assert abs(summ["flat_accuracy"] - sum(1 for r in settled if r["status"] == "win") / max(1, sum(1 for r in settled if r["status"] in ("win", "loss")))) < 1e-9
    best_first = [r["mtg1"]["outcome"] for r in eng.query_signals(limit=1000, sort="mtg1", order="asc")["rows"] if r["mtg1"] and not r["mtg1"]["skip"]]
    order_of = {"win": 0, "mtg1": 1, "draw": 3, "open": 4, "lost": 5}
    assert [order_of[o] for o in best_first] == sorted(order_of[o] for o in best_first)                                    # sorted by outcome, best first
    assert eng.delete_note(note["id"]) and not eng.delete_note(note["id"])
    assert eng.add_note(999999, ["x"], "y") is None and eng.signal_detail(999999) is None


def test_clear_removes_related_rows():
    eng = Engine(":memory:")
    run_session(eng, minutes=120)
    eng.add_note(next(s for s in eng.signals if s["direction"] != "SKIP")["id"], ["t"], "n")
    eng.clear()
    for table in ("signals", "signal_features", "signal_outcomes", "signal_notes"):
        assert eng.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert eng.db.execute("SELECT COUNT(*) FROM strategies").fetchone()[0] == 1     # strategies survive a clear


def test_open_db_fresh_has_everything():
    con = open_db(":memory:")
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("signals", "candles", "sentiment", "instruments", "events", "strategies", "strategy_versions", "strategy_scope",
              "signal_features", "signal_outcomes", "signal_notes", "schema_version"):
        assert t in names, t


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all foundation tests passed")
