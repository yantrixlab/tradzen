"""Manual trade generator: on demand, ask every chosen strategy what it thinks about ONE pair/timeframe, combine the opinions into a single
graded trade idea (direction, grade, entry/expiry, reasons, higher-timeframe check) and show what history says about that level of agreement.

Honesty rules baked in: the 'conviction' is how strongly the strategies agree, never a win probability; the measured accuracy comes from settled
signals only and is shown with a confidence interval; a trade that is not clearly supported is reported as NO TRADE.
Generated trades are saved and settled automatically (same rule as signals: next candle open -> close), so the real hit-rate of the generator is measured too."""
from __future__ import annotations

import json
import time

from . import evaluator
from .candles import TFS, bucket
from .stats_util import wilson

USABLE = ("paper", "live", "paused")
GRADE_RANK = {"C": 1, "B": 2, "A": 3}
MIN_CANDLES = 60
LATE_FRACTION = 0.2          # entering later than this fraction of the candle changes the trade: warn
STALE_SECONDS = 90           # no tick for this long: feed is down, the analysis would be old
MIN_TRACK = 30               # settled signals needed before a strategy's own record changes its weight


class NotClosedYet(ValueError):
    """The candle right before the requested entry has not closed/arrived yet. `wait` = seconds until it should (negative: feed lag)."""
    def __init__(self, msg: str, wait: float) -> None:
        super().__init__(msg)
        self.wait = wait


# ------------------------------------------------------------------ helpers
def server_now(engine, sym: str) -> tuple[float, float | None]:
    """(estimated server time, seconds since the last tick for this pair or None)."""
    lt = engine.last_tick.get(sym)
    if lt:
        age = max(0.0, time.time() - lt["recv"])
        return lt["ts"] + age, age
    return time.time(), None


def breakeven(payout) -> float | None:
    return round(1 / (1 + payout / 100), 4) if payout else None


def _interval(wins: int, n: int) -> tuple[float | None, float | None]:
    if not n:
        return None, None
    lo, hi = wilson(wins, n)
    return round(lo, 4), round(hi, 4)


def _definitions(engine, scope: str, ids: list[int] | None) -> list[dict]:
    if ids:
        want = set(ids)
        return [d for d in engine.repo.definitions(only_active=False, system_only=True) if d["id"] in want and d["status"] in USABLE]
    if scope == "all":
        return [d for d in engine.repo.definitions(only_active=False, system_only=True) if d["status"] in USABLE]
    return list(engine.strategies.values())


def track_records(engine, tf: int) -> dict[int, tuple[int, int]]:
    """strategy id -> (wins, settled) on this timeframe, from settled signals."""
    rec: dict[int, list[int]] = {}
    for s in engine.signals:
        if s["tf"] == tf and s["status"] in ("win", "loss"):
            r = rec.setdefault(s["strategy_id"], [0, 0])
            r[0] += s["status"] == "win"
            r[1] += 1
    return {k: (v[0], v[1]) for k, v in rec.items()}


def calibration(engine, tf: int) -> dict:
    """Accuracy of past signals grouped by HOW MANY non-meta strategies agreed on the same pair/candle/direction.
    Answers 'does agreement actually help?' with evidence instead of belief."""
    meta_ids = {d["id"] for d in engine.strategies.values() if d["spec"]["kind"] == "meta"}
    groups: dict[tuple, list] = {}
    for s in engine.signals:
        if s["tf"] != tf or s["direction"] == "SKIP" or s["status"] not in ("win", "loss") or s["strategy_id"] in meta_ids:
            continue
        g = groups.setdefault((s["symbol"], s["basis_t"], s["direction"]), [set(), s["status"]])
        g[0].add(s["strategy_id"])
    buckets = {n: [0, 0] for n in (1, 2, 3, 4)}
    for ids, status in groups.values():
        b = buckets[min(len(ids), 4)]
        b[1] += 1
        b[0] += status == "win"
    payouts = [i["payout"] for i in engine.instruments.values() if i.get("payout")]
    rows = []
    for n, (w, t) in buckets.items():
        lo, hi = _interval(w, t)
        rows.append({"agree": n, "label": "4+" if n == 4 else str(n), "settled": t, "wins": w,
                     "accuracy": round(w / t, 4) if t else None, "ci_low": lo, "ci_high": hi})
    return {"tf": tf, "rows": rows, "breakeven": breakeven(sum(payouts) / len(payouts)) if payouts else None}


