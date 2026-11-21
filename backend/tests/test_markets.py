"""The public chart platform: locks decided on the server (the market list, the live answer and every other route that returns a pair's candles), the countdown fields, self sign-up that
is closed until the admin opens it, and public pricing. Run from backend/:  .venv\\Scripts\\python tests\\test_markets.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_auth import ADMIN, ADMIN_PW, H, _cleanup, _client, _main  # noqa: E402

PAIRS = {"EURUSD_otc": ("EUR/USD (OTC)", "OTC", "currency", 88), "AUDCAD_otc": ("AUD/CAD (OTC)", "OTC", "currency", 79), "BTCUSD_otc": ("Bitcoin (OTC)", "OTC", "cryptocurrency", 53),
         "EURUSD": ("EUR/USD", "LIVE", "currency", 20)}


def setup():
    main = _main(QXS_ENV="production", QXS_SECRET_KEY="k" * 32, QXS_ADMIN_USER=ADMIN, QXS_ADMIN_PASSWORD=ADMIN_PW)
    eng = main.engine
    for sym, (name, market, cat, payout) in PAIRS.items():
        eng.instruments[sym] = {"symbol": sym, "name": name, "category": cat, "payout": payout, "open": sym != "EURUSD", "market": market}
    t = time.time()
    for i in range(40):                                                     # a minute of ticks on two pairs: they are streaming and have a forming candle
        for sym in ("EURUSD_otc", "AUDCAD_otc"):
            eng.on_tick(sym, t - 40 + i, 1.1 + 0.0001 * i)
    adm = _client(main)
    assert adm.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW}).status_code == 200
    return main, eng, adm


def login(main, adm, name):
    assert adm.post("/api/users", json={"username": name, "password": f"{name}-pass-12345", "role": "trader", "must_change_pw": False}, headers=H).status_code == 200
    c = _client(main)
    assert c.post("/api/auth/login", json={"username": name, "password": f"{name}-pass-12345"}).status_code == 200
    return c


def test_nothing_is_locked_until_the_admin_turns_locking_on():
    main, eng, adm = setup()
    anna = login(main, adm, "anna")
    lst = anna.get("/api/markets").json()
    assert lst["locking"] is False and lst["all_open"] is True and not any(p["locked"] for p in lst["pairs"]), "an update must not change what existing users can open"
    assert anna.get("/api/markets/AUDCAD_otc/live").status_code == 200


def test_locks_are_enforced_on_every_route_that_returns_candles():
    main, eng, adm = setup()
    anna, benny = login(main, adm, "anna"), login(main, adm, "benny")
    pro = adm.post("/api/plans", json={"name": "Pro", "description": "", "features": {"cap.charts.all_pairs": True}}, headers=H).json()
    ids = {u["username"]: u["id"] for u in adm.get("/api/users").json()}
    assert adm.patch(f"/api/users/{ids['benny']}", json={"plan_id": pro["id"]}, headers=H).status_code == 200
    assert anna.get("/api/platform-admin/settings").status_code == 403 and anna.put("/api/platform-admin/settings", json={"lock": True}, headers=H).status_code == 403
    s = adm.put("/api/platform-admin/settings", json={"lock": True, "free_pairs": ["EURUSD_otc", "EURUSD_otc"], "signup_open": False}, headers=H).json()
    assert s["lock"] is True and s["free_pairs"] == ["EURUSD_otc"]
    assert adm.put("/api/platform-admin/settings", json={"lock": True, "free_pairs": ["../x"]}, headers=H).status_code == 400

    lst = {p["symbol"]: p for p in anna.get("/api/markets").json()["pairs"]}
    assert lst["EURUSD_otc"]["locked"] is False and lst["AUDCAD_otc"]["locked"] is True and lst["EURUSD"]["locked"] is True
    assert lst["EURUSD_otc"]["streaming"] is True and lst["AUDCAD_otc"]["payout"] == 79 and lst["EURUSD"]["open"] is False
    assert {p["symbol"]: p["locked"] for p in benny.get("/api/markets").json()["pairs"]}["AUDCAD_otc"] is False, "a plan with 'Open every pair' opens them all"
    assert {p["symbol"]: p["locked"] for p in adm.get("/api/markets").json()["pairs"]}["AUDCAD_otc"] is False

    assert anna.get("/api/markets/EURUSD_otc/live").status_code == 200
    assert anna.get("/api/markets/AUDCAD_otc/live").status_code == 403 and benny.get("/api/markets/AUDCAD_otc/live").status_code == 200
    assert anna.get("/api/markets/NOPE_otc/live").status_code == 404
    bt = {"symbol": "AUDCAD_otc", "tf": 60, "limit": 200}
    assert anna.post("/api/backtests/chart", json=bt, headers=H).status_code == 403 and benny.post("/api/backtests/chart", json=bt, headers=H).status_code == 200
    assert anna.post("/api/backtests/chart", json={**bt, "symbol": "EURUSD_otc"}, headers=H).status_code == 200
    sc = {"spec": {}, "symbol": "AUDCAD_otc", "tf": 60}
    assert anna.post("/api/strategies/chart", json=sc, headers=H).status_code == 403
    assert anna.get("/api/mychart?symbol=AUDCAD_otc").status_code == 403 and anna.get("/api/mychart?symbol=EURUSD_otc").status_code == 200
    assert anna.get("/api/mychart").status_code == 200
    eng.user_sel[ids["anna"]] = {"symbol": "AUDCAD_otc", "period": 60, "at": time.time()}          # the pair open on her own Quotex tab is hers to see
    assert anna.get("/api/mychart?symbol=AUDCAD_otc").status_code == 200

    off = adm.put("/api/platform-admin/settings", json={"lock": False, "free_pairs": [], "signup_open": False}, headers=H).json()
    assert off["lock"] is False and anna.get("/api/markets/BTCUSD_otc/live").status_code == 200, "switching locking off opens everything again"


def test_the_live_answer_carries_what_the_countdown_needs():
    main, eng, adm = setup()
    anna = login(main, adm, "anna")
    now = time.time()
    d = anna.get("/api/markets/EURUSD_otc/live?tf=60").json()
    assert d["status"] == "live" and d["streaming"] is True and d["tf"] == 60 and d["market"] == "OTC" and d["payout"] == 88 and d["name"] == "EUR/USD (OTC)"
    assert d["closes_at"] % 60 == 0 and 0 < d["closes_at"] - d["now"] <= 60 and abs(d["now"] - now) < 5, "the server's clock and the close of the running candle"
    assert d["forming"] and d["forming"]["t"] % 60 == 0 and d["last_price"] is not None and isinstance(d["candles"], list)
    assert anna.get("/api/markets/EURUSD_otc/live?tf=300").json()["closes_at"] % 300 == 0
    assert anna.get("/api/markets/EURUSD_otc/live?tf=7").json()["tf"] == 60, "an unknown timeframe falls back to 1 minute"
    quiet = anna.get("/api/markets/BTCUSD_otc/live").json()
    assert quiet["status"] == "waiting" and quiet["streaming"] is False and quiet["forming"] is None
    assert anna.get("/api/markets/EURUSD/live").json()["status"] == "closed", "a market that is shut says so"


def test_signup_is_closed_until_opened_then_rate_limited():
    main, eng, adm = setup()
    anon = _client(main)
    assert anon.get("/api/auth/config").json() == {"signup_open": False}
    assert anon.post("/api/auth/register", json={"username": "newbie", "password": "newbie-pass-12345", "accept_terms": True}, headers=H).status_code == 403
    adm.put("/api/platform-admin/settings", json={"lock": False, "free_pairs": [], "signup_open": True}, headers=H)
    assert anon.get("/api/auth/config").json() == {"signup_open": True}
    assert anon.post("/api/auth/register", json={"username": "newbie", "password": "short", "accept_terms": True}, headers=H).status_code == 400
    assert anon.post("/api/auth/register", json={"username": "a b", "password": "newbie-pass-12345", "accept_terms": True}, headers=H).status_code == 400
    ok = anon.post("/api/auth/register", json={"username": "newbie", "password": "newbie-pass-12345", "accept_terms": True}, headers=H)
    assert ok.status_code == 200, ok.text
    me = anon.get("/api/me").json()
    assert me["user"]["username"] == "newbie" and me["user"]["role"] == "trader" and me["plan"]["name"] == "Standard", "signed in at once, on the default plan, never as staff"
    assert _client(main).post("/api/auth/register", json={"username": "newbie", "password": "another-pass-12345", "accept_terms": True}, headers=H).status_code == 409
    assert any(a["action"] == "signup" for a in main.auth.audit_tail(20))
    got = [_client(main).post("/api/auth/register", json={"username": f"user{i}", "password": f"user{i}-pass-12345", "accept_terms": True}, headers=H).status_code for i in range(6)]
    assert got == [200, 429, 429, 429, 429, 429], got                   # every attempt counts, valid or not: four were used above, the fifth succeeds, the rest wait
    assert anon.post("/api/auth/register", json={"username": "x" * 61, "password": "newbie-pass-12345", "accept_terms": True}, headers=H).status_code in (422, 429)


def test_public_pricing_needs_no_sign_in_and_shows_no_payment_details():
    main, eng, adm = setup()
    pro = adm.post("/api/plans", json={"name": "Pro", "description": "everything", "features": {}}, headers=H).json()
    m = adm.post("/api/pay-admin/methods", json={"kind": "upi", "label": "UPI", "currency": "INR", "address": "secret@upi", "instructions": "pay here"}, headers=H).json()
    adm.put("/api/pay-admin/prices", json={"plan_id": pro["id"], "method_id": m["id"], "amount": 499, "days": 30}, headers=H)
    pub = _client(main).get("/api/public/plans")
    assert pub.status_code == 200
    assert pub.json() == {"plans": [{"id": pro["id"], "name": "Pro", "description": "everything", "prices": [{"method": "UPI", "currency": "INR", "amount": 499.0, "days": 30}]}]}
    assert "secret@upi" not in pub.text and "pay here" not in pub.text
    assert _client(main).get("/api/markets").status_code == 401, "the markets need a signed-in account"


if __name__ == "__main__":
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                fn()
                print("ok", name)
        print("all markets tests passed")
    finally:
        _cleanup()
