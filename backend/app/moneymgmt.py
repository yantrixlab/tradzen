"""Money management: how much to stake on the NEXT trade, from your balance and the trades you really placed today.

Advice only. Nothing here ever places, changes or cancels a trade, and no stake plan turns a win rate below break-even into a profit: it only decides how fast
an account moves while you trade. What it does is keep every single trade small, step up only a little after a loss, step back down after wins and stop the day
before a bad run turns into a bad week.

The rules, in the order they are applied (each one is spelled out in the `reasons` the page shows):
  1. Base stake        = `base_pct` of the balance (default 1%).
  2. Recovery (optional) after a loss. A ledger of the money lost in the current recovery (`debt`) grows with every loss and shrinks with every win's profit; it starts
     at zero each trading day. While there is debt the stake is  base + debt / (spread x payout):  a win then earns a normal base profit PLUS about 1/spread of the
     debt, so after one loss a single win clears it and after a longer run it takes `spread` wins. Never open-ended: the stake is capped at `max_pct` of the balance.
  3. Brakes (stake is cut to half the base, never stepped up): the day's loss is near its limit, one more loss ends the streak limit, today's gain is near the target,
     or half of today's best profit was given back. Also never a step-up while your recent win rate is below the break-even of the payout.
  4. Never more than the room left under today's loss limit, never more than the cap, never more than the balance.
  5. Stop: the daily loss limit is reached, the losing-streak limit is reached, the daily profit target is reached, the Trading guard's "I'm done for today" is
     set (or its break is running), or the balance is too small for the smallest stake.

The day is the Trading guard's day (same start hour and time zone), so both pages always talk about the same "today".
"""
from __future__ import annotations

import copy
import math
import time
from datetime import datetime, timezone

from . import control

DEFAULTS: dict = {
    "account": "real",                                        # which account the page follows: real | demo
    "base_pct": 1.0,                                          # normal stake, % of the balance
    "max_pct": 3.0,                                           # hard cap for any single stake, % of the balance
    "min_stake": 1.0,                                         # the broker's smallest stake, in the account currency
    "recovery": {"on": True, "spread": 3},                    # step up a little after a loss; a win takes back 1/spread of the debt (+ a normal profit)
    "loss": {"on": True, "pct": 5.0},                         # daily loss limit, % of the balance the day started with
    "target": {"on": True, "pct": 5.0},                       # daily profit target, same base
    "streak": {"on": True, "max": 4},                         # this many losses in a row -> stop for today
}

NEAR_LOSS = 0.7            # this share of the daily loss limit used = brakes on
NEAR_TARGET = 0.7          # this share of the daily target reached = protect it
GIVE_BACK = 0.5            # this share of the day's best profit given back = brakes on
REDUCE = 0.5               # brakes: stake = this x the base
MIN_RATE_TRADES = 30       # trades needed before the win rate is judged against break-even
RATE_WINDOW = 100          # the win rate is measured over the last this-many settled trades
DEFAULT_PAYOUT = 0.85


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
    if math.isnan(x):
        return default
    return min(hi, max(lo, x))


def clean_settings(s: dict) -> dict:
    """Clamp every setting to a safe range: the page and the API both go through this, so a bad number can never make the advice risky."""
    o = copy.deepcopy(s)
    o["account"] = "demo" if o.get("account") == "demo" else "real"
    o["base_pct"] = round(_num(o.get("base_pct"), 0.1, 10.0, 1.0), 2)
    o["max_pct"] = round(max(o["base_pct"], _num(o.get("max_pct"), 0.1, 10.0, 3.0)), 2)         # the cap is never below the base stake
    o["min_stake"] = round(_num(o.get("min_stake"), 0.01, 1e6, 1.0), 2)
    o["recovery"]["on"], o["recovery"]["spread"] = bool(o["recovery"]["on"]), int(_num(o["recovery"]["spread"], 2, 6, 3))
    o["loss"]["on"], o["loss"]["pct"] = bool(o["loss"]["on"]), round(_num(o["loss"]["pct"], 0.5, 50.0, 5.0), 2)
    o["target"]["on"], o["target"]["pct"] = bool(o["target"]["on"]), round(_num(o["target"]["pct"], 0.5, 100.0, 5.0), 2)
    o["streak"]["on"], o["streak"]["max"] = bool(o["streak"]["on"]), int(_num(o["streak"]["max"], 2, 10, 4))
    return o


