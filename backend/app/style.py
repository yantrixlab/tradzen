"""My style: what the trades you place have in common, how well each habit works, and a live rule that signals the same situation.

STEP 1  CONTEXT   For every first trade you placed (martingale steps and repeated clicks are not separate decisions) the market is described AS IT WAS at your entry, from
                  stored candles only: how far price had already moved inside the current candle (in ATR), how many seconds into the candle you entered, the colour of the last
                  closed candle, the 5-candle return, the EMA slope, RSI, Bollinger position, ADX, streak ...
STEP 2  HABITS    Each habit ('you bet against the candle's own move', 'you enter in the first 5 seconds', 'you trade AUD/CAD' ...) gets its win rate with a 95 % range that is
                  widened for the number of habits compared, and a verdict against the break-even rate: proven / promising / weak / against / too few trades.
STEP 3  THE RULE  A habit that is at least 'promising' and can be seen on a live tick becomes a live rule (today: fade the candle's move). Its numbers come from YOUR trades
                  (how far price must have moved, when in the candle you act) and it skips the pairs where your own record is below break-even.
STEP 4  THE RECORD  The rule's signals are saved and settled at the candle close (win = the close is on your side of the entry price, like your own trades), so the rule earns
                  its own measured record. Alerts stay off until that record is at or above break-even over 30+ signals (setting: earned | all | off); until then it is paper.

Said plainly: 30 trades cannot prove a style. Habits are shown with their uncertainty; the live record is the real test, and it keeps growing with every candle.
"""
from __future__ import annotations

import json
import math
import time

import numpy as np
import pandas as pd

from . import control, vec
from .broker_trades import BrokerTrades
from .stats_util import wilson

NAME = "MY STYLE (learned from your trades)"
MIN_FEW = 8                       # fewer trades than this: no verdict
PROMISING_N = 10
ALERT_AFTER = 30                  # settled signals the rule must have, at or above break-even, before it may alert (mode "earned")
MAX_ALERTS_PER_HOUR = 8
MTG_MULT = 2                      # stake multiplier of the one martingale step (the Trading guard's default)
FLAT = 0.08                       # |move so far| in ATR under which the candle counts as flat
DEFAULTS = {"enabled": True, "alert": "earned", "min_payout": 75.0}
FEATS = ("off", "inc", "last_col", "ret5", "ema", "rsi", "pctb", "adx", "atr_ratio", "streak", "last_body", "hour")


def _merge(stored: dict | None) -> dict:
    s = {**DEFAULTS, **(stored or {})}
    s["enabled"] = bool(s["enabled"])
    s["alert"] = "earned" if s["alert"] == "promising" else s["alert"]
    s["alert"] = s["alert"] if s["alert"] in ("off", "earned", "all") else "earned"
    s["min_payout"] = float(min(100, max(0, s["min_payout"])))
    return s


# ----------------------------------------------------------------------------------------------- the habits (direction-relative: d = +1 CALL, -1 PUT)
# (key, label, group, test(ctx, d))
TRAITS = [
    ("fade_move", "Against the candle's own move so far", "In the candle", lambda c, d: d * c["inc"] <= -FLAT),
    ("follow_move", "With the candle's own move so far", "In the candle", lambda c, d: d * c["inc"] >= FLAT),
    ("flat_move", "Candle still flat when you enter", "In the candle", lambda c, d: abs(c["inc"]) < FLAT),
    ("at_open", "Entry in the first 5 seconds of the candle", "Timing", lambda c, d: c["off"] <= 5),
    ("mid", "Entry 5-40 seconds into the candle", "Timing", lambda c, d: 5 < c["off"] < 40),
    ("late", "Entry in the last 20 seconds", "Timing", lambda c, d: c["off"] >= 40),
    ("with_last", "Same way as the last closed candle", "Recent candles", lambda c, d: d * c["last_col"] > 0),
    ("against_last", "Against the last closed candle", "Recent candles", lambda c, d: d * c["last_col"] < 0),
    ("with_ret5", "With the last 5 candles' move", "Recent candles", lambda c, d: d * c["ret5"] > 0),
    ("against_ret5", "Against the last 5 candles' move", "Recent candles", lambda c, d: d * c["ret5"] < 0),
    ("long_run_fade", "Against a run of 3+ same-colour candles", "Recent candles", lambda c, d: d * c["streak"] <= -3),
    ("with_ema", "With the EMA21 slope", "Trend", lambda c, d: d * c["ema"] > 0),
    ("against_ema", "Against the EMA21 slope", "Trend", lambda c, d: d * c["ema"] < 0),
    ("strong_trend", "Strong trend (ADX 30+)", "Trend", lambda c, d: c["adx"] >= 30),
    ("weak_trend", "Weak trend (ADX under 20)", "Trend", lambda c, d: c["adx"] < 20),
    ("rsi_fade", "Fading an RSI stretch", "Stretch", lambda c, d: (c["rsi"] >= 60 and d < 0) or (c["rsi"] <= 40 and d > 0)),
    ("rsi_follow", "Riding an RSI stretch", "Stretch", lambda c, d: (c["rsi"] >= 60 and d > 0) or (c["rsi"] <= 40 and d < 0)),
    ("bb_fade", "Fading a Bollinger band edge", "Stretch", lambda c, d: (c["pctb"] >= 0.85 and d < 0) or (c["pctb"] <= 0.15 and d > 0)),
    ("busy", "Busy market (ATR above its average)", "Market", lambda c, d: c["atr_ratio"] >= 1.15),
    ("quiet", "Quiet market (ATR below its average)", "Market", lambda c, d: c["atr_ratio"] <= 0.85),
]
M_TESTED = len(TRAITS) + 6          # habits compared (the traits plus about 6 pairs): the multiple-comparison correction
Z = 1.96 + 0.32 * math.log(M_TESTED)


