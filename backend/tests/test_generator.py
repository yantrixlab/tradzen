"""Manual trade generator: grading, entry/expiry, replay outcome, gating, saving + settlement, calibration, API.
Run from backend/:  .venv\\Scripts\\python tests\\test_generator.py"""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from app import control, generator  # noqa: E402
from app.engine import Engine  # noqa: E402
from test_runner import SYM, session  # noqa: E402


_CACHE: dict = {}


def built():
    """One shared 300-minute synthetic session (building it takes ~45 s); tests that change state restore it."""
    if not _CACHE:
        eng = Engine(":memory:", seed_builtins=True)
        _CACHE["v"] = (eng, session(eng, minutes=300))
    return _CACHE["v"]


def test_generate_shape_and_entry_times():
    eng, end = built()
    g = generator.generate(eng, SYM, 60, now=end, save=False)
    last = eng._series(SYM, 60).bridged_tail(60)[0][-1]["t"]
    assert g["entry_t"] == last + 60 and g["expiry_t"] == g["entry_t"] + 60, "entry = candle after the last closed one, expiry one candle later"
    assert g["verdict"] in ("CALL", "PUT", "NO TRADE") and g["symbol"] == SYM and g["tf"] == 60 and g["candles"] >= 200
    assert g["counts"]["considered"] >= 14 and len(g["strategies"]) >= 14
    assert g["calibration"]["rows"][0]["label"] == "1" and {m["tf"] for m in g["mtf"]} == {300, 900}
    assert g["replay"] is False and g["outcome"] is None and g["trade_id"] is None
    if g["verdict"] != "NO TRADE":
        assert g["grade"] in "ABC" and 0 <= g["conviction"] <= 100 and g["agree"] > g["against"]
        assert all(s["supports"] for s in g["strategies"] if s["direction"] == g["verdict"])
    else:
        assert g["blocked"] and g["grade"] is None and g["conviction"] == 0


def test_replay_reports_the_real_outcome_for_every_past_candle():
    eng, end = built()
    series = eng._series(SYM, 60)
    seen = 0
    for entry_t in sorted(series.rows)[-50:-1]:
        g = generator.generate(eng, SYM, 60, at=entry_t + 5, now=end, save=False)
        assert g["replay"] is True and g["entry_t"] == entry_t
        if g["verdict"] in ("CALL", "PUT"):
            c = series.rows[entry_t]
            expect = "draw" if c["c"] == c["o"] else "win" if (g["verdict"] == "CALL") == (c["c"] > c["o"]) else "loss"
            assert g["outcome"]["status"] == expect and g["outcome"]["entry"] == c["o"] and g["outcome"]["exit"] == c["c"]
            seen += 1
        else:
            assert g["outcome"] is None
    assert seen >= 1, "at least some replays must produce a trade on this synthetic data"


def test_future_time_and_missing_data_are_refused_not_guessed():
    eng, end = built()
    last = eng._series(SYM, 60).bridged_tail(60)[0][-1]["t"]
    try:
        generator.generate(eng, SYM, 60, at=last + 60 * 10, now=end)
    except generator.NotClosedYet as e:
        assert "future" in str(e) and e.wait > 90
    else:
        raise AssertionError("future entry must be refused")
    for now, key in ((last + 100, "Try again"), (last + 130, "not arrived")):          # candle about to close / feed a little late
        try:
            generator.generate(eng, SYM, 60, at=last + 120, now=now)
        except generator.NotClosedYet as e:
            assert key in str(e) and not any(len(w) >= 10 and w.isdigit() for w in str(e).split()), "no raw epoch numbers in messages"
            assert (e.wait > 0) == (key == "Try again")
        else:
            raise AssertionError("entry whose previous candle has not arrived must be refused")
    for bad in (dict(tf=45), dict(min_grade="Z")):
        try:
            generator.generate(eng, SYM, **{"tf": 60, **bad})
        except ValueError:
            continue
        raise AssertionError(bad)
    try:
        generator.generate(eng, "NOPE_otc", 60)
    except ValueError as e:
        assert "no candles" in str(e)
    else:
        raise AssertionError("unknown pair must be refused")
    eng2 = eng
    control.bulk_status(eng2, None, "paused")
    try:
        generator.generate(eng2, SYM, 60, now=end)
    except ValueError as e:
        assert "no strategies" in str(e)
    else:
        raise AssertionError("no running strategies => clear message")
    g = generator.generate(eng2, SYM, 60, scope="all", now=end, save=False)           # paused strategies can still be used on demand
    assert g["counts"]["considered"] >= 14
    only = [s["id"] for s in eng2.repo.list() if s["key"] in ("macd-cross", "rsi-extremes")]
    g = generator.generate(eng2, SYM, 60, ids=only, now=end, save=False)
    assert g["counts"]["considered"] == 2
    control.apply_preset(eng2, "standard")                                              # restore the shared engine


