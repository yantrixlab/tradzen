"""Live sessions: the admin starts a session and sends signals by hand (heads-up -> signal -> result), users watch it, premium sessions stay closed to a plan without them."""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.candles import bucket  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.live_session import SessionError, stats_of  # noqa: E402
from test_auth import ADMIN, ADMIN_PW, H, _cleanup, _client, _main  # noqa: E402

SYM = "EURUSD"


def _setup():
    main = _main(QXS_ENV="production", QXS_SECRET_KEY="k" * 32, QXS_ADMIN_USER=ADMIN, QXS_ADMIN_PASSWORD=ADMIN_PW)
    eng = main.engine
    eng.instruments[SYM] = {"symbol": SYM, "name": "EUR/USD", "category": "currency", "payout": 80, "open": True, "market": "LIVE"}
    adm = _client(main)
    assert adm.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW}).status_code == 200
    return main, eng, adm


def _user(main, adm, name):
    assert adm.post("/api/users", json={"username": name, "password": f"{name}-pass-12345", "role": "trader", "must_change_pw": False}, headers=H).status_code == 200
    c = _client(main)
    assert c.post("/api/auth/login", json={"username": name, "password": f"{name}-pass-12345"}).status_code == 200
    return c


def _shift(eng, sg, k):
    """Move a sent signal to its own candle, k * 10 minutes after the first one, so every case in a test has fresh candles (ticks only move forward)."""
    t = bucket(time.time(), 60) + 60 + 600 * k
    eng.sessions._set(sg["id"], entry_t=t)
    return {**sg, "entry_t": t}


def _candle(eng, entry_t, o, c, tf=60):
    """Close the candle that opens at entry_t with the given open and close (three ticks, then one in the next candle)."""
    eng.on_tick(SYM, entry_t + 1, o)
    eng.on_tick(SYM, entry_t + 20, max(o, c))
    eng.on_tick(SYM, entry_t + 40, c)
    eng.on_tick(SYM, entry_t + tf + 1, c)