def _p_better(wins: int, n: int, be: float) -> float:
    """One-sided p that the true win rate is above break-even (normal approximation)."""
    if n == 0:
        return 1.0
    z = (wins / n - be) / math.sqrt(be * (1 - be) / n)
    return 0.5 * math.erfc(z / math.sqrt(2))


def verdict(wins: int, n: int, be: float) -> str:
    if n < MIN_FEW:
        return "few"
    lo, _ = wilson(wins, n, Z)
    rate = wins / n
    if lo > be:
        return "proven"
    if rate > be and n >= PROMISING_N:
        return "promising"
    if rate < be - 0.03:
        return "against"
    return "weak"


def context_from(rows: list[dict], entry_t: float, entry_price: float, cand_open: float | None = None) -> dict | None:
    """The market state at an entry, from the CLOSED candles before the entry candle. `rows` = candles (t, o, h, l, c, n) up to and including the candle before the entry
    candle, oldest first; `cand_open` = the open of the candle the entry is in (when known)."""
    if len(rows) < 40:
        return None
    d = pd.DataFrame(rows[-400:])[["t", "o", "h", "l", "c", "n"]].rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"}).astype(float).reset_index(drop=True)
    f = vec.featurize(d)
    i = len(d) - 1
    atr = float(f.atr14.iloc[i])
    if not np.isfinite(atr) or atr <= 0:
        return None
    o = cand_open if cand_open is not None else float(d.close.iloc[i])             # a candle opens where the previous one closed
    g = lambda col: float(f[col].iloc[i]) if np.isfinite(f[col].iloc[i]) else 0.0  # noqa: E731
    return {"off": round(float(entry_t % 60), 1), "inc": round((entry_price - o) / atr, 4), "last_col": float(np.sign(d.close.iloc[i] - d.open.iloc[i])),
            "last_body": round(abs(d.close.iloc[i] - d.open.iloc[i]) / atr, 3), "ret5": float(np.sign(g("ret5_pct"))), "ema": float(np.sign(g("ema21_slope_pct"))),
            "rsi": round(g("rsi14"), 1), "pctb": round(g("bb_pctb"), 3), "adx": round(g("adx14"), 1), "atr_ratio": round(g("atr_ratio"), 3), "streak": g("streak"),
            "hour": int(pd.to_datetime(entry_t, unit="s").hour), "atr": atr}


