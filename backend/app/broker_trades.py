"""The trades you place on the broker, and whether they won.

The extension (read-only, as always) forwards the order events the Quotex page already receives - an order opened, an order closed with its result -
as `{"k": "o", "n": <event name>, "dir": "in"|"out", "d": <whitelisted fields>, "ts": <seconds>}`. This module turns them into one row per trade in
`broker_trades`, keeps the last few hundred raw events in `broker_events` (so the parser can be corrected from what the broker really sends), and answers the
Trades page: totals, win rate, profit / loss, per day, per pair, and whether each trade followed a Tradzen signal.

Nothing here ever places, changes or cancels a trade.

Profit: Quotex reports a closed deal's `profit`. Depending on the build that is the net gain (0.80 on a 1.00 stake) or the whole return (1.80), and a lost
deal reports 0. `settle()` reads it like this: more than the stake = whole return, net = profit - stake; exactly the stake = refund (draw); between 0 and the
stake = net gain; 0 = the stake is lost (a draw when the close price equals the open price); below 0 = the net result already.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime, timezone

KEEP_EVENTS = 400
MTG_WINDOW = 90.0                  # a trade on the same pair opened within this many seconds after a LOSS closed is the martingale step of that loss
REQ_TTL = 20.0                     # a request we saw leave the page is matched to the broker's answer within this many seconds
OPEN_RE = re.compile(r"open|creat|pending", re.I)
CLOSE_RE = re.compile(r"clos|settle|history", re.I)


def to_ts(v) -> float | None:
    """Seconds since the epoch from a number (seconds or milliseconds), a numeric string or an ISO date string (read as UTC); None if it is neither."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            v = float(s)
        except ValueError:
            try:
                d = datetime.fromisoformat(s.replace("Z", "+00:00"))
            except ValueError:
                return None
            return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    if not isinstance(v, (int, float)) or v <= 0:
        return None
    return v / 1000.0 if v > 1e11 else float(v)


def num(v) -> float | None:
    try:
        return None if v is None or isinstance(v, bool) else float(v)
    except (TypeError, ValueError):
        return None


def direction_of(o: dict) -> str | None:
    c = o.get("command")
    if c is None:
        c = o.get("action", o.get("direction"))
    if isinstance(c, str):
        s = c.strip().lower()
        if s in ("call", "buy", "up", "higher", "0"):
            return "CALL"
        if s in ("put", "sell", "down", "lower", "1"):
            return "PUT"
        return None
    if c in (0, 1) and not isinstance(c, bool):
        return "CALL" if c == 0 else "PUT"
    return None


def settle(amount: float | None, profit: float | None, open_price: float | None = None, close_price: float | None = None):
    """(net result in account currency, 'win' | 'loss' | 'draw') of a closed deal, or (None, 'open') when there is not enough to tell."""
    if amount is None or profit is None:
        return None, "open"
    if profit > amount * 1.0001:
        net = profit - amount
    elif abs(profit - amount) <= 1e-9 * max(1.0, amount):
        return 0.0, "draw"
    elif profit > 0:
        net = profit
    elif profit == 0:
        if open_price is not None and close_price is not None and open_price == close_price:
            return 0.0, "draw"
        net = -amount
    else:
        net = profit
    return round(net, 4), "win" if net > 0 else "loss" if net < 0 else "draw"


def parse_currency(v) -> dict | None:
    """{'code': 'INR', 'symbol': '₹'} from whatever the broker calls the account currency: 'USD', '$', {'code': 'USD', 'symbol': '$'}; None if unreadable."""
    code = symbol = None
    if isinstance(v, str):
        v = v.strip()
        if re.fullmatch(r"[A-Za-z]{3}", v):
            code = v.upper()
        elif v:
            symbol = v[:4]
    elif isinstance(v, dict):
        c = v.get("code") or v.get("iso")
        if isinstance(c, str) and re.fullmatch(r"[A-Za-z]{3}", c.strip()):
            code = c.strip().upper()
        sy = v.get("symbol") or v.get("currencySymbol")
        if isinstance(sy, str) and sy.strip():
            symbol = sy.strip()[:4]
    return {"code": code, "symbol": symbol} if code or symbol else None


# What the Quotex page shows next to the balance -> the ISO code. '$' is shared by several currencies, so it keeps a dollar code that is already known.
SYMBOL_CODES = {"₹": "INR", "€": "EUR", "£": "GBP", "R$": "BRL", "¥": "JPY", "₽": "RUB", "₺": "TRY", "₦": "NGN", "₴": "UAH", "₸": "KZT", "฿": "THB", "₫": "VND",
                "₱": "PHP", "৳": "BDT", "Rp": "IDR", "RM": "MYR", "zł": "PLN", "Kč": "CZK", "₪": "ILS", "₩": "KRW", "Rs": "PKR"}