def _round(engine, sym: str, tf: int, rows: list[dict], syn: bool, defs: list[dict]) -> list[tuple[dict, object]]:
    """Evaluate the given strategies on `rows` (candles closed before the entry) for one timeframe; consensus strategies last."""
    window = rows[-400:]
    ctx = {"payout": engine.instruments.get(sym, {}).get("payout")}
    cache: dict = {}
    out, results = [], {}
    for d in defs:
        spec = d["spec"]
        if tf not in spec["timeframes"] or not engine._in_scope(spec, sym) or spec["kind"] == "meta":
            continue
        try:
            res = engine._gap_guard(evaluator.evaluate(spec, evaluator.tail_for(spec, rows, window), cache, ctx), syn)
        except Exception:                                   # one broken strategy must not break the generator
            continue
        results[d["key"]] = (d["name"], res)
        out.append((d, res))
    for d in defs:
        if d["spec"]["kind"] == "meta" and tf in d["spec"]["timeframes"] and len(window) >= MIN_CANDLES:
            out.append((d, engine._gap_guard(evaluator.consensus(d["spec"], results), syn)))
    return out


def _lean(votes: list[tuple[dict, object]], weights: dict[int, float]) -> tuple[str | None, float, float]:
    """(direction or None, call weight, put weight) of a list of evaluated strategies; consensus strategies do not vote twice."""
    call = sum(r.strength / 100 * weights.get(d["id"], 1.0) for d, r in votes if d["spec"]["kind"] != "meta" and r.direction == "CALL")
    put = sum(r.strength / 100 * weights.get(d["id"], 1.0) for d, r in votes if d["spec"]["kind"] != "meta" and r.direction == "PUT")
    if call == put:
        return None, call, put
    return ("CALL" if call > put else "PUT"), call, put


def _outcome(candle: dict, direction: str) -> dict:
    status = "draw" if candle["c"] == candle["o"] else "win" if (direction == "CALL") == (candle["c"] > candle["o"]) else "loss"
    return {"status": status, "entry": candle["o"], "exit": candle["c"]}


def _why(res) -> list[str]:
    """The strongest factor notes behind a strategy's vote, in plain words."""
    notes = [f["note"] for f in sorted(res.factors, key=lambda f: -abs(f["vote"]) * f["weight"]) if f["vote"] and f.get("note")]
    return notes[:2] or ([res.reason] if res.reason else [])