def floor_stake(x: float) -> float:
    """A stake you can type: whole units from 10 up, cents below. Always rounded DOWN, so rounding can never push it over the cap."""
    if x <= 0:
        return 0.0
    return float(math.floor(x + 1e-9)) if x >= 10 else math.floor(x * 100 + 1e-9) / 100.0


def payout_of(rows: list[dict]) -> float:
    """The payout of a win as a fraction (0.86 = 86%): the average of the latest wins, else DEFAULT_PAYOUT."""
    pays = []
    for r in rows:
        if r.get("status") != "win":
            continue
        p = r.get("payout")
        if p is None and r.get("amount") and r.get("profit"):
            p = r["profit"] / r["amount"] * 100
        if p:
            pays.append(p / 100.0)
    pays = pays[-20:]
    p = sum(pays) / len(pays) if pays else DEFAULT_PAYOUT
    return min(0.98, max(0.5, p))


def _loss_of(t: dict) -> float:
    pr = t.get("profit")
    return -pr if pr is not None and pr < 0 else float(t.get("amount") or 0)


def ledger(trades: list[dict]) -> dict:
    """Walk today's settled trades in order: the result, the recovery debt (`owed` = the part of it that today is really below zero), the current streak and the best / worst running result."""
    debt = cum = peak = low = 0.0
    kind, n, staked = None, 0, 0.0
    wins = losses = draws = 0
    for t in trades:
        st = t.get("status")
        staked += t.get("amount") or 0
        if st == "win":
            pr = t.get("profit") or 0.0
            cum += pr
            debt = max(0.0, debt - pr)
            wins += 1
            kind, n = ("win", n + 1) if kind == "win" else ("win", 1)
        elif st == "loss":
            ls = _loss_of(t)
            cum -= ls
            debt += ls
            losses += 1
            kind, n = ("loss", n + 1) if kind == "loss" else ("loss", 1)
        else:
            draws += 1                                        # a draw changes nothing: no debt, the streak goes on
        peak, low = max(peak, cum), min(low, cum)
    # what is still to win back is never more than what today is actually below zero: a profit made earlier today is not chased back after a later loss
    return {"net": round(cum, 4), "debt": round(debt, 4), "owed": round(min(debt, max(0.0, -cum)), 4), "streak": {"kind": kind, "n": n}, "peak": round(peak, 4), "low": round(low, 4),
            "n": len(trades), "wins": wins, "losses": losses, "draws": draws, "staked": round(staked, 2)}


