"""Evaluates a (validated) strategy spec on candles and returns a strategy.Result - the same object the legacy engine produced.

evaluate(spec, rows, cache, ctx):  rows = chronological closed candles [{t,o,h,l,c,n}]; cache shares indicator work between strategies
evaluated on the same candle; ctx = engine facts (payout, ...). consensus(spec, results) builds the meta-strategy result.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import numpy as np
import pandas as pd

from . import expr, indreg, patterns
from .strategy import FN_FACTORS, Result

SIDE_THRESHOLD = 0.3             # |vote| needed for a factor to count as "agreeing"


def build_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows).rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"})
    if "n" not in df:
        df["n"] = 0
    return df[["t", "open", "high", "low", "close", "n"]].astype(float).reset_index(drop=True)


def _streak(colors: np.ndarray) -> np.ndarray:
    out, run = np.zeros(len(colors)), 0
    for i, c in enumerate(colors):
        run = (run + int(c) if run * c > 0 else int(c)) if c != 0 else 0
        out[i] = run
    return out


def base_env(df: pd.DataFrame) -> dict:
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    body, rng = c - o, h - l
    ab = body.abs()
    upper, lower = h - np.maximum(o, c), np.minimum(o, c) - l
    doji = (ab / rng.replace(0, np.nan)) < 0.1
    tiny = 1e-12
    env = {
        "open": o, "high": h, "low": l, "close": c, "ticks": df["n"], "body": body, "range": rng, "upper_wick": upper, "lower_wick": lower,
        "green": c > o, "red": c < o, "streak": pd.Series(_streak(np.sign(body.to_numpy())), df.index), "hlc3": (h + l + c) / 3,
        "hour": pd.Series(((df["t"] % 86400) // 3600).astype(int), df.index), "weekday": pd.Series((((df["t"] // 86400) + 3) % 7).astype(int), df.index),
        "doji": doji.fillna(False),
        "bull_engulf": (c.shift() < o.shift()) & (c > o) & (c >= o.shift()) & (o <= c.shift()) & (ab > ab.shift()),
        "bear_engulf": (c.shift() > o.shift()) & (c < o) & (c <= o.shift()) & (o >= c.shift()) & (ab > ab.shift()),
        "hammer": (lower >= 2 * ab) & (upper <= np.maximum(ab, tiny) * 0.6) & ~doji.fillna(False) & (rng > 0),
        "star": (upper >= 2 * ab) & (lower <= np.maximum(ab, tiny) * 0.6) & ~doji.fillna(False) & (rng > 0),
        "inside_bar": (h < h.shift()) & (l > l.shift()),
    }
    return env


def _ind_env(spec: dict, df: pd.DataFrame, cache: dict) -> dict:
    env = base_env(df)
    for it in spec["indicators"]:
        params = {k: v for k, v in it.items() if k not in ("id", "type")}
        env[it["id"]] = indreg.compute(cache, df, it["type"], params)
    return env


def _minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def _in_window(minute: int, window: str) -> bool:
    a, b = _minutes(window[:5]), _minutes(window[6:])
    return a <= minute < b if a <= b else (minute >= a or minute < b)


def _filter_reason(f: dict, df: pd.DataFrame, direction: str, cache: dict, ctx: dict) -> str | None:
    typ = f["type"]
    if typ in ("adx_min", "adx_max"):
        v = indreg.compute(cache, df, "adx", {"n": f["n"]}).adx.iloc[-1]
        if np.isnan(v):
            return None
        if typ == "adx_min" and v < f["value"]:
            return f"ADX {v:.1f} < {f['value']} (market too flat)"
        if typ == "adx_max" and v > f["value"]:
            return f"ADX {v:.1f} > {f['value']} (market trending too hard)"
    elif typ in ("atr_ratio_min", "atr_ratio_max"):
        a = indreg.compute(cache, df, "atr", {"n": 14})
        if a.dropna().shape[0] < 10:
            return None
        ratio = a.iloc[-1] / a.iloc[-50:].mean()
        if typ == "atr_ratio_min" and ratio < f["value"]:
            return f"volatility {ratio:.2f}x average < {f['value']}"
        if typ == "atr_ratio_max" and ratio > f["value"]:
            return f"volatility {ratio:.2f}x average > {f['value']}"
    elif typ == "hours":
        minute = int(df["t"].iloc[-1] % 86400 // 60)
        if f["include"] and not any(_in_window(minute, w) for w in f["include"]):
            return "outside allowed hours (UTC)"
        if any(_in_window(minute, w) for w in f["exclude"]):
            return "inside excluded hours (UTC)"
    elif typ == "weekdays":
        wd = int(((df["t"].iloc[-1] // 86400) + 3) % 7)
        if f["include"] and wd not in f["include"]:
            return "weekday not allowed"
        if wd in f["exclude"]:
            return "weekday excluded"
    elif typ == "trend_alignment":
        e = [indreg.compute(cache, df, "ema", {"n": n}).iloc[-1] for n in (9, 21, 50)]
        if any(np.isnan(x) for x in e):
            return None
        stack = 1 if e[0] > e[1] > e[2] else -1 if e[0] < e[1] < e[2] else 0
        align = stack * (1 if direction == "CALL" else -1)
        want = f["value"]
        if (want == "with" and align <= 0) or (want == "against" and align >= 0) or (want == "not_against" and align < 0) \
                or (want == "not_with" and align > 0):
            return f"EMA trend alignment {align:+d} fails '{want}'"
    elif typ == "direction_only":
        if direction != f["value"]:
            return f"only {f['value']} signals allowed"
    elif typ == "min_payout":
        p = ctx.get("payout")
        if p is not None and p < f["value"]:
            return f"payout {p}% < {f['value']}%"
    return None


DEFAULT_LOOKBACK = 400
MAX_LOOKBACK = 1000


def lookback(spec: dict) -> int:
    """How many recent candles live evaluation must show this strategy so it answers exactly as the backtest does. 400 for ordinary strategies; a strategy with
    the supply/demand zone tracker remembers a zone for `max_age` candles, so it needs that many plus the base and the ATR warm-up."""
    need = DEFAULT_LOOKBACK
    for it in spec.get("indicators", []):
        if it.get("type") == "zones":
            need = max(need, int(it.get("max_age", 250)) + int(it.get("base_len", 8)) + 230)      # a zone lives max_age candles; +230: ATR (14-candle smoothing) is exact to ~1e-8 by then
        elif it.get("type") == "engulf":
            need = max(need, int(it.get("max_age", 120)) + int(it.get("trend_age", 120)) + 2 * int(it.get("swing", 3)) + 250)   # zone life + the structure behind its birth + ATR warm-up
        elif it.get("type") == "structure":
            need = max(need, 2 * int(it.get("max_age", 120)) + 2 * int(it.get("swing", 3)) + 20)   # swings count for max_age candles, and `age` looks back another max_age
    return min(need, MAX_LOOKBACK)


def tail_for(spec: dict, rows: list[dict], default_window: list[dict] | None = None) -> list[dict]:
    """The newest candles this strategy should be evaluated on (default_window = rows[-400:] when the caller already has it)."""
    lb = lookback(spec)
    if lb <= DEFAULT_LOOKBACK:
        return default_window if default_window is not None else rows[-DEFAULT_LOOKBACK:]
    return rows[-lb:]


def _run_continues(spec: dict, idx: int, rows: list[dict], direction: str, gap: int, ctx: dict) -> str | None:
    """'one_per_run' filter: did the candles just before this one already signal the same direction (as seen by the filters ahead of this one)?
    Mirrors vec._filter_fail so live and backtest agree. Only called for a candle that has a signal, so it costs nothing on quiet candles."""
    ahead = {**spec, "filters": spec["filters"][:idx]}
    for k in range(1, gap + 1):
        if len(rows) - k < 1:
            break
        if evaluate(ahead, rows[:-k], None, ctx).direction == direction:
            return f"{direction} signal already fired {k} candle{'s' if k > 1 else ''} ago (one signal per run)"
    return None


def evaluate(spec: dict, rows: list[dict], cache: dict | None = None, ctx: dict | None = None) -> Result:
    cache = {} if cache is None else cache
    ctx = ctx or {}
    gates = spec["gates"]
    need = int(gates["min_candles"])
    if len(rows) < need:
        return Result("SKIP", 0.0, 0, 0, [], f"warming up ({len(rows)}/{need} candles)")
    df = build_df(rows)
    env = None
    facs, total_w = [], float(sum(f["weight"] for f in spec["factors"])) or 1.0
    scale = 100.0 / total_w
    for f in spec["factors"]:
        try:
            if f["kind"] == "fn":
                fn, _ = FN_FACTORS[f["fn"]]
                vote, note = fn(df, **f["params"])
            elif f["kind"] == "pattern":
                env = env or _ind_env(spec, df, cache)
                c = bool(patterns.match(df, env, f["call"]).iloc[-1]) if f.get("call") else False
                p = bool(patterns.match(df, env, f["put"]).iloc[-1]) if f.get("put") else False
                vote = f["vote"] if c and not p else -f["vote"] if p and not c else 0.0
                note = "call pattern matched" if vote > 0 else "put pattern matched" if vote < 0 else ("both patterns matched" if c and p else "no pattern matched")
            else:
                env = env or _ind_env(spec, df, cache)
                c = bool(expr.evaluate(f["call"], env, df.index).iloc[-1]) if f.get("call") else False
                p = bool(expr.evaluate(f["put"], env, df.index).iloc[-1]) if f.get("put") else False
                vote = f["vote"] if c and not p else -f["vote"] if p and not c else 0.0
                note = "call condition true" if vote > 0 else "put condition true" if vote < 0 else ("both conditions true" if c and p else "no condition met")
        except Exception as e:   # one broken factor must not stop the loop; it simply abstains
            vote, note = 0.0, f"error: {e}"
        facs.append({"key": f["id"], "name": f["name"], "weight": round(f["weight"] * scale, 4), "vote": round(float(vote), 2), "note": note})

    score = sum(x["vote"] * x["weight"] for x in facs)
    side = 1 if score > 0 else -1 if score < 0 else 0
    agree = sum(1 for x in facs if side and abs(x["vote"]) >= SIDE_THRESHOLD and np.sign(x["vote"]) == side)
    opposing = sum(x["weight"] for x in facs if side and abs(x["vote"]) >= SIDE_THRESHOLD and np.sign(x["vote"]) == -side)
    strength = int(min(100, round(abs(score))))
    direction, reason, tier = "SKIP", "", ""
    if not side:
        reason = "no factor active"
    elif agree < gates["min_agree"]:
        reason = f"only {agree} factor(s) agree (need {gates['min_agree']})"
    elif opposing > gates["max_opposing_weight"]:
        reason = f"conflicting factors (weight {opposing:.0f})"
    elif strength < gates["min_strength"]:
        reason = f"score too weak ({strength})"
    else:
        direction = "CALL" if side > 0 else "PUT"
        ta = gates["tier_a"]
        tier = "A" if (ta is None or (agree >= ta["agree"] and strength >= ta["strength"])) else "B"
    if direction != "SKIP":
        for idx, flt in enumerate(spec["filters"]):
            why = _run_continues(spec, idx, rows, direction, int(flt["gap"]), ctx) if flt["type"] == "one_per_run" else _filter_reason(flt, df, direction, cache, ctx)
            if why:
                direction, tier, reason = "SKIP", "", f"filtered: {why}"
                break
    return Result(direction, round(float(score), 1), strength, agree, facs, reason, tier)


def consensus(spec: dict, results: dict[str, tuple[str, Result]]) -> Result:
    """results: strategy key -> (name, Result) for the non-meta strategies evaluated on this candle."""
    c = spec["consensus"]
    members = list(results) if c["members"] == "all_active" else [m for m in c["members"] if m in results]
    calls = [m for m in members if results[m][1].direction == "CALL"]
    puts = [m for m in members if results[m][1].direction == "PUT"]
    need = c["min_agree"]
    side = calls if len(calls) >= need and not puts else puts if len(puts) >= need and not calls else None
    weight = round(100 / max(1, len(members)), 2)
    facs = [{"key": m, "name": results[m][0], "weight": weight,
             "vote": 1.0 if m in calls else -1.0 if m in puts else 0.0, "note": f"strength {results[m][1].strength}" if results[m][1].direction != "SKIP" else "no signal"}
            for m in members]
    if side is None:
        why = "strategies disagree" if calls and puts else f"only {max(len(calls), len(puts))} of {need} needed agree"
        return Result("SKIP", 0.0, 0, max(len(calls), len(puts)), facs, why)
    strength = int(round(sum(results[m][1].strength for m in side) / len(side)))
    score = round(sum(results[m][1].score for m in side) / len(side), 1)
    return Result("CALL" if side is calls else "PUT", score, strength, len(side), facs, "", "A")


# ----------------------------------------------------------------------------------------------- heads-up (the early "get ready" alert)
def _value(env: dict, ref: str) -> float | None:
    """The value of 'name' or 'name.field' on the last candle (None when unknown or not a number)."""
    nm, _, attr = ref.partition(".")
    obj = env.get(nm)
    ser = getattr(obj, attr, None) if attr and isinstance(obj, SimpleNamespace) else obj
    if not isinstance(ser, pd.Series) or not len(ser):
        return None
    v = ser.iloc[-1]
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(v) else v


def fmt_number(v: float | None) -> str:
    if v is None:
        return "-"
    if abs(v) >= 1000:
        return f"{v:,.2f}"
    return str(int(v)) if float(v).is_integer() else f"{v:.6g}"


def heads_up(spec: dict, rows: list[dict], cache: dict | None = None) -> list[dict]:
    """The early alerts that hold on the LAST closed candle of `rows`: [{direction, level, text, title}]. A strategy opts in with a `heads_up` block (spec.py); it is
    evaluated like a factor condition, so the same candles that make the signal can warn about it one candle earlier. `text` has its {name.field} placeholders filled."""
    hu = spec.get("heads_up")
    if not hu or len(rows) < int(spec["gates"]["min_candles"]):
        return []
    df = build_df(rows)
    env = _ind_env(spec, df, {} if cache is None else cache)
    out = []
    for side, direction in (("call", "CALL"), ("put", "PUT")):
        cond = hu.get(side)
        if not cond:
            continue
        try:
            if not bool(expr.evaluate(cond, env, df.index).iloc[-1]):
                continue
        except Exception:  # noqa: BLE001 - an unreadable condition abstains, like a broken factor
            continue
        ref = hu["level"].get(side)
        text = re.sub(r"\{([a-z][a-z0-9_]*(?:\.[a-z_]+)?)\}", lambda m: fmt_number(_value(env, m.group(1))), hu["text"].get(side) or "")
        out.append({"direction": direction, "level": _value(env, ref) if ref else None, "text": text, "title": hu.get("title") or ""})
    return out