def test_a_session_from_heads_up_to_result():
    main, eng, adm = _setup()
    anon, trader = _client(main), _user(main, adm, "anna")
    body = {"title": "Evening", "tier": "free"}
    assert anon.post("/api/sessions", json=body, headers=H).status_code == 401 and trader.post("/api/sessions", json=body, headers=H).status_code == 403
    assert adm.get("/api/sessions/live").json()["session"] is None
    s = adm.post("/api/sessions", json={**body, "symbol": SYM, "tf": 60}, headers=H).json()
    assert s["status"] == "live" and s["follow_admin"] == 0
    assert adm.post("/api/sessions", json=body, headers=H).status_code == 409, "one live session at a time"
    sid = s["id"]
    # heads-up: the candle and the area are known, nothing to trade yet
    hu = adm.post(f"/api/sessions/{sid}/signals", json={"direction": "call", "area_lo": 1.0850, "area_hi": 1.0840, "area_label": "support", "note": "bounce"}, headers=H).json()
    assert hu["status"] == "headsup" and hu["direction"] == "CALL" and hu["area_lo"] == 1.084 and hu["area_hi"] == 1.085, "the ends of the area are put in order"
    assert hu["entry_t"] == bucket(time.time(), 60) + 60 and hu["tier"] == "free"
    ev = [e for e in eng.notify_log if e["kind"] == "session"]
    assert [e["stage"] for e in ev] == ["started", "headsup"] and ev[-1]["entry_t"] == hu["entry_t"] and "support" in ev[-1]["message"]
    # send it: a signal event with the area, then the candle settles it
    sent = adm.post(f"/api/sessions/{sid}/signals/{hu['id']}/send", headers=H).json()
    assert sent["status"] == "pending" and sent["payout"] == 80
    assert adm.post(f"/api/sessions/{sid}/signals/{hu['id']}/send", headers=H).status_code == 409, "sent once"
    sig_ev = [e for e in eng.notify_log if e["kind"] == "signal"][-1]
    assert sig_ev["id"] == f"s{hu['id']}" and sig_ev["direction"] == "CALL" and sig_ev["area"]["lo"] == 1.084 and sig_ev["target_t"] == hu["entry_t"] and sig_ev["aud"] == "session"
    _candle(eng, hu["entry_t"], 1.0800, 1.0830)
    done = adm.get(f"/api/sessions/{sid}").json()["signals"][0]
    assert done["status"] == "win" and done["entry"] == 1.08 and done["exit"] == 1.083
    res = [e for e in eng.notify_log if e["kind"] == "result"][-1]
    assert res["id"] == sig_ev["id"] and res["status"] == "win" and res["tier_stats"]["wins"] == 1
    # a PUT that closes up loses; a candle that closes where it opened is a draw
    for k, (direction, o, c, want) in enumerate((("PUT", 1.0800, 1.0810, "loss"), ("PUT", 1.0800, 1.0800, "draw")), start=1):
        sg = adm.post(f"/api/sessions/{sid}/signals", json={"direction": direction, "entry": "after", "send": True}, headers=H).json()
        assert sg["status"] == "pending"
        sg = _shift(eng, sg, k)
        _candle(eng, sg["entry_t"], o, c)
        assert [x for x in adm.get(f"/api/sessions/{sid}").json()["signals"] if x["id"] == sg["id"]][0]["status"] == want
    # a two-candle trade compares the first open with the last close
    sg = _shift(eng, adm.post(f"/api/sessions/{sid}/signals", json={"direction": "CALL", "expiry": 2, "send": True}, headers=H).json(), 3)
    for dt, p in ((1, 1.0800), (11, 1.0795), (21, 1.0790), (31, 1.0790), (41, 1.0790), (51, 1.0790),          # the first candle closes lower ...
                  (61, 1.0795), (71, 1.0805), (81, 1.0810), (91, 1.0820), (101, 1.0830), (111, 1.0830), (121, 1.0830)):     # ... the second closes higher than the first open
        eng.on_tick(SYM, sg["entry_t"] + dt, p)           # (ticks more than 20 s apart make a candle incomplete, and an incomplete candle settles nothing)
    assert [x for x in adm.get(f"/api/sessions/{sid}").json()["signals"] if x["id"] == sg["id"]][0]["status"] == "win"
    # a candle that never comes: the next candle that does arrive voids it
    sg = _shift(eng, adm.post(f"/api/sessions/{sid}/signals", json={"direction": "CALL", "send": True}, headers=H).json(), 5)
    for dt in (121, 141, 161, 181):
        eng.on_tick(SYM, sg["entry_t"] + dt, 1.0800)
    assert [x for x in adm.get(f"/api/sessions/{sid}").json()["signals"] if x["id"] == sg["id"]][0]["status"] == "void"
    # cancel: a heads-up quietly; a sent signal tells everybody not to take it
    hu2 = adm.post(f"/api/sessions/{sid}/signals", json={"direction": "PUT"}, headers=H).json()
    n0 = len([e for e in eng.notify_log if e["kind"] == "session"])
    assert adm.post(f"/api/sessions/{sid}/signals/{hu2['id']}/cancel", headers=H).json()["status"] == "cancelled" and len([e for e in eng.notify_log if e["kind"] == "session"]) == n0
    sg = adm.post(f"/api/sessions/{sid}/signals", json={"direction": "PUT", "send": True}, headers=H).json()
    assert adm.post(f"/api/sessions/{sid}/signals/{sg['id']}/cancel", headers=H).json()["status"] == "cancelled" and eng.notify_log[-1]["stage"] == "cancelled"
    assert adm.post(f"/api/sessions/{sid}/signals/{sg['id']}/cancel", headers=H).status_code == 409
    # the record: 2 wins, 1 loss, 1 draw, 1 void (cancelled ones do not count); net uses the payout stored at send time (80%)
    st = adm.get(f"/api/sessions/{sid}").json()["stats"]
    assert (st["wins"], st["losses"], st["draws"], st["voids"], st["total"]) == (2, 1, 1, 1, 5) and abs(st["net"] - (0.8 * 2 - 1)) < 1e-9 and st["accuracy"] == 2 / 3 and abs(st["breakeven"] - 1 / 1.8) < 1e-9
    # validation
    for bad in ({"direction": "SIDEWAYS"}, {"direction": "CALL", "expiry": 9}, {"direction": "CALL", "area_lo": 1.08}, {"direction": "CALL", "entry": "later"}, {"direction": "CALL", "area_lo": -1, "area_hi": 1}):
        assert adm.post(f"/api/sessions/{sid}/signals", json=bad, headers=H).status_code == 400, bad
    # a heads-up whose candle already started cannot be sent
    old = adm.post(f"/api/sessions/{sid}/signals", json={"direction": "CALL"}, headers=H).json()
    eng.sessions._set(old["id"], entry_t=bucket(time.time(), 60) - 60)
    assert adm.post(f"/api/sessions/{sid}/signals/{old['id']}/send", headers=H).status_code == 409
    # ending: unsent heads-ups are cancelled, everybody is told, nothing can be added any more
    hu3 = adm.post(f"/api/sessions/{sid}/signals", json={"direction": "CALL"}, headers=H).json()
    assert adm.post(f"/api/sessions/{sid}/end", headers=H).json()["status"] == "ended"
    assert eng.notify_log[-1]["stage"] == "ended" and "2 win" in eng.notify_log[-1]["message"]
    assert [x for x in adm.get(f"/api/sessions/{sid}").json()["signals"] if x["id"] == hu3["id"]][0]["status"] == "cancelled"
    assert adm.post(f"/api/sessions/{sid}/signals", json={"direction": "CALL"}, headers=H).status_code == 404 and adm.post(f"/api/sessions/{sid}/end", headers=H).status_code == 404
    assert adm.get("/api/sessions/live").json()["session"] is None
    hist = adm.get("/api/sessions").json()
    assert hist[0]["id"] == sid and hist[0]["stats"]["wins"] == 2 and hist[0]["signals"] >= 5
    actions = [a["action"] for a in adm.get("/api/audit").json()]
    assert {"session-start", "session-headsup", "session-signal", "session-cancel", "session-end"} <= set(actions)


