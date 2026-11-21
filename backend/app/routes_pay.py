"""HTTP routes of the plan payments (see payments.py). A signed-in user sees the plans and payment methods, submits the reference of a payment and follows its status
(/api/pay/...); the admin keeps the payment methods and prices and reviews the orders (/api/pay-admin/..., admin-only by the default-deny table in auth.py)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .payments import PayError, Payments


class OrderIn(BaseModel):
    plan_id: int
    method_id: int
    reference: str = Field(..., max_length=200)
    network: str | None = None
    note: str = Field("", max_length=400)
    proof: str | None = Field(None, max_length=2_200_000)        # optional screenshot, a data URL


class MethodIn(BaseModel):
    kind: str
    label: str
    currency: str
    address: str = ""
    instructions: str = ""
    active: bool = True
    sort: int = 0


class QrIn(BaseModel):
    image: str | None = Field(None, max_length=900_000)          # a data URL; null removes the QR


class PriceIn(BaseModel):
    plan_id: int
    method_id: int
    amount: float | None = None                                  # empty / 0 = not sold with this method
    days: int = 30


class RejectIn(BaseModel):
    reason: str = Field("", max_length=300)


class ApproveIn(BaseModel):
    note: str = Field("", max_length=300)


def bind(engine, auth, pay: Payments) -> APIRouter:
    r = APIRouter()

    def call(fn, *a, **k):
        try:
            return fn(*a, **k)
        except PayError as e:
            raise HTTPException(e.status, e.message)

    def user(request: Request) -> dict:
        return request.state.user

    # ---- the buyer
    @r.get("/api/pay/plans")
    async def catalog(request: Request) -> dict:
        return pay.catalog(user(request))

    @r.get("/api/pay/orders")
    async def my_orders(request: Request) -> list[dict]:
        return [{k: v for k, v in o.items() if k not in ("user_id", "username", "reviewer")} for o in pay.my_orders(user(request)["id"])]

    @r.post("/api/pay/orders")
    async def submit(body: OrderIn, request: Request) -> dict:
        u = user(request)
        o = call(pay.submit, u, body.plan_id, body.method_id, body.reference, body.network, body.note, body.proof)
        auth.audit(u.get("username", "?"), "pay-submit", str(o["id"]), {"plan": o["plan"], "method": o["method"], "amount": o["amount"], "currency": o["currency"]})
        return o

    @r.post("/api/pay/orders/{oid}/cancel")
    async def cancel(oid: int, request: Request) -> dict:
        o = call(pay.cancel, user(request)["id"], oid)
        auth.audit(user(request).get("username", "?"), "pay-cancel", str(oid))
        return o

    @r.get("/api/pay/qr/{name}")
    async def qr(name: str) -> FileResponse:
        p = pay.image_path("qr", name)
        if p is None:
            raise HTTPException(404, "no such image")
        return FileResponse(p, headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})

    # ---- the admin
    @r.get("/api/pay-admin/overview")
    async def overview() -> dict:
        return {"methods": pay.methods(), "prices": pay.prices(), "stats": pay.stats(),
                "plans": [{"id": p["id"], "name": p["name"]} for p in pay.plan_list()]}

    @r.post("/api/pay-admin/methods")
    async def method_create(body: MethodIn, request: Request) -> dict:
        m = call(pay.save_method, None, body.model_dump())
        auth.audit(user(request)["username"], "pay-method-create", m["label"])
        return m

    @r.put("/api/pay-admin/methods/{mid}")
    async def method_update(mid: int, body: MethodIn, request: Request) -> dict:
        m = call(pay.save_method, mid, body.model_dump())
        auth.audit(user(request)["username"], "pay-method-update", m["label"], {"active": m["active"]})
        return m

    @r.delete("/api/pay-admin/methods/{mid}")
    async def method_delete(mid: int, request: Request) -> dict:
        label = call(pay.method, mid)["label"]
        what = call(pay.delete_method, mid)
        auth.audit(user(request)["username"], "pay-method-delete", label, {"result": what})
        return {"ok": True, "result": what}

    @r.put("/api/pay-admin/methods/{mid}/qr")
    async def method_qr(mid: int, body: QrIn, request: Request) -> dict:
        m = call(pay.set_qr, mid, body.image)
        auth.audit(user(request)["username"], "pay-qr", m["label"], {"removed": body.image is None})
        return m

    @r.put("/api/pay-admin/prices")
    async def price_set(body: PriceIn, request: Request) -> dict:
        call(pay.set_price, body.plan_id, body.method_id, body.amount, body.days)
        auth.audit(user(request)["username"], "pay-price", f"plan {body.plan_id} / method {body.method_id}", {"amount": body.amount, "days": body.days})
        return {"ok": True, "prices": pay.prices()}

    @r.get("/api/pay-admin/orders")
    async def orders(status: str | None = None, q: str = "", limit: int = 300) -> dict:
        return {"orders": call(pay.orders, status, q, limit), "stats": pay.stats()}

    @r.post("/api/pay-admin/orders/{oid}/approve")
    async def approve(oid: int, body: ApproveIn, request: Request) -> dict:
        o = call(pay.approve, oid, user(request)["username"], body.note)
        auth.audit(user(request)["username"], "pay-approve", str(oid), {"user": o["username"], "plan": o["plan"], "until": o["plan_until"], "reference": o["reference"]})
        return o

    @r.post("/api/pay-admin/orders/{oid}/reject")
    async def reject(oid: int, body: RejectIn, request: Request) -> dict:
        o = call(pay.reject, oid, user(request)["username"], body.reason)
        auth.audit(user(request)["username"], "pay-reject", str(oid), {"user": o["username"], "reason": o["review_note"]})
        return o

    @r.get("/api/pay-admin/proof/{name}")
    async def proof(name: str) -> FileResponse:
        p = pay.image_path("proof", name)
        if p is None:
            raise HTTPException(404, "no such image")
        return FileResponse(p, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    return r