def advise(trades: list[dict], balance: float | None, s: dict, *, payout: float = DEFAULT_PAYOUT, recent: tuple[int, int] = (0, 0), open_risk: float = 0.0,
           start_balance: float | None = None, manual_stop: bool = False, cooldown: dict | None = None) -> dict:
    """The advice for the next trade. Pure: `trades` = today's settled trades of the account, oldest first ({status, amount, profit, payout}); `balance` = what the account
    holds now; `recent` = (wins, settled trades) over the last RATE_WINDOW trades; `open_risk` = stakes of trades still running. Returns the stake (None when there is no
    balance), a status (go | careful | stop), its kind, a one-line reason and the steps behind it."""
    L = ledger(trades)
    out: dict = {"stake": None, "status": "stop", "kind": "no_balance", "headline": "Waiting for your balance", "reason": "Reload the Quotex tab once so the extension can read your balance.",
                 "reasons": [], "base": None, "cap": None, "debt": L["owed"], "room": None, "ledger": L}
    if not balance or balance <= 0:
        return out
    net = L["net"]
    start = start_balance if start_balance and start_balance > 0 else balance - net
    base = balance * s["base_pct"] / 100.0
    cap = balance * s["max_pct"] / 100.0
    mn = s["min_stake"]
    be = 1.0 / (1.0 + payout)
    loss_lim = start * s["loss"]["pct"] / 100.0 if s["loss"]["on"] else None
    tgt = start * s["target"]["pct"] / 100.0 if s["target"]["on"] else None
    used = max(0.0, -net)
    room = None if loss_lim is None else max(0.0, loss_lim - used - open_risk)
    streak_loss = L["streak"]["n"] if L["streak"]["kind"] == "loss" else 0
    streak_win = L["streak"]["n"] if L["streak"]["kind"] == "win" else 0
    smax = s["streak"]["max"] if s["streak"]["on"] else None
    out.update({"base": round(base, 2), "cap": round(cap, 2), "room": None if room is None else round(room, 2), "loss_limit": loss_lim, "target_amount": tgt,
                "payout": round(payout, 4), "break_even": round(be, 4), "start_balance": round(start, 2)})

    def stop(kind: str, head: str, why: str) -> dict:
        out.update({"stake": 0.0, "status": "stop", "kind": kind, "headline": head, "reason": why, "reasons": [why]})
        return out

    if manual_stop:
        return stop("manual", "You said you are done for today", "The Trading guard is set to \"done for today\". Resume there if you want to trade again.")
    if cooldown:
        return stop("cooldown", "Take your break", f"The Trading guard break is running ({cooldown.get('why') or 'after a losing streak'}). Step away, then come back with the base stake.")
    if loss_lim is not None and net <= -loss_lim + 1e-9:
        return stop("loss_limit", "Daily loss limit reached", f"Today is down {used:,.2f}, the limit is {loss_lim:,.2f} ({s['loss']['pct']:g}% of the day's start). Stop for today - tomorrow starts clean.")
    if smax is not None and streak_loss >= smax:
        return stop("streak", f"{streak_loss} losses in a row", f"{streak_loss} lost trades back to back reaches your limit of {smax}. Stop for today, or take a long break before the next trade.")
    if tgt is not None and net >= tgt - 1e-9:
        return stop("target", "Daily target reached", f"Today is up {net:,.2f}, the target is {tgt:,.2f} ({s['target']['pct']:g}%). Stopping now keeps the win.")
    if cap < mn:
        return stop("small_balance", "Balance too small for these limits", f"{s['max_pct']:g}% of your balance is {cap:,.2f}, below the smallest stake {mn:,.2f}. Raise the cap in the settings or add funds - the advice never goes over your cap.")
    if room is not None and room < mn:
        return stop("loss_limit", "Almost no loss room left", f"Only {room:,.2f} is left under today's loss limit, below the smallest stake {mn:,.2f}. Stop for today.")

    reasons = [f"Base stake: {s['base_pct']:g}% of your balance = {base:,.2f}."]
    stake, status, kind, head = base, "go", "base", "Go - base stake"
    wr_bad = recent[1] >= MIN_RATE_TRADES and recent[0] / recent[1] < be - 1e-9
    debt = L["owed"]

    # recovery: gentle, payout-aware, capped
    if s["recovery"]["on"] and debt > 1e-9:
        if wr_bad:
            reasons.append(f"You owe {debt:,.2f} from today's losses, but your recent win rate is below break-even, so the stake is NOT stepped up (chasing a negative edge costs more).")
        else:
            sp = s["recovery"]["spread"]
            need = base + debt / (sp * payout)
            stake = min(need, cap)
            kind, head = "recovery", "Recovery stake"
            reasons.append(f"Today's losses to win back: {debt:,.2f}. Stake = base + debt / ({sp} x {payout * 100:.0f}% payout) = {need:,.2f}: a win earns a normal profit plus about 1/{sp} of the debt (one win clears a single loss).")
            if need > cap + 1e-9:
                reasons.append(f"Limited to your cap of {cap:,.2f} ({s['max_pct']:g}% of the balance).")
    elif debt > 1e-9:
        reasons.append(f"Recovery is off: staying on the base stake after the {debt:,.2f} lost today.")

    # brakes: the stake drops to half the base and never steps up
    brakes: list[str] = []
    if loss_lim and used / loss_lim >= NEAR_LOSS:
        brakes.append(f"{used / loss_lim * 100:.0f}% of today's loss limit is used ({used:,.2f} of {loss_lim:,.2f}).")
    if smax is not None and streak_loss >= max(1, smax - 1) and streak_loss >= 1:
        brakes.append(f"{streak_loss} lost in a row - one more loss reaches your limit of {smax}.")
    if tgt and net / tgt >= NEAR_TARGET:
        brakes.append(f"You are {net / tgt * 100:.0f}% of the way to today's target ({net:,.2f} of {tgt:,.2f}) - protect it.")
    if L["peak"] > 0 and L["peak"] >= base * 2 and (L["peak"] - net) >= GIVE_BACK * L["peak"]:
        brakes.append(f"Half of today's best profit is gone (best {L['peak']:,.2f}, now {net:,.2f}).")
    if brakes:
        stake = min(stake, base * REDUCE)
        status, kind, head = "careful", "brake", "Careful - smaller stake"
        reasons += brakes + [f"Stake cut to {int(REDUCE * 100)}% of the base."]
    elif streak_loss >= 2:
        status = "careful"
    # a rising stake is never recommended while the win rate is below break-even
    if wr_bad and stake > base:
        stake = base
    if streak_win >= 3 and not brakes and debt <= 1e-9:
        kind, head = "win_streak", "Go - keep it steady"
        reasons.append(f"{streak_win} wins in a row. Keep the stake at the base: winning is not a reason to bet more.")
    elif streak_win >= 1 and debt > 1e-9 and kind == "recovery":
        reasons.append(f"{streak_win} win{'s' if streak_win != 1 else ''} since the last loss: the stake has stepped down as the debt shrank.")

    # hard limits
    stake = min(stake, cap)
    if room is not None and stake > room:
        stake = room
        status = "careful" if status == "go" else status
        reasons.append(f"Limited to {room:,.2f}: that is all that is left under today's loss limit, so even a loss stays inside it.")
    stake = floor_stake(stake)
    if stake < mn:
        lim = min(cap, room if room is not None else cap)
        if mn <= lim + 1e-9:
            stake = mn
            reasons.append(f"Raised to the smallest stake the broker accepts ({mn:,.2f}).")
        else:
            return stop("small_balance", "Balance too small for these limits", f"The stake the rules allow is below the smallest stake {mn:,.2f}. Stop, or raise the cap / loss limit in the settings.")
    stake = min(stake, floor_stake(cap), balance)
    if open_risk > 0:
        reasons.append(f"{open_risk:,.2f} is already running in open trades.")
    out.update({"stake": round(stake, 2), "status": status, "kind": kind, "headline": head, "reasons": reasons, "reason": _one_line(kind, stake, base, debt, s, streak_loss, streak_win, brakes)})
    return out