def test_the_chart_follows_the_admins_quotex_tab_until_a_pair_is_picked():
    main, eng, adm = _setup()
    eng.instruments["GBPUSD"] = {"symbol": "GBPUSD", "name": "GBP/USD", "category": "currency", "payout": 85, "open": True, "market": "LIVE"}
    admin_id = next(u["id"] for u in adm.get("/api/users").json() if u["username"] == ADMIN)
    s = adm.post("/api/sessions", json={"title": "Follow"}, headers=H).json()
    assert s["follow_admin"] == 1
    assert adm.post(f"/api/sessions/{s['id']}/signals", json={"direction": "CALL"}, headers=H).status_code == 409, "no pair open yet"
    eng.user_sel[admin_id] = {"symbol": SYM, "period": 300, "at": time.time()}
    live = adm.get("/api/sessions/live").json()
    assert live["pair"] == {"symbol": SYM, "tf": 300} and live["selected"]["symbol"] == SYM and live["selected"]["tf"] == 300 and any(p["symbol"] == SYM for p in live["symbols"])
    eng.user_sel[admin_id] = {"symbol": "GBPUSD", "period": 60, "at": time.time()}          # he switches on Quotex: the chart follows
    assert adm.get("/api/sessions/live").json()["pair"]["symbol"] == "GBPUSD"
    assert adm.put(f"/api/sessions/{s['id']}/pair", json={"symbol": SYM, "tf": 900}, headers=H).json()["follow_admin"] == 0
    assert adm.get("/api/sessions/live").json()["pair"] == {"symbol": SYM, "tf": 900}, "a pair picked by hand holds"
    assert adm.put(f"/api/sessions/{s['id']}/pair", json={"symbol": "NOPE"}, headers=H).status_code == 400
    assert adm.put(f"/api/sessions/{s['id']}/pair", json={}, headers=H).json()["follow_admin"] == 1
    assert adm.get("/api/sessions/live").json()["pair"]["symbol"] == "GBPUSD"


