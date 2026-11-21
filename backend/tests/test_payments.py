"""Plans bought with manual payments: reference formats, images, one live order per reference, prices copied from the list, approval that switches a plan on (and renews without
losing days), rejection, and who may call what. Run from backend/:  .venv\\Scripts\\python tests\\test_payments.py"""
import base64
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import payments as P  # noqa: E402
from app.db import open_db  # noqa: E402
from test_auth import ADMIN, ADMIN_PW, H, _cleanup, _client, _main  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
PNG_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()
UTR, UTR2 = "123456789012", "210987654321"
TRC = "a" * 64
BEP = "0x" + "b" * 64


def test_reference_formats():
    assert P.check_reference("upi", None, " 1234 5678 9012 ") == (UTR, UTR)
    for bad in ("12345678901", "1234567890123", "12345678901a", ""):
        try:
            P.check_reference("upi", None, bad)
            raise AssertionError(bad)
        except P.PayError:
            pass
    assert P.check_reference("usdt", "TRC20", TRC.upper())[1] == TRC
    assert P.check_reference("usdt", "BEP20", BEP)[1] == BEP
    for net, bad in (("TRC20", BEP), ("BEP20", TRC), ("ERC20", "0x12"), ("TRC20", "z" * 64)):
        try:
            P.check_reference("usdt", net, bad)
            raise AssertionError((net, bad))
        except P.PayError:
            pass
    assert P.check_reference("other", None, "ABC-123_x")[1] == "abc-123_x"


def test_images_are_sniffed_capped_and_stored_under_a_hash_name():
    d = Path(tempfile.mkdtemp())
    pay = P.Payments(open_db(":memory:"), d, lambda i: {"id": i}, lambda: [])
    name = pay.save_image(PNG_URL, "qr", P.QR_MAX)
    assert name.endswith(".png") and (d / "qr" / name).read_bytes() == PNG and pay.image_path("qr", name) is not None
    assert pay.image_path("qr", "../../etc/passwd") is None and pay.image_path("qr", "x.png") is None and pay.image_path("proof", name) is None
    for bad in ("data:image/png;base64," + base64.b64encode(b"<svg onload=alert(1)>").decode(), "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=", "hello", "",
                "data:image/png;base64," + base64.b64encode(PNG + b"0" * (P.QR_MAX + 5)).decode()):
        try:
            pay.save_image(bad, "qr", P.QR_MAX)
            raise AssertionError("accepted a bad image")
        except P.PayError:
            pass


