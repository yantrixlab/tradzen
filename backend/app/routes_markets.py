"""HTTP routes of the public chart platform (see markets.py): the market list with its padlocks, the live answer of one pair, the public sign-up / pricing, and the admin's switches
(lock pairs behind a paid plan, the free pairs, open sign-up). Who may call what: auth.py (the sign-up and pricing routes are public, the markets are for any signed-in user, the
switches are admin-only by the default-deny table)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .auth import COOKIE, SESSION_DAYS, AuthError
from .markets import Locked, Markets
from .payments import Payments


class RegisterIn(BaseModel):
    username: str = Field(..., max_length=60)
    password: str = Field(..., max_length=200)


class PlatformIn(BaseModel):
    lock: bool = False
    free_pairs: list[str] = Field(default_factory=list, max_length=200)
    signup_open: bool = False


def bind(engine, auth, markets: Markets, pay: Payments) -> APIRouter:
    r = APIRouter()

    def user(request: Request) -> dict:
        return request.state.user

    # ---- public
    @r.get("/api/auth/config")
    async def config() -> dict:
        """What the sign-in page needs before anybody is signed in."""
        return {"signup_open": bool(auth.enabled and markets.signup_open())}

    @r.get("/api/public/plans")
    async def public_plans() -> dict:
        """The plans on sale with their prices (no payment details: those are shown to a signed-in account at checkout)."""
        cat = pay.catalog({})
        methods = {m["id"]: m for m in cat["methods"]}
        return {"plans": [{"id": p["id"], "name": p["name"], "description": p["description"],
                           "prices": [{"method": methods[x["method_id"]]["label"], "currency": methods[x["method_id"]]["currency"], "amount": x["amount"], "days": x["days"]}
                                      for x in p["prices"] if x["method_id"] in methods]} for p in cat["plans"]]}

    @r.post("/api/auth/register")
    async def register(body: RegisterIn, request: Request, response: Response) -> dict:
        if not auth.enabled or not markets.signup_open():
            raise HTTPException(403, "sign-up is closed")
        ip = request.client.host if request.client else ""
        wait = markets.check_signup_rate(ip)
        if wait:
            raise HTTPException(429, f"too many sign-ups from here; try again in {int(wait // 60) + 1} minutes")
        try:
            u, _ = auth.create_user("sign-up", body.username, body.password, "trader", must_change_pw=False)
            row = auth.login_row(u["username"])
            token = auth.login_finish(True, row, u["username"], ip, request.headers.get("user-agent", ""))
        except AuthError as e:
            raise HTTPException(e.status, e.message)
        auth.audit("sign-up", "signup", u["username"], {"ip": ip})
        response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, secure=auth.production, samesite="lax", path="/")
        return {"ok": True, "username": u["username"]}

    # ---- the markets (any signed-in user)
    @r.get("/api/markets")
    async def listing(request: Request) -> dict:
        return markets.listing(user(request))

    @r.get("/api/markets/{symbol}/live")
    async def live(symbol: str, request: Request, tf: int = 60) -> dict:
        try:
            out = markets.live(user(request), symbol, tf)
        except Locked as e:
            raise HTTPException(403, str(e))
        if out is None:
            raise HTTPException(404, "unknown market")
        return out

    # ---- the admin's switches
    @r.get("/api/platform-admin/settings")
    async def get_settings() -> dict:
        return {**markets.settings(), "pairs": [{"symbol": p["symbol"], "name": p["name"], "market": p["market"]} for p in engine.pair_list()]}

    @r.put("/api/platform-admin/settings")
    async def put_settings(body: PlatformIn, request: Request) -> dict:
        try:
            out = markets.save_settings(body.lock, body.free_pairs, body.signup_open)
        except ValueError as e:
            raise HTTPException(400, str(e))
        auth.audit(user(request)["username"], "platform-settings", "", out)
        return out

    return r