def test_who_gets_the_session_and_its_alerts():
    main, eng, adm = _setup()
    anna, benny, anon = _user(main, adm, "anna"), _user(main, adm, "benny"), _client(main)
    ids = {u["username"]: u["id"] for u in adm.get("/api/users").json()}
    from app.routes_auth import event_visible
    feats, acc = main.features, main.access
    user = lambda n: main.auth.get_user(ids[n]) | {"via": "session"}          # noqa: E731
    assert anon.get("/api/sessions/live").status_code == 401
    # a free session: everybody with the menu
    s = adm.post("/api/sessions", json={"title": "Free one", "tier": "free", "symbol": SYM}, headers=H).json()
    sg = adm.post(f"/api/sessions/{s['id']}/signals", json={"direction": "CALL", "send": True}, headers=H).json()
    ev = eng.notify_log[-1]
    for n in ("anna", "benny"):
        assert event_visible(user(n), ev, feats.can, acc) is True and anna.get("/api/sessions/live").json()["signals"][0]["id"] == sg["id"]
    # muted: no alerts (the page still works)
    assert benny.put("/api/sessions/alerts", json={"on": False}, headers=H).json() == {"alerts_on": False}
    assert event_visible(user("benny"), ev, feats.can, acc) is False and benny.get("/api/sessions/live").json()["alerts_on"] is False and event_visible(user("anna"), ev, feats.can, acc) is True
    assert benny.put("/api/sessions/alerts", json={"on": True}, headers=H).status_code == 200 and event_visible(user("benny"), ev, feats.can, acc) is True
    adm.post(f"/api/sessions/{s['id']}/end", headers=H)
    # a premium session: the signals only for a plan with the premium feature
    p = adm.post("/api/sessions", json={"title": "Premium one", "tier": "premium", "symbol": SYM}, headers=H).json()
    psg = adm.post(f"/api/sessions/{p['id']}/signals", json={"direction": "PUT", "send": True}, headers=H).json()
    pev = eng.notify_log[-1]
    assert pev["tier_req"] == "premium"
    for n in ("anna", "benny"):
        assert event_visible(user(n), pev, feats.can, acc) is False
    locked = anna.get("/api/sessions/live").json()
    assert locked["locked"] is True and locked["signals"] == [] and locked["stats"] is None and locked["session"]["tier"] == "premium" and locked["selected"] is not None, "the chart is shared, the signals are not"
    assert anna.get(f"/api/sessions/{p['id']}").json()["locked"] is True and anna.get(f"/api/sessions/{p['id']}").json()["signals"] == []
    assert [h for h in anna.get("/api/sessions").json() if h["id"] == p["id"]][0]["stats"] is None
    # give anna the premium feature (what a Premium plan does): she sees it, benny still does not
    assert adm.put(f"/api/users/{ids['anna']}/features", json={"overrides": {"cap.sessions.premium": True}}, headers=H).status_code == 200
    assert event_visible(user("anna"), pev, feats.can, acc) is True and event_visible(user("benny"), pev, feats.can, acc) is False
    seen = anna.get("/api/sessions/live").json()
    assert seen["locked"] is False and seen["signals"][0]["id"] == psg["id"] and seen["stats"]["total"] == 1
    # staff and the admin always see everything
    assert event_visible(main.auth.get_user(1) | {"via": "session"}, pev, feats.can, acc) is True
    # the viewer cannot control anything
    for method, path, body in (("post", f"/api/sessions/{p['id']}/end", None), ("put", f"/api/sessions/{p['id']}/pair", {"symbol": SYM}), ("post", f"/api/sessions/{p['id']}/signals", {"direction": "CALL"}),
                               ("post", f"/api/sessions/{p['id']}/signals/{psg['id']}/cancel", None)):
        assert getattr(anna, method)(path, headers=H, **({"json": body} if body else {})).status_code == 403, path


