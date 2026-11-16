"""Each account's own display preferences (which chart layers are on, the indicators picked ...): small JSON values kept per user in the main database, so they follow
the person to another browser or computer. Only the SPA reads and writes them; nothing here affects trading."""
from __future__ import annotations

import json
import re

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,59}$")
MAX_VALUE = 4000                 # characters of JSON per value
MAX_KEYS = 120


def get_all(db, uid: int) -> dict:
    out = {}
    for k, v in db.execute("SELECT key, value FROM user_prefs WHERE user_id=?", (uid,)):
        try:
            out[k] = json.loads(v)
        except ValueError:
            continue
    return out


def apply(db, uid: int, changes: dict) -> dict:
    """Set the given keys (a value of None removes the key). Raises ValueError for a bad key / value; returns everything the user has stored."""
    if not isinstance(changes, dict) or len(changes) > 40:
        raise ValueError("prefs must be an object with at most 40 keys per request")
    for k, v in changes.items():
        if not isinstance(k, str) or not KEY_RE.match(k):
            raise ValueError(f"bad preference name: {k!r}")
        if v is not None and len(json.dumps(v, separators=(",", ":"))) > MAX_VALUE:
            raise ValueError(f"the value of '{k}' is too large")
    have = {r[0] for r in db.execute("SELECT key FROM user_prefs WHERE user_id=?", (uid,))}
    if len(have | {k for k, v in changes.items() if v is not None}) > MAX_KEYS:
        raise ValueError("too many stored preferences")
    for k, v in changes.items():
        if v is None:
            db.execute("DELETE FROM user_prefs WHERE user_id=? AND key=?", (uid, k))
        else:
            db.execute("INSERT INTO user_prefs (user_id, key, value) VALUES (?,?,?) ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
                       (uid, k, json.dumps(v, separators=(",", ":"))))
    db.commit()
    return get_all(db, uid)
