"""Trading guard: a daily profit target, a daily loss limit, a losing-streak break, trade-size limits and an overtrading watch, computed from the trades you really
place on the broker (see broker_trades.py) and delivered as alerts through the same channel as the signal alerts (cards on the Quotex page, sound, desktop notice).

Read-only like everything else here: it never places, blocks or cancels a trade. It only tells you, at the moment it matters (a trade settles, a trade opens).

Day: your local calendar day (the browser sends its UTC offset), starting at `day_start_hour`.
Limits can be an amount in the account currency or a percentage of the balance you started the day with (balance now minus today's result).
Counting: a one-step martingale cycle (a lost first trade and its step) is ONE trade / ONE loss by default; clicks on the same pair, side and expiry are one position.
Alerts fire once per day per event (the `guard_alerts` table is the memory, so a restart never repeats them). Things that already happened more than 3 minutes ago are
recorded silently - turning the guard on never floods you with the past.
"""
from __future__ import annotations

import copy
import time
from datetime import datetime, timedelta, timezone

from . import control
from .broker_trades import BrokerTrades

SILENT_AFTER = 180.0                 # an event older than this when first seen is recorded, not alerted
SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "BRL": "R$", "JPY": "¥", "RUB": "₽", "TRY": "₺", "NGN": "₦", "PKR": "₨", "BDT": "৳",
           "IDR": "Rp", "MXN": "$", "CAD": "$", "AUD": "$", "ZAR": "R", "EGP": "E£", "KRW": "₩", "VND": "₫", "THB": "฿", "PHP": "₱"}

DEFAULTS: dict = {
    "enabled": True,
    "account": "real",                                       # which account is watched: real | demo | all
    "target": {"on": True, "value": 10.0, "unit": "percent"},    # daily profit target
    "loss": {"on": True, "value": 10.0, "unit": "percent"},      # daily loss limit
    "streak": {"on": True, "max": 3},                        # this many losses in a row -> take a break
    "cooldown_min": 15,
    "count_cycles": True,                                    # a lost first trade + lost martingale step = ONE loss
    "max_trades": {"on": False, "value": 30},                # trades per day
    "per_hour": {"on": True, "value": 12},                   # trades in any hour
    "after_target": {"extra_allowed": 2},                    # trades past the target before it is called overtrading
    "stake": {"on": True, "risk_pct": 5.0, "max_pct": 10.0, "max_amount": 0.0},   # risk per trade cycle / biggest single stake, in % of balance
    "mtg": {"on": True, "mult": 2.0},                        # you take one martingale step of this multiple
    "warn_at": 0.8,                                          # early warning at this share of a limit
    "day_start_hour": 0,
    "tz_min": 0,
    "sound": True,
}


def _merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        elif k in out:
            out[k] = v
    return out


def _num(v, lo: float, hi: float, default: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, x))


