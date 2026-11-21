"""Your own broker trades: order events -> one row per trade, win / loss / draw with the right profit, and whether the trade followed a signal."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import broker_trades as bt  # noqa: E402
from app.db import open_db  # noqa: E402

T = 1_794_650_000.0


def fresh():
    db = open_db(Path(tempfile.mkdtemp()) / "t.db")
    return db, bt.BrokerTrades(db)


def ev(name, d, ts, dr="in"):
    return {"k": "o", "n": name, "dir": dr, "d": d, "ts": ts}


def test_settle_reads_profit_as_net_or_as_return():
    assert bt.settle(1, 0.8) == (0.8, "win")                       # net gain
    assert bt.settle(1, 1.8) == (0.8, "win")                       # whole return
    assert bt.settle(1, 1.0) == (0.0, "draw")                      # refund
    assert bt.settle(1, 0) == (-1.0, "loss")                       # lost stake reports 0
    assert bt.settle(1, 0, 1.2, 1.2) == (0.0, "draw")              # same price: nothing happened
    assert bt.settle(2, -2) == (-2, "loss")
    assert bt.settle(None, 1) == (None, "open")


def test_a_full_trade_from_request_to_result():
    db, b = fresh()
    b.on_item(ev("orders/open", {"asset": "AUDUSD_otc", "amount": 1, "time": T + 60, "action": "call", "isDemo": 0, "requestId": 7}, T, "out"))
    b.on_item(ev("s_orders/open", {"id": "abc-1", "openTime": T, "closeTime": T + 60}, T + 0.4))            # the answer lacks asset / amount / direction
    r = b.query()["trades"][0]
    assert (r["symbol"], r["direction"], r["amount"], r["status"], r["is_demo"]) == ("AUDUSD_otc", "CALL", 1.0, "open", 0)
    b.on_item(ev("s_orders/close", {"deals": [{"id": "abc-1", "profit": 0.83, "openPrice": 0.6774, "closePrice": 0.6776, "closeTimestamp": T + 60}]}, T + 60.5))
    q = b.query()
    r = q["trades"][0]
    assert (r["status"], r["profit"], r["close_price"], r["symbol"]) == ("win", 0.83, 0.6776, "AUDUSD_otc")
    assert q["summary"]["net"] == 0.83 and q["summary"]["wins"] == 1 and q["total"] == 1


def test_a_trade_seen_only_when_it_closed_and_a_repeat_changes_nothing():
    db, b = fresh()
    d = {"deals": [{"id": 5, "asset": "EURUSD_otc", "amount": 2, "command": 1, "profit": 0, "openPrice": 1.1, "closePrice": 1.2, "isDemo": 1, "closeTimestamp": T}]}
    b.on_item(ev("s_orders/close", d, T + 1))
    b.on_item(ev("s_orders/close", d, T + 2))
    q = b.query()
    assert q["total"] == 1 and q["trades"][0]["status"] == "loss" and q["trades"][0]["profit"] == -2.0 and q["trades"][0]["direction"] == "PUT"
    assert b.query("real")["total"] == 0 and b.query("demo")["total"] == 1
    # a late "opened" echo must not reopen a settled trade
    b.on_item(ev("s_orders/open", {"id": 5, "asset": "EURUSD_otc", "amount": 2}, T + 3))
    assert b.query()["trades"][0]["status"] == "loss"


def test_summary_pairs_days_and_streaks():
    db, b = fresh()
    for i, (p, a) in enumerate([(0.8, 1), (0.8, 1), (0, 1), (0, 1), (0, 1), (1.8, 1)]):
        b.on_item(ev("s_orders/close", {"deals": [{"id": i, "asset": "A_otc" if i % 2 else "B_otc", "amount": a, "profit": p, "command": 0,
                                                    "openTimestamp": T + i * 100, "closeTimestamp": T + i * 100 + 60}]}, T + i * 100 + 61))
    s = b.query()["summary"]
    assert (s["wins"], s["losses"], s["n"]) == (3, 3, 6) and s["net"] == round(0.8 * 3 - 3, 2) and s["win_rate"] == 0.5
    assert s["best_loss_streak"] == 3 and s["best_win_streak"] == 2 and s["streak"] == {"kind": "win", "n": 1}
    assert s["break_even"] == round(1 / 1.8, 4)
    q = b.query()
    assert {p["symbol"] for p in q["pairs"]} == {"A_otc", "B_otc"} and q["daily"][-1]["cum"] == s["net"]


def test_following_a_signal_is_recognised():
    db, b = fresh()
    sid = db.execute("SELECT id FROM strategies LIMIT 1").fetchone()[0]
    for created, direction in ((T - 20, "CALL"), (T + 5000 - 20, "PUT")):
        db.execute("INSERT INTO signals (created, symbol, tf, basis_t, target_t, direction, status, tier, strategy_id, mode) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (created, "AUDUSD_otc", 60, 0, 0, direction, "win", "B", sid, "live"))
    db.commit()
    for i, (dr, off) in enumerate((("call", 0), ("call", 5000), ("call", 9000))):
        b.on_item(ev("s_orders/close", {"deals": [{"id": i, "asset": "AUDUSD_otc", "amount": 1, "profit": 0.8, "command": 0 if dr == "call" else 1,
                                                    "openTimestamp": T + off, "closeTimestamp": T + off + 60}]}, T + off + 61))
    rows = {r["id"]: r for r in b.query(names={sid: "S"})["trades"]}
    assert rows["0"]["source"] == "followed" and rows["0"]["signal"]["strategy"] == "S"
    assert rows["1"]["source"] == "against"                            # a PUT signal was out, the trade was a CALL
    assert rows["2"]["source"] == "own"


def deal(i, sym, profit, t0, amount=1, cmd=1):
    return {"id": f"m{i}", "asset": sym, "amount": amount, "profit": profit, "command": cmd, "openPrice": 1.0, "closePrice": 1.1, "openTimestamp": t0, "closeTimestamp": t0 + 60 - t0 % 60}


def test_one_step_martingale_cycles():
    db, b = fresh()
    base = T - T % 60
    seq = [  # (pair, profit, open offset, stake)
        ("A_otc", -1, 0, 1),            # first trade loses ...
        ("A_otc", 0.9, 62, 1),          # ... two 1 $ clicks 2 s after the close = ONE step of 2 $ that wins
        ("A_otc", 0.9, 62, 1),
        ("B_otc", -1, 10, 1),           # first loses, step loses: the cycle ends there
        ("B_otc", -2, 62, 2),
        ("B_otc", 1.8, 125, 2),         # next trade after a lost step starts a NEW cycle (one step only)
        ("C_otc", -1, 20, 1),           # loss, nothing for 5 minutes: not a step
        ("C_otc", 0.9, 400, 1),
    ]
    for i, (sym, pr, off, st) in enumerate(seq):
        b.on_item(ev("s_orders/close", {"deals": [deal(i, sym, pr, base + off, st)]}, base + off + 60))
    m = b.query()["mtg"]
    assert m["positions"] == 7 and m["cycles"] == 5 and m["taken"] == 2
    assert (m["recovered"], m["failed"]) == (1, 1)
    assert m["step"]["staked"] == 4.0 and m["step"]["net"] == round(1.8 - 2, 2)
    assert m["first"]["n"] == 5 and m["first"]["losses"] == 3
    assert m["net_with_mtg"] == round(-1 + 1.8 - 1 - 2 + 1.8 - 1 + 0.9, 2) and m["net_without_mtg"] == round(-1 - 1 + 1.8 - 1 + 0.9, 2) == -0.3
    rows = {r["id"]: r for r in b.query()["trades"]}
    assert rows["m1"]["step"] == rows["m2"]["step"] == 1 and rows["m1"]["cycle"] == "m0" and rows["m0"]["step"] == 0 and rows["m5"]["step"] == 0
    assert m["list"][-1]["outcome"] in ("recovered", "failed", "lost", "first")


def test_account_balance_and_currency():
    db, b = fresh()
    assert b.account_info()["live"] is None and b.account_info()["currency"] is None
    b.on_balance({"k": "b", "n": "s_balance", "dir": "in", "ts": T, "d": {"liveBalance": 1523.5, "demoBalance": 10000, "__keys": []}})
    a = b.account_info()
    assert (a["live"], a["demo"]) == (1523.5, 10000.0)
    # a newer balance wins, an older one does not
    b.on_balance({"k": "b", "n": "s_balance", "dir": "in", "ts": T + 5, "d": {"liveBalance": 1600}})
    b.on_balance({"k": "b", "n": "s_balance", "dir": "in", "ts": T + 1, "d": {"liveBalance": 1}})
    assert b.account_info()["live"] == 1600.0
    # the currency comes from a deal ('INR'), a symbol-only or a {code, symbol} value
    b.on_item(ev("s_orders/open", {"id": 1, "asset": "A_otc", "amount": 1, "command": 0, "isDemo": 0, "currency": "inr", "accountBalance": 1650.25, "openTimestamp": T + 10}, T + 10))
    a = b.account_info()
    assert a["currency"] == {"code": "INR", "symbol": None} and a["live"] == 1650.25
    assert bt.parse_currency({"code": "USD", "symbol": "$"}) == {"code": "USD", "symbol": "$"} and bt.parse_currency("$") == {"code": None, "symbol": "$"}
    assert bt.parse_currency(5) is None and bt.parse_currency("") is None
    # it survives a restart (same database)
    again = bt.BrokerTrades(db)
    assert again.account_info()["live"] == 1650.25 and again.account_info()["currency"]["code"] == "INR"


def test_trade_detail_has_candles_position_and_cycle():
    db, b = fresh()
    base = T - T % 60
    for k in range(60):                                                  # a stored 1-minute candle per minute, the hour before the trade
        db.execute("INSERT INTO candles (symbol, tf, ts, o, h, l, c, ticks, source) VALUES (?,?,?,?,?,?,?,?,?)", ("A_otc", 60, base - 3600 + k * 60 + 3000, 1.0, 1.2, 0.9, 1.1, 9, "t"))
    db.commit()
    b.on_item(ev("s_orders/close", {"deals": [deal(1, "A_otc", -1, base + 5)]}, base + 61))
    for i in (2, 3):                                                     # the step: two clicks on the next candle
        b.on_item(ev("s_orders/close", {"deals": [deal(i, "A_otc", 0.9, base + 62)]}, base + 121))
    d = b.detail("m2")
    assert d["trade"]["id"] == "m2" and d["position"]["stake"] == 2.0 and len(d["position"]["orders"]) == 2
    assert [p["step"] for p in d["cycle"]] == [0, 1] and d["cycle"][0]["status"] == "loss"
    assert d["facts"]["entry_offset_s"] == 2.0 and d["tf"] == 60 and d["window"][0] < base < d["window"][1]
    assert all(d["window"][0] <= c[0] <= d["window"][1] for c in d["candles"]) and len(d["candles"]) > 3
    assert b.detail("nope") is None


def test_a_removed_trade_is_hidden_stays_hidden_and_can_be_restored():
    db, b = fresh()
    d = {"deals": [deal(1, "A_otc", 0.9, T), deal(2, "A_otc", -1, T + 100)]}
    b.on_item(ev("s_orders/close", d, T + 200))
    assert b.query()["total"] == 2 and b.query()["removed"] == 0
    assert b.delete("m1") is True and b.delete("m1") is False                       # the second time there is nothing left to remove
    q = b.query()
    assert q["total"] == 1 and q["removed"] == 1 and [r["id"] for r in q["trades"]] == ["m2"] and q["summary"]["net"] == -1.0
    b.on_item(ev("orders/closed/list", d, T + 300))                                 # the broker sends the same trades again (a reloaded Quotex tab)
    assert b.query()["total"] == 1, "a removed trade must not come back by itself"
    assert b.restore("m1") == 1 and b.restore("m1") == 0
    q = b.query()
    assert q["total"] == 2 and q["removed"] == 0 and q["summary"]["net"] == round(0.9 - 1, 2)
    b.delete("m1"); b.delete("m2")
    assert b.restore() == 2 and b.query()["total"] == 2                             # restore everything


def test_garbage_is_survived_and_logged():
    db, b = fresh()
    for d in (None, "x", [], {"deals": "no"}, {"id": 1, "amount": "n/a"}, {"deals": [{"id": 9, "profit": "?"}]}):
        b.on_item(ev("s_orders/close", d, T))
    assert b.raw(5)[0]["name"] == "s_orders/close"
    assert b.query()["summary"]["n"] == 0


def _closed(b, i, asset, amount, profit, at, call=True):
    b.on_item(ev("s_orders/close", {"deals": [{"id": i, "asset": asset, "amount": amount, "command": 0 if call else 1, "profit": profit, "openPrice": 1.0 + i / 100, "closePrice": 1.1, "isDemo": 0,
                                               "openTimestamp": at, "closeTimestamp": at + 60}]}, at + 61))


def test_a_date_range_and_the_table_order_are_applied_by_the_server():
    db, b = fresh()
    DAY = 86400
    _closed(b, 1, "EURUSD_otc", 5, 4.0, T)                      # three trades on three different days
    _closed(b, 2, "AUDUSD_otc", 1, -1.0, T + DAY)
    _closed(b, 3, "GBPUSD_otc", 3, 2.5, T + 2 * DAY)
    assert b.query()["total"] == 3
    day2 = b.query(since=T + DAY - 10, until=T + 2 * DAY - 10)      # 'yesterday' style range: opened at or after `since`, before `until`
    assert day2["total"] == 1 and day2["trades"][0]["symbol"] == "AUDUSD_otc" and day2["summary"]["net"] == -1.0
    assert b.query(until=T + DAY - 10)["total"] == 1 and b.query(since=T + DAY - 10)["total"] == 2
    order = lambda **k: [r["symbol"] for r in b.query(**k)["trades"]]
    assert order() == ["GBPUSD_otc", "AUDUSD_otc", "EURUSD_otc"]                                     # newest first by default
    assert order(sort="t", desc=False) == ["EURUSD_otc", "AUDUSD_otc", "GBPUSD_otc"]
    assert order(sort="profit", desc=True) == ["EURUSD_otc", "GBPUSD_otc", "AUDUSD_otc"]
    assert order(sort="amount", desc=False) == ["AUDUSD_otc", "GBPUSD_otc", "EURUSD_otc"]
    assert order(sort="symbol", desc=False) == ["AUDUSD_otc", "EURUSD_otc", "GBPUSD_otc"]
    assert b.query(sort="profit", desc=True, limit=1)["trades"][0]["symbol"] == "EURUSD_otc"          # the order applies before the page is cut
    assert b.query(sort="profit")["summary"]["net"] == b.query()["summary"]["net"] == 5.5            # sorting the table never changes the totals


def _close_at(b, tid, sym, amount, profit, close_t, demo=0):
    b.on_item(ev("s_orders/close", {"deals": [{"id": tid, "asset": sym, "amount": amount, "command": 0, "profit": profit, "openPrice": 1.0, "closePrice": 1.1, "isDemo": demo,
                                                "openTime": close_t - 60, "closeTimestamp": close_t}]}, close_t + 1))


def test_the_calendar_groups_by_your_own_day_and_ranks_the_pairs():
    db, b = fresh()
    day0 = 1_794_614_400                       # a UTC midnight (2026-11-14 00:00 UTC)
    # day 0 (UTC): +0.8, -1 on A; day 1: +1.6 on B; day 3: -1 -1 on A, +0.8 on C; a trade at 23:50 UTC on day 4 is already day 5 for someone at UTC+5:30
    _close_at(b, "a1", "A_otc", 1, 0.8, day0 + 3600)
    _close_at(b, "a2", "A_otc", 1, 0, day0 + 7200)
    _close_at(b, "b1", "B_otc", 2, 1.6, day0 + 86400 + 3600)
    _close_at(b, "a3", "A_otc", 1, 0, day0 + 3 * 86400 + 100)
    _close_at(b, "a4", "A_otc", 1, 0, day0 + 3 * 86400 + 200)
    _close_at(b, "c1", "C_otc", 1, 0.8, day0 + 3 * 86400 + 300)
    _close_at(b, "d1", "B_otc", 1, 1.0, day0 + 4 * 86400 + 23 * 3600 + 3000, demo=1)             # a draw on the demo account at 23:50 UTC
    cal = b.calendar("real", day0, day0 + 7 * 86400, 0)
    assert sorted(cal["days"]) == ["2026-11-14", "2026-11-15", "2026-11-17"], "days without trades are absent, demo is excluded"
    d0 = cal["days"]["2026-11-14"]
    assert (d0["n"], d0["wins"], d0["losses"], d0["net"], d0["best"], d0["worst"]) == (2, 1, 1, -0.2, 0.8, -1.0) and d0["win_rate"] == 0.5
    assert cal["days"]["2026-11-17"]["top_pair"] == {"symbol": "C_otc", "net": 0.8} and cal["days"]["2026-11-17"]["net"] == -1.2
    s = cal["summary"]
    assert (s["trading_days"], s["green_days"], s["red_days"], s["n"], s["net"]) == (3, 1, 2, 6, 0.2) and s["best_day"]["date"] == "2026-11-15" and s["worst_day"]["date"] == "2026-11-17"
    assert s["first_day"] == "2026-11-14" and s["last_day"] == "2026-11-17" and s["current_streak"] == {"kind": "red", "n": 1} and s["best_red_streak"] == 1
    assert [p["symbol"] for p in cal["pairs"]] == ["B_otc", "C_otc", "A_otc"], "pairs ranked by net"
    both = b.calendar("all", day0, day0 + 7 * 86400, 0)
    assert "2026-11-18" in both["days"] and both["days"]["2026-11-18"]["draws"] == 1 and both["summary"]["flat_days"] == 1
    ist = b.calendar("all", day0, day0 + 7 * 86400, 330)                                          # UTC+5:30: 23:50 UTC is 05:20 the next day
    assert "2026-11-19" in ist["days"] and "2026-11-18" not in ist["days"]
    narrow = b.calendar("real", day0 + 86400, day0 + 2 * 86400, 0)                                 # a range keeps only its own days
    assert list(narrow["days"]) == ["2026-11-15"] and narrow["summary"]["n"] == 1
    assert b.calendar("real", day0 + 10 * 86400, day0 + 20 * 86400, 0)["summary"]["trading_days"] == 0, "an empty range is a valid answer"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