def test_the_whole_flow_over_http():
    main = _main(QXS_ENV="production", QXS_SECRET_KEY="k" * 32, QXS_ADMIN_USER=ADMIN, QXS_ADMIN_PASSWORD=ADMIN_PW)
    adm = _client(main)
    assert adm.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW}).status_code == 200
    plan = adm.post("/api/plans", json={"name": "Pro", "description": "everything", "features": {}}, headers=H).json()
    for name in ("anna", "benny"):
        assert adm.post("/api/users", json={"username": name, "password": f"{name}-pass-12345", "role": "trader", "must_change_pw": False}, headers=H).status_code == 200
    anna, benny = _client(main), _client(main)
    assert anna.post("/api/auth/login", json={"username": "anna", "password": "anna-pass-12345"}).status_code == 200
    assert benny.post("/api/auth/login", json={"username": "benny", "password": "benny-pass-12345"}).status_code == 200

    # the admin sets up two methods, a QR and prices
    upi = adm.post("/api/pay-admin/methods", json={"kind": "upi", "label": "UPI", "currency": "INR", "address": "shop@upi", "instructions": "Pay, then send the UTR."}, headers=H).json()
    usdt = adm.post("/api/pay-admin/methods", json={"kind": "usdt", "label": "USDT", "currency": "USDT", "address": "TXyz"}, headers=H).json()
    assert adm.post("/api/pay-admin/methods", json={"kind": "upi", "label": "", "currency": "INR"}, headers=H).status_code == 400
    assert adm.post("/api/pay-admin/methods", json={"kind": "card", "label": "x", "currency": "INR"}, headers=H).status_code == 400
    qr = adm.put(f"/api/pay-admin/methods/{upi['id']}/qr", json={"image": PNG_URL}, headers=H).json()
    assert qr["qr"].endswith(".png")
    assert adm.put(f"/api/pay-admin/methods/{upi['id']}/qr", json={"image": "data:image/png;base64,aGVsbG8="}, headers=H).status_code == 400
    assert adm.put("/api/pay-admin/prices", json={"plan_id": plan["id"], "method_id": upi["id"], "amount": 499, "days": 30}, headers=H).status_code == 200
    assert adm.put("/api/pay-admin/prices", json={"plan_id": plan["id"], "method_id": usdt["id"], "amount": 5, "days": 30}, headers=H).status_code == 200
    assert adm.put("/api/pay-admin/prices", json={"plan_id": 999, "method_id": usdt["id"], "amount": 5}, headers=H).status_code == 400

    # who may call what
    assert anna.get("/api/pay-admin/overview").status_code == 403 and anna.post("/api/pay-admin/methods", json={"kind": "upi", "label": "x", "currency": "INR"}, headers=H).status_code == 403
    assert anna.get("/api/pay-admin/orders").status_code == 403 and anna.post("/api/pay-admin/orders/1/approve", json={}, headers=H).status_code == 403
    assert _client(main).get("/api/pay/plans").status_code == 401, "the catalogue needs a signed-in account"

    cat = anna.get("/api/pay/plans").json()
    assert [p["name"] for p in cat["plans"]] == ["Pro"] and {x["amount"] for x in cat["plans"][0]["prices"]} == {499.0, 5.0}
    assert {m["label"] for m in cat["methods"]} == {"UPI", "USDT"} and cat["current"]["paid"] is False
    assert anna.get(f"/api/pay/qr/{qr['qr']}").content == PNG and anna.get("/api/pay/qr/" + "0" * 20 + ".png").status_code == 404

    # submitting
    base = {"plan_id": plan["id"], "method_id": upi["id"], "reference": UTR}
    assert anna.post("/api/pay/orders", json={**base, "reference": "123"}, headers=H).status_code == 400, "a UTR is 12 digits"
    assert anna.post("/api/pay/orders", json={**base, "method_id": 999}, headers=H).status_code == 400
    o = anna.post("/api/pay/orders", json={**base, "amount": 1, "currency": "EUR", "days": 9999, "proof": PNG_URL}, headers=H)
    assert o.status_code == 200, o.text
    o = o.json()
    assert (o["status"], o["amount"], o["currency"], o["days"]) == ("pending", 499.0, "INR", 30), "amount, currency and days come from the price list, not from the user"
    assert o["proof"].endswith(".png")
    assert anna.post("/api/pay/orders", json={**base, "reference": UTR2}, headers=H).status_code == 409, "one pending order per user"
    assert benny.post("/api/pay/orders", json=base, headers=H).status_code == 409, "a reference can be used once, whoever submits it"
    assert benny.post("/api/pay/orders", json={**base, "reference": UTR.upper()}, headers=H).status_code == 409
    assert benny.post("/api/pay/orders", json={"plan_id": plan["id"], "method_id": usdt["id"], "reference": TRC}, headers=H).status_code == 400, "USDT needs the network"
    assert benny.post("/api/pay/orders", json={"plan_id": plan["id"], "method_id": usdt["id"], "reference": TRC, "network": "TRC20"}, headers=H).status_code == 200
    mine = anna.get("/api/pay/orders").json()
    assert [x["status"] for x in mine] == ["pending"] and "username" not in mine[0] and "reviewer" not in mine[0]

    # cancelling frees the reference; benny cannot touch anna's order
    assert benny.post(f"/api/pay/orders/{o['id']}/cancel", headers=H).status_code == 404
    assert anna.post(f"/api/pay/orders/{o['id']}/cancel", headers=H).json()["status"] == "cancelled"
    assert anna.post(f"/api/pay/orders/{o['id']}/cancel", headers=H).status_code == 409
    again = anna.post("/api/pay/orders", json=base, headers=H)
    assert again.status_code == 200, "a cancelled order frees its reference"
    oid = again.json()["id"]

    # the admin's queue: oldest first, searchable; the proof is the admin's to see
    q = adm.get("/api/pay-admin/orders?status=pending").json()
    assert [x["username"] for x in q["orders"]] == ["benny", "anna"] and q["stats"]["pending"] == 2
    assert [x["username"] for x in adm.get("/api/pay-admin/orders?q=anna").json()["orders"]] == ["anna", "anna"]
    assert adm.get("/api/pay-admin/orders?status=bogus").status_code == 400
    assert adm.get("/api/pay-admin/proof/" + o["proof"]).status_code == 200 and anna.get("/api/pay-admin/proof/" + o["proof"]).status_code == 403

    # approving switches the plan on for the paid days
    before = time.time()
    a = adm.post(f"/api/pay-admin/orders/{oid}/approve", json={"note": "matched the bank statement"}, headers=H).json()
    assert a["status"] == "approved" and a["reviewer"] == ADMIN and abs(a["plan_until"] - (before + 30 * 86400)) < 60
    me = anna.get("/api/me").json()
    assert me["plan"]["name"] == "Pro" and abs(me["user"]["plan_until"] - a["plan_until"]) < 1
    assert adm.post(f"/api/pay-admin/orders/{oid}/approve", json={}, headers=H).status_code == 409, "approving twice does nothing twice"
    assert adm.post(f"/api/pay-admin/orders/{oid}/reject", json={"reason": "x"}, headers=H).status_code == 409

    # renewing the same plan adds to the end of the running period
    renew = anna.post("/api/pay/orders", json={**base, "reference": UTR2}, headers=H).json()
    r2 = adm.post(f"/api/pay-admin/orders/{renew['id']}/approve", json={}, headers=H).json()
    assert abs(r2["plan_until"] - (a["plan_until"] + 30 * 86400)) < 1 and abs(r2["plan_from"] - a["plan_until"]) < 1
    assert anna.get("/api/pay/plans").json()["current"]["paid"] is True

    # rejecting needs a reason and tells the user why
    b_id = next(x["id"] for x in adm.get("/api/pay-admin/orders?status=pending").json()["orders"] if x["username"] == "benny")
    assert adm.post(f"/api/pay-admin/orders/{b_id}/reject", json={"reason": ""}, headers=H).status_code == 400
    rj = adm.post(f"/api/pay-admin/orders/{b_id}/reject", json={"reason": "no such transaction"}, headers=H).json()
    assert rj["status"] == "rejected" and benny.get("/api/me").json()["plan"]["name"] != "Pro"
    assert benny.get("/api/pay/orders").json()[0]["review_note"] == "no such transaction"
    assert benny.post("/api/pay/orders", json={"plan_id": plan["id"], "method_id": usdt["id"], "reference": TRC, "network": "TRC20"}, headers=H).status_code == 200, "a rejected reference may be corrected and sent again"

    # a method with orders is only switched off; an unused one is deleted
    assert "switched off" in adm.delete(f"/api/pay-admin/methods/{upi['id']}", headers=H).json()["result"]
    assert [m["label"] for m in anna.get("/api/pay/plans").json()["methods"]] == ["USDT"]
    extra = adm.post("/api/pay-admin/methods", json={"kind": "other", "label": "Cash", "currency": "INR"}, headers=H).json()
    assert adm.delete(f"/api/pay-admin/methods/{extra['id']}", headers=H).json()["result"] == "deleted"
    st = adm.get("/api/pay-admin/overview").json()["stats"]
    assert st["pending"] == 1 and st["all_time"] == [{"currency": "INR", "orders": 2, "amount": 998.0}]
    actions = {a["action"] for a in main.auth.audit_tail(60)}
    assert {"pay-submit", "pay-approve", "pay-reject", "pay-method-create", "pay-qr", "pay-price"} <= actions, actions


if __name__ == "__main__":
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                fn()
                print("ok", name)
        print("all payment tests passed")
    finally:
        _cleanup()
