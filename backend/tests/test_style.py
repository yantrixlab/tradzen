"""My style: honest verdicts, the market context of an entry, the habits learned from trades, and the live rule (fires once per candle, settles at the close, alerts only once earned)."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import style as S  # noqa: E402
from app.engine import Engine  # noqa: E402

T0 = 1_794_700_000 // 60 * 60
SYM = "A_otc"


def test_verdicts_are_cautious():
    be = 0.53
    assert S.verdict(7, 7, be) == "few"                                     # under 8 trades: no verdict, whatever the rate
    assert S.verdict(9, 10, be) == "promising"                              # above break-even, but the widened range still contains it
    assert S.verdict(5, 12, be) == "against"
    assert S.verdict(6, 12, be) == "weak"
    assert S.verdict(95, 100, be) == "proven"                               # only a long, clear record is proven
    assert S._p_better(0, 0, be) == 1.0 and S._p_better(60, 100, be) < 0.1 < S._p_better(55, 100, be)


def feed(eng, minutes=90, wobble=1.0):
    """Ticks for `minutes` closed 1-minute candles of SYM: an oscillating market, so the ATR is real."""
    eng.instruments[SYM] = {"symbol": SYM, "name": "A (OTC)", "payout": 90, "market": "OTC", "category": "", "open": True}
    for k in range(minutes):
        base = 100 + 3 * ((k // 3) % 2) + (k % 3) * 0.2
        t = T0 + k * 60
        for off, dp in ((0, 0), (15, wobble), (30, -wobble), (45, 0.5 * wobble), (59, 0.2)):
            eng.on_tick(SYM, float(t + off), base + dp)
    eng._flush(force=True)


def user_trade(eng, i, k, direction, win, fade_by=1.0, stake=1.0):
    """A trade the user made 15 s into candle k, against (fade_by>0) or with the candle's move so far."""
    cand = T0 + k * 60
    row = eng.db.execute("SELECT o, c FROM candles WHERE symbol=? AND tf=60 AND ts=?", (SYM, cand)).fetchone()
    entry = row[0] + (1.0 if direction == "PUT" else -1.0) * fade_by                      # PUT after price went UP inside the candle = a fade
    deal = {"id": f"u{i}", "asset": SYM, "amount": stake, "profit": 0.9 if win else -stake, "command": 0 if direction == "CALL" else 1, "isDemo": 0, "openPrice": entry,
            "closePrice": entry + (0.1 if win == (direction == "CALL") else -0.1), "openTimestamp": cand + 15, "closeTimestamp": cand + 60}
    eng.trades.on_item({"k": "o", "n": "s_orders/close", "dir": "in", "ts": cand + 61, "d": {"deals": [deal]}})


def rig(wins=8, fades=10):
    eng = Engine(Path(tempfile.mkdtemp()) / "s.db", seed_builtins=False)
    feed(eng)
    for i in range(fades):                                                                  # 10 trades that fade the move inside the candle, `wins` of them won (spaced out: no martingale)
        user_trade(eng, i, 40 + i * 4, "PUT" if i % 2 else "CALL", win=i < wins)
    return eng


def test_context_of_an_entry():
    eng = rig()
    rows = eng._series(SYM, 60).contiguous_tail(60)
    ctx = S.context_from(rows[:50], T0 + 50 * 60 + 15, 105.0, 103.0)
    assert ctx and abs(ctx["off"] - 15) < 1e-6 and ctx["inc"] > 0 and ctx["atr"] > 0 and ctx["last_col"] in (-1.0, 0.0, 1.0)
    assert abs(ctx["inc"] - (105.0 - 103.0) / ctx["atr"]) < 1e-3                          # the move so far, in ATRs of the closed candles
    assert S.context_from(rows[:10], T0, 100.0) is None                                   # too little history to describe the market


