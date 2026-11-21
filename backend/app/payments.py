"""Plans bought with manual payments: the admin lists payment methods (UPI, USDT ...) with a QR image and instructions and sets what each plan costs per method; a user pays outside the app
and submits the payment's reference (a UPI UTR or a transaction hash) with an optional screenshot; the admin checks it against the bank / wallet and approves or rejects it. Approving
switches the user's plan on for the paid days (the plan ends by itself: features.py already falls back to the default plan once `plan_until` has passed).

What the code guarantees (what it cannot know is whether the money really arrived - that is the admin's check):
  * the amount, currency and days of an order are copied from the price list at submission - the user never sends them
  * a reference can be used by ONE live (pending / approved) order only, whoever submits it; its format is checked per method (UPI UTR = 12 digits, TRC20 hash = 64 hex, BEP20 / ERC20 = 0x + 64 hex)
  * one pending order per user, and at most MAX_PER_DAY submissions a day
  * images (QR codes, proofs) are accepted only as PNG / JPEG / WEBP by their first bytes, size-capped, stored under a hash name and served with nosniff
  * approving is one transaction: the order and the user's plan change together or not at all
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

KINDS = ("upi", "usdt", "other")
NETWORKS = ("TRC20", "BEP20", "ERC20", "other")
MAX_PER_DAY = 5
QR_MAX = 600_000
PROOF_MAX = 1_500_000
MAX_DAYS = 3650
STATUSES = ("pending", "approved", "rejected", "cancelled")
IMG_RE = re.compile(r"^[0-9a-f]{20}\.(png|jpg|webp)$")
_DATA_URL = re.compile(r"^data:image/(png|jpeg|jpg|webp);base64,([A-Za-z0-9+/=\s]+)$")


class PayError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def _sniff(raw: bytes) -> str | None:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return None


def check_reference(kind: str, network: str | None, ref: str) -> tuple[str, str]:
    """(clean reference, normalised key) or PayError. UPI: the 12-digit UTR; USDT: the transaction hash of the chosen network; anything else: 6-100 letters, digits, - or _."""
    ref = re.sub(r"\s+", "", ref or "")
    if kind == "upi":
        if not re.fullmatch(r"\d{12}", ref):
            raise PayError(400, "a UPI reference (UTR) is exactly 12 digits")
    elif kind == "usdt":
        if network == "TRC20":
            ok = re.fullmatch(r"[0-9a-fA-F]{64}", ref)
        elif network in ("BEP20", "ERC20"):
            ok = re.fullmatch(r"0x[0-9a-fA-F]{64}", ref)
        else:
            ok = re.fullmatch(r"[A-Za-z0-9_-]{16,100}", ref)
        if not ok:
            raise PayError(400, {"TRC20": "a TRC20 transaction hash is 64 hexadecimal characters", "BEP20": "a BEP20 transaction hash is 0x followed by 64 hexadecimal characters",
                                 "ERC20": "an ERC20 transaction hash is 0x followed by 64 hexadecimal characters"}.get(network or "", "that transaction ID does not look right (16-100 letters and digits)"))
    elif not re.fullmatch(r"[A-Za-z0-9_-]{6,100}", ref):
        raise PayError(400, "the reference must be 6-100 letters, digits, - or _")
    return ref, ref.lower()


class Payments:
    def __init__(self, db: sqlite3.Connection, pay_dir: Path, plan_lookup, plan_list) -> None:
        """plan_lookup(id) -> {"id", "name", ...} (features.plan); plan_list() -> all plans (features.plans)."""
        self.db = db
        self.dir = Path(pay_dir)
        self.plan_lookup = plan_lookup
        self.plan_list = plan_list

    # ------------------------------------------------------------------ images
    def save_image(self, data_url: str, folder: str, cap: int) -> str:
        m = _DATA_URL.match((data_url or "").strip())
        if not m:
            raise PayError(400, "send the image as a PNG, JPEG or WEBP (a data URL)")
        try:
            raw = base64.b64decode(m.group(2), validate=False)
        except (binascii.Error, ValueError):
            raise PayError(400, "the image could not be read")
        if not raw or len(raw) > cap:
            raise PayError(400, f"the image must be under {cap // 1000} KB")
        ext = _sniff(raw)
        if not ext:
            raise PayError(400, "only PNG, JPEG or WEBP images are accepted")
        name = f"{hashlib.sha256(raw).hexdigest()[:20]}.{ext}"
        d = self.dir / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(raw)
        return name

    def image_path(self, folder: str, name: str) -> Path | None:
        if not IMG_RE.match(name or ""):
            return None
        p = (self.dir / folder / name)
        return p if p.is_file() else None

    # ------------------------------------------------------------------ methods and prices (admin)
    @staticmethod
    def _method(r) -> dict:
        return {"id": r[0], "kind": r[1], "label": r[2], "currency": r[3], "address": r[4], "instructions": r[5], "qr": r[6], "active": bool(r[7]), "sort": r[8]}

    _MCOLS = "id, kind, label, currency, address, instructions, qr_file, active, sort"

    def methods(self, only_active: bool = False) -> list[dict]:
        q = f"SELECT {self._MCOLS} FROM pay_methods" + (" WHERE active=1" if only_active else "") + " ORDER BY sort, id"
        return [self._method(r) for r in self.db.execute(q)]

    def save_method(self, mid: int | None, data: dict) -> dict:
        kind = data.get("kind")
        label = (data.get("label") or "").strip()
        currency = (data.get("currency") or "").strip().upper()
        if kind not in KINDS:
            raise PayError(400, f"kind must be one of {list(KINDS)}")
        if not label or len(label) > 60:
            raise PayError(400, "give the method a name (up to 60 characters)")
        if not re.fullmatch(r"[A-Z]{3,5}", currency):
            raise PayError(400, "the currency is a short code such as INR or USDT")
        address = (data.get("address") or "").strip()[:200]
        instr = (data.get("instructions") or "").strip()[:1500]
        active, sort = int(bool(data.get("active", True))), int(data.get("sort") or 0)
        if mid is None:
            cur = self.db.execute("INSERT INTO pay_methods (kind, label, currency, address, instructions, active, sort, created) VALUES (?,?,?,?,?,?,?,?)",
                                  (kind, label, currency, address, instr, active, sort, time.time()))
            mid = cur.lastrowid
        else:
            if not self.db.execute("SELECT 1 FROM pay_methods WHERE id=?", (mid,)).fetchone():
                raise PayError(404, "no such payment method")
            self.db.execute("UPDATE pay_methods SET kind=?, label=?, currency=?, address=?, instructions=?, active=?, sort=? WHERE id=?", (kind, label, currency, address, instr, active, sort, mid))
        self.db.commit()
        return self.method(mid)

    def method(self, mid: int) -> dict:
        r = self.db.execute(f"SELECT {self._MCOLS} FROM pay_methods WHERE id=?", (mid,)).fetchone()
        if not r:
            raise PayError(404, "no such payment method")
        return self._method(r)

    def delete_method(self, mid: int) -> str:
        """Removes a method nobody has paid with; one with orders is only switched off (the history keeps its name)."""
        self.method(mid)
        used = self.db.execute("SELECT COUNT(*) FROM pay_orders WHERE method_id=?", (mid,)).fetchone()[0]
        if used:
            self.db.execute("UPDATE pay_methods SET active=0 WHERE id=?", (mid,))
            self.db.commit()
            return "switched off (it has orders, so it stays in the history)"
        self.db.execute("DELETE FROM pay_prices WHERE method_id=?", (mid,))
        self.db.execute("DELETE FROM pay_methods WHERE id=?", (mid,))
        self.db.commit()
        return "deleted"

    def set_qr(self, mid: int, data_url: str | None) -> dict:
        self.method(mid)
        name = self.save_image(data_url, "qr", QR_MAX) if data_url else None
        self.db.execute("UPDATE pay_methods SET qr_file=? WHERE id=?", (name, mid))
        self.db.commit()
        return self.method(mid)

    def prices(self) -> list[dict]:
        return [{"plan_id": p, "method_id": m, "amount": a, "days": d} for p, m, a, d in self.db.execute("SELECT plan_id, method_id, amount, days FROM pay_prices ORDER BY plan_id, method_id")]

    def set_price(self, plan_id: int, method_id: int, amount: float | None, days: int = 30) -> None:
        if self.plan_lookup(plan_id)["id"] != plan_id:
            raise PayError(400, "no such plan")
        self.method(method_id)
        if amount is None or amount <= 0:
            self.db.execute("DELETE FROM pay_prices WHERE plan_id=? AND method_id=?", (plan_id, method_id))
        else:
            if not 1 <= days <= MAX_DAYS:
                raise PayError(400, f"days must be between 1 and {MAX_DAYS}")
            if amount > 1e9:
                raise PayError(400, "that amount is too large")
            self.db.execute("INSERT INTO pay_prices (plan_id, method_id, amount, days) VALUES (?,?,?,?) ON CONFLICT(plan_id, method_id) DO UPDATE SET amount=excluded.amount, days=excluded.days",
                            (plan_id, method_id, round(float(amount), 2), int(days)))
        self.db.commit()

    # ------------------------------------------------------------------ what a user sees
    def catalog(self, user: dict) -> dict:
        """Plans that have a price, the active methods with their QR and instructions, and the user's current plan."""
        methods = self.methods(only_active=True)
        ok = {m["id"] for m in methods}
        by_plan: dict[int, list[dict]] = {}
        for pr in self.prices():
            if pr["method_id"] in ok:
                by_plan.setdefault(pr["plan_id"], []).append({"method_id": pr["method_id"], "amount": pr["amount"], "days": pr["days"]})
        plans = [{"id": p["id"], "name": p["name"], "description": p.get("description", ""), "prices": by_plan[p["id"]]} for p in self.plan_list() if p["id"] in by_plan]
        pid, until = user.get("plan_id"), user.get("plan_until")
        active = bool(pid is not None and until and until > time.time())
        cur = self.plan_lookup(pid if active else None)
        return {"methods": [{k: v for k, v in m.items() if k != "active"} for m in methods], "plans": plans,
                "current": {"plan_id": cur["id"], "name": cur["name"], "paid": active, "until": until if active else None}}

    def _order(self, r) -> dict:
        keys = ("id", "user_id", "username", "plan_id", "plan", "method_id", "method", "kind", "amount", "currency", "days", "reference", "network", "note", "proof", "status", "reviewer",
                "review_note", "created", "reviewed", "plan_from", "plan_until", "chain", "terms_version")
        out = dict(zip(keys, r))
        try:
            out["chain"] = json.loads(out["chain"]) if out["chain"] else None
        except ValueError:
            out["chain"] = None
        return out

    _OSQL = ("SELECT o.id, o.user_id, COALESCE(u.username, '#' || o.user_id), o.plan_id, COALESCE(p.name, '#' || o.plan_id), o.method_id, COALESCE(m.label, '#' || o.method_id), COALESCE(m.kind, ''), "
             "o.amount, o.currency, o.days, o.reference, o.network, o.note, o.proof_file, o.status, o.reviewer, o.review_note, o.created, o.reviewed, o.plan_from, o.plan_until, o.chain_check, o.terms_version "
             "FROM pay_orders o LEFT JOIN users u ON u.id = o.user_id LEFT JOIN plans p ON p.id = o.plan_id LEFT JOIN pay_methods m ON m.id = o.method_id")

    def my_orders(self, uid: int) -> list[dict]:
        return [self._order(r) for r in self.db.execute(self._OSQL + " WHERE o.user_id=? ORDER BY o.id DESC LIMIT 100", (uid,))]

    # ------------------------------------------------------------------ submitting
    def submit(self, user: dict, plan_id: int, method_id: int, reference: str, network: str | None = None, note: str = "", proof: str | None = None, terms_version: int | None = None) -> dict:
        uid = user["id"]
        if terms_version is None:
            raise PayError(400, "tick the box to accept the terms, the risk warning and the refund policy")
        if not self.db.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
            raise PayError(400, "plans are bought from a signed-in account")
        m = self.db.execute(f"SELECT {self._MCOLS} FROM pay_methods WHERE id=? AND active=1", (method_id,)).fetchone()
        if not m:
            raise PayError(400, "that payment method is not available")
        price = self.db.execute("SELECT amount, days FROM pay_prices WHERE plan_id=? AND method_id=?", (plan_id, method_id)).fetchone()
        if not price:
            raise PayError(400, "that plan is not sold with this method")
        method = self._method(m)
        if method["kind"] == "usdt":
            if network not in NETWORKS:
                raise PayError(400, f"choose the network: {', '.join(NETWORKS)}")
        else:
            network = None
        ref, key = check_reference(method["kind"], network, reference)
        if self.db.execute("SELECT 1 FROM pay_orders WHERE user_id=? AND status='pending'", (uid,)).fetchone():
            raise PayError(409, "you already have a payment waiting for review - wait for it, or cancel it first")
        if self.db.execute("SELECT COUNT(*) FROM pay_orders WHERE user_id=? AND created > ?", (uid, time.time() - 86400)).fetchone()[0] >= MAX_PER_DAY:
            raise PayError(429, f"at most {MAX_PER_DAY} submissions a day")
        proof_name = self.save_image(proof, "proof", PROOF_MAX) if proof else None
        try:
            cur = self.db.execute("INSERT INTO pay_orders (user_id, plan_id, method_id, amount, currency, days, reference, ref_norm, network, note, proof_file, status, created, terms_version) "
                                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)",
                                  (uid, plan_id, method_id, price[0], method["currency"], price[1], ref, key, network, (note or "").strip()[:300], proof_name, time.time(), terms_version))
            self.db.execute("UPDATE users SET terms_version=?, terms_at=? WHERE id=?", (terms_version, time.time(), uid))
        except sqlite3.IntegrityError:
            raise PayError(409, "this reference was already submitted - each payment can be used once")
        self.db.commit()
        return self._order(self.db.execute(self._OSQL + " WHERE o.id=?", (cur.lastrowid,)).fetchone())

    def cancel(self, uid: int, oid: int) -> dict:
        r = self.db.execute("SELECT status FROM pay_orders WHERE id=? AND user_id=?", (oid, uid)).fetchone()
        if not r:
            raise PayError(404, "no such order")
        if r[0] != "pending":
            raise PayError(409, "only a payment that is still waiting can be cancelled")
        self.db.execute("UPDATE pay_orders SET status='cancelled', reviewed=? WHERE id=?", (time.time(), oid))
        self.db.commit()
        return self._order(self.db.execute(self._OSQL + " WHERE o.id=?", (oid,)).fetchone())

    # ------------------------------------------------------------------ review (admin)
    def orders(self, status: str | None = None, q: str = "", limit: int = 300) -> list[dict]:
        where, args = [], []
        if status:
            if status not in STATUSES:
                raise PayError(400, f"status must be one of {list(STATUSES)}")
            where.append("o.status=?")
            args.append(status)
        q = (q or "").strip().lower()
        if q:
            where.append("(LOWER(o.reference) LIKE ? OR LOWER(COALESCE(u.username, '')) LIKE ?)")
            args += [f"%{q}%", f"%{q}%"]
        sql = self._OSQL + (" WHERE " + " AND ".join(where) if where else "") + (" ORDER BY o.created ASC" if status == "pending" else " ORDER BY o.id DESC") + " LIMIT ?"
        return [self._order(r) for r in self.db.execute(sql, (*args, max(1, min(limit, 1000))))]

    def approve(self, oid: int, reviewer: str, note: str = "") -> dict:
        """Switch the plan on: from now, or from the end of the same plan when it is still running (renewing does not lose days)."""
        with self.db:                                          # one transaction: the order and the user change together
            r = self.db.execute("SELECT user_id, plan_id, days, status FROM pay_orders WHERE id=?", (oid,)).fetchone()
            if not r:
                raise PayError(404, "no such order")
            uid, plan_id, days, status = r
            if status != "pending":
                raise PayError(409, f"this order is already {status}")
            u = self.db.execute("SELECT plan_id, plan_until FROM users WHERE id=?", (uid,)).fetchone()
            if not u:
                raise PayError(409, "the account of this order no longer exists")
            now = time.time()
            start = u[1] if (u[0] == plan_id and u[1] and u[1] > now) else now
            until = start + days * 86400
            self.db.execute("UPDATE users SET plan_id=?, plan_until=? WHERE id=?", (plan_id, until, uid))
            self.db.execute("UPDATE pay_orders SET status='approved', reviewer=?, review_note=?, reviewed=?, plan_from=?, plan_until=? WHERE id=?", (reviewer, (note or "")[:300], now, start, until, oid))
        return self._order(self.db.execute(self._OSQL + " WHERE o.id=?", (oid,)).fetchone())

    def reject(self, oid: int, reviewer: str, reason: str) -> dict:
        reason = (reason or "").strip()
        if not reason:
            raise PayError(400, "give the user a reason")
        r = self.db.execute("SELECT status FROM pay_orders WHERE id=?", (oid,)).fetchone()
        if not r:
            raise PayError(404, "no such order")
        if r[0] != "pending":
            raise PayError(409, f"this order is already {r[0]}")
        self.db.execute("UPDATE pay_orders SET status='rejected', reviewer=?, review_note=?, reviewed=? WHERE id=?", (reviewer, reason[:300], time.time(), oid))
        self.db.commit()
        return self._order(self.db.execute(self._OSQL + " WHERE o.id=?", (oid,)).fetchone())

    def chain_check(self, oid: int, fetch=None) -> dict:
        """The advisory on-chain check of a USDT order (chaincheck.py), stored on the order. Network I/O: call it off the event loop."""
        from . import chaincheck
        r = self.db.execute("SELECT o.network, o.reference, o.amount, o.created, m.address, m.kind FROM pay_orders o LEFT JOIN pay_methods m ON m.id = o.method_id WHERE o.id=?", (oid,)).fetchone()
        if not r:
            raise PayError(404, "no such order")
        network, ref, amount, created, address, kind = r
        if kind != "usdt" or network not in chaincheck.USDT:
            raise PayError(400, "the on-chain check covers USDT payments on TRC20, BEP20 and ERC20")
        res = chaincheck.check(network, ref, address or "", float(amount), float(created or time.time()), fetch)
        self.db.execute("UPDATE pay_orders SET chain_check=? WHERE id=?", (json.dumps(res), oid))
        self.db.commit()
        return res

    def stats(self) -> dict:
        pending = self.db.execute("SELECT COUNT(*) FROM pay_orders WHERE status='pending'").fetchone()[0]
        month = time.strftime("%Y-%m-01", time.gmtime())
        since = time.mktime(time.strptime(month, "%Y-%m-%d")) - time.timezone
        rows = self.db.execute("SELECT currency, COUNT(*), SUM(amount) FROM pay_orders WHERE status='approved' AND reviewed >= ? GROUP BY currency", (since,)).fetchall()
        total = self.db.execute("SELECT currency, COUNT(*), SUM(amount) FROM pay_orders WHERE status='approved' GROUP BY currency").fetchall()
        return {"pending": pending, "month": [{"currency": c, "orders": n, "amount": round(a or 0, 2)} for c, n, a in rows],
                "all_time": [{"currency": c, "orders": n, "amount": round(a or 0, 2)} for c, n, a in total]}