def test_stats_helper_and_restart_recovery():
    rows = [{"status": s, "direction": d, "payout": 80} for s, d in (("win", "CALL"), ("win", "PUT"), ("loss", "CALL"), ("draw", "PUT"), ("pending", "CALL"), ("void", "CALL"), ("cancelled", "PUT"), ("headsup", "PUT"))]
    st = stats_of(rows)
    assert (st["total"], st["wins"], st["losses"], st["draws"], st["pending"], st["voids"], st["calls"], st["puts"]) == (6, 2, 1, 1, 1, 1, 4, 2) and st["accuracy"] == 2 / 3
    assert stats_of([])["accuracy"] is None and stats_of([])["net"] == 0
    # a restart: a signal that was waiting for its candle is settled from the stored candles, one whose candle is long gone is voided, a heads-up is cancelled
    path = Path(tempfile.mkdtemp()) / "t.db"
    e1 = Engine(path, seed_builtins=False)
    e1.instruments[SYM] = {"symbol": SYM, "name": "EUR/USD", "payout": 80, "open": True, "market": "LIVE"}
    s = e1.sessions.start(1, "Restart", "free", SYM, 60)
    won = e1.sessions.create_signal(s["id"], "CALL", send=True)
    stale = e1.sessions.create_signal(s["id"], "PUT", send=True)
    head = e1.sessions.create_signal(s["id"], "PUT")
    past = bucket(time.time(), 60) - 3600
    e1.sessions._set(won["id"], entry_t=past)                    # it was sent an hour ago, while the server was running ...
    won["entry_t"] = past
    e1.sessions.on_close = lambda *a: None                       # ... and the live settle "did not happen" (the process died)
    for dt, p in ((1, 1.0800), (15, 1.0810), (30, 1.0830), (45, 1.0830), (61, 1.0830)):
        e1.on_tick(SYM, won["entry_t"] + dt, p)
    e1._flush(force=True)
    e1.db.execute("UPDATE session_signals SET entry_t=? WHERE id=?", (bucket(time.time(), 60) - 7200, stale["id"]))
    e1.db.commit()
    e1.close()
    e2 = Engine(path, seed_builtins=False)
    rows = {r["id"]: r for r in e2.sessions.rows(s["id"])}
    assert rows[won["id"]]["status"] == "win" and rows[won["id"]]["exit"] == 1.083 and rows[stale["id"]]["status"] == "void"
    assert e2.sessions.current() is not None and e2.sessions.current()["id"] == s["id"], "the session itself is still live"
    assert rows[head["id"]]["status"] == "headsup", "this heads-up is still for a coming candle"
    e2.sessions.sweep(now=head["entry_t"] + 600)
    assert [r for r in e2.sessions.rows(s["id"]) if r["id"] == head["id"]][0]["status"] == "cancelled", "an old heads-up is dropped"
    e2.sessions.sweep(now=time.time() + 13 * 3600)
    assert e2.sessions.current() is None, "a session nobody ended is ended after 12 hours"
    try:
        e2.sessions.start(1, "x", "gold")
    except SessionError as e:
        assert e.status == 400
    else:
        raise AssertionError("an unknown tier is refused")
    e2.close()