def test_habits_are_learned_from_the_trades_and_not_over_claimed():
    eng = rig(wins=8, fades=10)
    p = eng.style.profile()
    assert p["n"] == 10 and p["wins"] == 8 and p["steps"]["n"] == 0
    t = {x["key"]: x for x in p["traits"]}
    assert t["fade_move"]["n"] == 10 and t["fade_move"]["wins"] == 8 and t["follow_move"]["n"] == 0
    assert t["fade_move"]["verdict"] in ("promising", "weak") and t["fade_move"]["verdict"] != "proven"     # 8 of 10 is not proof
    assert t["fade_move"]["p"] < 0.15 and t["fade_move"]["p_corrected"] == min(1.0, t["fade_move"]["p"] * S.M_TESTED) and t["fade_move"]["p_corrected"] > 0.05   # looks good, but not after correcting for the habits compared
    assert any("None of your habits is statistically proven" in b for b in p["bullets"])
    rule = p["rule"]
    assert rule and rule["key"] == "fade_move" and rule["thr_atr"] >= 0.12 and rule["from_s"] <= 15 <= rule["to_s"] and rule["skip_pairs"] == []
    assert "OTHER way" in rule["text"]


def test_a_habit_that_loses_makes_no_rule():
    eng = rig(wins=3, fades=10)                                                              # 3 of 10: the habit is below break-even
    assert eng.style.profile()["rule"] is None


def test_the_live_rule_fires_once_per_candle_settles_and_alerts_only_when_allowed():
    eng = rig(wins=8, fades=10)
    st = eng.style
    prof = st.profile()
    thr = prof["rule"]["thr_atr"]
    k = 90
    cand = T0 + k * 60
    atr = st._refresh(SYM)["atr"] if st._refresh(SYM) else None
    assert atr and atr > 0
    seq0 = eng.seq
    eng.on_tick(SYM, float(cand), 100.0)                                                       # a new candle opens
    eng.on_tick(SYM, float(cand + 2), 100.0 + 2 * thr * atr)                                   # a big move, but only 2 s in: too early
    assert st.db.execute("SELECT COUNT(*) FROM style_signals").fetchone()[0] == 0
    eng.on_tick(SYM, float(cand + 18), 100.0 + 0.5 * thr * atr)                                # too small a move
    assert st.db.execute("SELECT COUNT(*) FROM style_signals").fetchone()[0] == 0
    eng.on_tick(SYM, float(cand + 22), 100.0 + 2 * thr * atr)                                  # the move the rule fades
    sig = st.db.execute("SELECT direction, entry_price, expire_t, status FROM style_signals").fetchall()
    assert sig == [("PUT", 100.0 + 2 * thr * atr, cand + 60, "pending")], sig                  # price went up: bet down
    eng.on_tick(SYM, float(cand + 23), 100.0 + 3 * thr * atr)                                  # the same candle again: no second signal
    assert st.db.execute("SELECT COUNT(*) FROM style_signals").fetchone()[0] == 1
    assert eng.seq == seq0, "paper only until the rule has earned alerts: nothing was sent to the Quotex page"
    assert st.alerts_open()[0] is False
    # the candle closes below the entry: the PUT wins
    eng.on_tick(SYM, float(cand + 38), 100.0 + 0.2)
    eng.on_tick(SYM, float(cand + 50), 100.0 - 1.0)
    eng.on_tick(SYM, float(cand + 61), 100.0)
    s = st.db.execute("SELECT status, exit_price FROM style_signals").fetchone()
    assert s[0] == "win" and abs(s[1] - (100.0 - 1.0)) < 1e-9 and st.record()["n"] == 1 and st.record()["wins"] == 1
    # with 'all' the same kind of signal is delivered as a normal signal card, and its result follows
    st.save_settings({"alert": "all"})
    for off in (0, 15, 30, 45, 59):                                                          # candle 91 passes with a steady stream of ticks
        eng.on_tick(SYM, float(T0 + 91 * 60 + off), 100.0)
    k = 92
    cand = T0 + k * 60
    eng.on_tick(SYM, float(cand), 100.0)
    eng.on_tick(SYM, float(cand + 11), 100.0)
    eng.on_tick(SYM, float(cand + 22), 100.0 - 2 * thr * atr)
    ev = [e for e in eng.notify_log if e["seq"] > seq0]
    assert len(ev) == 1 and ev[0]["kind"] == "signal" and ev[0]["direction"] == "CALL" and ev[0]["strategy"] == S.NAME and ev[0]["id"].startswith("st")
    assert ev[0]["expire_t"] == cand + 60 and ev[0]["tier_stats"]["n"] == 1 and ev[0]["breakeven"] and ev[0]["factors"]
    eng.on_tick(SYM, float(cand + 40), 100.0)
    eng.on_tick(SYM, float(cand + 55), 100.0 + 1.0)
    eng.on_tick(SYM, float(cand + 61), 100.0)
    res = [e for e in eng.notify_log if e["kind"] == "result" and e["id"] == ev[0]["id"]]
    assert len(res) == 1 and res[0]["status"] == "win" and res[0]["exit"] == 101.0


