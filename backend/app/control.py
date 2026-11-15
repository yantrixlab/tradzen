"""Operator controls: global settings, bulk strategy status changes, pause-all / resume-all, presets, and 'what does every running strategy
say about this pair right now' (the signal board). Functions take the Engine as a parameter so this module has no import cycle with it."""
from __future__ import annotations

import json
import re

from . import evaluator

CONTROLLABLE = ("paper", "live", "paused")          # draft/archived strategies are never touched by bulk actions
BUILTIN_PRESETS = {
    "standard": "Default strategy live, everything else paper (the recommended setup)",
    "observe": "Everything paper: record and measure, no alerts",
    "off": "Everything paused: nothing runs",
}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-]{0,38}$")


# ------------------------------------------------------------------ settings
def get_setting(db, key: str, default=None):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_setting(db, key: str, value) -> None:
    db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
    db.commit()


def set_alerts_enabled(engine, enabled: bool) -> None:
    engine.alerts_enabled = bool(enabled)
    set_setting(engine.db, "alerts_enabled", bool(enabled))
    engine._log("alerts-master", "ON" if enabled else "OFF (all alerts suppressed)")


def settings_view(engine) -> dict:
    return {"alerts_enabled": engine.alerts_enabled, "can_resume": get_setting(engine.db, "pause_snapshot") is not None}


# ------------------------------------------------------------------ bulk status
def _controllable(engine, ids=None) -> list[dict]:
    return [s for s in engine.repo.list(system_only=True) if s["status"] in CONTROLLABLE and (ids is None or s["id"] in set(ids))]


def bulk_status(engine, ids: list[int] | None, status: str) -> dict:
    """Set `status` on the given strategies (or on every controllable one when ids is None)."""
    if status not in CONTROLLABLE:
        raise ValueError(f"status must be one of {list(CONTROLLABLE)}")
    targets = _controllable(engine, ids)
    changed = [s for s in targets if s["status"] != status]
    for s in changed:
        engine.repo.set_status(s["id"], status)
    engine.reload_strategies()
    if changed:
        engine._log("bulk-status", f"{len(changed)} strateg{'y' if len(changed) == 1 else 'ies'} -> {status}")
    return {"changed": len(changed), "unchanged": len(targets) - len(changed), "status": status}


def pause_all(engine) -> dict:
    """Pause everything that is running, remembering what each strategy was so resume_all can restore it exactly."""
    running = {str(s["id"]): s["status"] for s in _controllable(engine) if s["status"] in ("paper", "live")}
    if running and get_setting(engine.db, "pause_snapshot") is None:       # pausing twice must not overwrite the real snapshot
        set_setting(engine.db, "pause_snapshot", running)
    r = bulk_status(engine, None, "paused")
    return {**r, "can_resume": get_setting(engine.db, "pause_snapshot") is not None}


def resume_all(engine) -> dict:
    snap = get_setting(engine.db, "pause_snapshot")
    if not snap:
        raise ValueError("nothing to resume: strategies were not paused with 'Pause all'")
    restored = 0
    for sid, status in snap.items():
        s = next((x for x in engine.repo.list(system_only=True) if x["id"] == int(sid)), None)
        if s and s["status"] == "paused" and status in ("paper", "live"):
            engine.repo.set_status(s["id"], status)
            restored += 1
    set_setting(engine.db, "pause_snapshot", None)
    engine.db.execute("DELETE FROM settings WHERE key='pause_snapshot'")
    engine.db.commit()
    engine.reload_strategies()
    engine._log("resume-all", f"{restored} strategies restored")
    return {"restored": restored}


# ------------------------------------------------------------------ presets
def list_presets(engine) -> dict:
    custom = get_setting(engine.db, "presets", {})
    return {"builtin": [{"name": k, "description": v} for k, v in BUILTIN_PRESETS.items()],
            "custom": [{"name": k, "count": len(v)} for k, v in sorted(custom.items())]}


def save_preset(engine, name: str) -> dict:
    name = (name or "").strip()
    if not NAME_RE.match(name) or name.lower() in BUILTIN_PRESETS:
        raise ValueError("preset name: 1-39 letters, digits, spaces, dashes; must not be 'standard', 'observe' or 'off'")
    custom = get_setting(engine.db, "presets", {})
    custom[name] = {str(s["id"]): s["status"] for s in _controllable(engine)}
    set_setting(engine.db, "presets", custom)
    return {"name": name, "count": len(custom[name])}