def test_the_side_panel_can_watch_but_not_control():
    main, eng, adm = _setup()
    adm.post("/api/sessions", json={"title": "Panel", "symbol": SYM}, headers=H)
    ext = _client(main)
    tok = ext.post("/api/auth/extension-login", json={"username": ADMIN, "password": ADMIN_PW, "device": "d", "feeder": False}).json()["token"]
    t = ext.post("/api/auth/panel-ticket", headers={"Authorization": "Bearer " + tok}).json()["ticket"]
    sess = {"Authorization": "Session " + ext.post("/api/auth/panel-session", json={"ticket": t}).json()["token"], **H}
    live = ext.get("/api/sessions/live", headers=sess).json()
    assert live["session"]["title"] == "Panel" and ext.get("/api/sessions", headers=sess).status_code == 200 and ext.put("/api/sessions/alerts", json={"on": True}, headers=sess).status_code == 200
    assert ext.post("/api/sessions", json={"title": "x"}, headers=sess).status_code == 403 and ext.post(f"/api/sessions/{live['session']['id']}/end", headers=sess).status_code == 403


def test_running_candle_defined_time_and_defined_price():
    main, eng, adm = _setup()
    s = adm.post("/api/sessions", json={"title": "Modes", "symbol": SYM, "tf": 60}, headers=H).json()
    sid, url = s["id"], f"/api/sessions/{s['id']}/signals"
    now = time.time()
    # a running-candle trade is taken at once, at the live price: nothing to get ready for, and no price means no trade
    assert adm.post(url, json={"direction": "CALL", "entry": "current"}, headers=H).status_code == 400, "no heads-up for the running candle"
    assert adm.post(url, json={"direction": "CALL", "entry": "current", "send": True}, headers=H).status_code == 409, "no live price yet"
    eng.on_tick(SYM, now - 15, 1.0800)                                  # the last tick is 15 s old (its time is the feed's)
    cur = adm.post(url, json={"direction": "CALL", "entry": "current", "send": True, "note": "momentum"}, headers=H).json()
    assert cur["status"] == "pending" and cur["entry_mode"] == "current" and cur["entry_t"] == bucket(now, 60) and cur["entry"] == 1.08 and cur["entry_px"] == 1.08
    ev = [e for e in eng.notify_log if e["kind"] == "signal"][-1]
    assert abs(ev["now"] - time.time()) < 5 and ev["now"] > now - 5, "the card counts down on the server's clock, not on the (older) time of the last tick"
    assert ev["entry_mode"] == "current" and ev["basis_t"] == cur["entry_t"] and "NOW" in ev["factors"][0]["note"] and "momentum" in ev["factors"][0]["note"]
    # it is settled by that candle's close against the price it was taken at (not against the candle's open)
    cur = _shift(eng, cur, 1)
    _candle(eng, cur["entry_t"], 1.0850, 1.0830)                    # the candle opened above the entry and closes below its open, yet above the entry price: a win for a CALL at 1.0800
    assert [x for x in adm.get(f"/api/sessions/{sid}").json()["signals"] if x["id"] == cur["id"]][0]["status"] == "win"
    assert [x for x in adm.get(f"/api/sessions/{sid}").json()["signals"] if x["id"] == cur["id"]][0]["entry"] == 1.08
    # too late in the candle (under 10 s left): refused, send it for the next one
    import app.live_session as ls
    real = ls.time.time
    ls.time.time = lambda: bucket(real(), 60) + 55
    try:
        assert adm.post(url, json={"direction": "PUT", "entry": "current", "send": True}, headers=H).status_code == 409
    finally:
        ls.time.time = real
    # a defined time: the candle that opens at that time (any moment inside it), only in the future and at most 2 h ahead
    at = bucket(now, 60) + 5 * 60 + 17
    tm = adm.post(url, json={"direction": "PUT", "entry": "time", "at": at}, headers=H).json()
    assert tm["entry_mode"] == "time" and tm["entry_t"] == bucket(at, 60) and tm["status"] == "headsup"
    for bad in ({"entry": "time"}, {"entry": "time", "at": now - 300}, {"entry": "time", "at": bucket(now, 60)}, {"entry": "time", "at": now + 3 * 3600}):
        assert adm.post(url, json={"direction": "CALL", **bad}, headers=H).status_code == 400, bad
    # a defined price: the trade is entered at it, not at the candle's open
    px = adm.post(url, json={"direction": "CALL", "send": True, "entry_px": 1.0825}, headers=H).json()
    assert px["entry"] == 1.0825 and px["entry_px"] == 1.0825 and px["entry_mode"] == "next"
    px = _shift(eng, px, 2)
    _candle(eng, px["entry_t"], 1.0800, 1.0820)                     # closes above the candle's open (a win by the open) but below the entry price 1.0825: a loss
    got = [x for x in adm.get(f"/api/sessions/{sid}").json()["signals"] if x["id"] == px["id"]][0]
    assert got["status"] == "loss" and got["entry"] == 1.0825
    assert adm.post(url, json={"direction": "CALL", "entry_px": -2}, headers=H).status_code == 400


