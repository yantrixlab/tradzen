"""Sign-in, the current user, user management (admin), extension tokens, and the guard that every route passes through (see auth.py for the rules)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from .auth import BEARER_PATHS, COOKIE, CSRF_HEADER, CSRF_VALUE, SESSION_DAYS, Auth, AuthError, allowed, match, verify_password
from .feature_flags import BY_KEY, REGISTRY, Features, FeatureError, feature_json

LOCAL_HOSTS = ("127.0.0.1", "::1", "localhost", "testclient")
_DUMMY_HASH = "scrypt$16384$8$1$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


class LoginIn(BaseModel):
    username: str
    password: str


class PasswordIn(BaseModel):
    old: str | None = None
    new: str


class UserIn(BaseModel):
    username: str
    password: str | None = None
    role: str = "trader"
    must_change_pw: bool = True


class UserPatch(BaseModel):
    role: str | None = None
    active: bool | None = None
    plan_id: int | None = None
    plan_until: float | None = None
    password: str | None = None
    reset_password: bool | None = None
    must_change_pw: bool | None = None


class PlanIn(BaseModel):
    name: str
    description: str = ""
    features: dict = {}
    copy_of: int | None = None


class PlanPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    features: dict | None = None
    is_default: bool | None = None


class OverridesIn(BaseModel):
    overrides: dict = {}


class TokenIn(BaseModel):
    label: str = "extension"
    feeder: bool = False


def event_visible(user: dict | None, ev: dict, can=None, access=None) -> bool:
    """Who may see an alert event. A private alert (trading guard, the learned style rule, a user's own strategy) goes to its owner only, whoever else is signed in.
    A shared strategy's alert goes to its audience (strategy_access.Access.event_ok), and only to users whose plan has "Receive the admin's signals"."""
    if ev.get("to") is not None:
        return user is not None and ev["to"] == user.get("id")
    if user is None:
        return True
    if user.get("role") != "admin" and can is not None and not can(user, "cap.signals.receive"):
        return False
    if access is not None:
        return access.event_ok(user, ev)
    return user.get("role") in ("admin", "editor")                  # without an audience rule nothing is shared with plain users


def bind(auth: Auth, features: Features, on_user_deleted=None) -> tuple[APIRouter, object]:
    router = APIRouter(prefix="/api")

    async def guard(request: Request) -> None:
        """Runs before every route: who is this, and may they use this route? Sets request.state.user."""
        path, method = request.url.path, request.method
        rule, feature = match(method, path)
        request.state.user = None
        if rule == "public":
            return
        if not auth.enabled:                                           # local development: the machine itself is the admin; nobody else gets in
            ip = request.client.host if request.client else ""
            if ip not in LOCAL_HOSTS or "x-forwarded-for" in request.headers:
                raise HTTPException(403, "sign-in is switched off, which is only allowed for the local machine (set QXS_ENV=production to host it)")
            request.state.user = auth.local_identity()
            return
        user = None
        header = request.headers.get("authorization", "")
        if header[:7].lower() == "bearer ":
            if path in BEARER_PATHS:
                user = auth.user_from_token(header[7:].strip())
        elif rule != "ingest" and (cookie := request.cookies.get(COOKIE)):
            user = auth.user_from_session(cookie)
            if user and method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
                raise HTTPException(403, "missing request header")
        if user is None:
            raise HTTPException(401, "sign in required")
        if rule == "ingest" and user["via"] != "token":
            raise HTTPException(401, "the extension must send its access token")
        if user.get("must_change_pw") and not path.startswith(("/api/auth/", "/api/me")):
            raise HTTPException(403, "change your password first")
        if not allowed(user["role"], rule):
            raise HTTPException(403, "your role cannot use this")
        if feature and not features.can(user, feature):
            raise HTTPException(403, f"“{BY_KEY[feature].label}” is not included in your plan")
        request.state.user = user

    def me_payload(user: dict) -> dict:
        plan = features._plan_for(user)
        return {"user": {k: user.get(k) for k in ("id", "username", "role", "must_change_pw", "plan_id", "plan_until")}, "auth_enabled": auth.enabled, "via": user.get("via"),
                "features": features.values(user), "plan": {"id": plan["id"], "name": plan["name"]}}

    # ---- sign in / out
    @router.post("/auth/login")
    async def login(body: LoginIn, request: Request, response: Response) -> dict:
        if not auth.enabled:
            raise HTTPException(400, "sign-in is switched off on this server")
        ip = request.client.host if request.client else ""                # behind the reverse proxy uvicorn (--proxy-headers) already puts the real client address here
        try:
            auth.login_precheck(ip, body.username)
            row = auth.login_row(body.username)
            ok = await run_in_threadpool(verify_password, body.password, row[1] if row else _DUMMY_HASH)          # the slow part runs off the event loop
            token = auth.login_finish(ok and row is not None, row, body.username, ip, request.headers.get("user-agent", ""))
        except AuthError as e:
            raise HTTPException(e.status, e.message)
        response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, secure=auth.production, samesite="lax", path="/")
        return me_payload({**auth.get_user(row[0]), "via": "session"})

    @router.post("/auth/logout")
    async def logout(request: Request, response: Response) -> dict:
        if (cookie := request.cookies.get(COOKIE)):
            auth.logout(cookie)
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @router.get("/me")
    async def me(request: Request) -> dict:
        return me_payload(request.state.user)

    @router.post("/auth/password")
    async def password(body: PasswordIn, request: Request) -> dict:
        user = request.state.user
        if not auth.enabled or user["via"] == "local":
            raise HTTPException(400, "sign-in is switched off on this server")
        try:
            auth.change_password(user, body.old, body.new, check_old=True)
        except AuthError as e:
            raise HTTPException(e.status, e.message)
        return {"ok": True}

    # ---- users (admin)
    def _call(fn, *a):
        try:
            return fn(*a)
        except AuthError as e:
            raise HTTPException(e.status, e.message)

    @router.get("/users")
    async def users() -> list[dict]:
        return auth.list_users()

    @router.post("/users")
    async def user_create(body: UserIn, request: Request) -> dict:
        u, generated = _call(auth.create_user, request.state.user["username"], body.username, body.password, body.role, body.must_change_pw)
        return {"user": u, "generated_password": generated}

    @router.patch("/users/{uid}")
    async def user_update(uid: int, body: UserPatch, request: Request) -> dict:
        patch = body.model_dump(exclude_unset=True)
        if patch.get("plan_id") is not None and features.plan(patch["plan_id"])["id"] != patch["plan_id"]:
            raise HTTPException(400, "no such plan")
        u, generated = _call(auth.update_user, request.state.user, uid, patch)
        return {"user": u, "generated_password": generated}

    @router.post("/users/{uid}/logout")
    async def user_logout(uid: int, request: Request) -> dict:
        _call(auth.force_logout, request.state.user, uid)
        return {"ok": True}

    @router.delete("/users/{uid}")
    async def user_delete(uid: int, request: Request) -> dict:
        _call(auth.delete_user, request.state.user, uid)
        if on_user_deleted:
            on_user_deleted(uid)                                       # their private database goes with them
        return {"ok": True}

    @router.get("/audit")
    async def audit(n: int = 100) -> list[dict]:
        return auth.audit_tail(max(1, min(n, 500)))

    # ---- features and plans (admin)
    def _fcall(fn, *a):
        try:
            return fn(*a)
        except FeatureError as e:
            raise HTTPException(e.status, e.message)

    @router.get("/features")
    async def features_list() -> dict:
        return {"features": [feature_json(f) for f in REGISTRY], "plans": features.plans(), "roles": ["trader", "editor", "admin"]}

    @router.post("/plans")
    async def plan_create(body: PlanIn, request: Request) -> dict:
        p = _fcall(features.create_plan, body.name, body.description, body.features, body.copy_of)
        auth.audit(request.state.user["username"], "plan-create", p["name"])
        return p

    @router.put("/plans/{pid}")
    async def plan_update(pid: int, body: PlanPatch, request: Request) -> dict:
        patch = body.model_dump(exclude_unset=True)
        p = _fcall(features.update_plan, pid, patch)
        auth.audit(request.state.user["username"], "plan-update", p["name"], {k: v for k, v in patch.items() if k != "description"})
        return p

    @router.delete("/plans/{pid}")
    async def plan_delete(pid: int, request: Request) -> dict:
        name = features.plan(pid)["name"]
        _fcall(features.delete_plan, pid)
        auth.audit(request.state.user["username"], "plan-delete", name)
        return {"ok": True}

    @router.get("/users/{uid}/features")
    async def user_features(uid: int) -> dict:
        u = auth.get_user(uid)
        if not u:
            raise HTTPException(404, "no such user")
        return {"overrides": features.overrides(uid), "effective": features.effective({**u, "via": "db"}), "plan": features._plan_for(u)["name"]}

    @router.put("/users/{uid}/features")
    async def user_features_set(uid: int, body: OverridesIn, request: Request) -> dict:
        u = auth.get_user(uid)
        if not u:
            raise HTTPException(404, "no such user")
        saved = _fcall(features.set_overrides, uid, body.overrides)
        auth.audit(request.state.user["username"], "user-features", u["username"], saved)
        return {"overrides": saved, "effective": features.effective({**u, "via": "db"})}

    # ---- extension tokens (any signed-in user, for their own extension)
    @router.get("/tokens")
    async def tokens(request: Request) -> list[dict]:
        return auth.list_tokens(request.state.user["id"])

    @router.post("/tokens")
    async def token_create(body: TokenIn, request: Request) -> dict:
        user = request.state.user
        if user["via"] == "local":
            raise HTTPException(400, "sign-in is switched off on this server")
        t, secret = _call(auth.create_token, user, body.label, body.feeder)
        return {"token": t, "secret": secret, "note": "shown once: copy it into the extension now"}

    @router.delete("/tokens/{tid}")
    async def token_delete(tid: int, request: Request) -> dict:
        _call(auth.revoke_token, request.state.user, tid)
        return {"ok": True}

    return router, Depends(guard)