def clean_settings(s: dict) -> dict:
    """Clamp every setting to a sane range (the page and the API both go through this)."""
    o = copy.deepcopy(s)
    o["enabled"], o["sound"], o["count_cycles"] = bool(o["enabled"]), bool(o["sound"]), bool(o["count_cycles"])
    o["account"] = o["account"] if o["account"] in ("real", "demo", "all") else "real"
    for k in ("target", "loss"):
        o[k]["on"] = bool(o[k]["on"])
        o[k]["unit"] = "amount" if o[k]["unit"] == "amount" else "percent"
        o[k]["value"] = _num(o[k]["value"], 0, 1e9 if o[k]["unit"] == "amount" else 100, 10.0)
    o["streak"]["on"], o["streak"]["max"] = bool(o["streak"]["on"]), int(_num(o["streak"]["max"], 2, 20, 3))
    o["cooldown_min"] = int(_num(o["cooldown_min"], 0, 240, 15))
    o["max_trades"]["on"], o["max_trades"]["value"] = bool(o["max_trades"]["on"]), int(_num(o["max_trades"]["value"], 1, 1000, 30))
    o["per_hour"]["on"], o["per_hour"]["value"] = bool(o["per_hour"]["on"]), int(_num(o["per_hour"]["value"], 1, 200, 12))
    o["after_target"]["extra_allowed"] = int(_num(o["after_target"]["extra_allowed"], 0, 50, 2))
    st = o["stake"]
    st["on"], st["risk_pct"] = bool(st["on"]), _num(st["risk_pct"], 0.1, 50, 5.0)
    st["max_pct"], st["max_amount"] = _num(st["max_pct"], 0.1, 100, 10.0), _num(st["max_amount"], 0, 1e9, 0.0)
    o["mtg"]["on"], o["mtg"]["mult"] = bool(o["mtg"]["on"]), _num(o["mtg"]["mult"], 1.0, 10.0, 2.0)
    o["warn_at"] = _num(o["warn_at"], 0.5, 0.95, 0.8)
    o["day_start_hour"] = int(_num(o["day_start_hour"], 0, 23, 0))
    o["tz_min"] = int(_num(o["tz_min"], -840, 840, 0))
    return o


COLS = ("id", "symbol", "direction", "amount", "open_t", "expire_t", "close_t", "open_price", "close_price", "payout", "profit", "status", "is_demo")


