"""Strategy storage: lifecycle (draft/paper/live/paused/archived), immutable versions, built-in seeding, cloning."""
from __future__ import annotations

import json
import re
import sqlite3
import time

from .builtins import BUILTINS, DEFAULT_KEY
from .spec import validate_spec

STATUSES = ("draft", "paper", "live", "paused", "archived")
AUDIENCES = ("admin", "optin", "everyone")       # who gets a shared strategy's signals: staff only (testing) | users who add it | every user
KEY_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


class RepoError(ValueError):
    pass


def _dump(spec: dict) -> str:
    return json.dumps(spec, sort_keys=True)


class StrategyRepo:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        self.last_errors: dict[str, list[str]] = {}

    # ------------------------------------------------------------------ seeding
    def ensure_builtins(self, include_extra: bool = True) -> None:
        now = time.time()
        for b in BUILTINS:
            if not include_extra and b["key"] != DEFAULT_KEY:
                continue
            row = self.db.execute("SELECT id FROM strategies WHERE key=?", (b["key"],)).fetchone()
            if row is None:
                cur = self.db.execute(
                    "INSERT INTO strategies (key, name, kind, description, status, created, updated) VALUES (?,?,?,?,?,?,?)",
                    (b["key"], b["name"], "builtin", b["description"], b["status"], now, now))
                sid = cur.lastrowid
            else:
                sid = row[0]
                self.db.execute("UPDATE strategies SET name=?, description=?, kind='builtin' WHERE id=?", (b["name"], b["description"], sid))
            latest = self.db.execute("SELECT version, spec FROM strategy_versions WHERE strategy_id=? ORDER BY version DESC LIMIT 1", (sid,)).fetchone()
            if latest is None or _dump(json.loads(latest[1])) != _dump(b["spec"]):
                version = (latest[0] + 1) if latest else 1
                note = "Built-in definition" if latest is None else "Built-in definition updated (executable spec)"
                self.db.execute("INSERT INTO strategy_versions (strategy_id, version, spec, notes, created) VALUES (?,?,?,?,?)",
                                (sid, version, _dump(b["spec"]), note, now))
        self.db.commit()

    # ------------------------------------------------------------------ reads
    def _version_row(self, sid: int, version: int | None = None):
        q = "SELECT version, spec, notes, created FROM strategy_versions WHERE strategy_id=? "
        if version is None:
            return self.db.execute(q + "ORDER BY version DESC LIMIT 1", (sid,)).fetchone()
        return self.db.execute(q + "AND version=?", (sid, version)).fetchone()

    def list(self, include_archived: bool = False, system_only: bool = False) -> list[dict]:
        """system_only: leave out the users' own strategies (the admin's tools - bulk control, the risk advisor, sweeps - only ever work on the shared set)."""
        out = []
        for sid, key, name, kind, desc, status, created, updated, owner, audience in self.db.execute(
                "SELECT id, key, name, kind, description, status, created, updated, owner_id, audience FROM strategies ORDER BY id"):
            if status == "archived" and not include_archived:
                continue
            if system_only and owner is not None:
                continue
            v = self._version_row(sid)
            tags = next((b["tags"] for b in BUILTINS if b["key"] == key), [])
            out.append({"id": sid, "key": key, "name": name, "kind": kind, "description": desc, "status": status,
                        "version": v[0] if v else None, "created": created, "updated": updated, "tags": tags, "owner_id": owner, "audience": audience})
        return out

    def get(self, sid: int) -> dict | None:
        r = self.db.execute("SELECT id, key, name, kind, description, status, created, updated, owner_id, audience FROM strategies WHERE id=?", (sid,)).fetchone()
        if not r:
            return None
        keys = ["id", "key", "name", "kind", "description", "status", "created", "updated", "owner_id", "audience"]
        d = dict(zip(keys, r))
        v = self._version_row(sid)
        d["version"], d["spec"], d["notes"] = v[0], json.loads(v[1]), v[2]
        d["versions"] = [{"version": ver, "created": c, "notes": n} for ver, c, n in
                         self.db.execute("SELECT version, created, notes FROM strategy_versions WHERE strategy_id=? ORDER BY version DESC", (sid,))]
        d["tags"] = next((b["tags"] for b in BUILTINS if b["key"] == d["key"]), [])
        return d

    def get_version(self, sid: int, version: int) -> dict | None:
        v = self._version_row(sid, version)
        return {"version": v[0], "spec": json.loads(v[1]), "notes": v[2], "created": v[3]} if v else None

    def definitions(self, only_active: bool = True, system_only: bool = False) -> list[dict]:
        """Normalised, validated executable definitions of the strategies that should run (status paper/live)."""
        self.last_errors = {}
        out = []
        for s in self.list(system_only=system_only):
            if only_active and s["status"] not in ("paper", "live"):
                continue
            d = self.get(s["id"])
            v = validate_spec(d["spec"])
            if not v["ok"]:
                self.last_errors[s["key"]] = v["errors"]
                continue
            out.append({"id": d["id"], "key": d["key"], "name": d["name"], "kind": d["kind"], "status": d["status"],
                        "version": d["version"], "spec": v["spec"], "owner_id": d["owner_id"], "audience": d["audience"]})
        return out

    def definition_by_key(self, key: str) -> dict | None:
        row = self.db.execute("SELECT id FROM strategies WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        d = self.get(row[0])
        v = validate_spec(d["spec"])
        return {"id": d["id"], "key": d["key"], "name": d["name"], "kind": d["kind"], "status": d["status"],
                "version": d["version"], "spec": v["spec"]} if v["ok"] else None

    # ------------------------------------------------------------------ writes
    @staticmethod
    def stored_key(key: str, owner_id: int | None) -> str:
        """A user's own strategies live in the same table as the shared ones, so their key is namespaced (u<id>-<key>): two users can both have "my-rsi"."""
        return key if owner_id is None else f"u{int(owner_id)}-{key}"

    @staticmethod
    def public_key(key: str, owner_id: int | None) -> str:
        prefix = f"u{int(owner_id)}-" if owner_id is not None else ""
        return key[len(prefix):] if prefix and key.startswith(prefix) else key

    def create(self, key: str, name: str, description: str, spec: dict, status: str = "paper", owner_id: int | None = None) -> int:
        if not KEY_RE.match(key or ""):
            raise RepoError("key must be 2-41 characters: lowercase letters, digits and dashes, starting with a letter")
        if status not in STATUSES or status == "archived":
            raise RepoError(f"status must be one of {STATUSES[:-1]}")
        skey = self.stored_key(key, owner_id)
        if self.db.execute("SELECT 1 FROM strategies WHERE key=?", (skey,)).fetchone():
            raise RepoError(f"a strategy with key '{key}' already exists")
        v = validate_spec(spec)
        if not v["ok"]:
            raise RepoError("invalid spec: " + "; ".join(v["errors"][:5]))
        now = time.time()
        cur = self.db.execute("INSERT INTO strategies (key, name, kind, description, status, created, updated, owner_id, audience) VALUES (?,?,?,?,?,?,?,?,?)",
                              (skey, (name or key)[:80], "custom", (description or "")[:1000], status, now, now, owner_id, "admin"))
        self.db.execute("INSERT INTO strategy_versions (strategy_id, version, spec, notes, created) VALUES (?,?,?,?,?)",
                        (cur.lastrowid, 1, _dump(spec), "Created", now))
        self.db.commit()
        return cur.lastrowid

    def new_version(self, sid: int, spec: dict, notes: str = "") -> int:
        s = self.get(sid)
        if not s:
            raise RepoError("strategy not found")
        if s["kind"] == "builtin":
            raise RepoError("built-in strategies are read-only; clone it to change it")
        v = validate_spec(spec)
        if not v["ok"]:
            raise RepoError("invalid spec: " + "; ".join(v["errors"][:5]))
        version = s["version"] + 1
        now = time.time()
        self.db.execute("INSERT INTO strategy_versions (strategy_id, version, spec, notes, created) VALUES (?,?,?,?,?)",
                        (sid, version, _dump(spec), (notes or "")[:500], now))
        # an edited strategy has to re-earn live status: results of the old version do not carry over
        new_status = "paper" if s["status"] == "live" else s["status"]
        self.db.execute("UPDATE strategies SET updated=?, status=? WHERE id=?", (now, new_status, sid))
        self.db.commit()
        return version

    def update_meta(self, sid: int, name: str | None, description: str | None) -> None:
        s = self.get(sid)
        if not s or s["kind"] == "builtin":
            raise RepoError("only custom strategies can be renamed")
        self.db.execute("UPDATE strategies SET name=?, description=?, updated=? WHERE id=?",
                        ((name or s["name"])[:80], (description if description is not None else s["description"])[:1000], time.time(), sid))
        self.db.commit()

    def set_status(self, sid: int, status: str) -> None:
        if status not in STATUSES:
            raise RepoError(f"status must be one of {STATUSES}")
        if not self.db.execute("SELECT 1 FROM strategies WHERE id=?", (sid,)).fetchone():
            raise RepoError("strategy not found")
        self.db.execute("UPDATE strategies SET status=?, updated=? WHERE id=?", (status, time.time(), sid))
        self.db.commit()

    def set_audience(self, sid: int, audience: str) -> None:
        if audience not in AUDIENCES:
            raise RepoError(f"audience must be one of {AUDIENCES}")
        row = self.db.execute("SELECT owner_id FROM strategies WHERE id=?", (sid,)).fetchone()
        if not row:
            raise RepoError("strategy not found")
        if row[0] is not None:
            raise RepoError("a user's own strategy is only ever sent to its owner")
        self.db.execute("UPDATE strategies SET audience=?, updated=? WHERE id=?", (audience, time.time(), sid))
        self.db.commit()

    def owned_count(self, owner_id: int) -> int:
        return self.db.execute("SELECT COUNT(*) FROM strategies WHERE owner_id=? AND status!='archived'", (owner_id,)).fetchone()[0]

    def clone(self, sid: int, key: str, name: str | None = None, owner_id: int | None = None) -> int:
        s = self.get(sid)
        if not s:
            raise RepoError("strategy not found")
        return self.create(key, name or f"{s['name']} (copy)", s["description"], s["spec"], status="draft", owner_id=owner_id)

    def delete(self, sid: int) -> str:
        s = self.get(sid)
        if not s:
            raise RepoError("strategy not found")
        if s["kind"] == "builtin":
            raise RepoError("built-in strategies cannot be deleted (pause or archive them)")
        if self.db.execute("SELECT 1 FROM signals WHERE strategy_id=? LIMIT 1", (sid,)).fetchone():
            self.set_status(sid, "archived")           # keep the history that belongs to it
            return "archived"
        self.db.execute("DELETE FROM strategy_versions WHERE strategy_id=?", (sid,))
        self.db.execute("DELETE FROM strategy_subscriptions WHERE strategy_id=?", (sid,))
        self.db.execute("DELETE FROM strategies WHERE id=?", (sid,))
        self.db.commit()
        return "deleted"
