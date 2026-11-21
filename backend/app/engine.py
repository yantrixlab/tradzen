"""Engine: ingests the extension's feed, builds candles, issues signals on candle close, settles them, keeps stats.

Persistence (phase A): every closed candle, instrument, sentiment sample and feed event is stored in SQLite; every issued
signal gets a context snapshot (signal_features) and, when settled, a rich outcome row (signal_outcomes).
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd

from . import clinic, control, evaluator, momentum_ai, mtg
from . import features as feat_mod
from . import sentiment as sentiment_mod
from . import strategy
from .candles import TFS, Series, aggregate, bucket, infer_tf, rows_from_history
from .db import DEFAULT_STRATEGY_KEY, open_db, open_user_db
from .backtest import BacktestRunner
from .ai import Advisor
from .broker_trades import BrokerTrades
from .guard import Guard
from .style import NAME as STYLE_NAME, Style
from .risk import RiskBook
from .edgelab import EdgeLab
from .contrib import Contributions
from .leases import Leases
from .live_session import Sessions
from .research import ResearchRunner
from .sweep import SweepRunner
from .spec import validate_spec
from .stats_util import wilson
from .strategy_repo import StrategyRepo

MTG_TTL = 20.0                 # seconds between refreshes of the per-strategy martingale column
STREAM_TIMEOUT = 15.0          # seconds without a tick => pair is not streaming
SENTIMENT_EVERY = 20.0         # store at most one sentiment sample per pair per this many seconds (the raw value jumps around a lot; a denser
                               # series lets a study smooth it, and 'known at candle open' is then never more than ~20 s stale)
GAP_LIMIT = 20.0               # seconds without ticks => the candle(s) around it are incomplete and are discarded
MAX_SIGNALS_MEM = 10000
MUTE_WINDOW = 100              # a live strategy is auto-muted when its last N settled signals are clearly below break-even
MUTE_MIN = 60
MUTE_LONG = 500               # ... or when its last N settled signals are clearly below break-even: over 100 signals chance can hide a losing strategy (the 95 % range is
                              # ~20 points wide), over 500 it cannot - so a long record that is still below break-even mutes it, and it un-mutes by itself if the record recovers
FLUSH_EVERY = 1.0              # seconds between batched database commits
STORE_ONLY_TFS = (5, 10, 15, 30, 120, 180, 600, 1800, 3600, 7200, 14400, 86400)   # saved when the page sends them, never signalled on
RELOAD_CANDLES = 800          # per (symbol, tf) loaded back into memory at startup
NEAR_MISS_ATR = 0.25           # a loss that closed within this many ATR of entry counts as a near miss
SIGNAL_COLS = ["id", "created", "symbol", "tf", "basis_t", "target_t", "direction", "score", "strength", "agree", "factors",
               "reason", "entry", "status", "exit", "sentiment", "tier", "strategy_id", "strategy_version", "mode"]



class UserCtx:
    """What is private to one user: their own database and the trades / guard / style objects working on it."""

    def __init__(self, uid: int, db, trades, guard, style) -> None:
        self.uid, self.db, self.trades, self.guard, self.style = uid, db, trades, guard, style
        self.last_seen = time.time()


class Engine:
    def __init__(self, db_path: str | Path = ":memory:", seed_builtins: bool | None = None) -> None:
        """seed_builtins: install the built-in strategy library (paper mode). Default: yes for real databases, no for ':memory:' (tests)."""
        self.series: dict[tuple[str, int], Series] = {}
        self.open: dict[tuple[str, int], dict] = {}
        self.instruments: dict[str, dict] = {}
        self.sentiment: dict[str, dict] = {}
        self.last_tick: dict[str, dict] = {}
        self.selected: dict = {"symbol": None, "period": 60}
        self.user_sel: dict[int, dict] = {}          # account id -> the pair and timeframe open on that account's own Quotex tab ({symbol, period, at}); drives "My chart"
        self._pairs_cache: tuple[float, list[dict]] = (0.0, [])
        self.signals: list[dict] = []
        self.pending: dict[tuple[int, str, int, int], dict] = {}      # (strategy id, symbol, tf, target_t)
        self.ingest_stats = {"batches": 0, "ticks": 0, "last_recv": None}
        self.feed_events: list[dict] = []
        self.seq = 0                              # alert event sequence (signal issued / signal settled)
        self.notify_log: list[dict] = []
        self._atr: dict[int, float] = {}          # signal id -> ATR at signal time (for outcome margins)
        self._cbuf: list[tuple] = []              # write buffers, flushed in batches
        self._ebuf: list[tuple] = []
        self._sbuf: list[tuple] = []
        self._sent_at: dict[str, float] = {}
        self._last_flush = time.time()
        self.db_path = str(db_path)
        self._started = time.time()
        self.db = open_db(db_path)
        self.repo = StrategyRepo(self.db)
        self.backtests = BacktestRunner(self)
        self.sweeps = SweepRunner(self)
        self.research = ResearchRunner(self)
        self.ai = Advisor(self)
        self.risk = RiskBook(self)
        self.trades = BrokerTrades(self.db)          # the trades you place on the broker, seen through the extension (read-only)
        self.guard = Guard(self, self.trades)        # daily target / loss limit / streak / overtrading watch over the trades you place (alerts like the signal alerts)
        self.style = Style(self)                     # learns what your own trades have in common; its live rule signals the same situation (paper record first)
        # Everything above is the PRIMARY user's (the admin: the data that existed before accounts). Every other user gets a private database file and their own
        # trades / guard / style objects, created on first use (see user()).
        self.primary_uid = 1
        self._primary = UserCtx(1, self.db, self.trades, self.guard, self.style)
        self.users: dict[int, UserCtx] = {}
        self.users_dir: Path | None = None if str(db_path) == ":memory:" else Path(db_path).parent / "users"
        self._sty: list = []
        self._sty_at = 0.0
        self.edgelab = EdgeLab(self)
        self.feed_at: dict[str, float] = {}                 # symbol -> when the admin's price feed (feeder token) last sent a tick for it
        self.feeder_seen: dict[int, dict] = {}              # feeder account id -> {agent, first, last, ticks, batches, pairs}: who feeds the market (the admin's tab, or the server's feed worker)
        self.tick_ring: dict[str, deque] = {}               # symbol -> its last few applied ticks (ts, price): what other accounts' ticks are compared with
        self.leases = Leases(self, enabled=os.getenv("QXS_LIVE_FEED", "on").lower() not in ("off", "0", "false", "no"))   # live prices from ordinary accounts, one holder per pair
        self.contrib = Contributions(self, (*TFS, *STORE_ONLY_TFS), enabled=os.getenv("QXS_CONTRIB", "on").lower() not in ("off", "0", "false", "no"))   # shared candles: history contributed by every extension
        self._mtg: dict[int, dict] = {}              # strategy id -> one-step martingale summary of its real settled signals (refreshed in the background)
        self._mtg_at, self._mtg_ready, self._mtg_busy = 0.0, False, False
        self._sigmtg_cache: dict = {}                # (signal filter) -> (time, martingale outcomes of the matching settled signals): the Signal history page
        self.alerts_enabled: bool = bool(control.get_setting(self.db, "alerts_enabled", True))   # master switch for every alert
        self.repo.ensure_builtins(include_extra=(str(db_path) != ":memory:") if seed_builtins is None else seed_builtins)
        self.strategy_meta: dict[int, dict] = {}      # id -> {owner, audience, status}: used for who may see / receive a strategy (see strategy_access.py)
        self.strategies: dict[int, dict] = {}         # executable definitions of strategies in paper/live mode
        self.names: dict[int, str] = {}
        self.default_def: dict = {}
        self.strategy_id = 0
        self.strategy_version = 0
        self._alerted: set[int] = set()               # signal ids that produced an alert card (their result is announced too)
        self._mute_state: dict[int, bool] = {}
        self.eval_counts: dict[int, int] = {}
        self._fkey, self._fbase = None, None          # per-candle shared feature snapshot
        self.reload_strategies()
        self._load_instruments()
        self._load_candles()
        self._load_events()
        self._load()
        self.sessions = Sessions(self)               # live sessions: the admin sends signals by hand, users watch (live_session.py); settled by the candles below

    # ------------------------------------------------------------------ users: private data per user
    def set_primary(self, uid: int) -> None:
        """The user whose data is the main database's (the admin from the .env file)."""
        self.primary_uid = self._primary.uid = int(uid)
        self._sty_at = 0.0

    def user(self, uid: int) -> "UserCtx":
        """The private trades / guard / style of one user (opened on first use). Nothing is shared between users except the market."""
        uid = int(uid)
        if uid == self.primary_uid:
            self._primary.last_seen = time.time()
            return self._primary
        ctx = self.users.get(uid)
        if ctx is None:
            if self.users_dir is None:
                path: str | Path = ":memory:"
            else:
                self.users_dir.mkdir(parents=True, exist_ok=True)
                path = self.users_dir / f"{uid}.db"
            db = open_user_db(path)
            trades = BrokerTrades(db, shared=self.db, public_only=True)
            ctx = UserCtx(uid, db, trades, Guard(self, trades, db), Style(self, db, uid))
            self.users[uid] = ctx
            self._sty_at = 0.0
        ctx.last_seen = time.time()
        return ctx

    def purge_user_strategies(self, uid: int) -> None:
        """A deleted user's strategies stop running and go away (those that already have signals are archived), with their subscriptions and backtests."""
        for s in self.repo.list(include_archived=True):
            if s["owner_id"] == uid:
                try:
                    self.repo.delete(s["id"])
                except Exception:                                    # noqa: BLE001
                    pass
        self.db.execute("DELETE FROM strategy_subscriptions WHERE user_id=?", (uid,))
        for (rid,) in self.db.execute("SELECT id FROM backtest_runs WHERE owner_id=?", (uid,)).fetchall():
            self.backtests.delete_run(rid)
        self.db.commit()
        self.reload_strategies()

    def drop_user(self, uid: int) -> None:
        """Forget a deleted user: close and delete their private database."""
        self.leases.drop(int(uid))
        self.user_sel.pop(int(uid), None)
        self.sessions.mute(int(uid), True)                      # forget its "session alerts off" row
        self.db.execute("DELETE FROM user_prefs WHERE user_id=?", (int(uid),))
        self.db.commit()
        ctx = self.users.pop(int(uid), None)
        if ctx is not None:
            try:
                ctx.db.close()
            except Exception:                                        # noqa: BLE001
                pass
        if self.users_dir is not None:
            for suffix in ("", "-wal", "-shm"):
                try:
                    (self.users_dir / f"{int(uid)}.db{suffix}").unlink()
                except OSError:
                    pass
        self._sty_at = 0.0

    def users_gc(self, idle: float = 6 * 3600) -> None:
        """Close the private databases of users who have not been seen for a while (they reopen on the next request)."""
        now = time.time()
        for uid, ctx in list(self.users.items()):
            if now - ctx.last_seen > idle:
                try:
                    ctx.db.close()
                except Exception:                                    # noqa: BLE001
                    pass
                del self.users[uid]
        self._sty_at = 0.0

    def _active_styles(self) -> list:
        now = time.time()
        if now - self._sty_at > 10:
            self._sty = [c.style for c in [self._primary, *self.users.values()] if c is self._primary or now - c.last_seen < 7200]
            self._sty_at = now
        return self._sty

    # ------------------------------------------------------------------ strategies
    def reload_strategies(self) -> None:
        """Re-read strategy definitions from the database (call after any strategy change)."""
        self.strategies = {d["id"]: d for d in self.repo.definitions(only_active=True)}
        self.strategy_errors = dict(self.repo.last_errors)
        everything = self.repo.list(include_archived=True)
        self.names = {s["id"]: s["name"] for s in everything}
        self.strategy_meta = {s["id"]: {"owner": s["owner_id"], "audience": s["audience"], "status": s["status"]} for s in everything}       # who owns / receives each one
        self.default_def = self.repo.definition_by_key(DEFAULT_STRATEGY_KEY) or {}
        self.strategy_id = self.default_def.get("id", 0)
        self.strategy_version = self.default_def.get("version", 0)
        for sid in list(self._mute_state):
            if sid not in self.strategies:
                del self._mute_state[sid]

    def _in_scope(self, spec: dict, sym: str) -> bool:
        sc = spec.get("scope") or {}
        if sc.get("symbols") and sym not in sc["symbols"]:
            return False
        market = "OTC" if sym.endswith("_otc") else "LIVE"
        return not sc.get("market") or sc["market"] == market

    def is_muted(self, sid: int) -> bool:
        """Auto-mute: a live strategy stops alerting when the upper 95 % bound of its win rate is below break-even over its last MUTE_WINDOW settled signals,
        or over its last MUTE_LONG (the long window catches a strategy that is slowly but clearly losing). It keeps recording and settling signals while
        muted, so it un-mutes by itself the moment its record no longer shows that."""
        recent = [s for s in reversed(self.signals) if s["strategy_id"] == sid and s["status"] in ("win", "loss")][:MUTE_LONG]
        if len(recent) < MUTE_MIN:
            return False
        verdict = None
        for size in (MUTE_WINDOW, MUTE_LONG):
            part = recent[:size]
            if len(part) < (MUTE_MIN if size == MUTE_WINDOW else MUTE_LONG):
                continue
            payouts = [self.instruments[s["symbol"]]["payout"] for s in part if s["symbol"] in self.instruments and self.instruments[s["symbol"]].get("payout")]
            if not payouts:
                continue
            wins = sum(s["status"] == "win" for s in part)
            be = 1 / (1 + (sum(payouts) / len(payouts)) / 100)
            if wilson(wins, len(part))[1] < be:
                verdict = (len(part), wins)
                break
        muted = verdict is not None
        if self._mute_state.get(sid) != muted:
            self._mute_state[sid] = muted
            if muted:
                self._log("auto-mute", f"{self.names.get(sid, sid)}: last {verdict[0]} settled signals, {verdict[1]} wins - clearly below break-even")
            else:
                self._log("auto-unmute", f"{self.names.get(sid, sid)}: its recent record no longer shows it below break-even")
        return muted

    def _load_instruments(self) -> None:
        for sym, name, market, cat, payout, is_open in self.db.execute("SELECT symbol, name, market, category, payout, open FROM instruments"):
            self.instruments[sym] = {"symbol": sym, "name": name, "category": cat, "payout": payout, "open": bool(is_open), "market": market}

    def _load_candles(self) -> None:
        for sym, tf in self.db.execute("SELECT DISTINCT symbol, tf FROM candles").fetchall():
            cur = self.db.execute("SELECT ts, o, h, l, c, ticks FROM candles WHERE symbol=? AND tf=? ORDER BY ts DESC LIMIT ?",
                                  (sym, tf, RELOAD_CANDLES))
            for ts, o, h, l, c, n in cur.fetchall():
                self._series(sym, tf).put({"t": ts, "o": o, "h": h, "l": l, "c": c, "n": n or 0})

    def _load_events(self) -> None:
        rows = self.db.execute("SELECT ts, kind, detail FROM events ORDER BY id DESC LIMIT 100").fetchall()
        self.feed_events = [{"t": t, "e": k, "d": d} for t, k, d in reversed(rows)]

    def _load(self) -> None:
        cols = ", ".join(SIGNAL_COLS)
        cur = self.db.execute(f"SELECT {cols} FROM signals ORDER BY id DESC LIMIT ?", (MAX_SIGNALS_MEM,))
        for row in reversed(cur.fetchall()):
            s = dict(zip(SIGNAL_COLS, row))
            s["factors"] = json.loads(s["factors"] or "[]")
            if not s["tier"] and s["direction"] != "SKIP":
                s["tier"] = "A"          # signals recorded before tiers existed used the strict rule
            if s["status"] == "pending":
                s["status"] = "void"          # a restart loses the in-flight candle
                self.db.execute("UPDATE signals SET status='void' WHERE id=?", (s["id"],))
            self.signals.append(s)
        self.db.commit()

    # ------------------------------------------------------------------ write buffers
    def _flush(self, force: bool = False) -> None:
        if not force and time.time() - self._last_flush < FLUSH_EVERY:
            return
        self._last_flush = time.time()
        if self._cbuf:
            self.db.executemany(
                "INSERT INTO candles (symbol, tf, ts, o, h, l, c, ticks, source) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(symbol, tf, ts) DO UPDATE SET o=excluded.o, h=excluded.h, l=excluded.l, c=excluded.c, "
                "ticks=excluded.ticks, source=excluded.source WHERE excluded.ticks >= COALESCE(candles.ticks, 0) OR candles.source LIKE 'contrib:%'", self._cbuf)   # the price feed's own candle replaces a contributed one
            self._cbuf.clear()
        if self._ebuf:
            self.db.executemany("INSERT INTO events (ts, kind, detail) VALUES (?,?,?)", self._ebuf)
            self._ebuf.clear()
        if self._sbuf:
            self.db.executemany("INSERT OR REPLACE INTO sentiment (symbol, ts, pct) VALUES (?,?,?)", self._sbuf)
            self._sbuf.clear()
        self.db.commit()

    def close(self) -> None:
        self._flush(force=True)
        self.db.close()

    def _queue_candle(self, sym: str, tf: int, row: dict, source: str) -> None:
        self._cbuf.append((sym, tf, int(row["t"]), row["o"], row["h"], row["l"], row["c"], int(row.get("n", 0)), source))

    def _log(self, kind: str, detail=None, when: float | None = None) -> None:
        ts = when or time.time()
        self.feed_events.append({"t": ts, "e": kind, "d": detail})
        del self.feed_events[:-300]
        self._ebuf.append((ts, kind, None if detail is None else str(detail)))

    # ------------------------------------------------------------------ signal persistence
    def _save_signal(self, s: dict) -> None:
        cur = self.db.execute(
            "INSERT INTO signals (created, symbol, tf, basis_t, target_t, direction, score, strength, agree, factors, reason,"
            " entry, status, exit, sentiment, tier, strategy_id, strategy_version, mode, expiry_candles)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (s["created"], s["symbol"], s["tf"], s["basis_t"], s["target_t"], s["direction"], s["score"], s["strength"],
             s["agree"], json.dumps(s["factors"]), s["reason"], s["entry"], s["status"], s["exit"], s["sentiment"], s["tier"],
             s["strategy_id"], s["strategy_version"], s["mode"], 1))
        s["id"] = cur.lastrowid
        self.db.commit()

    def _update_signal(self, s: dict) -> None:
        self.db.execute("UPDATE signals SET status=?, exit=?, entry=? WHERE id=?", (s["status"], s["exit"], s["entry"], s["id"]))
        self.db.commit()

    def _save_features(self, signal_id: int, f: dict) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO signal_features (signal_id, features, hour_utc, weekday, session, adx, atr_ratio, rsi, bb_pctb, streak, trend_align)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (signal_id, json.dumps(f), f.get("hour_utc"), f.get("weekday"), f.get("session"), f.get("adx14"), f.get("atr_ratio"),
             f.get("rsi14"), f.get("bb_pctb"), f.get("streak"), f.get("trend_align")))
        self.db.commit()

    def _save_outcome(self, sig: dict, row: dict) -> None:
        atr = self._atr.pop(sig["id"], None)
        d = 1 if sig["direction"] == "CALL" else -1
        margin = mfe = mae = None
        if atr:
            margin = (row["c"] - row["o"]) * d / atr
            mfe = ((row["h"] - row["o"]) if d > 0 else (row["o"] - row["l"])) / atr
            mae = ((row["o"] - row["l"]) if d > 0 else (row["h"] - row["o"])) / atr
        near = int(sig["status"] == "loss" and margin is not None and margin > -NEAR_MISS_ATR)
        self.db.execute(
            "INSERT OR REPLACE INTO signal_outcomes (signal_id, settled_at, entry, exit, margin_atr, mfe_atr, mae_atr, near_miss, result, candle_ticks)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sig["id"], time.time(), row["o"], row["c"], margin, mfe, mae, near, sig["status"], row.get("n")))
        self.db.commit()

    # ------------------------------------------------------------------ alert events (pushed to the extension)
    @staticmethod
    def _brief(st: dict) -> dict:
        return {"n": st["settled"], "wins": st["wins"], "losses": st["losses"], "acc": st["accuracy"],
                "lo": st["ci_low"], "hi": st["ci_high"]}

    def _aud(self, sid: int) -> dict:
        """Who an alert of this strategy is for: its owner only (own strategy), or the audience of the shared strategy."""
        m = self.strategy_meta.get(sid) or {"owner": None, "audience": "admin"}
        return {"aud": "owner", "to": m["owner"]} if m["owner"] is not None else {"aud": m["audience"]}

    def _emit(self, kind: str, sig: dict, also: list[dict] | None = None) -> dict:
        inst = self.instruments.get(sig["symbol"], {})
        payout = inst.get("payout")
        lt = self.last_tick.get(sig["symbol"])
        active = sorted((f for f in sig["factors"] if abs(f["vote"]) >= 0.3), key=lambda f: -abs(f["vote"]) * f["weight"])
        self.seq += 1
        ev = {"seq": self.seq, "kind": kind, **self._aud(sig["strategy_id"]), "t": time.time(), "id": sig["id"], "symbol": sig["symbol"],
              "name": inst.get("name", sig["symbol"]), "market": inst.get("market", ""), "tf": sig["tf"],
              "direction": sig["direction"], "tier": sig["tier"], "strength": sig["strength"], "score": sig["score"],
              "agree": sig["agree"], "status": sig["status"], "entry": sig["entry"], "exit": sig["exit"],
              "basis_t": sig["basis_t"], "target_t": sig["target_t"], "expire_t": sig["target_t"] + sig["tf"],
              "payout": payout, "breakeven": 1 / (1 + payout / 100) if payout else None,
              "price": lt["price"] if lt else None, "now": lt["ts"] if lt else None, "sentiment": sig["sentiment"],
              "factors": [{"name": f["name"], "vote": f["vote"], "note": f["note"]} for f in active[:5]],
              "strategy": self.names.get(sig["strategy_id"], "?"), "strategy_id": sig["strategy_id"], "also": also or [],
              "tier_stats": self._brief(self.stats(tier=sig["tier"], strategy_id=sig["strategy_id"])),
              "pair_stats": self._brief(self.stats(sig["symbol"], sig["tf"], strategy_id=sig["strategy_id"]))}
        self.notify_log.append(ev)
        del self.notify_log[:-200]
        return ev

    @staticmethod
    def _style_stats(r: dict) -> dict:
        return {"n": r["n"], "wins": r["wins"], "losses": r["losses"], "acc": r["acc"], "lo": r["lo"], "hi": r["hi"]}

    def emit_style_signal(self, s: dict) -> dict:
        """A signal of the rule learned from the user's own trades, delivered like any signal alert (same card, same sound)."""
        inst = self.instruments.get(s["symbol"], {})
        sign = 1 if s["direction"] == "CALL" else -1
        self.seq += 1
        ev = {"seq": self.seq, "kind": "signal", "to": s.get("uid", self.primary_uid), "t": time.time(), "id": f"st{s['id']}", "symbol": s["symbol"], "name": inst.get("name", s["symbol"]), "market": inst.get("market", ""), "tf": 60,
              "direction": s["direction"], "tier": "B", "strength": s["conviction"], "score": sign * s["conviction"], "agree": 1, "status": "pending", "entry": s["entry"], "exit": None,
              "basis_t": s["cand"] - 60, "target_t": s["cand"], "expire_t": s["cand"] + 60, "payout": s["payout"], "breakeven": 1 / (1 + s["payout"] / 100), "price": s["entry"], "now": s["ts"],
              "sentiment": None, "factors": [{"name": s["rule"]["name"], "vote": float(sign), "note": f"price moved {s['inc']:+.2f} ATR in {s['off']:.0f} s - {s['rule']['text']}"}],
              "strategy": STYLE_NAME, "strategy_id": -1, "also": [], "tier_stats": self._style_stats(s["stats"]), "pair_stats": self._style_stats(s["pair_stats"])}
        self.notify_log.append(ev)
        del self.notify_log[:-200]
        return ev

    def emit_style_result(self, sid: int, sym: str, direction: str, entry: float, exit_p: float, status: str, payout: float | None, style=None) -> dict:
        inst = self.instruments.get(sym, {})
        style = style or self.style
        rec, pair = style.record(), style.record(sym)
        self.seq += 1
        ev = {"seq": self.seq, "kind": "result", "to": style.uid, "t": time.time(), "id": f"st{sid}", "symbol": sym, "name": inst.get("name", sym), "tf": 60, "direction": direction, "tier": "B", "status": status,
              "entry": entry, "exit": exit_p, "payout": payout, "breakeven": 1 / (1 + payout / 100) if payout else None, "strategy": STYLE_NAME,
              "tier_stats": self._style_stats(rec), "pair_stats": self._style_stats(pair)}
        self.notify_log.append(ev)
        del self.notify_log[:-200]
        return ev

    def _emit_guard(self, g: dict, uid: int | None = None) -> dict:
        """A trading-guard alert, delivered like a signal alert (the extension shows it as a card with sound). Private: only its owner gets it."""
        self.seq += 1
        ev = {"seq": self.seq, "kind": "guard", "to": self.primary_uid if uid is None else uid, "t": time.time(), "id": f"g{self.seq}", "name": "Trading guard", **g}
        self.notify_log.append(ev)
        del self.notify_log[:-200]
        self._log("guard", f"{g['level']}: {g['title']}")
        return ev

    def events_since(self, after: int | None, uid: int | None = None) -> list[dict]:
        """Alerts newer than the caller's cursor. With `uid`: the shared signal alerts plus the private ones (guard, learned style) addressed to that user."""
        if after is None or after > self.seq:          # first contact, or the backend restarted: no replay
            return []
        return [e for e in self.notify_log if e["seq"] > after and (uid is None or e.get("to") in (None, uid))][-20:]

    # ------------------------------------------------------------------ ingest
    def ingest(self, payload: dict, uid: int | None = None, feeder: bool = True) -> dict:
        """One batch from an extension. The price feed (ticks, history, instruments, sentiment) is accepted only from the feeder; every sender's own orders ("o")
        and balances ("b") go to that sender's private data (`uid`; default: the primary user). Alerts come back filtered for the sender."""
        uid = self.primary_uid if uid is None else int(uid)
        self.ingest_stats["batches"] += 1
        if feeder:
            self.ingest_stats["last_recv"] = time.time()
        n_ticks = 0
        got_order = False
        fed: set[str] = set()
        for item in payload.get("items", []):
            k = item.get("k")
            if not feeder and k not in ("o", "b", "sel"):
                continue
            if k == "t":
                now_f = time.time()
                for sym, ts, price, *rest in item.get("d", []):
                    self.on_tick(sym, float(ts), float(price))
                    self.feed_at[sym] = now_f                # the price feed has this pair: nobody else holds it
                    fed.add(sym)
                    n_ticks += 1
            elif k == "h":
                self.seed_history(item.get("asset"), rows_from_history(item.get("rows", [])))
            elif k == "i":
                self._on_instruments(item.get("list", []))
            elif k == "s":
                now = time.time()
                for sym, pct in item.get("d", []):
                    self.sentiment[sym] = {"pct": pct, "t": now}
                    if now - self._sent_at.get(sym, 0) >= SENTIMENT_EVERY:
                        self._sent_at[sym] = now
                        self._sbuf.append((sym, int(now), pct))
            elif k == "sel":
                if item.get("asset"):
                    try:
                        per = int(item.get("period") or 60)
                    except (TypeError, ValueError):
                        per = 60
                    self.user_sel[uid] = {"symbol": str(item["asset"])[:40], "period": per if per in TFS else 60, "at": time.time()}
                    if feeder:
                        self.selected = {"symbol": item["asset"], "period": int(item.get("period") or 60)}
            elif k == "ev":
                self._log(item.get("e", "?"), item.get("d"), item.get("ts"))
            elif k == "o":
                self.user(uid).trades.on_item(item)
                got_order = True
            elif k == "b":
                self.user(uid).trades.on_balance(item)
        self.ingest_stats["ticks"] += n_ticks
        if feeder:
            rec = self.feeder_seen.setdefault(uid, {"agent": None, "first": time.time(), "last": 0.0, "ticks": 0, "batches": 0, "pairs": {}})
            now_s = time.time()
            rec.update(last=now_s, agent=str(payload.get("agent") or "extension")[:40], batches=rec["batches"] + 1, ticks=rec["ticks"] + n_ticks)
            for sym in fed:
                rec["pairs"][sym] = now_s
        if got_order:
            try:
                for g in self.user(uid).guard.check():
                    self._emit_guard(g, uid)
            except Exception as e:                           # noqa: BLE001 - the guard must never break the data feed
                self._log("guard-error", str(e))
        self._flush()
        lr = self.ingest_stats["last_recv"]
        return {"ok": True, "ticks": n_ticks, "seq": self.seq, "events": self.events_since(payload.get("after_seq"), uid), "feeder": feeder,
                "feed_age": (time.time() - lr) if lr else None, "now": time.time()}          # `now`: the server's clock, so the extension can show countdowns on it (a PC clock may be a few seconds off)

    def _on_instruments(self, rows: list) -> None:
        now = time.time()
        for sym, name, cat, payout, is_open in rows:
            market = "OTC" if sym.endswith("_otc") else "LIVE"
            self.instruments[sym] = {"symbol": sym, "name": name, "category": cat, "payout": payout,
                                     "open": bool(is_open), "market": market}
            self.db.execute(
                "INSERT INTO instruments (symbol, name, market, category, payout, open, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(symbol) DO UPDATE SET name=excluded.name, market=excluded.market, category=excluded.category, "
                "payout=excluded.payout, open=excluded.open, last_seen=excluded.last_seen",
                (sym, name, market, cat, payout, int(bool(is_open)), now, now))
        self.db.commit()

    # ------------------------------------------------------------------ ticks -> candles
    def on_tick(self, sym: str, ts: float, price: float, src: str = "tick") -> None:
        """One live price. `src` is where the candles built from it are tagged ("tick" = the price feed, "tick:<account id>" = a leased ordinary account)."""
        if ts > 1e11:
            ts /= 1000.0
        ring = self.tick_ring.get(sym)
        if ring is None:
            ring = self.tick_ring[sym] = deque(maxlen=12)
        ring.append((ts, price))
        prev = self.last_tick.get(sym)
        if prev and ts - prev["ts"] > GAP_LIMIT:
            # server-time gap vs wall-clock gap tells "delivery paused" (both large) from "ticks lost upstream" (wall small)
            self._log("tick-gap", f"{sym}: {ts - prev['ts']:.0f}s of ticks missing (arrived {time.time() - prev['recv']:.0f}s after the previous one)")
        self.last_tick[sym] = {"ts": ts, "price": price, "recv": time.time()}
        for tf in TFS:
            key = (sym, tf)
            b = bucket(ts, tf)
            cur = self.open.get(key)
            if cur is not None and b < cur["t"]:
                continue                                   # late tick
            if cur is None or b > cur["t"]:
                outage = cur is None or ts - cur["last"] > GAP_LIMIT
                if cur is not None:
                    if cur["t"] + tf - cur["last"] > GAP_LIMIT:
                        cur["partial"] = True              # we stopped seeing ticks before the candle ended
                    self._close(sym, tf, cur)
                # after an outage the first tick may be mid-candle => OHLC unreliable unless it is right at the start
                self.open[key] = {"t": b, "o": price, "h": price, "l": price, "c": price, "n": 1,
                                  "first": ts, "last": ts, "partial": bool(outage and ts - b > 3), "src": src}
            else:
                if ts - cur["last"] > GAP_LIMIT:
                    cur["partial"] = True                  # ticks went missing inside this candle
                cur["h"] = max(cur["h"], price)
                cur["l"] = min(cur["l"], price)
                cur["c"] = price
                cur["n"] += 1
                cur["last"] = ts
                if src != cur.get("src"):
                    cur["src"] = "tick" if "tick" in (src, cur.get("src")) else "tick:mixed"      # more than one source built this candle (a hand-over): the feed's tag wins
        for st in self._active_styles():
            if st.enabled_fast:
                st.on_tick(sym, ts, price)                 # (catches its own errors: the tick path must never break)

    def _series(self, sym: str, tf: int) -> Series:
        return self.series.setdefault((sym, tf), Series())

    def _close(self, sym: str, tf: int, row: dict) -> None:
        if row.get("partial"):
            return          # joined mid-candle / ticks went missing: OHLC unreliable (history, if any, will fill it)
        src = row.get("src", "tick")
        row = {k: row[k] for k in ("t", "o", "h", "l", "c", "n")}
        self._series(sym, tf).put(row)
        self._queue_candle(sym, tf, row, src)
        self._settle(sym, tf, row)
        self.sessions.on_close(sym, tf, row)
        self._run_strategies(sym, tf, row)
        if tf == 60:
            for st in self._active_styles():
                st.on_close(sym, row)

    # ------------------------------------------------------------------ history seeding
    def _derive_higher(self, sym: str, tf: int, t_from: int, t_to: int, ref: float, source: str = "derived", only_missing: bool = False) -> int:
        """Build the higher timeframes (5m, 15m) around the closed `tf` candles stored between t_from and t_to. They are built from what the DATABASE holds there, not from
        the in-memory tail: a scroll-back batch is months older than the tail, and its edge buckets need the neighbours already stored. only_missing: leave a higher candle
        that already exists untouched (a contribution never replaces anything). Returns how many were added."""
        higher = [dst for dst in TFS if dst > tf and dst % tf == 0]
        if not higher:
            return 0
        self._flush(force=True)
        lo, hi = t_from - max(higher), t_to + max(higher)
        base = [{"t": t, "o": o_, "h": h, "l": l, "c": c, "n": n or 0} for t, o_, h, l, c, n in self.db.execute(
            "SELECT ts, o, h, l, c, ticks FROM candles WHERE symbol=? AND tf=? AND ts BETWEEN ? AND ? ORDER BY ts", (sym, tf, lo, hi))]
        added = 0
        for dst in higher:
            have = {r[0] for r in self.db.execute("SELECT ts FROM candles WHERE symbol=? AND tf=? AND ts BETWEEN ? AND ?", (sym, dst, lo, hi))} if only_missing else set()
            for a in aggregate(base, tf, dst, ref):
                if a["t"] in have:
                    continue
                self._series(sym, dst).put(a)
                self._queue_candle(sym, dst, a, source)
                added += 1
        return added

    def seed_history(self, sym: str | None, rows: list[dict]) -> None:
        note = {"asset": sym, "rows": len(rows), "at": time.time(), "result": ""}
        log = self.ingest_stats.setdefault("hist", [])
        log.append(note)
        del log[:-8]
        if not sym or len(rows) < 2:
            note["result"] = "ignored: fewer than 2 rows"
            return
        tf = infer_tf([r["t"] for r in rows])
        note["tf"] = tf
        if tf not in TFS and tf not in STORE_ONLY_TFS:
            note["result"] = f"ignored: unsupported timeframe {tf}"
            return
        lasts = [r["last"] for r in rows if r.get("last")]
        ref = max(lasts + [max(r["t"] for r in rows) + 0.0, self.last_tick.get(sym, {}).get("ts", 0)])
        closed = sorted((r for r in rows if r["t"] + tf <= ref), key=lambda r: r["t"])
        forming = [r for r in rows if r["t"] + tf > ref]
        if closed:                                        # how much of this batch is news to the database (scroll-back batches overlap)
            self._flush(force=True)
            have = self.db.execute("SELECT COUNT(*) FROM candles WHERE symbol=? AND tf=? AND ts BETWEEN ? AND ?",
                                   (sym, tf, closed[0]["t"], closed[-1]["t"])).fetchone()[0]
            note["new"] = max(0, len({r["t"] for r in closed}) - have)
            note["from"], note["to"] = min(r["t"] for r in closed), max(r["t"] for r in closed)
        if tf in STORE_ONLY_TFS and tf not in TFS:         # kept for research/backtests; the live engine does not trade these
            for r in closed:
                self._queue_candle(sym, tf, {k: r[k] for k in ("t", "o", "h", "l", "c", "n")}, "history")
            note["result"] = "stored (not traded)"
            self._flush(force=True)
            return
        note["result"] = "seeded"
        for r in closed:
            row = {k: r[k] for k in ("t", "o", "h", "l", "c", "n")}
            self._series(sym, tf).put(row)
            self._queue_candle(sym, tf, row, "history")
        if closed:
            self._derive_higher(sym, tf, closed[0]["t"], closed[-1]["t"], ref)
        for r in forming:                                  # continue the forming candle with live ticks
            cur = self.open.get((sym, tf))
            seen = r.get("last") or float(r["t"])
            if cur is None:
                self.open[(sym, tf)] = {"t": r["t"], "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"], "n": r["n"],
                                        "first": float(r["t"]), "last": seen, "partial": False}
            elif cur["t"] == r["t"] and cur.get("partial") and cur["first"] - seen <= GAP_LIMIT:
                # history covers the start of the candle and joins our ticks without a hole
                cur.update(o=r["o"], h=max(cur["h"], r["h"]), l=min(cur["l"], r["l"]), partial=False, n=max(cur["n"], r["n"]))

    # ------------------------------------------------------------------ signals
    GAP_REASON = "recent data gap (bridged candles in the last 8) - signals paused"

    def _gap_guard(self, res: strategy.Result, syn: bool) -> strategy.Result:
        if syn and res.direction != "SKIP":
            return strategy.Result("SKIP", res.score, res.strength, res.agree, res.factors, self.GAP_REASON, "")
        return res

    def _evaluate_spec(self, spec: dict, sym: str, tf: int, cache: dict | None = None) -> tuple[strategy.Result, list[dict], bool]:
        rows, syn = self._series(sym, tf).bridged_tail(tf)
        ctx = {"payout": self.instruments.get(sym, {}).get("payout")}
        res = evaluator.evaluate(spec, evaluator.tail_for(spec, rows), cache if cache is not None else {}, ctx)
        return self._gap_guard(res, syn), rows, syn

    def _evaluate(self, sym: str, tf: int) -> tuple[strategy.Result, list[dict], bool]:
        """Analysis of the default strategy (used by the dashboard's 'current analysis')."""
        return self._evaluate_spec(self.default_def["spec"], sym, tf)

    def _features(self, sym: str, tf: int, rows: list[dict], sig: dict) -> dict:
        key = (sym, tf, sig["basis_t"])
        if self._fkey != key:                              # one indicator pass per candle, shared by all strategies that signal on it
            self._fkey, self._fbase = key, feat_mod.base_features(rows[-300:])
        prev = next((s for s in reversed(self.signals) if s["symbol"] == sym and s["direction"] != "SKIP"
                     and s["strategy_id"] == sig["strategy_id"] and s is not sig), None)
        inst = self.instruments.get(sym, {})
        ctx = {"symbol": sym, "tf": tf, "direction": sig["direction"], "tier": sig["tier"], "strength": sig["strength"],
               "score": sig["score"], "agree": sig["agree"], "market": inst.get("market"), "category": inst.get("category"),
               "payout": inst.get("payout"), "sentiment": sig["sentiment"], "strategy": self.names.get(sig["strategy_id"]),
               "minutes_since_last": round((sig["basis_t"] - prev["basis_t"]) / 60, 2) if prev else None,
               "concurrent": sum(1 for s in self.signals[-300:] if s["basis_t"] == sig["basis_t"] and s["direction"] != "SKIP"
                                 and s["symbol"] != sym and s["strategy_id"] == sig["strategy_id"]),
               "factor_votes": {f["key"]: f["vote"] for f in sig["factors"]}}
        return feat_mod.finalize(self._fbase, ctx)

    def _run_strategies(self, sym: str, tf: int, row: dict) -> None:
        """Evaluate every active strategy on the candle that just closed, record the signals, then raise grouped alerts."""
        defs = [d for d in self.strategies.values() if tf in d["spec"]["timeframes"] and self._in_scope(d["spec"], sym)]
        if not defs:
            return
        rows, syn = self._series(sym, tf).bridged_tail(tf)
        window = rows[-400:]
        ctx = {"payout": self.instruments.get(sym, {}).get("payout")}
        cache: dict = {}                                    # indicators computed once per candle, shared by all strategies
        results: dict[str, tuple[dict, strategy.Result]] = {}
        for d in defs:
            if d["spec"]["kind"] == "meta":
                continue
            self.eval_counts[d["id"]] = self.eval_counts.get(d["id"], 0) + 1
            try:
                if d["key"] == momentum_ai.AI_KEY:           # the AI builds its own rule from the recent momentum; no preset spec is evaluated
                    res = momentum_ai.auto_result(self, sym, tf, rows, syn, os.getenv("QXS_AI_MIN_GRADE", "C").upper())
                else:
                    res = self._gap_guard(evaluator.evaluate(d["spec"], evaluator.tail_for(d["spec"], rows, window), cache, ctx), syn)
            except Exception as e:                          # a broken strategy must never stop the others
                self._log("strategy-error", f"{d['key']} {sym} tf={tf}: {e}")
                continue
            results[d["key"]] = (d, res)
        plain = {k: (d["name"], r) for k, (d, r) in results.items()}
        for d in defs:
            if d["spec"]["kind"] == "meta" and len(window) >= 60:
                self.eval_counts[d["id"]] = self.eval_counts.get(d["id"], 0) + 1
                results[d["key"]] = (d, self._gap_guard(evaluator.consensus(d["spec"], plain), syn))
        issued = []
        for d, res in results.values():
            sig = self._record(d, res, sym, tf, row, rows)
            if sig and sig["direction"] != "SKIP":
                issued.append(sig)
        self._alert(issued)

    def _record(self, d: dict, res: strategy.Result, sym: str, tf: int, row: dict, rows: list[dict]) -> dict | None:
        if res.reason.startswith("warming up"):
            return None
        if res.direction == "SKIP" and d["id"] != self.strategy_id:
            return None                                     # skips are only recorded for the default strategy (volume)
        sent = self.sentiment.get(sym)
        sig = {"created": time.time(), "symbol": sym, "tf": tf, "basis_t": row["t"], "target_t": row["t"] + tf,
               "direction": res.direction, "score": res.score, "strength": res.strength, "agree": res.agree,
               "tier": res.tier, "factors": res.factors, "reason": res.reason, "entry": row["c"],
               "status": "pending" if res.direction != "SKIP" else "skip", "exit": None,
               "sentiment": sent["pct"] if sent else None,
               "strategy_id": d["id"], "strategy_version": d["version"], "mode": "live" if d["status"] == "live" else "paper"}
        self._save_signal(sig)
        self.signals.append(sig)
        del self.signals[:-MAX_SIGNALS_MEM]
        if res.direction != "SKIP":
            self.pending[(d["id"], sym, tf, sig["target_t"])] = sig
            try:
                f = self._features(sym, tf, rows, sig)
                self._save_features(sig["id"], f)
                self.risk.tag(sig, f)                       # RISKY tag when the context matches a loss pattern learned from the strategy's backtest trades
                if f.get("atr14"):
                    self._atr[sig["id"]] = f["atr14"]
            except Exception as e:                          # a feature bug must never block a live signal
                self._log("feature-error", f"{sym} tf={tf}: {e}")
        return sig

    def _alert(self, issued: list[dict]) -> None:
        """One alert per (pair, timeframe, direction): the strongest live, un-muted signal; the others are listed as 'also'."""
        if not self.alerts_enabled:
            return                                          # master switch off: signals are still recorded and settled
        live = [s for s in issued if s["mode"] == "live" and not self.is_muted(s["strategy_id"])]
        # Alerts are grouped within one audience only, so a strategy that is still being tested never shows up inside an alert that users get, and nobody's own strategy
        # is mixed with anyone else's. 'optin' strategies are alerted one by one (each user has added different ones).
        classes: dict[tuple, list[dict]] = {}
        for s in live:
            a = self._aud(s["strategy_id"])
            key = ("optin", s["strategy_id"]) if a["aud"] == "optin" else (a["aud"], a.get("to"))
            classes.setdefault(key, []).append(s)
        for members in classes.values():
            for direction in ("CALL", "PUT"):
                group = sorted((s for s in members if s["direction"] == direction), key=lambda s: -s["strength"])
                if not group:
                    continue
                primary, rest = group[0], group[1:]
                self._alerted.add(primary["id"])
                self._emit("signal", primary, also=[{"name": self.names.get(s["strategy_id"], "?"), "tier": s["tier"], "strength": s["strength"]} for s in rest])

    def _settle(self, sym: str, tf: int, row: dict) -> None:
        due = [k for k in self.pending if k[1] == sym and k[2] == tf and k[3] == row["t"]]
        for key in due:
            sig = self.pending.pop(key)
            sig["entry"], sig["exit"] = row["o"], row["c"]
            if row["c"] == row["o"]:
                sig["status"] = "draw"
            else:
                up = row["c"] > row["o"]
                sig["status"] = "win" if (sig["direction"] == "CALL") == up else "loss"
            self._update_signal(sig)
            self._save_outcome(sig, row)
            if sig["id"] in self._alerted:
                self._alerted.discard(sig["id"])
                self._emit("result", sig)
        for key in [k for k in self.pending if k[1] == sym and k[2] == tf and k[3] < row["t"]]:
            stale = self.pending.pop(key)
            stale["status"] = "void"
            self._update_signal(stale)
            self._alerted.discard(stale["id"])

    def _analysis_dict(self, res: strategy.Result, rows: list[dict], sym: str, tf: int, syn: bool, need: int) -> dict:
        last = rows[-1]["t"] if rows else None
        return {"symbol": sym, "tf": tf, "candles": len(rows), "need": need, "direction": res.direction,
                "score": res.score, "strength": res.strength, "agree": res.agree, "tier": res.tier, "factors": res.factors,
                "reason": res.reason, "basis_t": last, "bridged": syn}

    def analyze(self, sym: str, tf: int) -> dict:
        res, rows, syn = self._evaluate(sym, tf)
        last = self._series(sym, tf).tail(1)
        d = self._analysis_dict(res, rows, sym, tf, syn, self.default_def["spec"]["gates"]["min_candles"])
        d["basis_t"] = last[0]["t"] if last else None
        return d

    def analyze_spec(self, spec: dict, sym: str, tf: int) -> dict:
        """Evaluate any (validated) spec on the stored candles right now - used by the strategy builder's preview."""
        res, rows, syn = self._evaluate_spec(spec, sym, tf)
        last = self._series(sym, tf).tail(1)
        d = self._analysis_dict(res, rows, sym, tf, syn, spec["gates"]["min_candles"])
        d["basis_t"] = last[0]["t"] if last else None
        return d

    def clear(self) -> None:
        for table in ("signal_notes", "signal_outcomes", "signal_features", "signals"):
            self.db.execute(f"DELETE FROM {table}")
        self.db.commit()
        self.signals.clear()
        self.pending.clear()
        self._atr.clear()

    # ------------------------------------------------------------------ queries for the admin panel
    # what the signal table can be sorted by -> the SQL expression (a whitelist: the client never supplies SQL)
    SIGNAL_SORTS = {"candle": "s.target_t", "pair": "s.symbol", "tf": "s.tf", "strategy": "st.name COLLATE NOCASE", "signal": "s.direction", "tier": "s.tier",
                    "strength": "s.strength", "adx": "f.adx", "hour": "f.hour_utc", "mode": "s.mode", "result": "s.status", "margin": "o.margin_atr"}

    def query_signals(self, symbol: str | None = None, tf: int | None = None, tier: str | None = None, status: str | None = None,
                      direction: str | None = None, strategy_id: int | None = None, include_skips: bool = False,
                      limit: int = 100, offset: int = 0, sort: str | None = None, order: str = "desc", allowed: set[int] | None = None) -> dict:
        """allowed: only the signals of these strategies (a user sees their own and the ones they receive); None = everything."""
        where, args = [], []
        if allowed is not None:
            where.append(f"s.strategy_id IN ({','.join('?' * len(allowed)) or 'NULL'})")
            args.extend(sorted(allowed))
        if not include_skips:
            where.append("s.direction != 'SKIP'")
        pairs = [p.strip() for p in str(symbol).split(",") if p.strip()] if symbol else []          # one pair, or several separated by commas
        if pairs:
            where.append(f"s.symbol IN ({','.join('?' * len(pairs))})")
            args.extend(pairs)
        for col, val in (("tf", tf), ("tier", tier), ("status", status), ("direction", direction), ("strategy_id", strategy_id)):
            if val not in (None, ""):
                where.append(f"s.{col} = ?")
                args.append(val)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        total = self.db.execute(f"SELECT COUNT(*) FROM signals s {clause}", args).fetchone()[0]
        limit, offset = max(1, min(limit, 1000)), max(0, offset)
        base = (f"SELECT {', '.join('s.' + c for c in SIGNAL_COLS)}, f.hour_utc, f.session, f.adx, f.atr_ratio, f.rsi, f.trend_align, "
                f"o.margin_atr, o.near_miss FROM signals s LEFT JOIN signal_features f ON f.signal_id = s.id "
                f"LEFT JOIN signal_outcomes o ON o.signal_id = s.id LEFT JOIN strategies st ON st.id = s.strategy_id")
        keys = SIGNAL_COLS + ["hour_utc", "session", "adx", "atr_ratio", "rsi", "trend_align", "margin_atr", "near_miss"]
        mtg_info = self._signal_mtg(clause, args)                                  # every settled signal's martingale outcome + the summary over this filter
        if sort in ("mtg1", "mtg2"):
            # ordered by the martingale outcome of each signal: best first (won outright, rescued at M1, at M2 ... lost); signals without one (pending ...) always last
            ids = [r[0] for r in self.db.execute(f"SELECT s.id FROM signals s {clause}", args)]
            asc = str(order).lower() == "asc"
            ladder_key = "m1" if sort == "mtg1" else "m2"
            rank = {"win": 0, "mtg1": 1, "mtg2": 2, "draw": 3, "open": 4, "lost": 5}

            def key(i):
                info = mtg_info["rows"].get(i)
                oc = info[ladder_key]["outcome"] if info and not info[ladder_key]["skip"] else None
                r_ = rank.get(oc) if oc else None
                return (r_ is None, (r_ if asc else -r_) if r_ is not None else 0, -i)
            page = sorted(ids, key=key)[offset:offset + limit]
            fetched = {r[0]: r for r in self.db.execute(f"{base} WHERE s.id IN ({','.join('?' * len(page)) or 'NULL'})", page)}
            raw = [fetched[i] for i in page if i in fetched]
        else:
            col = self.SIGNAL_SORTS.get(sort or "")
            direction_sql = "ASC" if str(order).lower() == "asc" else "DESC"
            # the whole table is ordered (not just the visible page); rows without a value (pending margin, no ADX) always go last; ties keep newest first
            order_sql = f"({col} IS NULL), {col} {direction_sql}, s.id DESC" if col else "s.id DESC"
            raw = self.db.execute(f"{base} {clause} ORDER BY {order_sql} LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
        rows = []
        risk = self.risk.risk_for([r[0] for r in raw])
        for r in raw:
            d = dict(zip(keys, r))
            d["factors"] = json.loads(d["factors"] or "[]")
            info = mtg_info["rows"].get(d["id"])
            d["mtg1"], d["mtg2"] = (info["m1"], info["m2"]) if info else (None, None)
            d["risk"] = risk.get(d["id"])
            rows.append(d)
        return {"total": total, "rows": rows, "mtg": mtg_info["summary"]}

    @staticmethod
    def _mtg_group(items: list[dict]) -> dict:
        """Plain, one-step and two-step martingale accuracy (and net units) of a group of settled signals, from their per-signal ladder outcomes (see _signal_mtg)."""
        w = sum(1 for i in items if i["first"] == "win")
        l_ = sum(1 for i in items if i["first"] == "loss")
        out: dict = {"settled": len(items), "wins": w, "losses": l_, "flat_accuracy": w / (w + l_) if w + l_ else None}
        for key in ("m1", "m2"):
            taken = [i[key] for i in items if not i[key]["skip"]]
            won, rescued = sum(1 for m in taken if m["outcome"] == "win"), sum(1 for m in taken if m["outcome"].startswith("mtg"))
            lost = sum(1 for m in taken if m["outcome"] == "lost")
            decided = won + rescued + lost
            out[key] = {"accuracy": (won + rescued) / decided if decided else None, "net": round(sum(m["pl"] for m in taken if m["pl"] is not None), 2),
                        "rescued": rescued, "lost": lost, "taken": len(taken), "skipped": len(items) - len(taken)}
        return out

    def strategy_mtg_detail(self, sid: int) -> dict:
        """Martingale view of one strategy's real settled signals: overall, by timeframe and by tier (the strategy page)."""
        info = self._signal_mtg("WHERE s.direction != 'SKIP' AND s.strategy_id = ?", [sid])["rows"]
        meta = {r[0]: (r[1], r[2]) for r in self.db.execute("SELECT id, tf, tier FROM signals WHERE strategy_id = ? AND direction IN ('CALL','PUT')", (sid,))}
        rows = [{**v, "tf": meta[i][0], "tier": meta[i][1]} for i, v in info.items() if i in meta]
        return {"all": self._mtg_group(rows),
                "by_tf": {str(tf): self._mtg_group([r for r in rows if r["tf"] == tf]) for tf in TFS},
                "by_tier": {t: self._mtg_group([r for r in rows if r["tier"] == t]) for t in ("A", "B")}}

    def signal_pairs(self, strategy_id: int | None = None, allowed: set[int] | None = None) -> list[dict]:
        """Every pair a signal filter can pick: the pairs that have signals (with how many) and every instrument the feed has listed (0 signals so far).
        With a strategy: only the pairs that strategy has signalled, with its own counts."""
        if allowed is not None and strategy_id is None:
            counts = dict(self.db.execute(f"SELECT symbol, COUNT(*) FROM signals WHERE direction != 'SKIP' AND strategy_id IN ({','.join('?' * len(allowed)) or 'NULL'}) GROUP BY symbol",
                                          sorted(allowed)).fetchall())
            out = [{"symbol": s, "n": int(n), "payout": (self.instruments.get(s) or {}).get("payout")} for s, n in counts.items()]
            return sorted(out, key=lambda p: (-p["n"], p["symbol"]))
        if strategy_id is not None:
            counts = dict(self.db.execute("SELECT symbol, COUNT(*) FROM signals WHERE direction != 'SKIP' AND strategy_id = ? GROUP BY symbol", (strategy_id,)).fetchall())
            out = [{"symbol": s, "n": int(n), "payout": (self.instruments.get(s) or {}).get("payout")} for s, n in counts.items()]
            return sorted(out, key=lambda p: (-p["n"], p["symbol"]))
        counts = dict(self.db.execute("SELECT symbol, COUNT(*) FROM signals WHERE direction != 'SKIP' GROUP BY symbol").fetchall())
        out = [{"symbol": s, "n": int(counts.get(s, 0)), "payout": (self.instruments.get(s) or {}).get("payout")} for s in set(counts) | set(self.instruments)]
        return sorted(out, key=lambda p: (-p["n"], p["symbol"]))

    MTG_MULT = 2.2
    MTG_SIGNAL_CAP = 20000                # the newest this-many settled signals of a filter take part in the martingale figures

    def _signal_mtg(self, clause: str, args: list) -> dict:
        """Martingale outcome of every settled signal matching a filter, with one step and with two steps (stake x2.2 per step), plus the accuracy / net summary.
        Same rule as everywhere else (mtg.py): the re-entry is the candle after the losing one, a signal that fires while a ladder is running cannot be taken.
        Cached for a few seconds: the page asks again every few seconds."""
        key = (clause, tuple(args))
        hit = self._sigmtg_cache.get(key)
        now = time.time()
        if hit and now - hit[0] < 20:
            return hit[1]
        settled = (clause + " AND " if clause else "WHERE ") + "s.direction IN ('CALL','PUT') AND s.status IN ('win','loss','draw')"
        rows = self.db.execute(f"SELECT s.id, s.symbol, s.tf, s.basis_t, s.direction, s.status FROM signals s {settled} ORDER BY s.id DESC LIMIT {self.MTG_SIGNAL_CAP}", args).fetchall()
        empty = {"signals": 0, "taken": 0, "skipped": 0, "wins": 0, "rescued": 0, "lost": 0, "draws": 0, "open": 0, "accuracy": None, "net": 0.0, "flat_net": 0.0}
        if not rows:
            out = {"rows": {}, "summary": {"settled": 0, "flat_accuracy": None, "m1": {**empty, "steps": 1}, "m2": {**empty, "steps": 2}, "capped": False}}
        else:
            df = pd.DataFrame(rows, columns=["id", "symbol", "tf", "t", "direction", "result"]).set_index("id", drop=False)
            df["dir"] = np.where(df["direction"] == "CALL", 1, -1)
            df["payout"] = df["symbol"].map(lambda s: float((self.instruments.get(s) or {}).get("payout") or 85.0))
            df["profit"] = np.where(df["result"] == "win", df["payout"] / 100.0, np.where(df["result"] == "loss", -1.0, 0.0))
            self._flush(force=True)                                              # candles still in memory must be in the table the ladders read
            candles: dict = {}
            t1 = mtg.add_ladders(self.db, df, 1, self.MTG_MULT, candles)
            t2 = mtg.add_ladders(self.db, df, 2, self.MTG_MULT, candles)
            per = {}
            for i in df.index:
                per[int(i)] = {"first": df.at[i, "result"], "m1": {"outcome": t1.at[i, "mtg_outcome"], "skip": bool(t1.at[i, "mtg_skip"]), "pl": None if pd.isna(t1.at[i, "mtg_pl"]) else round(float(t1.at[i, "mtg_pl"]), 2)},
                               "m2": {"outcome": t2.at[i, "mtg_outcome"], "skip": bool(t2.at[i, "mtg_skip"]), "pl": None if pd.isna(t2.at[i, "mtg_pl"]) else round(float(t2.at[i, "mtg_pl"]), 2)}}
            w, l_ = int((df["result"] == "win").sum()), int((df["result"] == "loss").sum())
            out = {"rows": per, "summary": {"settled": int(len(df)), "flat_accuracy": w / (w + l_) if w + l_ else None, "flat_net": round(float(df["profit"].sum()), 2),
                                            "m1": mtg.table_summary(t1, 1, self.MTG_MULT), "m2": mtg.table_summary(t2, 2, self.MTG_MULT), "capped": len(rows) >= self.MTG_SIGNAL_CAP}}
        if len(self._sigmtg_cache) > 40:
            self._sigmtg_cache.clear()
        self._sigmtg_cache[key] = (now, out)
        return out

    def signal_detail(self, signal_id: int) -> dict | None:
        cols = ", ".join(SIGNAL_COLS)
        r = self.db.execute(f"SELECT {cols} FROM signals WHERE id=?", (signal_id,)).fetchone()
        if not r:
            return None
        sig = dict(zip(SIGNAL_COLS, r))
        sig["factors"] = json.loads(sig["factors"] or "[]")
        sig["risk"] = self.risk.risk_for([signal_id]).get(signal_id)
        f = self.db.execute("SELECT features FROM signal_features WHERE signal_id=?", (signal_id,)).fetchone()
        o = self.db.execute("SELECT settled_at, entry, exit, margin_atr, mfe_atr, mae_atr, near_miss, result, candle_ticks "
                            "FROM signal_outcomes WHERE signal_id=?", (signal_id,)).fetchone()
        notes = [{"id": i, "created": c, "tags": json.loads(t or "[]"), "note": n} for i, c, t, n in
                 self.db.execute("SELECT id, created, tags, note FROM signal_notes WHERE signal_id=? ORDER BY id", (signal_id,))]
        lo, hi = sig["basis_t"] - 40 * sig["tf"], sig["target_t"] + 6 * sig["tf"]
        candles = [{"t": t, "o": o_, "h": h, "l": l, "c": c, "n": n} for t, o_, h, l, c, n in self.db.execute(
            "SELECT ts, o, h, l, c, ticks FROM candles WHERE symbol=? AND tf=? AND ts BETWEEN ? AND ? ORDER BY ts",
            (sig["symbol"], sig["tf"], lo, hi))]
        return {"signal": sig, "features": json.loads(f[0]) if f else None,
                "outcome": dict(zip(["settled_at", "entry", "exit", "margin_atr", "mfe_atr", "mae_atr", "near_miss", "result", "candle_ticks"], o)) if o else None,
                "notes": notes, "candles": candles}

    def add_note(self, signal_id: int, tags: list[str], note: str) -> dict | None:
        if not self.db.execute("SELECT 1 FROM signals WHERE id=?", (signal_id,)).fetchone():
            return None
        clean = sorted({t.strip().lower()[:40] for t in tags if t and t.strip()})[:12]
        cur = self.db.execute("INSERT INTO signal_notes (signal_id, created, tags, note) VALUES (?,?,?,?)",
                              (signal_id, time.time(), json.dumps(clean), (note or "")[:2000]))
        self.db.commit()
        return {"id": cur.lastrowid, "signal_id": signal_id, "tags": clean, "note": (note or "")[:2000]}

    def delete_note(self, note_id: int) -> bool:
        cur = self.db.execute("DELETE FROM signal_notes WHERE id=?", (note_id,))
        self.db.commit()
        return cur.rowcount > 0

    def coverage(self) -> list[dict]:
        self._flush(force=True)
        out = []
        for sym, tf, n, t0, t1 in self.db.execute("SELECT symbol, tf, COUNT(*), MIN(ts), MAX(ts) FROM candles GROUP BY symbol, tf ORDER BY symbol, tf"):
            expected = (t1 - t0) // tf + 1
            out.append({"symbol": sym, "tf": tf, "candles": n, "first": t0, "last": t1, "expected": expected,
                        "missing": expected - n, "completeness": round(n / expected, 4) if expected else None})
        return out

    def sentiment_coverage(self, window: int = 600) -> dict:
        self._flush(force=True)
        return sentiment_mod.coverage(self.db, time.time(), self.instruments, window)

    def sentiment_study(self, tf: int = 60, tolerance: int = 30, min_payout: float | None = None) -> dict:
        self._flush(force=True)
        payouts = {s: i.get("payout") for s, i in self.instruments.items() if i.get("payout")}
        return sentiment_mod.study(self.db, tf, tolerance, payouts, min_payout)

    def collection_plan(self, min_payout: float = 80.0, target_total: int = 100_000, per_pair_target: int = 5_000, tf: int = 60) -> dict:
        """What to collect next. Strategy verdicts need thousands of trades on pairs that actually pay well, so progress is counted on pairs paying
        >= min_payout only; each such pair gets a concrete instruction (open it, scroll back to <time>). Counts are 1m candles by default."""
        self._flush(force=True)
        now = time.time()
        have = {s: (n, t0, t1) for s, n, t0, t1 in self.db.execute("SELECT symbol, COUNT(*), MIN(ts), MAX(ts) FROM candles WHERE tf=? GROUP BY symbol", (tf,))}
        pairs = []
        for sym in sorted(set(self.instruments) | set(have)):
            inst = self.instruments.get(sym) or {"symbol": sym, "name": sym, "payout": None, "open": True, "market": "OTC" if sym.endswith("_otc") else "LIVE"}
            payout = inst.get("payout")
            if payout is None or payout < min_payout:
                continue
            n, t0, t1 = have.get(sym, (0, None, None))
            lt = self.last_tick.get(sym)
            streaming = bool(lt and now - lt["recv"] < STREAM_TIMEOUT)
            need = max(0, per_pair_target - n)
            scroll_to = int(t1 - per_pair_target * tf) if (need and t1) else None          # back to here and the pair has its target
            pairs.append({"symbol": sym, "name": inst.get("name") or sym, "market": inst.get("market"), "payout": payout, "open": bool(inst.get("open", True)),
                          "streaming": streaming, "candles": n, "first": t0, "last": t1, "age_s": None if t1 is None else int(now - t1), "need": need,
                          "scroll_to": scroll_to, "done": need == 0})
        # tradable now first, unfinished before finished, and among those finish what you already started (long unbroken series beat many thin ones)
        pairs.sort(key=lambda p: (not p["open"], p["done"], -p["candles"], p["symbol"]))
        got = sum(p["candles"] for p in pairs)
        live_pairs = [p for p in pairs if p["streaming"] and p["open"]]
        live_rate = len(live_pairs) * (3600 // tf)                                            # candles per hour that live capture adds
        missing = max(0, target_total - got)
        return {"tf": tf, "min_payout": min_payout, "target_total": target_total, "per_pair_target": per_pair_target, "have_total": got,
                "progress": min(1.0, got / target_total) if target_total else 1.0, "pairs_total": len(pairs), "pairs_done": sum(p["done"] for p in pairs),
                "streaming_pairs": len(live_pairs), "live_rate_per_hour": live_rate,
                "eta_hours_live_only": round(missing / live_rate, 1) if live_rate and missing else (0.0 if not missing else None), "pairs": pairs}

    def gaps(self, symbol: str, tf: int, limit: int = 200) -> dict:
        """Holes in one stored series (newest first): after/before are the candle times either side, missing = candles absent."""
        self._flush(force=True)
        ts = [r[0] for r in self.db.execute("SELECT ts FROM candles WHERE symbol=? AND tf=? ORDER BY ts", (symbol, tf))]
        holes = [{"after": a, "before": b, "missing": (b - a) // tf - 1} for a, b in zip(ts, ts[1:]) if b - a != tf]
        return {"symbol": symbol, "tf": tf, "candles": len(ts), "gaps": len(holes), "missing": sum(h["missing"] for h in holes),
                "holes": holes[::-1][:limit]}

    def export_rows(self, symbol: str | None, tf: int | None, since: float | None, until: float | None):
        """Yield (symbol, tf, ts, o, h, l, c, ticks, source) rows for export, oldest first."""
        self._flush(force=True)
        q, args = "SELECT symbol, tf, ts, o, h, l, c, ticks, source FROM candles WHERE 1=1", []
        for col, op, val in (("symbol", "=", symbol), ("tf", "=", tf), ("ts", ">=", since), ("ts", "<=", until)):
            if val is not None:
                q += f" AND {col}{op}?"
                args.append(val)
        cur = self.db.execute(q + " ORDER BY symbol, tf, ts", args)
        while chunk := cur.fetchmany(5000):
            yield from chunk

    # ------------------------------------------------------------------ stats / state
    def stats(self, symbol: str | None = None, tf: int | None = None, tier: str | None = None, strategy_id: int | None = None,
              mode: str | None = None, allowed: set[int] | None = None) -> dict:
        sigs = [s for s in self.signals if (not symbol or s["symbol"] == symbol) and (not tf or s["tf"] == tf)
                and (strategy_id is None or s["strategy_id"] == strategy_id) and (mode is None or s["mode"] == mode)
                and (allowed is None or s["strategy_id"] in allowed)]
        issued = [s for s in sigs if s["direction"] != "SKIP" and (not tier or s.get("tier") == tier)]
        wins = sum(s["status"] == "win" for s in issued)
        losses = sum(s["status"] == "loss" for s in issued)
        settled = wins + losses
        acc = wins / settled if settled else None
        lo = hi = None
        if settled:                                           # Wilson 95 % interval
            z, p = 1.96, wins / settled
            den = 1 + z * z / settled
            centre = (p + z * z / (2 * settled)) / den
            half = z * math.sqrt(p * (1 - p) / settled + z * z / (4 * settled * settled)) / den
            lo, hi = centre - half, centre + half
        calls = sum(s["direction"] == "CALL" for s in issued)
        puts = sum(s["direction"] == "PUT" for s in issued)
        skips = 0 if tier else sum(s["direction"] == "SKIP" for s in sigs)
        expectancy = None                                    # average profit per 1 unit staked: win -> payout, loss -> -1
        if settled:
            pay = [self.instruments[s["symbol"]]["payout"] for s in issued if s["status"] in ("win", "loss")
                   and s["symbol"] in self.instruments and self.instruments[s["symbol"]].get("payout")]
            if pay:
                ap = sum(pay) / len(pay) / 100
                expectancy = (wins * ap - losses) / settled
        payouts = [self.instruments[s["symbol"]]["payout"] for s in issued if s["symbol"] in self.instruments]
        avg_payout = sum(payouts) / len(payouts) if payouts else None
        return {"total": len(issued), "calls": calls, "puts": puts, "skips": skips, "wins": wins, "losses": losses,
                "draws": sum(s["status"] == "draw" for s in issued), "pending": sum(s["status"] == "pending" for s in issued),
                "settled": settled, "accuracy": acc, "ci_low": lo, "ci_high": hi,
                "avg_strength": round(sum(s["strength"] for s in issued) / len(issued)) if issued else None,
                "breakeven": 1 / (1 + avg_payout / 100) if avg_payout else None, "expectancy": expectancy}

    def equity(self, strategy_id: int | None = None, mode: str | None = None, points: int = 400, allowed: set[int] | None = None) -> dict:
        """Cumulative profit (units of stake) of settled signals in settlement order."""
        sigs = sorted((s for s in self.signals if s["direction"] != "SKIP" and s["status"] in ("win", "loss", "draw")
                       and (strategy_id is None or s["strategy_id"] == strategy_id) and (mode is None or s["mode"] == mode)
                       and (allowed is None or s["strategy_id"] in allowed)),
                      key=lambda s: s["target_t"])
        cum, curve = 0.0, []
        for s in sigs:
            payout = (self.instruments.get(s["symbol"]) or {}).get("payout") or 85
            cum += payout / 100 if s["status"] == "win" else -1.0 if s["status"] == "loss" else 0.0
            curve.append([s["target_t"], round(cum, 4)])
        step = max(1, len(curve) // points)
        return {"n": len(curve), "profit": round(cum, 4), "points": curve[::step] + ([curve[-1]] if curve and (len(curve) - 1) % step else [])}

    def system_info(self) -> dict:
        import os
        import platform
        counts = {t: self.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("signals", "candles", "signal_features", "signal_outcomes", "signal_notes", "backtest_runs", "backtest_trades", "insights", "events", "sentiment")}
        size = os.path.getsize(self.db_path) if self.db_path != ":memory:" and os.path.exists(self.db_path) else 0
        wal = os.path.getsize(self.db_path + "-wal") if size and os.path.exists(self.db_path + "-wal") else 0
        return {"db_path": self.db_path, "db_bytes": size, "wal_bytes": wal, "schema_version": self.db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0],
                "counts": counts, "python": platform.python_version(), "started": getattr(self, "_started", None), "now": time.time(),
                "strategies_running": len(self.strategies), "strategy_errors": self.strategy_errors, "ingest": self.ingest_stats}

    # ------------------------------------------------------------------ strategy management (used by the API)
    def chart_rows(self, sym: str, tf: int, want: int = 600) -> tuple[list[dict], bool]:
        """Candles for a chart: the stored history merged with what is in memory, so ANY pair that has stored candles can be charted with indicators - not only
        the ones streaming right now. Same gap rule as live evaluation (holes of <= 3 candles are bridged, bigger ones cut the series)."""
        mem = self._series(sym, tf)
        if len(mem.rows) >= want:
            return mem.bridged_tail(tf)
        self._flush(force=True)
        s = Series()
        for ts, o, h, l, c, n in self.db.execute("SELECT ts, o, h, l, c, ticks FROM candles WHERE symbol=? AND tf=? ORDER BY ts DESC LIMIT ?", (sym, tf, want)).fetchall():
            s.put({"t": ts, "o": o, "h": h, "l": l, "c": c, "n": n or 0})
        for r in mem.rows.values():                                        # memory is newer: it wins where both have a candle
            s.put(r)
        return s.bridged_tail(tf)

    def chart_rows_all(self, sym: str, tf: int, want: int = 4000) -> list[dict]:
        """The newest `want` stored candles (merged with memory), in time order with small holes bridged and big ones left - for charts that draw indicators,
        zones and structure over the whole loaded history, one uninterrupted stretch at a time."""
        mem = self._series(sym, tf)
        if len(mem.rows) >= want:
            return mem.bridged_all(tf)
        self._flush(force=True)
        s = Series()
        for ts, o, h, l, c, n in self.db.execute("SELECT ts, o, h, l, c, ticks FROM candles WHERE symbol=? AND tf=? ORDER BY ts DESC LIMIT ?", (sym, tf, want)).fetchall():
            s.rows[ts] = {"t": ts, "o": o, "h": h, "l": l, "c": c, "n": n or 0}        # set directly: Series.put keeps only the newest MAX_ROWS (live memory), a chart wants them all
        for t, r in mem.rows.items():                                                  # memory is newer: it wins where both have a candle
            old = s.rows.get(t)
            if old is None or r.get("n", 0) >= old.get("n", 0):
                s.rows[t] = r
        return s.bridged_all(tf)

    def strategy_mtg(self) -> dict[int, dict]:
        """One-step martingale numbers per strategy, from the signals each has really issued and settled. Cheap to call (it is polled every couple of seconds):
        returns the last result and, when it is older than MTG_TTL, refreshes it in a background thread."""
        now = time.time()
        if now - self._mtg_at > MTG_TTL and not self._mtg_busy:
            self._mtg_at, self._mtg_busy = now, True
            payouts = {s: i.get("payout") for s, i in self.instruments.items() if i.get("payout")}
            if self.db_path == ":memory:":                              # tests: one connection only, so run inline
                self._mtg_run(payouts)
            else:
                threading.Thread(target=self._mtg_run, args=(payouts,), daemon=True, name="strategy-mtg").start()
        return self._mtg

    def _mtg_run(self, payouts: dict) -> None:
        conn, owned = self.backtests._connect()
        try:
            self._mtg = mtg.signals_summary(conn, payouts)
            self._mtg_ready = True
        except Exception as e:                                           # noqa: BLE001 - a side column must never take the page down
            self._log("mtg-summary-error", str(e)[:200])
        finally:
            self._mtg_busy = False
            if owned:
                conn.close()

    def _present(self, d: dict, viewer: dict | None) -> dict:
        """What a viewer gets of a strategy: a user's own strategy shows the key they typed, and only whoever can change a strategy sees its errors."""
        owner = d.get("owner_id")
        d["mine"] = viewer is not None and owner is not None and owner == viewer["id"]
        if owner is not None:
            d["key"] = self.repo.public_key(d["key"], owner)
        return d

    def strategy_summary(self, ids: set[int] | None = None, viewer: dict | None = None) -> list[dict]:
        out = []
        mtg_all = self.strategy_mtg()
        for s in self.repo.list():
            if ids is not None and s["id"] not in ids:
                continue
            st = self.stats(strategy_id=s["id"])
            live_ids = self.strategies.get(s["id"])
            last = next((x["created"] for x in reversed(self.signals) if x["strategy_id"] == s["id"] and x["direction"] != "SKIP"), None)
            out.append({**s, "stats": st, "muted": self.is_muted(s["id"]) if s["status"] == "live" else False,
                        "running": live_ids is not None, "evaluations": self.eval_counts.get(s["id"], 0), "last_signal": last,
                        "error": self.strategy_errors.get(s["key"]),
                        "mtg1": mtg_all.get(s["id"]), "mtg1_pending": not self._mtg_ready})
            self._present(out[-1], viewer)
        return out

    def strategy_detail(self, sid: int, viewer: dict | None = None) -> dict | None:
        d = self.repo.get(sid)
        if not d:
            return None
        d["stats"] = self.stats(strategy_id=sid)
        d["stats_by_tf"] = {str(tf): self.stats(tf=tf, strategy_id=sid) for tf in TFS}
        d["stats_by_tier"] = {t: self.stats(tier=t, strategy_id=sid) for t in ("A", "B")}
        d["mtg"] = self.strategy_mtg_detail(sid)
        d["muted"] = self.is_muted(sid) if d["status"] == "live" else False
        d["error"] = self.strategy_errors.get(d["key"])
        d["running"] = sid in self.strategies
        d["evaluations"] = self.eval_counts.get(sid, 0)
        return self._present(d, viewer)

    def create_strategy(self, key: str, name: str, description: str, spec: dict, status: str = "paper", owner_id: int | None = None) -> int:
        sid = self.repo.create(key, name, description, spec, status, owner_id)
        self.reload_strategies()
        return sid

    def new_strategy_version(self, sid: int, spec: dict, notes: str = "") -> int:
        v = self.repo.new_version(sid, spec, notes)
        self.reload_strategies()
        return v

    def set_strategy_status(self, sid: int, status: str) -> None:
        self.repo.set_status(sid, status)
        self.reload_strategies()

    def set_strategy_audience(self, sid: int, audience: str) -> None:
        self.repo.set_audience(sid, audience)
        self.reload_strategies()

    def clone_strategy(self, sid: int, key: str, name: str | None = None, owner_id: int | None = None) -> int:
        new = self.repo.clone(sid, key, name, owner_id)
        self.reload_strategies()
        return new

    def rename_strategy(self, sid: int, name: str | None, description: str | None) -> None:
        self.repo.update_meta(sid, name, description)
        self.reload_strategies()

    def delete_strategy(self, sid: int) -> str:
        res = self.repo.delete(sid)
        self.reload_strategies()
        return res

    @staticmethod
    def validate_strategy_spec(spec) -> dict:
        return validate_spec(spec)

    # ------------------------------------------------------------------ clinic (loss analysis)
    def clinic_analyze(self, source: str = "live", run_id: int | None = None, strategy_id: int | None = None, valid_fraction: float = 0.3,
                       min_n: int = clinic.MIN_N, q: float = 0.10, save: bool = False) -> dict:
        if source == "backtest":
            run = self.backtests.get_run(int(run_id)) if run_id is not None else None
            if not run or run["status"] != "done":
                raise ValueError("backtest run not found or not finished")
            trades, strategy_id = self.backtests.load_trades(run["id"]), run["strategy_id"]
        elif source == "live":
            trades = clinic.trades_from_signals(self, strategy_id)
        else:
            raise ValueError("source must be 'live' or 'backtest'")
        result = clinic.analyze(trades, valid_fraction, min_n, q)
        result.update(source=source, run_id=run_id, strategy_id=strategy_id)
        if save:
            self.db.execute("DELETE FROM insights WHERE status='new' AND source=? AND COALESCE(run_id,-1)=COALESCE(?,-1) AND COALESCE(strategy_id,-1)=COALESCE(?,-1)",
                            (source, run_id, strategy_id))
            for f in result["findings"]:
                if f["significant"]:
                    self.db.execute(
                        "INSERT INTO insights (created, source, run_id, strategy_id, dimension, bucket, n, acc, baseline, q_value, validation, suggestion, status)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (time.time(), source, run_id, strategy_id, f["dimension"], f["bucket"], f["n"], f["acc"], f["baseline"], f["q"],
                         json.dumps(f["validation"]), json.dumps(f["suggestion"]) if f["suggestion"] else None, "new"))
            self.db.commit()
        return result

    def clinic_try(self, strategy_id: int, suggestion: dict, config: dict) -> dict:
        d = self.repo.get(strategy_id)
        if not d:
            raise ValueError("strategy not found")
        base_spec = validate_spec(d["spec"])["spec"]
        cand_spec = clinic.candidate_spec(base_spec, suggestion["filter"])
        cfg = {k: config.get(k) for k in ("symbols", "tfs", "start", "end")}
        base = self.backtests.run_inline(base_spec, cfg)
        cand = self.backtests.run_inline(cand_spec, cfg)
        from .backtest import split_time
        split = split_time(base, float(config.get("valid_fraction", 0.3)))
        return {"candidate_spec": cand_spec, "split_t": split, "comparison": clinic.compare(base, cand, split), "suggestion": suggestion}

    def clinic_apply(self, strategy_id: int, suggestion: dict, key: str | None = None, name: str | None = None) -> int:
        """Create a NEW strategy (paper mode) = the original plus the suggested filter; the original is never modified."""
        d = self.repo.get(strategy_id)
        if not d:
            raise ValueError("strategy not found")
        cand = clinic.candidate_spec(validate_spec(d["spec"])["spec"], suggestion["filter"])
        base_key, n = key or f"{d['key']}-f", 1
        new_key = base_key
        while self.db.execute("SELECT 1 FROM strategies WHERE key=?", (new_key,)).fetchone():
            n += 1
            new_key = f"{base_key}{n}"
        sid = self.create_strategy(new_key, name or f"{d['name']} + filter", f"{d['name']} with filter: {suggestion.get('text', suggestion['filter']['type'])}", cand, "paper")
        self.db.execute("UPDATE strategy_versions SET notes=? WHERE strategy_id=? AND version=1",
                        (f"Created from clinic suggestion: {suggestion.get('text', '')}", sid))
        self.db.commit()
        return sid

    def list_insights(self, strategy_id: int | None = None, status: str | None = None, limit: int = 200) -> list[dict]:
        where, args = [], []
        if strategy_id is not None:
            where.append("strategy_id=?")
            args.append(strategy_id)
        if status:
            where.append("status=?")
            args.append(status)
        sql = ("SELECT id, created, source, run_id, strategy_id, dimension, bucket, n, acc, baseline, q_value, validation, suggestion, status FROM insights "
               + ("WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?")
        keys = ["id", "created", "source", "run_id", "strategy_id", "dimension", "bucket", "n", "acc", "baseline", "q", "validation", "suggestion", "status"]
        out = []
        for r in self.db.execute(sql, args + [limit]):
            d = dict(zip(keys, r))
            d["validation"] = json.loads(d["validation"]) if d["validation"] else None
            d["suggestion"] = json.loads(d["suggestion"]) if d["suggestion"] else None
            d["strategy"] = self.names.get(d["strategy_id"])
            out.append(d)
        return out

    def set_insight_status(self, insight_id: int, status: str) -> bool:
        if status not in ("new", "tested", "applied", "dismissed"):
            raise ValueError("status must be new, tested, applied or dismissed")
        cur = self.db.execute("UPDATE insights SET status=? WHERE id=?", (status, insight_id))
        self.db.commit()
        return cur.rowcount > 0

    def similar_signals(self, signal_id: int, k: int = 15) -> dict | None:
        return clinic.similar(self, signal_id, k)

    def feed_health(self, sel: dict | None, symbols: list[dict], now: float) -> dict:
        """Why are no signals coming? Plain-English reasons, worst first, each with what to do about it. Strategies only signal when the pair has an uninterrupted
        run of candles (indicators never span a data gap), so a feed that paused - almost always because the Quotex tab was hidden - means a wait."""
        reasons: list[dict] = []
        clock = lambda t: time.strftime("%H:%M", time.localtime(t))  # noqa: E731

        def add(level: str, code: str, title: str, detail: str, fix: str | None = None, **extra) -> None:
            reasons.append({"level": level, "code": code, "title": title, "detail": detail, "fix": fix, **extra})

        recv = self.ingest_stats.get("last_recv")
        age = None if not recv else now - recv
        vis = next((e for e in reversed(self.feed_events) if e["e"] == "visibility"), None)
        hidden = bool(vis and vis["d"] == "hidden")
        keep_visible = "Bring the Quotex tab to the front and keep that Chrome window visible - not minimised, not covered by another window."
        if recv is None:
            add("bad", "no_data", "No data from the Quotex extension yet", "The backend has not received any prices.",
                "Open quotex.com with the Tradzen extension enabled (and the backend running, see the README).")
        elif age > 10:
            add("bad", "silent", f"No prices for {int(age)} s",
                (f"The Quotex tab went hidden at {clock(vis['t'])}. A tab that is covered, minimised or in the background stops sending prices." if hidden
                 else "The extension is not sending prices: the Quotex tab may be closed, asleep or crashed, or the extension was reloaded."), keep_visible)
        if sel and sel.get("analysis"):
            an, gap = sel["analysis"], sel.get("gap")
            name = next((s["name"] for s in symbols if s["symbol"] == sel["symbol"]), sel["symbol"])
            have, need = int(an.get("candles") or 0), int(an.get("need") or 60)
            if have < need:
                more = need - have
                if gap:
                    add("warn", "gap", f"{name}: waiting for {more} more uninterrupted candles ({have}/{need})",
                        f"{gap['missing']} candle(s) are missing before {clock(gap['before'])}: prices stopped arriving then, usually because the Quotex tab was hidden. "
                        f"Indicators never span a gap, so every strategy waits until the pair has {need} candles in a row - about {more} minutes if the feed stays on.",
                        "Reload the Quotex page (F5) while the tab is visible: it re-sends about 200 candles of history and fills the hole at once. Then keep the window visible.",
                        have=have, need=need)
                else:
                    add("warn", "warming", f"{name}: warming up ({have}/{need} candles)",
                        "This pair has not collected enough candles yet; they arrive live, one per minute.",
                        "Reload the Quotex page (F5) to load its recent history straight away.", have=have, need=need)
            elif an.get("bridged"):
                add("info", "bridged", f"{name}: a short data gap was bridged", "Signals stay paused for 8 candles after a bridged gap so a patched candle is never traded.")
        elif recv is not None and not any(s["streaming"] for s in symbols):
            add("bad", "no_pair", "No pair is streaming", "Prices arrive, but not for any chart.", "Open a chart (for example EUR/USD OTC) on Quotex.")
        cold = [s for s in symbols if s["streaming"] and sel and s["symbol"] != sel["symbol"] and 0 < s.get("warm", {}).get(str(sel["tf"]), 0) < 60]
        if cold:
            add("info", "others_warming", f"{len(cold)} other streaming pair(s) are still warming up",
                ", ".join(f"{s['name']} {s['warm'][str(sel['tf'])]}/60" for s in cold[:4]) + (" ..." if len(cold) > 4 else ""))
        streaming = [s for s in symbols if s["streaming"]]
        if len(streaming) == 1 and recv is not None and age is not None and age <= 10:
            add("info", "one_pair", f"Only {streaming[0]['name']} is streaming",
                "Signals come only from pairs that are open on Quotex. Open more pairs there to add them (each needs 60 uninterrupted candles first).")
        if not self.alerts_enabled:
            add("info", "alerts_off", "Alerts are switched off", "Signals are still recorded and measured, but no alert is sent.")
        order = {"bad": 0, "warn": 1, "info": 2}
        reasons.sort(key=lambda r: order[r["level"]])
        return {"paused": any(r["level"] in ("bad", "warn") for r in reasons), "reasons": reasons}

    def pair_list(self) -> list[dict]:
        """The pairs a chart can show (symbol, name, market, payout, streaming), kept for a few seconds: every open panel asks for it."""
        now = time.time()
        if now - self._pairs_cache[0] < 4.0:
            return self._pairs_cache[1]
        out = []
        for sym in sorted(set(self.instruments) | set(self.last_tick)):
            inst = self.instruments.get(sym, {"symbol": sym, "name": sym, "category": "", "payout": None, "open": True, "market": "OTC" if sym.endswith("_otc") else "LIVE"})
            lt = self.last_tick.get(sym)
            out.append({"symbol": sym, "name": inst.get("name", sym), "market": inst.get("market"), "payout": inst.get("payout"), "category": inst.get("category", ""),
                        "streaming": bool(lt and now - lt["recv"] < STREAM_TIMEOUT), "last_price": lt["price"] if lt else None})
        self._pairs_cache = (now, out)
        return out

    def chart_of(self, sym: str | None, tfv: int) -> dict | None:
        """What a chart needs for one pair and timeframe: the last candles, the forming one, the last price (the shape of `selected` in mychart / state)."""
        if not (sym and (sym in self.instruments or sym in self.last_tick or sym in {k[0] for k in self.open})):
            return None
        lt = self.last_tick.get(sym)
        forming = self.open.get((sym, tfv))
        series = self._series(sym, tfv)
        return {"symbol": sym, "tf": tfv, "candles": series.tail(80), "offset": (lt["ts"] - lt["recv"]) if lt else 0.0, "last_price": lt["price"] if lt else None,
                "gap": series.last_gap(tfv), "forming": {k: forming[k] for k in ("t", "o", "h", "l", "c", "n")} if forming else None, "sentiment": self.sentiment.get(sym)}

    def mychart(self, uid: int, symbol: str | None = None, tf: int | None = None) -> dict:
        """The chart of one account: the pair open on ITS Quotex tab and its timeframe (it follows them when you switch), unless a pair is asked for by hand."""
        now = time.time()
        mine = self.user_sel.get(int(uid))
        sym = symbol or (mine["symbol"] if mine else None)
        tfv = tf if tf in TFS else (mine["period"] if mine and mine["period"] in TFS else 60)
        sel = self.chart_of(sym, tfv)
        return {"now": now, "selected": sel, "symbols": self.pair_list(), "following": not symbol,
                "mine": {"symbol": mine["symbol"], "tf": mine["period"], "age": now - mine["at"]} if mine else None}

    def state(self, symbol: str | None = None, tf: int | None = None) -> dict:
        now = time.time()
        symbols = []
        for sym in sorted(set(self.instruments) | set(self.last_tick)):
            inst = self.instruments.get(sym, {"symbol": sym, "name": sym, "category": "", "payout": None, "open": True,
                                              "market": "OTC" if sym.endswith("_otc") else "LIVE"})
            lt = self.last_tick.get(sym)
            symbols.append({**inst, "streaming": bool(lt and now - lt["recv"] < STREAM_TIMEOUT),
                            "last_price": lt["price"] if lt else None,
                            "warm": {str(t): len(self._series(sym, t).bridged_tail(t)[0]) for t in TFS} if lt else {}})
        streaming = [s["symbol"] for s in symbols if s["streaming"]]
        sym = symbol or (self.selected["symbol"] if self.selected["symbol"] in streaming else None) or (streaming[0] if streaming else None)
        tfv = tf or (self.selected["period"] if self.selected["period"] in TFS else 60)
        sel = None
        if sym:
            rows = self._series(sym, tfv).tail(80)
            forming = self.open.get((sym, tfv))
            last = next((s for s in reversed(self.signals) if s["symbol"] == sym and s["tf"] == tfv and s["strategy_id"] == self.strategy_id), None)
            lt = self.last_tick.get(sym)
            sel = {"symbol": sym, "tf": tfv, "candles": rows, "offset": (lt["ts"] - lt["recv"]) if lt else 0.0,
                   "last_price": lt["price"] if lt else None, "gap": self._series(sym, tfv).last_gap(tfv),
                   "forming": {k: forming[k] for k in ("t", "o", "h", "l", "c", "n")} if forming else None,
                   "analysis": self.analyze(sym, tfv), "last_signal": last, "sentiment": self.sentiment.get(sym),
                   "stats": self.stats(sym, tfv, strategy_id=self.strategy_id)}
        dflt = self.strategy_id
        shared = {sid for sid, m in self.strategy_meta.items() if m["owner"] is None}          # the staff dashboard shows the shared strategies, never a user's own
        strategies = self.strategy_summary(ids=shared)
        live_ids = {s["id"] for s in strategies if s["status"] == "live"}
        # the newest signals of the strategies that are LIVE right now: with many paper strategies the last 40 signals can hold none of them
        recent_live = [s for s in reversed(self.signals) if s["strategy_id"] in live_ids and s["direction"] != "SKIP"][:15]
        recent_all = [s for s in self.signals[-120:] if s["strategy_id"] in shared][-40:]
        lacking = [s for s in recent_all + recent_live if "risk" not in s and s["direction"] != "SKIP"]            # signals loaded from the database after a restart
        if lacking:
            found = self.risk.risk_for([s["id"] for s in lacking])
            for s in lacking:
                s["risk"] = found.get(s["id"])
        return {"now": now, "alerts_enabled": self.alerts_enabled, "ingest": self.ingest_stats, "symbols": symbols, "selected": sel, "stats": self.stats(strategy_id=dflt),
                "tiers": {"A": self.stats(tier="A", strategy_id=dflt), "B": self.stats(tier="B", strategy_id=dflt)},
                "strategies": strategies, "health": self.feed_health(sel, symbols, now),
                "recent": list(reversed(recent_all)), "recent_live": recent_live, "tfs": list(TFS), "feed_events": list(reversed(self.feed_events[-25:]))}