# ------------------------------------------------------------------ generate
def generate(engine, sym: str, tf: int, at: float | None = None, scope: str = "running", ids: list[int] | None = None,
             min_grade: str = "C", save: bool = True, now: float | None = None) -> dict:
    if tf not in TFS:
        raise ValueError(f"timeframe must be one of {list(TFS)}")
    if min_grade not in GRADE_RANK:
        raise ValueError("min_grade must be A, B or C")
    inst = engine.instruments.get(sym)
    series = engine._series(sym, tf)
    all_rows, _ = series.bridged_tail(tf)
    if not all_rows:
        raise ValueError("no candles are stored for this pair yet - open it on Quotex (extension on) so data flows in, then try again")
    srv_now, age = server_now(engine, sym)
    if now is not None:
        srv_now, age = now, 0.0
    last_closed = all_rows[-1]["t"]
    entry_t = bucket(at, tf) if at is not None else last_closed + tf
    if entry_t > last_closed + tf:
        wait = entry_t - srv_now
        if wait > 90:
            msg = f"that time is {int(wait // 60)} min in the future - the app cannot predict the future, it analyses candles that have already closed"
        elif wait > 0:
            msg = f"that entry is {int(wait) + 1}s away - the candle before it has not closed yet. Try again right after it closes"
        else:
            msg = "the previous candle has not arrived from the feed yet (a few seconds of lag is normal) - try again in a moment"
        raise NotClosedYet(msg, wait)
    rows = [r for r in all_rows if r["t"] + tf <= entry_t]
    if not rows or rows[-1]["t"] + tf != entry_t:
        raise ValueError("there is no closed candle directly before that time (data gap) - choose another time or let the feed fill in")
    syn = any(r.get("syn") for r in rows[-8:])
    existing = series.rows.get(entry_t)                    # entry candle already closed => this is a replay with a known outcome
    replay = existing is not None
    if scope == "ai":                                      # no predefined strategy: the AI builds one from the recent momentum (see momentum_ai.py)
        return _generate_ai(engine, sym, tf, inst, rows, syn, replay, existing, srv_now, age, entry_t, min_grade, save)
    defs = _definitions(engine, scope, ids)
    if not defs:
        raise ValueError("no strategies selected: switch some on (Paper/Live), or choose 'all strategies'")

    track = track_records(engine, tf)
    pay = inst.get("payout") if inst else None
    be = breakeven(pay)
    weights: dict[int, float] = {}
    for d in defs:
        w, n = track.get(d["id"], (0, 0))
        if n >= MIN_TRACK and be:
            lo, hi = wilson(w, n)
            weights[d["id"]] = 0.5 if hi < be else 1.5 if lo > be else 1.0

    votes = _round(engine, sym, tf, rows, syn, defs)
    direction, call_w, put_w = _lean(votes, weights)
    voters = [(d, r) for d, r in votes if d["spec"]["kind"] != "meta" and r.direction != "SKIP"]
    n_call = sum(r.direction == "CALL" for _, r in voters)
    n_put = len(voters) - n_call
    considered = sum(1 for d, _ in votes if d["spec"]["kind"] != "meta")
    agree_n, against_n = (n_call, n_put) if direction == "CALL" else (n_put, n_call)
    total_w = call_w + put_w
    margin = abs(call_w - put_w) / total_w if total_w else 0.0

    # higher / lower timeframe check: do the same strategies lean the same way on the other timeframes?
    mtf, opposed, aligned = [], False, 0
    for other in TFS:
        if other == tf:
            continue
        orows = [r for r in engine._series(sym, other).bridged_tail(other)[0] if r["t"] + other <= entry_t]
        if len(orows) < MIN_CANDLES:
            mtf.append({"tf": other, "calls": 0, "puts": 0, "lean": None, "note": "not enough candles"})
            continue
        osyn = any(r.get("syn") for r in orows[-8:])
        ov = _round(engine, sym, other, orows, osyn, defs)
        lean, _, _ = _lean(ov, weights)
        oc = sum(1 for d, r in ov if d["spec"]["kind"] != "meta" and r.direction == "CALL")
        op = sum(1 for d, r in ov if d["spec"]["kind"] != "meta" and r.direction == "PUT")
        mtf.append({"tf": other, "calls": oc, "puts": op, "lean": lean, "note": ""})
        if lean and direction:
            aligned += lean == direction
            opposed = opposed or lean != direction

    # decide
    warnings: list[str] = []
    block = ""
    if len(rows) < MIN_CANDLES:
        block = f"only {len(rows)} uninterrupted candles are stored before this time (strategies need {MIN_CANDLES}) - the feed needs to run longer without gaps"
    elif syn:
        block = engine.GAP_REASON
    elif not replay and (age is None or age > STALE_SECONDS):
        block = "the live feed is not updating (no tick for over %ds) - the analysis would be old" % STALE_SECONDS
    elif not replay and srv_now - entry_t >= tf:
        block = f"the latest closed candle is {int((srv_now - last_closed) // 60)} min old - recent candles are missing (feed gap), so the entry candle is already over"
    elif not voters:
        block = "none of the selected strategies has a signal on the last closed candle"
    elif direction is None:
        block = "the strategies are split evenly between CALL and PUT"
    elif against_n >= agree_n:
        block = f"the strategies disagree ({n_call} CALL vs {n_put} PUT) - no clear edge"
    if inst and not inst.get("open", True) and not replay:
        warnings.append("Quotex lists this pair as closed right now - check it is tradable before entering")
    conviction = 0
    grade = None
    verdict = "NO TRADE"
    if not block:
        conviction = round(100 * margin * min(1.0, agree_n / 3))
        if opposed:
            conviction = round(conviction * 0.7)
        if agree_n >= 3 and against_n == 0 and not opposed and conviction >= 60:
            grade = "A"
        elif agree_n >= 2 and margin >= 0.5 and not opposed:
            grade = "B"
        else:
            grade = "C"
        if opposed:
            warnings.append("another timeframe leans the opposite way")
        if GRADE_RANK[grade] < GRADE_RANK[min_grade]:
            block = f"grade {grade} is below your minimum ({min_grade})"
            grade = None
        else:
            verdict = direction
    if block:
        verdict = "NO TRADE"
    secs_in = srv_now - entry_t
    late = (not replay) and secs_in > tf * LATE_FRACTION
    if late and verdict != "NO TRADE":
        warnings.append(f"the entry candle started {int(secs_in)}s ago - the measured results assume entry at its open; wait for the next candle and generate again")
    if replay:
        warnings.append("replay: this candle has already closed - shown with what actually happened")

    cal = calibration(engine, tf)
    here = next((r for r in cal["rows"] if r["agree"] == min(max(agree_n, 1), 4)), None)
    if verdict != "NO TRADE" and here and here["settled"] < MIN_TRACK:
        warnings.append(f"only {here['settled']} past signals had this level of agreement - too few to say whether it helps")

    rows_out = []
    for d, r in sorted(votes, key=lambda x: (x[1].direction == "SKIP", -x[1].strength, x[0]["name"])):
        w, n = track.get(d["id"], (0, 0))
        lo, hi = _interval(w, n)
        rows_out.append({"id": d["id"], "key": d["key"], "name": d["name"], "status": d["status"], "meta": d["spec"]["kind"] == "meta",
                         "direction": r.direction, "tier": r.tier, "strength": r.strength, "reason": r.reason,
                         "why": _why(r) if r.direction != "SKIP" else [], "weight": weights.get(d["id"], 1.0),
                         "supports": bool(direction) and r.direction == direction, "opposes": bool(direction) and r.direction not in ("SKIP", direction),
                         "track": {"settled": n, "wins": w, "accuracy": round(w / n, 4) if n else None, "ci_low": lo, "ci_high": hi}})
    outcome = _outcome(existing, verdict) if replay and verdict in ("CALL", "PUT") else None
    headline = (f"{verdict} - grade {grade}" if verdict != "NO TRADE" else f"NO TRADE - {block}")
    out = {"symbol": sym, "name": inst["name"] if inst else sym, "market": inst["market"] if inst else ("OTC" if sym.endswith("_otc") else "LIVE"),
           "tf": tf, "payout": pay, "breakeven": be, "now": srv_now, "entry_t": entry_t, "expiry_t": entry_t + tf,
           "seconds_to_entry": round(entry_t - srv_now, 1), "late": late, "replay": replay, "data_age": age,
           "price": rows[-1]["c"], "last_closed": rows[-1]["t"], "candles": len(rows),
           "verdict": verdict, "grade": grade, "conviction": conviction, "headline": headline, "blocked": block or None,
           "counts": {"calls": n_call, "puts": n_put, "neutral": considered - len(voters), "considered": considered},
           "agree": agree_n, "against": against_n, "margin": round(margin, 3),
           "mtf": mtf, "mtf_aligned": aligned, "mtf_opposed": opposed, "warnings": warnings,
           "strategies": rows_out, "calibration": cal, "calibration_here": here, "outcome": outcome, "trade_id": None}
    if save and verdict in ("CALL", "PUT") and not replay:
        out["trade_id"] = _save(engine, out)
    return out