class Style:
    def __init__(self, engine, db=None, uid: int = 1) -> None:
        """db = this user's own database (trades, learned signals); the candles always come from the shared main database."""
        self.engine = engine
        self.db = db if db is not None else engine.db
        self.shared = engine.db
        self.uid = uid
        self._s = _merge(control.get_setting(self.db, "style", {}))
        self.cache: dict[str, dict] = {}              # symbol -> state of the last closed candle (ATR, last colour, ...)
        self._rule: dict | None = None
        self._rule_at = 0.0
        self._fired: set[tuple[str, int]] = set()     # (symbol, candle) already signalled
        self._alert_times: list[float] = []
        self._err_at = 0.0
        self._alerted_ids: set[int] = set()           # style signals that were shown as alerts (their result is shown too)
        try:
            self._backfill_steps()
        except Exception as e:                                     # noqa: BLE001
            self._oops(e)

    def _backfill_steps(self) -> None:
        """Lost signals recorded before the one-step martingale existed: play the step from the stored candles (same side, from the losing candle's close, next candle)."""
        rows = self.db.execute("SELECT id, symbol, direction, expire_t, exit_price FROM style_signals WHERE status='loss' AND step_status IS NULL AND exit_price IS NOT NULL").fetchall()
        for sid, sym, d, exp, exit_p in rows:
            nxt = self.shared.execute("SELECT c FROM candles WHERE symbol=? AND tf=60 AND ts=?", (sym, exp)).fetchone()
            if nxt is None:
                newer = self.shared.execute("SELECT 1 FROM candles WHERE symbol=? AND tf=60 AND ts>?", (sym, exp)).fetchone()
                self.db.execute("UPDATE style_signals SET step_status=?, step_entry=?, step_expire_t=? WHERE id=?", ("void" if newer else "pending", exit_p, exp + 60, sid))
            else:
                self.db.execute("UPDATE style_signals SET step_status=?, step_entry=?, step_exit=?, step_expire_t=? WHERE id=?", (self._judge(d, exit_p, nxt[0]), exit_p, nxt[0], exp + 60, sid))
        if rows:
            self.db.commit()

    @staticmethod
    def _judge(direction: str, entry: float, exit_p: float) -> str:
        return "draw" if exit_p == entry else "win" if (exit_p > entry) == (direction == "CALL") else "loss"

    # ------------------------------------------------------------------ settings
    @property
    def enabled_fast(self) -> bool:
        return self._s["enabled"]

    def settings(self) -> dict:
        return dict(self._s)

    def save_settings(self, patch: dict) -> dict:
        self._s = _merge({**self._s, **{k: v for k, v in (patch or {}).items() if k in DEFAULTS}})
        control.set_setting(self.db, "style", self._s)
        self._rule_at = 0.0
        return self.settings()

    # ------------------------------------------------------------------ steps 1 + 2: the trades -> context -> habits
    def _first_trades(self) -> list[dict]:
        cols = ("id", "symbol", "direction", "amount", "open_t", "expire_t", "close_t", "open_price", "close_price", "payout", "profit", "status", "is_demo")
        rows = [dict(zip(cols, r)) for r in self.db.execute(f"SELECT {', '.join(cols)} FROM broker_trades WHERE hidden=0 AND COALESCE(is_demo,0)=0 ORDER BY open_t")]
        for r in rows:
            r["t"] = r["open_t"] or 0
        _, pos, _ = BrokerTrades._martingale_full(rows)
        return [p for p in pos if p["step"] == 0 and p["status"] in ("win", "loss")], pos, rows

    def _context_of(self, p: dict) -> dict | None:
        o = p["orders"][0]
        row = self.db.execute("SELECT ctx FROM trade_context WHERE trade_id=?", (o["id"],)).fetchone()
        if row:
            return json.loads(row[0]) if row[0] else None
        sym, cand = o["symbol"], int(p["open_t"] // 60 * 60)
        prev = self.shared.execute("SELECT ts, o, h, l, c, ticks FROM candles WHERE symbol=? AND tf=60 AND ts < ? ORDER BY ts DESC LIMIT 400", (sym, cand)).fetchall()[::-1]
        cur = self.shared.execute("SELECT o FROM candles WHERE symbol=? AND tf=60 AND ts=?", (sym, cand)).fetchone()
        ctx = None
        if prev and cur and prev[-1][0] == cand - 60 and o["open_price"] is not None:
            tail = [{"t": a, "o": b, "h": c_, "l": d_, "c": e, "n": n} for a, b, c_, d_, e, n in prev]
            run = [tail[-1]]                                       # the newest uninterrupted run
            for r in reversed(tail[:-1]):
                if run[0]["t"] - r["t"] != 60:
                    break
                run.insert(0, r)
            ctx = context_from(run, p["open_t"], o["open_price"], cur[0])
        if ctx is not None or p["status"] in ("win", "loss"):
            self.db.execute("INSERT OR REPLACE INTO trade_context (trade_id, ctx) VALUES (?,?)", (o["id"], json.dumps(ctx) if ctx else None))
            self.db.commit()
        return ctx

    def profile(self) -> dict:
        firsts, pos, rows = self._first_trades()
        pays = [r["payout"] for r in rows if r["payout"]]
        be = 1 / (1 + (sum(pays) / len(pays)) / 100) if pays else 0.54
        items = []
        for p in firsts:
            ctx = self._context_of(p)
            if ctx:
                items.append((p, ctx, 1 if p["dir"] == "CALL" else -1, p["status"] == "win"))
        n = len(items)
        wins = sum(1 for *_, w in items if w)
        lo, hi = wilson(wins, n, 1.96) if n else (None, None)
        traits = []
        for key, label, group, test in TRAITS:
            sel = [(c, d, w) for _, c, d, w in items if test(c, d)]
            k, wn = len(sel), sum(1 for *_, w in sel if w)
            tl, th = wilson(wn, k, Z) if k else (None, None)
            traits.append({"key": key, "label": label, "group": group, "n": k, "wins": wn, "rate": wn / k if k else None, "lo": tl, "hi": th, "share": k / n if n else 0,
                           "verdict": verdict(wn, k, be), "p": _p_better(wn, k, be), "p_corrected": min(1.0, _p_better(wn, k, be) * M_TESTED)})
        pairs = []
        by: dict[str, list[bool]] = {}
        for p, _c, _d, w in items:
            by.setdefault(p["symbol"], []).append(w)
        for sym, ws in sorted(by.items(), key=lambda kv: -len(kv[1])):
            pl, ph = wilson(sum(ws), len(ws), Z)
            pairs.append({"symbol": sym, "n": len(ws), "wins": sum(ws), "rate": sum(ws) / len(ws), "lo": pl, "hi": ph, "verdict": verdict(sum(ws), len(ws), be)})
        offs = [c["off"] for _, c, _, _ in items]
        steps = [q for q in pos if q["step"] == 1 and q["status"] in ("win", "loss")]
        prof = {"n": n, "wins": wins, "rate": wins / n if n else None, "lo": lo, "hi": hi, "be": be, "p": _p_better(wins, n, be) if n else 1.0, "positions": len(pos), "orders": len(rows),
                "steps": {"n": len(steps), "wins": sum(1 for q in steps if q["status"] == "win")}, "need": 100,
                "timing": {"median": float(np.median(offs)) if offs else None, "at_open": sum(1 for x in offs if x <= 5) / n if n else 0, "late": sum(1 for x in offs if x >= 40) / n if n else 0},
                "traits": traits, "pairs": pairs, "z": Z, "compared": M_TESTED}
        prof["bullets"] = self._bullets(prof)
        prof["rule"] = self.rule(items, prof, be)
        return prof

    @staticmethod
    def _bullets(p: dict) -> list[str]:
        if not p["n"]:
            return ["No finished first trades with stored candles yet - place a few trades and the picture builds up."]
        out = [f"{p['n']} independent first trades analysed (plus {p['steps']['n']} martingale steps and repeated clicks, which are not separate decisions). "
               f"They won {p['wins']} = {p['rate'] * 100:.0f}% (95% range {p['lo'] * 100:.0f}-{p['hi'] * 100:.0f}%) against a break-even rate of {p['be'] * 100:.0f}%."]
        t = {x["key"]: x for x in p["traits"]}
        if p["timing"]["median"] is not None:
            out.append(f"You usually enter {p['timing']['median']:.0f} s into the candle; {p['timing']['at_open'] * 100:.0f}% of your entries come in the first 5 seconds, {p['timing']['late'] * 100:.0f}% in the last 20.")
        f, w = t["fade_move"], t["follow_move"]
        if f["n"] and w["n"] and f["share"] > w["share"]:
            out.append(f"In {f['share'] * 100:.0f}% of your trades price had already moved inside the candle and you bet AGAINST that move: {f['wins']} of {f['n']} won ({f['rate'] * 100:.0f}%). "
                       f"When you went WITH the move it was {w['wins']} of {w['n']} ({w['rate'] * 100:.0f}%).")
        elif f["n"] and w["n"]:
            out.append(f"You follow the candle's move in {w['share'] * 100:.0f}% of trades ({w['wins']}/{w['n']} won) and fade it in {f['share'] * 100:.0f}% ({f['wins']}/{f['n']} won).")
        if p["pairs"]:
            best = max((x for x in p["pairs"] if x["n"] >= 3), key=lambda x: x["rate"], default=None)
            worst = min((x for x in p["pairs"] if x["n"] >= 3), key=lambda x: x["rate"], default=None)
            if best and worst and best["symbol"] != worst["symbol"]:
                out.append(f"Best pair for you: {best['symbol'].replace('_otc', ' OTC')} ({best['wins']}/{best['n']}); weakest: {worst['symbol'].replace('_otc', ' OTC')} ({worst['wins']}/{worst['n']}).")
        proven = [x for x in p["traits"] if x["verdict"] == "proven"]
        if proven:
            out.append("Habits that are statistically proven even after correcting for the number compared: " + ", ".join(x["label"].lower() for x in proven) + ".")
        else:
            out.append(f"None of your habits is statistically proven yet: with {p['n']} trades, chance alone can make a habit look good (the ranges above are widened for the {p['compared']} habits compared). "
                       f"About {p['need']} first trades are needed for solid conclusions.")
        return out

    # ------------------------------------------------------------------ step 3: the live rule
    def rule(self, items: list | None = None, prof: dict | None = None, be: float | None = None) -> dict | None:
        """The live rule learned from the trades, or None when no habit is promising enough to act on. Cached for a minute."""
        if items is None:
            if time.time() - self._rule_at < 60:
                return self._rule
            p = self.profile()
            return p["rule"]
        t = {x["key"]: x for x in prof["traits"]}
        f = t["fade_move"]
        rule = None
        if f["n"] >= MIN_FEW and f["rate"] is not None and f["rate"] >= be:          # a habit that at least beats break-even in your own trades: tried live as an experiment
            incs = sorted(abs(c["inc"]) for _, c, d, _w in items if d * c["inc"] <= -FLAT)
            offs = sorted(c["off"] for _, c, d, _w in items if d * c["inc"] <= -FLAT)
            if incs:
                thr = max(0.12, round(float(np.percentile(incs, 25)), 2))
                lo_off = min(20.0, max(4.0, round(float(np.percentile(offs, 10)), 0)))
                hi_off = min(52.0, max(lo_off + 8, round(float(np.percentile(offs, 90)), 0)))
                skip = [x["symbol"] for x in prof["pairs"] if x["n"] >= 5 and x["rate"] < be]
                rule = {"key": "fade_move", "name": "Fade the candle's move", "thr_atr": thr, "from_s": lo_off, "to_s": hi_off, "skip_pairs": skip, "evidence": {"n": f["n"], "wins": f["wins"], "rate": f["rate"], "verdict": f["verdict"]},
                        "text": f"When the current candle has already moved at least {thr} ATR one way and you are {lo_off:.0f}-{hi_off:.0f} s into it, bet the OTHER way until the candle closes"
                                + (f" (skipping {', '.join(s.replace('_otc', '') for s in skip)}, where your own record is below break-even)." if skip else ".")}
        self._rule, self._rule_at = rule, time.time()
        return rule

    # ------------------------------------------------------------------ live: ticks and candle closes
    def _refresh(self, sym: str) -> dict | None:
        """The state of the last closed candle for a pair (ATR, EMA slope ...), recomputed once per candle."""
        rows = self.engine._series(sym, 60).contiguous_tail(60)
        if len(rows) < 60:
            self.cache.pop(sym, None)
            return None
        ctx = context_from(rows, rows[-1]["t"] + 60, rows[-1]["c"], rows[-1]["c"])
        if ctx is None:
            self.cache.pop(sym, None)
            return None
        ctx["cand"] = rows[-1]["t"] + 60
        self.cache[sym] = ctx
        return ctx

    def on_close(self, sym: str, row: dict) -> None:
        """A 1-minute candle closed: settle the signals that expire with it and refresh the pair's state."""
        try:
            self._refresh(sym)
            for sid, d, entry, payout in self.db.execute("SELECT id, direction, entry_price, payout FROM style_signals WHERE status='pending' AND symbol=? AND expire_t=?", (sym, row["t"] + 60)).fetchall():
                exit_p = row["c"]
                status = self._judge(d, entry, exit_p)
                self.db.execute("UPDATE style_signals SET status=?, exit_price=?, settled_at=? WHERE id=?", (status, exit_p, time.time(), sid))
                if status == "loss":                               # the one martingale step: the same side again, from this close, on the next candle
                    self.db.execute("UPDATE style_signals SET step_status='pending', step_entry=?, step_expire_t=? WHERE id=?", (exit_p, row["t"] + 120, sid))
                if sid in self._alerted_ids:
                    self._alerted_ids.discard(sid)
                    self.engine.emit_style_result(sid, sym, d, entry, exit_p, status, payout, self)
            for sid, d, entry in self.db.execute("SELECT id, direction, step_entry FROM style_signals WHERE step_status='pending' AND symbol=? AND step_expire_t=?", (sym, row["t"] + 60)).fetchall():
                self.db.execute("UPDATE style_signals SET step_status=?, step_exit=? WHERE id=?", (self._judge(d, entry, row["c"]), row["c"], sid))
            self.db.execute("UPDATE style_signals SET step_status='void' WHERE step_status='pending' AND symbol=? AND step_expire_t < ?", (sym, row["t"] + 60))
            # a signal whose candle was thrown away (ticks went missing) can never settle: it is void, not pending forever
            self.db.execute("UPDATE style_signals SET status='void', settled_at=? WHERE status='pending' AND symbol=? AND expire_t < ?", (time.time(), sym, row["t"] + 60))
            for (vid,) in self.db.execute("SELECT id FROM style_signals WHERE status='void' AND symbol=? AND expire_t < ?", (sym, row["t"] + 60)).fetchall():
                self._alerted_ids.discard(vid)
            self.db.commit()
            if len(self._fired) > 400:
                self._fired = {k for k in self._fired if k[1] >= row["t"] - 600}
        except Exception as e:                                     # noqa: BLE001
            self._oops(e)

    def on_tick(self, sym: str, ts: float, price: float) -> None:
        if not self._s["enabled"]:
            return
        try:
            cur = self.engine.open.get((sym, 60))
            if not cur or cur.get("partial"):
                return
            off = ts - cur["t"]
            if off < 4 or off > 52 or (sym, cur["t"]) in self._fired:
                return
            rule = self._rule if time.time() - self._rule_at < 60 else self.rule()
            if not rule or sym in rule["skip_pairs"] or not (rule["from_s"] <= off <= rule["to_s"]):
                return
            st = self.cache.get(sym) or self._refresh(sym)
            if not st or st["cand"] != cur["t"]:
                st = self._refresh(sym)
                if not st or st["cand"] != cur["t"]:
                    return
            inc = (price - cur["o"]) / st["atr"]
            if abs(inc) < rule["thr_atr"]:
                return
            inst = self.engine.instruments.get(sym) or {}
            pay = inst.get("payout")
            if not pay or float(pay) < self._s["min_payout"] or not inst.get("open", True):
                return
            self._fired.add((sym, cur["t"]))
            direction = "PUT" if inc > 0 else "CALL"
            conv = int(max(0, min(100, round(abs(inc) / max(rule["thr_atr"], 0.01) * 40))))
            cur_id = self.db.execute(
                "INSERT INTO style_signals (created, symbol, direction, entry_t, entry_price, expire_t, inc_atr, off_s, payout, status, rule) VALUES (?,?,?,?,?,?,?,?,?,'pending',?)",
                (time.time(), sym, direction, ts, price, cur["t"] + 60, round(inc, 3), round(off, 1), float(pay), rule["key"])).lastrowid
            self.db.commit()
            if self._may_alert():
                self._alerted_ids.add(cur_id)
                self.engine.emit_style_signal({"uid": self.uid, "id": cur_id, "symbol": sym, "direction": direction, "entry": price, "ts": ts, "cand": cur["t"], "inc": inc, "off": off,
                                               "payout": float(pay), "conviction": conv, "rule": rule, "stats": self.record(), "pair_stats": self.record(sym)})
        except Exception as e:                                     # noqa: BLE001 - never break the tick path
            self._oops(e)

    def _oops(self, e: Exception) -> None:
        if time.time() - self._err_at > 60:
            self._err_at = time.time()
            try:
                self.engine._log("style-error", str(e))
            except Exception:                                      # noqa: BLE001
                pass

    # ------------------------------------------------------------------ the record and the alert policy
    def record(self, sym: str | None = None) -> dict:
        q = "SELECT status, payout FROM style_signals WHERE status IN ('win','loss')" + (" AND symbol=?" if sym else "")
        rows = self.db.execute(q, (sym,) if sym else ()).fetchall()
        w = sum(1 for s, _ in rows if s == "win")
        n = len(rows)
        pays = [p for _, p in rows if p]
        be = 1 / (1 + (sum(pays) / len(pays)) / 100) if pays else None
        lo, hi = wilson(w, n, 1.96) if n else (None, None)
        pend = self.db.execute("SELECT COUNT(*) FROM style_signals WHERE status='pending'").fetchone()[0]
        return {"n": n, "wins": w, "losses": n - w, "acc": w / n if n else None, "lo": lo, "hi": hi, "be": be, "pending": pend if not sym else None}

    def mtg(self) -> dict:
        """The one-step martingale on the rule's lost signals: the same side again on the next candle with a stake MTG_MULT times bigger. Profit in units of the first stake."""
        rows = self.db.execute("SELECT status, step_status, payout FROM style_signals WHERE status IN ('win','loss')").fetchall()
        steps = [(s, p) for st, s, p in rows if st == "loss" and s in ("win", "loss", "draw")]
        wins = sum(1 for s, _ in steps if s == "win")
        net_flat = net_mtg = 0.0
        cycles = 0
        for st, step, pay in rows:
            pr = (pay or 85) / 100
            if st == "loss" and step == "pending":
                continue                                           # the cycle is not finished: left out of both sums
            cycles += 1
            if st == "win":
                net_flat += pr
                net_mtg += pr
                continue
            net_flat -= 1
            if step == "win":
                net_mtg += -1 + MTG_MULT * pr
            elif step == "loss":
                net_mtg -= 1 + MTG_MULT
            else:                                                  # draw (stake back) or void (not played)
                net_mtg -= 1
        n_loss = sum(1 for st, _, _ in rows if st == "loss")
        pend = sum(1 for st, s, _ in rows if st == "loss" and s == "pending")
        return {"mult": MTG_MULT, "lost": n_loss, "n": len(steps), "wins": wins, "losses": sum(1 for s, _ in steps if s == "loss"), "pending": pend,
                "acc": wins / len(steps) if steps else None, "cycles": cycles, "net_flat": round(net_flat, 2), "net_mtg": round(net_mtg, 2)}

    def alerts_open(self) -> tuple[bool, str]:
        """May the rule alert right now? 'earned' = only once its own measured record is long enough (30+ settled signals) and at or above break-even."""
        a = self._s["alert"]
        if a == "off":
            return False, "alerts are off"
        if a == "all":
            return True, "alerting on every signal"
        r = self.record()
        if r["n"] < ALERT_AFTER:
            return False, f"paper only: it needs {ALERT_AFTER} settled signals before it may alert ({r['n']} so far)"
        if r["acc"] >= (r["be"] or 0.54):
            return True, f"earned: {r['acc'] * 100:.0f}% over {r['n']} signals, at or above break-even"
        return False, f"paper only: its record {r['acc'] * 100:.0f}% over {r['n']} signals is below break-even {(r['be'] or 0.54) * 100:.0f}% (it keeps recording and can recover)"

    def _may_alert(self) -> bool:
        if not self.engine.alerts_enabled or not self.alerts_open()[0]:
            return False
        now = time.time()
        self._alert_times = [t for t in self._alert_times if now - t < 3600]
        if len(self._alert_times) >= MAX_ALERTS_PER_HOUR:
            return False
        self._alert_times.append(now)
        return True

    # ------------------------------------------------------------------ for the page
    def status(self) -> dict:
        prof = self.profile()
        recent = [{"id": i, "created": c, "symbol": s, "direction": d, "entry": e, "exit": x, "expire_t": ex, "inc_atr": inc, "off_s": off, "status": st, "payout": p,
                   "step": ss, "step_entry": se, "step_exit": sx}
                  for (i, c, s, d, e, x, ex, inc, off, st, p, ss, se, sx) in self.db.execute(
                      "SELECT id, created, symbol, direction, entry_price, exit_price, expire_t, inc_atr, off_s, status, payout, step_status, step_entry, step_exit FROM style_signals ORDER BY id DESC LIMIT 40")]
        ok, why = self.alerts_open()
        return {"settings": self.settings(), "profile": prof, "record": self.record(), "mtg": self.mtg(), "recent": recent, "alerting": ok, "alert_note": why, "now": time.time(),
                "streaming": sorted(s for s, c in self.cache.items() if c)}
