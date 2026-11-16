"""HTTP routes of the live sessions (see live_session.py). The admin runs a session; everybody whose plan includes the Sessions menu watches it.
Who may call what is decided by the rules in auth.py: the GETs and the alert switch are open to every signed-in user with the Sessions menu, everything else is admin-only."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .live_session import SessionError


class StartIn(BaseModel):
    title: str = ""
    tier: str = "free"
    symbol: str | None = None
    tf: int | None = None


class PairIn(BaseModel):
    symbol: str | None = None          # None = follow the admin's own Quotex tab again
    tf: int | None = None


class SignalIn(BaseModel):
    direction: str
    entry: str = "next"                # next | after: the candle that opens next, or the one after it
    expiry: int = 1                    # candles the trade runs
    area_lo: float | None = None
    area_hi: float | None = None
    area_label: str = ""
    note: str = ""
    send: bool = False                 # false = a heads-up first, sent later


class AlertsIn(BaseModel):
    on: bool


def bind(engine, auth, features) -> APIRouter:
    router = APIRouter(prefix="/api/sessions")
    ss = engine.sessions

    def call(fn, *a, **k):
        try:
            return fn(*a, **k)
        except SessionError as e:
            raise HTTPException(e.status, e.message)

    def who(request: Request) -> tuple[dict, bool, bool]:
        user = request.state.user
        return user, user.get("role") == "admin", bool(features.can(user, "cap.sessions.premium"))

    def audit(request: Request, action: str, target: str, detail: dict | None = None) -> None:
        auth.audit(request.state.user.get("username", "?"), action, target, detail)

    # ---- everybody with the Sessions menu
    @router.get("/live")
    async def live(request: Request) -> dict:
        user, admin, premium = who(request)
        out = ss.view(premium, admin)
        out["alerts_on"] = user["id"] not in ss.muted
        return out

    @router.get("")
    async def history(request: Request, limit: int = 50) -> list[dict]:
        _, admin, premium = who(request)
        return ss.history(premium, admin, limit)

    @router.put("/alerts")
    async def alerts(body: AlertsIn, request: Request) -> dict:
        ss.mute(request.state.user["id"], body.on)
        return {"alerts_on": body.on}

    @router.get("/{sid}")
    async def detail(sid: int, request: Request) -> dict:
        _, admin, premium = who(request)
        return call(ss.detail, sid, premium, admin)

    # ---- the admin
    @router.post("")
    async def start(body: StartIn, request: Request) -> dict:
        s = call(ss.start, request.state.user["id"], body.title, body.tier, body.symbol, body.tf)
        audit(request, "session-start", str(s["id"]), {"title": s["title"], "tier": s["tier"]})
        return s

    @router.post("/{sid}/end")
    async def end(sid: int, request: Request) -> dict:
        s = call(ss.end, sid)
        audit(request, "session-end", str(sid), {"title": s["title"]})
        return s

    @router.put("/{sid}/pair")
    async def pair(sid: int, body: PairIn, request: Request) -> dict:
        s = call(ss.set_pair, sid, body.symbol, body.tf)
        audit(request, "session-pair", str(sid), {"symbol": body.symbol, "tf": body.tf})
        return s

    @router.post("/{sid}/signals")
    async def new_signal(sid: int, body: SignalIn, request: Request) -> dict:
        sig = call(ss.create_signal, sid, body.direction, body.entry, body.expiry, body.area_lo, body.area_hi, body.area_label, body.note, body.send)
        audit(request, "session-signal" if sig["status"] == "pending" else "session-headsup", str(sid), {"signal": sig["id"], "direction": sig["direction"], "symbol": sig["symbol"]})
        return sig

    @router.post("/{sid}/signals/{sig_id}/send")
    async def send(sid: int, sig_id: int, request: Request) -> dict:
        sig = call(ss.send, sid, sig_id)
        audit(request, "session-signal", str(sid), {"signal": sig_id, "direction": sig["direction"], "symbol": sig["symbol"]})
        return sig

    @router.post("/{sid}/signals/{sig_id}/cancel")
    async def cancel(sid: int, sig_id: int, request: Request) -> dict:
        sig = call(ss.cancel, sid, sig_id)
        audit(request, "session-cancel", str(sid), {"signal": sig_id})
        return sig

    return router