def _one_line(kind: str, stake: float, base: float, debt: float, s: dict, sl: int, sw: int, brakes: list[str]) -> str:
    if kind == "recovery":
        return f"{sl} lost in a row - a slightly bigger stake lets one win take back part of today's losses." if sl else "Still recovering today's losses - the stake steps down as you win."
    if kind == "brake":
        return brakes[0]
    if kind == "win_streak":
        return f"{sw} wins in a row - keep the stake at the base, do not chase."
    if stake > base + 0.005:
        return f"{s['base_pct']:g}% of your balance is {base:,.2f}, below the smallest stake the broker accepts - so the minimum, {stake:,.2f}."
    return f"Normal stake: {s['base_pct']:g}% of your balance."


def what_if(trades: list[dict], balance: float, s: dict, adv: dict, **ctx) -> dict | None:
    """What the advice becomes if the next trade (at the advised stake) loses, or wins at the current payout. None when the advice is to stop."""
    if not adv.get("stake") or adv["status"] == "stop":
        return None
    stk, p = adv["stake"], ctx.get("payout", DEFAULT_PAYOUT)
    res = {}
    for name, status, profit in (("loss", "loss", -stk), ("win", "win", round(stk * p, 4))):
        t2 = trades + [{"status": status, "amount": stk, "profit": profit, "payout": p * 100}]
        a = advise(t2, balance + profit, s, **{**ctx, "start_balance": adv.get("start_balance"), "open_risk": 0.0})
        res[name] = {"result": profit, "stake": a["stake"], "status": a["status"], "kind": a["kind"], "headline": a["headline"], "reason": a["reason"]}
    return res


def ladder(balance: float, s: dict, payout: float = DEFAULT_PAYOUT) -> list[dict]:
    """The stake after 0, 1, 2 ... losses in a row from a fresh day at this balance: what each step risks, what has been lost so far and what a win at that step brings."""
    rows, trades, bal = [], [], balance
    for i in range(0, 12):
        a = advise(trades, bal, s, payout=payout, start_balance=balance)
        stk = a["stake"] or 0.0
        lost = round(balance - bal, 2)
        rows.append({"losses": i, "stake": stk, "status": a["status"], "kind": a["kind"], "pct": round(stk / balance * 100, 2) if balance else 0.0,
                     "lost_before": lost, "win_net": round(stk * payout - lost, 2) if stk else None})
        if a["status"] == "stop" or not stk:
            break
        trades = trades + [{"status": "loss", "amount": stk, "profit": -stk}]
        bal -= stk
    return rows


