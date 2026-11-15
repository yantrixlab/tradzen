"""Accounts: password hashing, the env admin, roles, the default-deny guard on every route, sessions, extension tokens, lockout and the local (sign-in off) mode."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import auth as A  # noqa: E402
from app.db import open_db  # noqa: E402

ADMIN, ADMIN_PW = "boss", "a-strong-pass-1"


def test_passwords():
    h = A.hash_password("correct horse battery")
    assert h.startswith("scrypt$") and A.verify_password("correct horse battery", h) and not A.verify_password("wrong", h)
    assert A.hash_password("x" * 10) != A.hash_password("x" * 10), "every hash has its own salt"
    assert not A.verify_password("anything", "garbage") and not A.verify_password("anything", "")
    assert A.password_problem("short") and A.password_problem("password") and A.password_problem("aaaaaaaaaa") and A.password_problem("longenough1") is None
    assert len(A.generate_password()) == 14


def test_the_rules_close_everything_that_is_not_listed():
    r = A.rule_for
    assert r("GET", "/admin/trades") == "public" and r("GET", "/healthz") == "public" and r("POST", "/api/auth/login") == "public"
    assert r("POST", "/ingest") == "ingest" and r("GET", "/api/events") == "any" and r("GET", "/api/me") == "any" and r("POST", "/api/tokens") == "any"
    assert all(r("GET", p) == "any" for p in ("/api/trades", "/api/trades/raw", "/api/guard", "/api/style", "/api/account")) and r("GET", "/api/strategies") == "any" and r("PUT", "/api/strategies/3") == "any" and r("POST", "/api/backtests") == "any" and r("POST", "/api/strategies/bulk") == "staff" and r("POST", "/api/strategies/4/audience") == "staff" and r("GET", "/api/library") == "any" and r("GET", "/api/sweeps") == "staff"
    for p in ("/api/users", "/api/audit", "/api/system", "/api/clear", "/api/settings", "/api/ai/status", "/api/something-new"):
        assert r("GET", p) == "admin", p
    assert r("POST", "/api/auth/login") == "public" and r("GET", "/api/auth/login") == "any"
    assert A.allowed("admin", "admin") and A.allowed("editor", "staff") and not A.allowed("editor", "admin") and not A.allowed("trader", "staff") and A.allowed("trader", "any")


def test_env_admin_and_user_management():
    db = open_db(":memory:")
    a = A.Auth(db, enabled=True, production=True, secret="s" * 32)
    a.ensure_env_admin(ADMIN, ADMIN_PW)
    admin = next(u for u in a.list_users() if u["username"] == ADMIN)
    assert admin["role"] == "admin" and admin["env_admin"]
    for bad in ("short", "admin", "changeme123"[:5]):
        try:
            A.Auth(open_db(":memory:"), enabled=True, production=True, secret="s" * 32).ensure_env_admin("x", bad)
            assert False, bad
        except RuntimeError:
            pass
    pw_before = db.execute("SELECT pw_hash FROM users WHERE id=?", (admin["id"],)).fetchone()[0]
    a.ensure_env_admin(ADMIN, ADMIN_PW)                                                      # a restart with the same password changes nothing
    assert db.execute("SELECT pw_hash FROM users WHERE id=?", (admin["id"],)).fetchone()[0] == pw_before
    a.ensure_env_admin(ADMIN, "another-strong-pass-2")                                      # a new password in .env is applied at the next start
    assert A.verify_password("another-strong-pass-2", db.execute("SELECT pw_hash FROM users WHERE id=?", (admin["id"],)).fetchone()[0])
    u, gen = a.create_user("boss", "ann", None, "trader")
    assert gen and u["role"] == "trader" and u["must_change_pw"]
    for args in (("an", "pw-long-enough", "trader"), ("ann", "pw-long-enough", "trader"), ("bob", "pw-long-enough", "wizard"), ("bob", "short", "trader")):
        try:
            a.create_user("boss", *args)
            assert False, args
        except A.AuthError:
            pass
    actor = a.get_user(admin["id"])
    for patch in ({"role": "trader"}, {"active": False}):                                    # the env admin is untouchable ...
        try:
            a.update_user(actor, admin["id"], patch)
            assert False, patch
        except A.AuthError:
            pass
    try:
        a.delete_user(actor, admin["id"])
        assert False
    except A.AuthError:
        pass
    ed, _ = a.update_user(actor, u["id"], {"role": "editor"})                                # ... anybody else can be promoted, deactivated, deleted
    assert ed["role"] == "editor"
    a.update_user(actor, u["id"], {"active": False})
    assert a.get_user(u["id"])["active"] is False
    a.delete_user(actor, u["id"])
    assert a.get_user(u["id"]) is None
    assert any(x["action"] == "user-create" for x in a.audit_tail()) and any(x["action"] == "user-delete" for x in a.audit_tail())
    try:                                                                                      # sign-in without a secret is refused
        A.Auth(open_db(":memory:"), enabled=True, secret="")
        assert False
    except RuntimeError:
        pass


def _main(**env):
    d = tempfile.mkdtemp()
    for k in ("QXS_ENV", "QXS_AUTH", "QXS_SECRET_KEY", "QXS_ADMIN_USER", "QXS_ADMIN_PASSWORD", "QXS_QUOTEX_LINK"):
        os.environ.pop(k, None)
    os.environ.update({"QXS_DB": str(Path(d) / "a.db"), "QXS_MARKETDATA": "off", "QXS_QUOTEX_LINK": "off", **env})        # most tests are not about tying accounts: test_quotex_link.py switches it on
    sys.modules.pop("app.main", None)
    import app.main as main
    return main


def _client(main):
    from fastapi.testclient import TestClient
    return TestClient(main.app, base_url="https://testserver")                              # not entered: no lifespan, one shared engine for the whole test


H = {"X-Requested-With": "qxs"}


def test_hosted_mode_default_deny_roles_sessions_and_tokens():
    main = _main(QXS_ENV="production", QXS_SECRET_KEY="k" * 32, QXS_ADMIN_USER=ADMIN, QXS_ADMIN_PASSWORD=ADMIN_PW)
    anon = _client(main)
    assert anon.get("/healthz").status_code == 200 and anon.get("/docs").status_code == 404 and anon.get("/openapi.json").status_code == 404
    home = anon.get("/", follow_redirects=False)
    assert home.status_code == 200 and "Tradzen" in home.text and 'href="/admin"' in home.text and "{{" not in home.text, "visitors who are not signed in see the landing page"
    assert "Request access" not in home.text, "no contact link configured: no request button"
    assert anon.get("/landing").status_code == 200 and anon.get("/robots.txt").text.count("Disallow") >= 3
    for path in ("/api/strategies", "/api/users", "/api/events", "/api/me", "/api/system", "/api/trades", "/api/state", "/api/signals"):
        assert anon.get(path).status_code == 401, path
    assert anon.post("/ingest", json={"items": []}).status_code == 401 and anon.post("/api/clear").status_code == 401

    import os as _os
    for value, shown in (("mailto:hello@tradzen.example", True), ("https://t.me/tradzen", True), ("javascript:alert(1)", False), ("", False), ('https://x.example/"><script>', True)):
        _os.environ["QXS_CONTACT"] = value
        page = anon.get("/").text
        assert ("Request access" in page) is shown, value
        assert "javascript:" not in page and '"><script>' not in page, "the contact link is escaped and only mailto: / https: are accepted"
    _os.environ.pop("QXS_CONTACT", None)
    adm = _client(main)
    assert adm.post("/api/auth/login", json={"username": ADMIN, "password": "wrong"}).status_code == 401
    r = adm.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW})
    assert r.status_code == 200 and r.json()["user"]["role"] == "admin"
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "secure" in set_cookie and "samesite=lax" in set_cookie
    signed_in_home = adm.get("/", follow_redirects=False)
    assert signed_in_home.status_code == 302 and signed_in_home.headers["location"] == "/admin", "a signed-in visitor goes straight to the dashboard"
    assert adm.get("/api/me").json()["user"]["username"] == ADMIN and adm.get("/api/strategies").status_code == 200 and adm.get("/api/users").status_code == 200
    assert adm.post("/api/users", json={"username": "ed", "password": "editor-pass-1", "role": "editor", "must_change_pw": False}).status_code == 403, "changes need the request header (CSRF)"

    def make(name, role):
        r = adm.post("/api/users", json={"username": name, "password": f"{name}-pass-12345", "role": role, "must_change_pw": False}, headers=H)
        assert r.status_code == 200, r.text
        c = _client(main)
        assert c.post("/api/auth/login", json={"username": name, "password": f"{name}-pass-12345"}).status_code == 200
        return c, r.json()["user"]["id"]

    ed, ed_id = make("edie", "editor")
    tr, tr_id = make("tom", "trader")
    assert ed.get("/api/strategies").status_code == 200 and ed.get("/api/users").status_code == 403 and ed.get("/api/system").status_code == 403 and ed.post("/api/clear", headers=H).status_code == 403
    assert tr.get("/api/sweeps").status_code == 403 and tr.get("/api/state").status_code == 403 and tr.get("/api/users").status_code == 403 and tr.get("/api/trades").status_code == 200
    assert tr.get("/api/events").status_code == 200 and tr.get("/api/me").json()["user"]["role"] == "trader"
    assert adm.post("/api/users", json={"username": "xavier1", "password": "xavier-pass-77"}, headers=H).json()["user"]["role"] == "trader"      # default role

    # a role change takes effect on the very next request, and deactivating signs the person out
    assert adm.patch(f"/api/users/{tr_id}", json={"role": "editor"}, headers=H).status_code == 200 and tr.get("/api/sweeps").status_code == 200
    assert adm.patch(f"/api/users/{tr_id}", json={"role": "trader"}, headers=H).status_code == 200
    assert tr.get("/api/me").status_code == 200 and tr.get("/api/sweeps").status_code == 403, "demoted: the same session, fewer rights"
    assert adm.patch(f"/api/users/{ed_id}", json={"active": False}, headers=H).status_code == 200 and ed.get("/api/me").status_code == 401
    assert adm.patch(f"/api/users/{ed_id}", json={"active": True}, headers=H).status_code == 200
    assert _client(main).post("/api/auth/login", json={"username": "edie", "password": "edie-pass-12345"}).status_code == 200
    boss = next(u for u in adm.get("/api/users").json() if u["username"] == ADMIN)
    assert adm.patch(f"/api/users/{boss['id']}", json={"role": "trader"}, headers=H).status_code == 400 and adm.delete(f"/api/users/{boss['id']}", headers=H).status_code == 400

    # extension tokens: a trader's token only receives alerts (its items are ignored), only the admin can make a feeder token, and a cookie never works on /ingest
    tr, _ = make("tom2", "trader")
    t = tr.post("/api/tokens", json={"label": "chrome"}, headers=H).json()
    assert t["secret"].startswith("qxs_") and tr.post("/api/tokens", json={"feeder": True}, headers=H).status_code == 403
    ext = _client(main)
    bearer = {"Authorization": f"Bearer {t['secret']}"}
    n0 = main.engine.ingest_stats["batches"]
    r = ext.post("/ingest", json={"items": [{"t": 1, "s": "EURUSD", "p": 1.1}, {"k": "o", "n": "orders/open", "d": {"asset": "X"}}], "after_seq": 0}, headers=bearer)
    assert r.status_code == 200 and r.json()["ticks"] == 0, "a trader token cannot feed the market"
    assert main.engine.ingest_stats["batches"] == n0 + 1
    assert ext.get("/api/events", headers=bearer).status_code == 200 and ext.get("/api/strategies", headers=bearer).status_code == 401, "a token only works where the extension needs it"
    assert tr.post("/ingest", json={"items": []}, headers=H).status_code == 401, "a browser session is not an extension token"
    feeder = adm.post("/api/tokens", json={"label": "my feed", "feeder": True}, headers=H).json()
    fr = ext.post("/ingest", json={"items": [{"t": 1, "s": "EURUSD", "p": 1.1, "ts": 1_800_000_000}], "after_seq": 0}, headers={"Authorization": f"Bearer {feeder['secret']}"})
    assert fr.status_code == 200 and fr.json()["ticks"] >= 0
    assert tr.delete(f"/api/tokens/{t['token']['id']}", headers=H).status_code == 200 and ext.post("/ingest", json={"items": []}, headers=bearer).status_code == 401, "a revoked token stops working"

    # a new account must choose its own password before anything else
    r = adm.post("/api/users", json={"username": "newbie", "password": "temp-pass-123"}, headers=H)
    nb = _client(main)
    assert nb.post("/api/auth/login", json={"username": "newbie", "password": "temp-pass-123"}).status_code == 200
    assert nb.get("/api/events").status_code == 403 and nb.get("/api/me").json()["user"]["must_change_pw"] is True
    assert nb.post("/api/auth/password", json={"old": "temp-pass-123", "new": "mine-now-4567"}, headers=H).status_code == 200 and nb.get("/api/events").status_code == 200
    assert nb.post("/api/auth/logout", headers=H).status_code == 200 and nb.get("/api/me").status_code == 401

    # five wrong passwords lock the sign-in for a while, even for the right one
    for _ in range(5):
        assert _client(main).post("/api/auth/login", json={"username": "tom", "password": "nope"}).status_code == 401
    assert _client(main).post("/api/auth/login", json={"username": "tom", "password": "tom-pass-12345"}).status_code == 429
    assert any(x["action"] == "login-failed" for x in adm.get("/api/audit").json())


def test_private_alerts_never_go_to_other_users():
    from app.routes_auth import event_visible as v
    tr, ed, adm = {"id": 7, "role": "trader"}, {"id": 8, "role": "editor"}, {"id": 1, "role": "admin"}
    assert not v(tr, {"kind": "signal", "id": 7, "strategy_id": 3}) and v(ed, {"kind": "signal", "id": 7, "strategy_id": 3}), "without an audience rule nothing is shared with plain users"
    assert v(tr, {"kind": "guard", "id": "g1", "to": 7}) and not v(ed, {"kind": "guard", "id": "g1", "to": 7})              # a private alert only reaches its owner
    assert not v(adm, {"kind": "guard", "id": "g1", "to": 7}), "not even the admin sees another user's guard alerts"
    assert v(adm, {"kind": "signal", "id": "st4", "to": 1}) and not v(tr, {"kind": "signal", "id": "st4", "to": 1})
    assert v(None, {"kind": "signal", "id": 3})


def test_local_mode_is_open_to_the_machine_only():
    main = _main()
    c = _client(main)
    assert c.get("/api/strategies").status_code == 200 and c.get("/api/users").status_code == 200 and c.get("/api/me").json()["user"]["role"] == "admin"
    assert c.get("/api/strategies", headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 403, "behind a proxy, sign-in must be on"
    assert c.post("/api/auth/login", json={"username": "a", "password": "b"}).status_code == 400 and c.post("/api/tokens", json={}, headers=H).status_code == 400
    assert c.post("/ingest", json={"items": [], "after_seq": 0}).json()["ok"] is True
    try:
        _main(QXS_ENV="production", QXS_AUTH="off")
        assert False
    except RuntimeError:
        pass


def _cleanup():
    sys.modules.pop("app.main", None)
    for k in ("QXS_DB", "QXS_MARKETDATA", "QXS_ENV", "QXS_AUTH", "QXS_SECRET_KEY", "QXS_ADMIN_USER", "QXS_ADMIN_PASSWORD", "QXS_QUOTEX_LINK"):
        os.environ.pop(k, None)


if __name__ == "__main__":
    try:
        for name, fn in list(globals().items()):
            if name.startswith("test_"):
                fn()
                print("ok", name)
    finally:
        _cleanup()
