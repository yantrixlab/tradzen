"""Tenancy: every user's trades, guard, style and balance are private (a database of their own), only the admin's price feed feeds the market, and private alerts reach only their owner."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from test_auth import ADMIN, ADMIN_PW, H, _cleanup, _client, _main  # noqa: E402

T = time.time() - 600


def closed(i, sym, profit, amount=1.0, cmd=0, t0=None):
    t0 = T if t0 is None else t0
    deal = {"id": i, "asset": sym, "amount": amount, "profit": profit, "command": cmd, "isDemo": 0, "openPrice": 1.1, "closePrice": 1.1 + (0.001 if (profit > 0) == (cmd == 0) else -0.001),
            "openTimestamp": t0, "closeTimestamp": t0 + 60}
    return {"k": "o", "n": "s_orders/close", "dir": "in", "ts": t0 + 61, "d": {"deals": [deal]}}


def balance(v):
    return {"k": "b", "n": "balance/change", "dir": "in", "ts": T, "d": {"balance": v, "isDemo": 0, "currency": "USD"}}


def test_every_user_has_a_private_world():
    main = _main(QXS_ENV="production", QXS_SECRET_KEY="k" * 32, QXS_ADMIN_USER=ADMIN, QXS_ADMIN_PASSWORD=ADMIN_PW)
    eng = main.engine
    adm = _client(main)
    assert adm.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW}).status_code == 200
    assert eng.primary_uid == adm.get("/api/me").json()["user"]["id"], "the admin from the .env file owns the main database"

    def make(name):
        r = adm.post("/api/users", json={"username": name, "password": f"{name}-pass-12345", "role": "trader", "must_change_pw": False}, headers=H)
        assert r.status_code == 200, r.text
        c = _client(main)
        assert c.post("/api/auth/login", json={"username": name, "password": f"{name}-pass-12345"}).status_code == 200
        tok = c.post("/api/tokens", json={"label": "chrome"}, headers=H).json()["secret"]
        return c, {"Authorization": f"Bearer {tok}"}, r.json()["user"]["id"]

    anna, anna_ext, anna_id = make("anna")
    ben, ben_ext, ben_id = make("benny")
    ext = _client(main)
    # the same broker id for both: nothing collides, because nothing is shared
    r = ext.post("/ingest", json={"items": [closed("D1", "EURUSD", 0.9), balance(120.5)], "after_seq": 0}, headers=anna_ext)
    assert r.status_code == 200 and r.json()["feeder"] is False
    ext.post("/ingest", json={"items": [closed("D1", "GBPUSD", -2.0, amount=2.0), closed("D2", "GBPUSD", -2.0, amount=2.0, t0=T + 200), balance(7)], "after_seq": 0}, headers=ben_ext)
    a, b = anna.get("/api/trades").json(), ben.get("/api/trades").json()
    assert a["total"] == 1 and a["trades"][0]["symbol"] == "EURUSD" and a["summary"]["wins"] == 1
    assert b["total"] == 2 and {t["symbol"] for t in b["trades"]} == {"GBPUSD"} and b["summary"]["losses"] == 2
    assert anna.get("/api/account").json()["live"] == 120.5 and ben.get("/api/account").json()["live"] == 7
    assert adm.get("/api/trades").json()["total"] == 0 and adm.get("/api/account").json()["live"] is None, "the admin's own pages hold only the admin's trades"
    assert anna.get("/api/trades/D2/detail").status_code == 404, "benny's trade does not exist for anna"
    assert anna.delete("/api/trades/D2", headers=H).status_code == 404 and ben.get("/api/trades").json()["total"] == 2
    assert ben.delete("/api/trades/D1", headers=H).status_code == 200 and anna.get("/api/trades").json()["total"] == 1 and ben.get("/api/trades").json()["total"] == 1
    assert anna.get("/api/trades/raw").json()["events"] and all("GBPUSD" not in str(e) for e in anna.get("/api/trades/raw").json()["events"])

    # guard settings and state are private too
    assert ben.put("/api/guard/settings", json={"target": {"mode": "amount", "value": 5}}, headers=H).status_code == 200
    ga, gb = anna.get("/api/guard").json(), ben.get("/api/guard").json()
    assert gb["settings"]["target"] != ga["settings"]["target"] and gb["settings"]["target"]["value"] == 5
    assert anna.get("/api/style").json()["profile"]["n"] == 0 and ben.get("/api/style").json()["settings"]

    # the market: a user's ticks are ignored, only the admin's price-feed token feeds it
    n_ticks = eng.ingest_stats["ticks"]
    ext.post("/ingest", json={"items": [{"k": "t", "d": [["EURUSD", time.time(), 1.1]]}, {"k": "i", "list": [["EURUSD", "EUR/USD", "x", 80, 1]]}], "after_seq": 0}, headers=anna_ext)
    assert eng.ingest_stats["ticks"] == n_ticks and "EURUSD" not in eng.last_tick and "EURUSD" not in eng.instruments
    feeder = adm.post("/api/tokens", json={"label": "feed", "feeder": True}, headers=H).json()["secret"]
    ext.post("/ingest", json={"items": [{"k": "t", "d": [["EURUSD", time.time(), 1.1]]}], "after_seq": 0}, headers={"Authorization": f"Bearer {feeder}"})
    assert eng.ingest_stats["ticks"] == n_ticks + 1 and "EURUSD" in eng.last_tick

    # private alerts: the guard alert of anna goes to anna's extension only; a shared signal alert goes to all
    eng.seq += 1
    eng.strategy_meta[3] = {"owner": None, "audience": "everyone", "status": "live"}
    eng.notify_log.append({"seq": eng.seq, "kind": "signal", "aud": "everyone", "id": 501, "strategy_id": 3, "t": time.time()})
    eng._emit_guard({"level": "warn", "rule": "test", "title": "only for anna", "message": "m"}, anna_id)
    after = eng.seq - 2
    seen = {name: [e["kind"] for e in c.get(f"/api/events?after={after}").json()["events"]] for name, c in (("anna", anna), ("ben", ben), ("admin", adm))}
    assert seen == {"anna": ["signal", "guard"], "ben": ["signal"], "admin": ["signal"]}, seen
    r = ext.post("/ingest", json={"items": [], "after_seq": after}, headers=ben_ext).json()
    assert [e["kind"] for e in r["events"]] == ["signal"]

    # the plan can stop a user's trades from being recorded
    std = adm.get("/api/features").json()["plans"][0]["id"]
    assert adm.put(f"/api/plans/{std}", json={"features": {"cap.trades.track": False}}, headers=H).status_code == 200
    ext.post("/ingest", json={"items": [closed("D9", "EURUSD", 1.0, t0=T + 400)], "after_seq": 0}, headers=anna_ext)
    assert anna.get("/api/trades").json()["total"] == 1
    adm.put(f"/api/plans/{std}", json={"features": {"cap.trades.track": "default"}}, headers=H)

    # private pages are behind their switches
    adm.put(f"/api/plans/{std}", json={"features": {"menu.guard": False}}, headers=H)
    assert anna.get("/api/guard").status_code == 403 and anna.get("/api/trades").status_code == 200
    adm.put(f"/api/plans/{std}", json={"features": {"menu.guard": "default"}}, headers=H)

    # deleting a user deletes their private database
    path = eng.users_dir / f"{ben_id}.db"
    assert path.exists() and (eng.users_dir / f"{anna_id}.db").exists()
    assert adm.delete(f"/api/users/{ben_id}", headers=H).status_code == 200 and not path.exists() and ben_id not in eng.users


def test_private_databases_are_separate_files_with_the_same_tables():
    from app.engine import Engine
    import tempfile
    eng = Engine(Path(tempfile.mkdtemp()) / "m.db", seed_builtins=False)
    u2, u3 = eng.user(2), eng.user(3)
    assert eng.user(1) is eng._primary and u2 is eng.user(2) and u2.db is not u3.db and u2.db is not eng.db
    u2.trades.on_item(closed("X", "EURUSD", 0.8))
    assert u2.trades.db.execute("SELECT COUNT(*) FROM broker_trades").fetchone()[0] == 1
    assert u3.trades.db.execute("SELECT COUNT(*) FROM broker_trades").fetchone()[0] == 0 and eng.db.execute("SELECT COUNT(*) FROM broker_trades").fetchone()[0] == 0
    tables = {r[0] for r in u2.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"broker_trades", "broker_events", "guard_alerts", "trade_context", "style_signals", "settings"} <= tables and "candles" not in tables and "signals" not in tables
    u2.guard.save_settings({"cooldown_min": 20})
    assert u3.guard.settings()["cooldown_min"] != 20 and eng.guard.settings()["cooldown_min"] != 20
    eng.drop_user(2)
    assert not (eng.users_dir / "2.db").exists()
    eng.close()


def test_only_live_signals_are_matched_with_other_users_trades():
    from app.engine import Engine
    eng = Engine(":memory:", seed_builtins=False)
    now = time.time()
    for mode in ("paper", "live"):
        eng.db.execute("INSERT INTO signals (created, symbol, tf, basis_t, target_t, direction, tier, status, strategy_id, mode, score, strength, agree, reason, factors) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (now - 30, "EURUSD", 60, 0, 0, "CALL", "A", "pending", 5, mode, 1, 1, 1, "", "[]"))
    eng.db.commit()
    own = [{"t": now, "symbol": "EURUSD", "direction": "CALL"}]
    eng._primary.trades._tag_signals(own, {5: "secret"})
    assert own[0]["source"] == "followed"
    other = eng.user(9)
    other.trades.shared.execute("UPDATE signals SET mode='paper'")                           # only a paper signal exists
    rows = [{"t": now, "symbol": "EURUSD", "direction": "CALL"}]
    other.trades._tag_signals(rows, {5: "secret"})
    assert rows[0]["source"] == "own", "a strategy that is still being tested is not revealed through a user's trade"


if __name__ == "__main__":
    try:
        for name, fn in list(globals().items()):
            if name.startswith("test_"):
                fn()
                print("ok", name)
    finally:
        _cleanup()