def test_status_dialogs_and_the_panel_sender_are_for_the_right_people():
    main, eng, adm = _setup()
    anna = _user(main, adm, "anna")
    ids = {u["username"]: u["id"] for u in adm.get("/api/users").json()}
    assert adm.get("/api/sessions/status").json()["live"] is False and _client(main).get("/api/sessions/status").status_code == 401
    s = adm.post("/api/sessions", json={"title": "Dialogs", "symbol": SYM}, headers=H).json()
    mine, hers = adm.get("/api/sessions/status").json(), anna.get("/api/sessions/status").json()
    assert mine["live"] and mine["mine"] is True and mine["session"]["title"] == "Dialogs" and hers["live"] and hers["mine"] is False
    assert adm.get("/api/sessions/live").json()["mine"] is True and anna.get("/api/sessions/live").json().get("mine") is False
    # the start dialog goes to everybody but the admin who pressed the button; the end dialog too, with the result
    feats, acc = main.features, main.access
    from app.routes_auth import event_visible
    started = [e for e in eng.notify_log if e["kind"] == "session" and e["stage"] == "started"][-1]
    assert started["by"] == ids[ADMIN] and event_visible(main.auth.get_user(ids[ADMIN]) | {"via": "session"}, started, feats.can, acc) is False
    assert event_visible(main.auth.get_user(ids["anna"]) | {"via": "session"}, started, feats.can, acc) is True
    # the side panel's limited session of the starting admin can send signals (and cancel them) but not start / end sessions; a trader's cannot send
    eng.on_tick(SYM, time.time(), 1.08)
    ext = _client(main)
    sess = lambda name, pw: {"Authorization": "Session " + ext.post("/api/auth/panel-session", json={"ticket": ext.post("/api/auth/panel-ticket", headers={"Authorization": "Bearer " + ext.post(          # noqa: E731
        "/api/auth/extension-login", json={"username": name, "password": pw, "device": "d", "feeder": False}).json()["token"]}).json()["ticket"]}).json()["token"], **H}
    a, t = sess(ADMIN, ADMIN_PW), sess("anna", "anna-pass-12345")
    sg = ext.post(f"/api/sessions/{s['id']}/signals", json={"direction": "CALL", "entry": "current", "send": True}, headers=a)
    assert sg.status_code == 200 and sg.json()["status"] == "pending"
    assert ext.post(f"/api/sessions/{s['id']}/signals", json={"direction": "CALL", "entry": "next"}, headers=t).status_code == 403
    assert ext.post(f"/api/sessions/{s['id']}/end", headers=a).status_code == 403 and ext.get("/api/sessions/status", headers=t).json()["mine"] is False
    adm.post(f"/api/sessions/{s['id']}/end", headers=H)
    ended = [e for e in eng.notify_log if e["kind"] == "session" and e["stage"] == "ended"][-1]
    assert ended["by"] == ids[ADMIN] and ended["result"]["total"] == 1 and event_visible(main.auth.get_user(ids["anna"]) | {"via": "session"}, ended, feats.can, acc) is True


if __name__ == "__main__":
    try:
        for name, fn in list(globals().items()):
            if name.startswith("test_"):
                fn()
                print("ok", name)
    finally:
        _cleanup()
