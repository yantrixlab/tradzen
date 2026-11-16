"""Shared candles: one candle store for everybody, fed by every signed-in extension.

The price feed (the admin's tab) builds candles from live ticks. Every other extension can add what its Quotex page loads anyway: the chart history that arrives when
a pair is opened or scrolled back. That fills holes the feed missed (a hidden tab, a restart) and gives the pairs nobody streams their past, so the whole server works
on the same, ever more complete data.

Rules, in the order they are applied to one batch of history rows:

  1. Who:    the account must be allowed (plan feature, a linked Quotex account when linking is on) and not paused.
  2. Load:   a per-account allowance (batches per minute, rows per hour) and one global write budget. Beyond them the batch is refused with "busy" and a retry time -
             nothing is queued, so a flood costs one comparison, not a database write.
  3. Shape:  a known pair, a timeframe the engine stores, candles that are closed, aligned to their timeframe, not absurdly old, with sane OHLC and no jump
             between neighbours. A batch whose price level is far from what the server already holds for that pair is refused as a whole (wrong pair, wrong scale).
  4. Merge:  FIRST WRITE WINS. A candle the server already has is never changed by a contribution. If the stored one disagrees (beyond a small tolerance) it is
             counted as a conflict and kept for the admin to look at. Only missing candles are inserted (source "contrib:<account id>", so one account's data can be
             removed as a whole). The price feed's own live candle may later replace a contributed one: the feed is the authority.
  5. Trust:  an account whose contributions are mostly refused or in conflict is paused automatically; the admin can pause, resume or revoke anybody.

It never touches the live candle that is being built from ticks, so a contribution cannot disturb a running signal.
"""
from __future__ import annotations

import json
import math
import re
import statistics
import time

from .candles import TFS, infer_tf, rows_from_history

SYMBOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{1,31}$")
MAX_ROWS = 1000                  # candles in one history item
MAX_ITEMS = 12                   # history items in one /ingest batch
MAX_AGE = 800 * 86400            # older than this is not accepted
CLOSE_SLACK = 5                  # a candle must have ended at least this many seconds ago
RANGE_LIMIT = 0.25               # (high - low) / close above this is not a candle
JUMP_LIMIT = 0.05                # open vs the previous close of the neighbouring candle
LEVEL_LIMIT = 0.15               # median price of the batch vs the nearest stored candle
LEVEL_WINDOW = 7 * 86400         # ... when that candle is not further away than this
CONFLICT_TOL = 0.002             # relative difference of open / high / low / close above which a stored candle and a contribution disagree
USER_BATCHES = (0.2, 12)         # per account: refill per second, burst  (12 history items a minute)
USER_ROWS = (6.0, 4000)          # per account: candles per second (about 21,000 an hour), burst
GLOBAL_ROWS = (3000.0, 20000)    # the whole server: candles per second that contributions may cost
AUTO_WINDOW_DAYS = 7
AUTO_MIN_BATCHES = 3             # ... and this many history items (one odd batch never pauses anybody)
AUTO_MIN_ROWS = 300              # an account is judged only after this many candles in the window ...
AUTO_BAD_SHARE = 0.4             # ... and is paused when more than this share was refused or in conflict
WANT_TTL = 120.0                 # the list of wanted ranges is recomputed at most this often
DEPTH_TTL = 600.0                # ... and the (heavier) per-pair depth part this often
KEEP_DISPUTES = 500


class Bucket:
    """Token bucket: `rate` tokens a second up to `burst`. take(n) returns 0 when allowed, otherwise the seconds to wait."""

    def __init__(self, rate: float, burst: float) -> None:
        self.rate, self.burst, self.level, self.at = float(rate), float(burst), float(burst), time.monotonic()

    def take(self, n: float = 1.0) -> float:
        now = time.monotonic()
        self.level = min(self.burst, self.level + (now - self.at) * self.rate)
        self.at = now
        if n > self.burst:
            n = self.burst
        if self.level >= n:
            self.level -= n
            return 0.0
        return (n - self.level) / self.rate


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def _rel(a: float, b: float) -> float:
    return abs(a - b) / max(abs(b), 1e-12)


