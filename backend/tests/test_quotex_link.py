"""One Tradzen account <-> one Quotex account: the binding, what the server accepts from a tab that is on another Quotex account, the admin's unlink,
and the extension's own sign-in (username + password -> its own revocable token)."""
import hashlib
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from test_auth import ADMIN, ADMIN_PW, H, _cleanup, _client, _main  # noqa: E402
from test_tenancy import balance, closed  # noqa: E402


def qh(uid: str) -> str:
    """What the extension sends: SHA-256 of the account number with the fixed prefix (see extension/background.js)."""
    return hashlib.sha256(("tradzen-qx-v1:" + uid).encode()).hexdigest()


def test_qx_check_binds_once_and_keeps_only_a_keyed_hash():
    from app import db as dbmod
    from app.auth import Auth
    conn = dbmod.open_db(":memory:")
    a = Auth(conn, enabled=True, secret="s" * 32)
    a.create_user("t", "anna", "pass-anna-123", "trader", False)
    a.create_user("t", "benny", "pass-benny-123", "trader", False)
    anna, ben = (conn.execute("SELECT id FROM users WHERE username=?", (n,)).fetchone()[0] for n in ("anna", "benny"))
    assert a.qx_check(anna, None) == "unknown" and a.qx_check(anna, "not-a-hash") == "unknown" and a.qx_check(anna, "A" * 64) == "unknown", "only a real SHA-256 counts"
    assert a.qx_check(anna, qh("4711234"), "4711234") == "bound"
    assert a.qx_check(anna, qh("4711234")) == "ok" and a.qx_check(anna, qh("999")) == "mismatch"
    assert a.qx_check(ben, qh("4711234")) == "taken", "a Quotex account belongs to one Tradzen user only"
    assert a.qx_check(ben, qh("555"), "555") == "bound"
    row = conn.execute("SELECT qx_hash, qx_hint FROM users WHERE id=?", (anna,)).fetchone()
    assert row[1] == "234" and row[0] != qh("4711234") and "4711234" not in str(row) and len(row[0]) == 64, "the stored value is a keyed hash, never the number or the plain hash"
    assert a.get_user(anna)["qx_linked"] is True and a.get_user(anna)["qx_hint"] == "234"
    admin = {"username": "boss", "id": 99, "role": "admin"}
    a.qx_unlink(admin, anna)
    assert a.get_user(anna)["qx_linked"] is False and a.qx_check(anna, qh("999"), "999") == "bound", "after an unlink the account can take another Quotex account"
    assert a.qx_check(ben, qh("4711234")) == "mismatch" and a.qx_check(ben, qh("555")) == "ok"
    off = Auth(conn, enabled=True, secret="s" * 32, link_quotex=False)
    assert off.qx_check(ben, qh("anything")) == "ok" and off.qx_check(ben, None) == "ok", "switched off: no checks"
    # two racing first links for the same Quotex account: the unique index lets exactly one win
    cur = conn.cursor()
    cur.execute("UPDATE users SET qx_hash=NULL WHERE id=?", (ben,))
    key = a._h("qx:" + qh("999"))
    try:
        cur.execute("UPDATE users SET qx_hash=? WHERE id=?", (key, ben))
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised
    conn.close()


