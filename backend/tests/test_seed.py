"""Moving collected data to a new server: a snapshot archive, loading it on the first start only, safe unpacking, and merging market data later without touching accounts."""
import io
import json
import sqlite3
import sys
import tarfile
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import seed as S  # noqa: E402
from app.db import open_db  # noqa: E402
from app.engine import Engine  # noqa: E402
from test_auth import ADMIN, ADMIN_PW, H, _cleanup, _client, _main  # noqa: E402


def source(d: Path, candles: int = 500) -> Path:
    """A 'local' installation: candles, a custom strategy, the admin's own trades and an AI file next to the database."""
    eng = Engine(d / "signals.db", seed_builtins=False)
    for sym in ("EURUSD_otc", "AUDCAD_otc"):
        eng.db.executemany("INSERT INTO candles (symbol, tf, ts, o, h, l, c, ticks, source) VALUES (?,?,?,?,?,?,?,?,?)",
                           [(sym, 60, 1_700_000_000 + 60 * i, 1.0, 1.1, 0.9, 1.05, 30, "live") for i in range(candles)])
    eng.db.execute("INSERT INTO sentiment (symbol, ts, pct) VALUES ('EURUSD_otc', 1700000000, 55)")
    eng.db.execute("INSERT INTO instruments (symbol, name, market, category, payout, open, first_seen, last_seen) VALUES ('EURUSD_otc', 'EUR/USD (OTC)', 'OTC', 'x', 88, 1, 1, 1)")
    eng.db.execute("INSERT INTO settings (key, value) VALUES ('probe', '\"mine\"')")
    eng.db.commit()
    eng.close()
    (d / "ai").mkdir()
    (d / "ai" / "model.json").write_text('{"trained": true}')
    (d / "backups").mkdir()
    (d / "backups" / "ignored.db").write_bytes(b"x")                      # backups are never part of a seed
    return d / "signals.db"


def test_make_and_apply_a_seed_on_the_first_start_only():
    d = Path(tempfile.mkdtemp())
    db = source(d / "local") if (d / "local").mkdir() is None else None
    out = d / "seed" / "tradzen-seed.tar.gz"
    info = S.make_seed(db, out)
    assert out.exists() and info["counts"]["candles"] == 1000 and info["counts"]["schema_version"] == S.latest_schema() and info["files"] == ["files/ai/model.json"]
    with tarfile.open(out) as t:
        assert sorted(t.getnames()) == ["files/ai/model.json", "seed.json", "signals.db"]

    new = d / "server" / "data" / "signals.db"
    res = S.apply_seed(out, new)                                               # a new server: no database yet
    assert res and res["counts"]["candles"] == 1000 and res["files"] == 1
    assert new.exists() and (new.parent / "ai" / "model.json").read_text() == '{"trained": true}' and not (new.parent / "backups").exists()
    c = sqlite3.connect(new)
    assert c.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 1000 and c.execute("SELECT value FROM settings WHERE key='probe'").fetchone()[0] == '"mine"'
    c.close()
    assert not [p for p in new.parent.iterdir() if p.name.startswith(".seed")], "no temporary files left behind"
    c = sqlite3.connect(new)
    c.execute("INSERT INTO settings (key, value) VALUES ('later', '1')")
    c.commit()
    c.close()
    assert S.apply_seed(out, new) is None, "a database exists now: the seed is ignored"
    c = sqlite3.connect(new)
    assert c.execute("SELECT COUNT(*) FROM settings WHERE key='later'").fetchone()[0] == 1
    c.close()
    e = Engine(new, seed_builtins=False)                                         # and the engine opens it like any database
    assert len(e.instruments) == 1 and e.db.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 1000
    e.close()


