"""Phase B (part 2): strategy lifecycle, multi-strategy runner, alert grouping, auto-mute, API.
Run from backend/:  .venv\\Scripts\\python tests\\test_runner.py"""
import os
import random
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from app import evaluator  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.strategy_repo import RepoError  # noqa: E402
from test_engine import walk  # noqa: E402

SYM, T0 = "R_otc", 1_700_000_040
SIMPLE = {"indicators": [{"id": "rsi14", "type": "rsi", "n": 14}],
          "factors": [{"id": "r", "name": "RSI turn", "weight": 100, "kind": "expr", "call": "cross_above(rsi14, 35)", "put": "cross_below(rsi14, 65)"}]}


def session(eng: Engine, minutes: int = 300, seed: int = 3, sym: str = SYM, hist: bool = True, t_start=None) -> float:
    t0 = t_start or T0
    if hist:
        h = walk(250, seed, t0=t0 - 250 * 60)
        raw = [[r["t"], r["o"], r["c"], r["h"], r["l"], r["n"], r["t"] + 59.0] for r in h]
        raw.append([t0, h[-1]["c"], h[-1]["c"], h[-1]["c"], h[-1]["c"], 3, t0 + 3.0])
        eng.ingest({"items": [{"k": "i", "list": [[sym, "EUR/USD (OTC)", "currency", 85, True]]}, {"k": "h", "asset": sym, "rows": raw}]})
    ts, price, rnd = t0 + 4.0, 1.2, random.Random(seed + 6)
    while ts < t0 + minutes * 60:
        price += rnd.gauss(0, 0.0004)
        eng.ingest({"items": [{"k": "t", "d": [[sym, ts, price, 0]]}]})
        ts += 5.0
    return ts


def test_lifecycle_and_versions():
    eng = Engine(":memory:", seed_builtins=True)
    keys = {s["key"] for s in eng.repo.list()}
    assert {"confluence-5", "bollinger-reversion", "macd-cross", "consensus-3", "candle-streak-reversal"} <= keys
    assert next(s for s in eng.repo.list() if s["key"] == "confluence-5")["status"] == "live"
    assert all(s["status"] == "paper" for s in eng.repo.list() if s["key"] != "confluence-5")
    sid = eng.create_strategy("my-rsi", "My RSI", "test", SIMPLE)
    d = eng.strategy_detail(sid)
    assert d["kind"] == "custom" and d["status"] == "paper" and d["version"] == 1
    for bad in (lambda: eng.create_strategy("my-rsi", "dup", "", SIMPLE), lambda: eng.create_strategy("Bad Key", "x", "", SIMPLE),
                lambda: eng.create_strategy("broken", "x", "", {"factors": []}), lambda: eng.create_strategy("st", "x", "", SIMPLE, "archived")):
        try:
            bad()
        except RepoError:
            continue
        raise AssertionError("expected RepoError")
    eng.set_strategy_status(sid, "live")
    v2 = {**SIMPLE, "gates": {"min_candles": 80}}
    assert eng.new_strategy_version(sid, v2, "longer warm-up") == 2
    d = eng.strategy_detail(sid)
    assert d["version"] == 2 and d["status"] == "paper", "an edited live strategy must go back to paper"
    assert [v["version"] for v in d["versions"]] == [2, 1] and eng.repo.get_version(sid, 1)["spec"] == SIMPLE
    for forbidden in (lambda: eng.new_strategy_version(eng.strategy_id, SIMPLE), lambda: eng.delete_strategy(eng.strategy_id)):
        try:
            forbidden()
        except RepoError:
            continue
        raise AssertionError("built-ins must be read-only")
    clone = eng.clone_strategy(next(s["id"] for s in eng.repo.list() if s["key"] == "macd-cross"), "my-macd")
    assert eng.strategy_detail(clone)["status"] == "draft" and clone not in eng.strategies, "drafts do not run"
    eng.set_strategy_status(clone, "paper")
    assert clone in eng.strategies
    assert eng.delete_strategy(clone) == "deleted" and clone not in {s["id"] for s in eng.repo.list()}
    eng.set_strategy_status(sid, "paused")
    assert sid not in eng.strategies


