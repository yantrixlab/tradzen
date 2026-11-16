"""Shared candles: what an ordinary extension may add to the candle store (validation, first write wins, load limits, trust, the hosted flow)."""
import hashlib
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.contrib import Bucket  # noqa: E402
from app.engine import Engine  # noqa: E402
from test_auth import ADMIN, ADMIN_PW, H, _cleanup, _client, _main  # noqa: E402

SYM = "GBPNZD_otc"
NOW = 1_794_400_000 // 900 * 900           # a 15-minute boundary
LATER = NOW + 3600                         # "now" for the unit tests: everything up to NOW is closed


def raw(t0: int, n: int, tf: int = 60, price: float = 1.9) -> list[list]:
    """[time, open, close, high, low, ticks, lastTick] in platform order."""
    rows = []
    for i in range(n):
        o = price + 0.0001 * (i % 7)
        c = o + 0.00005
        rows.append([t0 + i * tf, o, c, c + 0.0002, o - 0.0002, 20, t0 + i * tf + tf - 1])
    return rows


def make() -> Engine:
    e = Engine(Path(tempfile.mkdtemp()) / "t.db")
    e.instruments[SYM] = {"symbol": SYM, "name": "GBP/NZD (OTC)", "category": "currency", "payout": 85, "open": True, "market": "OTC"}
    e.on_tick(SYM, NOW + 5.0, 1.9)                           # the feed is streaming this pair
    return e


def give(e: Engine, uid: int, rows: list[list], asset: str = SYM, now: float = LATER) -> dict:
    out = e.contrib.intake(uid, [{"k": "h", "asset": asset, "rows": rows}], now=now)
    return out["results"][0] if out["results"] else out


def stored(e: Engine, tf: int = 60, src: str | None = None) -> int:
    e._flush(force=True)
    q, a = "SELECT COUNT(*) FROM candles WHERE symbol=? AND tf=?", [SYM, tf]
    if src:
        q, a = q + " AND source LIKE ?", a + [src]
    return e.db.execute(q, a).fetchone()[0]


def test_new_candles_are_stored_and_higher_timeframes_built():
    e = make()
    r = give(e, 7, raw(NOW - 300 * 60, 300))
    assert (r["received"], r["new"], r["dup"], r["conflict"], r["rejected"]) == (300, 300, 0, 0, 0), r
    assert stored(e, 60, "contrib:7") == 300
    assert stored(e, 300, "contrib:7") >= 55 and stored(e, 900, "contrib:7") >= 19, "5m and 15m candles are built from them, tagged with the contributor"
    assert e.contrib.mine(7, now=LATER)["today"]["new"] == 300 and e.contrib.mine(7, now=LATER)["stored"] >= 300


def test_first_write_wins_and_disagreement_is_recorded():
    e = make()
    give(e, 7, raw(NOW - 100 * 60, 100))
    before = e.db.execute("SELECT o, h, l, c, source FROM candles WHERE symbol=? AND tf=60 AND ts=?", (SYM, NOW - 50 * 60)).fetchone()
    again = give(e, 8, raw(NOW - 100 * 60, 100))
    assert (again["new"], again["dup"], again["conflict"]) == (0, 100, 0), "the same candles again change nothing"
    other = raw(NOW - 100 * 60, 100)
    for row in other[:10]:
        row[1:5] = [x * 1.01 for x in row[1:5]]                                  # 1 % off: a different history of the same minutes
    r = give(e, 9, other)
    assert r["conflict"] == 10 and r["new"] == 0 and r["dup"] == 90, r
    assert e.db.execute("SELECT o, h, l, c, source FROM candles WHERE symbol=? AND tf=60 AND ts=?", (SYM, NOW - 50 * 60)).fetchone() == before
    assert e.db.execute("SELECT o FROM candles WHERE symbol=? AND tf=60 AND ts=?", (SYM, NOW - 100 * 60)).fetchone()[0] == other[0][1] / 1.01, "the stored candle is untouched"
    ds = e.contrib.overview(now=LATER)["disputes"]
    assert len(ds) == 5 and ds[0]["uid"] == 9 and ds[0]["stored_source"] == "contrib:7", "a few examples are kept for the admin"


