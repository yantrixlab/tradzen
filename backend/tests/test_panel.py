"""The extension's side panel signs itself in: a one-time ticket from the extension's token, a limited session for the page inside the panel, and the frame rules."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from test_auth import ADMIN, ADMIN_PW, H, _cleanup, _client, _main  # noqa: E402


def test_the_panel_signs_in_from_the_extension_token():
    main = _main(QXS_ENV="production", QXS_SECRET_KEY="k" * 32, QXS_ADMIN_USER=ADMIN, QXS_ADMIN_PASSWORD=ADMIN_PW)
    ext, page = _client(main), _client(main)
    tok = ext.post("/api/auth/extension-login", json={"username": ADMIN, "password": ADMIN_PW, "device": "d", "feeder": False}).json()["token"]
    bearer = {"Authorization": "Bearer " + tok}
    # only a token asks for a ticket
    assert ext.post("/api/auth/panel-ticket").status_code == 401
    ticket = ext.post("/api/auth/panel-ticket", headers=bearer).json()["ticket"]
    assert len(ticket) > 20
    # the page trades it, once, for a session it sends as a header (no cookie is set)
    r = page.post("/api/auth/panel-session", json={"ticket": ticket})
    assert r.status_code == 200 and not r.cookies and r.json()["expires_in"] == 12 * 3600
    sess = {"Authorization": "Session " + r.json()["token"], **H}
    assert page.post("/api/auth/panel-session", json={"ticket": ticket}).status_code == 401, "a ticket works once"
    assert page.post("/api/auth/panel-session", json={"ticket": "nonsense"}).status_code == 401
    # what it may do: the live board
    assert page.get("/api/me", headers=sess).json()["user"]["username"] == ADMIN
    assert page.get("/api/state", headers=sess).status_code == 200 and page.get("/api/strategies", headers=sess).status_code == 200
    assert page.post("/api/strategies/pause-all", json={}, headers=sess).status_code == 200
    assert page.post("/api/strategies/resume-all", json={}, headers=sess).status_code == 200
    # what it may not, although the account is an admin
    for method, path in (("GET", "/api/users"), ("GET", "/api/audit"), ("GET", "/api/system"), ("GET", "/api/tokens"), ("POST", "/api/tokens"), ("POST", "/api/auth/password"),
                         ("POST", "/api/users"), ("POST", "/api/clear"), ("GET", "/api/data/export"), ("POST", "/api/auth/panel-ticket")):
        assert page.request(method, path, headers=sess, json={} if method == "POST" else None).status_code in (403, 401), (method, path)
    assert page.post("/api/strategies/pause-all", json={}, headers={"Authorization": sess["Authorization"]}).status_code == 403, "changes need the request header"
    # a panel session is not a cookie session and a cookie session is not a panel session
    assert page.get("/api/state", headers={"Authorization": "Session nonsense"}).status_code == 401
    plain = _client(main)
    plain.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW})
    cookie_token = plain.cookies.get("qxs_session")
    assert page.get("/api/me", headers={"Authorization": "Session " + cookie_token}).status_code == 401, "only a panel session may be sent as a header"
    # a panel session ends with the account
    assert page.get("/api/me", headers=sess).status_code == 200
    main.auth.db.execute("UPDATE sessions SET expires=? WHERE ua LIKE '%'", (time.time() - 1,))
    main.auth.db.commit()
    assert page.get("/api/me", headers=sess).status_code == 401
    # a ticket is too old after a minute
    t2 = ext.post("/api/auth/panel-ticket", headers=bearer).json()["ticket"]
    for k, (uid, exp) in list(main.auth._tickets.items()):
        main.auth._tickets[k] = (uid, time.time() - 1)
    assert page.post("/api/auth/panel-session", json={"ticket": t2}).status_code == 401


def test_only_the_panel_page_may_be_framed_and_only_by_an_extension():
    main = _main(QXS_ENV="production", QXS_SECRET_KEY="k" * 32, QXS_ADMIN_USER=ADMIN, QXS_ADMIN_PASSWORD=ADMIN_PW)
    c = _client(main)
    normal = c.get("/admin/live")
    assert normal.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in normal.headers["content-security-policy"]
    panel = c.get("/admin/live?embed=1&panel=1")
    assert "x-frame-options" not in panel.headers and "frame-ancestors chrome-extension:" in panel.headers["content-security-policy"] and "script-src 'self'" in panel.headers["content-security-policy"]
    assert c.get("/healthz?panel=1").headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in c.get("/?panel=1").headers["content-security-policy"]


if __name__ == "__main__":
    try:
        for name, fn in list(globals().items()):
            if name.startswith("test_"):
                fn()
                print("ok", name)
    finally:
        _cleanup()
