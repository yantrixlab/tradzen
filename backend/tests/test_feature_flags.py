"""Features section: the registry, how a user's switches resolve (role ceiling, override, plan, default, plan expiry), plan management and the server-side enforcement."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import auth as A  # noqa: E402
from app import feature_flags as FT  # noqa: E402
from app.db import open_db  # noqa: E402
from test_auth import ADMIN, ADMIN_PW, H, _cleanup, _client, _main  # noqa: E402


def test_the_registry_is_consistent():
    keys = [f.key for f in FT.REGISTRY]
    assert len(keys) == len(set(keys)) and all(f.min_role in FT.RANK for f in FT.REGISTRY) and all(f.kind in ("toggle", "limit") for f in FT.REGISTRY)
    assert all(isinstance(f.default, bool) for f in FT.REGISTRY if f.kind == "toggle") and all(f.default is None or isinstance(f.default, int) for f in FT.REGISTRY if f.kind == "limit")
    assert FT.BY_KEY["menu.account"].locked
    for rx, _m, _r, feature in A._RULES:                                                  # every feature a route asks for exists
        assert feature is None or feature in FT.BY_KEY, feature


def user(role, plan_id=None, plan_until=None, uid=5):
    return {"id": uid, "role": role, "plan_id": plan_id, "plan_until": plan_until, "via": "db", "username": "u"}


def test_resolution_order():
    db = open_db(":memory:")
    ft = FT.Features(db)
    std = ft.plan(None)
    assert std["name"] == "Standard" and std["is_default"] and ft.plans()[0]["id"] == std["id"]
    # the admin has everything, with no limits
    assert ft.can(user("admin"), "menu.system") and ft.values(user("admin"))["cap.strategies.own"] is None
    # a trader can never have a staff feature, whatever the plan or an override says
    ft.update_plan(std["id"], {"features": {"menu.sweeps": True}})
    ft.set_overrides(5, {"menu.system": True})
    t = user("trader")
    assert not ft.can(t, "menu.sweeps") and not ft.can(t, "menu.system") and ft.effective(t)["menu.sweeps"]["source"] == "role"
    # an editor: default on -> the plan can switch it off -> an override can switch it on again
    e = user("editor")
    assert ft.can(e, "menu.backtests") and ft.effective(e)["menu.backtests"]["source"] == "default"
    ft.update_plan(std["id"], {"features": {"menu.backtests": False}})
    assert not ft.can(e, "menu.backtests") and ft.effective(e)["menu.backtests"]["source"] == "plan"
    ft.set_overrides(5, {"menu.backtests": True})
    assert ft.can(e, "menu.backtests") and ft.effective(e)["menu.backtests"]["source"] == "override"
    ft.set_overrides(5, {"menu.backtests": "default"})
    assert not ft.can(e, "menu.backtests")                                                # "default" removes the override
    # always-on and the admin-only features
    assert ft.can(t, "menu.account") and not ft.can(e, "menu.users") and not ft.can(e, "menu.features")
    # limits: a number, 0 (= off) or None (= unlimited)
    assert ft.limit(t, "cap.strategies.own") == 3
    ft.update_plan(std["id"], {"features": {"cap.strategies.own": 0}})
    assert ft.limit(t, "cap.strategies.own") == 0 and not ft.can(t, "cap.strategies.own")
    ft.update_plan(std["id"], {"features": {"cap.strategies.own": None}})
    assert ft.limit(t, "cap.strategies.own") is None and ft.can(t, "cap.strategies.own")
    # a plan past its end date falls back to the default plan
    pro = ft.create_plan("Pro", "", {"cap.signals.receive": True, "cap.extension.connect": True, "cap.strategies.own": 20})
    assert ft.limit(user("trader", pro["id"], time.time() + 1000), "cap.strategies.own") == 20
    assert ft.limit(user("trader", pro["id"], time.time() - 1000), "cap.strategies.own") is None                  # back on Standard (None = unlimited there)
    assert ft.limit(user("trader", pro["id"], None), "cap.strategies.own") == 20                                 # no end date: stays


def test_validation_and_plan_management():
    ft = FT.Features(open_db(":memory:"))
    std = ft.plan(None)
    for bad in ({"nope": True}, {"menu.sweeps": "yes"}, {"cap.strategies.own": -1}, {"cap.strategies.own": 1.5}, {"cap.strategies.own": True}, {"menu.account": False}, {"cap.strategies.own": 10 ** 9}):
        try:
            ft.update_plan(std["id"], {"features": bad})
            assert False, bad
        except FT.FeatureError as e:
            assert e.status == 400
    for args in (("x",), ("Standard",)):
        try:
            ft.create_plan(*args)
            assert False, args
        except FT.FeatureError as e:
            assert e.status in (400, 409)
    pro = ft.create_plan("Pro", "paid later", {"menu.sweeps": False})
    copy = ft.create_plan("Pro copy", copy_of=pro["id"])
    assert copy["features"] == {"menu.sweeps": False}
    try:
        ft.delete_plan(std["id"])
        assert False
    except FT.FeatureError:
        pass
    ft.db.execute("INSERT INTO users (username, pw_hash, role, plan_id, created) VALUES ('t','x','trader',?,1)", (pro["id"],))
    assert next(p for p in ft.plans() if p["id"] == pro["id"])["users"] == 1
    ft.update_plan(pro["id"], {"is_default": True})                                          # a different default
    assert ft.plan(None)["id"] == pro["id"] and sum(p["is_default"] for p in ft.plans()) == 1
    ft.update_plan(std["id"], {"is_default": True})
    ft.delete_plan(pro["id"])
    assert ft.db.execute("SELECT plan_id FROM users WHERE username='t'").fetchone()[0] is None, "its users fall back to the default plan"
    assert ft.update_plan(std["id"], {"features": {"menu.sweeps": False}})["features"] == {"menu.sweeps": False}
    assert ft.update_plan(std["id"], {"features": {"menu.sweeps": "default"}})["features"] == {}


def test_the_server_enforces_features():
    main = _main(QXS_ENV="production", QXS_SECRET_KEY="k" * 32, QXS_ADMIN_USER=ADMIN, QXS_ADMIN_PASSWORD=ADMIN_PW)
    adm = _client(main)
    assert adm.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW}).status_code == 200
    me = adm.get("/api/me").json()
    assert me["features"]["menu.system"] is True and me["features"]["cap.strategies.own"] is None and me["plan"]["name"] == "Standard"

    def make(name, role, **extra):
        r = adm.post("/api/users", json={"username": name, "password": f"{name}-pass-12345", "role": role, "must_change_pw": False}, headers=H)
        assert r.status_code == 200, r.text
        uid = r.json()["user"]["id"]
        if extra:
            assert adm.patch(f"/api/users/{uid}", json=extra, headers=H).status_code == 200
        c = _client(main)
        assert c.post("/api/auth/login", json={"username": name, "password": f"{name}-pass-12345"}).status_code == 200
        return c, uid

    ed, ed_id = make("edie", "editor")
    tr, tr_id = make("tom", "trader")
    # nobody but the admin sees or changes the features, plans and overrides
    for c in (ed, tr):
        assert c.get("/api/features").status_code == 403 and c.post("/api/plans", json={"name": "Hack"}, headers=H).status_code == 403 and c.put(f"/api/users/{tr_id}/features", json={"overrides": {}}, headers=H).status_code == 403
    cat = adm.get("/api/features").json()
    assert {f["key"] for f in cat["features"]} == {f.key for f in FT.REGISTRY} and cat["plans"][0]["is_default"]
    assert ed.get("/api/backtests").status_code == 200 and ed.get("/api/me").json()["features"]["menu.backtests"] is True and ed.get("/api/me").json()["features"]["menu.system"] is False

    # a plan without the Backtest lab: the menu's API closes for its users, the rest stays open
    plan = adm.post("/api/plans", json={"name": "No backtests", "features": {"menu.backtests": False}}, headers=H).json()
    assert adm.patch(f"/api/users/{ed_id}", json={"plan_id": plan["id"]}, headers=H).status_code == 200
    r = ed.get("/api/backtests")
    assert r.status_code == 403 and "plan" in r.json()["detail"] and ed.get("/api/strategies").status_code == 200 and ed.get("/api/me").json()["features"]["menu.backtests"] is False
    assert adm.patch(f"/api/users/{ed_id}", json={"plan_id": 9999}, headers=H).status_code == 400
    # one user's override beats the plan; "default" removes it again
    assert adm.put(f"/api/users/{ed_id}/features", json={"overrides": {"menu.backtests": True}}, headers=H).status_code == 200 and ed.get("/api/backtests").status_code == 200
    assert adm.get(f"/api/users/{ed_id}/features").json()["effective"]["menu.backtests"]["source"] == "override"
    assert adm.put(f"/api/users/{ed_id}/features", json={"overrides": {"menu.backtests": "default"}}, headers=H).status_code == 200 and ed.get("/api/backtests").status_code == 403
    assert adm.put(f"/api/users/{ed_id}/features", json={"overrides": {"nope": True}}, headers=H).status_code == 400
    # an expired plan end date falls back to the default plan (which has the menu)
    assert adm.patch(f"/api/users/{ed_id}", json={"plan_until": time.time() - 60}, headers=H).status_code == 200 and ed.get("/api/backtests").status_code == 200
    assert adm.delete(f"/api/plans/{plan['id']}", headers=H).status_code == 200 and adm.delete(f"/api/plans/{cat['plans'][0]['id']}", headers=H).status_code == 400

    # a trader: the extension token and the admin's signals are switches too
    assert tr.post("/api/tokens", json={"label": "chrome"}, headers=H).status_code == 200
    eng = main.engine
    eng.seq += 1
    eng.strategy_meta[3] = {"owner": None, "audience": "everyone", "status": "live"}
    eng.notify_log.append({"seq": eng.seq, "kind": "signal", "aud": "everyone", "id": 77, "strategy_id": 3, "t": time.time()})
    after = eng.seq - 1
    assert len(tr.get(f"/api/events?after={after}").json()["events"]) == 1
    std_id = cat["plans"][0]["id"]
    assert adm.put(f"/api/plans/{std_id}", json={"features": {"cap.signals.receive": False, "cap.extension.connect": False}}, headers=H).status_code == 200
    assert tr.post("/api/tokens", json={"label": "again"}, headers=H).status_code == 403
    assert tr.get(f"/api/events?after={after}").json()["events"] == [] and tr.get("/api/events").status_code == 200
    assert adm.get(f"/api/events?after={after}").json()["events"], "the admin still sees everything"
    assert adm.put(f"/api/users/{tr_id}/features", json={"overrides": {"cap.signals.receive": True}}, headers=H).status_code == 200
    assert len(tr.get(f"/api/events?after={after}").json()["events"]) == 1
    assert tr.get("/api/me").json()["features"]["cap.extension.connect"] is False
    acts = [a["action"] for a in adm.get("/api/audit?n=100").json()]
    assert {"plan-create", "plan-update", "plan-delete", "user-features"} <= set(acts)


if __name__ == "__main__":
    try:
        for name, fn in list(globals().items()):
            if name.startswith("test_"):
                fn()
                print("ok", name)
    finally:
        _cleanup()