def test_a_lost_signal_gets_a_one_step_martingale_on_the_next_candle():
    eng = rig(wins=8, fades=10)
    st = eng.style
    thr, atr = st.profile()["rule"]["thr_atr"], st._refresh(SYM)["atr"]
    cand = T0 + 90 * 60

    eng.on_tick(SYM, float(cand), 100.0)
    eng.on_tick(SYM, float(cand + 11), 100.0)
    eng.on_tick(SYM, float(cand + 22), 100.0 + 2 * thr * atr)                            # price up: the rule bets PUT ...
    entry = 100.0 + 2 * thr * atr
    eng.on_tick(SYM, float(cand + 40), entry + 1)
    eng.on_tick(SYM, float(cand + 58), entry + 2)                                        # ... and the candle closes above: a loss
    eng.on_tick(SYM, float(cand + 60), entry + 2)                                        # next candle opens: the step is the same side (PUT) from the losing close
    row = st.db.execute("SELECT status, step_status, step_entry FROM style_signals").fetchone()
    assert row[0] == "loss" and row[1] == "pending" and abs(row[2] - (entry + 2)) < 1e-9, row
    assert st.mtg()["pending"] == 1 and st.mtg()["n"] == 0
    for off, px in ((72, entry + 1.5), (84, entry + 1), (100, entry + 0.5), (118, entry)):   # the step candle closes below its entry: the step wins
        eng.on_tick(SYM, float(cand + off), px)
    eng.on_tick(SYM, float(cand + 121), entry)
    row = st.db.execute("SELECT step_status, step_exit FROM style_signals").fetchone()
    assert row == ("win", entry), row
    m = st.mtg()
    pr = st.db.execute("SELECT payout FROM style_signals").fetchone()[0] / 100
    assert m["n"] == 1 and m["wins"] == 1 and m["pending"] == 0 and m["cycles"] == 1
    assert abs(m["net_flat"] - (-1)) < 1e-9 and abs(m["net_mtg"] - (-1 + S.MTG_MULT * pr)) < 1e-9, m
    assert eng.style.status()["recent"][0]["step"] == "win"


def test_old_lost_signals_are_backfilled_with_their_step():
    eng = rig()
    st = eng.style
    cand = T0 + 50 * 60                                                                  # a lost PUT on candle 50 whose step candle (51) is stored
    c50 = st.db.execute("SELECT c FROM candles WHERE symbol=? AND tf=60 AND ts=?", (SYM, cand)).fetchone()[0]
    c51 = st.db.execute("SELECT c FROM candles WHERE symbol=? AND tf=60 AND ts=?", (SYM, cand + 60)).fetchone()[0]
    st.db.execute("INSERT INTO style_signals (created, symbol, direction, entry_t, entry_price, expire_t, payout, status, exit_price) VALUES (1,?,?,1,1,?,90,'loss',?)", (SYM, "PUT", cand + 60, c50))
    st._backfill_steps()
    row = st.db.execute("SELECT step_status, step_entry, step_exit FROM style_signals").fetchone()
    assert row[1] == c50 and row[2] == c51 and row[0] == ("draw" if c51 == c50 else "win" if c51 < c50 else "loss")