def test_a_bad_or_dangerous_archive_is_refused():
    d = Path(tempfile.mkdtemp())
    db = source(d / "local") if (d / "local").mkdir() is None else None
    good = d / "good.tar.gz"
    S.make_seed(db, good)

    def archive(name, members):
        with tarfile.open(d / name, "w:gz") as t:
            for n, data in members:
                ti = tarfile.TarInfo(n)
                ti.size = len(data)
                t.addfile(ti, io.BytesIO(data))
        return d / name

    manifest = json.dumps({"format": S.FORMAT, "files": []}).encode()
    for members in ([("../evil.txt", b"x"), ("seed.json", manifest), ("signals.db", b"x")], [("/etc/passwd", b"x")], [("files/other/x", b"x"), ("seed.json", manifest)],
                    [("seed.json", manifest)], [("seed.json", json.dumps({"format": 99}).encode()), ("signals.db", b"x")]):
        bad = archive("bad.tar.gz", members)
        try:
            S.apply_seed(bad, d / "x" / "signals.db")
            assert False, members
        except S.SeedError:
            pass
        assert not (d / "x" / "signals.db").exists()
    try:
        S.apply_seed(d / "missing.tar.gz", d / "y" / "signals.db")
        assert False
    except S.SeedError:
        pass
    notdb = d / "notdb.db"
    c = sqlite3.connect(notdb)
    c.execute("CREATE TABLE t (a)")
    c.commit()
    c.close()
    try:
        S.apply_seed(notdb, d / "z" / "signals.db")
        assert False
    except S.SeedError:
        pass
    newer = d / "newer.db"                                                       # a database from a newer version of the app
    c = open_db(newer)
    c.execute("INSERT INTO schema_version (version, applied) VALUES (?, ?)", (S.latest_schema() + 1, time.time()))
    c.commit()
    c.close()
    try:
        S.apply_seed(newer, d / "w" / "signals.db")
        assert False
    except S.SeedError as e:
        assert "newer" in str(e)


def test_merge_adds_market_data_and_leaves_everything_else_alone():
    d = Path(tempfile.mkdtemp())
    db = source(d / "local", candles=500) if (d / "local").mkdir() is None else None
    seed = d / "seed.tar.gz"
    S.make_seed(db, seed)
    # a server that already runs: a few of the same candles (with its own values), more recent ones, accounts and a strategy of its own
    (d / "srv").mkdir()
    eng = Engine(d / "srv" / "signals.db", seed_builtins=False)
    eng.db.executemany("INSERT INTO candles (symbol, tf, ts, o, h, l, c, ticks, source) VALUES (?,?,?,?,?,?,?,?,?)",
                       [("EURUSD_otc", 60, 1_700_000_000 + 60 * i, 9.0, 9.0, 9.0, 9.0, 7, "server") for i in range(10)] +
                       [("EURUSD_otc", 60, 1_700_000_000 + 60 * (600 + i), 1.0, 1.0, 1.0, 1.0, 7, "server") for i in range(5)])
    eng.db.execute("INSERT INTO users (username, pw_hash, role, active, created) VALUES ('boss', 'x', 'admin', 1, 1)")
    eng.db.execute("INSERT INTO instruments (symbol, name, market, category, payout, open, first_seen, last_seen) VALUES ('EURUSD_otc', 'server name', 'OTC', 'x', 91, 1, 1, 2)")
    eng.db.commit()
    eng.close()
    added = S.merge_market(seed, d / "srv" / "signals.db")
    assert added == {"candles": 1000 - 10, "sentiment": 1, "instruments": 0}, added             # 10 candles the server already had are not added again
    c = sqlite3.connect(d / "srv" / "signals.db")
    assert c.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 1000 + 5
    assert c.execute("SELECT c, source FROM candles WHERE symbol='EURUSD_otc' AND ts=1700000000").fetchone() == (9.0, "server"), "the server's own row wins"
    assert c.execute("SELECT payout FROM instruments WHERE symbol='EURUSD_otc'").fetchone()[0] == 91
    assert c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1 and c.execute("SELECT COUNT(*) FROM settings WHERE key='probe'").fetchone()[0] == 0, "nothing but market data is touched"
    c.close()
    again = S.merge_market(seed, d / "srv" / "signals.db")
    assert again == {"candles": 0, "sentiment": 0, "instruments": 0}, "merging twice adds nothing"
    raw = S.merge_market(db, d / "srv" / "signals.db")                                          # a plain database file works as a source too
    assert raw["candles"] == 0


