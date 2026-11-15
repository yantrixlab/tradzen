"""Candle-sequence patterns: "3-6 green candles each with a higher high, then a green hammer whose low is below the previous candle's
low, then 1-3 red candles that trade below the hammer's low".

A pattern is plain JSON (never code):  {"steps": [step, ...]}  matched so that the LAST candle of the LAST step is the candle that just closed.
  step = {"label": str, "color": any|green|red, "shape": any|hammer|star|doji|strong|weak|long, "min": 1-8, "max": 1-8,
          "rels": [{"prop": P, "op": < > <= >=, "ref": "prev" | earlier step index, "refprop": P, "scope": each|any|last}]}
  P    = open high low close body_mid body range        (body = |close-open|, range = high-low, body_mid = (open+close)/2)
A relation compares THIS step's candle with a reference candle: "prev" = the candle right before it in time, an integer = the last candle of
that earlier step. With several candles in a step, scope says whether the relation must hold on each of them (for ref "prev" the first candle
of a multi-candle step starts the run and is not compared with the candle before the step), on at least one, or on the last one only.

match() is vectorised (one boolean per candle, so backtests and charts reuse it); repeat counts are expanded into every combination.
"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

COLORS = ("any", "green", "red")
SHAPES = ("any", "hammer", "star", "doji", "strong", "weak", "long")
PROPS = ("open", "high", "low", "close", "body_mid", "body", "range")
OPS = (">", "<", ">=", "<=")
SCOPES = ("each", "any", "last")
MAX_STEPS, MAX_REPEAT, MAX_COMBOS, MAX_RELS = 8, 8, 600, 4


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def validate(raw, label: str = "pattern") -> tuple[dict | None, list[str]]:
    """Normalise a pattern. Returns (pattern or None, errors)."""
    errors: list[str] = []
    if not isinstance(raw, dict) or not isinstance(raw.get("steps"), list):
        return None, [f"{label}: needs a 'steps' list"]
    steps_in = raw["steps"]
    if not 1 <= len(steps_in) <= MAX_STEPS:
        return None, [f"{label}: 1-{MAX_STEPS} steps are allowed"]
    steps = []
    for i, s in enumerate(steps_in):
        where = f"{label} step {i + 1}"
        if not isinstance(s, dict):
            errors.append(f"{where}: must be an object")
            continue
        color, shape = s.get("color", "any"), s.get("shape", "any")
        lo, hi = s.get("min", 1), s.get("max", s.get("min", 1))
        if color not in COLORS:
            errors.append(f"{where}: color must be one of {list(COLORS)}")
        if shape not in SHAPES:
            errors.append(f"{where}: shape must be one of {list(SHAPES)}")
        if not (_is_int(lo) and _is_int(hi) and 1 <= lo <= hi <= MAX_REPEAT):
            errors.append(f"{where}: repeat must be whole numbers with 1 <= min <= max <= {MAX_REPEAT}")
            lo, hi = 1, 1
        rels_in = s.get("rels") or []
        if not isinstance(rels_in, list) or len(rels_in) > MAX_RELS:
            errors.append(f"{where}: at most {MAX_RELS} relations")
            rels_in = []
        rels = []
        for j, r in enumerate(rels_in):
            rw = f"{where} relation {j + 1}"
            if not isinstance(r, dict):
                errors.append(f"{rw}: must be an object")
                continue
            ref = r.get("ref", "prev")
            ok = r.get("prop") in PROPS and r.get("refprop") in PROPS and r.get("op") in OPS and r.get("scope", "each") in SCOPES
            if not ok:
                errors.append(f"{rw}: needs prop/refprop from {list(PROPS)}, op from {list(OPS)}, scope from {list(SCOPES)}")
            elif not (ref == "prev" or (_is_int(ref) and 0 <= ref < i)):
                errors.append(f"{rw}: ref must be 'prev' or an EARLIER step number")
            else:
                rels.append({"prop": r["prop"], "op": r["op"], "ref": ref, "refprop": r["refprop"], "scope": r.get("scope", "each")})
        steps.append({"label": str(s.get("label") or f"Step {i + 1}")[:40], "color": color, "shape": shape, "min": lo, "max": hi, "rels": rels})
    if errors:
        return None, errors
    combos = int(np.prod([s["max"] - s["min"] + 1 for s in steps]))
    if combos > MAX_COMBOS:
        return None, [f"{label}: too many repeat combinations ({combos}); narrow the min-max ranges"]
    return {"steps": steps}, []


def _props(df: pd.DataFrame) -> dict[str, np.ndarray]:
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    return {"open": o, "high": h, "low": l, "close": c, "body_mid": (o + c) / 2, "body": np.abs(c - o), "range": h - l}


def _shapes(df: pd.DataFrame, env: dict) -> dict[str, np.ndarray]:
    p = _props(df)
    rng = np.where(p["range"] > 0, p["range"], np.nan)
    ratio = p["body"] / rng
    avg_body = pd.Series(p["body"]).rolling(14, min_periods=3).mean().to_numpy()
    with np.errstate(invalid="ignore"):
        return {
            "green": p["close"] > p["open"], "red": p["close"] < p["open"],
            "hammer": env["hammer"].to_numpy(bool), "star": env["star"].to_numpy(bool), "doji": env["doji"].to_numpy(bool),
            "strong": ratio >= 0.6, "weak": ratio <= 0.3, "long": p["body"] >= 1.3 * avg_body,
        }


def _shift(a: np.ndarray, k: int):
    if k == 0:
        return a
    out = np.full(a.shape, False if a.dtype == bool else np.nan)
    if k < len(a):
        out[k:] = a[: len(a) - k]
    return out


def _cmp(a: np.ndarray, op: str, b: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]


def match(df: pd.DataFrame, env: dict, pattern: dict, spans: bool = False):
    """Boolean Series: True where the pattern ends on that candle. With spans=True also returns
    {candle index: [(step, first candle index, last candle index), ...]} for the combination chosen at each match (longest runs preferred)."""
    n = len(df)
    steps = pattern["steps"]
    props, shp = _props(df), _shapes(df, env)
    hit = np.zeros(n, bool)
    found: dict[int, list[tuple[int, int, int]]] = {}
    ranges = [range(s["max"], s["min"] - 1, -1) for s in steps]          # longest runs first
    for counts in itertools.product(*ranges):
        after = [sum(counts[k + 1:]) for k in range(len(steps))]           # candles that come after step k
        off = lambda k, j: after[k] + (counts[k] - 1 - j)                  # noqa: E731  offset (candles back from the last) of candle j of step k
        ok = np.ones(n, bool)
        ok[: sum(counts) - 1] = False
        for k, s in enumerate(steps):
            for j in range(counts[k]):
                o = off(k, j)
                if s["color"] != "any":
                    ok &= _shift(shp[s["color"]], o)
                if s["shape"] != "any":
                    ok &= _shift(shp[s["shape"]], o)
            for r in s["rels"]:
                per = []
                for j in range(counts[k]):
                    o = off(k, j)
                    ro = o + 1 if r["ref"] == "prev" else off(r["ref"], counts[r["ref"]] - 1)
                    per.append(_cmp(_shift(props[r["prop"]], o), r["op"], _shift(props[r["refprop"]], ro)))
                if r["scope"] == "each":
                    use = per[1:] if (r["ref"] == "prev" and counts[k] > 1) else per
                    for x in use:
                        ok &= x
                elif r["scope"] == "last":
                    ok &= per[-1]
                else:
                    anyp = per[0].copy()
                    for x in per[1:]:
                        anyp |= x
                    ok &= anyp
        if spans:
            for t in np.flatnonzero(ok & ~hit):
                seq, start = [], t - (sum(counts) - 1)
                for k in range(len(steps)):
                    seq.append((k, int(start), int(start + counts[k] - 1)))
                    start += counts[k]
                found[int(t)] = seq
        hit |= ok
    series = pd.Series(hit, df.index)
    return (series, found) if spans else series
