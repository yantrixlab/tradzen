"""Hosting: consistent backups of every database with retention, the nightly schedule, browser-hardening headers, request limits, the health check and the admin-only backup API."""
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import backup  # noqa: E402
from app.engine import Engine  # noqa: E402
from test_auth import ADMIN, ADMIN_PW, H, _cleanup, _client, _main  # noqa: E402


def test_backup_copies_every_database_and_keeps_the_newest():
    d = Path(tempfile.mkdtemp())
    eng = Engine(d / "m.db", seed_builtins=False)
    u = eng.user(7)
    u.db.execute("INSERT INTO settings (key, value) VALUES ('probe', '\"private\"')")
    u.db.commit()
    eng.db.execute("INSERT INTO settings (key, value) VALUES ('probe', '\"main\"')")
    eng.db.commit()
    assert backup.list_backups(d / "m.db") == []
    first = backup.run_backup(d / "m.db", keep=2)
    assert first["files"] == 2 and first["bytes"] > 0 and (d / "backups" / first["name"] / "m.db").exists() and (d / "backups" / first["name"] / "users" / "7.db").exists()
    c = sqlite3.connect(d / "backups" / first["name"] / "users" / "7.db")                 # the copy is a complete, readable database
    assert c.execute("SELECT value FROM settings WHERE key='probe'").fetchone()[0] == '"private"'
    c.close()
    eng.db.execute("UPDATE settings SET value='\"changed\"' WHERE key='probe'")
    eng.db.commit()
    c = sqlite3.connect(d / "backups" / first["name"] / "m.db")
    assert c.execute("SELECT value FROM settings WHERE key='probe'").fetchone()[0] == '"main"', "a backup is a snapshot, later changes are not in it"
    c.close()
    for i in range(3):                                                                      # three more: only the newest two stay
        (d / "backups" / f"2026010{i + 1}-000000").mkdir()
        (d / "backups" / f"2026010{i + 1}-000000" / "m.db").write_bytes(b"x")
    made = backup.run_backup(d / "m.db", keep=2)
    names = [b["name"] for b in backup.list_backups(d / "m.db")]
    assert len(names) == 2 and names[0] == made["name"] and made["removed"] >= 3
    try:
        backup.run_backup(":memory:")
        assert False
    except ValueError:
        pass
    eng.close()


def test_the_nightly_backup_runs_once_a_day_after_its_hour():
    d = Path(tempfile.mkdtemp())
    eng = Engine(d / "m.db", seed_builtins=False)
    day = datetime(2031, 5, 5, tzinfo=timezone.utc).timestamp()
    assert backup.maybe_nightly(d / "m.db", 4, 7, now=day + 3 * 3600) is None, "before the hour: nothing"
    assert backup.list_backups(d / "m.db") == []
    assert backup.maybe_nightly(d / "m.db", 0, 7) is not None, "after the hour (hour 0 = always): today's backup is made"
    assert backup.maybe_nightly(d / "m.db", 0, 7) is None, "one backup per day"
    eng.close()


def test_hosted_server_headers_limits_and_backup_api():
    main = _main(QXS_ENV="production", QXS_SECRET_KEY="k" * 32, QXS_ADMIN_USER=ADMIN, QXS_ADMIN_PASSWORD=ADMIN_PW)
    anon = _client(main)
    for path in ("/healthz", "/admin", "/api/me"):
        r = anon.get(path)
        h = r.headers
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer", path
        assert "default-src 'self'" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"] and "script-src 'self'" in h["content-security-policy"]
    assert anon.get("/healthz").json() == {"ok": True}

    # a wrong-password lockout cannot be dodged by sending a different forwarded address each time
    for i in range(5):
        assert _client(main).post("/api/auth/login", json={"username": ADMIN, "password": "nope"}, headers={"X-Forwarded-For": f"9.9.9.{i}"}).status_code == 401
    assert _client(main).post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW}, headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 429
    main.auth._fails.clear()

    adm = _client(main)
    assert adm.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW}).status_code == 200
    r = adm.post("/api/users", json={"username": "tina", "password": "tina-pass-12345", "role": "trader", "must_change_pw": False}, headers=H)
    assert r.status_code == 200
    tina = _client(main)
    assert tina.post("/api/auth/login", json={"username": "tina", "password": "tina-pass-12345"}).status_code == 200
    tok = {"Authorization": "Bearer " + tina.post("/api/tokens", json={"label": "chrome"}, headers=H).json()["secret"]}
    ext = _client(main)
    big = ext.post("/ingest", content=b"{" + b" " * 5_100_000 + b"}", headers={**tok, "Content-Type": "application/json"})
    assert big.status_code == 413, big.status_code
    codes = [ext.post("/ingest", json={"items": [], "after_seq": 0}, headers=tok).status_code for _ in range(105)]
    assert codes[0] == 200 and codes.count(200) <= 100 and codes[-1] == 429, "an account cannot flood the server"
    feeder = adm.post("/api/tokens", json={"label": "feed", "feeder": True}, headers=H).json()["secret"]
    assert all(ext.post("/ingest", json={"items": [], "after_seq": 0}, headers={"Authorization": "Bearer " + feeder}).status_code == 200 for _ in range(110)), "the price feed is not limited"

    # the backup API belongs to the admin
    assert tina.get("/api/system/backups").status_code == 403 and tina.post("/api/system/backup", headers=H).status_code == 403
    listing = adm.get("/api/system/backups").json()
    assert listing["nightly"] is True and listing["keep"] == 7 and listing["backups"] == []
    made = adm.post("/api/system/backup", headers=H)
    assert made.status_code == 200, made.text
    assert adm.get("/api/system/backups").json()["backups"][0]["name"] == made.json()["name"]
    assert any(a["action"] == "backup" for a in adm.get("/api/audit").json())


if __name__ == "__main__":
    try:
        for name, fn in list(globals().items()):
            if name.startswith("test_"):
                fn()
                print("ok", name)
    finally:
        _cleanup()