def test_a_pair_where_you_lose_is_skipped_and_the_switch_works():
    eng = rig(wins=8, fades=10)
    st = eng.style
    cand = T0 + 90 * 60
    st.save_settings({"enabled": False})
    eng.on_tick(SYM, float(cand), 100.0)
    eng.on_tick(SYM, float(cand + 11), 100.0)
    eng.on_tick(SYM, float(cand + 22), 130.0)
    assert st.db.execute("SELECT COUNT(*) FROM style_signals").fetchone()[0] == 0, "disabled: no signals"
    st.save_settings({"enabled": True})
    r = st.profile()["rule"]
    r["skip_pairs"] = [SYM]                                                                  # as if the pair's own record were below break-even
    st._rule, st._rule_at = r, 1e18
    cand = T0 + 94 * 60
    eng.on_tick(SYM, float(cand), 100.0)
    eng.on_tick(SYM, float(cand + 11), 100.0)
    eng.on_tick(SYM, float(cand + 22), 130.0)
    assert st.db.execute("SELECT COUNT(*) FROM style_signals").fetchone()[0] == 0


def test_a_signal_on_a_discarded_candle_becomes_void():
    eng = rig()
    st = eng.style
    thr, atr = st.profile()["rule"]["thr_atr"], st._refresh(SYM)["atr"]
    cand = T0 + 90 * 60
    eng.on_tick(SYM, float(cand), 100.0)
    eng.on_tick(SYM, float(cand + 11), 100.0)
    eng.on_tick(SYM, float(cand + 22), 100.0 + 2 * thr * atr)                                # a signal ...
    assert st.record()["pending"] == 1
    eng.on_tick(SYM, float(cand + 55), 100.0)                                                # ... then 33 s without ticks: the candle is thrown away
    eng.on_tick(SYM, float(cand + 61), 100.0)
    assert st.db.execute("SELECT status FROM style_signals").fetchone()[0] == "pending"      # it cannot be settled by this candle
    for sec in range(62, 125, 10):                                                           # the next candle is complete and closes
        eng.on_tick(SYM, float(cand + sec), 100.0)
    eng.on_tick(SYM, float(cand + 185), 100.0)
    assert st.db.execute("SELECT status FROM style_signals").fetchone()[0] == "void" and st.record()["n"] == 0


def test_alert_policy_earned_needs_a_record():
    eng = rig()
    st = eng.style
    for i in range(30):                                                                      # 30 settled signals, 20 won (67 %)
        st.db.execute("INSERT INTO style_signals (created, symbol, direction, entry_t, entry_price, expire_t, payout, status, exit_price) VALUES (1,?,?,1,1,1,90,?,1)", (SYM, "CALL", "win" if i < 20 else "loss"))
    ok, why = st.alerts_open()
    assert ok and "earned" in why
    st.db.execute("UPDATE style_signals SET status='loss' WHERE id > 8")                     # 8 of 30: below break-even
    assert st.alerts_open()[0] is False
    st.save_settings({"alert": "off"})
    assert st.alerts_open() == (False, "alerts are off")
    st.save_settings({"alert": "promising"})                                                 # the old name still means 'earned'
    assert st.settings()["alert"] == "earned"


def test_status_for_the_page():
    eng = rig()
    s = eng.style.status()
    assert {"settings", "profile", "record", "recent", "alerting", "alert_note"} <= set(s) and s["profile"]["traits"] and s["record"]["n"] == 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
