"""Trading guard: daily target, loss limit, losing streak + break, overtrading after a stop, stake limits, silent backfill, once-only alerts, settings and the page's data."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import guard as G  # noqa: E402
from app.engine import Engine  # noqa: E402

DAY = 1_794_650_000 // 86400 * 86400
BASE = DAY + 10 * 3600                     # 10:00 UTC on the test day
START = 1000.0


class Rig:
    """An engine with a known balance; trades are fed as the broker would send them and check() is called at the moment each one settles."""

    def __init__(self, **settings):
        self.eng = Engine(Path(tempfile.mkdtemp()) / "g.db", seed_builtins=False)
        self.g = self.eng.guard
        self.g.save_settings({"tz_min": 0, **settings})
        self.net = 0.0
        self.n = 0
        self.events = []

    def balance(self, ts):
        self.eng.trades.on_balance({"k": "b", "n": "s_balance", "dir": "in", "ts": ts, "d": {"liveBalance": START + self.net, "demoBalance": 10000}})

    def trade(self, profit, off, stake=50.0, sym=None, cmd=0, settle_delay=60):
        """A trade opened `off` seconds after 10:00 that settles a minute later with `profit` (negative = lost stake); returns the alerts it caused."""
        self.n += 1
        sym = sym or f"P{self.n}_otc"
        t0 = BASE + off
        self.net += profit
        deal = {"id": f"t{self.n}", "asset": sym, "amount": stake, "profit": profit if profit > 0 else -stake, "command": cmd, "isDemo": 0, "openPrice": 1.0,
                "closePrice": 1.1, "openTimestamp": t0, "closeTimestamp": t0 + settle_delay}
        if profit < 0:
            self.net += stake + profit                                   # a lost trade loses the stake, whatever number was passed
            self.net -= 0
        self.balance(t0 + settle_delay)
        self.eng.trades.on_item({"k": "o", "n": "s_orders/close", "dir": "in", "ts": t0 + settle_delay + 1, "d": {"deals": [deal]}})
        out = self.g.check(now=t0 + settle_delay + 1)
        self.events += out
        return out


def titles(evs):
    return [e["title"] for e in evs]


def test_settings_are_merged_and_clamped():
    r = Rig()
    s = r.g.save_settings({"target": {"value": 999999, "unit": "percent"}, "streak": {"max": 1}, "cooldown_min": -5, "account": "nonsense", "stake": {"max_pct": 0}})
    assert s["target"]["value"] == 100 and s["streak"]["max"] == 2 and s["cooldown_min"] == 0 and s["account"] == "real" and s["stake"]["max_pct"] == 0.1
    assert s["loss"]["unit"] == "percent" and s["enabled"] is True                        # untouched settings keep their defaults
    a, b, _ = r.g.window(BASE, {**s, "tz_min": 330, "day_start_hour": 0})                 # +05:30: the day starts at 18:30 UTC the evening before
    assert b - a == 86400 and a <= BASE < b and (a + 330 * 60) % 86400 == 0


def test_target_hit_then_overtrading_after_it():
    r = Rig(target={"on": True, "value": 90, "unit": "amount"}, loss={"on": False}, streak={"on": False}, per_hour={"on": False}, stake={"on": False}, **{"after_target": {"extra_allowed": 2}})
    assert r.trade(46, 0) == []                                                           # +46 of 90: not even the early warning yet (51 %)
    ev = r.trade(46, 400)                                                                 # +92: the target
    assert titles(ev) == ["Daily target reached ✓"] and ev[0]["level"] == "success" and "92.00" in ev[0]["message"] and "90.00" in ev[0]["message"]
    for off, profit in ((800, 46), (1200, -50), (1600, 46), (2000, 46), (2400, 46)):     # five trades after the target; 2 are allowed
        r.trade(profit, off)
    levels = [e["level"] for e in r.events if e["rule"] == "overtrading"]
    assert levels == ["warn", "warn", "danger", "danger", "danger"], levels              # beyond the 2 allowed it turns into a danger alert
    assert len({e["title"] + e["message"] for e in r.events}) == len(r.events), "no alert may repeat"
    st = r.g.status(now=BASE + 3000)
    assert st["state"] == "overtrading" and st["target_hit"] and st["after_target"] >= 3 and st["over"]
    assert r.g.check(now=BASE + 3001) == []                                               # checking again repeats nothing


def test_a_percent_limit_is_a_share_of_the_balance_the_day_started_with():
    r = Rig(target={"on": True, "value": 10, "unit": "percent"}, loss={"on": False}, streak={"on": False}, per_hour={"on": False}, stake={"on": False})
    ev = r.trade(46, 0)
    ev += r.trade(46, 400)                                                                # +92 of 100 (10 % of 1000) = 92 %: only the early warning
    assert titles(ev) == ["Close to your daily target"] and ev[0]["level"] == "info" and 0.9 < ev[0]["progress"] < 0.93
    ev = r.trade(46, 800)                                                                 # +138
    assert titles(ev) == ["Daily target reached ✓"]
    st = r.g.status(now=BASE + 900)
    assert st["target"] == 100.0 and abs(st["balance"]["start"] - START) < 1e-6


def test_loss_limit_and_trading_past_it():
    r = Rig(loss={"on": True, "value": 10, "unit": "percent"}, target={"on": False}, streak={"on": False}, per_hour={"on": False}, stake={"on": False})
    r.trade(-50, 0, sym="A_otc")
    ev = r.trade(-50, 400, sym="B_otc")                                                   # -100 = 10 % of 1000
    assert [e["rule"] for e in ev] == ["loss"] and ev[0]["level"] == "danger"
    ev = r.trade(46, 800, sym="C_otc")
    assert [e["rule"] for e in ev] == ["overtrading"] and "loss limit" in ev[0]["message"]
    st = r.g.status(now=BASE + 900)
    assert st["state"] == "stop" and st["loss_hit"] and st["stake"]["suggested"]["amount"] == 0.0


def test_warnings_before_the_limits():
    r = Rig(loss={"on": True, "value": 10, "unit": "percent"}, target={"on": True, "value": 10, "unit": "percent"}, streak={"on": False}, per_hour={"on": False}, stake={"on": False})
    ev = r.trade(-50, 0)                                                                  # 50 of 100 lost: no warning yet
    assert ev == []
    ev = r.trade(-40, 400, stake=40)                                                      # 90 % of the loss limit
    assert [e["title"] for e in ev] == ["Approaching your daily loss limit"] and ev[0]["level"] == "warn" and 0.85 < ev[0]["progress"] < 0.95


def test_losing_streak_asks_for_a_break_and_flags_trades_during_it():
    r = Rig(streak={"on": True, "max": 3}, cooldown_min=15, target={"on": False}, loss={"on": False}, per_hour={"on": False}, stake={"on": False})
    r.trade(-20, 0, stake=20)
    ev = r.trade(-20, 400, stake=20)
    assert [e["level"] for e in ev] == ["warn"] and ev[0]["title"] == "2 losses in a row"
    ev = r.trade(-20, 800, stake=20)
    assert [e["level"] for e in ev] == ["danger"] and ev[0]["title"] == "3 losses in a row" and "15-minute break" in ev[0]["message"]
    assert r.g._state()["cooldown"]["until"] > BASE + 800
    ev = r.trade(30, 900, stake=20)                                                       # a trade 2 minutes into the break
    assert [e["rule"] for e in ev] == ["cooldown"]
    st = r.g.status(now=BASE + 960)
    assert st["state"] == "cooldown" and st["cooldown"] and st["cooldown"]["why"] == "3 losses in a row"


def test_a_lost_martingale_cycle_counts_as_one_loss():
    r = Rig(streak={"on": True, "max": 3}, count_cycles=True, target={"on": False}, loss={"on": False}, per_hour={"on": False}, stake={"on": False})
    r.trade(-10, 0, stake=10, sym="X_otc")
    r.trade(-20, 62, stake=20, sym="X_otc")                                               # the step lost too: ONE lost cycle
    c = r.g.compute(r.g._rows(DAY, DAY + 86400, "real"), r.g.settings(), 1000, BASE + 200)
    assert c["streak"] == 1 and c["count"] == 1 and c["orders"] == 2
    r2 = Rig(streak={"on": True, "max": 3}, count_cycles=False, target={"on": False}, loss={"on": False}, per_hour={"on": False}, stake={"on": False})
    r2.trade(-10, 0, stake=10, sym="X_otc")
    r2.trade(-20, 62, stake=20, sym="X_otc")
    c2 = r2.g.compute(r2.g._rows(DAY, DAY + 86400, "real"), r2.g.settings(), 1000, BASE + 200)
    assert c2["streak"] == 2


def test_stake_and_speed_limits():
    r = Rig(stake={"on": True, "risk_pct": 5, "max_pct": 5, "max_amount": 0}, per_hour={"on": True, "value": 3}, target={"on": False}, loss={"on": False}, streak={"on": False})
    ev = r.trade(46, 0, stake=50)                                                         # exactly 5 % of ~1000: fine
    assert ev == []
    ev = r.trade(95, 100, stake=100)                                                      # 10 % of the balance
    assert [e["rule"] for e in ev] == ["stake"] and "Suggested" in ev[0]["message"]
    r.trade(46, 200, stake=50)
    ev = r.trade(46, 300, stake=50)                                                       # 4 trades inside an hour, the limit is 3
    assert "volume" in [e["rule"] for e in ev], titles(ev)


def test_the_past_is_recorded_silently_and_a_disabled_guard_is_quiet():
    r = Rig(target={"on": True, "value": 5, "unit": "percent"}, loss={"on": False}, streak={"on": False}, per_hour={"on": False}, stake={"on": False})
    r.n = 0
    for k, off in enumerate((0, 100)):
        r.n += 1
        deal = {"id": f"old{k}", "asset": f"Q{k}_otc", "amount": 50, "profit": 46, "command": 0, "isDemo": 0, "openPrice": 1, "closePrice": 2, "openTimestamp": BASE + off, "closeTimestamp": BASE + off + 60}
        r.net += 46
        r.eng.trades.on_item({"k": "o", "n": "orders/closed/list", "dir": "in", "ts": BASE + 200, "d": [deal]})
    r.balance(BASE + 200)
    assert r.g.check(now=BASE + 3 * 3600) == [], "an old target hit must not alert hours later"
    assert r.g.status(now=BASE + 3 * 3600)["target_hit"] is True and r.g.status(now=BASE + 3 * 3600)["alerts"] == []
    r.g.save_settings({"enabled": False})
    assert r.g.check(now=BASE + 3 * 3600 + 5) == []


def test_status_for_the_page_and_the_suggested_stake():
    r = Rig()
    st = r.g.status(now=BASE)
    assert st["state"] == "ok" and st["trades"]["orders"] == 0 and {x["key"] for x in st["rules"]} == {"target", "loss", "streak", "trades", "hour", "stake"}
    r.trade(46, 0)
    r.trade(-50, 400)
    st = r.g.status(now=BASE + 600)
    assert st["trades"]["wins"] == 1 and st["trades"]["losses"] == 1 and st["net"] == -4.0 and st["days"][0]["trades"] == 2
    sug = st["stake"]["suggested"]
    assert abs(sug["amount"] - (START - 4.0) * 0.05 / 3) <= 0.02 and sug["step"] == round(sug["amount"] * 2, 2)       # 5 % at risk over the first trade + its x2 step
    assert st["balance"]["value"] == START - 4.0 and abs(st["balance"]["start"] - START) < 1e-6
    r.g.stop_today(now=BASE + 700)
    st = r.g.status(now=BASE + 710)
    assert st["state"] == "stop" and st["manual_stop"]
    ev = r.trade(46, 800)
    assert [e["title"] for e in ev] == ["You said you were done for today"]
    r.g.resume()
    assert r.g.status(now=BASE + 900)["manual_stop"] is False


def test_the_engine_delivers_guard_alerts_to_the_extension():
    r = Rig(loss={"on": True, "value": 5, "unit": "percent"}, target={"on": False}, streak={"on": False}, per_hour={"on": False}, stake={"on": False})
    r.balance(BASE)
    deal = {"id": "z1", "asset": "A_otc", "amount": 80, "profit": -80, "command": 0, "isDemo": 0, "openPrice": 1, "closePrice": 2, "openTimestamp": BASE, "closeTimestamp": BASE + 60}
    import time as _t
    now = _t.time()
    deal.update(openTimestamp=now - 70, closeTimestamp=now - 10)
    r.eng.trades.on_balance({"k": "b", "n": "s_balance", "dir": "in", "ts": now, "d": {"liveBalance": START - 80, "demoBalance": 1}})
    r.g.save_settings({"tz_min": 0})
    out = r.eng.ingest({"items": [{"k": "o", "n": "s_orders/close", "dir": "in", "ts": now, "d": {"deals": [deal]}}], "after_seq": 0})
    ev = [e for e in out["events"] if e["kind"] == "guard"]
    assert ev and ev[0]["level"] == "danger" and ev[0]["rule"] == "loss" and ev[0]["title"].startswith("Daily loss limit")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