def test_the_price_feed_replaces_a_contributed_candle_but_never_the_other_way():
    e = make()
    t = NOW - 10 * 60
    give(e, 7, raw(t, 5))
    e._queue_candle(SYM, 60, {"t": t, "o": 1.9, "h": 1.91, "l": 1.89, "c": 1.905, "n": 1}, "tick")       # the feed's own candle for the same minute
    e._flush(force=True)
    row = e.db.execute("SELECT o, source FROM candles WHERE symbol=? AND tf=60 AND ts=?", (SYM, t)).fetchone()
    assert row == (1.9, "tick"), row
    r = give(e, 8, raw(t, 5))
    assert r["new"] == 0 and e.db.execute("SELECT source FROM candles WHERE symbol=? AND tf=60 AND ts=?", (SYM, t)).fetchone()[0] == "tick"
    # a feed row is not replaced by a lower-tick feed row of another source either (the old rule is unchanged)
    e._queue_candle(SYM, 60, {"t": t, "o": 5, "h": 5, "l": 5, "c": 5, "n": 0}, "history")
    e._flush(force=True)
    assert e.db.execute("SELECT o FROM candles WHERE symbol=? AND tf=60 AND ts=?", (SYM, t)).fetchone()[0] == 1.9


def test_validation_refuses_what_is_not_a_candle():
    e = make()
    assert give(e, 7, raw(NOW - 50 * 60, 50), asset="NOPE_otc")["reason"] == "unknown pair", "contributions cannot create pairs"
    assert give(e, 7, raw(NOW - 50 * 60, 50), asset="EURUSD_mkt")["reason"] == "bad pair name", "the real-market data keeps its own names"
    assert give(e, 7, raw(NOW - 50 * 60, 50), asset="a b")["reason"] == "bad pair name"
    assert give(e, 7, raw(NOW, 1))["reason"] == "fewer than 2 candles"
    assert give(e, 7, raw(NOW - 50 * 60, 50, tf=7))["reason"] == "unsupported timeframe"
    bad = raw(NOW - 50 * 60, 50)
    bad[10][3] = bad[10][4] - 0.1                                              # high below low
    bad[20][1] = -1                                                            # negative price
    bad[30][4] = 0.5                                                           # a 70 % candle
    bad[40][0] += 7                                                            # not on a minute boundary
    r = give(e, 7, bad)
    assert r["new"] == 46 and r["rejected"] == 4, r
    assert stored(e, 60, "contrib:7") == 46
    # a candle that has not ended yet is not part of a contribution (and so nothing "from the future" can be stored)
    r = give(e, 7, raw(NOW, 10), now=NOW + 300)
    assert (r["received"], r["new"]) == (4, 4), r                              # candles NOW .. NOW+180 have ended; NOW+240 .. NOW+540 have not
    assert e.db.execute("SELECT COUNT(*) FROM candles WHERE symbol=? AND tf=60 AND ts>=?", (SYM, NOW + 240)).fetchone()[0] == 0
    # a jump between neighbours that no market makes: the odd candle and the one after it are refused
    j = raw(NOW - 200 * 60, 40)
    j[20][1:5] = [x * 1.3 for x in j[20][1:5]]
    r = give(e, 7, j)
    assert (r["rejected"], r["new"], r["reason"]) == (2, 38, "jump"), r


def test_a_batch_at_the_wrong_price_level_is_refused_as_a_whole():
    e = make()
    give(e, 7, raw(NOW - 100 * 60, 100, price=1.9))
    r = give(e, 8, raw(NOW - 300 * 60, 100, price=2.6))                        # 37 % away from what the server holds for the pair
    assert r["new"] == 0 and r["rejected"] == 100 and "level" in r["reason"], r
    ok = give(e, 8, raw(NOW - 300 * 60, 100, price=1.95))
    assert ok["new"] == 100, "a normal move is fine"


