"""Who may see, change and receive which strategy.

  * A shared strategy (owner_id NULL) is made and controlled by staff (admin, editor). Its audience says who gets its signals:
      admin    still being tested: staff only
      optin    published: it is listed in the library, a user adds it to their account to receive it
      everyone published: every user receives it (a user can still switch it off for themselves)
  * A user's own strategy (owner_id = the user) is seen, changed and received by that user only. It runs on the same shared candles.
  * The logic (the spec) of a strategy is visible to whoever may change it, never to a user who only receives it.
"""
from __future__ import annotations

import time

STAFF = ("admin", "editor")
PUBLISHED = ("optin", "everyone")
CACHE_SECONDS = 5.0


def is_staff(user: dict | None) -> bool:
    return user is None or user.get("role") in STAFF


class Access:
    def __init__(self, engine) -> None:
        self.engine = engine
        self._subs: dict[int, tuple[float, dict[int, bool]]] = {}

    # ---- the strategies
    def meta(self, sid: int | None) -> dict | None:
        return self.engine.strategy_meta.get(sid) if sid is not None else None

    def can_edit(self, user: dict, sid: int) -> bool:
        """Change, run, backtest, read the logic of."""
        m = self.meta(sid)
        if not m:
            return False
        if m["owner"] is not None:
            return m["owner"] == user["id"]
        return is_staff(user)

    def in_library(self, m: dict) -> bool:
        return m["owner"] is None and m["audience"] in PUBLISHED and m["status"] in ("live", "paper", "paused")

    def can_view(self, user: dict, sid: int) -> bool:
        """See that it exists (name, description, record) - without its logic unless can_edit."""
        m = self.meta(sid)
        if not m:
            return False
        return self.can_edit(user, sid) or self.in_library(m)

    def visible_ids(self, user: dict) -> set[int]:
        return {sid for sid in self.engine.strategy_meta if self.can_view(user, sid)}

    # ---- subscriptions
    def subscriptions(self, uid: int) -> dict[int, bool]:
        """strategy id -> muted, for the strategies the user added (optin) or switched off (everyone)."""
        hit = self._subs.get(uid)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
        subs = {sid: bool(m) for sid, m in self.engine.db.execute("SELECT strategy_id, muted FROM strategy_subscriptions WHERE user_id=?", (uid,))}
        self._subs[uid] = (time.time(), subs)
        return subs

    def _changed(self, uid: int) -> None:
        self._subs.pop(uid, None)

    def subscribe(self, uid: int, sid: int) -> None:
        self.engine.db.execute("INSERT INTO strategy_subscriptions (user_id, strategy_id, muted, created) VALUES (?,?,0,?) "
                               "ON CONFLICT(user_id, strategy_id) DO UPDATE SET muted=0", (uid, sid, time.time()))
        self.engine.db.commit()
        self._changed(uid)

    def unsubscribe(self, uid: int, sid: int) -> None:
        """optin: remove it. everyone: remember that this user switched it off."""
        m = self.meta(sid)
        if m and m["audience"] == "everyone":
            self.engine.db.execute("INSERT INTO strategy_subscriptions (user_id, strategy_id, muted, created) VALUES (?,?,1,?) "
                                   "ON CONFLICT(user_id, strategy_id) DO UPDATE SET muted=1", (uid, sid, time.time()))
        else:
            self.engine.db.execute("DELETE FROM strategy_subscriptions WHERE user_id=? AND strategy_id=?", (uid, sid))
        self.engine.db.commit()
        self._changed(uid)

    def receives(self, user: dict, sid: int) -> bool:
        """Does this user get the alerts and may browse the signals of this strategy?"""
        m = self.meta(sid)
        if not m:
            return False
        if m["owner"] is not None:
            return m["owner"] == user["id"]
        if is_staff(user):
            return True
        if not self.in_library(m):
            return False
        subs = self.subscriptions(user["id"])
        if m["audience"] == "everyone":
            return not subs.get(sid, False)
        return sid in subs and not subs[sid]                       # optin: added and not switched off

    def allowed_signal_ids(self, user: dict) -> set[int]:
        return {sid for sid in self.engine.strategy_meta if self.receives(user, sid)}

    # ---- alert events
    def event_ok(self, user: dict | None, ev: dict) -> bool:
        """May this user get this alert? Private alerts only go to their owner; a shared strategy's alert goes to whoever its audience includes."""
        if ev.get("to") is not None:
            return user is not None and ev["to"] == user.get("id")
        if user is None:
            return True
        aud = ev.get("aud")
        if aud == "admin" or aud is None:                          # testing (or untagged): staff only
            return user.get("role") in STAFF
        if user.get("role") == "admin":
            return True
        if user.get("role") == "editor" and aud in PUBLISHED:
            return True
        return self.receives(user, ev.get("strategy_id")) if ev.get("strategy_id") is not None else aud == "everyone"

    # ---- the library page
    def library(self, user: dict) -> list[dict]:
        out = []
        subs = self.subscriptions(user["id"])
        for s in self.engine.strategy_summary(ids={sid for sid, m in self.engine.strategy_meta.items() if self.in_library(m)}, viewer=user):
            m = self.meta(s["id"])
            on = (not subs.get(s["id"], False)) if m["audience"] == "everyone" else (s["id"] in subs and not subs[s["id"]])
            out.append({**s, "receiving": on, "always": m["audience"] == "everyone"})
        return out
