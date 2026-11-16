"""Live prices from ordinary accounts: one holder per pair (lease), the price feed's priority, per-tick checks, load limits, spot-checks, trust, and the hosted flow."""
import hashlib
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import leases as L  # noqa: E402
from app.contrib import Bucket  # noqa: E402
from app.engine import Engine  # noqa: E402
from test_auth import ADMIN, ADMIN_PW, H, _cleanup, _client, _main  # noqa: E402

SYM = "GBPNZD_otc"
BASE = (int(time.time()) // 60 - 10) * 60          # a minute boundary a few minutes ago: every tick below is "in the past" but within the age limit when `now` is set to it


def make() -> Engine:
    e = Engine(Path(tempfile.mkdtemp()) / "t.db")
    e.instruments[SYM] = {"symbol": SYM, "name": "GBP/NZD (OTC)", "category": "currency", "payout": 85, "open": True, "market": "OTC"}
    e.instruments["EURUSD"] = {"symbol": "EURUSD", "name": "EUR/USD", "category": "currency", "payout": 85, "open": True, "market": "LIVE"}
    return e


def tk(sym: str, ts: float, price: float, n: int = 1, step: float = 1.0) -> dict:
    return {"k": "t", "d": [[sym, ts + i * step, price + 0.00001 * i, 0] for i in range(n)]}


def pres(*syms: str) -> dict:
    return {"k": "tp", "syms": list(syms)}


def give(e: Engine, uid: int, items: list[dict], now: float) -> dict:
    return e.leases.answer(uid, items, now=now)


def stream(e: Engine, uid: int, t0: float, n: int, sym: str = SYM, price: float = 1.9) -> int:
    """A tick every second, sent the way an extension does: a few seconds at a time, each request made right after its last tick. Returns how many were applied."""
    done = 0
    for i in range(0, n, 10):
        k = min(10, n - i)
        done += give(e, uid, [pres(sym), tk(sym, t0 + i, price, k)], t0 + i + k)["applied"]
    return done


def sources(e: Engine, sym: str = SYM, tf: int = 60) -> set:
    e._flush(force=True)
    return {r[0] for r in e.db.execute("SELECT source FROM candles WHERE symbol=? AND tf=?", (sym, tf))}


def test_the_first_account_takes_a_free_pair_and_its_candles_are_tagged():
    e = make()
    r = give(e, 7, [pres(SYM)], BASE)                       # presence only: nothing held yet, the pair is free, so the account is asked to send it
    assert r["state"] == "on" and r["send"] == [SYM] and r["hold"] == [], r
    r = give(e, 7, [pres(SYM), tk(SYM, BASE, 1.9, 5)], BASE + 5)
    assert r["applied"] == 5 and r["hold"] == [SYM] and r["send"] == [SYM], r
    assert stream(e, 7, BASE + 5, 125) == 125                                  # a tick every second for two minutes: the first candles close
    assert sources(e) == {"tick:7"}, "candles built from an account's ticks are tagged with it"
    assert e.last_tick[SYM]["price"] > 1.9 and e.ingest_stats["ticks"] == 130 and e.ingest_stats["last_recv"] is not None
    assert e.leases.overview(now=BASE + 130)["holders"][0]["username"] == "#7"
    assert give(e, 7, [], BASE + 131)["send"] == [SYM], "a plain poll (no presence item) keeps the last presence"
    assert give(e, 7, [], BASE + 131 + L.PRESENCE_TTL + 1)["send"] == [], "... but only for a short time"


def test_a_second_account_is_only_spot_checked_and_a_silent_holder_is_replaced():
    e = make()
    give(e, 7, [pres(SYM), tk(SYM, BASE, 1.9, 10)], BASE + 10)
    r = give(e, 8, [pres(SYM)], BASE + 11)
    assert r["send"] == [SYM] and r["hold"] == [], "a held pair is offered to others only as a short spot-check"
    r = give(e, 8, [pres(SYM), {"k": "t", "d": [[SYM, BASE + 5, 1.9 + 0.00005, 0], [SYM, BASE + 6, 1.9 + 0.00005 * 2, 0]]}], BASE + 12)
    assert r["applied"] == 0 and r["hold"] == [], "its ticks are never applied while the pair is held"
    r = give(e, 8, [pres(SYM), {"k": "t", "d": [[SYM, BASE + 8, 1.95, 0]]}], BASE + 13)             # a different price at the same moment
    e.leases.flush(BASE + 14)
    row = e.db.execute("SELECT shadow_ok, shadow_bad FROM feed_stats WHERE uid=8").fetchone()
    assert row[0] >= 2 and row[1] == 1, row
    give(e, 7, [pres(SYM), tk(SYM, BASE + 30, 1.9, 2)], BASE + 31)                  # the holder keeps sending
    r = give(e, 8, [pres(SYM)], BASE + 32)                  # the spot-check window (15 s) is over and the next is two minutes away
    assert r["send"] == [] and e.leases.holders[SYM]["uid"] == 7, r
    # the holder goes silent: after the lease time the pair is free and the other account is asked to send it
    r = give(e, 8, [pres(SYM)], BASE + 31 + L.LEASE_TTL + 2)
    assert r["send"] == [SYM] and SYM not in e.leases.holders
    r = give(e, 8, [pres(SYM), tk(SYM, BASE + 40, 1.9 + 0.0001, 3)], BASE + 42)
    assert r["applied"] == 3 and r["hold"] == [SYM] and e.leases.holders[SYM]["uid"] == 8


def test_the_price_feed_has_priority():
    e = make()
    give(e, 7, [pres(SYM), tk(SYM, BASE, 1.9, 5)], BASE + 5)
    assert e.leases.holders[SYM]["uid"] == 7
    e.ingest({"items": [{"k": "t", "d": [[SYM, BASE + 6, 1.9002, 0]]}]}, uid=1, feeder=True)
    e.feed_at[SYM] = BASE + 6                                # (the tests use the simulated clock)
    r = give(e, 7, [pres(SYM), tk(SYM, BASE + 7, 1.9, 3)], BASE + 8)
    assert r["applied"] == 0 and r["hold"] == [] and r["send"] == [] and SYM not in e.leases.holders, r
    r = give(e, 9, [pres(SYM)], BASE + 9)
    assert r["send"] == [], "nobody is asked to send a pair the feed streams"
    # the feed stops: after the priority time the pair is free again
    r = give(e, 7, [pres(SYM)], BASE + 6 + L.FEEDER_TTL + 1)
    assert r["send"] == [SYM]


def test_every_tick_is_checked():
    e = make()
    give(e, 7, [pres(SYM), tk(SYM, BASE, 1.9, 3)], BASE + 3)
    def tot(k):
        return sum(s[k] for s in e.leases._st.values())

    def one(ts, price, now):
        before = tot("bad"), tot("late")
        out = give(e, 7, [{"k": "t", "d": [[SYM, ts, price, 0]]}], now)
        return out["applied"], tot("bad") - before[0], tot("late") - before[1]

    assert one(BASE + 10, 1.9005, BASE + 10) == (1, 0, 0)
    assert one(BASE + 11, 1.9005 * 1.03, BASE + 11) == (0, 1, 0), "a 3 % jump from one tick to the next is refused"
    assert one(BASE + 10, 1.9005, BASE + 12) == (0, 0, 1), "a replay is late, not an offence"
    assert one(BASE + 12, float("nan"), BASE + 12) == (0, 1, 0)
    assert one(BASE + 13, -1.0, BASE + 13) == (0, 1, 0)
    assert one(BASE + 100, 1.9, BASE + 13) == (0, 1, 0), "a tick from the future"
    assert one(BASE - 100, 1.9, BASE + 14) == (0, 0, 1), "an old tick (a throttled tab) is only late"
    assert e.leases.holders[SYM]["strikes"] == 4
    r = give(e, 7, [{"k": "t", "d": [["NOPE", BASE + 14, 1.0, 0]]}], BASE + 14)
    assert r["applied"] == 0 and tot("dropped") >= 1, "an unknown pair is ignored"
    # five bad ticks in a row cost the lease (it already had four)
    give(e, 7, [{"k": "t", "d": [[SYM, BASE + 15 + i, 3.0 + i, 0] for i in range(6)]}], BASE + 16)
    assert SYM not in e.leases.holders and any(x["e"] == "lease-lost" for x in e.feed_events)


def test_first_tick_after_a_pause_is_checked_against_the_stored_candles():
    e = make()
    e._series(SYM, 60).put({"t": BASE - 60, "o": 1.9, "h": 1.91, "l": 1.89, "c": 1.9, "n": 20})
    assert give(e, 7, [pres(SYM), tk(SYM, BASE, 2.6, 1)], BASE + 1)["applied"] == 0, "a price level far from the last candle (wrong pair or scale) is refused"
    assert give(e, 7, [pres(SYM), tk(SYM, BASE + 1, 1.905, 1)], BASE + 2)["applied"] == 1
    assert SYM in e.leases.holders


def test_limits_per_account_per_pair_and_global():
    e = make()
    big = {"k": "t", "d": [[SYM, BASE + i * 0.01, 1.9, 0] for i in range(1000)]}
    r = give(e, 7, [pres(SYM), big], BASE + 12)
    assert r["state"] == "on" and r["applied"] <= L.MAX_TICKS, "only the newest ticks of one request are looked at"
    r = give(e, 7, [pres(SYM), {"k": "t", "d": [[SYM, BASE + 20 + i * 0.01, 1.9, 0] for i in range(300)]}], BASE + 24)
    assert r["state"] == "busy" and r["retry_after"] >= 1 and r["applied"] == 0, "over the allowance: dropped, told to wait"
    e2 = make()
    e2.leases._global = Bucket(0.1, 5)
    assert give(e2, 7, [pres(SYM), tk(SYM, BASE, 1.9, 5)], BASE + 5)["applied"] == 5
    r = give(e2, 8, [pres(SYM), tk(SYM, BASE + 6, 1.9, 5)], BASE + 11)
    assert r["state"] == "busy" and r["applied"] == 0 and r["retry_after"] >= 1, "the server-wide budget"
    # an account holds at most MAX_LEASES pairs
    e3 = make()
    for i in range(L.MAX_LEASES + 2):
        s = f"PAIR{i}"
        e3.instruments[s] = {"symbol": s, "name": s, "category": "currency", "payout": 80, "open": True, "market": "LIVE"}
        give(e3, 7, [pres(s), tk(s, BASE, 1.0 + i, 2)], BASE + 3)
    assert sum(1 for h in e3.leases.holders.values() if h["uid"] == 7) == L.MAX_LEASES


def test_trust_pause_resume_and_revoke():
    e = make()
    lz = e.leases
    # mostly refused ticks -> paused automatically (never on a handful)
    give(e, 7, [pres(SYM), tk(SYM, BASE, 1.9, 3)], BASE + 3)
    st = lz._stat(7, BASE + 4)
    st["bad"] += 100
    lz.flush(BASE + 4)
    assert lz.blocked(7) is None, "too few ticks to judge"
    st = lz._stat(7, BASE + 5)
    st["bad"] += 300
    st["applied"] += 100
    lz.flush(BASE + 5)
    b = lz.blocked(7)
    assert b and b["auto"] and SYM not in lz.holders
    assert give(e, 7, [pres(SYM), tk(SYM, BASE + 10, 1.9, 2)], BASE + 12) == {"state": "paused", "send": [], "hold": []}
    assert lz.resume(7) is True and lz.blocked(7) is None
    # an account that keeps disagreeing with the others is paused too
    st = lz._stat(8, BASE + 6)
    st["shadow_ok"] += 20
    st["shadow_bad"] += 80
    lz.flush(BASE + 6)
    assert lz.blocked(8) and "disagreed" in lz.blocked(8)["reason"]
    lz.resume(8)
    # revoke removes only the candles built from that account's ticks
    e2 = make()
    assert stream(e2, 7, BASE, 130) == 130
    assert sources(e2) == {"tick:7"}
    e2.db.execute("INSERT INTO candles (symbol, tf, ts, o, h, l, c, ticks, source) VALUES (?,?,?,?,?,?,?,?,?)", (SYM, 60, BASE - 600, 1.9, 1.9, 1.9, 1.9, 5, "tick"))
    e2.db.commit()
    assert e2.leases.revoke(7) >= 2 and sources(e2) == {"tick"}
    assert e2.leases.overview(now=BASE + 200)["users"][0]["stored"] == 0


def test_disabled_server_does_nothing():
    e = make()
    e.leases.enabled = False
    assert give(e, 7, [pres(SYM), tk(SYM, BASE, 1.9, 3)], BASE + 3) == {"state": "off", "send": [], "hold": []}
    assert SYM not in e.last_tick


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

    def token(name, feeder=False, pw=None):
        r = ext.post("/api/auth/extension-login", json={"username": name, "password": pw or f"{name}-pass-12345", "device": "d", "feeder": feeder})
        return {"Authorization": "Bearer " + r.json()["token"]}

    anna, benny = token("anna"), token("benny")
    now = time.time()

    def ticks(n=4, p=1.08, t=None):
        t = t or now
        return {"k": "t", "d": [["EURUSD", t - n + i, p + 0.00001 * i, 0] for i in range(n)]}

    items = [pres("EURUSD"), ticks()]
    r = ext.post("/ingest", json={"items": items, "after_seq": 0}, headers=anna).json()
    assert r["feed"] == {"state": "wait"} and "EURUSD" not in eng.last_tick, "the Quotex account is not known yet"
    r = ext.post("/ingest", json={"items": items, "after_seq": 0, "qx": {"h": qh("4711"), "hint": "711"}}, headers=anna).json()
    assert r["feed"]["state"] == "on" and r["feed"]["hold"] == ["EURUSD"] and r["feed"]["applied"] == 4 and r["feed"]["send"] == ["EURUSD"], r["feed"]
    assert eng.last_tick["EURUSD"]["price"] > 1.08 and eng.leases.holders["EURUSD"]["uid"] == ids["anna"]
    # another account: spot-checked, not applied; a wrong Quotex account gets nothing
    ext.post("/ingest", json={"items": [], "qx": {"h": qh("999"), "hint": "999"}}, headers=benny)
    r = ext.post("/ingest", json={"items": [pres("EURUSD"), ticks(3, 1.08, now + 1)], "after_seq": 0, "qx": {"h": qh("999")}}, headers=benny).json()
    assert r["feed"]["state"] == "on" and r["feed"]["applied"] == 0 and r["feed"]["hold"] == []
    r = ext.post("/ingest", json={"items": items, "after_seq": 0, "qx": {"h": qh("555")}}, headers=anna).json()
    assert r["qx"]["state"] == "mismatch" and r["feed"] == {"state": "wait"}
    # reports
    anna_c = _client(main)
    assert anna_c.post("/api/auth/login", json={"username": "anna", "password": "anna-pass-12345"}).status_code == 200
    mine = anna_c.get("/api/livefeed/mine").json()
    assert mine["holding"][0]["symbol"] == "EURUSD" and mine["today"]["applied"] == 4 and mine["paused"] is None
    assert anna_c.get("/api/livefeed").status_code == 403 and anna_c.post(f"/api/livefeed/{ids['benny']}/pause", json={}, headers=H).status_code == 403
    ov = adm.get("/api/livefeed").json()
    assert ov["holders"][0]["username"] == "anna" and ov["users"][0]["username"] == "anna" and ov["users"][0]["applied"] == 4
    # pause / resume / revoke by the admin
    assert adm.post(f"/api/livefeed/{ids['anna']}/pause", json={"reason": "testing"}, headers=H).status_code == 200
    r = ext.post("/ingest", json={"items": items, "after_seq": 0, "qx": {"h": qh("4711")}}, headers=anna).json()
    assert r["feed"]["state"] == "paused" and "EURUSD" not in eng.leases.holders
    assert adm.post(f"/api/livefeed/{ids['anna']}/resume", headers=H).json()["was_paused"] is True
    assert adm.delete(f"/api/livefeed/{ids['anna']}", headers=H).json()["ok"] is True
    acts = {a["action"] for a in adm.get("/api/audit").json()}
    assert {"livefeed-pause", "livefeed-resume", "livefeed-revoke"} <= acts
    # a plan that switches the feature off
    std = adm.get("/api/features").json()["plans"][0]["id"]
    assert adm.put(f"/api/plans/{std}", json={"features": {"cap.ticks.feed": False}}, headers=H).status_code == 200
    r = ext.post("/ingest", json={"items": items, "after_seq": 0, "qx": {"h": qh("4711")}}, headers=anna).json()
    assert r["feed"] == {"state": "off"}
    # the price-feed token has its own path (and no lease answer); it also takes a pair over from an account
    f = token(ADMIN, feeder=True, pw=ADMIN_PW)
    r = ext.post("/ingest", json={"items": [ticks(3, 1.08, now + 5)], "after_seq": 0}, headers=f).json()
    assert "feed" not in r and eng.feed_at["EURUSD"] > 0


def test_local_server_has_no_lease_answer():
    main = _main()
    lc = _client(main)
    r = lc.post("/ingest", json={"items": [], "after_seq": 0})
    assert r.status_code == 200 and "feed" not in r.json()


if __name__ == "__main__":
    try:
        for name, fn in list(globals().items()):
            if name.startswith("test_"):
                fn()
                print("ok", name)
    finally:
        _cleanup()
