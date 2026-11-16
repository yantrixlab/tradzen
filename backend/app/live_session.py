"""Live sessions: the admin starts a session, sends signals by hand (heads-up -> signal -> result) and everybody whose plan includes sessions watches it.

* One live session at a time. A session has a tier (free | premium): who may see its signals is decided on the server (see `view`, `visible`), never in the page.
* A signal is one trade idea: it starts as a heads-up (the candle and the area are shown, nothing to trade yet), becomes `pending` when the admin sends it, and is settled
  by the candle it is for: the entry candle's open against the close of its last candle (the same rule as the engine's own signals: CALL wins when it closes above its
  open). A candle that never arrives voids it. The payout is stored when the signal is sent.
* Alerts ride on the engine's event log (`engine.notify_log`, `seq`), so the extension gets them with its normal answer: `signal` and `result` events (the same cards as
  strategy signals) and `session` events (started / headsup / cancelled / ended). They carry `aud: "session"` and `tier_req`; `visible` decides who gets them.
"""
from __future__ import annotations

import math
import time

from .candles import TFS, bucket
from .stats_util import wilson

TIERS = ("free", "premium")
DIRECTIONS = ("CALL", "PUT")
OPEN = ("headsup", "pending")
STALE_GRACE = 120.0                  # seconds, plus four candles: after that a candle that never came voids the signal
AUTO_END = 12 * 3600                 # a session nobody ended
ENTRY_GRACE = 3.0                    # a signal may still be sent this long after its candle opened (clock differences)
MAX_TITLE, MAX_NOTE, MAX_EXPIRY = 80, 300, 3

SIG_COLS = "id, session_id, created, sent_at, symbol, tf, direction, entry_t, expiry_candles, area_lo, area_hi, area_label, note, tier, payout, status, entry, exit, settled_at"
SES_COLS = "id, title, tier, status, symbol, tf, follow_admin, started, ended, started_by, note"


class SessionError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def _row(cols: str, r) -> dict:
    return dict(zip([c.strip() for c in cols.split(",")], r))


def stats_of(rows: list[dict]) -> dict:
    """The record of a list of session signals, in the shape of `engine.stats` (so the same TrackRecord component shows it) plus `net` in units of the stake."""
    sent = [r for r in rows if r["status"] not in ("headsup", "cancelled")]
    wins = sum(r["status"] == "win" for r in sent)
    losses = sum(r["status"] == "loss" for r in sent)
    draws = sum(r["status"] == "draw" for r in sent)
    pend = sum(r["status"] == "pending" for r in sent)
    voids = sum(r["status"] == "void" for r in sent)
    n = wins + losses
    pays = [r["payout"] for r in sent if r["status"] in ("win", "loss") and r.get("payout")]
    ap = sum(pays) / len(pays) if pays else None
    net = sum((r["payout"] or 85.0) / 100 if r["status"] == "win" else -1.0 if r["status"] == "loss" else 0.0 for r in sent)
    lo, hi = wilson(wins, n) if n else (None, None)
    be = 1 / (1 + ap / 100) if ap else None
    return {"total": len(sent), "calls": sum(r["direction"] == "CALL" for r in sent), "puts": sum(r["direction"] == "PUT" for r in sent), "skips": 0, "wins": wins, "losses": losses,
            "draws": draws, "pending": pend, "voids": voids, "settled": wins + losses + draws, "accuracy": (wins / n) if n else None, "ci_low": lo, "ci_high": hi,
            "avg_strength": None, "breakeven": be, "expectancy": ((wins * (ap / 100) - losses) / n) if (n and ap) else None, "net": round(net, 4)}


