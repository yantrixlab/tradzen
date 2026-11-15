"""Accounts for the hosted (multi-user) version: users with a role, login sessions, extension tokens, an audit log, and the access rules of every route.

Roles (fixed, the admin changes a user's role):
  admin  - everything (users, features, system, ...). The env admin (QXS_ADMIN_USER / QXS_ADMIN_PASSWORD) is always an admin and cannot be removed in the UI.
  editor - staff: builds, tests and publishes strategies; no user / system management.
  trader - a general user.

Authentication is ON when QXS_ENV=production or QXS_AUTH=on. Otherwise (local development, the default) it is OFF: every request is the local admin, but only
requests from the machine itself are accepted - so a development server can never be exposed by accident.

Passwords are hashed with scrypt (standard library). Session and API tokens are random, only a keyed hash is stored. Nothing here needs a third-party package.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time

ROLES = ("admin", "editor", "trader")
SESSION_DAYS = 14
COOKIE = "qxs_session"
CSRF_HEADER = "x-requested-with"           # the SPA sends "qxs"; cookie-authenticated changes without it are refused
CSRF_VALUE = "qxs"
MIN_PASSWORD = 8
LOCK_AFTER, LOCK_SECONDS = 5, 300           # wrong passwords per (ip, username) before a pause
WEAK_ADMIN_PASSWORDS = {"admin", "password", "changeme", "change-me", "12345678", "admin123", "password123"}
BEARER_PATHS = ("/ingest", "/api/events", "/api/me")          # an extension token works here only; everything else needs a browser session
_SCRYPT = (2 ** 14, 8, 1)


class AuthError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


# ---------------------------------------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    n, r, p = _SCRYPT
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${base64.b64encode(salt).decode()}${base64.b64encode(h).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, h = stored.split("$")
        if kind != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(h))
    except Exception:                                           # noqa: BLE001 - a malformed hash is simply "wrong"
        return False


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"the password needs at least {MIN_PASSWORD} characters"
    if len(password) > 200:
        return "the password is too long"
    if password.lower() in WEAK_ADMIN_PASSWORDS or len(set(password)) < 4:
        return "that password is too easy to guess"
    return None


def generate_password(n: int = 14) -> str:
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # no look-alike characters
    return "".join(secrets.choice(alphabet) for _ in range(n))


# ---------------------------------------------------------------------------------------------- access rules
# First match wins. rule: public | any (any signed-in user) | staff (admin, editor) | admin | ingest (an extension token). Anything not listed is admin-only,
# so a route added later is closed until someone decides who may use it.
# (path, methods, rule, feature). `feature` (see feature_flags.py) is checked in addition to the role, so the admin can switch a menu's API off for a plan or a user.
_RULES: list[tuple[re.Pattern, set[str] | None, str, str | None]] = [(re.compile(p), m, r, f) for p, m, r, f in [
    (r"^/healthz$", None, "public", None),
    (r"^/api/auth/login$", {"POST"}, "public", None),
    (r"^/ingest$", {"POST"}, "ingest", None),
    (r"^/api/auth/", None, "any", None),
    (r"^/api/me$", None, "any", None),
    (r"^/api/tokens$", {"POST"}, "any", "cap.extension.connect"),
    (r"^/api/tokens(/\d+)?$", None, "any", None),
    (r"^/api/events$", {"GET"}, "any", None),
    # Private data of the signed-in user (their own database): open to every role, each behind its own switch.
    (r"^/api/(trades|account)(/|$)", None, "any", "menu.trades"),
    (r"^/api/guard(/|$)", None, "any", "menu.guard"),
    (r"^/api/style(/|$)", None, "any", "menu.style"),
    # Strategies, signals and backtests are open to every role: each handler only shows and changes what the caller owns or receives (strategy_access.py).
    (r"^/api/(signal-pairs|signals(/\d+)?|stats|equity)$", {"GET"}, "any", "menu.signals"),
    (r"^/api/state$", {"GET"}, "staff", None),
    (r"^/api/strategies/(bulk|pause-all|resume-all)$", None, "staff", "menu.strategies"),            # these act on the shared strategies
    (r"^/api/strategies/\d+/audience$", None, "staff", "cap.strategies.publish"),
    (r"^/api/(strategies|spec)(/|$)", None, "any", "menu.strategies"),
    (r"^/api/library(/|$)", None, "any", "menu.library"),
    (r"^/api/backtests(/|$)", None, "any", "menu.backtests"),
    (r"^/api/data/coverage$", {"GET"}, "any", None),
    (r"^/api/sweeps(/|$)", None, "staff", "menu.sweeps"),
    (r"^/api/(clinic|insights)(/|$)", None, "staff", "menu.clinic"),
    (r"^/api/data/(coverage|plan|sentiment|market|gaps)(/|$)", {"GET"}, "staff", "menu.data"),
    (r"^/api/(presets|analyze|risk|strategy-health|upgrades|readiness)(/|$)", None, "staff", None),
    (r"^/api/signals/\d+/(similar|notes)$", None, "staff", "menu.signals"),
    (r"^/api/notes/\d+$", None, "staff", "menu.signals"),
]]
OPEN_PREFIXES = ("/admin", "/docs", "/openapi.json", "/redoc")      # the SPA shell and its files carry no data; docs are switched off separately in production


def match(method: str, path: str) -> tuple[str, str | None]:
    """(rule, feature) for a request. Anything not listed is admin-only, so a route added later stays closed until someone decides who may use it."""
    if not path.startswith(("/api/", "/ingest", "/healthz")):
        return "public", None                                   # the SPA shell, the classic page redirect and static files: no data in them
    for rx, methods, rule, feature in _RULES:
        if rx.match(path) and (methods is None or method.upper() in methods):
            return rule, feature
    return "admin", None


def rule_for(method: str, path: str) -> str:
    return match(method, path)[0]


def allowed(role: str, rule: str) -> bool:
    return rule in ("public", "any", "ingest") or (rule == "staff" and role in ("admin", "editor")) or (rule == "admin" and role == "admin")


# ---------------------------------------------------------------------------------------------- the accounts service
class Auth:
    def __init__(self, db: sqlite3.Connection, *, enabled: bool, production: bool = False, secret: str = "") -> None:
        self.db = db
        self.enabled = enabled
        self.production = production
        if enabled and len(secret) < 16:
            raise RuntimeError("QXS_SECRET_KEY must be set (at least 16 random characters) when sign-in is enabled")
        self._key = (secret or "dev-not-used").encode()
        self._fails: dict[tuple[str, str], list[float]] = {}

    # ---- helpers
    def _h(self, token: str) -> str:
        return hmac.new(self._key, token.encode(), hashlib.sha256).hexdigest()

    def audit(self, actor: str, action: str, target: str = "", detail: dict | None = None) -> None:
        self.db.execute("INSERT INTO audit_log (ts, actor, action, target, detail) VALUES (?,?,?,?,?)", (time.time(), actor, action, target, json.dumps(detail or {})))
        self.db.commit()

    @staticmethod
    def _public(row) -> dict:
        i, name, role, plan_id, plan_until, active, must, env, created, last = row
        return {"id": i, "username": name, "role": role, "plan_id": plan_id, "plan_until": plan_until, "active": bool(active), "must_change_pw": bool(must),
                "env_admin": bool(env), "created": created, "last_login": last}

    _COLS = "id, username, role, plan_id, plan_until, active, must_change_pw, env_admin, created, last_login"

    def get_user(self, uid: int) -> dict | None:
        r = self.db.execute(f"SELECT {self._COLS} FROM users WHERE id=?", (uid,)).fetchone()
        return self._public(r) if r else None

    def list_users(self) -> list[dict]:
        out = []
        for r in self.db.execute(f"SELECT {self._COLS} FROM users ORDER BY id").fetchall():
            u = self._public(r)
            u["sessions"] = self.db.execute("SELECT COUNT(*) FROM sessions WHERE user_id=? AND expires>?", (u["id"], time.time())).fetchone()[0]
            u["tokens"] = self.db.execute("SELECT COUNT(*) FROM api_tokens WHERE user_id=? AND revoked=0", (u["id"],)).fetchone()[0]
            out.append(u)
        return out

    def local_identity(self) -> dict:
        return {"id": 1, "username": "local", "role": "admin", "via": "local", "feeder": True, "must_change_pw": False, "active": True}

    # ---- the admin from the environment
    def ensure_env_admin(self, username: str | None, password: str | None) -> None:
        if not username or not password:
            if self.enabled and not self.db.execute("SELECT 1 FROM users WHERE role='admin' AND active=1").fetchone():
                raise RuntimeError("no admin account: set QXS_ADMIN_USER and QXS_ADMIN_PASSWORD")
            return
        if self.production and (password.lower() in WEAK_ADMIN_PASSWORDS or len(password) < 10 or password.lower().startswith("change-this")):
            raise RuntimeError("QXS_ADMIN_PASSWORD is too weak for production (at least 10 characters, nothing common)")
        row = self.db.execute("SELECT id, pw_hash, env_admin FROM users WHERE username=?", (username,)).fetchone()
        self.db.execute("UPDATE users SET env_admin=0 WHERE env_admin=1 AND username<>?", (username,))     # renamed in .env: the old one becomes an ordinary admin
        if row is None:
            self.db.execute("INSERT INTO users (username, pw_hash, role, active, env_admin, created) VALUES (?,?,?,?,?,?)", (username, hash_password(password), "admin", 1, 1, time.time()))
        else:
            if not verify_password(password, row[1]):
                self.db.execute("UPDATE users SET pw_hash=?, must_change_pw=0 WHERE id=?", (hash_password(password), row[0]))
                self.db.execute("DELETE FROM sessions WHERE user_id=?", (row[0],))
            self.db.execute("UPDATE users SET role='admin', active=1, env_admin=1 WHERE id=?", (row[0],))
        self.db.commit()

    # ---- login / sessions
    def _client_key(self, ip: str, username: str) -> tuple[str, str]:
        return ip or "?", username.lower()

    def _locked(self, key: tuple[str, str]) -> float:
        now = time.time()
        fails = [t for t in self._fails.get(key, []) if now - t < LOCK_SECONDS]
        self._fails[key] = fails
        if len(self._fails) > 5000:
            self._fails = {k: v for k, v in self._fails.items() if v and now - v[-1] < LOCK_SECONDS}
        return (fails[0] + LOCK_SECONDS - now) if len(fails) >= LOCK_AFTER else 0.0

    def login_precheck(self, ip: str, username: str) -> None:
        wait = self._locked(self._client_key(ip, username))
        if wait > 0:
            raise AuthError(429, f"too many wrong passwords: try again in {int(wait // 60) + 1} min")

    def login_row(self, username: str) -> tuple | None:
        return self.db.execute("SELECT id, pw_hash, active FROM users WHERE username=?", (username,)).fetchone()

    def login_finish(self, ok: bool, row: tuple | None, username: str, ip: str, ua: str) -> str:
        """Called after the (slow) password check; returns the new session token or raises."""
        key = self._client_key(ip, username)
        if not ok or row is None or not row[2]:
            self._fails.setdefault(key, []).append(time.time())
            self.audit(username[:60], "login-failed", "", {"ip": ip})
            raise AuthError(401, "wrong username or password")
        self._fails.pop(key, None)
        token = secrets.token_urlsafe(32)
        now = time.time()
        self.db.execute("INSERT INTO sessions (token_hash, user_id, created, expires, last_seen, ip, ua) VALUES (?,?,?,?,?,?,?)", (self._h(token), row[0], now, now + SESSION_DAYS * 86400, now, ip, ua[:200]))
        self.db.execute("UPDATE users SET last_login=? WHERE id=?", (now, row[0]))
        self.db.execute("DELETE FROM sessions WHERE expires<?", (now,))
        self.db.commit()
        return token

    def user_from_session(self, token: str) -> dict | None:
        r = self.db.execute("SELECT user_id, expires, last_seen FROM sessions WHERE token_hash=?", (self._h(token),)).fetchone()
        if not r or r[1] < time.time():
            return None
        u = self.get_user(r[0])
        if not u or not u["active"]:
            return None
        if time.time() - (r[2] or 0) > 300:
            self.db.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (time.time(), self._h(token)))
            self.db.commit()
        return {**u, "via": "session", "feeder": False}

    def logout(self, token: str) -> None:
        self.db.execute("DELETE FROM sessions WHERE token_hash=?", (self._h(token),))
        self.db.commit()

    def user_from_token(self, token: str) -> dict | None:
        r = self.db.execute("SELECT id, user_id, feeder, revoked, last_used FROM api_tokens WHERE token_hash=?", (self._h(token),)).fetchone()
        if not r or r[3]:
            return None
        u = self.get_user(r[1])
        if not u or not u["active"]:
            return None
        if time.time() - (r[4] or 0) > 60:
            self.db.execute("UPDATE api_tokens SET last_used=? WHERE id=?", (time.time(), r[0]))
            self.db.commit()
        return {**u, "via": "token", "feeder": bool(r[2]) and u["role"] == "admin"}

    # ---- users (admin only; the routes check the role)
    def create_user(self, actor: str, username: str, password: str | None, role: str = "trader", must_change_pw: bool = True) -> tuple[dict, str | None]:
        username = username.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{3,40}", username):
            raise AuthError(400, "username: 3-40 letters, digits and _ . @ -")
        if role not in ROLES:
            raise AuthError(400, f"role must be one of {', '.join(ROLES)}")
        generated = None
        if not password:
            password = generated = generate_password()
        elif (p := password_problem(password)):
            raise AuthError(400, p)
        if self.db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise AuthError(409, "that username is taken")
        cur = self.db.execute("INSERT INTO users (username, pw_hash, role, active, must_change_pw, created) VALUES (?,?,?,?,?,?)", (username, hash_password(password), role, 1, int(must_change_pw), time.time()))
        self.db.commit()
        self.audit(actor, "user-create", username, {"role": role})
        return self.get_user(cur.lastrowid), generated

    def update_user(self, actor: dict, uid: int, patch: dict) -> tuple[dict, str | None]:
        u = self.get_user(uid)
        if not u:
            raise AuthError(404, "no such user")
        sets, args, note, generated = [], [], {}, None

        def put(col: str, val, key: str | None = None, shown=None) -> None:
            sets.append(f"{col}=?")
            args.append(val)
            if key:
                note[key] = val if shown is None else shown

        if "role" in patch and patch["role"] != u["role"]:
            if patch["role"] not in ROLES:
                raise AuthError(400, f"role must be one of {', '.join(ROLES)}")
            if u["env_admin"]:
                raise AuthError(400, "the admin from the .env file is always an admin")
            if uid == actor["id"]:
                raise AuthError(400, "you cannot change your own role")
            if u["role"] == "admin" and self._admins() <= 1:
                raise AuthError(400, "there must be at least one admin")
            put("role", patch["role"], "role")
        if "active" in patch and bool(patch["active"]) != u["active"]:
            if not patch["active"]:
                if u["env_admin"] or uid == actor["id"]:
                    raise AuthError(400, "you cannot deactivate this account")
                if u["role"] == "admin" and self._admins() <= 1:
                    raise AuthError(400, "there must be at least one active admin")
            put("active", int(bool(patch["active"])), "active", bool(patch["active"]))
        for k in ("plan_id", "plan_until"):
            if k in patch:
                put(k, patch[k], k)
        if "must_change_pw" in patch:
            put("must_change_pw", int(bool(patch["must_change_pw"])))
        if patch.get("reset_password"):
            generated = generate_password()
            put("pw_hash", hash_password(generated), "password", "reset")
            put("must_change_pw", 1)
        elif patch.get("password"):
            if (p := password_problem(patch["password"])):
                raise AuthError(400, p)
            put("pw_hash", hash_password(patch["password"]), "password", "set")
        if sets:
            self.db.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", (*args, uid))
            if "password" in note or note.get("active") is False:                      # a role change needs no new sign-in: the role is read on every request
                self.revoke_all(uid)
            self.db.commit()
            self.audit(actor["username"], "user-update", u["username"], note)
        return self.get_user(uid), generated

    def _admins(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]

    def revoke_all(self, uid: int) -> None:
        self.db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        self.db.execute("UPDATE api_tokens SET revoked=1 WHERE user_id=?", (uid,))
        self.db.commit()

    def force_logout(self, actor: dict, uid: int) -> None:
        u = self.get_user(uid)
        if not u:
            raise AuthError(404, "no such user")
        self.revoke_all(uid)
        self.audit(actor["username"], "user-logout", u["username"])

    def delete_user(self, actor: dict, uid: int) -> None:
        u = self.get_user(uid)
        if not u:
            raise AuthError(404, "no such user")
        if u["env_admin"] or uid == actor["id"]:
            raise AuthError(400, "you cannot delete this account")
        if u["role"] == "admin" and self._admins() <= 1:
            raise AuthError(400, "there must be at least one admin")
        self.revoke_all(uid)
        self.db.execute("DELETE FROM api_tokens WHERE user_id=?", (uid,))
        self.db.execute("DELETE FROM user_features WHERE user_id=?", (uid,))
        self.db.execute("DELETE FROM users WHERE id=?", (uid,))
        self.db.commit()
        self.audit(actor["username"], "user-delete", u["username"])

    def change_password(self, user: dict, old: str | None, new: str, check_old: bool) -> None:
        if (p := password_problem(new)):
            raise AuthError(400, p)
        if check_old:
            row = self.db.execute("SELECT pw_hash FROM users WHERE id=?", (user["id"],)).fetchone()
            if not row or not old or not verify_password(old, row[0]):
                raise AuthError(400, "the current password is wrong")
        self.db.execute("UPDATE users SET pw_hash=?, must_change_pw=0 WHERE id=?", (hash_password(new), user["id"]))
        self.db.commit()
        self.audit(user["username"], "password-change", user["username"])

    # ---- extension tokens
    def create_token(self, user: dict, label: str, feeder: bool) -> tuple[dict, str]:
        if feeder and user["role"] != "admin":
            raise AuthError(403, "only an admin can create a price-feed token")
        token = "qxs_" + secrets.token_urlsafe(32)
        cur = self.db.execute("INSERT INTO api_tokens (token_hash, user_id, label, feeder, created) VALUES (?,?,?,?,?)", (self._h(token), user["id"], label.strip()[:60] or "extension", int(feeder), time.time()))
        self.db.commit()
        self.audit(user["username"], "token-create", label, {"feeder": feeder})
        return {"id": cur.lastrowid, "label": label.strip()[:60] or "extension", "feeder": feeder}, token

    def list_tokens(self, user_id: int) -> list[dict]:
        return [{"id": i, "label": l, "feeder": bool(f), "created": c, "last_used": lu} for (i, l, f, c, lu) in
                self.db.execute("SELECT id, label, feeder, created, last_used FROM api_tokens WHERE user_id=? AND revoked=0 ORDER BY id DESC", (user_id,))]

    def revoke_token(self, user: dict, tid: int) -> None:
        r = self.db.execute("SELECT user_id FROM api_tokens WHERE id=?", (tid,)).fetchone()
        if not r or (r[0] != user["id"] and user["role"] != "admin"):
            raise AuthError(404, "no such token")
        self.db.execute("UPDATE api_tokens SET revoked=1 WHERE id=?", (tid,))
        self.db.commit()
        self.audit(user["username"], "token-revoke", str(tid))

    def audit_tail(self, n: int = 100) -> list[dict]:
        return [{"id": i, "ts": t, "actor": a, "action": ac, "target": tg, "detail": json.loads(d or "{}")} for (i, t, a, ac, tg, d) in
                self.db.execute("SELECT id, ts, actor, action, target, detail FROM audit_log ORDER BY id DESC LIMIT ?", (n,))]


def from_env(db: sqlite3.Connection, env: dict | None = None) -> Auth:
    """Build the accounts service from the environment (QXS_ENV, QXS_AUTH, QXS_SECRET_KEY, QXS_ADMIN_USER, QXS_ADMIN_PASSWORD) and make sure the env admin exists."""
    env = os.environ if env is None else env
    production = env.get("QXS_ENV", "").lower() == "production"
    flag = env.get("QXS_AUTH", "").lower()
    enabled = production or flag in ("on", "1", "true", "yes")
    if flag in ("off", "0", "false", "no") and production:
        raise RuntimeError("QXS_AUTH=off is not allowed with QXS_ENV=production")
    a = Auth(db, enabled=enabled, production=production, secret=env.get("QXS_SECRET_KEY", ""))
    a.ensure_env_admin(env.get("QXS_ADMIN_USER"), env.get("QXS_ADMIN_PASSWORD"))
    return a
