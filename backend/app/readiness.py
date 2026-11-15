"""How close is a strategy to giving a signal? - so a trader can get ready.

A strategy signals when its rules hold on the candle that just CLOSED. This module asks the same question about the candle that is still FORMING, as if it
closed right now (the caller appends the forming candle), and reports it as a readiness from 0 to 1 for each side:

  * a condition rule ("a and b and c"): the share of its conditions that are true now (3 of 5 -> 0.6), and which ones are still missing;
  * a built-in factor (RSI, Bollinger ... votes between -1 and +1): how big its vote already is;
  * a candle pattern: 1 when matched, else 0;
  * then the strategy's gates: the best `min_agree` factors must be ready, and the combined strength must reach `min_strength`.

This is a reading of the market's current state, never a probability: 100 % means "a signal would be issued if this candle closed as it is"; the candle can still
change until it closes. It changes nothing in how signals are issued (evaluator.evaluate does that).
"""
from __future__ import annotations

import ast

import numpy as np

from . import evaluator, expr, patterns
from .strategy import FN_FACTORS

MAX_MISSING = 3


def _clauses(text: str) -> list[str]:
    """A rule split at its top-level 'and's: 'a and b and (c or d)' -> ['a', 'b', '(c or d)']."""
    try:
        node = ast.parse(text.strip(), mode="eval").body
    except SyntaxError:
        return [text]
    out: list = []

    def walk(n):
        if isinstance(n, ast.BoolOp) and isinstance(n.op, ast.And):
            for v in n.values:
                walk(v)
        else:
            out.append(ast.unparse(n))
    walk(node)
    return out or [text]


def _rule(text: str | None, env: dict, index) -> tuple[float, int, int, list[str]]:
    """(readiness, conditions met, conditions total, the conditions still missing)."""
    if not text:
        return 0.0, 0, 0, []
    parts = _clauses(text)
    met, missing = 0, []
    for c in parts:
        try:
            v = expr.evaluate(c, env, index).iloc[-1]
            ok = bool(v) and not (isinstance(v, float) and np.isnan(v))
        except Exception:  # noqa: BLE001 - an unreadable condition just counts as not met
            ok = False
        if ok:
            met += 1
        elif len(missing) < MAX_MISSING:
            missing.append(c if len(c) <= 90 else c[:89] + "…")
    return met / len(parts), met, len(parts), missing


def of(spec: dict, rows: list[dict], cache: dict, ctx: dict) -> dict:
    """Readiness of one factor-style strategy on `rows` (closed candles + the forming one). Returns
    {state: warming|ready|blocked|building, lead: CALL|PUT|None, p: 0..1, call: {p, met, total, missing}, put: {...}, reason, direction}."""
    gates, need = spec["gates"], int(spec["gates"]["min_candles"])
    if len(rows) < need:
        return {"state": "warming", "lead": None, "p": 0.0, "have": len(rows), "need": need, "reason": f"warming up ({len(rows)}/{need} candles)"}
    df = evaluator.build_df(rows)
    env = None
    total_w = float(sum(f["weight"] for f in spec["factors"])) or 1.0
    sides = {"call": [], "put": []}                                  # per factor: (weight share, readiness, met, total, missing)
    for f in spec["factors"]:
        try:
            if f["kind"] == "fn":
                fn, _ = FN_FACTORS[f["fn"]]
                vote, _note = fn(df, **f["params"])
                pc, pp = max(0.0, min(1.0, float(vote))), max(0.0, min(1.0, -float(vote)))
                for key, p in (("call", pc), ("put", pp)):
                    sides[key].append((f["weight"] / total_w, p, int(round(p * 100)), 100, []))
            elif f["kind"] == "pattern":
                env = env or evaluator._ind_env(spec, df, cache)
                for key in ("call", "put"):
                    hit = bool(patterns.match(df, env, f[key]).iloc[-1]) if f.get(key) else False
                    sides[key].append((f["weight"] / total_w, 1.0 if hit else 0.0, int(hit), 1, [] if hit else [f"{key} pattern not complete"]))
            else:
                env = env or evaluator._ind_env(spec, df, cache)
                for key in ("call", "put"):
                    p, met, tot, missing = _rule(f.get(key), env, df.index)
                    sides[key].append((f["weight"] / total_w, p, met, tot, missing))
        except Exception:  # noqa: BLE001 - a broken factor abstains, like in evaluate()
            for key in sides:
                sides[key].append((f["weight"] / total_w, 0.0, 0, 1, ["factor could not be read"]))
    k, strength_needed = max(1, int(gates["min_agree"])), float(gates["min_strength"])
    out: dict = {}
    for key, items in sides.items():
        ps = sorted((i[1] for i in items), reverse=True)[:k]
        p_agree = sum(ps) / k
        p_strength = min(1.0, (sum(i[0] * i[1] for i in items) * 100.0) / strength_needed) if strength_needed > 0 else 1.0
        best = max(items, key=lambda i: i[1]) if items else None
        out[key] = {"p": round(min(p_agree, p_strength), 3), "met": best[2] if best else 0, "total": best[3] if best else 0, "missing": best[4] if best else []}
    lead = None if max(out["call"]["p"], out["put"]["p"]) <= 0 else "CALL" if out["call"]["p"] >= out["put"]["p"] else "PUT"
    res = evaluator.evaluate(spec, rows, cache, ctx)                 # the real decision on this provisional candle: a signal, or why not
    p = out["call" if lead == "CALL" else "put"]["p"] if lead else 0.0
    state, reason = "building", ""
    if res.direction != "SKIP":
        state, lead, p = "ready", res.direction, 1.0
    elif res.reason.startswith("filtered:") or (p >= 0.999 and res.reason):
        state, reason = "blocked", res.reason
    return {"state": state, "lead": lead, "p": round(p, 3), "direction": res.direction, "reason": reason, **out}