def delete_preset(engine, name: str) -> bool:
    custom = get_setting(engine.db, "presets", {})
    if name not in custom:
        return False
    del custom[name]
    set_setting(engine.db, "presets", custom)
    return True


def apply_preset(engine, name: str) -> dict:
    controllable = _controllable(engine)
    if name in BUILTIN_PRESETS:
        wanted = {s["id"]: ("paused" if name == "off" else "paper" if name == "observe" else "live" if s["id"] == engine.strategy_id else "paper")
                  for s in controllable}
    else:
        custom = get_setting(engine.db, "presets", {})
        if name not in custom:
            raise ValueError(f"unknown preset '{name}'")
        wanted = {int(k): v for k, v in custom[name].items() if v in CONTROLLABLE}
    changed = 0
    for s in controllable:
        target = wanted.get(s["id"])
        if target and target != s["status"]:
            engine.repo.set_status(s["id"], target)
            changed += 1
    engine.reload_strategies()
    engine._log("preset", f"'{name}' applied, {changed} strategies changed")
    return {"preset": name, "changed": changed}


# ------------------------------------------------------------------ signal board
def readiness(engine, sym: str, tf: int) -> dict:
    """How close every running strategy is to a signal on this pair RIGHT NOW: each strategy is evaluated as if the still-forming candle closed as it is
    (see readiness.py). Cheap enough to poll every second or two: cached for a second, and the indicators are shared between strategies."""
    import time
    from . import readiness as rd
    series = engine._series(sym, tf)
    rows, syn = series.bridged_tail(tf)
    forming = engine.open.get((sym, tf))
    if forming and (not rows or forming["t"] > rows[-1]["t"]):
        rows = [*rows, {k: forming[k] for k in ("t", "o", "h", "l", "c", "n")}]
    stamp = (round(forming["c"], 8) if forming else None, rows[-1]["t"] if rows else 0, len(rows), syn,
             tuple(sorted((d["id"], d["version"], d["status"]) for d in engine.strategies.values())))
    cache_all = engine.__dict__.setdefault("_ready_cache", {})
    hit = cache_all.get((sym, tf))
    now = time.time()
    if hit and hit[0] == stamp and now - hit[1] < 1.0:
        return hit[2]
    window = rows[-400:]
    ctx = {"payout": engine.instruments.get(sym, {}).get("payout")}
    cache: dict = {}
    out: dict[str, dict] = {}
    for d in engine.strategies.values():
        spec = d["spec"]
        if tf not in spec["timeframes"] or not engine._in_scope(spec, sym) or spec["kind"] == "meta":
            continue
        try:
            r = rd.of(spec, evaluator.tail_for(spec, rows, window), cache, ctx)
            if syn and r["state"] == "ready":                                     # a patched candle in the last 8: no signal is issued
                r = {**r, "state": "blocked", "reason": engine.GAP_REASON}
            out[str(d["id"])] = r
        except Exception as e:  # noqa: BLE001 - one broken strategy must not break the panel
            out[str(d["id"])] = {"state": "error", "lead": None, "p": 0.0, "reason": str(e)[:120]}
    for d in engine.strategies.values():                                          # a consensus strategy is ready when enough of its members are
        spec = d["spec"]
        if spec["kind"] != "meta" or tf not in spec["timeframes"] or len(window) < 60:
            continue
        c = spec["consensus"]
        members = [k for k, v in engine.strategies.items() if v["spec"]["kind"] != "meta" and str(k) in out] if c["members"] == "all_active" else \
            [k for k, v in engine.strategies.items() if v["key"] in c["members"] and str(k) in out]
        need = max(1, int(c["min_agree"]))
        calls = sum(1 for k in members if out[str(k)].get("state") == "ready" and out[str(k)].get("lead") == "CALL")
        puts = sum(1 for k in members if out[str(k)].get("state") == "ready" and out[str(k)].get("lead") == "PUT")
        lead = "CALL" if calls >= puts and calls else "PUT" if puts else None
        n = max(calls, puts)
        ready = n >= need and not (calls and puts)
        conflict = n >= need and bool(calls and puts)                             # enough members agree, but others say the opposite: no consensus
        out[str(d["id"])] = {"state": "ready" if ready else "blocked" if conflict else "building", "lead": lead, "p": 1.0 if ready or conflict else round(min(1.0, n / need), 3),
                             "direction": lead if ready else "SKIP", "reason": f"strategies disagree ({calls} CALL, {puts} PUT)" if conflict else "",
                             "call": {"p": round(min(1.0, calls / need), 3), "met": calls, "total": need, "missing": []},
                             "put": {"p": round(min(1.0, puts / need), 3), "met": puts, "total": need, "missing": []}}
    result = {"symbol": sym, "tf": tf, "forming": bool(forming), "candle_t": forming["t"] if forming else None, "price": forming["c"] if forming else None,
              "at": now, "strategies": out}
    cache_all[(sym, tf)] = (stamp, now, result)
    if len(cache_all) > 40:
        for k in list(cache_all)[:10]:
            del cache_all[k]
    return result