def test_multi_strategy_runner():
    eng = Engine(":memory:", seed_builtins=True)
    session(eng, minutes=360)
    eng._flush(force=True)
    issued = [s for s in eng.signals if s["direction"] != "SKIP"]
    by = {}
    for s in issued:
        by.setdefault(s["strategy_id"], []).append(s)
    assert len(by) >= 4, f"only {len(by)} strategies produced signals"
    dflt = eng.strategy_id
    assert all(s["mode"] == ("live" if s["strategy_id"] == dflt else "paper") for s in issued)
    assert all(s["direction"] != "SKIP" or s["strategy_id"] == dflt for s in eng.signals), "skips only recorded for the default strategy"
    sigs_ev = [e for e in eng.notify_log if e["kind"] == "signal"]
    assert sigs_ev and all(e["strategy_id"] == dflt for e in sigs_ev), "paper strategies must not alert"
    keys = [(e["symbol"], e["tf"], e["basis_t"], e["direction"]) for e in sigs_ev]
    assert len(keys) == len(set(keys)), "one alert per pair/timeframe/direction/candle"
    alerted = {e["id"] for e in sigs_ev}
    assert {e["id"] for e in eng.notify_log if e["kind"] == "result"} <= alerted, "results only for alerted signals"
    assert not [k for k in eng.pending if k[3] < T0 + 300 * 60 - 3 * 900]
    settled = [s for s in issued if s["status"] in ("win", "loss", "draw")]
    assert len(settled) >= len(issued) - 40
    for sid in by:
        st = eng.stats(strategy_id=sid)
        assert st["total"] == len(by[sid]) and st["wins"] + st["losses"] == st["settled"]
    # features/outcomes exist for every issued signal of every strategy
    n_feat = eng.db.execute("SELECT COUNT(*) FROM signal_features").fetchone()[0]
    assert n_feat == len(issued), (n_feat, len(issued))
    summary = {s["key"]: s for s in eng.strategy_summary()}
    assert summary["confluence-5"]["status"] == "live" and summary["confluence-5"]["evaluations"] > 100
    assert any(v["stats"]["total"] for k, v in summary.items() if k != "confluence-5")


def test_promoting_a_strategy_alerts_and_groups_duplicates():
    eng = Engine(":memory:", seed_builtins=True)
    for s in eng.repo.list():
        if s["status"] == "paper":
            eng.set_strategy_status(s["id"], "live")             # everything live: many simultaneous signals per candle
    session(eng, minutes=360)
    sigs_ev = [e for e in eng.notify_log if e["kind"] == "signal"]
    assert len({e["strategy_id"] for e in sigs_ev}) >= 2, "promoted strategies must alert"
    keys = [(e["symbol"], e["tf"], e["basis_t"], e["direction"]) for e in sigs_ev]
    assert len(keys) == len(set(keys)), "duplicates must be folded into one alert"
    grouped = [e for e in sigs_ev if e["also"]]
    assert grouped, "with every strategy live some alerts must list the other agreeing strategies"
    for e in grouped:
        assert all({"name", "tier", "strength"} <= set(a) for a in e["also"]) and e["strength"] >= max(a["strength"] for a in e["also"])


def test_auto_mute():
    eng = Engine(":memory:", seed_builtins=False)
    eng.instruments[SYM] = {"symbol": SYM, "name": "X", "payout": 85, "market": "OTC", "category": "", "open": True}
    sid = eng.strategy_id

    def fake(n_win, n_loss):
        eng.signals[:] = [s for s in eng.signals if s["strategy_id"] != sid]
        for i in range(n_win + n_loss):
            eng.signals.append({"strategy_id": sid, "symbol": SYM, "status": "win" if i < n_win else "loss", "direction": "CALL", "tf": 60,
                                "basis_t": i, "tier": "A", "strength": 50, "mode": "live"})
    fake(20, 20)
    assert eng.is_muted(sid) is False, "too few settled signals to judge"
    fake(30, 70)                                           # 30 % wins: upper bound far below the 54 % break-even
    assert eng.is_muted(sid) is True
    assert any(e["e"] == "auto-mute" for e in eng.feed_events)
    sig = {"mode": "live", "strategy_id": sid, "direction": "CALL", "id": 1, "symbol": SYM, "tf": 60, "tier": "A", "strength": 50}
    seq = eng.seq
    eng._alert([sig])
    assert eng.seq == seq, "muted strategies do not alert"
    fake(60, 40)                                           # 60 % wins
    assert eng.is_muted(sid) is False and any(e["e"] == "auto-unmute" for e in eng.feed_events)


def test_a_long_losing_record_mutes_even_when_100_signals_cannot_tell():
    eng = Engine(":memory:", seed_builtins=False)
    eng.instruments[SYM] = {"symbol": SYM, "name": "X", "payout": 85, "market": "OTC", "category": "", "open": True}
    sid = eng.strategy_id

    def fake(n, rate):
        eng.signals[:] = [{"strategy_id": sid, "symbol": SYM, "status": "win" if (i % 100) < rate else "loss", "direction": "CALL", "tf": 60,
                           "basis_t": i, "tier": "A", "strength": 50, "mode": "live"} for i in range(n)]
    fake(100, 49)                                          # 49 % over 100 signals: the 95 % range still contains break-even (54 %), so no verdict yet
    assert eng.is_muted(sid) is False
    fake(300, 49)
    assert eng.is_muted(sid) is False                      # and 300 is still too few for the long rule
    fake(600, 49)                                          # 49 % over 500: range 44.7-53.6 %, clearly below the 54 % break-even
    assert eng.is_muted(sid) is True
    assert any(e["e"] == "auto-mute" and "clearly below break-even" in (e["d"] or "") for e in eng.feed_events)
    fake(600, 58)                                          # the record recovers: it alerts again by itself
    assert eng.is_muted(sid) is False and any(e["e"] == "auto-unmute" for e in eng.feed_events)


