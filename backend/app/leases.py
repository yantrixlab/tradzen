"""Live prices from ordinary accounts: one holder per pair (a lease).

Live prices used to come only from the admin's price-feed tab, so only the pairs open in that one tab produced signals. Any signed-in extension also sees the live ticks
of the pairs open in ITS Quotex tab. This lets those accounts stream them, under these rules:

  1. One source per pair. A pair is held by exactly one account at a time (its lease); everybody else's ticks for it are only compared with it ("shadow"), never applied.
     The admin's price feed always has priority: while it streams a pair (a tick within FEEDER_TTL) no lease exists on it, and a lease holder is demoted the moment the
     feed returns. A lease that sends nothing for LEASE_TTL seconds is free; the next account that offers ticks for the pair takes it. The gap is at most a few
     seconds, and the engine already discards a candle with a hole in its ticks, so a hand-over can lose one candle but never make a wrong one.
  2. The server decides what is sent. The extension only says which pairs it sees ("presence"); the answer lists the pairs to send (those it holds, free ones, and a
     short spot-check window now and then on a held pair), so bandwidth is spent only where the server needs it.
  3. Every tick is checked: a finite positive price, not old (30 s) and not in the future, newer than the last applied tick of the pair (a replay or a second tab
     is "late", not an offence), and close to the previous tick (2 %), or - after a pause - to the last stored candle (10 %). Five bad ticks in a row cost the lease.
  4. Load: a per-account and a global tick allowance (token buckets); over them the extra ticks are dropped and the answer says "busy". At most MAX_LEASES pairs per account.
  5. Trust: spot-check ticks are compared with the applied ones (same moment, 0.1 %). An account that is mostly refused, or mostly disagrees with the others, is paused
     automatically. The admin can pause, resume, or revoke (delete the candles built from that account's ticks: source "tick:<account id>").
"""
from __future__ import annotations

import math
import time

from .contrib import SYMBOL_RE, Bucket, _day, _rel

FEEDER_TTL = 8.0                 # a pair the price feed sent a tick for this recently belongs to the feed
LEASE_TTL = 5.0                  # a holder silent for this long loses the pair
MAX_LEASES = 8                   # pairs one account may hold
TICK_AGE = 30.0                  # older ticks are late (a throttled tab), not bad
TICK_AHEAD = 10.0                # ticks further in the future are bad
JUMP_LIMIT = 0.02                # a tick vs the previous applied tick of the pair
LEVEL_LIMIT = 0.10               # the first tick after a pause vs the last stored 1-minute candle ...
LEVEL_FRESH = 6 * 3600           # ... when that candle is not older than this
REF_FRESH = 120.0                # the previous tick is a reference while it is not older than this
STRIKES = 5                      # bad ticks in a row that cost the lease
MAX_TICKS = 300                  # ticks looked at per request (the newest)
MAX_PRESENCE = 24
PRESENCE_TTL = 20.0              # a presence item holds this long (the extension repeats it every 5 s)
USER_TICKS = (40.0, 400)         # per account: ticks per second, burst
GLOBAL_TICKS = (2000.0, 10000)   # the whole server
VERIFY_EVERY = 120.0             # a held pair is spot-checked by another account at most this often ...
VERIFY_WINDOW = 15.0             # ... for this long
SHADOW_TOL = 0.001               # relative difference above which a spot-check tick disagrees
SHADOW_DT = 3.0                  # ... against the applied tick nearest in time, when that is not further away than this
SHADOW_MAX = 20                  # spot-check ticks compared per pair and request
FLUSH_EVERY = 30.0
AUTO_DAYS = 2
AUTO_MIN_TICKS = 200             # judged only after this many ticks ...
AUTO_BAD_SHARE = 0.3             # ... paused when more than this share was refused
AUTO_MIN_SHADOW = 60
AUTO_SHADOW_BAD = 0.5            # ... or when more than this share of its spot-check ticks disagreed

_COUNTERS = ("requests", "applied", "bad", "late", "shadow_ok", "shadow_bad", "dropped")