class Sessions:
    def __init__(self, engine) -> None:
        self.e = engine
        self.muted: set[int] = {r[0] for r in self.db.execute("SELECT user_id FROM session_mutes")}
        self._pend: dict[int, dict] = {}                      # signal id -> its row, while it is `pending`
        for r in self.db.execute(f"SELECT {SIG_COLS} FROM session_signals WHERE status='pending'"):
            row = _row(SIG_COLS, r)
            self._pend[row["id"]] = row
        self.recover()

    @property
    def db(self):
        return self.e.db

    # ------------------------------------------------------------------ reading
    def current(self) -> dict | None:
        r = self.db.execute(f"SELECT {SES_COLS} FROM live_sessions WHERE status='live' ORDER BY id DESC LIMIT 1").fetchone()
        return _row(SES_COLS, r) if r else None

    def get(self, sid: int) -> dict | None:
        r = self.db.execute(f"SELECT {SES_COLS} FROM live_sessions WHERE id=?", (sid,)).fetchone()
        return _row(SES_COLS, r) if r else None

    def rows(self, sid: int) -> list[dict]:
        return [_row(SIG_COLS, r) for r in self.db.execute(f"SELECT {SIG_COLS} FROM session_signals WHERE session_id=? ORDER BY id", (sid,))]

    def _signal(self, sid: int, sig_id: int) -> dict:
        r = self.db.execute(f"SELECT {SIG_COLS} FROM session_signals WHERE id=? AND session_id=?", (sig_id, sid)).fetchone()
        if not r:
            raise SessionError(404, "no such signal in this session")
        return _row(SIG_COLS, r)

    def known(self, sym: str | None) -> bool:
        return bool(sym) and (sym in self.e.instruments or sym in self.e.last_tick)

    def pair(self, s: dict) -> tuple[str | None, int]:
        """The pair and timeframe of the session's chart: the admin's own Quotex tab while `follow_admin`, otherwise the one picked by hand."""
        if not s["follow_admin"] and s["symbol"]:
            return s["symbol"], s["tf"]
        mine = self.e.user_sel.get(s["started_by"])
        if mine:
            return mine["symbol"], mine["period"]
        sel = self.e.selected
        if sel.get("symbol"):
            return sel["symbol"], sel["period"] if sel["period"] in TFS else 60
        return s["symbol"], s["tf"]

    # ------------------------------------------------------------------ control (admin)
    def start(self, uid: int, title: str, tier: str = "free", symbol: str | None = None, tf: int | None = None) -> dict:
        if tier not in TIERS:
            raise SessionError(400, "tier must be free or premium")
        if self.current():
            raise SessionError(409, "a session is already live: end it first")
        if symbol and not self.known(symbol):
            raise SessionError(400, "unknown pair")
        title = (title or "").strip()[:MAX_TITLE] or "Live session"
        cur = self.db.execute("INSERT INTO live_sessions (title, tier, status, symbol, tf, follow_admin, started, started_by) VALUES (?,?,?,?,?,?,?,?)",
                              (title, tier, "live", symbol, tf if tf in TFS else 60, 0 if symbol else 1, time.time(), uid))
        self.db.commit()
        s = self.get(cur.lastrowid)
        self.e._log("session-start", f"{title} ({tier})")
        self._session_event(s, "started", "Live session started", f"{title}" + (" · premium" if tier == "premium" else ""), level="info", sound="sstart")
        return s

    def end(self, sid: int) -> dict:
        s = self.current()
        if not s or s["id"] != sid:
            raise SessionError(404, "this session is not live")
        now = time.time()
        for r in self.rows(sid):
            if r["status"] == "headsup":
                self._set(r["id"], status="cancelled")
        self.db.execute("UPDATE live_sessions SET status='ended', ended=? WHERE id=?", (now, sid))
        self.db.commit()
        s = self.get(sid)
        st = stats_of(self.rows(sid))
        msg = f"{s['title']}: {st['wins']} win · {st['losses']} loss" + (f" · {st['draws']} draw" if st["draws"] else "") + (f" · {st['pending']} still running" if st["pending"] else "")
        self.e._log("session-end", s["title"])
        self._session_event(s, "ended", "Live session ended", msg, level="info", sound="send")
        return s

    def set_pair(self, sid: int, symbol: str | None, tf: int | None = None) -> dict:
        s = self.current()
        if not s or s["id"] != sid:
            raise SessionError(404, "this session is not live")
        if symbol is None:                                    # back to following the admin's Quotex tab
            self.db.execute("UPDATE live_sessions SET follow_admin=1 WHERE id=?", (sid,))
        else:
            if not self.known(symbol):
                raise SessionError(400, "unknown pair")
            self.db.execute("UPDATE live_sessions SET follow_admin=0, symbol=?, tf=? WHERE id=?", (symbol, tf if tf in TFS else s["tf"], sid))
        self.db.commit()
        return self.get(sid)

    def create_signal(self, sid: int, direction: str, entry: str = "next", expiry: int = 1, area_lo: float | None = None, area_hi: float | None = None,
                      area_label: str = "", note: str = "", send: bool = False) -> dict:
        s = self.current()
        if not s or s["id"] != sid:
            raise SessionError(404, "this session is not live")
        direction = (direction or "").upper()
        if direction not in DIRECTIONS:
            raise SessionError(400, "direction must be CALL or PUT")
        sym, tf = self.pair(s)
        if not sym or not self.known(sym):
            raise SessionError(409, "no pair yet: open a chart on Quotex or pick a pair for the session")
        if entry not in ("next", "after"):
            raise SessionError(400, "entry must be next or after")
        expiry = int(expiry or 1)
        if not 1 <= expiry <= MAX_EXPIRY:
            raise SessionError(400, f"expiry must be 1 to {MAX_EXPIRY} candles")
        if (area_lo is None) != (area_hi is None):
            raise SessionError(400, "give both ends of the area, or neither")
        if area_lo is not None:
            area_lo, area_hi = float(area_lo), float(area_hi)
            if not (math.isfinite(area_lo) and math.isfinite(area_hi) and area_lo > 0 and area_hi > 0):
                raise SessionError(400, "the area must be positive prices")
            area_lo, area_hi = min(area_lo, area_hi), max(area_lo, area_hi)
        now = time.time()
        entry_t = bucket(now, tf) + tf * (1 if entry == "next" else 2)
        cur = self.db.execute("INSERT INTO session_signals (session_id, created, symbol, tf, direction, entry_t, expiry_candles, area_lo, area_hi, area_label, note, tier, status) "
                              "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'headsup')",
                              (sid, now, sym, tf, direction, entry_t, expiry, area_lo, area_hi, (area_label or "")[:60], (note or "")[:MAX_NOTE], s["tier"]))
        self.db.commit()
        if send:
            return self.send(sid, cur.lastrowid)
        sig = self._signal(sid, cur.lastrowid)
        self._headsup_event(s, sig)
        return sig

    def send(self, sid: int, sig_id: int) -> dict:
        s = self.current()
        if not s or s["id"] != sid:
            raise SessionError(404, "this session is not live")
        sig = self._signal(sid, sig_id)
        if sig["status"] != "headsup":
            raise SessionError(409, "this signal was already sent or cancelled")
        now = time.time()
        if now >= sig["entry_t"] + ENTRY_GRACE:
            raise SessionError(409, "its candle has already started: make a new signal")
        inst = self.e.instruments.get(sig["symbol"], {})
        lt = self.e.last_tick.get(sig["symbol"])
        self._set(sig_id, status="pending", sent_at=now, payout=inst.get("payout"), entry=lt["price"] if lt else None)
        sig = self._signal(sid, sig_id)
        self._pend[sig_id] = sig
        self._signal_event("signal", s, sig)
        self.e._log("session-signal", f"{sig['direction']} {sig['symbol']} {sig['entry_t']}")
        return sig

    def cancel(self, sid: int, sig_id: int) -> dict:
        s = self.current()
        if not s or s["id"] != sid:
            raise SessionError(404, "this session is not live")
        sig = self._signal(sid, sig_id)
        if sig["status"] not in OPEN:
            raise SessionError(409, "this signal is already settled or cancelled")
        if sig["status"] == "pending" and time.time() >= sig["entry_t"]:
            raise SessionError(409, "its candle has already started: it cannot be cancelled")
        was_sent = sig["status"] == "pending"
        self._set(sig_id, status="cancelled")
        self._pend.pop(sig_id, None)
        sig = self._signal(sid, sig_id)
        if was_sent:                                          # people were told to trade it: tell them not to
            self._cancel_event(s, sig)
        return sig

    def _set(self, sig_id: int, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE session_signals SET {cols} WHERE id=?", (*fields.values(), sig_id))
        self.db.commit()
        if sig_id in self._pend:
            self._pend[sig_id].update(fields)

    # ------------------------------------------------------------------ settling
    @staticmethod
    def _last_t(sig: dict) -> int:
        return sig["entry_t"] + (sig["expiry_candles"] - 1) * sig["tf"]

    def _finish(self, sig: dict, exit_p: float, emit: bool = True) -> None:
        entry = sig["entry"]
        if entry is None or exit_p is None:
            return self._void(sig)
        status = "draw" if exit_p == entry else "win" if (sig["direction"] == "CALL") == (exit_p > entry) else "loss"
        self._set(sig["id"], status=status, exit=exit_p, settled_at=time.time())
        done = self._pend.pop(sig["id"], None) or sig
        done.update(status=status, exit=exit_p)
        s = self.get(sig["session_id"])
        if emit and s:
            self._signal_event("result", s, {**done})

    def _void(self, sig: dict) -> None:
        self._set(sig["id"], status="void", settled_at=time.time())
        self._pend.pop(sig["id"], None)

    def on_close(self, sym: str, tf: int, row: dict) -> None:
        """Called by the engine for every closed candle: settles the pending signals that are waiting for it."""
        if not self._pend:
            return
        for sig in list(self._pend.values()):
            if sig["symbol"] != sym or sig["tf"] != tf:
                continue
            last = self._last_t(sig)
            if row["t"] == sig["entry_t"]:
                sig["entry"] = row["o"]
                self.db.execute("UPDATE session_signals SET entry=? WHERE id=?", (row["o"], sig["id"]))
                self.db.commit()
            if row["t"] == last:
                self._finish(sig, row["c"])
            elif row["t"] > last:
                self._void(sig)

    def _from_db(self, sig: dict) -> bool:
        """Settle from the stored candles (after a restart, or when the live close was missed). True when it could be decided."""
        last = self._last_t(sig)
        got = {t: (o, c) for t, o, c in self.db.execute("SELECT ts, o, c FROM candles WHERE symbol=? AND tf=? AND ts IN (?,?)", (sig["symbol"], sig["tf"], sig["entry_t"], last))}
        if sig["entry_t"] in got and last in got:
            sig["entry"] = got[sig["entry_t"]][0]
            self._finish(sig, got[last][1], emit=False)
            return True
        return False

    def recover(self) -> None:
        self.sweep(recovering=True)

    def sweep(self, now: float | None = None, recovering: bool = False) -> None:
        """Voids what can no longer be settled, ends a session nobody ended. Cheap: called about once a minute."""
        now = now or time.time()
        if recovering or any(now > self._last_t(s) + s["tf"] for s in self._pend.values()):
            self.e._flush(force=True)
        for sig in list(self._pend.values()):
            if now <= self._last_t(sig) + sig["tf"]:
                continue
            if not self._from_db(sig) and now > self._last_t(sig) + 5 * sig["tf"] + STALE_GRACE:
                self._void(sig)
        for r in self.db.execute(f"SELECT {SIG_COLS} FROM session_signals WHERE status='headsup'").fetchall():
            sig = _row(SIG_COLS, r)
            s = self.get(sig["session_id"])
            if not s or s["status"] != "live" or now > sig["entry_t"] + sig["tf"]:
                self._set(sig["id"], status="cancelled")      # a heads-up whose candle is gone, or of an ended session
        s = self.current()
        if s and now - s["started"] > AUTO_END:
            self.end(s["id"])

    # ------------------------------------------------------------------ alerts
    def _push(self, kind: str, s: dict, **fields) -> dict:
        e = self.e
        e.seq += 1
        ev = {"seq": e.seq, "kind": kind, "aud": "session", "session_id": s["id"], "tier_req": s["tier"], "t": time.time(), **fields}
        e.notify_log.append(ev)
        del e.notify_log[:-200]
        return ev

    @staticmethod
    def _brief(st: dict) -> dict:
        return {"n": st["wins"] + st["losses"], "wins": st["wins"], "losses": st["losses"], "acc": st["accuracy"], "lo": st["ci_low"], "hi": st["ci_high"]}

    def _name(self, sym: str) -> str:
        return self.e.instruments.get(sym, {}).get("name", sym)

    @staticmethod
    def _area_text(sig: dict) -> str:
        if sig.get("area_lo") is None:
            return ""
        lo, hi = sig["area_lo"], sig["area_hi"]
        f = (lambda v: f"{v:.2f}") if max(lo, hi) >= 100 else (lambda v: f"{v:.5f}")
        return f"area {f(lo)} – {f(hi)}" + (f" ({sig['area_label']})" if sig.get("area_label") else "")

    def _signal_event(self, kind: str, s: dict, sig: dict) -> dict:
        sym, tf = sig["symbol"], sig["tf"]
        inst = self.e.instruments.get(sym, {})
        lt = self.e.last_tick.get(sym)
        rows = self.rows(s["id"])
        sign = 1.0 if sig["direction"] == "CALL" else -1.0
        payout = sig.get("payout")
        area = {"lo": sig["area_lo"], "hi": sig["area_hi"], "label": sig["area_label"]} if sig.get("area_lo") is not None else None
        note = " · ".join(x for x in (self._area_text(sig), sig.get("note") or "") if x)
        return self._push(kind, s, id=f"s{sig['id']}", symbol=sym, name=inst.get("name", sym), market=inst.get("market", ""), tf=tf, direction=sig["direction"], tier="A",
                          strength=100, score=sign * 100, agree=1, status=sig["status"], entry=sig.get("entry"), exit=sig.get("exit"), basis_t=sig["entry_t"] - tf, target_t=sig["entry_t"],
                          entry_t=sig["entry_t"], expire_t=sig["entry_t"] + sig["expiry_candles"] * tf, expiry_candles=sig["expiry_candles"], payout=payout,
                          breakeven=1 / (1 + payout / 100) if payout else None, price=lt["price"] if lt else None, now=lt["ts"] if lt else None, sentiment=None,
                          factors=[{"name": "Live session", "vote": sign, "note": note}] if note else [{"name": "Live session", "vote": sign, "note": s["title"]}],
                          strategy=s["title"], strategy_id=None, also=[], tier_stats=self._brief(stats_of(rows)),
                          pair_stats=self._brief(stats_of([r for r in rows if r["symbol"] == sym])), area=area, note=sig.get("note") or "", session_title=s["title"], session_tier=s["tier"])

    def _session_event(self, s: dict, stage: str, title: str, message: str, level: str = "info", sound: str = "ginfo", **extra) -> dict:
        return self._push("session", s, id=f"ss{self.e.seq + 1}", stage=stage, level=level, title=title, message=message, chips=[], name="Live session", session_title=s["title"],
                          session_tier=s["tier"], sound=sound, **extra)

    def _headsup_event(self, s: dict, sig: dict) -> dict:
        d = "CALL ▲" if sig["direction"] == "CALL" else "PUT ▼"
        text = " · ".join(x for x in (f"{d} on {self._name(sig['symbol'])}", self._area_text(sig), sig.get("note") or "") if x)
        return self._session_event(s, "headsup", "Get ready", text, level="warn", sound="shead", entry_t=sig["entry_t"], direction=sig["direction"], symbol=sig["symbol"], tf=sig["tf"],
                                   signal_id=sig["id"])

    def _cancel_event(self, s: dict, sig: dict) -> dict:
        return self._session_event(s, "cancelled", "Signal cancelled", f"Do not take {sig['direction']} on {self._name(sig['symbol'])}.", level="danger", sound="gstop", entry_t=sig["entry_t"],
                                   symbol=sig["symbol"], signal_id=sig["id"])

    # ------------------------------------------------------------------ views
    def mute(self, uid: int, on: bool) -> None:
        if on:
            self.db.execute("DELETE FROM session_mutes WHERE user_id=?", (uid,))
            self.muted.discard(uid)
        else:
            self.db.execute("INSERT OR IGNORE INTO session_mutes (user_id) VALUES (?)", (uid,))
            self.muted.add(uid)
        self.db.commit()

    def visible(self, user: dict, ev: dict, can) -> bool:
        """Who gets a session alert: staff always; others need the Sessions menu, the premium feature for a premium session, and must not have muted session alerts."""
        if user.get("role") in ("admin", "editor"):
            return True
        if user["id"] in self.muted or not can(user, "menu.sessions"):
            return False
        return ev.get("tier_req", "free") == "free" or bool(can(user, "cap.sessions.premium"))

    def _public(self, s: dict, admin: bool) -> dict:
        out = {k: s[k] for k in ("id", "title", "tier", "status", "started", "ended")}
        if admin:
            out["follow_admin"] = bool(s["follow_admin"])
        return out

    def view(self, entitled: bool, admin: bool = False) -> dict:
        """What the live page polls: the session, the chart of its pair, its signals and its record. A premium session shows a non-entitled user only that it exists."""
        now = time.time()
        s = self.current()
        if not s:
            return {"now": now, "session": None}
        sym, tf = self.pair(s)
        locked = s["tier"] == "premium" and not (entitled or admin)
        rows = [] if locked else self.rows(s["id"])
        out = {"now": now, "session": self._public(s, admin), "locked": locked, "pair": {"symbol": sym, "tf": tf}, "selected": self.e.chart_of(sym, tf) if sym else None,
               "signals": rows, "stats": None if locked else stats_of(rows)}
        if admin:
            out["symbols"] = self.e.pair_list()
        return out

    def history(self, entitled: bool, admin: bool = False, limit: int = 50) -> list[dict]:
        out = []
        for r in self.db.execute(f"SELECT {SES_COLS} FROM live_sessions ORDER BY id DESC LIMIT ?", (max(1, min(200, limit)),)).fetchall():
            s = _row(SES_COLS, r)
            rows = self.rows(s["id"])
            locked = s["tier"] == "premium" and not (entitled or admin)
            out.append({**self._public(s, admin), "locked": locked, "signals": len([x for x in rows if x["status"] != "cancelled"]), "stats": None if locked else stats_of(rows)})
        return out

    def detail(self, sid: int, entitled: bool, admin: bool = False) -> dict:
        s = self.get(sid)
        if not s:
            raise SessionError(404, "no such session")
        locked = s["tier"] == "premium" and not (entitled or admin)
        rows = [] if locked else self.rows(sid)
        return {**self._public(s, admin), "locked": locked, "signals": rows, "stats": None if locked else stats_of(rows)}
