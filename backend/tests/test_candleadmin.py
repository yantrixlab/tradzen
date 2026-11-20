"""Inspecting and removing stored candles: the inventory, the quality check, a removal that needs a confirmation, saves what it removes and keeps the live memory in step.
Run from backend/:  .venv\\Scripts\\python tests\\test_candleadmin.py"""
import gzip
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import candleadmin  # noqa: E402
from app.engine import Engine  # noqa: E402
from test_auth import ADMIN, ADMIN_PW, H, _cleanup, _client, _main  # noqa: E402

T0 = 1_794_400_020 // 900 * 900
SYM = "AUDCAD_mkt"


def put(e: Engine, sym: str, tf: int, t: int, o, h, l, c, src: str) -> None:
    e.db.execute("INSERT OR REPLACE INTO candles (symbol, tf, ts, o, h, l, c, ticks, source) VALUES (?,?,?,?,?,?,?,?,?)", (sym, tf, t, o, h, l, c, 20, src))


def filled() -> tuple[Engine, Path]:
    d = Path(tempfile.mkdtemp())
    e = Engine(d / "t.db")
    for i in range(120):                                              # a clean real-market series
        o = 0.95 + 0.0001 * (i % 5)
        put(e, SYM, 60, T0 + i * 60, o, o + 0.0003, o - 0.0002, o + 0.0001, "yahoo")
    put(e, SYM, 60, T0 + 50 * 60, 0.9500, 0.9502, 0.9420, 0.9501, "yahoo")             # a spike: a range of 8 pips among 5-pip candles
    put(e, SYM, 60, T0 + 70 * 60, 0.9700, 0.9701, 0.9699, 0.9700, "yahoo")             # a jump: opens 200 pips above the previous close
    put(e, SYM, 60, T0 + 80 * 60, 0.95, 0.95, 0.95, 0.95, "yahoo")                     # flat
    put(e, SYM, 60, T0 + 90 * 60, 0.95, 0.94, 0.96, 0.95, "yahoo")                     # broken shape: high below low
    for i in range(30):
        put(e, "EURUSD_otc", 60, T0 + i * 60, 1.1, 1.1003, 1.0998, 1.1001, "tick")
    for i in range(10):
        put(e, "EURUSD_otc", 60, T0 + 3600 + i * 60, 1.1, 1.1003, 1.0998, 1.1001, "contrib:7")
    for i in range(6):
        put(e, SYM, 300, T0 + i * 300, 0.95, 0.9503, 0.9498, 0.9501, "derived")
    e.db.commit()
    return e, d


def test_inventory_groups_by_pair_timeframe_and_kind():
    e, _ = filled()
    inv = {(r["symbol"], r["tf"], r["kind"]): r for r in candleadmin.inventory(e)}
    assert inv[(SYM, 60, "yahoo")]["candles"] == 120 and inv[(SYM, 60, "yahoo")]["flat"] == 1
    assert inv[("EURUSD_otc", 60, "tick")]["candles"] == 30 and inv[("EURUSD_otc", 60, "contrib")]["candles"] == 10
    assert inv[(SYM, 300, "derived")]["candles"] == 6


def test_check_finds_spikes_jumps_flat_and_broken_candles():
    e, _ = filled()
    r = candleadmin.check(e, SYM, 60)
    assert r["examined"] == 120 and r["broken_shape"] == 1 and r["flat"] == 1
    assert r["spikes"] >= 1 and any(x["t"] == T0 + 50 * 60 for x in r["worst_spikes"]), r["worst_spikes"]
    assert any(x["t"] == T0 + 70 * 60 for x in r["worst_jumps"]), r["worst_jumps"]
    assert r["verdict"] == "suspect candles found: look at the examples"
    clean = candleadmin.check(e, "EURUSD_otc", 60, "tick")
    assert clean["verdict"] == "looks clean" and clean["spikes"] == 0 and clean["jumps"] == 0


def test_a_dry_run_only_counts():
    e, d = filled()
    res = candleadmin.delete(e, d / "deleted", SYM, tf=60, kind="yahoo", dry_run=True)
    assert res["dry_run"] and res["total"] == 126 and res["refetch"] is True, res          # 120 one-minute + the 6 five-minute candles built from them
    assert e.db.execute("SELECT COUNT(*) FROM candles WHERE symbol=?", (SYM,)).fetchone()[0] == 126, "nothing was removed"
    assert not (d / "deleted").exists()


def test_removal_by_time_range_keeps_the_rest_and_saves_what_it_removed():
    e, d = filled()
    since, until = T0 + 40 * 60, T0 + 60 * 60
    res = candleadmin.delete(e, d / "deleted", SYM, tf=60, since=since, until=until, with_derived=False, dry_run=False)
    assert res["removed"] == 21 and res["archive"].endswith(".csv.gz"), res
    left = [r[0] for r in e.db.execute("SELECT ts FROM candles WHERE symbol=? AND tf=60 ORDER BY ts", (SYM,))]
    assert all(not since <= t <= until for t in left) and len(left) == 120 - 21
    assert e.db.execute("SELECT COUNT(*) FROM candles WHERE symbol=? AND tf=300", (SYM,)).fetchone()[0] == 6, "the 5m candles stay when asked"
    text = gzip.open(d / "deleted" / res["archive"], "rt", encoding="utf-8").read().splitlines()
    assert text[0].startswith("symbol,tf,time") and len(text) == 22, "every removed candle is in the archive"
    assert not any(since <= t <= until for t in e.series[(SYM, 60)].rows), "the live memory was rebuilt from what is left"


