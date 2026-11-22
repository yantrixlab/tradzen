"""Where the candles come from, and who may supply them (admin).

Today three kinds of sender can put market data into the server:
  * a FEEDER: an admin's price-feed token - the admin's own Quotex tab (the extension) or the server's feed worker (app/feedworker.py, a process that logs in to Quotex itself)
  * ordinary accounts' extensions, which share the chart history their Quotex page loads (`contrib`, contrib.py) and stream the live prices of a pair when nobody else does (`leases`, leases.py)
This module lets the admin switch the last two off at runtime (they stay off after a restart: the choice is stored in the settings table and wins over QXS_CONTRIB / QXS_LIVE_FEED),
and shows who is feeding the market right now: each feeder with its agent name and age, the pairs that stream and through whom, and whether the feed worker is alive.
Turn them off once the feed worker is healthy: then nobody's chart supplies candles any more.
"""
from __future__ import annotations

import os
import time

from .candles import is_display_only
from .control import get_setting, set_setting

KEY = "sources.switches"
STREAM_TIMEOUT = 15.0
FEEDER_TTL = 8.0
WORKER_PREFIX = "feedworker"
WORKER_ALIVE = 30.0               # a feed worker that sent something this recently is alive


def _env_on(name: str) -> bool:
    return os.getenv(name, "on").lower() not in ("off", "0", "false", "no")


def apply(engine) -> None:
    """Make the stored switches the running ones (called at start-up and after a change)."""
    s = get_setting(engine.db, KEY, None) or {}
    if "contrib" in s:
        engine.contrib.enabled = bool(s["contrib"])
    if "leases" in s:
        engine.leases.enabled = bool(s["leases"])


def switches(engine) -> dict:
    return {"contrib": {"on": bool(engine.contrib.enabled), "default": _env_on("QXS_CONTRIB")},
            "leases": {"on": bool(engine.leases.enabled), "default": _env_on("QXS_LIVE_FEED")}}


def save(engine, contrib: bool, leases: bool) -> dict:
    set_setting(engine.db, KEY, {"contrib": bool(contrib), "leases": bool(leases)})
    apply(engine)
    engine._log("sources", f"candles from ordinary accounts: {'ON' if contrib else 'OFF'}; live prices from ordinary accounts: {'ON' if leases else 'OFF'}")
    return switches(engine)


def status(engine, now: float | None = None) -> dict:
    now = now or time.time()
    names = {i: n for i, n in engine.db.execute("SELECT id, username FROM users")}
    feeders = []
    for uid, rec in sorted(engine.feeder_seen.items()):
        age = now - rec["last"]
        live = sum(1 for t in rec["pairs"].values() if now - t < STREAM_TIMEOUT)
        feeders.append({"uid": uid, "username": names.get(uid, "local" if uid == engine.primary_uid else f"#{uid}"), "agent": rec["agent"], "worker": str(rec["agent"]).startswith(WORKER_PREFIX),
                        "age": round(age, 1), "alive": age < WORKER_ALIVE, "ticks": rec["ticks"], "batches": rec["batches"], "pairs_live": live, "since": rec["first"]})
    by = {"feed": 0, "lease": 0, "other": 0}
    for sym, lt in engine.last_tick.items():
        if now - lt["recv"] >= STREAM_TIMEOUT or is_display_only(sym):
            continue
        if now - engine.feed_at.get(sym, 0.0) < FEEDER_TTL:
            by["feed"] += 1
        elif sym in engine.leases.holders:
            by["lease"] += 1
        else:
            by["other"] += 1
    workers = [f for f in feeders if f["worker"]]
    alive = [f for f in workers if f["alive"]]
    return {"switches": switches(engine), "feeders": feeders, "streaming": by, "total_streaming": sum(by.values()),
            "worker": {"seen": bool(workers), "alive": bool(alive), "age": min((f["age"] for f in workers), default=None), "pairs_live": max((f["pairs_live"] for f in alive), default=0)},
            "ready_to_switch_off": bool(alive) and by["lease"] == 0 and by["other"] == 0,
            "stored_from_accounts": engine.db.execute("SELECT COUNT(*) FROM candles WHERE source LIKE 'contrib:%' OR source LIKE 'tick:%'").fetchone()[0]}