def _generate_ai(engine, sym, tf, inst, rows, syn, replay, existing, srv_now, age, entry_t, min_grade, save) -> dict:
    """The AI-built strategy: where did the recent momentum start, what does that kind of situation do next (learned from history, tested on unseen candles), what is the trade."""
    from . import momentum_ai
    pay = inst.get("payout") if inst else None
    be = breakeven(pay)
    warnings: list[str] = []
    block = ""
    if len(rows) < MIN_CANDLES:
        block = f"only {len(rows)} uninterrupted candles are stored before this time (need {MIN_CANDLES}) - the feed needs to run longer without gaps"
    elif syn:
        block = engine.GAP_REASON
    elif not replay and (age is None or age > STALE_SECONDS):
        block = "the live feed is not updating (no tick for over %ds) - the analysis would be old" % STALE_SECONDS
    elif not replay and srv_now - entry_t >= tf:
        block = f"the latest closed candle is {int((srv_now - rows[-1]['t']) // 60)} min old - recent candles are missing (feed gap), so the entry candle is already over"
    model = read = None
    if not block:
        model = momentum_ai.model_for(engine, tf)
        if not model["ok"]:
            block = model["why"]
        else:
            read = momentum_ai.read_now(rows, model, be)
            if not read["ok"]:
                block = read["why"]
    verdict, grade, conviction = "NO TRADE", None, 0
    ai: dict = {"model": None, "read": None}
    strategies: list[dict] = []
    if model and model["ok"]:
        ai["model"] = {k: model[k] for k in ("rows", "train", "valid", "span", "base_follow", "z", "breakeven", "built")}
        ai["model"]["leaves"] = [{k: lf[k] for k in ("rule", "side", "n_train", "n_valid", "acc_valid", "lo", "hi", "verdict")} for lf in model["leaves"]]
    if read and read["ok"]:
        lf = read["leaf"]
        edge = (lf["acc_valid"] or 0.5) - 0.5
        conviction = max(0, min(100, round(edge * 1000)))                     # 0 at a coin flip, 100 at +10 points of unseen-data accuracy
        grade = "A" if lf["verdict"] == "proven" else "B" if lf["verdict"] == "weak" and (lf["acc_valid"] or 0) >= 0.53 else "C"
        ai["read"] = {"origin": read["origin"], "state": read["state"], "leaf": {k: v for k, v in lf.items() if k != "path"}, "leg": read["leg"], "trade": read["trade"]}
        if grade == "C":
            warnings.append("the AI's own test found NO proven edge for this momentum situation - on candles it never saw this rule won about "
                            f"{(lf['acc_valid'] or 0) * 100:.1f}% (break-even {read['breakeven'] * 100:.1f}%). The trade below is its best lean, not a confirmed edge")
        if GRADE_RANK[grade] < GRADE_RANK[min_grade]:
            block = f"grade {grade} is below your minimum ({min_grade}) - the AI's test did not show enough edge for this situation"
            grade = None
        else:
            verdict = read["trade"]
        d_word = "up" if read["leg"] == "UP" else "down"
        strategies = [{"id": 0, "key": "ai-momentum", "name": "AI momentum model (built from recent data)", "status": "ai", "meta": False, "direction": verdict if verdict != "NO TRADE" else "SKIP",
                       "tier": grade or "C", "strength": conviction, "reason": f"{read['leg']}-leg from {read['origin']['candles_ago']} candles ago -> {lf['side']} it",
                       "why": [f"momentum started {read['origin']['candles_ago']} candles ago at {read['origin']['price']:.5g} ({d_word} leg)", f"rule: {lf['rule']}",
                               f"rule's {lf['side']} call won {(lf['acc_valid'] or 0) * 100:.1f}% on {lf['n_valid']:,} unseen candles"], "weight": 1.0, "supports": verdict != "NO TRADE", "opposes": False,
                       "track": {"settled": lf["n_valid"], "wins": lf["wins_valid"], "accuracy": lf["acc_valid"], "ci_low": lf["lo"], "ci_high": lf["hi"]}}]
    if block:
        verdict = "NO TRADE"
    secs_in = srv_now - entry_t
    late = (not replay) and secs_in > tf * LATE_FRACTION
    if late and verdict != "NO TRADE":
        warnings.append(f"the entry candle started {int(secs_in)}s ago - the measured results assume entry at its open; wait for the next candle and generate again")
    if replay:
        warnings.append("replay: this candle has already closed - shown with what actually happened (the AI learned from all stored candles, including this one, so this is not a fair test)")
    if inst and not inst.get("open", True) and not replay:
        warnings.append("Quotex lists this pair as closed right now - check it is tradable before entering")
    outcome = _outcome(existing, verdict) if replay and verdict in ("CALL", "PUT") else None
    out = {"symbol": sym, "name": inst["name"] if inst else sym, "market": inst["market"] if inst else ("OTC" if sym.endswith("_otc") else "LIVE"),
           "tf": tf, "payout": pay, "breakeven": be, "now": srv_now, "entry_t": entry_t, "expiry_t": entry_t + tf,
           "seconds_to_entry": round(entry_t - srv_now, 1), "late": late, "replay": replay, "data_age": age,
           "price": rows[-1]["c"], "last_closed": rows[-1]["t"], "candles": len(rows),
           "verdict": verdict, "grade": grade, "conviction": conviction, "headline": (f"{verdict} - grade {grade}" if verdict != "NO TRADE" else f"NO TRADE - {block}"), "blocked": block or None,
           "counts": {"calls": int(verdict == "CALL"), "puts": int(verdict == "PUT"), "neutral": 0, "considered": 1}, "agree": int(verdict != "NO TRADE"), "against": 0, "margin": 0.0,
           "mtf": [], "mtf_aligned": 0, "mtf_opposed": False, "warnings": warnings, "strategies": strategies, "calibration": {"rows": [], "breakeven": be}, "calibration_here": None,
           "outcome": outcome, "trade_id": None, "ai": ai}
    if save and verdict in ("CALL", "PUT") and not replay:
        out["trade_id"] = _save(engine, out)
    return out