def test_stale_feed_and_late_entry_are_flagged():
    eng, end = built()
    g = generator.generate(eng, SYM, 60, now=end + 3600, save=False)                   # an hour after the last data (replay=False)
    last = eng._series(SYM, 60).bridged_tail(60)[0][-1]["t"]
    assert g["entry_t"] == last + 60
    assert g["verdict"] == "NO TRADE" and "missing" in g["blocked"], "entry candle already over => never a recommendation"
    g_late = generator.generate(eng, SYM, 60, now=g["entry_t"] + 40, save=False)
    assert g_late["late"] is True
    if g_late["verdict"] != "NO TRADE":
        assert any("wait for the next candle" in w for w in g_late["warnings"])
    g_ok = generator.generate(eng, SYM, 60, now=g["entry_t"] + 2, save=False)
    assert g_ok["late"] is False
    # no tick received for a long time (real clock) => blocked, never a stale recommendation
    eng.last_tick[SYM]["recv"] -= 1000
    g_stale = generator.generate(eng, SYM, 60, save=False)
    eng.last_tick[SYM]["recv"] += 1000
    assert g_stale["verdict"] == "NO TRADE" and "not updating" in g_stale["blocked"]


def test_min_grade_filters_and_grading_is_consistent():
    eng, end = built()
    for entry_t in sorted(eng._series(SYM, 60).rows)[-60:-1]:
        g = generator.generate(eng, SYM, 60, at=entry_t, now=end, save=False)
        if g["verdict"] == "NO TRADE":
            continue
        if g["grade"] == "A":
            assert g["agree"] >= 3 and g["against"] == 0 and not g["mtf_opposed"] and g["conviction"] >= 60
        strict = generator.generate(eng, SYM, 60, at=entry_t, now=end, save=False, min_grade="A")
        assert (strict["verdict"] != "NO TRADE") == (g["grade"] == "A")
        if g["grade"] != "A":
            assert "below your minimum" in strict["blocked"]


def test_saved_trades_settle_and_are_idempotent():
    eng, end = built()
    series = eng._series(SYM, 60)
    saved = None
    for entry_t in sorted(series.rows)[-60:-1]:
        # simulate "live": the entry candle is unknown at generation time by hiding it, then restoring it
        row = series.rows.pop(entry_t)
        later = {t: series.rows.pop(t) for t in [t for t in series.rows if t > entry_t]}
        g = generator.generate(eng, SYM, 60, now=entry_t + 3)
        series.rows[entry_t] = row
        series.rows.update(later)
        if g["trade_id"]:
            again = generator.generate(eng, SYM, 60, now=entry_t + 6) if False else None
            saved = (g, entry_t)
            break
    assert saved, "some candle must yield a tradable verdict"
    g, entry_t = saved
    h = generator.history(eng)
    me = next(r for r in h["rows"] if r["id"] == g["trade_id"])
    assert me["status"] in ("win", "loss", "draw") and me["direction"] == g["verdict"] and me["entry_t"] == entry_t
    c = series.rows[entry_t]
    assert me["entry"] == c["o"] and me["exit"] == c["c"]
    assert h["summary"]["settled"] + h["summary"]["draws"] == len(h["rows"]) and h["summary"]["pending"] == 0
    assert generator._save(eng, g) == g["trade_id"], "same candle + direction is never saved twice"
    assert generator.delete_trade(eng, g["trade_id"]) and not generator.delete_trade(eng, g["trade_id"])


def test_calibration_counts_agreement_groups():
    eng, _ = built()
    cal = generator.calibration(eng, 60)
    assert [r["agree"] for r in cal["rows"]] == [1, 2, 3, 4]
    for r in cal["rows"]:
        assert r["wins"] <= r["settled"] and (r["accuracy"] is None) == (r["settled"] == 0)
        if r["settled"]:
            assert r["ci_low"] <= r["accuracy"] <= r["ci_high"]


def test_api_roundtrip():
    from fastapi.testclient import TestClient
    with tempfile.TemporaryDirectory() as d:
        os.environ["QXS_DB"] = str(Path(d) / "gen.db")
        sys.modules.pop("app.main", None)
        import app.main as main
        with TestClient(main.app) as c:
            r = c.post("/api/generate", json={"symbol": "NOPE_otc", "tf": 60})
            assert r.status_code == 400 and "no candles" in r.json()["detail"]
            assert c.post("/api/generate", json={"symbol": "X", "tf": 7}).status_code == 400
            h = c.get("/api/generate/history").json()
            assert h["rows"] == [] and h["summary"]["settled"] == 0
            assert c.get("/api/generate/calibration", params={"tf": 60}).json()["rows"][0]["label"] == "1"
            assert c.delete("/api/generate/history/999").status_code == 404
        del os.environ["QXS_DB"]
        sys.modules.pop("app.main", None)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all generator tests passed")