def test_command_line():
    d = Path(tempfile.mkdtemp())
    db = source(d / "local") if (d / "local").mkdir() is None else None
    assert S._main(["make", "--db", str(db), "--out", str(d / "o.tar.gz")]) == 0 and (d / "o.tar.gz").exists()
    assert S._main(["make", "--db", str(d / "nope.db"), "--out", str(d / "p.tar.gz")]) == 1
    (d / "srv").mkdir()
    open_db(d / "srv" / "signals.db").close()
    assert S._main(["merge", str(d / "o.tar.gz"), "--db", str(d / "srv" / "signals.db")]) == 0
    c = sqlite3.connect(d / "srv" / "signals.db")
    assert c.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 1000
    c.close()


def test_a_fresh_server_can_load_a_waiting_seed_and_keeps_the_old_database():
    d = Path(tempfile.mkdtemp())
    db = source(d / "local") if (d / "local").mkdir() is None else None
    seed = d / "seed.tar.gz"
    S.make_seed(db, seed)
    srv = d / "srv" / "signals.db"
    (d / "srv").mkdir()
    c = open_db(srv)
    c.execute("INSERT INTO users (username, pw_hash, role, active, env_admin, created) VALUES ('boss', 'x', 'admin', 1, 1, 1)")
    c.execute("INSERT INTO settings (key, value) VALUES ('onlyhere', '1')")
    c.commit()
    c.close()
    assert S.is_fresh(srv) and S.is_fresh(d / "nothing.db")
    assert S.apply_pending(srv) is None, "nothing waiting"
    (d / "srv" / "seed").mkdir()
    (d / "srv" / "seed" / S.PENDING_NAME).write_bytes(seed.read_bytes())
    res = S.apply_pending(srv)
    assert res and res["counts"]["candles"] == 1000
    c = sqlite3.connect(srv)
    assert c.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 1000 and c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    c.close()
    kept = [p for p in (d / "srv").iterdir() if ".before-seed-" in p.name and not p.name.endswith(("-wal", "-shm"))]
    assert len(kept) == 1 and sqlite3.connect(kept[0]).execute("SELECT value FROM settings WHERE key='onlyhere'").fetchone()[0] == "1", "the old database is set aside, not deleted"
    assert not (d / "srv" / "seed" / S.PENDING_NAME).exists() and list((d / "srv" / "seed").glob("loaded-*.tar.gz"))

    # not fresh: a second person has an account -> nothing is replaced and the file waits
    c = sqlite3.connect(srv)
    c.executemany("INSERT INTO users (username, pw_hash, role, active, created) VALUES (?, 'x', 'trader', 1, 1)", [("a1",), ("b2",)])
    c.commit()
    c.close()
    assert not S.is_fresh(srv)
    (d / "srv" / "seed" / S.PENDING_NAME).write_bytes(seed.read_bytes())
    assert S.apply_pending(srv) is None and (d / "srv" / "seed" / S.PENDING_NAME).exists()
    (d / "srv" / "users").mkdir()                                                         # a private database also means "not fresh"
    (d / "srv" / "users" / "5.db").write_bytes(b"x")
    c = sqlite3.connect(srv)
    c.execute("DELETE FROM users")
    c.commit()
    c.close()
    assert not S.is_fresh(srv)
    (d / "srv" / "users" / "5.db").unlink()
    assert S.is_fresh(srv)

    # a seed that cannot be loaded puts the old database back exactly as it was
    (d / "srv" / "seed" / S.PENDING_NAME).write_bytes(b"this is not an archive")
    try:
        S.apply_pending(srv)
        assert False
    except S.SeedError:
        pass
    assert srv.exists() and sqlite3.connect(srv).execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 1000