def test_hosted_flow_extension_sign_in_wrong_account_and_unlink():
    main = _main(QXS_ENV="production", QXS_SECRET_KEY="k" * 32, QXS_ADMIN_USER=ADMIN, QXS_ADMIN_PASSWORD=ADMIN_PW, QXS_QUOTEX_LINK="on")
    eng = main.engine
    adm = _client(main)
    assert adm.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW}).status_code == 200
    for name, force in (("anna", False), ("benny", False), ("newbie", True)):
        r = adm.post("/api/users", json={"username": name, "password": f"{name}-pass-12345", "role": "trader", "must_change_pw": force}, headers=H)
        assert r.status_code == 200, r.text
    ids = {u["username"]: u["id"] for u in adm.get("/api/users").json()}
    ext = _client(main)

    def sign_in(name, pw=None, device="dev1", feeder=False):
        return ext.post("/api/auth/extension-login", json={"username": name, "password": pw or f"{name}-pass-12345", "device": device, "feeder": feeder})

    # ---- the extension's own sign-in
    assert sign_in("anna", "wrong").status_code == 401
    assert sign_in("newbie").status_code == 403 and "choose your own password" in sign_in("newbie").json()["detail"], "a new account sets its password on the website first"
    assert sign_in("anna", feeder=True).status_code == 403, "only an admin can make a browser the price feed"
    r = sign_in("anna")
    assert r.status_code == 200 and r.json()["token"].startswith("qxs_") and r.json()["user"]["username"] == "anna" and r.json()["feeder"] is False
    tok1 = r.json()["token"]
    again = sign_in("anna").json()["token"]
    assert again != tok1
    assert ext.post("/ingest", json={"items": []}, headers={"Authorization": f"Bearer {tok1}"}).status_code == 401, "signing in again on the same browser replaces its token"
    a_ext = {"Authorization": f"Bearer {again}"}
    b_ext = {"Authorization": "Bearer " + sign_in("benny", device="dev2").json()["token"]}
    # ---- before the account number is known: nothing private is stored, alerts still arrive
    n0 = ext.post("/ingest", json={"items": [closed("D1", "EURUSD", 0.9), balance(50)], "after_seq": 0}, headers=a_ext).json()
    assert n0["qx"]["state"] == "unknown" and n0["as"]["username"] == "anna"
    anna_c = _client(main)
    # anna's website session needs her password: log her in
    assert anna_c.post("/api/auth/login", json={"username": "anna", "password": "anna-pass-12345"}).status_code == 200
    assert anna_c.get("/api/trades").json()["total"] == 0, "held back until the Quotex account is known"

    # ---- the first account number links the account; its trades and balance are stored
    r = ext.post("/ingest", json={"items": [closed("D1", "EURUSD", 0.9), balance(50)], "after_seq": 0, "qx": {"h": qh("4711234"), "hint": "234"}}, headers=a_ext).json()
    assert r["qx"] == {"state": "bound", "hint": "234"}
    assert anna_c.get("/api/trades").json()["total"] == 1 and anna_c.get("/api/account").json()["live"] == 50
    me = anna_c.get("/api/me").json()
    assert me["user"]["qx_linked"] is True and me["user"]["qx_hint"] == "234" and me["quotex_link"] is True
    r = ext.post("/ingest", json={"items": [], "after_seq": 0, "qx": {"h": qh("4711234")}}, headers=a_ext).json()
    assert r["qx"]["state"] == "ok"

    # ---- the tab is on another Quotex account: nothing private is stored, no alerts
    eng.seq += 1
    eng.strategy_meta[3] = {"owner": None, "audience": "everyone", "status": "live"}
    eng.notify_log.append({"seq": eng.seq, "kind": "signal", "aud": "everyone", "id": 501, "strategy_id": 3, "t": time.time()})
    after = eng.seq - 1
    wrong = ext.post("/ingest", json={"items": [closed("D2", "GBPUSD", -1.0, t0=time.time() - 300), balance(7)], "after_seq": after, "qx": {"h": qh("999"), "hint": "999"}}, headers=a_ext).json()
    assert wrong["qx"] == {"state": "mismatch", "hint": "234"} and wrong["events"] == [], "the wrong account gets no alerts"
    assert anna_c.get("/api/trades").json()["total"] == 1 and anna_c.get("/api/account").json()["live"] == 50, "and nothing of it is stored"
    right = ext.post("/ingest", json={"items": [], "after_seq": after, "qx": {"h": qh("4711234")}}, headers=a_ext).json()
    assert [e["kind"] for e in right["events"]] == ["signal"], "back on the linked account the alerts flow again"

    # ---- a Quotex account belongs to one Tradzen user
    r = ext.post("/ingest", json={"items": [closed("X1", "EURUSD", 2.0)], "after_seq": 0, "qx": {"h": qh("4711234")}}, headers=b_ext).json()
    assert r["qx"]["state"] == "taken" and r["events"] == []
    benny_c = _client(main)
    assert benny_c.post("/api/auth/login", json={"username": "benny", "password": "benny-pass-12345"}).status_code == 200
    assert benny_c.get("/api/trades").json()["total"] == 0

    # ---- only an admin unlinks; then the account can take another Quotex account
    assert anna_c.delete(f"/api/users/{ids['anna']}/quotex", headers=H).status_code == 403
    assert adm.delete(f"/api/users/{ids['anna']}/quotex", headers=H).status_code == 200
    assert anna_c.get("/api/me").json()["user"]["qx_linked"] is False
    r = ext.post("/ingest", json={"items": [closed("D3", "GBPUSD", -1.0, t0=time.time() - 200)], "after_seq": 0, "qx": {"h": qh("999"), "hint": "999"}}, headers=a_ext).json()
    assert r["qx"] == {"state": "bound", "hint": "999"} and anna_c.get("/api/trades").json()["total"] == 2
    assert any(a["action"] == "quotex-unlinked" for a in adm.get("/api/audit").json())
    users = {u["username"]: u for u in adm.get("/api/users").json()}
    assert users["anna"]["qx_hint"] == "999" and users["benny"]["qx_linked"] is False

    # ---- the price feed: an admin may make a browser the feed; prices are accepted whichever Quotex account the tab is on
    f = sign_in(ADMIN, ADMIN_PW, device="feed1", feeder=True)
    assert f.status_code == 200 and f.json()["feeder"] is True
    n = eng.ingest_stats["ticks"]
    r = ext.post("/ingest", json={"items": [{"k": "t", "d": [["EURUSD", time.time(), 1.1]]}, closed("A1", "EURUSD", 1.0)], "after_seq": 0}, headers={"Authorization": "Bearer " + f.json()["token"]}).json()
    assert r["feeder"] is True and eng.ingest_stats["ticks"] == n + 1 and r["qx"]["state"] == "unknown"
    assert adm.get("/api/trades").json()["total"] == 0, "the admin's own trades are held back until the admin's Quotex account is known too"

    # ---- the same lock-out as the website (last, because it pauses sign-in for that name)
    for _ in range(6):
        sign_in("benny", "wrong")
    assert sign_in("benny").status_code == 429


def test_local_server_needs_no_link():
    main = _main()                                                   # sign-in off: the machine is the admin
    lc = _client(main)
    r = lc.post("/ingest", json={"items": [closed("L1", "EURUSD", 1.0)], "after_seq": 0})
    assert r.status_code == 200 and "qx" not in r.json(), "no link state on a server that does not tie accounts"
    assert lc.get("/api/trades").json()["total"] == 1


if __name__ == "__main__":
    try:
        for name, fn in list(globals().items()):
            if name.startswith("test_"):
                fn()
                print("ok", name)
    finally:
        _cleanup()