DOLLAR_CODES = {"USD", "CAD", "AUD", "NZD", "MXN", "SGD", "HKD", "ARS", "CLP", "COP"}


def _deals(d) -> list[tuple[dict, bool]]:
    """(deal dict, came inside a `deals` list) for every order in an event payload."""
    out: list[tuple[dict, bool]] = []
    if isinstance(d, list):
        for x in d:
            out.extend(_deals(x))
    elif isinstance(d, dict):
        if isinstance(d.get("deals"), list):
            out.extend((x, True) for x in d["deals"] if isinstance(x, dict))
        elif d.get("id") is not None or d.get("ticket") is not None:
            out.append((d, False))
    return out


class BrokerTrades:
    def __init__(self, db: sqlite3.Connection, shared: sqlite3.Connection | None = None, public_only: bool = False) -> None:
        """db = this user's own database (trades, raw events, settings); shared = the main database with the market (signals, candles). public_only: only signals that
        were really alerted (live) may be matched with the user's trades, so nobody learns about strategies that are still being tested."""
        self.db = db
        self.shared = shared if shared is not None else db
        self.public_only = public_only
        self.requests: list[dict] = []           # orders that left the page, waiting for the broker's answer
        self._logged = 0
        from . import control
        self._control = control
        stored = control.get_setting(db, "account", None) or {}
        # balances by account (live / demo) with the time they were seen, and the account currency
        self.account: dict = {"live": stored.get("live"), "demo": stored.get("demo"), "live_at": stored.get("live_at"), "demo_at": stored.get("demo_at"),
                              "currency": stored.get("currency"), "updated": stored.get("updated")}

    # ------------------------------------------------------------------ account balance + currency
    def _set_balance(self, which: str, value, ts: float) -> bool:
        v = num(value)
        if v is None or which not in ("live", "demo") or ts < (self.account.get(which + "_at") or 0):
            return False
        changed = self.account[which] != v
        self.account[which], self.account[which + "_at"] = v, ts
        return changed

    def _set_currency(self, v, page: bool = False) -> bool:
        """A currency from an order (`currency: 'USD'`, which can be an old trade in the account's earlier currency) or, with page=True, the symbol the Quotex page itself
        shows next to the balance - that is what the account is in right now, so once it has been seen an order can no longer change it."""
        c = parse_currency(v)
        if not c:
            return False
        old = self.account.get("currency") or {}
        if page:
            sym = c["symbol"]
            if not sym:
                return False
            code = c["code"] or SYMBOL_CODES.get(sym) or ((old.get("code") if old.get("code") in DOLLAR_CODES else "USD") if sym == "$" else None)
            merged = {"code": code, "symbol": sym, "page": True}
        elif old.get("page"):
            return False
        else:
            merged = {"code": c["code"] or old.get("code"), "symbol": c["symbol"] or old.get("symbol")}
        if merged == old:
            return False
        self.account["currency"] = merged
        return True

    def on_balance(self, item: dict, now: float | None = None) -> None:
        """A balance message: {liveBalance, demoBalance} or {balance, isDemo}, plus the currency when it is in there."""
        ts = to_ts(item.get("ts")) or float(now if now is not None else time.time())
        d = item.get("d")
        if not isinstance(d, dict):
            return
        changed = False
        if "liveBalance" in d or "demoBalance" in d:
            changed |= self._set_balance("live", d.get("liveBalance"), ts)
            changed |= self._set_balance("demo", d.get("demoBalance"), ts)
        elif d.get("balance") is not None:
            changed |= self._set_balance("demo" if d.get("isDemo") else "live", d.get("balance"), ts)
        for k in ("currency", "currencySymbol"):
            if d.get(k) is not None:
                changed |= self._set_currency(d.get(k), page=bool(d.get("page")))
        self._log(ts, item.get("dir"), "balance:" + str(item.get("n")), d)
        self._save(changed, ts)

    def _save(self, changed: bool, ts: float) -> None:
        if changed:
            self.account["updated"] = ts
            self._control.set_setting(self.db, "account", self.account)

    def account_info(self) -> dict:
        return dict(self.account)

    # ------------------------------------------------------------------ intake
    def on_item(self, item: dict, now: float | None = None) -> None:
        now = float(now if now is not None else time.time())
        name, direction, d = str(item.get("n") or ""), item.get("dir"), item.get("d")
        ts = to_ts(item.get("ts")) or now
        self._log(ts, direction, name, d)
        if direction == "out":
            if isinstance(d, dict) and OPEN_RE.search(name) and d.get("asset") and d.get("amount") is not None and not re.search(r"cancel|clos", name, re.I):
                self.requests = [r for r in self.requests if ts - r["t"] < REQ_TTL][-9:]
                self.requests.append({"t": ts, "req": d.get("requestId"), "asset": d.get("asset"), "amount": num(d.get("amount")), "dir": direction_of(d),
                                      "expire": to_ts(d.get("time")), "demo": d.get("isDemo")})
            return
        for o, in_list in _deals(d):
            try:
                self._deal(name, o, in_list, ts)
            except Exception:                    # noqa: BLE001 - an odd payload must never break the feed
                continue
        self.db.commit()

    def _take_request(self, o: dict, ts: float) -> dict | None:
        rid = o.get("requestId")
        for i, r in enumerate(self.requests):
            if (rid is not None and r["req"] == rid) or (rid is None and 0 <= ts - r["t"] < REQ_TTL and (not o.get("asset") or o.get("asset") == r["asset"])):
                return self.requests.pop(i)
        return None

    def _deal(self, name: str, o: dict, in_list: bool, ts: float) -> None:
        tid = str(o.get("id") if o.get("id") is not None else o.get("ticket"))
        acc_changed = self._set_currency(o.get("currency")) if o.get("currency") is not None else False
        if o.get("accountBalance") is not None and o.get("isDemo") is not None:
            acc_changed |= self._set_balance("demo" if o.get("isDemo") else "live", o.get("accountBalance"), ts)
        if acc_changed:
            self._save(True, ts)
        status_txt = str(o.get("status") or o.get("result") or "").lower()
        amount, profit = num(o.get("amount")), num(o.get("profit"))
        open_price, close_price = num(o.get("openPrice")), num(o.get("closePrice"))
        open_t = to_ts(o.get("openTimestamp")) or to_ts(o.get("openTime"))
        close_t = to_ts(o.get("closeTimestamp")) or to_ts(o.get("closeTime"))
        closed = bool(CLOSE_RE.search(name) or in_list or status_txt in ("closed", "win", "loss", "won", "lost", "draw")
                      or (profit is not None and close_t is not None and close_t <= ts + 1 and not OPEN_RE.search(name)))
        req = None if closed else self._take_request(o, ts)
        asset = o.get("asset") or (req and req["asset"])
        direction = direction_of(o) or (req and req["dir"])
        amount = amount if amount is not None else (req and req["amount"])
        demo = o.get("isDemo", req and req["demo"])
        demo = None if demo is None else int(bool(demo))
        if closed:
            if amount is None:                       # the closing message may not repeat the stake: take it from the opening one
                row = self.db.execute("SELECT amount, open_price FROM broker_trades WHERE id=?", (tid,)).fetchone()
                amount = row[0] if row else None
                open_price = open_price if open_price is not None else (row[1] if row else None)
            net, status = settle(amount, profit, open_price, close_price)
            if status == "open" and status_txt in ("win", "won", "loss", "lost", "draw"):
                status = {"won": "win", "lost": "loss"}.get(status_txt, status_txt)
        else:
            net, status = None, "open"
        expire = None if closed else (close_t or (req and req["expire"]))
        payout = num(o.get("percentProfit", o.get("profitPercent")))
        if payout is None and status == "win" and amount:
            payout = round(net / amount * 100, 1)
        if status == "open" and open_t is None:
            open_t = ts
        self.db.execute(
            "INSERT INTO broker_trades (id, symbol, direction, amount, open_t, expire_t, close_t, open_price, close_price, payout, profit, status, is_demo, first_seen, updated) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "symbol=COALESCE(excluded.symbol, symbol), direction=COALESCE(excluded.direction, direction), amount=COALESCE(excluded.amount, amount), "
            "open_t=COALESCE(open_t, excluded.open_t), expire_t=COALESCE(excluded.expire_t, expire_t), close_t=COALESCE(excluded.close_t, close_t), "
            "open_price=COALESCE(excluded.open_price, open_price), close_price=COALESCE(excluded.close_price, close_price), "
            "payout=COALESCE(excluded.payout, payout), profit=COALESCE(excluded.profit, profit), "
            "status=CASE WHEN excluded.status='open' AND broker_trades.status!='open' THEN broker_trades.status ELSE excluded.status END, "
            "is_demo=COALESCE(excluded.is_demo, is_demo), updated=excluded.updated",
            (tid, asset, direction, amount, open_t, expire, (close_t or ts) if closed else None, open_price, close_price if closed else None,
             payout, net, status, demo, ts, ts))

    def _log(self, ts: float, direction, name: str, d) -> None:
        try:
            self.db.execute("INSERT INTO broker_events (ts, dir, name, payload) VALUES (?,?,?,?)", (ts, direction, name, json.dumps(d)[:4000]))
            self._logged += 1
            if self._logged % 50 == 0:
                self.db.execute("DELETE FROM broker_events WHERE id <= (SELECT MAX(id) FROM broker_events) - ?", (KEEP_EVENTS,))
            self.db.commit()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------------ answers
    def raw(self, limit: int = 60) -> list[dict]:
        rows = self.db.execute("SELECT ts, dir, name, payload FROM broker_events ORDER BY id DESC LIMIT ?", (max(1, min(limit, KEEP_EVENTS)),)).fetchall()
        out = []
        for ts, dr, nm, pl in rows:
            try:
                p = json.loads(pl)
            except ValueError:
                p = pl
            out.append({"ts": ts, "dir": dr, "name": nm, "payload": p})
        return out

    def delete(self, tid: str) -> bool:
        """Remove a trade from the page. It is hidden, not erased: it stays hidden when the broker resends it, and `restore` brings it back."""
        cur = self.db.execute("UPDATE broker_trades SET hidden=1 WHERE id=? AND hidden=0", (tid,))
        self.db.commit()
        return cur.rowcount > 0

    def restore(self, tid: str | None = None) -> int:
        """Bring back one removed trade (or all of them when tid is None); returns how many came back."""
        cur = self.db.execute("UPDATE broker_trades SET hidden=0 WHERE hidden=1" + (" AND id=?" if tid is not None else ""), (tid,) if tid is not None else ())
        self.db.commit()
        return cur.rowcount

    SORT_KEYS = {"t": "t", "symbol": "symbol", "direction": "direction", "amount": "amount", "price": "open_price", "status": "status", "profit": "profit", "source": "source"}

    def query(self, account: str = "all", since: float | None = None, tz_min: int = 0, limit: int = 200, offset: int = 0,
              names: dict[int, str] | None = None, until: float | None = None, sort: str = "", desc: bool = True) -> dict:
        """Everything the Trades page shows. account: all | real | demo. since / until: only trades opened at or after / before these times (seconds).
        sort: one of SORT_KEYS - orders the TABLE of trades (the totals, charts and martingale cycles always cover the whole range); default newest first."""
        where, args = ["hidden = 0"], []
        if account == "real":
            where.append("COALESCE(is_demo, 0) = 0")
        elif account == "demo":
            where.append("is_demo = 1")
        if since:
            where.append("COALESCE(open_t, first_seen) >= ?")
            args.append(since)
        if until:
            where.append("COALESCE(open_t, first_seen) < ?")
            args.append(until)
        cond = ("WHERE " + " AND ".join(where)) if where else ""
        rows = [dict(zip(("id", "symbol", "direction", "amount", "open_t", "expire_t", "close_t", "open_price", "close_price", "payout", "profit", "status", "is_demo"), r))
                for r in self.db.execute(
                    "SELECT id, symbol, direction, amount, open_t, expire_t, close_t, open_price, close_price, payout, profit, status, is_demo "
                    f"FROM broker_trades {cond} ORDER BY COALESCE(open_t, first_seen) DESC LIMIT 5000", args)]
        for r in rows:
            r["t"] = r["open_t"] or 0
        self._tag_signals(rows, names or {})
        mtg = self._martingale(rows)
        settled = [r for r in rows if r["status"] in ("win", "loss", "draw")]
        page = rows
        if sort in self.SORT_KEYS and sort != "t":
            k = self.SORT_KEYS[sort]
            have = [r for r in rows if r.get(k) is not None]
            page = sorted(have, key=lambda r: (r[k], r["t"]), reverse=desc) + [r for r in rows if r.get(k) is None]     # empty values always last
        elif sort == "t" and not desc:
            page = rows[::-1]
        return {"summary": self._summary(settled, rows), "daily": self._daily(settled, tz_min), "pairs": self._pairs(settled), "source": self._by_source(settled),
                "sides": self._by_side(settled), "mtg": mtg, "total": len(rows), "trades": page[max(0, offset):max(0, offset) + max(1, min(limit, 1000))],
                "events_seen": self.db.execute("SELECT COUNT(*) FROM broker_events").fetchone()[0],
                "removed": self.db.execute("SELECT COUNT(*) FROM broker_trades WHERE hidden=1").fetchone()[0]}

    def _tag_signals(self, rows: list[dict], names: dict[int, str]) -> None:
        """Did this trade follow a Tradzen signal? 'followed' = same pair and direction shortly before entry, 'against' = the opposite call, 'own' = no signal."""
        times = [r["t"] for r in rows if r["t"]]
        if not times:
            for r in rows:
                r["source"], r["signal"] = "own", None
            return
        lo, hi = min(times) - 180, max(times) + 10
        by_sym: dict[str, list[tuple]] = {}
        for sid, created, sym, dr, tier, strat, mode in self.shared.execute(
                "SELECT id, created, symbol, direction, tier, strategy_id, mode FROM signals WHERE created BETWEEN ? AND ? AND status != 'skip' AND direction IN ('CALL','PUT')"
                + (" AND mode='live'" if self.public_only else ""), (lo, hi)):
            by_sym.setdefault(sym, []).append((created, sid, dr, tier, strat, mode))
        for r in rows:
            r["source"], r["signal"] = "own", None
            if not r["t"] or not r["symbol"]:
                continue
            cands = [c for c in by_sym.get(r["symbol"], []) if r["t"] - 150 <= c[0] <= r["t"] + 3]
            same = [c for c in cands if c[2] == r["direction"]]
            if same:
                c = max(same, key=lambda x: x[0])
                r["source"] = "followed"
                r["signal"] = {"id": c[1], "tier": c[3], "mode": c[5], "strategy": names.get(c[4], "?"), "strategy_id": c[4], "created": c[0]}
            elif cands:
                r["source"] = "against"

    @staticmethod
    def _stats(rows: list[dict]) -> dict:
        w = sum(1 for r in rows if r["status"] == "win")
        lo = sum(1 for r in rows if r["status"] == "loss")
        dr = len(rows) - w - lo
        staked = sum(r["amount"] or 0 for r in rows)
        net = sum(r["profit"] or 0 for r in rows)
        return {"n": len(rows), "wins": w, "losses": lo, "draws": dr, "win_rate": round(w / (w + lo), 4) if w + lo else None,
                "staked": round(staked, 2), "net": round(net, 2), "roi": round(net / staked, 4) if staked else None}

    def _summary(self, settled: list[dict], rows: list[dict]) -> dict:
        s = self._stats(settled)
        s["open"] = sum(1 for r in rows if r["status"] == "open")
        s["placed"] = len(rows)
        s["avg_stake"] = round(s["staked"] / len(settled), 2) if settled else None
        pays = [r["payout"] for r in settled if r["status"] == "win" and r["payout"]]
        s["avg_payout"] = round(sum(pays) / len(pays), 1) if pays else None
        nets = [r["profit"] for r in settled if r["profit"] is not None]
        s["best"], s["worst"] = (round(max(nets), 2), round(min(nets), 2)) if nets else (None, None)
        s["break_even"] = round(1 / (1 + s["avg_payout"] / 100), 4) if s["avg_payout"] else None
        ordered = sorted((r for r in settled if r["status"] in ("win", "loss")), key=lambda r: r["close_t"] or r["t"])
        cur = best_w = best_l = run = 0
        last = None
        for r in ordered:
            run = run + 1 if r["status"] == last else 1
            last = r["status"]
            if last == "win":
                best_w = max(best_w, run)
            else:
                best_l = max(best_l, run)
        cur = run if ordered else 0
        s["streak"] = {"kind": last, "n": cur}
        s["best_win_streak"], s["best_loss_streak"] = best_w, best_l
        return s

    def _daily(self, settled: list[dict], tz_min: int) -> list[dict]:
        days: dict[str, list[dict]] = {}
        for r in settled:
            t = r["close_t"] or r["t"]
            if not t:
                continue
            day = datetime.fromtimestamp(t + tz_min * 60, tz=timezone.utc).strftime("%Y-%m-%d")
            days.setdefault(day, []).append(r)
        out, cum = [], 0.0
        for day in sorted(days):
            st = self._stats(days[day])
            cum += st["net"]
            out.append({"date": day, "n": st["n"], "wins": st["wins"], "losses": st["losses"], "net": st["net"], "cum": round(cum, 2)})
        return out

    def calendar(self, account: str = "all", since: float | None = None, until: float | None = None, tz_min: int = 0) -> dict:
        """The profit / loss calendar: one entry per calendar day (your own days, `tz_min` = your UTC offset in minutes) that has finished trades between `since` and `until`
        (seconds; `until` exclusive), a summary of the period and the pairs ranked by what they earned. A trade belongs to the day it CLOSED, like the by-day chart.
        Days without trades are simply absent. Draws count as trades but not as wins or losses."""
        where, args = ["hidden = 0", "status IN ('win', 'loss', 'draw')"], []
        if account == "real":
            where.append("COALESCE(is_demo, 0) = 0")
        elif account == "demo":
            where.append("is_demo = 1")
        if since:                                              # a day of margin each side: a trade opened late on the last day may close on the next one
            where.append("COALESCE(open_t, first_seen) >= ?")
            args.append(since - 86400)
        if until:
            where.append("COALESCE(open_t, first_seen) < ?")
            args.append(until + 86400)
        rows = [dict(zip(("id", "symbol", "amount", "profit", "status", "t"), r)) for r in self.db.execute(
            f"SELECT id, symbol, amount, profit, status, COALESCE(close_t, open_t, first_seen) FROM broker_trades WHERE {' AND '.join(where)}", args)]

        def day_of(t: float) -> str:
            return datetime.fromtimestamp(t + tz_min * 60, tz=timezone.utc).strftime("%Y-%m-%d")

        lo_day = day_of(since) if since else None
        hi_day = day_of(until - 1) if until else None
        by_day: dict[str, list[dict]] = {}
        for r in rows:
            if not r["t"]:
                continue
            d = day_of(r["t"])
            if (lo_day and d < lo_day) or (hi_day and d > hi_day):
                continue
            by_day.setdefault(d, []).append(r)
        days: dict[str, dict] = {}
        for d, rs in by_day.items():
            st = self._stats(rs)
            nets = [r["profit"] for r in rs if r["profit"] is not None]
            pairs: dict[str, float] = {}
            for r in rs:
                pairs[r["symbol"] or "?"] = pairs.get(r["symbol"] or "?", 0.0) + (r["profit"] or 0.0)
            top = max(pairs.items(), key=lambda kv: kv[1]) if pairs else None
            days[d] = {**st, "best": round(max(nets), 2) if nets else None, "worst": round(min(nets), 2) if nets else None,
                       "top_pair": {"symbol": top[0], "net": round(top[1], 2)} if top else None}
        allrows = [r for rs in by_day.values() for r in rs]
        order = sorted(days)
        green = [d for d in order if days[d]["net"] > 0]
        red = [d for d in order if days[d]["net"] < 0]
        run_g = run_r = best_g = best_r = 0
        for d in order:                                        # streaks over the days you traded (a day off does not break one)
            net = days[d]["net"]
            run_g = run_g + 1 if net > 0 else 0
            run_r = run_r + 1 if net < 0 else 0
            best_g, best_r = max(best_g, run_g), max(best_r, run_r)
        summary = {**self._stats(allrows), "trading_days": len(order), "green_days": len(green), "red_days": len(red), "flat_days": len(order) - len(green) - len(red),
                   "best_day": ({"date": max(order, key=lambda d: days[d]["net"]), "net": max(days[d]["net"] for d in order)} if order else None),
                   "worst_day": ({"date": min(order, key=lambda d: days[d]["net"]), "net": min(days[d]["net"] for d in order)} if order else None),
                   "avg_day": round(sum(days[d]["net"] for d in order) / len(order), 2) if order else None,
                   "best_green_streak": best_g, "best_red_streak": best_r, "current_streak": {"kind": ("green" if run_g else "red" if run_r else None), "n": run_g or run_r},
                   "first_day": order[0] if order else None, "last_day": order[-1] if order else None}
        g: dict[str, list[dict]] = {}
        for r in allrows:
            g.setdefault(r["symbol"] or "?", []).append(r)
        pairs_out = sorted(({"symbol": k, **self._stats(v)} for k, v in g.items()), key=lambda x: (-x["net"], -x["n"]))
        return {"days": days, "summary": summary, "pairs": pairs_out[:30]}

    def hours(self, account: str = "all", since: float | None = None, until: float | None = None, tz_min: int = 0) -> dict:
        """When you trade: the finished trades between `since` and `until` (seconds; `until` exclusive) grouped by the weekday and the hour they were OPENED, on your own clock
        (`tz_min` = your UTC offset in minutes). `cells` has one entry per slot that has trades: d = 0 (Sunday) .. 6 (Saturday), h = 0..23, plus the usual stats (n, wins, losses,
        draws, win_rate, staked, net, roi). Slots without trades are absent. Draws count as trades but not as wins or losses. `summary` covers every trade of the range."""
        where, args = ["hidden = 0", "status IN ('win', 'loss', 'draw')"], []
        if account == "real":
            where.append("COALESCE(is_demo, 0) = 0")
        elif account == "demo":
            where.append("is_demo = 1")
        if since:
            where.append("COALESCE(open_t, first_seen) >= ?")
            args.append(since)
        if until:
            where.append("COALESCE(open_t, first_seen) < ?")
            args.append(until)
        rows = [dict(zip(("amount", "profit", "status", "t"), r)) for r in self.db.execute(
            f"SELECT amount, profit, status, COALESCE(open_t, first_seen) FROM broker_trades WHERE {' AND '.join(where)}", args)]
        slots: dict[tuple[int, int], list[dict]] = {}
        for r in rows:
            if not r["t"]:
                continue
            at = datetime.fromtimestamp(r["t"] + tz_min * 60, tz=timezone.utc)
            slots.setdefault(((at.weekday() + 1) % 7, at.hour), []).append(r)         # Python's Monday = 0 -> Sunday = 0
        cells = [{"d": d, "h": h, **self._stats(rs)} for (d, h), rs in sorted(slots.items())]
        return {"cells": cells, "summary": self._stats([r for rs in slots.values() for r in rs])}

    def _pairs(self, settled: list[dict]) -> list[dict]:
        g: dict[str, list[dict]] = {}
        for r in settled:
            g.setdefault(r["symbol"] or "?", []).append(r)
        out = [{"symbol": k, **self._stats(v)} for k, v in g.items()]
        return sorted(out, key=lambda x: -x["n"])

    def _by_source(self, settled: list[dict]) -> list[dict]:
        return [{"source": k, **self._stats([r for r in settled if r["source"] == k])} for k in ("followed", "own", "against")]

    def _by_side(self, settled: list[dict]) -> list[dict]:
        return [{"direction": k, **self._stats([r for r in settled if r["direction"] == k])} for k in ("CALL", "PUT")]

    # ------------------------------------------------------------------ martingale (one step)
    @staticmethod
    def _martingale(rows: list[dict]) -> dict:
        return BrokerTrades._martingale_full(rows)[0]

    @staticmethod
    def _martingale_full(rows: list[dict]):
        """Group the orders into positions and the positions into cycles, and tag every order with its step.

        Position: orders on the same pair, direction and expiry (one decision, possibly clicked several times: 2 x 1 $ for a doubled stake).
        Cycle: a first position, plus - only when it LOST and a position on the same pair opens within MTG_WINDOW seconds after it closed - one martingale step.
        You take one step at most, so a loss on the step ends the cycle and the next trade starts a new one.
        Sets on each row: step (0 = first trade, 1 = martingale step), cycle (id of the first order of the cycle).
        """
        groups: dict[tuple, list[dict]] = {}
        for r in rows:
            if not r["symbol"] or not r["t"]:
                r["step"], r["cycle"] = 0, r["id"]
                continue
            groups.setdefault((r["symbol"], r["direction"], round(r["close_t"] or r["expire_t"] or r["t"])), []).append(r)
        pos = []
        for (sym, dr, _), g in groups.items():
            running = any(r["status"] == "open" for r in g)
            net = None if running else round(sum(r["profit"] or 0 for r in g), 4)
            status = "open" if running else "win" if net > 1e-9 else "loss" if net < -1e-9 else "draw"
            pos.append({"symbol": sym, "dir": dr, "orders": g, "n": len(g), "stake": round(sum(r["amount"] or 0 for r in g), 4), "net": net, "status": status,
                        "open_t": min(r["t"] for r in g), "close_t": max((r["close_t"] or r["expire_t"] or 0) for r in g), "step": 0, "cycle": None})
        pos.sort(key=lambda x: x["open_t"])
        cycles: list[list[dict]] = []
        last: dict[str, dict] = {}
        for p in pos:
            prev = last.get(p["symbol"])
            if prev and prev["status"] == "loss" and prev["step"] == 0 and prev["close_t"] and -1 <= p["open_t"] - prev["close_t"] <= MTG_WINDOW:
                p["step"], p["cycle"] = 1, prev["cycle"]
                cycles[p["cycle"]].append(p)
            else:
                p["cycle"] = len(cycles)
                cycles.append([p])
            last[p["symbol"]] = p
        for p in pos:
            first_id = cycles[p["cycle"]][0]["orders"][0]["id"]
            for r in p["orders"]:
                r["step"], r["cycle"] = p["step"], first_id

        def grp(ps: list[dict]) -> dict:
            done = [x for x in ps if x["status"] != "open"]
            w, lo = sum(1 for x in done if x["status"] == "win"), sum(1 for x in done if x["status"] == "loss")
            stake = sum(x["stake"] for x in done)
            net = sum(x["net"] for x in done)
            return {"n": len(done), "wins": w, "losses": lo, "draws": len(done) - w - lo, "win_rate": round(w / (w + lo), 4) if w + lo else None,
                    "staked": round(stake, 2), "net": round(net, 2)}

        firsts = [c[0] for c in cycles]
        steps = [c[1] for c in cycles if len(c) > 1]
        done_cycles = [c for c in cycles if all(x["status"] != "open" for x in c)]
        listing = []
        for c in sorted(cycles, key=lambda c: -c[0]["open_t"])[:60]:
            f = c[0]
            s2 = c[1] if len(c) > 1 else None
            net = None if any(x["status"] == "open" for x in c) else round(sum(x["net"] for x in c), 2)
            outcome = ("running" if net is None else "first" if not s2 and f["status"] == "win" else "draw" if not s2 and f["status"] == "draw" else "lost" if not s2
                       else "recovered" if s2["status"] == "win" else "failed" if s2["status"] == "loss" else "draw")
            listing.append({"t": f["open_t"], "symbol": f["symbol"], "dir": f["dir"], "id": f["orders"][0]["id"], "step_id": s2["orders"][0]["id"] if s2 else None, "first": {"stake": f["stake"], "net": f["net"], "status": f["status"], "orders": f["n"]},
                            "step": s2 and {"stake": s2["stake"], "net": s2["net"], "status": s2["status"], "orders": s2["n"], "ratio": round(s2["stake"] / f["stake"], 2) if f["stake"] else None},
                            "net": net, "outcome": outcome, "exposure": round(sum(x["stake"] for x in c), 2)})
        sg = grp(steps)
        first_only = round(sum(x["net"] for x in firsts if x["status"] != "open"), 2)
        total = round(sum(x["net"] for c in done_cycles for x in c), 2)
        recovered = sum(1 for c in done_cycles if len(c) > 1 and c[1]["status"] == "win")
        failed = sum(1 for c in done_cycles if len(c) > 1 and c[1]["status"] == "loss")
        ratios = [c[1]["stake"] / c[0]["stake"] for c in cycles if len(c) > 1 and c[0]["stake"]]
        summary = {"positions": len(pos), "cycles": len(cycles), "first": grp(firsts), "step": sg, "taken": len(steps), "recovered": recovered, "failed": failed,
                "losses_not_followed": sum(1 for c in cycles if len(c) == 1 and c[0]["status"] == "loss"),
                "avg_ratio": round(sum(ratios) / len(ratios), 2) if ratios else None,
                "net_with_mtg": total, "net_without_mtg": first_only, "list": listing}
        return summary, pos, cycles

    # ------------------------------------------------------------------ one trade, with the candles around it
    def detail(self, tid: str, names: dict[int, str] | None = None, forming: dict | None = None, before: int = 40, after: int = 8) -> dict | None:
        """Everything the trade window shows: the trade, the position it belongs to (orders on the same candle), its martingale cycle, the signal it followed
        and the 1-minute candles around it (`before` candles ahead of the entry candle, `after` behind the expiry)."""
        cols = ("id", "symbol", "direction", "amount", "open_t", "expire_t", "close_t", "open_price", "close_price", "payout", "profit", "status", "is_demo")
        row = self.db.execute(f"SELECT {', '.join(cols)} FROM broker_trades WHERE id=?", (tid,)).fetchone()
        if not row:
            return None
        me = dict(zip(cols, row))
        t = me["open_t"] or 0
        near = [dict(zip(cols, r)) for r in self.db.execute(
            f"SELECT {', '.join(cols)} FROM broker_trades WHERE symbol=? AND hidden=0 AND COALESCE(open_t, first_seen) BETWEEN ? AND ? ORDER BY COALESCE(open_t, first_seen)",
            (me["symbol"], t - 1500, t + 1500))]
        for r in near:
            r["t"] = r["open_t"] or 0
        self._tag_signals(near, names or {})
        _, pos, cycles = self._martingale_full(near)
        mine = next((r for r in near if r["id"] == tid), None) or me
        position = next((p for p in pos if any(r["id"] == tid for r in p["orders"])), None)
        cyc = cycles[position["cycle"]] if position else []

        def pack(p: dict) -> dict:
            return {"step": p["step"], "stake": p["stake"], "net": p["net"], "status": p["status"], "open_t": p["open_t"], "close_t": p["close_t"], "dir": p["dir"],
                    "open_price": p["orders"][0]["open_price"], "close_price": p["orders"][0]["close_price"],
                    "orders": [{"id": r["id"], "amount": r["amount"], "profit": r["profit"], "status": r["status"], "open_t": r["open_t"]} for r in p["orders"]]}
        end_t = me["close_t"] or me["expire_t"] or t + 60
        tf = 60
        first = int(t // tf * tf) - before * tf
        last = int(end_t // tf * tf) + after * tf
        candles = [list(c) for c in self.shared.execute("SELECT ts, o, h, l, c FROM candles WHERE symbol=? AND tf=? AND ts BETWEEN ? AND ? ORDER BY ts", (me["symbol"], tf, first, last))]
        if forming and first <= forming["t"] <= last and (not candles or candles[-1][0] < forming["t"]):
            candles.append([forming["t"], forming["o"], forming["h"], forming["l"], forming["c"]])
        move = None if me["open_price"] is None or me["close_price"] is None else round(me["close_price"] - me["open_price"], 6)
        favour = None if move is None or move == 0 or me["direction"] not in ("CALL", "PUT") else (move > 0) == (me["direction"] == "CALL")
        return {"trade": {**me, "source": mine.get("source", "own"), "signal": mine.get("signal")}, "position": position and pack(position),
                "cycle": [pack(p) for p in cyc], "tf": tf, "candles": candles, "window": [first, last],
                "facts": {"entry_offset_s": round(t - t // tf * tf, 1) if t else None, "duration_s": round(end_t - t, 1) if t else None, "move": move, "move_favours_you": favour,
                          "signal_lead_s": round(t - mine["signal"]["created"], 1) if mine.get("signal") else None}}