def test_a_contribution_closes_a_hole_the_feed_left():
    e = make()
    e.ingest({"items": [{"k": "h", "asset": SYM, "rows": raw(NOW - 300 * 60, 100)}]})
    e.ingest({"items": [{"k": "h", "asset": SYM, "rows": raw(NOW - 100 * 60, 100)}]})              # the middle 100 minutes are missing
    e._flush(force=True)
    s = e.series[(SYM, 60)]
    assert len(s.contiguous_tail(60)) == 100 and s.last_gap(60)["missing"] == 100
    r = give(e, 7, raw(NOW - 200 * 60, 100))
    assert r["new"] == 100
    assert len(e.series[(SYM, 60)].contiguous_tail(60)) == 300, "the engine sees an uninterrupted run again at once"


def test_load_limits_refuse_instead_of_queueing():
    e = make()
    uid = 7
    e.contrib._user_batches[uid] = Bucket(0.0001, 2)                           # two history items, then wait
    assert give(e, uid, raw(NOW - 60 * 60, 50))["new"] == 50
    assert give(e, uid, raw(NOW - 120 * 60, 50))["new"] == 50
    out = e.contrib.intake(uid, [{"k": "h", "asset": SYM, "rows": raw(NOW - 180 * 60, 50)}], now=LATER)
    assert out["state"] == "busy" and out["retry_after"] > 60 and out["results"] == []
    assert stored(e, 60) == 100, "the refused batch cost nothing"
    e.contrib._global = Bucket(1, 60)                                          # the whole server is at its write budget
    assert e.contrib.intake(8, [{"k": "h", "asset": SYM, "rows": raw(NOW - 300 * 60, 60)}], now=LATER)["state"] == "on"
    out = e.contrib.intake(9, [{"k": "h", "asset": SYM, "rows": raw(NOW - 400 * 60, 60)}], now=LATER)
    assert out["state"] == "busy" and stored(e, 60, "contrib:9") == 0
    e.contrib._global = Bucket(3000, 20000)                                    # the budget is back
    big = e.contrib.intake(10, [{"k": "h", "asset": SYM, "rows": raw(NOW - 5000 * 60, 1001)}], now=LATER)
    assert big["state"] == "on" and big["results"][0]["received"] == 1000 and stored(e, 60, "contrib:10") == 1000, "a longer history is cut to its newest 1000 candles, not refused"
    junk = e.contrib.intake(11, [{"k": "h", "asset": SYM, "rows": "nope"}, {"k": "h", "asset": 5, "rows": None}], now=LATER)
    assert junk["state"] == "on" and all(r["new"] == 0 for r in junk["results"]), "garbage is answered, never raised"


def test_an_account_that_mostly_sends_rubbish_is_paused_and_can_be_revoked():
    e = make()
    assert give(e, 8, raw(NOW - 100 * 60, 100))["new"] == 100                  # an honest contributor, for the revoke check
    rubbish = raw(NOW - 500 * 60, 200)
    for row in rubbish:
        row[3] = row[4] - 0.01                                                 # every high below its low
    assert give(e, 7, rubbish)["rejected"] == 200
    assert e.contrib.blocked(7) is None, "judged only after a few hundred candles"
    assert e.contrib.intake(7, [{"k": "h", "asset": SYM, "rows": rubbish}], now=LATER)["state"] == "on", "and a few batches: one odd batch never pauses anybody"
    out = e.contrib.intake(7, [{"k": "h", "asset": SYM, "rows": rubbish}], now=LATER)
    assert out["state"] == "paused" and e.contrib.blocked(7)["auto"] is True
    assert e.contrib.intake(7, [{"k": "h", "asset": SYM, "rows": raw(NOW - 700 * 60, 50)}], now=LATER) == {"state": "paused", "results": []}
    assert e.contrib.resume(7) is True and e.contrib.blocked(7) is None
    assert give(e, 7, raw(NOW - 700 * 60, 50))["new"] == 50
    removed = e.contrib.revoke(7)
    assert removed >= 50 and stored(e, 60, "contrib:7") == 0 and stored(e, 60, "contrib:8") == 100, "only that account's candles go"
    e.contrib.enabled = False
    assert e.contrib.intake(8, [{"k": "h", "asset": SYM, "rows": raw(NOW - 900 * 60, 50)}], now=LATER)["state"] == "off"


