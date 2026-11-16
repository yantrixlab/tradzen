"""Display preferences are kept per account on the server (chart switches follow the person), validated, private, and a zone on the chart ends only when a candle closes through it."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from test_auth import ADMIN, ADMIN_PW, H, _cleanup, _client, _main  # noqa: E402


def test_prefs_are_per_account_and_validated():
    main = _main(QXS_ENV="production", QXS_SECRET_KEY="k" * 32, QXS_ADMIN_USER=ADMIN, QXS_ADMIN_PASSWORD=ADMIN_PW)
    adm = _client(main)
    assert adm.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW}).status_code == 200
    assert adm.post("/api/users", json={"username": "anna", "password": "anna-pass-12345", "role": "trader", "must_change_pw": False}, headers=H).status_code == 200
    anna = _client(main)
    assert anna.post("/api/auth/login", json={"username": "anna", "password": "anna-pass-12345"}).status_code == 200
    assert _client(main).get("/api/prefs").status_code == 401, "signed out: nothing"
    assert anna.get("/api/prefs").json() == {"prefs": {}}
    r = anna.put("/api/prefs", json={"prefs": {"chart.zone-text": False, "chart.indicators": [{"k": "ema", "n": 20}], "chart.mtg": 2}}, headers=H)
    assert r.status_code == 200 and r.json()["prefs"]["chart.zone-text"] is False
    assert anna.get("/api/prefs").json()["prefs"]["chart.indicators"] == [{"k": "ema", "n": 20}]
    assert adm.get("/api/prefs").json() == {"prefs": {}}, "another account sees none of it"
    # a change merges; null removes
    anna.put("/api/prefs", json={"prefs": {"chart.zone-text": True, "chart.mtg": None}}, headers=H)
    p = anna.get("/api/prefs").json()["prefs"]
    assert p["chart.zone-text"] is True and "chart.mtg" not in p and "chart.indicators" in p
    # junk is refused
    assert anna.put("/api/prefs", json={"prefs": {"Bad Key!": 1}}, headers=H).status_code == 400
    assert anna.put("/api/prefs", json={"prefs": {"chart.big": "x" * 5000}}, headers=H).status_code == 400
    assert anna.put("/api/prefs", json={"prefs": {f"k{i}": 1 for i in range(41)}}, headers=H).status_code == 400
    assert anna.put("/api/prefs", json={"prefs": "nope"}, headers=H).status_code == 422
    assert anna.put("/api/prefs", json={"prefs": {"chart.x": 1}}).status_code in (400, 403), "no CSRF header, no change"
    # the extension's side panel (limited session) can read and set them too
    ext = _client(main)
    tok = {"Authorization": "Bearer " + ext.post("/api/auth/extension-login", json={"username": "anna", "password": "anna-pass-12345", "device": "d", "feeder": False}).json()["token"]}
    t = ext.post("/api/auth/panel-ticket", headers=tok).json()["ticket"]
    s = {"Authorization": "Session " + ext.post("/api/auth/panel-session", json={"ticket": t}).json()["token"], "x-requested-with": "qxs"}
    assert ext.get("/api/prefs", headers=s).json()["prefs"]["chart.zone-text"] is True
    assert ext.put("/api/prefs", json={"prefs": {"chart.tags": False}}, headers=s).status_code == 200
    assert anna.get("/api/prefs").json()["prefs"]["chart.tags"] is False


def test_zone_ends_only_when_a_candle_closes_through_it():
    from app.chartdata import close_through
    closes = np.array([10.0, 10.2, 10.1, 10.4, 10.3, 10.6, 10.5, 9.7, 9.9])
    # a supply zone 10.45..10.55 formed at candle 2: it stands until a CLOSE above 10.55 (candle 5: 10.6), a wick or a close inside does not end it
    assert close_through(closes, "supply", 10.45, 10.55, 2) == 5
    # a demand zone 9.8..10.0 formed at candle 2: ended by the first close below 9.8 (candle 7)
    assert close_through(closes, "demand", 9.8, 10.0, 2) == 7
    # the formation candle itself never counts, and a zone nobody closed through is still standing
    assert close_through(closes, "supply", 10.45, 10.55, 5) is None
    assert close_through(closes, "demand", 9.0, 9.5, 0) is None


if __name__ == "__main__":
    try:
        for name, fn in list(globals().items()):
            if name.startswith("test_"):
                fn()
                print("ok", name)
    finally:
        _cleanup()