def test_a_broken_strategy_does_not_stop_the_others():
    eng = Engine(":memory:", seed_builtins=True)
    bad_id = eng.create_strategy("explodes", "Explodes", "", SIMPLE)
    orig = evaluator.evaluate

    def flaky(spec, rows, cache=None, ctx=None):
        if spec["factors"][0]["id"] == "r" and spec["factors"][0].get("call", "").startswith("cross_above(rsi14, 35)"):
            raise RuntimeError("boom")
        return orig(spec, rows, cache, ctx)
    evaluator.evaluate = flaky
    try:
        session(eng, minutes=200)
    finally:
        evaluator.evaluate = orig
    assert any(e["e"] == "strategy-error" for e in eng.feed_events)
    assert not [s for s in eng.signals if s["strategy_id"] == bad_id]
    assert [s for s in eng.signals if s["strategy_id"] == eng.strategy_id], "other strategies must keep working"


def test_scope_and_timeframes_are_respected():
    eng = Engine(":memory:", seed_builtins=False)
    only5 = eng.create_strategy("five-min", "5m only", "", {**SIMPLE, "timeframes": [300], "scope": {"market": "OTC"}})
    live_only = eng.create_strategy("live-only", "live only", "", {**SIMPLE, "scope": {"market": "LIVE"}})
    session(eng, minutes=400)
    assert {s["tf"] for s in eng.signals if s["strategy_id"] == only5} <= {300}
    assert not [s for s in eng.signals if s["strategy_id"] == live_only], "an OTC pair must not feed a LIVE-only strategy"


def test_api_roundtrip():
    from fastapi.testclient import TestClient
    with tempfile.TemporaryDirectory() as d:
        os.environ["QXS_DB"] = str(Path(d) / "api.db")
        for m in [m for m in sys.modules if m == "app.main"]:
            del sys.modules[m]
        import app.main as main
        with TestClient(main.app) as c:
            lst = c.get("/api/strategies").json()
            assert any(s["key"] == "confluence-5" for s in lst) and len(lst) >= 17
            cat = c.get("/api/spec/catalog").json()
            assert "bollinger" in cat["indicators"] and "bollinger_bounce" in cat["fn_factors"] and "adx_max" in cat["filters"]
            assert c.post("/api/strategies/validate", json={"spec": {"factors": []}}).json()["ok"] is False
            ok = c.post("/api/strategies/validate", json={"spec": SIMPLE}).json()
            assert ok["ok"] and ok["spec"]["gates"]["min_agree"] == 1
            r = c.post("/api/strategies", json={"key": "api-one", "name": "API one", "spec": SIMPLE})
            assert r.status_code == 200, r.text
            sid = r.json()["id"]
            assert c.post("/api/strategies", json={"key": "api-one", "name": "dup", "spec": SIMPLE}).status_code == 400
            assert c.post("/api/strategies", json={"key": "bad-spec", "name": "x", "spec": {"factors": []}}).status_code == 400
            assert c.post(f"/api/strategies/{sid}/versions", json={"spec": {**SIMPLE, "gates": {"min_candles": 90}}, "notes": "v2"}).json()["version"] == 2
            assert c.post(f"/api/strategies/{sid}/status", json={"status": "live"}).json()["status"] == "live"
            assert c.post(f"/api/strategies/{sid}/status", json={"status": "nonsense"}).status_code == 400
            assert c.put(f"/api/strategies/{sid}", json={"name": "Renamed"}).json()["name"] == "Renamed"
            cl = c.post(f"/api/strategies/{sid}/clone", json={"key": "api-clone"}).json()
            assert cl["status"] == "draft" and cl["kind"] == "custom"
            builtin_id = next(s["id"] for s in lst if s["key"] == "macd-cross")
            assert c.post(f"/api/strategies/{builtin_id}/versions", json={"spec": SIMPLE}).status_code == 400
            assert c.delete(f"/api/strategies/{builtin_id}").status_code == 400
            assert c.get("/api/strategies/99999").status_code == 404
            # warm a pair through /ingest, then preview a spec on it
            h = walk(250, 3, t0=T0 - 250 * 60)
            raw = [[r["t"], r["o"], r["c"], r["h"], r["l"], r["n"], r["t"] + 59.0] for r in h]
            raw.append([T0, h[-1]["c"], h[-1]["c"], h[-1]["c"], h[-1]["c"], 3, T0 + 3.0])
            assert c.post("/ingest", json={"items": [{"k": "h", "asset": SYM, "rows": raw}]}).status_code == 200
            pv = c.post("/api/strategies/preview", json={"spec": SIMPLE, "symbol": SYM, "tf": 60}).json()
            assert pv["analysis"]["candles"] >= 200 and pv["analysis"]["direction"] in ("CALL", "PUT", "SKIP")
            assert c.get(f"/api/strategies/{sid}/preview", params={"symbol": SYM, "tf": 60}).status_code == 200
            assert c.post("/api/strategies/preview", json={"spec": {"kind": "meta", "consensus": {"members": "all_active"}}, "symbol": SYM}).status_code == 400
            assert c.delete(f"/api/strategies/{cl['id']}").json()["result"] == "deleted"
            assert "strategies" in c.get("/api/state").json()
        del os.environ["QXS_DB"]
        sys.modules.pop("app.main", None)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all runner tests passed")