class MoneyMgmt:
    def __init__(self, db, trades, guard=None) -> None:
        """db = this user's own database (the settings live there), trades = their BrokerTrades, guard = their Trading guard (same 'today', its break and manual stop)."""
        self.db, self.trades, self.guard = db, trades, guard

    # ------------------------------------------------------------------ settings
    def settings(self) -> dict:
        return clean_settings(_merge(DEFAULTS, control.get_setting(self.db, "moneymgmt", {}) or {}))

    def save_settings(self, patch: dict) -> dict:
        s = clean_settings(_merge(self.settings(), patch))
        control.set_setting(self.db, "moneymgmt", s)
        return s

    # ------------------------------------------------------------------ the day and the trades
    def _day(self, now: float, tz_min: int | None) -> tuple[float, float, str, dict]:
        if self.guard is not None:
            gs = self.guard.settings()
            if tz_min is not None and tz_min != gs["tz_min"] and -840 <= tz_min <= 840:
                gs = self.guard.save_settings({"tz_min": tz_min})
            a, b, label = self.guard.window(now, gs)
            return a, b, label, gs
        tz = int(tz_min or 0)
        a = ((now + tz * 60) // 86400) * 86400 - tz * 60
        return a, a + 86400, datetime.fromtimestamp(a + tz * 60, tz=timezone.utc).strftime("%Y-%m-%d"), {"tz_min": tz}

    def _rows(self, account: str, since: float | None = None, until: float | None = None, limit: int = 5000) -> list[dict]:
        where = ["hidden = 0"]
        args: list = []
        if account == "demo":
            where.append("is_demo = 1")
        else:
            where.append("COALESCE(is_demo, 0) = 0")
        if since is not None:
            where.append("COALESCE(open_t, first_seen) >= ?")
            args.append(since)
        if until is not None:
            where.append("COALESCE(open_t, first_seen) < ?")
            args.append(until)
        cols = ("id", "symbol", "direction", "amount", "open_t", "close_t", "payout", "profit", "status")
        sql = f"SELECT {', '.join(cols)}, COALESCE(close_t, open_t, first_seen) FROM broker_trades WHERE {' AND '.join(where)} ORDER BY COALESCE(open_t, first_seen) DESC LIMIT ?"
        out = []
        for r in self.db.execute(sql, (*args, limit)):
            d = dict(zip(cols, r))
            d["t"] = r[-1]
            out.append(d)
        return out[::-1]                                                         # oldest first

    def balance(self, account: str) -> tuple[float | None, float | None]:
        a = self.trades.account_info()
        return (a.get("demo"), a.get("demo_at")) if account == "demo" else (a.get("live"), a.get("live_at"))

    # ------------------------------------------------------------------ the page
    def status(self, now: float | None = None, tz_min: int | None = None, account: str | None = None) -> dict:
        now = float(now if now is not None else time.time())
        s = self.settings()
        if account in ("real", "demo") and account != s["account"]:
            s = self.save_settings({"account": account})
        acc = s["account"]
        start, end, day, gs = self._day(now, tz_min)
        allrows = self._rows(acc, limit=400)
        today = self._rows(acc, start, end)
        settled = [r for r in today if r["status"] in ("win", "loss", "draw")]
        settled.sort(key=lambda r: r["close_t"] or r["t"])
        running = [r for r in today if r["status"] == "open"]
        open_risk = round(sum(r["amount"] or 0 for r in running), 2)
        hist = [r for r in allrows if r["status"] in ("win", "loss", "draw")]
        last = [r for r in hist if r["status"] in ("win", "loss")][-RATE_WINDOW:]
        recent = (sum(1 for r in last if r["status"] == "win"), len(last))
        payout = payout_of(hist[-60:])
        bal, bal_at = self.balance(acc)
        gst = self.guard._state() if self.guard is not None else {}
        manual = (gst.get("manual_stop") or {}).get("day") == day
        cd = gst.get("cooldown") or {}
        cooldown = cd if cd.get("until", 0) > now else None
        ctx = dict(payout=payout, recent=recent, open_risk=open_risk, manual_stop=manual, cooldown=cooldown)
        adv = advise(settled, bal, s, **ctx)
        wi = what_if(settled, bal, s, adv, payout=payout, recent=recent, manual_stop=manual, cooldown=cooldown) if bal else None
        lad = ladder(bal, s, payout) if bal else []
        L = adv["ledger"]
        warnings: list[dict] = []
        be = 1.0 / (1.0 + payout)
        if recent[1] >= MIN_RATE_TRADES and recent[0] / recent[1] < be:
            warnings.append({"kind": "edge", "text": f"Your win rate over the last {recent[1]} trades is {recent[0] / recent[1] * 100:.1f}%, below the {be * 100:.1f}% break-even at a {payout * 100:.0f}% payout. "
                                                     "No stake plan can fix that - it only controls how fast the account moves. Step-ups are switched off until it is back above break-even."})
        if self.guard is not None and gs.get("per_hour", {}).get("on"):
            hour = sum(1 for r in today if (r["open_t"] or 0) >= now - 3600)
            if hour > gs["per_hour"]["value"]:
                warnings.append({"kind": "fast", "text": f"Trading very fast: {hour} trades in the last hour (your guard limit is {gs['per_hour']['value']}). Slow down and wait for your setup."})
        worst = lad[-1]["lost_before"] + (lad[-1]["stake"] if lad and lad[-1]["status"] != "stop" else 0) if lad else 0
        if adv.get("loss_limit") and worst > adv["loss_limit"] + 1e-9:
            warnings.append({"kind": "ladder", "text": f"A full losing streak by these settings would lose {worst:,.2f}, more than the daily loss limit of {adv['loss_limit']:,.2f}. The limit still stops it first, but you may prefer a lower base or streak limit."})
        if running:
            warnings.append({"kind": "open", "text": f"{len(running)} trade{'s' if len(running) != 1 else ''} still running ({open_risk:,.2f} at stake): the advice updates when {'they' if len(running) != 1 else 'it'} settle{'' if len(running) != 1 else 's'}."})
        if bal_at and now - bal_at > 900:
            warnings.append({"kind": "stale", "text": "Your balance was last seen more than 15 minutes ago - reload the Quotex tab so the advice uses the real figure."})
        recent_strip = [{"id": r["id"], "status": r["status"], "profit": r["profit"], "amount": r["amount"], "symbol": r["symbol"], "t": r["close_t"] or r["t"]} for r in hist[-20:]]
        loss_lim, tgt = adv.get("loss_limit"), adv.get("target_amount")
        return {"settings": s, "account": acc, "now": now, "day": day, "currency": self.trades.account_info().get("currency"),
                "balance": {"value": bal, "at": bal_at, "start": adv.get("start_balance"), "demo": self.trades.account_info().get("demo"), "real": self.trades.account_info().get("live")},
                "advice": {k: adv.get(k) for k in ("stake", "status", "kind", "headline", "reason", "reasons", "base", "cap", "room", "debt")},
                "today": {"net": L["net"], "trades": L["n"], "wins": L["wins"], "losses": L["losses"], "draws": L["draws"], "staked": L["staked"], "peak": L["peak"], "low": L["low"],
                          "drawdown": round(max(0.0, L["peak"] - L["net"]), 2), "running": len(running), "open_risk": open_risk},
                "streak": L["streak"],
                "limits": {"loss": {"on": s["loss"]["on"], "limit": loss_lim, "used": round(max(0.0, -L["net"]), 2), "pct": (min(1.0, max(0.0, -L["net"]) / loss_lim) if loss_lim else None)},
                           "target": {"on": s["target"]["on"], "amount": tgt, "got": round(max(0.0, L["net"]), 2), "pct": (min(1.0, max(0.0, L["net"]) / tgt) if tgt else None)},
                           "streak": {"on": s["streak"]["on"], "max": s["streak"]["max"], "n": L["streak"]["n"] if L["streak"]["kind"] == "loss" else 0}},
                "edge": {"payout": round(payout, 4), "break_even": round(be, 4), "win_rate": round(recent[0] / recent[1], 4) if recent[1] else None, "n": recent[1], "enough": recent[1] >= MIN_RATE_TRADES},
                "recent": recent_strip, "ladder": lad, "whatif": wi, "warnings": warnings}