def test_wanted_ranges_name_the_holes_of_streaming_pairs_and_thin_pairs():
    e = make()
    e.ingest({"items": [{"k": "h", "asset": SYM, "rows": raw(NOW - 300 * 60, 100)}]})
    e.ingest({"items": [{"k": "h", "asset": SYM, "rows": raw(NOW - 100 * 60, 100)}]})
    e._flush(force=True)
    e.last_tick[SYM]["recv"] = NOW + 5                                         # (the unit tests run on a fixed clock)
    w = e.contrib.wants(now=NOW + 10)
    gap = [x for x in w if x["kind"] == "gap"]
    assert len(gap) == 1 and gap[0]["asset"] == SYM and gap[0]["missing"] == 100, w
    assert (gap[0]["from"], gap[0]["to"]) == (NOW - 200 * 60, NOW - 101 * 60), "exactly the minutes to fetch"
    depth = [x for x in w if x["kind"] == "depth"]
    assert depth and depth[0]["asset"] == SYM and depth[0]["to"] == NOW - 300 * 60 - 60, "and the pair needs history older than the oldest candle it has"
    assert w[0]["kind"] == "gap", "holes of streaming pairs come first (they stop signals)"


# ------------------------------------------------------------------------------------------------ the hosted flow
def qh(uid: str) -> str:
    return hashlib.sha256(("tradzen-qx-v1:" + uid).encode()).hexdigest()


