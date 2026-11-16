"""Everybody's own chart: each account follows the pair open on its own Quotex tab, a pair picked by hand wins, and nobody's pair moves anybody else's."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from test_auth import ADMIN, ADMIN_PW, H, _cleanup, _client, _main  # noqa: E402


def test_each_account_follows_its_own_pair():
    main = _main(QXS_ENV="production", QXS_SECRET_KEY="k" * 32, QXS_ADMIN_USER=ADMIN, QXS_ADMIN_PASSWORD=ADMIN_PW)
    eng = main.engine
    for sym, name in (("EURUSD", "EUR/USD"), ("GBPUSD", "GBP/USD")):
        eng.instruments[sym] = {"symbol": sym, "name": name, "category": "currency", "payout": 85, "open": True, "market": "LIVE"}
    adm = _client(main)
    assert adm.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW}).status_code == 200
    for n in ("anna", "benny"):
        assert adm.post("/api/users", json={"username": n, "password": f"{n}-pass-12345", "role": "trader", "must_change_pw": False}, headers=H).status_code == 200
    ext, web = _client(main), {}
    tok = {}
    for n in ("anna", "benny"):
        tok[n] = {"Authorization": "Bearer " + ext.post("/api/auth/extension-login", json={"username": n, "password": f"{n}-pass-12345", "device": "d", "feeder": False}).json()["token"]}
        web[n] = _client(main)
        assert web[n].post("/api/auth/login", json={"username": n, "password": f"{n}-pass-12345"}).status_code == 200

    def sel(n, asset, period=60):
        assert ext.post("/ingest", json={"items": [{"k": "sel", "asset": asset, "period": period}], "after_seq": 0}, headers=tok[n]).status_code == 200

    # nobody has opened a pair yet
    r = web["anna"].get("/api/mychart").json()
    assert r["selected"] is None and r["mine"] is None and r["following"] is True and any(s["symbol"] == "EURUSD" for s in r["symbols"])
    sel("anna", "EURUSD")
    sel("benny", "GBPUSD", 300)
    a, b = web["anna"].get("/api/mychart").json(), web["benny"].get("/api/mychart").json()
    assert a["selected"]["symbol"] == "EURUSD" and a["selected"]["tf"] == 60 and a["following"] and a["mine"]["symbol"] == "EURUSD"
    assert b["selected"]["symbol"] == "GBPUSD" and b["selected"]["tf"] == 300, "each account has its own pair and timeframe"
    assert eng.selected["symbol"] is None, "an ordinary account never moves the shared pair"
    # she switches pairs on Quotex: the chart follows
    sel("anna", "GBPUSD", 900)
    a = web["anna"].get("/api/mychart").json()
    assert a["selected"]["symbol"] == "GBPUSD" and a["selected"]["tf"] == 900
    # a pair or timeframe picked by hand wins; the Quotex pair stays reported
    a = web["anna"].get("/api/mychart?symbol=EURUSD&tf=60").json()
    assert a["selected"]["symbol"] == "EURUSD" and a["selected"]["tf"] == 60 and a["following"] is False and a["mine"]["symbol"] == "GBPUSD"
    assert web["anna"].get("/api/mychart?symbol=NOPE").json()["selected"] is None
    assert web["anna"].get("/api/mychart?tf=7").json()["selected"]["tf"] == 900, "an unknown timeframe is ignored"
    # junk from an extension does not break anything
    assert ext.post("/ingest", json={"items": [{"k": "sel", "asset": "X" * 500, "period": "abc"}]}, headers=tok["benny"]).status_code == 200
    assert len(web["benny"].get("/api/mychart").json()["mine"]["symbol"]) <= 40
    # the price-feed account moves the shared pair as before, and has its own chart too
    feeder = {"Authorization": "Bearer " + ext.post("/api/auth/extension-login", json={"username": ADMIN, "password": ADMIN_PW, "device": "f", "feeder": True}).json()["token"]}
    ext.post("/ingest", json={"items": [{"k": "sel", "asset": "EURUSD", "period": 60}]}, headers=feeder)
    assert eng.selected["symbol"] == "EURUSD" and adm.get("/api/mychart").json()["selected"]["symbol"] == "EURUSD"
    # signed out: nothing; the side panel's limited session may read it
    assert _client(main).get("/api/mychart").status_code == 401
    t = ext.post("/api/auth/panel-ticket", headers=tok["anna"]).json()["ticket"]
    s = ext.post("/api/auth/panel-session", json={"ticket": t}).json()["token"]
    assert ext.get("/api/mychart", headers={"Authorization": "Session " + s}).json()["selected"]["symbol"] == "GBPUSD"
    # removing an account forgets its pair
    eng.drop_user(ids := next(u["id"] for u in adm.get("/api/users").json() if u["username"] == "benny"))
    assert ids not in eng.user_sel


if __name__ == "__main__":
    try:
        for name, fn in list(globals().items()):
            if name.startswith("test_"):
                fn()
                print("ok", name)
    finally:
        _cleanup()
