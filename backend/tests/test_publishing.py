"""Strategy ownership and publishing: a user's own strategies are private, shared ones are published step by step (testing -> library -> everybody), alerts go
only to their audience, signal history hides other people's strategies and their logic, and plans limit what a user can create and test."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from test_auth import ADMIN, ADMIN_PW, H, _cleanup, _client, _main  # noqa: E402


def sig(i, sid, direction="CALL", strength=70):
    return {"id": i, "created": time.time(), "symbol": "EURUSD", "tf": 60, "basis_t": 0, "target_t": 60, "direction": direction, "score": 0.7, "strength": strength, "agree": 3,
            "tier": "A", "status": "pending", "entry": 1.1, "exit": None, "sentiment": None, "factors": [{"name": "rsi", "vote": 1, "weight": 1, "note": "secret logic"}],
            "reason": "secret reason", "strategy_id": sid, "strategy_version": 1, "mode": "live"}


def test_ownership_publishing_fanout_and_limits():
    main = _main(QXS_ENV="production", QXS_SECRET_KEY="k" * 32, QXS_ADMIN_USER=ADMIN, QXS_ADMIN_PASSWORD=ADMIN_PW)
    eng = main.engine
    spec = eng.default_def["spec"]
    adm = _client(main)
    assert adm.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW}).status_code == 200

    def make(name, role):
        r = adm.post("/api/users", json={"username": name, "password": f"{name}-pass-12345", "role": role, "must_change_pw": False}, headers=H)
        assert r.status_code == 200, r.text
        c = _client(main)
        assert c.post("/api/auth/login", json={"username": name, "password": f"{name}-pass-12345"}).status_code == 200
        return c, r.json()["user"]["id"]

    edie, _ = make("edie", "editor")
    anna, anna_id = make("anna", "trader")
    ben, ben_id = make("benny", "trader")

    def create(c, key, status="paper"):
        return c.post("/api/strategies", json={"key": key, "name": key.title(), "description": f"{key} d", "spec": spec, "status": status}, headers=H)

    # ---- who owns what
    shared = {k: create(edie, k).json()["id"] for k in ("alpha", "beta", "gamma")}                   # staff make shared strategies
    mine_a = [create(anna, "mine").json()["id"], create(anna, "second").json()["id"], create(anna, "third").json()["id"]]
    assert create(anna, "fourth").status_code == 403, "the default plan allows 3 strategies of one's own"
    mine_b = create(ben, "mine")                                                                     # the same key as anna's: no clash
    assert mine_b.status_code == 200 and mine_b.json()["key"] == "mine" and mine_b.json()["mine"] is True
    assert {s["name"] for s in anna.get("/api/strategies").json()} == {"Mine", "Second", "Third"} and {s["id"] for s in anna.get("/api/strategies").json()} == set(mine_a)
    assert {s["id"] for s in ben.get("/api/strategies").json()} == {mine_b.json()["id"]}
    staff_list = {s["id"] for s in edie.get("/api/strategies").json()}
    assert set(shared.values()) <= staff_list and not (set(mine_a) & staff_list), "staff see the shared strategies, never a user's own"
    assert anna.get(f"/api/strategies/{mine_b.json()['id']}").status_code == 404 and anna.put(f"/api/strategies/{shared['alpha']}", json={"name": "x"}, headers=H).status_code == 404
    assert anna.get(f"/api/strategies/{shared['alpha']}").status_code == 404, "a strategy that is still being tested does not exist for users"
    assert anna.get(f"/api/strategies/{mine_a[0]}").json()["spec"] and anna.get(f"/api/strategies/{mine_a[0]}/versions/1").status_code == 200
    assert anna.post(f"/api/strategies/{shared['alpha']}/audience", json={"audience": "everyone"}, headers=H).status_code == 403
    assert edie.post(f"/api/strategies/{mine_a[0]}/audience", json={"audience": "everyone"}, headers=H).status_code == 404
    assert edie.post(f"/api/strategies/{mine_a[0]}/status", json={"status": "live"}, headers=H).status_code == 404, "staff cannot change a user's strategy"
    assert anna.post("/api/strategies/bulk", json={}, headers=H).status_code == 403

    # ---- publishing: testing -> library (opt in) -> everybody
    assert anna.get("/api/library").json() == []
    assert edie.post(f"/api/strategies/{shared['alpha']}/audience", json={"audience": "optin"}, headers=H).status_code == 200
    assert edie.post(f"/api/strategies/{shared['beta']}/audience", json={"audience": "everyone"}, headers=H).status_code == 200
    assert edie.post(f"/api/strategies/{shared['gamma']}/audience", json={"audience": "bogus"}, headers=H).status_code == 400
    for k in ("alpha", "beta", "gamma"):
        assert edie.post(f"/api/strategies/{shared[k]}/status", json={"status": "live"}, headers=H).status_code == 200
    lib = {s["name"]: s for s in anna.get("/api/library").json()}
    assert set(lib) == {"Alpha", "Beta"} and lib["Alpha"]["receiving"] is False and lib["Beta"]["receiving"] is True and lib["Beta"]["always"] is True
    d = anna.get(f"/api/strategies/{shared['alpha']}").json()
    assert d["spec"] is None and d["versions"] == [] and d["name"] == "Alpha", "a published strategy shows its record, never its logic"
    assert anna.post(f"/api/strategies/{shared['alpha']}/clone", json={"key": "stolen"}, headers=H).status_code == 404 and anna.get(f"/api/strategies/{shared['alpha']}/versions/1").status_code == 404
    assert anna.post(f"/api/library/{shared['gamma']}/subscribe", headers=H).status_code == 404, "a strategy under test cannot be added"
    assert anna.post(f"/api/library/{shared['alpha']}/subscribe", headers=H).status_code == 200
    assert anna.post("/api/strategies", json={"key": "x", "name": "x", "spec": spec}, headers=H).status_code == 403
    for sid in mine_a[:1] + [mine_b.json()["id"]]:
        assert (anna if sid in mine_a else ben).post(f"/api/strategies/{sid}/status", json={"status": "live"}, headers=H).status_code == 200

    # ---- alerts reach only the audience (one candle, every strategy says CALL)
    clients = {"admin": adm, "edie": edie, "anna": anna, "ben": ben}
    after = eng.seq
    eng._alert([sig(901, shared["alpha"]), sig(902, shared["beta"], strength=60), sig(903, shared["gamma"], strength=99), sig(904, mine_a[0], strength=50), sig(905, mine_b.json()["id"], strength=40)])
    got = {n: [(e["kind"], e["strategy_id"], [a["name"] for a in e["also"]]) for e in c.get(f"/api/events?after={after}").json()["events"]] for n, c in clients.items()}
    a, b, g, ma, mb = shared["alpha"], shared["beta"], shared["gamma"], mine_a[0], mine_b.json()["id"]
    assert sorted(x[1] for x in got["anna"]) == sorted([a, b, ma]), got["anna"]                      # added Alpha + everybody's Beta + her own; not gamma, not benny's
    assert sorted(x[1] for x in got["ben"]) == sorted([b, mb]), got["ben"]                           # Beta (everybody) + his own; Alpha not added
    assert sorted(x[1] for x in got["edie"]) == sorted([a, b, g]), got["edie"]
    assert sorted(x[1] for x in got["admin"]) == sorted([a, b, g]), got["admin"]
    assert all(also == [] for evs in got.values() for _k, _s, also in evs), "alerts are grouped within one audience only: nothing is listed as 'also' across audiences"
    # results follow the same audience; a user can switch off a strategy that is sent to everybody
    after = eng.seq
    eng._emit("result", {**sig(902, b), "status": "win", "exit": 1.2})
    assert len(ben.get(f"/api/events?after={after}").json()["events"]) == 1
    assert ben.delete(f"/api/library/{b}/subscribe", headers=H).status_code == 200
    main.access._subs.clear()
    after = eng.seq
    eng._emit("result", {**sig(902, b), "status": "win", "exit": 1.2})
    assert ben.get(f"/api/events?after={after}").json()["events"] == [] and len(anna.get(f"/api/events?after={after}").json()["events"]) == 1
    assert next(s for s in ben.get("/api/library").json() if s["id"] == b)["receiving"] is False

    # ---- signal history: own + received, without other people's logic
    cols = "(id, created, symbol, tf, basis_t, target_t, direction, tier, status, strategy_id, strategy_version, mode, score, strength, agree, reason, factors)"
    for i, sid in ((901, a), (902, b), (903, g), (904, ma), (905, mb)):
        eng.db.execute(f"INSERT OR REPLACE INTO signals {cols} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (i, time.time(), "EURUSD", 60, 0, 60, "CALL", "A", "win", sid, 1, "live", 0.7, 70, 3, "secret reason", json.dumps([{"name": "rsi", "vote": 1, "weight": 1, "note": "secret"}])))
    eng.db.commit()
    rows = {r["id"]: r for r in anna.get("/api/signals?limit=50").json()["rows"]}
    assert set(rows) == {901, 902, 904}, set(rows)                                                   # Alpha (added), Beta (everybody) and her own; not Gamma (testing), not benny's
    assert rows[904]["factors"] and rows[901]["factors"] == [] and rows[901]["reason"] == "", "the logic of a published strategy stays hidden"
    assert anna.get("/api/signals/905").status_code == 404 and anna.get("/api/signals/903").status_code == 404 and anna.get("/api/signals/904").status_code == 200
    assert anna.get("/api/signals/901").json()["signal"]["factors"] == [] and anna.get("/api/signals/901").json()["features"] is None
    assert {r["id"] for r in ben.get("/api/signals?limit=50").json()["rows"]} == {905}, "benny switched Beta off and never added Alpha"
    assert {r["id"] for r in edie.get("/api/signals?limit=50").json()["rows"]} == {901, 902, 903}, "staff see the shared strategies' signals, not users'"
    assert anna.get(f"/api/stats?strategy_id={g}").status_code == 404 and anna.get(f"/api/equity?strategy_id={ma}").status_code == 200

    # ---- backtests: own runs only, limited by the plan
    r = anna.post("/api/backtests", json={"strategy_id": ma, "sync": True}, headers=H)
    assert r.status_code == 200, r.text
    run = r.json()["id"]
    assert anna.post("/api/backtests", json={"strategy_id": shared["gamma"], "sync": True}, headers=H).status_code == 404
    assert [x["id"] for x in anna.get("/api/backtests").json()] == [run] and ben.get("/api/backtests").json() == []
    assert ben.get(f"/api/backtests/{run}").status_code == 404 and ben.delete(f"/api/backtests/{run}", headers=H).status_code == 404 and edie.get(f"/api/backtests/{run}").status_code == 404
    assert adm.get(f"/api/backtests/{run}").status_code == 200 and run in [x["id"] for x in adm.get("/api/backtests").json()]
    std = adm.get("/api/features").json()["plans"][0]["id"]
    assert adm.put(f"/api/plans/{std}", json={"features": {"cap.backtest.daily": 1}}, headers=H).status_code == 200
    assert anna.post("/api/backtests", json={"strategy_id": ma, "sync": True}, headers=H).status_code == 429, "the plan allows one backtest a day"
    assert edie.post("/api/backtests", json={"strategy_id": g, "sync": True}, headers=H).status_code == 200, "staff are not limited"
    assert adm.put(f"/api/plans/{std}", json={"features": {"cap.strategies.own": 0}}, headers=H).status_code == 200
    assert create(ben, "another").status_code == 403 and create(ben, "another").json()["detail"].startswith("your plan does not allow")
    adm.put(f"/api/plans/{std}", json={"features": {"cap.strategies.own": "default", "cap.backtest.daily": "default"}}, headers=H)

    # ---- the admin's tools only touch the shared strategies; deleting a user removes theirs
    assert all(s["owner_id"] is None for s in eng.repo.list(system_only=True))
    assert adm.delete(f"/api/users/{anna_id}", headers=H).status_code == 200
    left = {s["id"]: s for s in eng.repo.list(include_archived=True) if s["owner_id"] == anna_id}
    assert all(s["status"] == "archived" for s in left.values()) and not any(m["owner"] == anna_id and m["status"] != "archived" for m in eng.strategy_meta.values())
    assert eng.db.execute("SELECT COUNT(*) FROM strategy_subscriptions WHERE user_id=?", (anna_id,)).fetchone()[0] == 0
    assert eng.db.execute("SELECT COUNT(*) FROM backtest_runs WHERE owner_id=?", (anna_id,)).fetchone()[0] == 0


if __name__ == "__main__":
    try:
        for name, fn in list(globals().items()):
            if name.startswith("test_"):
                fn()
                print("ok", name)
    finally:
        _cleanup()