def test_hosted_flow():
    main = _main(QXS_ENV="production", QXS_SECRET_KEY="k" * 32, QXS_ADMIN_USER=ADMIN, QXS_ADMIN_PASSWORD=ADMIN_PW, QXS_QUOTEX_LINK="on")
    eng = main.engine
    eng.instruments["EURUSD"] = {"symbol": "EURUSD", "name": "EUR/USD", "category": "currency", "payout": 85, "open": True, "market": "LIVE"}
    adm = _client(main)
    assert adm.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW}).status_code == 200
    for name in ("anna", "benny"):
        assert adm.post("/api/users", json={"username": name, "password": f"{name}-pass-12345", "role": "trader", "must_change_pw": False}, headers=H).status_code == 200
    ids = {u["username"]: u["id"] for u in adm.get("/api/users").json()}
    ext = _client(main)

    def token(name):
        r = ext.post("/api/auth/extension-login", json={"username": name, "password": f"{name}-pass-12345", "device": "d", "feeder": False})
        return {"Authorization": "Bearer " + r.json()["token"]}

    anna, benny = token("anna"), token("benny")
    t0 = (int(time.time()) // 60 - 300) * 60
    hist = {"k": "h", "asset": "EURUSD", "rows": raw(t0, 250, price=1.08)}
    # the Quotex account is not known yet: the history is not taken, and the answer says why
    r = ext.post("/ingest", json={"items": [hist], "after_seq": 0}, headers=anna).json()
    assert r["contrib"] == {"state": "wait"} and eng.db.execute("SELECT COUNT(*) FROM candles WHERE symbol='EURUSD'").fetchone()[0] == 0
    # linked: taken, tagged with the account, and the answer lists the result per item
    r = ext.post("/ingest", json={"items": [hist], "after_seq": 0, "qx": {"h": qh("4711"), "hint": "711"}}, headers=anna).json()
    c = r["contrib"]
    assert c["state"] == "on" and c["results"][0]["new"] == 250 and c["results"][0]["asset"] == "EURUSD" and c["results"][0]["done"] is True, c
    assert eng.db.execute("SELECT COUNT(*) FROM candles WHERE symbol='EURUSD' AND tf=60 AND source=?", (f"contrib:{ids['anna']}",)).fetchone()[0] == 250
    # the same range from another account: nothing new, nothing changed
    ext.post("/ingest", json={"items": [], "qx": {"h": qh("999"), "hint": "999"}}, headers=benny)
    r = ext.post("/ingest", json={"items": [hist], "after_seq": 0, "qx": {"h": qh("999")}}, headers=benny).json()
    assert r["contrib"]["results"][0]["new"] == 0 and r["contrib"]["results"][0]["dup"] == 250
    assert "want" in r["contrib"]
    # a tab on the wrong Quotex account contributes nothing
    r = ext.post("/ingest", json={"items": [{**hist, "rows": raw(t0 - 600 * 60, 50, price=1.08)}], "after_seq": 0, "qx": {"h": qh("555")}}, headers=anna).json()
    assert r["qx"]["state"] == "mismatch" and r["contrib"] == {"state": "wait"}
    assert eng.db.execute("SELECT COUNT(*) FROM candles WHERE symbol='EURUSD' AND tf=60").fetchone()[0] == 250
    # the account's own numbers
    anna_c = _client(main)
    assert anna_c.post("/api/auth/login", json={"username": "anna", "password": "anna-pass-12345"}).status_code == 200
    mine = anna_c.get("/api/contrib/mine").json()
    assert mine["today"]["new"] == 250 and mine["stored"] >= 250 and mine["paused"] is None
    assert anna_c.get("/api/contrib").status_code == 403 and anna_c.post(f"/api/contrib/{ids['benny']}/pause", json={}, headers=H).status_code == 403
    ov = adm.get("/api/contrib").json()
    assert ov["users"][0]["username"] == "anna" and ov["users"][0]["new"] == 250 and ov["stored_from_contributions"] >= 250
    # the admin pauses, resumes and revokes
    assert adm.post(f"/api/contrib/{ids['anna']}/pause", json={"reason": "testing"}, headers=H).status_code == 200
    r = ext.post("/ingest", json={"items": [{**hist, "rows": raw(t0 - 600 * 60, 50, price=1.08)}], "after_seq": 0, "qx": {"h": qh("4711")}}, headers=anna).json()
    assert r["contrib"]["state"] == "paused"
    assert adm.post(f"/api/contrib/{ids['anna']}/resume", headers=H).json()["was_paused"] is True
    assert adm.delete(f"/api/contrib/{ids['anna']}", headers=H).json()["removed"] >= 250
    assert eng.db.execute("SELECT COUNT(*) FROM candles WHERE symbol='EURUSD' AND tf=60").fetchone()[0] == 0
    acts = {a["action"] for a in adm.get("/api/audit").json()}
    assert {"contrib-pause", "contrib-resume", "contrib-revoke"} <= acts
    # a plan that switches the feature off
    std = adm.get("/api/features").json()["plans"][0]["id"]
    assert adm.put(f"/api/plans/{std}", json={"features": {"cap.candles.contribute": False}}, headers=H).status_code == 200
    r = ext.post("/ingest", json={"items": [hist], "after_seq": 0, "qx": {"h": qh("4711")}}, headers=anna).json()
    assert r["contrib"] == {"state": "off"}
    # the price feed's history keeps its own path (and no contrib answer)
    f = ext.post("/api/auth/extension-login", json={"username": ADMIN, "password": ADMIN_PW, "device": "feed", "feeder": True}).json()
    r = ext.post("/ingest", json={"items": [hist], "after_seq": 0}, headers={"Authorization": "Bearer " + f["token"]}).json()
    assert "contrib" not in r and eng.db.execute("SELECT COUNT(*) FROM candles WHERE symbol='EURUSD' AND tf=60").fetchone()[0] >= 200


def test_local_server_has_no_contributions_answer():
    main = _main()
    lc = _client(main)
    r = lc.post("/ingest", json={"items": [], "after_seq": 0})
    assert r.status_code == 200 and "contrib" not in r.json()


if __name__ == "__main__":
    try:
        for name, fn in list(globals().items()):
            if name.startswith("test_"):
                fn()
                print("ok", name)
    finally:
        _cleanup()