class Leases:
    def __init__(self, engine, enabled: bool = True) -> None:
        self.engine = engine
        self.db = engine.db
        self.enabled = enabled
        self.holders: dict[str, dict] = {}                 # symbol -> {uid, since, last, n, strikes}
        self._user: dict[int, Bucket] = {}
        self._global = Bucket(*GLOBAL_TICKS)
        self._present: dict[int, tuple[float, list[str]]] = {}
        self._verify: dict[tuple[str, int], float] = {}
        self._verified: dict[str, float] = {}
        self._st: dict[tuple[str, int], dict] = {}         # (day, uid) -> counters not yet written
        self._flushed = time.time()

    # ------------------------------------------------------------------ who may feed
    def blocked(self, uid: int) -> dict | None:
        row = self.db.execute("SELECT reason, since, auto FROM feed_block WHERE uid=?", (uid,)).fetchone()
        return {"reason": row[0], "since": row[1], "auto": bool(row[2])} if row else None

    def drop(self, uid: int) -> None:
        for sym in [s for s, h in self.holders.items() if h["uid"] == uid]:
            del self.holders[sym]

    def pause(self, uid: int, reason: str, auto: bool = False) -> None:
        self.db.execute("INSERT OR REPLACE INTO feed_block (uid, reason, since, auto) VALUES (?,?,?,?)", (uid, reason[:200], time.time(), int(auto)))
        self.db.commit()
        self.drop(uid)

    def resume(self, uid: int) -> bool:
        cur = self.db.execute("DELETE FROM feed_block WHERE uid=?", (uid,))
        self.db.commit()
        return cur.rowcount > 0

    def revoke(self, uid: int) -> int:
        """Remove every candle built from this account's ticks (1m, 5m and 15m). Candles the feed (or a hand-over of several sources) built are not touched."""
        self.drop(uid)
        self.engine._flush(force=True)
        cur = self.db.execute("DELETE FROM candles WHERE source=?", (f"tick:{int(uid)}",))
        self.db.commit()
        if cur.rowcount:
            self.engine.series.clear()
            self.engine._load_candles()
        return cur.rowcount

    # ------------------------------------------------------------------ one request
    def answer(self, uid: int, items: list[dict], now: float | None = None) -> dict:
        """The tick items ("t") and the presence item ("tp") of one /ingest batch from an ordinary account. Returns
        {"state": on|busy|paused|off, "send": [pairs to send ticks for], "hold": [pairs this account holds], "applied", "retry_after"?}."""
        now = now or time.time()
        if not self.enabled:
            return {"state": "off", "send": [], "hold": []}
        if self.blocked(uid):
            self.drop(uid)
            return {"state": "paused", "send": [], "hold": []}
        st = self._stat(uid, now)
        st["requests"] += 1
        self._expire(now)
        eng = self.engine
        ticks, present, explicit, dropped = self._parse(items, now)
        prev = self._present.get(uid)
        if explicit:
            self._present[uid] = (now, present)
        elif prev and now - prev[0] <= PRESENCE_TTL:                   # a request without a presence item (ticks only, or a plain poll): the last one still holds
            present = list(dict.fromkeys([*prev[1], *present]))[:MAX_PRESENCE]
        out = {"state": "on", "applied": 0}
        n = sum(len(v) for v in ticks.values())
        if n:
            wait = max(self._bucket(uid).take(n), self._global.take(n))
            if wait > 0:
                st["dropped"] += n
                out.update(state="busy", retry_after=max(1, math.ceil(wait)))
                ticks = {}
        st["dropped"] += dropped
        applied = 0
        for sym, rows in ticks.items():
            applied += self._sym(uid, sym, rows, now, st)
        if applied:
            eng.ingest_stats["last_recv"] = now
            eng.ingest_stats["ticks"] += applied
        out["applied"] = applied
        out["hold"] = sorted(s for s, h in self.holders.items() if h["uid"] == uid)
        out["send"] = self._wanted(uid, present, now)
        if now - self._flushed >= FLUSH_EVERY:
            self.flush(now)
        return out

    def _bucket(self, uid: int) -> Bucket:
        b = self._user.get(uid)
        if b is None:
            if len(self._user) > 5000:
                self._user.clear()
            b = self._user[uid] = Bucket(*USER_TICKS)
        return b

    def _parse(self, items: list[dict], now: float) -> tuple[dict[str, list[tuple[float, float]]], list[str], bool, int]:
        """Ticks grouped by pair (oldest first, only the newest MAX_TICKS kept), the pairs the extension sees, whether it said so explicitly (a presence item),
        and how many ticks were dropped unread."""
        eng = self.engine
        rows: list[tuple[float, str, float]] = []
        present: list[str] = []
        explicit = False
        dropped = 0
        for it in items:
            k = it.get("k")
            if k == "tp" and isinstance(it.get("syms"), list):
                explicit = True
                present.extend(s for s in it["syms"][:MAX_PRESENCE * 2] if isinstance(s, str))
            elif k == "t" and isinstance(it.get("d"), list):
                for r in it["d"][:2000]:
                    try:
                        sym, ts, price = r[0], float(r[1]), float(r[2])
                    except (TypeError, ValueError, IndexError):
                        dropped += 1
                        continue
                    if not isinstance(sym, str) or not SYMBOL_RE.match(sym) or sym not in eng.instruments:
                        dropped += 1
                        continue
                    rows.append((ts / 1000.0 if ts > 1e11 else ts, sym, price))
                    present.append(sym)
        if len(rows) > MAX_TICKS:
            rows.sort()
            dropped += len(rows) - MAX_TICKS
            rows = rows[-MAX_TICKS:]
        by: dict[str, list[tuple[float, float]]] = {}
        for ts, sym, price in sorted(rows):
            by.setdefault(sym, []).append((ts, price))
        seen: list[str] = []
        for s in present:
            if s in eng.instruments and SYMBOL_RE.match(s) and s not in seen:
                seen.append(s)
        return by, seen[:MAX_PRESENCE], explicit, dropped

    def _sym(self, uid: int, sym: str, rows: list[tuple[float, float]], now: float, st: dict) -> int:
        eng = self.engine
        h = self.holders.get(sym)
        if now - eng.feed_at.get(sym, 0.0) < FEEDER_TTL:
            if h:
                del self.holders[sym]                      # the price feed is back: it has priority
            self._shadow(sym, rows, st)
            return 0
        if h and h["uid"] != uid:
            self._shadow(sym, rows, st)
            return 0
        fresh = h is None
        if fresh:
            if sum(1 for x in self.holders.values() if x["uid"] == uid) >= MAX_LEASES:
                st["dropped"] += len(rows)
                return 0
            h = {"uid": uid, "since": now, "last": now, "n": 0, "strikes": 0}
        applied = 0
        for ts, price in rows:
            why = self._check(sym, ts, price, now)
            if why is None:
                eng.on_tick(sym, ts, price, src=f"tick:{uid}")
                applied += 1
                h["strikes"] = 0
            elif why == "late":
                st["late"] += 1
            else:
                st["bad"] += 1
                h["strikes"] += 1
                if h["strikes"] >= STRIKES:
                    self.holders.pop(sym, None)
                    eng._log("lease-lost", f"{sym}: account {uid} sent {STRIKES} refused ticks in a row ({why})")
                    st["applied"] += applied
                    return applied
        st["applied"] += applied
        if applied or (rows and not fresh):
            h["last"] = now
            h["n"] += applied
        if fresh and applied:
            self.holders[sym] = h
            eng._log("lease", f"{sym}: account {uid} streams it")
        return applied

    def _check(self, sym: str, ts: float, price: float, now: float) -> str | None:
        """Why this tick is not applied: "late" (old, or not newer than the last applied tick) is harmless, anything else counts against the account."""
        if not (math.isfinite(ts) and math.isfinite(price)) or price <= 0:
            return "bad number"
        if now - ts > TICK_AGE:
            return "late"
        if ts - now > TICK_AHEAD:
            return "from the future"
        eng = self.engine
        lt = eng.last_tick.get(sym)
        if lt and ts <= lt["ts"]:
            return "late"
        if lt and now - lt["recv"] <= REF_FRESH:
            if _rel(price, lt["price"]) > JUMP_LIMIT:
                return "jump"
            return None
        s = eng.series.get((sym, 60))
        tail = s.tail(1) if s else []
        if tail and ts - tail[0]["t"] <= LEVEL_FRESH and _rel(price, tail[0]["c"]) > LEVEL_LIMIT:
            return "level"
        return None

    def _shadow(self, sym: str, rows: list[tuple[float, float]], st: dict) -> None:
        """Compare an account's ticks with the ones that are applied (never apply them)."""
        ring = self.engine.tick_ring.get(sym)
        if not ring:
            return
        for ts, price in rows[-SHADOW_MAX:]:
            near = min(ring, key=lambda r: abs(r[0] - ts))
            if abs(near[0] - ts) > SHADOW_DT or not (math.isfinite(price) and price > 0):
                continue
            st["shadow_ok" if _rel(price, near[1]) <= SHADOW_TOL else "shadow_bad"] += 1

    def _wanted(self, uid: int, present: list[str], now: float) -> list[str]:
        """The pairs this account should send ticks for now."""
        eng = self.engine
        mine = sum(1 for h in self.holders.values() if h["uid"] == uid)
        send: list[str] = []
        for sym in present:
            if now - eng.feed_at.get(sym, 0.0) < FEEDER_TTL:
                continue                                    # the price feed streams it
            h = self.holders.get(sym)
            if h and h["uid"] == uid:
                send.append(sym)
            elif h is None:
                if mine < MAX_LEASES:
                    send.append(sym)                        # free: whoever sends first holds it
            elif self._verify.get((sym, uid), 0.0) > now:
                send.append(sym)                            # inside its spot-check window
            elif now - self._verified.get(sym, 0.0) >= VERIFY_EVERY:
                self._verify[(sym, uid)] = now + VERIFY_WINDOW
                self._verified[sym] = now
                send.append(sym)
        if len(self._verify) > 2000:
            self._verify = {k: v for k, v in self._verify.items() if v > now}
        return send[:MAX_LEASES + 4]

    def _expire(self, now: float) -> None:
        for sym in [s for s, h in self.holders.items() if now - h["last"] > LEASE_TTL]:
            del self.holders[sym]

    # ------------------------------------------------------------------ counters and trust
    def _stat(self, uid: int, now: float) -> dict:
        key = (_day(now), uid)
        st = self._st.get(key)
        if st is None:
            st = self._st[key] = dict.fromkeys(_COUNTERS, 0)
        return st

    def flush(self, now: float | None = None) -> None:
        now = now or time.time()
        self._flushed = now
        touched = set()
        for (day, uid), st in self._st.items():
            if not any(st.values()):
                continue
            self.db.execute(
                "INSERT INTO feed_stats (day, uid, requests, applied, bad, late, shadow_ok, shadow_bad, dropped) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(day, uid) DO UPDATE SET requests=requests+excluded.requests, applied=applied+excluded.applied, bad=bad+excluded.bad, late=late+excluded.late, "
                "shadow_ok=shadow_ok+excluded.shadow_ok, shadow_bad=shadow_bad+excluded.shadow_bad, dropped=dropped+excluded.dropped",
                (day, uid, *(st[c] for c in _COUNTERS)))
            touched.add(uid)
        self._st.clear()
        self.db.commit()
        for uid in touched:
            self._judge(uid, now)

    def _judge(self, uid: int, now: float) -> None:
        since = _day(now - (AUTO_DAYS - 1) * 86400)
        applied, bad, ok, sbad = self.db.execute("SELECT COALESCE(SUM(applied),0), COALESCE(SUM(bad),0), COALESCE(SUM(shadow_ok),0), COALESCE(SUM(shadow_bad),0) "
                                                 "FROM feed_stats WHERE uid=? AND day>=?", (uid, since)).fetchone()
        if self.blocked(uid):
            return
        why = None
        if applied + bad >= AUTO_MIN_TICKS and bad / (applied + bad) > AUTO_BAD_SHARE:
            why = f"{bad} of {applied + bad} ticks in {AUTO_DAYS} days were refused (jumps, wrong price level or time)"
        elif ok + sbad >= AUTO_MIN_SHADOW and sbad / (ok + sbad) > AUTO_SHADOW_BAD:
            why = f"{sbad} of {ok + sbad} spot-check ticks in {AUTO_DAYS} days disagreed with the other sources"
        if why:
            self.pause(uid, why, auto=True)
            self.engine._log("feed-paused", f"account {uid}: {why}")

    # ------------------------------------------------------------------ reports
    def _names(self, syms) -> dict[str, str]:
        return {s: (self.engine.instruments.get(s) or {}).get("name") or s for s in syms}

    def mine(self, uid: int, now: float | None = None) -> dict:
        now = now or time.time()
        self.flush(now)
        keys = _COUNTERS
        today = self.db.execute(f"SELECT {', '.join(keys)} FROM feed_stats WHERE day=? AND uid=?", (_day(now), uid)).fetchone()
        week = self.db.execute(f"SELECT {', '.join('COALESCE(SUM(%s),0)' % k for k in keys)} FROM feed_stats WHERE uid=? AND day>=?", (uid, _day(now - 6 * 86400))).fetchone()
        hold = sorted(s for s, h in self.holders.items() if h["uid"] == uid)
        nm = self._names(hold)
        return {"enabled": self.enabled, "paused": self.blocked(uid), "holding": [{"symbol": s, "name": nm[s]} for s in hold],
                "today": dict(zip(keys, today or (0,) * len(keys))), "week": dict(zip(keys, week))}

    def overview(self, now: float | None = None) -> dict:
        now = now or time.time()
        self.flush(now)
        self._expire(now)
        eng = self.engine
        users = {i: n for i, n in self.db.execute("SELECT id, username FROM users")}
        stored = {s: c for s, c in self.db.execute("SELECT source, COUNT(*) FROM candles WHERE source LIKE 'tick:%' GROUP BY source")}
        nm = self._names(list(self.holders) + list(eng.feed_at))
        holders = [{"symbol": s, "name": nm[s], "uid": h["uid"], "username": users.get(h["uid"], f"#{h['uid']}"), "since": h["since"], "age": round(now - h["last"], 1), "ticks": h["n"]}
                   for s, h in sorted(self.holders.items())]
        by_feed = [{"symbol": s, "name": nm[s], "age": round(now - at, 1)} for s, at in sorted(eng.feed_at.items()) if now - at < FEEDER_TTL]
        keys = _COUNTERS
        rows = []
        for r in self.db.execute(f"SELECT uid, {', '.join('SUM(%s)' % k for k in keys)} FROM feed_stats WHERE day>=? GROUP BY uid ORDER BY SUM(applied) DESC", (_day(now - 6 * 86400),)):
            rows.append({"uid": r[0], "username": users.get(r[0], f"#{r[0]}"), **dict(zip(keys, r[1:])), "stored": stored.get(f"tick:{r[0]}", 0), "paused": self.blocked(r[0]),
                         "holding": sorted(s for s, h in self.holders.items() if h["uid"] == r[0])})
        return {"enabled": self.enabled, "holders": holders, "feed_pairs": by_feed, "users": rows, "candles_from_accounts": sum(stored.values()),
                "limits": {"ticks_per_second": int(USER_TICKS[0]), "max_pairs_per_account": MAX_LEASES, "lease_seconds": LEASE_TTL, "feed_priority_seconds": FEEDER_TTL,
                           "jump_limit": JUMP_LIMIT, "auto_pause_share": AUTO_BAD_SHARE}}
