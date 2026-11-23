"""Money management: the stake for the next trade after losses and wins, the caps, every stop condition, tiny balances, the ladder, the what-if and the page's data."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import moneymgmt as M  # noqa: E402
from app.engine import Engine  # noqa: E402

S = M.clean_settings(M.DEFAULTS)           # base 1%, cap 3%, recovery x3, loss limit 5%, target 5%, streak 4
P = 0.85                                   # a win pays 85%


def win(stake, p=P):
    return {"status": "win", "amount": stake, "profit": round(stake * p, 4), "payout": p * 100}


def loss(stake):
    return {"status": "loss", "amount": stake, "profit": -stake}


def adv(trades=(), bal=1000.0, s=None, **kw):
    kw.setdefault("payout", P)
    return M.advise(list(trades), bal, s or S, start_balance=kw.pop("start", None) or 1000.0, **kw)


def cfg(**patch):
    return M.clean_settings(M._merge(M.DEFAULTS, patch))


def test_base_stake_is_a_share_of_the_balance():
    a = adv()
    assert a["stake"] == 10.0 and a["status"] == "go" and a["kind"] == "base" and a["base"] == 10.0 and a["cap"] == 30.0
    assert adv(bal=1234.56, start=1234.56)["stake"] == 12.34 or adv(bal=1234.56, start=1234.56)["stake"] == 12.0      # whole units from 10 up, rounded DOWN
    assert adv(bal=1234.56, start=1234.56)["stake"] == 12.0
    assert adv(bal=500.0, start=500.0)["stake"] == 5.0
    assert M.floor_stake(9.999) == 9.99 and M.floor_stake(10.99) == 10.0 and M.floor_stake(0) == 0.0


def test_after_one_loss_the_stake_steps_up_gently_and_one_win_clears_it():
    a = adv([loss(10)], bal=990.0)
    # base 9.9 + debt 10 / (3 x 0.85) = 13.8
    assert 13 <= a["stake"] <= 14 and a["kind"] == "recovery" and a["debt"] == 10.0 and a["status"] == "go"
    win_profit = a["stake"] * P
    assert win_profit >= 10.0, "a win at the recovery stake takes back the one lost trade"
    b = adv([loss(10), win(a["stake"])], bal=990.0 + win_profit)
    assert b["kind"] != "recovery" and b["stake"] == M.floor_stake(b["base"]) and b["debt"] == 0.0           # debt cleared: back to the base stake


def test_recovery_climbs_with_the_losses_but_never_past_the_cap():
    trades, bal, last = [], 1000.0, 0.0
    for i in range(3):
        a = adv(trades, bal=bal)
        assert a["stake"] >= last and a["stake"] <= a["cap"] + 1e-9
        last = a["stake"]
        trades.append(loss(a["stake"]))
        bal -= a["stake"]
    # a big debt on a small base: the cap is the limit
    a = adv([loss(25), loss(25)], bal=950.0, s=cfg(loss={"pct": 50}, streak={"max": 10}, base_pct=1.0, max_pct=2.0))
    assert a["stake"] <= 19.0 and a["stake"] == M.floor_stake(950 * 0.02), a
    assert any("cap" in r.lower() for r in a["reasons"])


def test_partial_win_steps_the_stake_down_but_keeps_recovering():
    SX = cfg(streak={"max": 8}, max_pct=5.0, loss={"pct": 50})
    t = [loss(10), loss(14), loss(19)]
    bal = 1000 - 43
    big = adv(t, bal=bal, s=SX)
    t2 = t + [win(big["stake"])]
    after = adv(t2, bal=bal + big["stake"] * P, s=SX)
    assert after["stake"] < big["stake"] and after["debt"] < big["debt"], (big["stake"], after["stake"])


def test_recovery_off_stays_on_the_base_stake():
    a = adv([loss(10)], bal=990.0, s=cfg(recovery={"on": False}))
    assert a["stake"] == 9.9 and a["kind"] == "base" and any("Recovery is off" in r for r in a["reasons"])


def test_brakes_near_the_loss_limit_and_before_the_streak_limit():
    # 4 of the 5% limit used (3.5/5 = 70%+): brakes -> half the base
    a = adv([loss(20), loss(20)], bal=960.0, s=cfg(streak={"max": 10}))
    assert a["status"] == "careful" and a["kind"] == "brake" and a["stake"] == M.floor_stake(a["base"] / 2), a
    # three losses with a streak limit of 4: one more reaches the limit -> half the base, amber
    b = adv([loss(5), loss(5), loss(5)], bal=985.0, s=cfg(loss={"pct": 50}))
    assert b["status"] == "careful" and b["stake"] == M.floor_stake(b["base"] / 2) and "one more loss" in " ".join(b["reasons"])


def test_stop_at_the_daily_loss_limit_and_it_never_asks_for_more_than_is_left():
    a = adv([loss(25), loss(25)], bal=950.0, s=cfg(streak={"max": 10}))
    assert a["status"] == "stop" and a["kind"] == "loss_limit" and a["stake"] == 0.0
    # the room left caps the stake: 5% of 1000 = 50, 47 lost -> room 3
    b = adv([loss(47)], bal=953.0, s=cfg(streak={"max": 10}, min_stake=1.0))
    assert b["stake"] <= 3.0 and b["status"] in ("careful", "stop")
    # room below the broker's smallest stake = stop
    c = adv([loss(49.5)], bal=950.5, s=cfg(streak={"max": 10}, min_stake=1.0))
    assert c["status"] == "stop"
    assert adv([loss(25)] * 3, bal=925.0, s=cfg(loss={"on": False}, streak={"max": 10}))["status"] != "stop", "no limit when the limit is switched off"


def test_open_trades_use_up_the_loss_room():
    a = adv([loss(30)], bal=970.0, s=cfg(streak={"max": 10}), open_risk=18.0)
    assert a["room"] == 2.0 and a["stake"] <= 2.0


def test_stop_after_the_losing_streak():
    a = adv([loss(2), loss(2), loss(2), loss(2)], bal=992.0)
    assert a["status"] == "stop" and a["kind"] == "streak" and a["stake"] == 0.0
    b = adv([loss(2), loss(2), loss(2), win(2), loss(2)], bal=992.0)
    assert b["status"] != "stop", "a win breaks the streak"
    c = adv([loss(2), {"status": "draw", "amount": 2, "profit": 0.0}, loss(2), loss(2), loss(2)], bal=992.0)
    assert c["kind"] == "streak", "a draw does not break a streak"
    assert adv([loss(2)] * 6, bal=988.0, s=cfg(streak={"on": False}, loss={"on": False}))["status"] != "stop"


def test_stop_at_the_profit_target_and_protect_it_on_the_way():
    a = adv([win(30), win(30)], bal=1051.0)
    assert a["status"] == "stop" and a["kind"] == "target" and "keeps the win" in a["reason"]
    b = adv([win(30), win(10)], bal=1034.0)               # 25.5 + 8.5 = 34 of 50 = 68%: not yet; 36 of 50 would be
    assert b["status"] == "go"
    c = adv([win(30), win(15)], bal=1038.25)              # 38.25 of 50 = 76%: protect it
    assert c["status"] == "careful" and c["stake"] <= c["base"] * 0.5 + 1e-9 and "protect" in " ".join(c["reasons"])
    assert adv([win(30), win(30)], bal=1051.0, s=cfg(target={"on": False}))["status"] != "stop"


def test_winning_streak_keeps_the_stake_flat_and_giveback_slows_down():
    a = adv([win(5), win(5), win(5)], bal=1012.75)
    assert a["kind"] == "win_streak" and a["stake"] == 10.0 and "not a reason to bet more" in " ".join(a["reasons"])
    # best +24.65, then two losses of 10: gave back more than half of the peak
    b = adv([win(29), loss(10), loss(10)], bal=1004.65, s=cfg(target={"pct": 50}))
    assert b["status"] == "careful" and "gone" in " ".join(b["reasons"]), b


def test_below_break_even_win_rate_never_steps_up():
    a = adv([loss(10)], bal=990.0, recent=(45, 100))               # 45% < break-even 54%
    assert a["stake"] == 9.9 and any("below break-even" in r for r in a["reasons"])
    b = adv([loss(10)], bal=990.0, recent=(60, 100))
    assert b["stake"] > 9.0
    c = adv([loss(10)], bal=990.0, recent=(10, 20))              # too few trades to judge
    assert c["stake"] > 9.0


def test_tiny_and_missing_balances():
    assert adv(bal=None)["stake"] is None and adv(bal=None)["kind"] == "no_balance"
    assert adv(bal=0.0)["stake"] is None
    a = adv(bal=40.0, start=40.0)                           # 3% cap = 1.2 >= 1: the smallest stake is still allowed
    assert a["stake"] == 1.0 and a["status"] == "go"
    b = adv(bal=20.0, start=20.0)                           # cap 0.6 < smallest stake 1: advice says the balance is too small, never a bigger stake
    assert b["status"] == "stop" and b["kind"] == "small_balance" and b["stake"] == 0.0
    c = adv(bal=3.0, start=3.0, s=cfg(base_pct=10, max_pct=10, min_stake=1.0))
    assert 0 < c["stake"] <= 0.3 + 1e-9 or c["status"] == "stop"


def test_stake_never_exceeds_the_cap_or_the_balance():
    for bal in (35.0, 100.0, 987.65, 16067.67, 250000.0):
        for n in range(0, 5):
            for s in (S, cfg(base_pct=2, max_pct=2), cfg(max_pct=10, base_pct=5), cfg(recovery={"spread": 2})):
                a = M.advise([loss(bal * 0.01)] * n, bal, s, start_balance=bal, payout=P)
                if a["stake"]:
                    assert a["stake"] <= bal * s["max_pct"] / 100 + 1e-9 and a["stake"] <= bal, (bal, n, a)


def test_guard_stop_and_break_win():
    a = adv(manual_stop=True)
    assert a["status"] == "stop" and a["kind"] == "manual"
    b = adv(cooldown={"until": 9e9, "why": "3 losses in a row"})
    assert b["status"] == "stop" and b["kind"] == "cooldown" and "3 losses" in b["reason"]


def test_ladder_is_capped_and_ends_in_a_stop():
    lad = M.ladder(1000.0, S, P)
    stakes = [r["stake"] for r in lad]
    assert stakes[0] == 10.0 and lad[-1]["status"] == "stop" and all(x <= 30 for x in stakes)
    assert stakes[:3] == sorted(stakes[:3]) and stakes[3] < stakes[2] and lad[1]["win_net"] > 0, "one win after one loss comes out ahead"
    assert len(lad) == S["streak"]["max"] + 1 and lad[-1]["losses"] == S["streak"]["max"]
    assert lad[0]["lost_before"] == 0 and lad[2]["lost_before"] == round(stakes[0] + stakes[1], 2)


def test_what_if_the_next_trade_loses_or_wins():
    a = adv([loss(10)], bal=990.0)
    w = M.what_if([loss(10)], 990.0, S, a, payout=P, recent=(0, 0), manual_stop=False, cooldown=None)
    assert w["loss"]["stake"] > a["stake"] and w["win"]["stake"] < a["stake"] and w["win"]["kind"] == "base"
    assert w["loss"]["result"] == -a["stake"] and w["win"]["result"] > 0
    st = adv([loss(2)] * 4, bal=992.0)
    assert M.what_if([loss(2)] * 4, 992.0, S, st, payout=P, recent=(0, 0), manual_stop=False, cooldown=None) is None


def test_settings_are_clamped_and_never_risky():
    s = M.clean_settings(M._merge(M.DEFAULTS, {"base_pct": 99, "max_pct": 0.01, "loss": {"pct": 9999}, "streak": {"max": 1}, "recovery": {"spread": 99}, "account": "x", "min_stake": -5}))
    assert s["base_pct"] == 10 and s["max_pct"] == 10 and s["loss"]["pct"] == 50 and s["streak"]["max"] == 2 and s["recovery"]["spread"] == 6 and s["account"] == "real" and s["min_stake"] == 0.01
    s = M.clean_settings(M._merge(M.DEFAULTS, {"base_pct": 2.5, "max_pct": 1.0}))
    assert s["max_pct"] == 2.5, "the cap can never sit below the base stake"
    assert M.clean_settings(M._merge(M.DEFAULTS, {"base_pct": "abc", "nonsense": 1}))["base_pct"] == 1.0
    assert M.clean_settings(M.DEFAULTS) == M.DEFAULTS


def test_ledger_counts_debt_streak_and_peak():
    L = M.ledger([win(10), loss(4), loss(6), win(4), draw := {"status": "draw", "amount": 5, "profit": 0.0}])
    assert L["wins"] == 2 and L["losses"] == 2 and L["draws"] == 1 and L["streak"] == {"kind": "win", "n": 1}
    assert abs(L["debt"] - (10 - 3.4)) < 1e-6 and abs(L["net"] - (8.5 - 10 + 3.4)) < 1e-6 and abs(L["peak"] - 8.5) < 1e-6 and abs(L["low"] - (-1.5)) < 1e-6
    assert abs(M.payout_of([win(10, 0.8), win(10, 0.9)]) - 0.85) < 1e-9 and M.payout_of([]) == M.DEFAULT_PAYOUT


def test_a_profit_made_earlier_is_not_chased_back_after_a_later_loss():
    # +8.5 then -10: the day is only 1.5 below zero, so only 1.5 is owed (not 10)
    a = adv([win(10), loss(10)], bal=998.5)
    assert a["debt"] == 1.5 and a["kind"] == "recovery" and a["stake"] < 12.0
    # a win-loss-win day is above zero: nothing owed, base stake
    b = adv([win(10), loss(5), win(5)], bal=1007.75)
    assert b["debt"] == 0.0 and b["kind"] == "base"


# ---------------------------------------------------------------------------------------------------- end to end on a real engine
DAY = 1_794_650_000 // 86400 * 86400
T0 = DAY + 10 * 3600


class Rig:
    def __init__(self):
        self.eng = Engine(Path(tempfile.mkdtemp()) / "m.db", seed_builtins=False)
        self.eng.guard.save_settings({"tz_min": 0})
        self.m = M.MoneyMgmt(self.eng.db, self.eng.trades, self.eng.guard)
        self.n = 0
        self.live = 1000.0
        self.demo = 5000.0

    def balance(self, ts):
        self.eng.trades.on_balance({"k": "b", "n": "s_balance", "dir": "in", "ts": ts, "d": {"liveBalance": self.live, "demoBalance": self.demo, "currency": "INR"}})

    def trade(self, off, result, stake, demo=0, settle_after=60):
        self.n += 1
        t0 = T0 + off
        profit = round(stake * P, 2) if result == "win" else -stake
        if demo:
            self.demo += profit
        else:
            self.live += profit
        deal = {"id": f"m{self.n}", "asset": f"EURUSD{self.n}_otc", "amount": stake, "profit": profit if profit > 0 else -stake, "command": 0, "isDemo": demo, "openPrice": 1.0,
                "closePrice": 1.1, "openTimestamp": t0, "closeTimestamp": t0 + settle_after, "percentProfit": 85}
        self.balance(t0 + settle_after)
        self.eng.trades.on_item({"k": "o", "n": "s_orders/close", "dir": "in", "ts": t0 + settle_after + 1, "d": {"deals": [deal]}})
        return T0 + off + settle_after + 5


def test_status_follows_the_live_balance_and_trades():
    r = Rig()
    now = T0 - 100
    r.balance(now)
    s = r.m.status(now=now, tz_min=0)
    assert s["advice"]["stake"] == 10.0 and s["advice"]["status"] == "go" and s["today"]["trades"] == 0 and s["account"] == "real" and s["balance"]["value"] == 1000.0
    assert s["currency"]["code"] == "INR" and s["whatif"] and len(s["ladder"]) == 5 and s["recent"] == []
    now = r.trade(0, "loss", 10)
    s = r.m.status(now=now, tz_min=0)
    assert s["today"]["losses"] == 1 and s["today"]["net"] == -10.0 and s["streak"] == {"kind": "loss", "n": 1}
    assert s["advice"]["kind"] == "recovery" and s["advice"]["stake"] > 9.0 and s["advice"]["debt"] == 10.0
    assert s["limits"]["loss"]["limit"] == 50.0 and s["limits"]["loss"]["used"] == 10.0 and abs(s["limits"]["loss"]["pct"] - 0.2) < 1e-9
    assert [x["status"] for x in s["recent"]] == ["loss"] and s["edge"]["payout"] == 0.85
    now = r.trade(200, "win", s["advice"]["stake"])
    s = r.m.status(now=now, tz_min=0)
    assert s["advice"]["kind"] == "base" and s["today"]["net"] > 0 and s["streak"]["kind"] == "win" and s["advice"]["debt"] == 0.0


def test_demo_and_real_are_separate_accounts():
    r = Rig()
    now = r.trade(0, "loss", 50, demo=1)
    r.trade(100, "win", 10, demo=0)
    d = r.m.status(now=now + 200, tz_min=0, account="demo")
    assert d["account"] == "demo" and d["today"]["trades"] == 1 and d["today"]["losses"] == 1 and d["balance"]["value"] == 4950.0
    assert r.m.settings()["account"] == "demo", "the chosen account is remembered"
    real = r.m.status(now=now + 200, tz_min=0, account="real")
    assert real["today"]["trades"] == 1 and real["today"]["wins"] == 1 and real["balance"]["value"] == 1008.5


def test_the_day_follows_the_guard_and_yesterday_does_not_count():
    r = Rig()
    r.trade(-86400 + 100, "loss", 10)                                             # yesterday
    now = r.trade(0, "win", 10)
    s = r.m.status(now=now, tz_min=0)
    assert s["today"]["trades"] == 1 and s["today"]["wins"] == 1
    assert len(s["recent"]) == 2, "the strip shows the latest results of any day"


def test_guard_manual_stop_and_settings_persist():
    r = Rig()
    now = r.trade(0, "win", 10)
    r.eng.guard.stop_today(now)
    s = r.m.status(now=now + 1, tz_min=0)
    assert s["advice"]["status"] == "stop" and s["advice"]["kind"] == "manual"
    r.eng.guard.resume()
    saved = r.m.save_settings({"base_pct": 2, "loss": {"pct": 3}, "recovery": {"on": False}})
    again = M.MoneyMgmt(r.eng.db, r.eng.trades, r.eng.guard).settings()
    assert again == saved and again["base_pct"] == 2.0 and again["loss"]["pct"] == 3.0 and again["recovery"]["on"] is False and again["streak"]["max"] == 4
    assert r.m.status(now=now + 5, tz_min=0)["advice"]["base"] == round(r.live * 0.02, 2)


def test_warnings_for_a_losing_edge_and_running_trades():
    r = Rig()
    now = T0
    for i in range(40):                                                            # 14 wins / 26 losses: far below break-even
        now = r.trade(i * 100 - 86400 * 2, "win" if i % 3 == 0 else "loss", 1)
    r.balance(T0 + 10)
    s = r.m.status(now=T0 + 10, tz_min=0)
    assert any(w["kind"] == "edge" for w in s["warnings"]) and s["edge"]["enough"] and s["edge"]["win_rate"] < s["edge"]["break_even"]
    r.eng.trades.on_item({"k": "o", "n": "s_orders/open", "dir": "in", "ts": T0 + 20, "d": {"id": "open1", "asset": "XYZ_otc", "amount": 7, "command": 1, "isDemo": 0,
                                                                                          "openTimestamp": T0 + 20, "closeTimestamp": T0 + 80}})
    s = r.m.status(now=T0 + 30, tz_min=0)
    assert s["today"]["running"] == 1 and s["today"]["open_risk"] == 7.0 and any(w["kind"] == "open" for w in s["warnings"])


def test_the_route_is_open_to_every_role_behind_its_own_switch():
    from app import auth
    from app.feature_flags import BY_KEY
    assert auth.match("GET", "/api/moneymgmt") == ("any", "menu.moneymgmt") and auth.match("PUT", "/api/moneymgmt/settings") == ("any", "menu.moneymgmt")
    assert "menu.moneymgmt" in BY_KEY and BY_KEY["menu.moneymgmt"].min_role == "trader"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