class Guard:
    def __init__(self, engine, trades=None, db=None) -> None:
        """trades / db: this user's own trades and private database (default: the main ones, i.e. the admin's)."""
        self.engine = engine
        self.db = db if db is not None else engine.db
        self.trades = trades if trades is not None else engine.trades

    # ------------------------------------------------------------------ settings and state
    def settings(self) -> dict:
        return clean_settings(_merge(DEFAULTS, control.get_setting(self.db, "guard", {}) or {}))

    def save_settings(self, patch: dict) -> dict:
        s = clean_settings(_merge(self.settings(), patch))
        control.set_setting(self.db, "guard", s)
        return s

    def _state(self) -> dict:
        return control.get_setting(self.db, "guard_state", {}) or {}

    def _save_state(self, st: dict) -> None:
        control.set_setting(self.db, "guard_state", st)

    def stop_today(self, now: float | None = None) -> dict:
        """'I am done for today': any trade opened afterwards is flagged as overtrading."""
        now = float(now if now is not None else time.time())
        s = self.settings()
        _, _, day = self.window(now, s)
        st = self._state()
        st["manual_stop"] = {"day": day, "t": now}
        self._save_state(st)
        return {"day": day, "t": now}

    def resume(self) -> None:
        st = self._state()
        st.pop("manual_stop", None)
        st.pop("cooldown", None)
        self._save_state(st)

    # ------------------------------------------------------------------ the day
    @staticmethod
    def window(now: float, s: dict, back: int = 0) -> tuple[float, float, str]:
        """(start, end, 'YYYY-MM-DD') of the local trading day that contains `now`, `back` days earlier."""
        tz = s["tz_min"] * 60
        local = now + tz - s["day_start_hour"] * 3600
        midnight = (local // 86400) * 86400 - back * 86400
        start = midnight - tz + s["day_start_hour"] * 3600
        label = datetime.fromtimestamp(midnight + s["day_start_hour"] * 3600, tz=timezone.utc).strftime("%Y-%m-%d")
        return start, start + 86400, label

    def _rows(self, since: float, until: float, account: str) -> list[dict]:
        where = ["hidden = 0", "COALESCE(open_t, first_seen) >= ?", "COALESCE(open_t, first_seen) < ?"]
        if account == "real":
            where.append("COALESCE(is_demo, 0) = 0")
        elif account == "demo":
            where.append("is_demo = 1")
        out = []
        for r in self.db.execute(f"SELECT {', '.join(COLS)} FROM broker_trades WHERE {' AND '.join(where)} ORDER BY COALESCE(open_t, first_seen)", (since, until)):
            d = dict(zip(COLS, r))
            d["t"] = d["open_t"] or 0
            out.append(d)
        return out

    def balance(self, s: dict) -> float | None:
        a = self.trades.account_info()
        return (a.get("demo") if s["account"] == "demo" else a.get("live") if a.get("live") is not None else a.get("demo"))

    def money(self, x: float | None, sign: bool = False) -> str:
        cur = (self.trades.account_info().get("currency") or {})
        sym = SYMBOLS.get(cur.get("code") or "", cur.get("symbol") or ((cur.get("code") + " ") if cur.get("code") else ""))
        if x is None:
            return "–"
        return f"{'−' if x < 0 else '+' if sign and x > 0 else ''}{sym}{abs(x):,.2f}"

    # ------------------------------------------------------------------ the numbers of one day
    @staticmethod
    def _limit(cfg: dict, start_balance: float | None) -> float | None:
        if not cfg["on"] or not cfg["value"]:
            return None
        if cfg["unit"] == "amount":
            return float(cfg["value"])
        return None if not start_balance else start_balance * cfg["value"] / 100.0

    def compute(self, rows: list[dict], s: dict, start_balance: float | None, now: float, st: dict | None = None, live: bool = True) -> dict:
        """Everything the guard knows about one day. `live` adds the clock-dependent parts (trades in the last hour, the cooldown)."""
        _, pos, cycles = BrokerTrades._martingale_full([dict(r) for r in rows])
        settled_pos = sorted((p for p in pos if p["status"] != "open"), key=lambda p: p["close_t"] or p["open_t"])
        running = sum(1 for p in pos if p["status"] == "open")
        net = round(sum(p["net"] or 0 for p in settled_pos), 4)
        target, loss = self._limit(s["target"], start_balance), self._limit(s["loss"], start_balance)

        # running total by the time each position settled: when was the target / the loss limit first reached, and how high did the day get
        cum, peak, low, target_t, loss_t = 0.0, 0.0, 0.0, None, None
        for p in settled_pos:
            cum += p["net"] or 0
            peak, low = max(peak, cum), min(low, cum)
            t = p["close_t"] or p["open_t"]
            if target and target_t is None and cum >= target - 1e-9:
                target_t = t
            if loss and loss_t is None and cum <= -loss + 1e-9:
                loss_t = t

        # trades and the losing streak (a lost martingale cycle is one loss)
        units = ([c for c in cycles if all(p["status"] != "open" for p in c)] if s["count_cycles"] else [[p] for p in settled_pos])
        units = sorted(units, key=lambda c: max((p["close_t"] or p["open_t"]) for p in c))
        unit_net = [sum(p["net"] or 0 for p in c) for c in units]
        streak, first = 0, None
        for c, n in zip(reversed(units), reversed(unit_net)):
            if n < -1e-9:
                streak, first = streak + 1, c[0]["orders"][0]["id"]
            elif n > 1e-9:
                break
        streak_t = max((p["close_t"] or p["open_t"]) for p in units[-1]) if units and streak else None
        longest = cur = 0
        for n in unit_net:
            cur = cur + 1 if n < -1e-9 else (0 if n > 1e-9 else cur)
            longest = max(longest, cur)
        count = len(cycles) if s["count_cycles"] else len(pos)
        biggest = max((p["stake"] for p in pos), default=0.0)
        hour = sum(1 for p in pos if p["open_t"] >= now - 3600) if live else 0
        after_target = [p for p in pos if target_t is not None and p["open_t"] > target_t]
        after_loss = [p for p in pos if loss_t is not None and p["open_t"] > loss_t]
        stop = (st or {}).get("manual_stop") or {}
        after_manual = [p for p in pos if stop.get("t") and p["open_t"] > stop["t"]]
        cd = (st or {}).get("cooldown") or {}
        in_cooldown = [p for p in pos if cd.get("start") and cd["start"] < p["open_t"] < cd.get("until", 0)]
        return {"pos": pos, "cycles": cycles, "settled_pos": settled_pos, "running": running, "net": net, "target": target, "loss": loss, "peak": round(peak, 4), "low": round(low, 4),
                "target_t": target_t, "loss_t": loss_t, "streak": streak, "streak_first": first, "streak_t": streak_t, "longest_streak": longest, "count": count,
                "orders": len(rows), "biggest": biggest, "hour": hour, "after_target": after_target, "after_loss": after_loss, "after_manual": after_manual,
                "in_cooldown": in_cooldown, "cooldown": cd if cd.get("until", 0) > now else None, "units": len(units)}

    def max_stake(self, s: dict, balance: float | None) -> float | None:
        lim = [x for x in ((balance * s["stake"]["max_pct"] / 100.0) if balance else None, s["stake"]["max_amount"] or None) if x]
        return min(lim) if lim and s["stake"]["on"] else None

    def suggest_stake(self, s: dict, balance: float | None, c: dict) -> dict:
        """The stake to start the next trade with: what the risk budget allows (including the martingale step), cut down when a loss limit is near or a streak is running."""
        if not balance:
            return {"amount": None, "reason": "Waiting for your balance (reload the Quotex tab once)."}
        mult = (1 + s["mtg"]["mult"]) if s["mtg"]["on"] else 1.0
        base = balance * s["stake"]["risk_pct"] / 100.0 / mult
        amount, why = base, f"{s['stake']['risk_pct']:g}% of your balance may be at risk in one trade" + (f", spread over the first trade and its x{s['mtg']['mult']:g} step" if s["mtg"]["on"] else "")
        left = (c["loss"] + c["net"]) if c["loss"] else None                 # what is still allowed to lose today
        if (c["loss"] and c["loss_t"] is not None) or c.get("manual"):
            return {"amount": 0.0, "base": round(base, 2), "reason": "You are done for today - the guard suggests no more trades."}
        if left is not None and left / mult < amount:
            amount, why = max(0.0, left / mult), f"only {self.money(left)} is left of today's loss limit"
        if c["streak"] >= 2:
            amount, why = amount / 2, why + f"; halved because you have lost {c['streak']} in a row"
        if c["target_t"] is not None:
            why += "; you are past your target - the smaller, the safer"
            amount = amount / 2
        amount = round(max(0.0, amount) - 0.005, 2) if amount > 0.01 else 0.0
        return {"amount": max(0.0, amount), "base": round(base, 2), "reason": why, "worst_cycle": round(max(0.0, amount) * mult, 2), "step": round(max(0.0, amount) * s["mtg"]["mult"], 2) if s["mtg"]["on"] else None}

    # ------------------------------------------------------------------ alerts
    def _alerted(self, day: str) -> set[str]:
        return {k for (k,) in self.db.execute("SELECT key FROM guard_alerts WHERE day=?", (day,))}

    def check(self, now: float | None = None) -> list[dict]:
        """Look at today, record what is new and return the alert events to send (kind 'guard'). Called whenever order messages arrive."""
        s = self.settings()
        if not s["enabled"]:
            return []
        now = float(now if now is not None else time.time())
        start, end, day = self.window(now, s)
        st = self._state()
        if st.get("manual_stop", {}).get("day") not in (None, day):
            st.pop("manual_stop", None)
        rows = self._rows(start, end, s["account"])
        if not rows:
            return []
        bal = self.balance(s)
        c0 = self.compute(rows, s, None, now, st)                           # first pass for today's result, to know the balance the day started with
        sb = (bal - c0["net"]) if bal is not None else None
        c = self.compute(rows, s, sb, now, st)
        have = self._alerted(day)
        out: list[dict] = []

        def fire(key: str, level: str, rule: str, title: str, msg: str, trigger_t: float, chips: list | None = None, progress: float | None = None) -> None:
            if key in have:
                return
            have.add(key)
            silent = now - (trigger_t or now) > SILENT_AFTER
            self.db.execute("INSERT OR IGNORE INTO guard_alerts (ts, day, key, level, rule, title, message, silent) VALUES (?,?,?,?,?,?,?,?)",
                            (now, day, key, level, rule, title, msg, int(silent)))
            if not silent:
                out.append({"level": level, "rule": rule, "title": title, "message": msg, "chips": chips or [], "progress": progress, "sound": s["sound"]})

        m, t, ls = self.money, c["target"], c["loss"]
        chips = [{"l": "Today", "v": m(c["net"], True)}]
        if t:
            chips.append({"l": "Target", "v": m(t)})
        if ls:
            chips.append({"l": "Loss limit", "v": m(-ls)})
        warn = s["warn_at"]

        # target
        if t:
            prog = max(0.0, c["net"] / t)
            if c["target_t"] is not None:
                fire("target", "success", "target", "Daily target reached ✓", f"You are {m(c['net'], True)} today - your target was {m(t)}. This is the moment to stop and keep the win.", c["target_t"], chips, 1.0)
            elif prog >= warn and c["settled_pos"]:
                fire("target_near", "info", "target", "Close to your daily target", f"{round(prog * 100)}% of the way: {m(c['net'], True)} of {m(t)}. One more careful trade at most.", c["settled_pos"][-1]["close_t"] or now, chips, prog)
        # loss limit
        if ls:
            prog = max(0.0, -c["net"] / ls)
            if c["loss_t"] is not None:
                fire("loss_limit", "danger", "loss", "Daily loss limit reached - stop for today", f"You are {m(c['net'], True)} today and your limit is {m(-ls)}. Close the platform: trading on after this is how small losses become big ones.", c["loss_t"], chips, 1.0)
            elif prog >= warn and c["settled_pos"]:
                fire("loss_near", "warn", "loss", "Approaching your daily loss limit", f"{round(prog * 100)}% of the limit used: {m(c['net'], True)} of {m(-ls)}. Reduce the stake or stop.", c["settled_pos"][-1]["close_t"] or now, chips, prog)
        # losing streak -> break
        sm = s["streak"]["max"]
        if s["streak"]["on"] and c["streak"] >= sm - 1 and c["streak"] >= 2 and c["streak_first"]:
            n = c["streak"]
            label = "cycles" if s["count_cycles"] else "trades"
            if n >= sm:
                brk = f" Take a {s['cooldown_min']}-minute break before the next trade." if s["cooldown_min"] else ""
                fire(f"streak:{c['streak_first']}:{n}", "danger", "streak", f"{n} losses in a row", f"{n} lost {label} back to back.{brk} Chasing a loss is when trades get worse.", c["streak_t"] or now,
                     chips + [{"l": "Streak", "v": f"{n} lost"}], None)
                if s["cooldown_min"] and c["streak_t"] and now - c["streak_t"] <= SILENT_AFTER:
                    st["cooldown"] = {"start": c["streak_t"], "until": c["streak_t"] + s["cooldown_min"] * 60, "why": f"{n} losses in a row"}
                    self._save_state(st)
            else:
                fire(f"streak_warn:{c['streak_first']}:{n}", "warn", "streak", f"{n} losses in a row", f"One more lost {label[:-1] if label.endswith('s') else label} and the guard asks you to take a break.", c["streak_t"] or now, chips, n / sm)
        # trades after the target / the loss limit / a manual stop (overtrading)
        extra = s["after_target"]["extra_allowed"]
        for i, p in enumerate(c["after_target"], 1):
            if c["loss_t"] is not None and p["open_t"] > c["loss_t"]:
                continue
            level, title = ("warn", "You are past your target") if i <= extra else ("danger", "Overtrading after your target")
            given = c["peak"] - c["net"]
            tail = f" You have already given back {m(given)} of the day's high." if given > 0.005 and c["peak"] > 0 else ""
            fire(f"after_target:{i}", level, "overtrading", title, f"Trade {i} since reaching your target of {m(t)}.{tail} " + ("Consider stopping." if i <= extra else "More trades now are more likely to give the win back."), p["open_t"], chips + [{"l": "After target", "v": f"{i} trade{'s' if i > 1 else ''}"}], None)
        for i, p in enumerate(c["after_loss"], 1):
            fire(f"after_loss:{i}", "danger", "overtrading", "Trading past your loss limit", f"Trade {i} since the daily loss limit of {m(-ls)} was reached. This is the point the guard exists for - please stop.", p["open_t"], chips + [{"l": "After limit", "v": f"{i} trade{'s' if i > 1 else ''}"}], None)
        for i, p in enumerate(c["after_manual"], 1):
            fire(f"after_stop:{i}", "danger", "overtrading", "You said you were done for today", f"A new trade was opened after you chose to stop for today (trade {i}).", p["open_t"], chips, None)
        for p in c["in_cooldown"]:
            fire(f"cooldown:{p['orders'][0]['id']}", "warn", "cooldown", "You are still on your break", f"A trade was opened during the {s['cooldown_min']}-minute break after {st.get('cooldown', {}).get('why', 'a losing streak')}.", p["open_t"], chips, None)
        # volume
        if s["max_trades"]["on"] and c["count"] > s["max_trades"]["value"]:
            fire(f"max_trades:{c['count']}", "warn", "volume", "Too many trades today", f"{c['count']} trades today, your limit is {s['max_trades']['value']}. More trades means more spread and more mistakes.", now, chips, None)
        if s["per_hour"]["on"] and c["hour"] > s["per_hour"]["value"]:
            fire(f"hour:{int(now // 3600)}:{c['hour']}", "warn", "volume", "Trading very fast", f"{c['hour']} trades in the last hour (limit {s['per_hour']['value']}). Slow down and wait for your setup.", now, chips, None)
        # size
        cap = self.max_stake(s, bal)
        if cap:
            for p in c["pos"]:
                step_ok = p["step"] == 1 and s["mtg"]["on"]
                if p["stake"] > cap + 1e-9 and not step_ok:
                    sug = self.suggest_stake(s, bal, c)
                    fire(f"stake:{p['orders'][0]['id']}", "warn", "stake", "Stake above your limit", f"Stake {m(p['stake'])} is more than your limit of {m(cap)} ({s['stake']['max_pct']:g}% of your balance)." + (f" Suggested now: {m(sug['amount'])}." if sug.get("amount") else ""), p["open_t"], chips, None)
        self.db.commit()
        return out

    # ------------------------------------------------------------------ the page
    def status(self, now: float | None = None, tz_min: int | None = None) -> dict:
        now = float(now if now is not None else time.time())
        s = self.settings()
        if tz_min is not None and tz_min != s["tz_min"] and -840 <= tz_min <= 840:
            s = self.save_settings({"tz_min": tz_min})
        start, end, day = self.window(now, s)
        st = self._state()
        rows = self._rows(start, end, s["account"])
        bal = self.balance(s)
        c0 = self.compute(rows, s, None, now, st)
        sb = (bal - c0["net"]) if bal is not None else None
        c = self.compute(rows, s, sb, now, st)
        m = self.money
        t, ls, cap = c["target"], c["loss"], self.max_stake(s, bal)
        manual = (st.get("manual_stop") or {}).get("day") == day
        extra = s["after_target"]["extra_allowed"]
        over = len(c["after_target"]) > extra or len(c["after_loss"]) > 0 or (manual and len(c["after_manual"]) > 0)

        # overall state, most serious first
        if ls and c["loss_t"] is not None:
            state, head, advice = "stop", "Daily loss limit reached", ["Stop trading for today - the guard will flag any further trade.", "Come back tomorrow with a clear head and the same stake rules."]
        elif manual:
            state, head, advice = "stop", "You stopped for today", ["Any new trade will be flagged as overtrading."]
        elif over:
            state, head, advice = "overtrading", "Overtrading", [f"{len(c['after_target'])} trades since your target was reached." if c["after_target"] else "Trades after a stop.", "Stop now. The day's result is already good - protect it." if c["peak"] > 0 else "Stop and review."] + ([f"You are also on a break after {c['cooldown'].get('why', 'a losing streak')}."] if c["cooldown"] else [])
        elif c["cooldown"]:
            state, head, advice = "cooldown", "Cooling down", [f"Break until {datetime.fromtimestamp(c['cooldown']['until'] + s['tz_min'] * 60, tz=timezone.utc).strftime('%H:%M')} after {c['cooldown'].get('why', 'a losing streak')}.", "Step away from the screen; review your last trades instead."]
        elif t and c["target_t"] is not None:
            state, head, advice = "target", "Daily target reached", ["Stopping now keeps the win.", f"The guard allows {extra} more trade{'s' if extra != 1 else ''} before it calls it overtrading."]
        elif (ls and c["net"] < 0 and -c["net"] / ls >= s["warn_at"]) or (s["streak"]["on"] and c["streak"] >= max(2, s["streak"]["max"] - 1)):
            state, head, advice = "caution", "Be careful", ["You are close to a limit.", "Cut the stake or stop for a while."]
        else:
            state, head, advice = "ok", "On track", ["Within all your limits."]
        sug = self.suggest_stake(s, bal, {**c, "manual": manual})

        def rule(key, title, on, value, limit, progress, level, note, unit="money"):
            return {"key": key, "title": title, "on": on, "value": value, "limit": limit, "progress": progress, "level": level, "note": note, "unit": unit}

        rules = [
            rule("target", "Daily profit target", s["target"]["on"], c["net"], t, (max(0.0, c["net"] / t) if t else None), "hit" if c["target_t"] is not None else ("near" if t and c["net"] / t >= s["warn_at"] else "ok") if t else "wait",
                 "Reached" if c["target_t"] is not None else (f"{m(max(0.0, t - c['net']))} to go" if t else "Waiting for your balance to turn the % into an amount" if s["target"]["on"] and s["target"]["unit"] == "percent" else "Off")),
            rule("loss", "Daily loss limit", s["loss"]["on"], c["net"], ls, (max(0.0, -c["net"] / ls) if ls else None), "hit" if c["loss_t"] is not None else ("near" if ls and -c["net"] / ls >= s["warn_at"] else "ok") if ls else "wait",
                 "Reached - stop" if c["loss_t"] is not None else (f"{m(max(0.0, ls + c['net']))} left to lose today" if ls else "Waiting for your balance" if s["loss"]["on"] and s["loss"]["unit"] == "percent" else "Off")),
            rule("streak", "Losing streak", s["streak"]["on"], c["streak"], s["streak"]["max"], min(1.0, c["streak"] / s["streak"]["max"]) if s["streak"]["on"] else None,
                 "hit" if s["streak"]["on"] and c["streak"] >= s["streak"]["max"] else "near" if s["streak"]["on"] and c["streak"] >= max(2, s["streak"]["max"] - 1) else "ok" if s["streak"]["on"] else "off",
                 f"{c['streak']} lost in a row (longest today {c['longest_streak']})" if s["streak"]["on"] else "Off", "count"),
            rule("trades", "Trades today", s["max_trades"]["on"], c["count"], s["max_trades"]["value"], min(1.0, c["count"] / s["max_trades"]["value"]) if s["max_trades"]["on"] else None,
                 "hit" if s["max_trades"]["on"] and c["count"] > s["max_trades"]["value"] else "near" if s["max_trades"]["on"] and c["count"] / s["max_trades"]["value"] >= s["warn_at"] else "ok" if s["max_trades"]["on"] else "off",
                 f"{c['orders']} orders = {c['count']} {'cycles' if s['count_cycles'] else 'positions'}", "count"),
            rule("hour", "Trades in the last hour", s["per_hour"]["on"], c["hour"], s["per_hour"]["value"], min(1.0, c["hour"] / s["per_hour"]["value"]) if s["per_hour"]["on"] else None,
                 "hit" if s["per_hour"]["on"] and c["hour"] > s["per_hour"]["value"] else "near" if s["per_hour"]["on"] and c["hour"] / s["per_hour"]["value"] >= s["warn_at"] else "ok" if s["per_hour"]["on"] else "off",
                 "Speed check", "count"),
            rule("stake", "Biggest stake today", s["stake"]["on"], c["biggest"], cap, (min(1.0, c["biggest"] / cap) if cap else None), "hit" if cap and c["biggest"] > cap + 1e-9 else "ok" if cap else "wait" if s["stake"]["on"] else "off",
                 f"Limit {s['stake']['max_pct']:g}% of balance" if cap else "Waiting for your balance" if s["stake"]["on"] else "Off"),
        ]
        # the last 14 days
        days = []
        since = self.window(now, s, 13)[0]
        allrows = self._rows(since, end, s["account"])
        bal_run = bal
        for back in range(0, 14):
            a, b, label = self.window(now, s, back)
            drows = [r for r in allrows if a <= (r["open_t"] or 0) < b]
            cd0 = self.compute(drows, s, None, now, None, live=False)
            start_bal = (bal_run - cd0["net"]) if bal_run is not None else None
            cd = self.compute(drows, s, start_bal, now, None, live=False)
            if bal_run is not None:
                bal_run = start_bal
            if not drows:
                continue
            ex = len(cd["after_target"]) > extra or len(cd["after_loss"]) > 0
            days.append({"day": label, "net": cd["net"], "trades": cd["count"], "orders": cd["orders"], "target_hit": cd["target_t"] is not None, "loss_hit": cd["loss_t"] is not None,
                         "overtraded": ex, "longest_streak": cd["longest_streak"], "peak": cd["peak"], "after_target": len(cd["after_target"]), "after_loss": len(cd["after_loss"])})
        alerts = [{"ts": ts, "level": lv, "rule": ru, "title": ti, "message": ms} for (ts, lv, ru, ti, ms) in
                  self.db.execute("SELECT ts, level, rule, title, message FROM guard_alerts WHERE silent=0 ORDER BY id DESC LIMIT 40")]
        return {"settings": s, "day": day, "now": now, "state": state, "headline": head, "advice": advice, "over": over, "manual_stop": manual,
                "balance": {"value": bal, "start": sb, "account": s["account"], "currency": self.trades.account_info().get("currency")},
                "net": c["net"], "peak": c["peak"], "low": c["low"], "target": t, "loss": ls, "target_hit": c["target_t"] is not None, "loss_hit": c["loss_t"] is not None,
                "given_back": round(c["peak"] - c["net"], 2) if c["target_t"] is not None and c["peak"] > c["net"] else 0.0,
                "trades": {"orders": c["orders"], "count": c["count"], "positions": len(c["pos"]), "running": c["running"], "wins": sum(1 for p in c["settled_pos"] if p["status"] == "win"),
                           "losses": sum(1 for p in c["settled_pos"] if p["status"] == "loss")},
                "after_target": len(c["after_target"]), "after_loss": len(c["after_loss"]), "extra_allowed": extra, "streak": c["streak"], "cooldown": c["cooldown"],
                "stake": {"biggest": c["biggest"], "max": cap, "suggested": sug}, "rules": rules, "days": days, "alerts": alerts, "series": [[p["close_t"] or p["open_t"], round(n, 2)] for p, n in self._series(c)]}

    @staticmethod
    def _series(c: dict):
        cum = 0.0
        for p in c["settled_pos"]:
            cum += p["net"] or 0
            yield p, cum