def analyze_all(engine, sym: str, tf: int) -> dict:
    """The current opinion of every running strategy on one pair/timeframe (evaluated on the last closed candle)."""
    series = engine._series(sym, tf)
    rows, syn = series.bridged_tail(tf)
    # The board reads the LAST CLOSED candle, so it only changes when a candle closes or a strategy is switched/edited: cache on exactly that.
    token = (rows[-1]["t"] if rows else 0, len(rows), syn, engine.instruments.get(sym, {}).get("payout"),
             tuple(sorted((d["id"], d["version"], d["status"]) for d in engine.strategies.values())))
    boards = engine.__dict__.setdefault("_board_cache", {})
    hit = boards.get((sym, tf))
    if hit and hit[0] == token:
        return hit[1]
    window = rows[-400:]
    ctx = {"payout": engine.instruments.get(sym, {}).get("payout")}
    cache: dict = {}
    out, results = [], {}

    def row(d, res, error: str | None = None) -> dict:
        return {"id": d["id"], "key": d["key"], "name": d["name"], "status": d["status"], "version": d["version"], "tags": d.get("tags", []),
                "direction": res.direction if res else "SKIP", "tier": res.tier if res else "", "strength": res.strength if res else 0,
                "score": res.score if res else 0, "agree": res.agree if res else 0, "reason": error or res.reason,
                "warming": bool(res and res.reason.startswith("warming")),
                "factors": [{"key": f["key"], "name": f["name"], "weight": round(f["weight"]), "vote": f["vote"], "note": f["note"]} for f in (res.factors if res else [])]}

    for d in engine.strategies.values():
        spec = d["spec"]
        if tf not in spec["timeframes"] or not engine._in_scope(spec, sym) or spec["kind"] == "meta":
            continue
        try:
            res = engine._gap_guard(evaluator.evaluate(spec, evaluator.tail_for(spec, rows, window), cache, ctx), syn)
        except Exception as e:                                           # one broken strategy must not break the board
            out.append(row(d, None, f"error: {e}"))
            continue
        results[d["key"]] = (d["name"], res)
        out.append(row(d, res))
    for d in engine.strategies.values():
        if d["spec"]["kind"] == "meta" and tf in d["spec"]["timeframes"] and len(window) >= 60:
            out.append(row(d, engine._gap_guard(evaluator.consensus(d["spec"], results), syn)))
    out.sort(key=lambda x: (x["direction"] == "SKIP", -x["strength"], x["name"]))
    calls = sum(1 for x in out if x["direction"] == "CALL")
    puts = sum(1 for x in out if x["direction"] == "PUT")
    result = {"symbol": sym, "tf": tf, "candles": len(rows), "bridged": syn, "calls": calls, "puts": puts, "neutral": len(out) - calls - puts,
              "last_candle": rows[-1]["t"] if rows else None, "strategies": out}
    boards[(sym, tf)] = (token, result)
    if len(boards) > 80:                                                 # bounded: forget the oldest entries
        for k in list(boards)[:20]:
            del boards[k]
    return result
