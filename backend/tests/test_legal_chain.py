"""Phase 5: the editable legal texts that must be accepted (at sign-up and at checkout, with the version recorded) and the ADVISORY on-chain check of USDT payments (TRC20 through TronGrid,
BEP20 / ERC20 through JSON-RPC) tested with canned answers in the documented shapes - the network is never touched. Run from backend/:  .venv\\Scripts\\python tests\\test_legal_chain.py"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import chaincheck as cc  # noqa: E402
from app import legal  # noqa: E402
from app.db import open_db  # noqa: E402
from test_auth import ADMIN, ADMIN_PW, H, _cleanup, _client, _main  # noqa: E402

MINE = "11" * 20                                # the admin's receiving address as 20 bytes
OTHER = "22" * 20
TRON_MINE = cc.tron_from_hex20(MINE)
NOW = time.time()


class FakeFetcher:
    """Canned answers. tron: the body of /v1/transactions/{id}/events; evm: {method: result}; fail: raise ConnectionError."""

    def __init__(self, tron=None, evm=None, fail=False) -> None:
        self.tron, self.evm, self.fail, self.calls = tron, evm or {}, fail, []

    def get(self, url, headers=None):
        self.calls.append(url)
        if self.fail:
            raise ConnectionError("boom")
        return self.tron

    def post(self, url, body):
        self.calls.append((url, body["method"]))
        if self.fail:
            raise ConnectionError("boom")
        return {"jsonrpc": "2.0", "id": 1, "result": self.evm.get(body["method"])}


def tron_event(to=MINE, value="5000000", contract=cc.USDT["TRC20"]["contract"], ts=None, name="Transfer"):
    return {"data": [{"event_name": name, "contract_address": contract, "block_timestamp": int((ts or NOW) * 1000), "result": {"from": "0x" + OTHER, "to": "0x" + to, "value": value}}], "success": True}


def evm_receipt(net="BEP20", to=MINE, units=5, status="0x1", block=100, latest=130, ts=None):
    c = cc.USDT[net]
    amount = hex(units * 10 ** c["decimals"])
    pad = lambda h: "0x" + "0" * 24 + h  # noqa: E731
    rc = {"status": status, "blockNumber": hex(block), "logs": [{"address": c["contract"], "topics": [cc.TRANSFER_TOPIC, pad(OTHER), pad(to)], "data": amount}]}
    return {"eth_getTransactionReceipt": rc, "eth_blockNumber": hex(latest), "eth_getBlockByNumber": {"timestamp": hex(int(ts or NOW))}}


def ok_checks(res):
    return {c["label"]: c["ok"] for c in res["checks"]}


# ------------------------------------------------------------------ addresses
def test_the_base58_addresses_validate_against_the_real_usdt_contract():
    usdt = cc.USDT["TRC20"]["contract"]
    h = cc.canon(usdt)
    assert h and len(h) == 40, "the checksum of a real TRON address (the USDT contract) must pass"
    assert cc.tron_from_hex20(h) == usdt, "and encoding it again gives the same text"
    assert cc.canon(usdt[:-1] + "x") is None and cc.canon("T" + "1" * 10) is None, "a damaged address is rejected"
    assert cc.canon("0x" + "AB" * 20) == "ab" * 20 and cc.canon("41" + "cd" * 20) == "cd" * 20 and cc.canon("nonsense") is None and cc.canon(None) is None
    assert cc.canon(TRON_MINE) == MINE


# ------------------------------------------------------------------ TRC20
def test_a_good_trc20_payment_is_confirmed():
    r = cc.check("TRC20", "a" * 64, TRON_MINE, 5.0, NOW - 60, FakeFetcher(tron=tron_event()), NOW)
    assert r["status"] == "confirmed" and all(ok_checks(r).values()) and r["amount"] == 5.0 and r["to"] == TRON_MINE, r


def test_trc20_problems_are_each_reported():
    f = lambda body: cc.check("TRC20", "a" * 64, TRON_MINE, 5.0, NOW - 60, FakeFetcher(tron=body), NOW)  # noqa: E731
    wrong = f(tron_event(to=OTHER))
    assert wrong["status"] == "mismatch" and ok_checks(wrong)["Sent to your address"] is False and cc.tron_from_hex20(OTHER) in str(wrong["checks"])
    low = f(tron_event(value="4000000"))
    assert low["status"] == "mismatch" and ok_checks(low)["Amount at least 5 USDT"] is False
    assert f(tron_event(value="4995000"))["status"] == "confirmed", "a cent of slack for rounding"
    assert f({"data": [], "success": True})["status"] == "not_found"
    assert f(tron_event(contract="TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj"))["status"] == "not_found", "a transfer of some other token is not a USDT payment"
    assert f(tron_event(name="Approval"))["status"] == "not_found"
    assert f("<html>")["status"] == "unreadable" and f({"nope": 1})["status"] == "unreadable"
    old = f(tron_event(ts=NOW - 30 * 86400))
    assert old["status"] == "mismatch" and ok_checks(old)["Made around the time of the order"] is False
    nodata = cc.check("TRC20", "a" * 64, "", 5.0, NOW, FakeFetcher(tron=tron_event()), NOW)
    assert nodata["status"] == "mismatch" and ok_checks(nodata)["Sent to your address"] is False, "no address on the payment method: cannot be compared"
    err = cc.check("TRC20", "a" * 64, TRON_MINE, 5.0, NOW, FakeFetcher(fail=True), NOW)
    assert err["status"] == "error" and "Could not reach" in err["summary"], "a network problem is a result, not a crash"
    assert cc.check("OMNI", "x", "y", 1, NOW)["status"] == "error"


# ------------------------------------------------------------------ BEP20 / ERC20
def test_evm_payments_use_the_right_contract_and_decimals():
    good = cc.check("BEP20", "0x" + "b" * 64, "0x" + MINE, 5.0, NOW - 60, FakeFetcher(evm=evm_receipt("BEP20")), NOW)
    assert good["status"] == "confirmed" and good["amount"] == 5.0 and good["confirmations"] == 31, good
    erc = cc.check("ERC20", "0x" + "b" * 64, "0x" + MINE.upper(), 5.0, NOW - 60, FakeFetcher(evm=evm_receipt("ERC20")), NOW)
    assert erc["status"] == "confirmed", "ERC20 USDT has 6 decimals and the address comparison ignores case"
    fresh = cc.check("BEP20", "0x" + "b" * 64, "0x" + MINE, 5.0, NOW - 60, FakeFetcher(evm=evm_receipt("BEP20", block=100, latest=102)), NOW)
    assert fresh["status"] == "mismatch" and fresh["confirmations"] == 3 and not ok_checks(fresh)["Confirmed (15+ confirmations)"]
    failed = cc.check("BEP20", "0x" + "b" * 64, "0x" + MINE, 5.0, NOW, FakeFetcher(evm=evm_receipt("BEP20", status="0x0")), NOW)
    assert failed["status"] == "mismatch" and "failed" in failed["summary"]
    unknown = cc.check("BEP20", "0x" + "b" * 64, "0x" + MINE, 5.0, NOW, FakeFetcher(evm={"eth_getTransactionReceipt": None}), NOW)
    assert unknown["status"] == "not_found"
    wrongto = cc.check("BEP20", "0x" + "b" * 64, "0x" + MINE, 5.0, NOW, FakeFetcher(evm=evm_receipt("BEP20", to=OTHER)), NOW)
    assert wrongto["status"] == "mismatch" and not ok_checks(wrongto)["Sent to your address"]
    # a BEP20 receipt whose log is from another contract is not a USDT transfer
    other = evm_receipt("BEP20")
    other["eth_getTransactionReceipt"]["logs"][0]["address"] = "0x" + "99" * 20
    assert cc.check("BEP20", "0x" + "b" * 64, "0x" + MINE, 5.0, NOW, FakeFetcher(evm=other), NOW)["checks"][0]["ok"] is False
    assert cc.check("BEP20", "0x" + "b" * 64, "0x" + MINE, 5.0, NOW, FakeFetcher(evm={"eth_getTransactionReceipt": {"junk": 1}}), NOW)["status"] == "unreadable"


# ------------------------------------------------------------------ the legal texts
def test_the_legal_texts_have_templates_a_version_and_can_be_edited():
    db = open_db(Path(tempfile.mkdtemp()) / "t.db")
    p = legal.public(db)
    assert p["version"] == 1 and p["edited"] is False and [s["key"] for s in p["sections"]] == ["terms", "risk", "refund"]
    assert all(len(s["text"]) > 200 for s in p["sections"]) and "not legal advice" in legal.__doc__
    p2 = legal.save(db, {"terms": "My terms.", "risk": "", "refund": "7 days."})
    assert p2["version"] == 2 and p2["edited"] is True and p2["sections"][0]["text"] == "My terms."
    assert p2["sections"][1]["text"] == legal.DEFAULTS["risk"], "an empty text goes back to the template"
    assert legal.save(db, {"terms": "My terms.", "risk": "", "refund": "7 days."})["version"] == 2, "saving the same words does not raise the version"
    assert legal.save(db, {"terms": "My terms, changed.", "risk": "", "refund": "7 days."})["version"] == 3
    try:
        legal.save(db, {"terms": "x" * (legal.MAX_LEN + 1)})
        raise AssertionError("an enormous text must be refused")
    except ValueError:
        pass


def test_the_terms_must_be_accepted_and_the_version_is_recorded_and_the_chain_check_is_admin_only():
    main = _main(QXS_ENV="production", QXS_SECRET_KEY="k" * 32, QXS_ADMIN_USER=ADMIN, QXS_ADMIN_PASSWORD=ADMIN_PW)
    cc.Fetcher = lambda *a, **k: FakeFetcher(tron=tron_event())                    # no network in a test: the default fetcher is replaced
    anon, adm = _client(main), _client(main)
    pub = anon.get("/api/public/legal")
    assert pub.status_code == 200 and pub.json()["version"] == 1 and len(pub.json()["sections"]) == 3, "readable before signing in"
    assert adm.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW}).status_code == 200
    assert adm.put("/api/platform-admin/settings", json={"lock": False, "free_pairs": [], "signup_open": True}, headers=H).status_code == 200

    # sign-up needs the tick, and records the version
    body = {"username": "newbie", "password": "newbie-pass-12345"}
    assert anon.post("/api/auth/register", json=body, headers=H).status_code == 400
    assert anon.post("/api/auth/register", json={**body, "accept_terms": False}, headers=H).status_code == 400
    saved = adm.put("/api/pay-admin/legal", json={"terms": "Edited terms.", "risk": "", "refund": ""}, headers=H).json()
    assert saved["version"] == 2
    assert anon.post("/api/auth/register", json={**body, "accept_terms": True}, headers=H).status_code == 200
    row = main.engine.db.execute("SELECT terms_version, terms_at FROM users WHERE username='newbie'").fetchone()
    assert row[0] == 2 and row[1] > 0, "the version the person agreed to is stored"
    assert anon.get("/api/public/legal").json()["sections"][0]["text"] == "Edited terms."
    assert anon.put("/api/pay-admin/legal", json={"terms": "x"}, headers=H).status_code == 403 and anon.get("/api/pay-admin/legal").status_code == 403, "the new trader (signed in by sign-up) cannot edit the texts"
    assert _client(main).put("/api/pay-admin/legal", json={"terms": "x"}, headers=H).status_code == 401

    # checkout needs it too
    pro = adm.post("/api/plans", json={"name": "Pro", "description": "", "features": {}}, headers=H).json()
    usdt = adm.post("/api/pay-admin/methods", json={"kind": "usdt", "label": "USDT", "currency": "USDT", "address": TRON_MINE}, headers=H).json()
    upi = adm.post("/api/pay-admin/methods", json={"kind": "upi", "label": "UPI", "currency": "INR", "address": "a@upi"}, headers=H).json()
    for m, amt in ((usdt, 5), (upi, 499)):
        adm.put("/api/pay-admin/prices", json={"plan_id": pro["id"], "method_id": m["id"], "amount": amt, "days": 30}, headers=H)
    order = {"plan_id": pro["id"], "method_id": usdt["id"], "reference": "a" * 64, "network": "TRC20"}
    assert anon.post("/api/pay/orders", json=order, headers=H).status_code == 400, "no tick, no order"
    assert anon.post("/api/pay/orders", json={**order, "accept_terms": False}, headers=H).status_code == 400
    o = anon.post("/api/pay/orders", json={**order, "accept_terms": True}, headers=H)
    assert o.status_code == 200 and o.json()["terms_version"] == 2, o.text

    # the advisory check: admin only, stored on the order, never approves
    oid = o.json()["id"]
    assert anon.post(f"/api/pay-admin/orders/{oid}/chain-check", headers=H).status_code == 403
    res = adm.post(f"/api/pay-admin/orders/{oid}/chain-check", headers=H)
    assert res.status_code == 200 and res.json()["status"] == "confirmed", res.text
    listed = adm.get("/api/pay-admin/orders?status=pending").json()["orders"][0]
    assert listed["chain"]["status"] == "confirmed" and listed["status"] == "pending", "the check is advice: the order is still waiting for the admin"
    assert adm.post("/api/pay-admin/orders/99999/chain-check", headers=H).status_code == 404
    anon.post(f"/api/pay/orders/{oid}/cancel", headers=H)
    upi_order = anon.post("/api/pay/orders", json={"plan_id": pro["id"], "method_id": upi["id"], "reference": "123456789012", "accept_terms": True}, headers=H).json()
    assert adm.post(f"/api/pay-admin/orders/{upi_order['id']}/chain-check", headers=H).status_code == 400, "only USDT payments can be looked up"
    assert any(a["action"] == "legal-save" for a in main.auth.audit_tail(30)) and any(a["action"] == "pay-chain-check" for a in main.auth.audit_tail(30))


if __name__ == "__main__":
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                fn()
                print("ok", name)
        print("all legal and chain tests passed")
    finally:
        _cleanup()
