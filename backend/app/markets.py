"""The public chart platform: which pairs a person may open, the market list with its padlocks, a light "live" answer for the chart (the forming candle, the countdown to the close, whether
the pair is streaming) and self sign-up.

Locks are decided HERE, on the server, and the other routes that hand out a pair's candles (backtest chart, strategy chart, the chart of a pair picked by hand) call `require` too, so a
padlock in the page is never the only thing between a free account and a paid pair.
  * Locking is OFF until the admin turns it on (nothing changes for existing users after an update). Turned on: admins and editors open every pair, anybody whose plan has "Open every
    pair" (cap.charts.all_pairs) opens every pair, everybody else only the admin's list of free pairs.
  * Sign-up is OFF until the admin opens it; open, it makes a trader account on the default plan, rate-limited per address and for the whole server.
"""
from __future__ import annotations

import re
import threading
import time

from .candles import TFS
from .control import get_setting, set_setting

FREE_KEY, SIGNUP_KEY = "platform.free_pairs", "platform.signup_open"
STREAM_TIMEOUT = 15.0
SYMBOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{1,31}$")
SIGNUP_PER_IP = (5, 3600.0)                     # at most 5 accounts an hour from one address
SIGNUP_ALL = (40, 3600.0)                       # and 40 an hour for the whole server


class Locked(Exception):
    """The pair is part of a paid plan."""


class Markets:
    def __init__(self, engine, features, auth) -> None:
        self.engine = engine
        self.features = features
        self.auth = auth
        self._signups: list[tuple[float, str]] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ the admin's switches
    def _free(self) -> list[str] | None:
        v = get_setting(self.engine.db, FREE_KEY, None)
        return [str(x) for x in v] if isinstance(v, list) else None

    def signup_open(self) -> bool:
        return bool(get_setting(self.engine.db, SIGNUP_KEY, False))

    def settings(self) -> dict:
        free = self._free()
        return {"lock": free is not None, "free_pairs": free or [], "signup_open": self.signup_open()}

    def save_settings(self, lock: bool, free_pairs: list[str], signup_open: bool) -> dict:
        clean = []
        for s in free_pairs or []:
            if not SYMBOL_RE.match(s or ""):
                raise ValueError(f"'{s}' is not a pair name")
            if s not in clean:
                clean.append(s)
        set_setting(self.engine.db, FREE_KEY, clean if lock else None)
        set_setting(self.engine.db, SIGNUP_KEY, bool(signup_open))
        return self.settings()

    # ------------------------------------------------------------------ who may open what
    def opens_all(self, user: dict | None) -> bool:
        if user is None or user.get("role") in ("admin", "editor"):
            return True
        if self._free() is None:                                   # locking is off
            return True
        return bool(self.features.can(user, "cap.charts.all_pairs"))

    def is_open(self, user: dict | None, symbol: str) -> bool:
        return self.opens_all(user) or symbol in (self._free() or [])

    def require(self, user: dict | None, symbol: str | None) -> None:
        if symbol and not self.is_open(user, symbol):
            raise Locked("this market is part of a paid plan")

    # ------------------------------------------------------------------ what the page shows
    def listing(self, user: dict | None) -> dict:
        free = self._free() or []
        allv = self.opens_all(user)
        pairs = []
        for p in self.engine.pair_list():
            inst = self.engine.instruments.get(p["symbol"], {})
            pairs.append({"symbol": p["symbol"], "name": p["name"], "market": p["market"], "category": p["category"], "payout": p["payout"], "open": inst.get("open", True),
                          "streaming": p["streaming"], "last_price": p["last_price"], "locked": not (allv or p["symbol"] in free)})
        plan = self.features._plan_for(user) if user else None
        return {"pairs": pairs, "all_open": allv, "locking": self._free() is not None, "free_pairs": free, "plan": plan["name"] if plan else None, "now": time.time()}

    def live(self, user: dict | None, symbol: str, tf: int) -> dict | None:
        """Everything the chart needs on every poll. None when the pair is unknown; raises Locked when it is not the person's to open."""
        tf = tf if tf in TFS else 60
        sel = self.engine.chart_of(symbol, tf)
        if sel is None:
            return None
        self.require(user, symbol)
        now = time.time()
        lt = self.engine.last_tick.get(symbol)
        age = (now - lt["recv"]) if lt else None
        inst = self.engine.instruments.get(symbol, {})
        streaming = bool(lt and age is not None and age < STREAM_TIMEOUT)
        is_open = inst.get("open", True)
        return {"now": now, "symbol": symbol, "name": inst.get("name", symbol), "market": inst.get("market") or ("OTC" if symbol.endswith("_otc") else "LIVE"), "payout": inst.get("payout"),
                "tf": tf, "open": is_open, "streaming": streaming, "age": age, "status": "closed" if not is_open else "live" if streaming else "waiting",
                "last_price": sel["last_price"], "forming": sel["forming"], "candles": sel["candles"], "closes_at": (int(now // tf) + 1) * tf, "gap": sel.get("gap")}

    # ------------------------------------------------------------------ sign-up
    def check_signup_rate(self, ip: str) -> float:
        """0 when a sign-up is allowed now, else the seconds to wait. Counts only attempts that reach this check."""
        now = time.time()
        with self._lock:
            self._signups = [(t, a) for t, a in self._signups if now - t < SIGNUP_ALL[1]]
            mine = [t for t, a in self._signups if a == ip]
            if len(mine) >= SIGNUP_PER_IP[0]:
                return mine[0] + SIGNUP_PER_IP[1] - now
            if len(self._signups) >= SIGNUP_ALL[0]:
                return self._signups[0][0] + SIGNUP_ALL[1] - now
            self._signups.append((now, ip))
        return 0.0