def test_derived_candles_over_the_removed_stretch_go_with_it():
    e, d = filled()
    res = candleadmin.delete(e, d / "deleted", SYM, tf=60, since=T0, until=T0 + 600, with_derived=True, dry_run=False)
    kinds = {(g["tf"], g["kind"]) for g in res["groups"]}
    assert (300, "derived") in kinds and (60, "yahoo") in kinds, res["groups"]
    five = [r[0] for r in e.db.execute("SELECT ts FROM candles WHERE symbol=? AND tf=300 ORDER BY ts", (SYM,))]
    assert T0 not in five and T0 + 300 not in five and T0 + 600 not in five and T0 + 900 in five, five


def test_the_source_filter_keeps_other_sources():
    e, d = filled()
    res = candleadmin.delete(e, d / "deleted", "EURUSD_otc", tf=60, kind="contrib", dry_run=False)
    assert res["removed"] == 10
    assert e.db.execute("SELECT COUNT(*) FROM candles WHERE symbol='EURUSD_otc'").fetchone()[0] == 30


def test_everything_of_every_pair_is_refused_but_a_filtered_wide_removal_works():
    e, d = filled()
    for bad in (dict(symbol="*"), dict(symbol=""), dict(symbol="*", tf=7), dict(symbol="*", since=5, until=1)):
        try:
            candleadmin.delete(e, d / "deleted", **bad)
            raise AssertionError(f"{bad} should have been refused")
        except ValueError:
            pass
    res = candleadmin.delete(e, d / "deleted", "*", kind="yahoo", dry_run=True)
    assert res["total"] == 120 and res["refetch"]


def test_the_http_routes_are_admin_only_and_a_removal_needs_the_confirmation():
    main = _main(QXS_ENV="production", QXS_SECRET_KEY="k" * 32, QXS_ADMIN_USER=ADMIN, QXS_ADMIN_PASSWORD=ADMIN_PW)
    eng = main.engine
    for i in range(20):
        put(eng, SYM, 60, T0 + i * 60, 0.95, 0.9503, 0.9498, 0.9501, "yahoo")
    eng.db.commit()
    adm = _client(main)
    assert adm.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW}).status_code == 200
    assert adm.post("/api/users", json={"username": "anna", "password": "anna-pass-12345", "role": "trader", "must_change_pw": False}, headers=H).status_code == 200
    anna = _client(main)
    assert anna.post("/api/auth/login", json={"username": "anna", "password": "anna-pass-12345"}).status_code == 200
    for method, url, body in (("get", "/api/data/inventory", None), ("get", f"/api/data/check?symbol={SYM}", None), ("post", "/api/data/delete", {"symbol": SYM, "dry_run": False, "confirm": SYM})):
        r = anna.request(method, url, json=body, headers=H)
        assert r.status_code == 403, (url, r.status_code)
    assert any(r["symbol"] == SYM for r in adm.get("/api/data/inventory").json())
    assert adm.get(f"/api/data/check?symbol={SYM}").json()["examined"] == 20
    r = adm.post("/api/data/delete", json={"symbol": SYM, "tf": 60}, headers=H).json()
    assert r["dry_run"] is True and r["total"] == 20, "a request without dry_run only counts"
    assert adm.post("/api/data/delete", json={"symbol": SYM, "tf": 60, "dry_run": False}, headers=H).status_code == 400, "no confirmation, no removal"
    assert adm.post("/api/data/delete", json={"symbol": SYM, "tf": 60, "dry_run": False, "confirm": "AUDUSD_mkt"}, headers=H).status_code == 400
    assert adm.post("/api/data/delete", json={"symbol": "*", "dry_run": False, "confirm": "DELETE"}, headers=H).status_code == 400, "every pair without a filter is refused"
    done = adm.post("/api/data/delete", json={"symbol": SYM, "tf": 60, "dry_run": False, "confirm": SYM}, headers=H)
    assert done.status_code == 200 and done.json()["removed"] == 20, done.text
    assert eng.db.execute("SELECT COUNT(*) FROM candles WHERE symbol=?", (SYM,)).fetchone()[0] == 0
    f = adm.get("/api/data/deleted/" + done.json()["archive"])
    assert f.status_code == 200 and gzip.decompress(f.content).decode().count("\n") == 21
    assert adm.get("/api/data/deleted/..%2Fsignals.db").status_code in (404, 400) and adm.get("/api/data/deleted/x.csv.gz").status_code == 404
    assert any(a["action"] == "candles-delete" for a in main.auth.audit_tail(5)), "the removal is in the audit log"


if __name__ == "__main__":
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                fn()
                print("ok", name)
        print("all candle admin tests passed")
    finally:
        _cleanup()