# ------------------------------------------------------------------ saved trades
def _save(engine, g: dict) -> int:
    row = engine.db.execute("SELECT id FROM manual_trades WHERE symbol=? AND tf=? AND entry_t=? AND direction=?",
                            (g["symbol"], g["tf"], g["entry_t"], g["verdict"])).fetchone()
    if row:
        return row[0]
    payload = {"for": [s["name"] for s in g["strategies"] if s["supports"] and not s["meta"]],
               "against": [s["name"] for s in g["strategies"] if s["opposes"] and not s["meta"]],
               "mtf": [{"tf": m["tf"], "lean": m["lean"]} for m in g["mtf"]]}
    cur = engine.db.execute(
        "INSERT INTO manual_trades (created, symbol, tf, entry_t, direction, grade, conviction, agree, against, payout, status, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (time.time(), g["symbol"], g["tf"], g["entry_t"], g["verdict"], g["grade"], g["conviction"], g["agree"], g["against"], g["payout"], "pending", json.dumps(payload)))
    engine.db.commit()
    return cur.lastrowid


def history(engine, limit: int = 50) -> dict:
    """Saved trades (newest first) with automatic settlement, plus the measured record of the generator itself."""
    pending = engine.db.execute("SELECT id, symbol, tf, entry_t, direction FROM manual_trades WHERE status='pending'").fetchall()
    for tid, sym, tf, entry_t, direction in pending:
        c = engine._series(sym, tf).rows.get(entry_t)
        if c:
            o = _outcome(c, direction)
            engine.db.execute("UPDATE manual_trades SET status=?, entry=?, exit=? WHERE id=?", (o["status"], o["entry"], o["exit"], tid))
        elif server_now(engine, sym)[0] > entry_t + tf * 4 + 120:
            engine.db.execute("UPDATE manual_trades SET status='void' WHERE id=?", (tid,))     # candle never arrived (feed gap)
    if pending:
        engine.db.commit()
    rows = []
    for r in engine.db.execute("SELECT id, created, symbol, tf, entry_t, direction, grade, conviction, agree, against, payout, status, entry, exit, payload "
                               "FROM manual_trades ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)):
        p = json.loads(r[14] or "{}")
        inst = engine.instruments.get(r[2], {})
        rows.append({"id": r[0], "created": r[1], "symbol": r[2], "name": inst.get("name", r[2]), "tf": r[3], "entry_t": r[4], "direction": r[5],
                     "grade": r[6], "conviction": r[7], "agree": r[8], "against": r[9], "payout": r[10], "status": r[11], "entry": r[12], "exit": r[13],
                     "strategies": p.get("for", [])})
    by: dict[str, list[int]] = {}
    tot_w = tot_n = 0
    pays = []
    for grade, status, payout in engine.db.execute("SELECT grade, status, payout FROM manual_trades WHERE status IN ('win','loss')"):
        b = by.setdefault(grade or "?", [0, 0])
        b[0] += status == "win"
        b[1] += 1
        tot_w += status == "win"
        tot_n += 1
        if payout:
            pays.append(payout)
    lo, hi = _interval(tot_w, tot_n)
    draws = engine.db.execute("SELECT COUNT(*) FROM manual_trades WHERE status='draw'").fetchone()[0]
    pend = engine.db.execute("SELECT COUNT(*) FROM manual_trades WHERE status='pending'").fetchone()[0]
    summary = {"settled": tot_n, "wins": tot_w, "draws": draws, "pending": pend, "accuracy": round(tot_w / tot_n, 4) if tot_n else None,
               "ci_low": lo, "ci_high": hi, "breakeven": breakeven(sum(pays) / len(pays)) if pays else None,
               "by_grade": [{"grade": g, "settled": n, "wins": w, "accuracy": round(w / n, 4)} for g, (w, n) in sorted(by.items())]}
    return {"rows": rows, "summary": summary}


def delete_trade(engine, tid: int) -> bool:
    cur = engine.db.execute("DELETE FROM manual_trades WHERE id=?", (tid,))
    engine.db.commit()
    return cur.rowcount > 0