def test_upload_merge_and_replace_through_the_admin_api():
    main = _main(QXS_ENV="production", QXS_SECRET_KEY="k" * 32, QXS_ADMIN_USER=ADMIN, QXS_ADMIN_PASSWORD=ADMIN_PW)
    d = Path(tempfile.mkdtemp())
    db = source(d / "local") if (d / "local").mkdir() is None else None
    seed = d / "seed.tar.gz"
    S.make_seed(db, seed)
    blob = seed.read_bytes()
    adm = _client(main)
    assert adm.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW}).status_code == 200
    r = adm.post("/api/users", json={"username": "tina", "password": "tina-pass-12345", "role": "trader", "must_change_pw": False}, headers=H)
    tina = _client(main)
    assert tina.post("/api/auth/login", json={"username": "tina", "password": "tina-pass-12345"}).status_code == 200
    for call in (lambda c: c.get("/api/system/seed"), lambda c: c.put("/api/system/seed", content=blob, headers=H), lambda c: c.post("/api/system/seed/merge", headers=H),
                 lambda c: c.post("/api/system/seed/replace", headers=H)):
        assert call(tina).status_code == 403, "only the admin can import data"
    st = adm.get("/api/system/seed").json()
    assert st["pending"] is None and st["server"]["candles"] == 0 and st["fresh"] is False, st                       # tina has an account: replacing is not allowed

    assert adm.put("/api/system/seed", content=b"definitely not a seed", headers=H).status_code == 400
    assert adm.get("/api/system/seed").json()["pending"] is None, "a refused upload leaves nothing behind"
    old = main.MAX_UPLOAD
    main.MAX_UPLOAD = 1000
    assert adm.put("/api/system/seed", content=blob, headers=H).status_code == 413
    main.MAX_UPLOAD = old
    up = adm.put("/api/system/seed", content=blob, headers=H)
    assert up.status_code == 200 and up.json()["counts"]["candles"] == 1000 and up.json()["bytes"] == len(blob)
    st = adm.get("/api/system/seed").json()
    assert st["pending"]["counts"]["candles"] == 1000 and st["pending"]["counts"]["schema_version"] == S.latest_schema()
    assert any(a["action"] == "seed-upload" for a in adm.get("/api/audit").json())

    # merge: runs in the background, adds only market data
    assert adm.post("/api/system/seed/merge", headers=H).status_code == 200
    for _ in range(100):
        m = adm.get("/api/system/seed").json()["merge"]
        if m["state"] != "running":
            break
        time.sleep(0.1)
    assert m["state"] == "done" and m["result"] == {"candles": 1000, "sentiment": 1, "instruments": 1}, m
    assert adm.get("/api/system/seed").json()["server"]["candles"] == 1000 and adm.get("/api/system/seed").json()["server"]["users"] == 2
    assert adm.post("/api/system/seed/replace", headers=H).status_code == 409, "tina has an account: replacing would delete it"

    # once nobody but the admin has an account, the whole seed can be loaded; the server restarts to do it
    assert adm.delete(f"/api/users/{r.json()['user']['id']}", headers=H).status_code == 200
    calls = []
    main._restart_hook = lambda: calls.append("restart")
    rep = adm.post("/api/system/seed/replace", headers=H)
    assert rep.status_code == 200 and rep.json() == {"restarting": True}
    import asyncio
    main._restart_hook()                                                                  # the timer would do this a moment later
    assert calls == ["restart"]
    assert adm.delete("/api/system/seed", headers=H).status_code == 200 and adm.get("/api/system/seed").json()["pending"] is None
    assert adm.post("/api/system/seed/merge", headers=H).status_code == 400 and adm.post("/api/system/seed/replace", headers=H).status_code == 400


if __name__ == "__main__":
    try:
        for name, fn in list(globals().items()):
            if name.startswith("test_"):
                fn()
                print("ok", name)
    finally:
        _cleanup()