def check_row(r: dict, tf: int, now: float) -> str | None:
    """Why this candle cannot be accepted, or None. (Forming candles are filtered out before.)"""
    o, h, l, c = r["o"], r["h"], r["l"], r["c"]
    if not all(math.isfinite(x) and x > 0 for x in (o, h, l, c)):
        return "bad number"
    if h < max(o, c) * (1 - 1e-9) or l > min(o, c) * (1 + 1e-9):
        return "bad shape"
    if (h - l) / c > RANGE_LIMIT:
        return "range too large"
    if r["t"] <= 0 or r["t"] % tf:
        return "misaligned"
    if r["t"] < now - MAX_AGE:
        return "too old"
    return None


class Contributions:
    def __init__(self, engine, allowed_tfs: tuple[int, ...], enabled: bool = True) -> None:
        self.engine = engine
        self.db = engine.db
        self.allowed_tfs = set(allowed_tfs)
        self.enabled = enabled
        self._user_batches: dict[int, Bucket] = {}
        self._user_rows: dict[int, Bucket] = {}
        self._global = Bucket(*GLOBAL_ROWS)
        self._want: tuple[float, list[dict]] = (0.0, [])
        self._depth: tuple[float, list[dict]] = (0.0, [])

    # ------------------------------------------------------------------ who may contribute
    def blocked(self, uid: int) -> dict | None:
        row = self.db.execute("SELECT reason, since, auto FROM candle_contrib_block WHERE uid=?", (uid,)).fetchone()
        return {"reason": row[0], "since": row[1], "auto": bool(row[2])} if row else None

    def pause(self, uid: int, reason: str, auto: bool = False) -> None:
        self.db.execute("INSERT OR REPLACE INTO candle_contrib_block (uid, reason, since, auto) VALUES (?,?,?,?)", (uid, reason[:200], time.time(), int(auto)))
        self.db.commit()

    def resume(self, uid: int) -> bool:
        cur = self.db.execute("DELETE FROM candle_contrib_block WHERE uid=?", (uid,))
        self.db.commit()
        return cur.rowcount > 0

    def revoke(self, uid: int) -> int:
        """Remove every candle this account contributed (also the 5m / 15m candles built from them). The live memory is reloaded from the table."""
        self.engine._flush(force=True)
        cur = self.db.execute("DELETE FROM candles WHERE source=?", (f"contrib:{int(uid)}",))
        self.db.commit()
        if cur.rowcount:
            self.engine.series.clear()
            self.engine._load_candles()
        return cur.rowcount

    # ------------------------------------------------------------------ intake
    def intake(self, uid: int, items: list[dict], now: float | None = None) -> dict:
        """The history items of one /ingest batch from a non-feeder account. Returns {"state": on|busy|paused, "retry_after", "results": [...]}."""
        now = now or time.time()
        if not self.enabled:
            return {"state": "off", "results": []}
        if self.blocked(uid):
            return {"state": "paused", "results": []}
        out: list[dict] = []
        for item in items[:MAX_ITEMS]:
            wait = self._allow(uid, _n_rows(item))
            if wait:
                return {"state": "busy", "retry_after": int(wait) + 1, "results": out}
            out.append(self._one(uid, item, now))
        if len(items) > MAX_ITEMS:
            extra = sum(_n_rows(i) for i in items[MAX_ITEMS:])
            self._count(uid, now, received=extra, rejected=extra)
        self._judge(uid, now)
        return {"state": "paused" if self.blocked(uid) else "on", "results": out}

    def _allow(self, uid: int, n_rows: int) -> float:
        n = float(min(max(n_rows, 1), MAX_ROWS))
        b = self._user_batches.setdefault(uid, Bucket(*USER_BATCHES))
        r = self._user_rows.setdefault(uid, Bucket(*USER_ROWS))
        if len(self._user_batches) > 5000:
            for k in [k for k, v in self._user_batches.items() if v.level >= v.burst]:
                self._user_batches.pop(k, None)
                self._user_rows.pop(k, None)
        wait = max(b.take(1), r.take(n), self._global.take(n))
        return wait

    def _one(self, uid: int, item: dict, now: float) -> dict:
        sym = item.get("asset")
        raw = item.get("rows")
        res = {"asset": sym if isinstance(sym, str) else None, "tf": None, "from": None, "to": None, "received": 0, "new": 0, "dup": 0, "conflict": 0, "rejected": 0,
               "reason": "", "done": True}
        if not isinstance(raw, list):
            res["reason"] = "no rows"
            return res
        raw = raw[-MAX_ROWS:]                                        # a longer history: the newest MAX_ROWS candles (the rest is not judged, it is just not taken)
        if not isinstance(sym, str) or not SYMBOL_RE.match(sym) or sym.endswith("_mkt"):
            res.update(received=len(raw), rejected=len(raw), reason="bad pair name")
            self._count(uid, now, batches=1, received=len(raw), rejected=len(raw))
            return res
        rows = rows_from_history(raw)
        bad_parse = len(raw) - len(rows)
        if len(rows) < 2:
            res.update(received=len(raw), rejected=len(raw), reason="fewer than 2 candles")
            self._count(uid, now, batches=1, received=len(raw), rejected=len(raw))
            return res
        known = sym in self.engine.instruments or self.db.execute("SELECT 1 FROM candles WHERE symbol=? LIMIT 1", (sym,)).fetchone()
        if not known:
            res.update(received=len(raw), rejected=len(raw), reason="unknown pair")
            self._count(uid, now, batches=1, received=len(raw), rejected=len(raw))
            return res
        tf = _infer_tf(rows)
        res["tf"] = tf
        if tf not in self.allowed_tfs:
            res.update(received=len(raw), rejected=len(raw), reason="unsupported timeframe")
            self._count(uid, now, batches=1, received=len(raw), rejected=len(raw))
            return res
        # the candle that is still forming is not part of a contribution (it is not final); everything else is judged
        closed = [r for r in rows if r["t"] + tf <= now - CLOSE_SLACK]
        received = len(closed) + bad_parse
        seen: dict[int, dict] = {}
        reasons: list[str] = ["unreadable candle"] * bad_parse
        for r in sorted(closed, key=lambda x: x["t"]):
            why = check_row(r, tf, now)
            if why:
                reasons.append(why)
            elif r["t"] not in seen:
                seen[r["t"]] = r
        ordered = [seen[t] for t in sorted(seen)]
        keep: list[dict] = []
        for i, r in enumerate(ordered):                                           # a jump between neighbours that no market makes
            prev = ordered[i - 1] if i else None
            if prev is not None and r["t"] - prev["t"] == tf and _rel(r["o"], prev["c"]) > JUMP_LIMIT:
                reasons.append("jump")
                continue
            keep.append(r)
        if keep and not self._level_ok(sym, tf, keep):
            res.update(received=received, rejected=received, reason="price level differs from the stored candles")
            self._count(uid, now, batches=1, received=received, rejected=received)
            return res
        stored = {}
        if keep:
            lo, hi = keep[0]["t"], keep[-1]["t"]
            stored = {t: (o, h, l, c, src) for t, o, h, l, c, src in self.db.execute(
                "SELECT ts, o, h, l, c, source FROM candles WHERE symbol=? AND tf=? AND ts BETWEEN ? AND ?", (sym, tf, lo, hi))}
            res["from"], res["to"] = lo, hi
        fresh, dup, conflict = [], 0, 0
        disputes = []
        for r in keep:
            old = stored.get(r["t"])
            if old is None:
                fresh.append(r)
            elif max(_rel(r["o"], old[0]), _rel(r["h"], old[1]), _rel(r["l"], old[2]), _rel(r["c"], old[3])) > CONFLICT_TOL:
                conflict += 1
                if len(disputes) < 5:
                    disputes.append((now, uid, sym, tf, r["t"], json.dumps(list(old[:4])), json.dumps([r["o"], r["h"], r["l"], r["c"]]), old[4]))
            else:
                dup += 1
        if fresh:
            self.db.executemany("INSERT OR IGNORE INTO candles (symbol, tf, ts, o, h, l, c, ticks, source) VALUES (?,?,?,?,?,?,?,?,?)",
                                [(sym, tf, r["t"], r["o"], r["h"], r["l"], r["c"], int(r.get("n") or 0), f"contrib:{uid}") for r in fresh])
            self._into_memory(sym, tf, fresh, uid, now)
        if disputes:
            self.db.executemany("INSERT INTO candle_disputes (ts, uid, symbol, tf, candle_ts, stored, sent, stored_source) VALUES (?,?,?,?,?,?,?,?)", disputes)
            self.db.execute("DELETE FROM candle_disputes WHERE id <= (SELECT MAX(id) FROM candle_disputes) - ?", (KEEP_DISPUTES,))
        rejected = received - len(keep)
        self._count(uid, now, batches=1, received=received, new=len(fresh), dup=dup, conflict=conflict, rejected=rejected)
        res.update(received=received, new=len(fresh), dup=dup, conflict=conflict, rejected=rejected, reason=reasons[0] if reasons else "")
        return res

    def _level_ok(self, sym: str, tf: int, rows: list[dict]) -> bool:
        """The median close of the batch must be near the nearest candle the server already has for that pair (when there is one within a week)."""
        mid = rows[len(rows) // 2]["t"]
        med = statistics.median(r["c"] for r in rows)
        before = self.db.execute("SELECT ts, c FROM candles WHERE symbol=? AND tf=? AND ts<=? ORDER BY ts DESC LIMIT 1", (sym, tf, mid)).fetchone()
        after = self.db.execute("SELECT ts, c FROM candles WHERE symbol=? AND tf=? AND ts>? ORDER BY ts LIMIT 1", (sym, tf, mid)).fetchone()
        near = min((x for x in (before, after) if x), key=lambda x: abs(x[0] - mid), default=None)
        if near is None or abs(near[0] - mid) > LEVEL_WINDOW:
            return True                                                          # nothing to compare with: new ground
        return _rel(med, near[1]) <= LEVEL_LIMIT

    def _into_memory(self, sym: str, tf: int, fresh: list[dict], uid: int, now: float) -> None:
        """New candles that fall inside what the engine holds in memory join it (a hole the feed missed is closed at once); the 5m / 15m candles that become complete are added."""
        series = self.engine.series.get((sym, tf))
        if series is not None and series.rows:
            first = min(series.rows)
            for r in fresh:
                if r["t"] >= first and r["t"] not in series.rows:
                    series.rows[r["t"]] = {k: r[k] for k in ("t", "o", "h", "l", "c", "n")}
        if tf in TFS:                                                            # timeframes that are only stored (not traded) build nothing above them
            self.engine._derive_higher(sym, tf, fresh[0]["t"], fresh[-1]["t"], now - CLOSE_SLACK, source=f"contrib:{uid}", only_missing=True)
            self.engine._flush(force=True)

    # ------------------------------------------------------------------ counters, trust
    def _count(self, uid: int, now: float, batches: int = 0, received: int = 0, new: int = 0, dup: int = 0, conflict: int = 0, rejected: int = 0) -> None:
        self.db.execute(
            "INSERT INTO candle_contrib (day, uid, batches, received, new, dup, conflict, rejected) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(day, uid) DO UPDATE SET batches=batches+excluded.batches, received=received+excluded.received, new=new+excluded.new, "
            "dup=dup+excluded.dup, conflict=conflict+excluded.conflict, rejected=rejected+excluded.rejected",
            (_day(now), uid, batches, received, new, dup, conflict, rejected))
        self.db.commit()

    def _judge(self, uid: int, now: float) -> None:
        since = _day(now - AUTO_WINDOW_DAYS * 86400)
        got, bad, batches = self.db.execute("SELECT COALESCE(SUM(received),0), COALESCE(SUM(conflict+rejected),0), COALESCE(SUM(batches),0) FROM candle_contrib "
                                            "WHERE uid=? AND day>=?", (uid, since)).fetchone()
        if got >= AUTO_MIN_ROWS and batches >= AUTO_MIN_BATCHES and bad / got > AUTO_BAD_SHARE and not self.blocked(uid):
            self.pause(uid, f"{bad} of {got} candles in {AUTO_WINDOW_DAYS} days were refused or disagreed with stored data", auto=True)
            self.engine._log("contrib-paused", f"account {uid}: {bad}/{got} candles refused or in conflict")

    # ------------------------------------------------------------------ what is wanted
    def wants(self, now: float | None = None, limit: int = 6) -> list[dict]:
        """Ranges worth contributing: recent holes of the pairs that are streaming (they stop signals), then the pairs with the least history. The extension shows them;
        the person opens the pair and scrolls back, and the page's own history answer is what gets contributed."""
        now = now or time.time()
        if now - self._want[0] < WANT_TTL:
            return self._want[1][:limit]
        out: list[dict] = []
        for sym, lt in list(self.engine.last_tick.items()):
            if now - lt["recv"] > 60 or len(out) >= 12:
                continue
            ts = [r[0] for r in self.db.execute("SELECT ts FROM candles WHERE symbol=? AND tf=60 AND ts>=? ORDER BY ts", (sym, int(now) - 86400))]
            holes = [(a, b) for a, b in zip(ts, ts[1:]) if b - a != 60]
            if holes:
                a, b = holes[-1]
                inst = self.engine.instruments.get(sym) or {}
                out.append({"asset": sym, "name": inst.get("name") or sym, "tf": 60, "kind": "gap", "from": a + 60, "to": b - 60, "missing": (b - a) // 60 - 1})
        if now - self._depth[0] >= DEPTH_TTL:
            plan = self.engine.collection_plan()
            depth = [{"asset": p["symbol"], "name": p["name"], "tf": 60, "kind": "depth", "from": p["scroll_to"] if p["scroll_to"] else int(now - 5000 * 60),
                      "to": (p["first"] - 60) if p["first"] else int(now), "missing": p["need"]}
                     for p in plan["pairs"] if p["open"] and not p["done"]][:12]
            self._depth = (now, depth)
        out.extend(self._depth[1])
        self._want = (now, out)
        return out[:limit]

    # ------------------------------------------------------------------ reports
    def mine(self, uid: int, now: float | None = None) -> dict:
        now = now or time.time()
        today = self.db.execute("SELECT batches, received, new, dup, conflict, rejected FROM candle_contrib WHERE day=? AND uid=?", (_day(now), uid)).fetchone()
        week = self.db.execute("SELECT COALESCE(SUM(received),0), COALESCE(SUM(new),0), COALESCE(SUM(dup),0), COALESCE(SUM(conflict),0), COALESCE(SUM(rejected),0) "
                               "FROM candle_contrib WHERE uid=? AND day>=?", (uid, _day(now - 6 * 86400))).fetchone()
        keys = ("batches", "received", "new", "dup", "conflict", "rejected")
        return {"enabled": self.enabled, "paused": self.blocked(uid), "today": dict(zip(keys, today or (0,) * 6)),
                "week": dict(zip(("received", "new", "dup", "conflict", "rejected"), week)),
                "stored": self.db.execute("SELECT COUNT(*) FROM candles WHERE source=?", (f"contrib:{uid}",)).fetchone()[0]}

    def overview(self, now: float | None = None) -> dict:
        now = now or time.time()
        since = _day(now - 6 * 86400)
        names = {i: n for i, n in self.db.execute("SELECT id, username FROM users")}
        users = []
        for uid, rec, new, dup, con, rej, batches in self.db.execute(
                "SELECT uid, SUM(received), SUM(new), SUM(dup), SUM(conflict), SUM(rejected), SUM(batches) FROM candle_contrib WHERE day>=? GROUP BY uid ORDER BY SUM(new) DESC", (since,)):
            users.append({"uid": uid, "username": names.get(uid, f"#{uid}"), "received": rec, "new": new, "dup": dup, "conflict": con, "rejected": rej, "batches": batches,
                          "stored": self.db.execute("SELECT COUNT(*) FROM candles WHERE source=?", (f"contrib:{uid}",)).fetchone()[0], "paused": self.blocked(uid)})
        total = self.db.execute("SELECT COUNT(*) FROM candles WHERE source LIKE 'contrib:%'").fetchone()[0]
        disputes = [{"id": i, "ts": t, "uid": u, "username": names.get(u, f"#{u}"), "symbol": s, "tf": tf, "candle_ts": ct, "stored": json.loads(st or "[]"), "sent": json.loads(se or "[]"),
                     "stored_source": ss} for i, t, u, s, tf, ct, st, se, ss in
                    self.db.execute("SELECT id, ts, uid, symbol, tf, candle_ts, stored, sent, stored_source FROM candle_disputes ORDER BY id DESC LIMIT 40")]
        return {"enabled": self.enabled, "stored_from_contributions": total, "users": users, "disputes": disputes, "wants": self.wants(now, 12),
                "limits": {"batches_per_minute": int(USER_BATCHES[0] * 60), "rows_per_hour": int(USER_ROWS[0] * 3600), "global_rows_per_second": int(GLOBAL_ROWS[0]),
                           "auto_pause_share": AUTO_BAD_SHARE, "auto_pause_min_rows": AUTO_MIN_ROWS}}


def _n_rows(item: dict) -> int:
    rows = item.get("rows")
    return len(rows) if isinstance(rows, list) else 0


def _infer_tf(rows: list[dict]) -> int | None:
    return infer_tf([r["t"] for r in rows])
