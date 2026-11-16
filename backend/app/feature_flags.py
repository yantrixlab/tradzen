"""The Features section: every menu and capability that can be switched on or off, plans (named sets of switches and limits) and per-user overrides.

The list of features is code (REGISTRY below), so a switch can never refer to something that does not exist. What is stored: plans (features as JSON) and user overrides.

What a user gets for a feature, first match wins:
  1. admin role                 -> everything, no limits
  2. role below the feature's   -> off (a Trader can never be given a staff feature by a plan; only the admin can promote the role)
     minimum role
  3. the user's override        (set by the admin on the Users page)
  4. the user's plan            (a plan past its end date falls back to the default plan)
  5. the feature's own default

`enforced` says whether the server already applies the feature to traders. The pages that show personal trading data (My trades, Guard, My style, ...) are
admin-only until the data is per-user (a later phase); their switches are stored now and start to work when that lands (`trader_soon`).
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

RANK = {"trader": 0, "editor": 1, "admin": 2}
MAX_LIMIT = 100000


@dataclass(frozen=True)
class F:
    key: str
    label: str
    group: str                    # Menus | Signals & alerts | Strategies | Limits
    desc: str
    kind: str = "toggle"          # toggle | limit
    min_role: str = "trader"      # the lowest role that can ever have it (enforced today)
    default: object = True        # toggle: bool; limit: int, None = unlimited
    enforced: bool = True         # applied by the server today
    trader_soon: bool = False     # meant for traders once the data is per-user / strategies can be published
    locked: bool = False          # always on (cannot be switched off)
    unit: str = ""


REGISTRY: list[F] = [
    # ---- menus (the sidebar entry AND its API)
    F("menu.account", "My account", "Menus", "Password and extension access tokens. Everybody has it.", locked=True),
    F("menu.overview", "Overview", "Menus", "The dashboard of the shared feed and strategies.", min_role="editor"),
    F("menu.signals", "Signal history", "Menus", "The signals of the strategies the user owns or receives, with their results."),
    F("menu.library", "Strategy library", "Menus", "The strategies the team published: their record, and adding them to the account."),
    F("menu.strategies", "Strategies", "Menus", "Build and run own strategies (staff: the shared strategies and who they go to)."),
    F("menu.backtests", "Backtest lab", "Menus", "Test own strategies on the stored candles."),
    F("menu.sweeps", "Sweeps", "Menus", "Try many parameter sets of a strategy.", min_role="editor"),
    F("menu.clinic", "Clinic", "Menus", "Find why a strategy loses and try fixes.", min_role="editor"),
    F("menu.data", "Data", "Menus", "Candle coverage and market data.", min_role="editor"),
    F("menu.live", "Quotex Signal", "Menus", "The live signal page and the master alert switch.", min_role="admin"),
    F("menu.generate", "Trade generator", "Menus", "One-off trade ideas from the stored market.", min_role="admin", enforced=False, trader_soon=True),
    F("menu.trades", "My trades", "Menus", "The user's own real Quotex trades, profit and balance (private to them).", min_role="trader"),
    F("menu.guard", "Trading guard", "Menus", "Daily target, loss limit, streak and overtrading alerts, on the user's own trades.", min_role="trader"),
    F("menu.style", "My style", "Menus", "Learns the user's habits from their own trades and runs a paper rule.", min_role="trader"),
    F("menu.alerts", "Alerts", "Menus", "Which strategies alert, and the alert log.", min_role="editor"),
    F("menu.research", "Research", "Menus", "The nightly edge check.", min_role="admin"),
    F("menu.edgelab", "Edge Lab", "Menus", "Weekly edge search over stored candles.", min_role="admin"),
    F("menu.ai", "AI advisor", "Menus", "Model training and its reading of the market.", min_role="admin"),
    F("menu.system", "System", "Menus", "Server health, database, logs.", min_role="admin"),
    F("menu.users", "Users", "Menus", "Create and manage users.", min_role="admin"),
    F("menu.features", "Features", "Menus", "This section.", min_role="admin"),
    # ---- signals and alerts
    F("cap.signals.receive", "Receive the admin's signals", "Signals & alerts", "Signal and result cards and sounds in the Chrome extension.", trader_soon=False),
    F("cap.trades.track", "Record own trades", "Signals & alerts", "Store the user's orders and balance sent by their extension (needed for My trades, Guard and My style).", min_role="trader"),
    F("cap.extension.connect", "Connect the Chrome extension", "Signals & alerts", "Create access tokens to link a browser to the account."),
    F("cap.candles.contribute", "Share candles", "Signals & alerts", "The extension adds the chart history its Quotex page loads to the shared candle store (a candle the server already has is never changed)."),
    F("cap.ticks.feed", "Share live prices", "Signals & alerts", "The extension may stream live prices of the pairs open in its Quotex tab when nobody else does (one account per pair at a time; the admin's price feed always has priority)."),
    # ---- strategies
    F("cap.strategies.use_published", "Use published strategies", "Strategies", "Add the strategies the team published to the account and receive their signals."),
    F("cap.strategies.publish", "Publish strategies", "Strategies", "Decide who gets a shared strategy's signals (testing, library, everybody).", min_role="editor"),
    F("cap.strategies.own", "Own strategies", "Limits", "How many strategies of their own a user may keep (0 = none).", kind="limit", default=3, unit="strategies"),
    F("cap.backtest.daily", "Backtests per day", "Limits", "Backtest runs a user may start per day (0 = none).", kind="limit", default=10, unit="runs / day"),
    F("cap.backtest.jobs", "Running backtests at once", "Limits", "Jobs of one user running at the same time.", kind="limit", default=1, unit="jobs"),
]
BY_KEY = {f.key: f for f in REGISTRY}
DEFAULT_PLAN = "Standard"


def feature_json(f: F) -> dict:
    return {"key": f.key, "label": f.label, "group": f.group, "desc": f.desc, "kind": f.kind, "min_role": f.min_role, "default": f.default, "enforced": f.enforced,
            "trader_soon": f.trader_soon, "locked": f.locked, "unit": f.unit}


class FeatureError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def clean_value(f: F, v):
    """Validate one value for a feature. Toggle: bool. Limit: a whole number 0..MAX_LIMIT, or None for unlimited."""
    if f.locked:
        raise FeatureError(400, f"“{f.label}” is always on")
    if f.kind == "toggle":
        if not isinstance(v, bool):
            raise FeatureError(400, f"“{f.label}” is on or off")
        return v
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or not (0 <= v <= MAX_LIMIT):
        raise FeatureError(400, f"“{f.label}” needs a whole number from 0 to {MAX_LIMIT}, or no limit")
    return v


def clean_map(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise FeatureError(400, "features must be an object")
    out = {}
    for k, v in raw.items():
        if k not in BY_KEY:
            raise FeatureError(400, f"unknown feature “{k}”")
        out[k] = clean_value(BY_KEY[k], v)
    return out


def resolve(f: F, role: str, plan: dict, overrides: dict) -> tuple[object, str]:
    """(value, where it came from). Toggle -> bool; limit -> int, or None for unlimited."""
    if role == "admin":
        return (True if f.kind == "toggle" else None), "admin"
    if f.locked:
        return True, "always on"
    if RANK.get(role, 0) < RANK[f.min_role]:
        return (False if f.kind == "toggle" else 0), "role"
    if f.key in overrides:
        return overrides[f.key], "override"
    if f.key in plan:
        return plan[f.key], "plan"
    return f.default, "default"


def allows(f: F, value) -> bool:
    return bool(value) if f.kind == "toggle" else (value is None or value > 0)


class Features:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        self.ensure_default_plan()

    # ---- plans
    def ensure_default_plan(self) -> None:
        if not self.db.execute("SELECT 1 FROM plans WHERE is_default=1").fetchone():
            self.db.execute("INSERT OR IGNORE INTO plans (name, description, features, is_default, created) VALUES (?,?,?,?,?)",
                            (DEFAULT_PLAN, "What every new user gets", "{}", 1, time.time()))
            if not self.db.execute("SELECT 1 FROM plans WHERE is_default=1").fetchone():              # the name existed but was not the default
                self.db.execute("UPDATE plans SET is_default=1 WHERE id=(SELECT MIN(id) FROM plans)")
            self.db.commit()

    @staticmethod
    def _plan(row) -> dict:
        i, name, desc, feats, default, created = row
        return {"id": i, "name": name, "description": desc, "features": json.loads(feats or "{}"), "is_default": bool(default), "created": created}

    def plans(self) -> list[dict]:
        out = []
        for r in self.db.execute("SELECT id, name, description, features, is_default, created FROM plans ORDER BY is_default DESC, id").fetchall():
            p = self._plan(r)
            p["users"] = self.db.execute("SELECT COUNT(*) FROM users WHERE plan_id=?", (p["id"],)).fetchone()[0]
            out.append(p)
        default_users = self.db.execute("SELECT COUNT(*) FROM users WHERE plan_id IS NULL OR plan_id NOT IN (SELECT id FROM plans)").fetchone()[0]
        for p in out:
            if p["is_default"]:
                p["users"] += default_users
        return out

    def plan(self, pid: int | None) -> dict:
        r = self.db.execute("SELECT id, name, description, features, is_default, created FROM plans WHERE id=?", (pid,)).fetchone() if pid is not None else None
        if r is None:
            r = self.db.execute("SELECT id, name, description, features, is_default, created FROM plans WHERE is_default=1").fetchone()
        return self._plan(r)

    def create_plan(self, name: str, description: str = "", features: dict | None = None, copy_of: int | None = None) -> dict:
        name = (name or "").strip()
        if not (2 <= len(name) <= 40):
            raise FeatureError(400, "a plan name has 2-40 characters")
        if self.db.execute("SELECT 1 FROM plans WHERE name=?", (name,)).fetchone():
            raise FeatureError(409, "a plan with that name exists")
        feats = clean_map(features or {})
        if copy_of is not None and not features:
            feats = dict(self.plan(copy_of)["features"])
        cur = self.db.execute("INSERT INTO plans (name, description, features, is_default, created) VALUES (?,?,?,?,?)", (name, description.strip()[:200], json.dumps(feats), 0, time.time()))
        self.db.commit()
        return self.plan(cur.lastrowid)

    def update_plan(self, pid: int, patch: dict) -> dict:
        p = self.plan(pid)
        if p["id"] != pid:
            raise FeatureError(404, "no such plan")
        sets, args = [], []
        if "name" in patch:
            name = (patch["name"] or "").strip()
            if not (2 <= len(name) <= 40):
                raise FeatureError(400, "a plan name has 2-40 characters")
            if self.db.execute("SELECT 1 FROM plans WHERE name=? AND id<>?", (name, pid)).fetchone():
                raise FeatureError(409, "a plan with that name exists")
            sets.append("name=?"), args.append(name)
        if "description" in patch:
            sets.append("description=?"), args.append((patch["description"] or "").strip()[:200])
        if "features" in patch:
            feats = dict(p["features"])
            for k, v in (patch["features"] or {}).items():                  # a value of "unset" (None for toggles is not valid) removes the plan's own value
                if k not in BY_KEY:
                    raise FeatureError(400, f"unknown feature “{k}”")
                if v == "default":
                    feats.pop(k, None)
                else:
                    feats[k] = clean_value(BY_KEY[k], v)
            sets.append("features=?"), args.append(json.dumps(feats))
        if patch.get("is_default"):
            self.db.execute("UPDATE plans SET is_default=0")
            sets.append("is_default=1")
        if sets:
            self.db.execute(f"UPDATE plans SET {', '.join(sets)} WHERE id=?", (*args, pid))
            self.db.commit()
        return self.plan(pid)

    def delete_plan(self, pid: int) -> None:
        p = self.plan(pid)
        if p["id"] != pid:
            raise FeatureError(404, "no such plan")
        if p["is_default"]:
            raise FeatureError(400, "the default plan cannot be deleted; make another plan the default first")
        self.db.execute("UPDATE users SET plan_id=NULL WHERE plan_id=?", (pid,))          # its users fall back to the default plan
        self.db.execute("DELETE FROM plans WHERE id=?", (pid,))
        self.db.commit()

    # ---- per-user overrides
    def overrides(self, uid: int) -> dict:
        return {k: json.loads(v) for k, v in self.db.execute("SELECT key, value FROM user_features WHERE user_id=?", (uid,))}

    def set_overrides(self, uid: int, raw: dict) -> dict:
        """Replace the user's overrides. A key with the value "default" (or missing) is not overridden."""
        clean = {}
        for k, v in (raw or {}).items():
            if k not in BY_KEY:
                raise FeatureError(400, f"unknown feature “{k}”")
            if v != "default":
                clean[k] = clean_value(BY_KEY[k], v)
        self.db.execute("DELETE FROM user_features WHERE user_id=?", (uid,))
        self.db.executemany("INSERT INTO user_features (user_id, key, value) VALUES (?,?,?)", [(uid, k, json.dumps(v)) for k, v in clean.items()])
        self.db.commit()
        return clean

    # ---- what a user gets
    def _plan_for(self, user: dict) -> dict:
        pid = user.get("plan_id")
        until = user.get("plan_until")
        if pid is not None and until and until < time.time():            # the paid period ended: back to the default plan
            pid = None
        return self.plan(pid)

    def effective(self, user: dict) -> dict[str, dict]:
        plan = self._plan_for(user)
        ov = self.overrides(user["id"]) if user.get("via") != "local" else {}
        out = {}
        for f in REGISTRY:
            v, src = resolve(f, user["role"], plan["features"], ov)
            out[f.key] = {"value": v, "source": src}
        return out

    def values(self, user: dict) -> dict:
        return {k: e["value"] for k, e in self.effective(user).items()}

    def can(self, user: dict | None, key: str) -> bool:
        if user is None:
            return True
        f = BY_KEY[key]
        if user["role"] == "admin":
            return True
        v, _ = resolve(f, user["role"], self._plan_for(user)["features"], self.overrides(user["id"]))
        return allows(f, v)

    def limit(self, user: dict, key: str) -> int | None:
        """A numeric limit for this user (None = unlimited)."""
        f = BY_KEY[key]
        v, _ = resolve(f, user["role"], self._plan_for(user)["features"], self.overrides(user["id"]) if user.get("via") != "local" else {})
        return v
