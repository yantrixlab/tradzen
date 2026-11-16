"""Chart data for the strategy builder: candles + every indicator the draft strategy declares + the truth of each condition on each
candle, so the UI can plot the lines and highlight where a condition fires. Pure function of (candle rows, raw spec, expressions).

Deliberately lenient: the spec is usually half-written while the user is building it, so a broken indicator or condition only drops
that one item (reported in `errors` / `marks[key].error`) and never fails the whole request.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pandas as pd

from . import evaluator, expr, indreg, patterns, spec as spec_mod, vec
from . import engulf as engulf_mod
from . import structure as structure_mod
from . import zones as zones_mod
from .evaluator import SIDE_THRESHOLD

MAX_ROWS = 400          # candles used for indicator warm-up
MIN_ROWS = 30
MAX_EXPRS = 80

# indicator type -> [(field or None, panel, style)]; panel 'price' draws over the candles, 'osc' in its own pane below
PLOTS: dict[str, list[tuple[str | None, str, str]]] = {
    "sma": [(None, "price", "line")], "ema": [(None, "price", "line")],
    "bollinger": [("upper", "price", "line"), ("mid", "price", "dash"), ("lower", "price", "line")],
    "donchian": [("upper", "price", "line"), ("mid", "price", "dash"), ("lower", "price", "line")],
    "supertrend": [("line", "price", "line")], "psar": [("sar", "price", "dots")],
    "zones": [("dz_hi", "price", "dash"), ("dz_lo", "price", "dash"), ("sz_hi", "price", "dash"), ("sz_lo", "price", "dash")],   # the zone price just touched
    "engulf": [("dz_hi", "price", "dash"), ("dz_lo", "price", "dash"), ("sz_hi", "price", "dash"), ("sz_lo", "price", "dash")],     # (replaced by the drawn zones)
    "structure": [("sh", "price", "dash"), ("sl", "price", "dash")],                                  # the newest swing high / low
    "rsi": [(None, "osc", "line")], "cci": [(None, "osc", "line")], "williams": [(None, "osc", "line")],
    "roc": [(None, "osc", "line")], "atr": [(None, "osc", "line")],
    "stoch": [("k", "osc", "line"), ("d", "osc", "dash")],
    "macd": [("hist", "osc", "bars"), ("line", "osc", "line"), ("signal", "osc", "dash")],
    "adx": [("adx", "osc", "line"), ("pdi", "osc", "dash"), ("mdi", "osc", "dash")],
}
LEVELS = {"rsi": [30, 70], "stoch": [20, 80], "cci": [-100, 100], "williams": [-80, -20], "macd": [0], "adx": [20, 25], "roc": [0]}
RANGES = {"rsi": (0, 100), "stoch": (0, 100), "williams": (-100, 0)}


def _clean(series: pd.Series, tail: int) -> list[float | None]:
    out = []
    for v in series.iloc[-tail:].tolist():
        out.append(None if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) else round(float(v), 8))
    return out


def _bools(series: pd.Series, tail: int) -> list[bool]:
    return [bool(v) for v in series.fillna(False).astype(bool).iloc[-tail:].tolist()]


def _label(it: dict) -> str:
    params = ",".join(str(v) for k, v in it.items() if k not in ("id", "type"))
    return f"{it['type'].upper()}({params})"


def _indicator_env(raw_spec: dict, df: pd.DataFrame, errors: list[str]) -> tuple[dict, list[dict]]:
    """Base names + every well-formed declared indicator. Returns (env, normalised indicator list)."""
    env = evaluator.base_env(df)
    good: list[dict] = []
    cache: dict = {}
    for i, it in enumerate(raw_spec.get("indicators") or []):
        if not isinstance(it, dict):
            continue
        iid, typ = it.get("id"), it.get("type")
        if not isinstance(iid, str) or typ not in indreg.REGISTRY or iid in env:
            if iid:
                errors.append(f"indicator '{iid}' skipped (unknown type or duplicate id)")
            continue
        dflt = indreg.defaults(typ)
        params = {k: v for k, v in it.items() if k in dflt and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0}
        merged = {**dflt, **params}
        try:
            env[iid] = indreg.compute(cache, df, typ, merged)
            good.append({"id": iid, "type": typ, **merged})
        except Exception as e:  # noqa: BLE001
            errors.append(f"indicator '{iid}': {e}")
    return env, good


def _plot_indicator(it: dict, env: dict, tail: int) -> dict:
    val = env[it["id"]]
    series = []
    for field, panel, style in PLOTS.get(it["type"], []):
        s = getattr(val, field) if field else val
        series.append({"field": field, "panel": panel, "style": style, "values": _clean(s, tail)})
    panel = series[0]["panel"] if series else "none"
    return {"id": it["id"], "type": it["type"], "label": _label(it), "panel": panel, "series": series,
            "levels": LEVELS.get(it["type"], []), "range": RANGES.get(it["type"])}


def _signals(norm: dict, env: dict, df: pd.DataFrame, tail: int) -> tuple[list[dict], dict[str, dict[str, list[bool]]]]:
    """Vectorised replay of the strategy's factor/gate logic on every candle. Built-in (fn) factors and filters cannot be
    vectorised, so they are skipped here - the 'Evaluate now' preview applies them to the latest candle."""
    n = len(df)
    total_w = float(sum(f["weight"] for f in norm["factors"])) or 1.0
    votes, weights, per_factor = [], [], {}
    for f in norm["factors"]:
        if f["kind"] == "fn":
            continue
        try:
            if f["kind"] == "pattern":
                c = patterns.match(df, env, f["call"]) if f.get("call") else pd.Series(False, index=df.index)
                p = patterns.match(df, env, f["put"]) if f.get("put") else pd.Series(False, index=df.index)
            else:
                c = expr.evaluate(f["call"], env, df.index) if f.get("call") else pd.Series(False, index=df.index)
                p = expr.evaluate(f["put"], env, df.index) if f.get("put") else pd.Series(False, index=df.index)
        except Exception:  # noqa: BLE001
            continue
        v = np.where(c & ~p, f["vote"], np.where(p & ~c, -f["vote"], 0.0))
        votes.append(v)
        weights.append(f["weight"] * 100.0 / total_w)
        per_factor[f["id"]] = {"call": _bools(c, tail), "put": _bools(p, tail)}
    if not votes:
        return [], per_factor
    V, W = np.array(votes), np.array(weights)[:, None]
    score = (V * W).sum(axis=0)
    side = np.sign(score)
    agree = ((np.abs(V) >= SIDE_THRESHOLD) & (np.sign(V) == side) & (side != 0)).sum(axis=0)
    opposing = (W * ((np.abs(V) >= SIDE_THRESHOLD) & (np.sign(V) == -side) & (side != 0))).sum(axis=0)
    strength = np.minimum(100, np.round(np.abs(score)))
    g = norm["gates"]
    ok = (side != 0) & (agree >= g["min_agree"]) & (opposing <= g["max_opposing_weight"]) & (strength >= g["min_strength"]) \
        & (np.arange(n) + 1 >= g["min_candles"])
    out = []
    for i in range(max(0, n - tail), n):
        if ok[i]:
            out.append({"i": i - (n - tail), "dir": "CALL" if side[i] > 0 else "PUT", "strength": int(strength[i])})
    return out, per_factor


MAX_ZONES_DRAWN = 60            # per request that draws one stretch (the builder); build_all draws every zone of a long history, see ZONES_ALL


def _with_default_marks(inds: list[dict]) -> list[dict]:
    """The chart's own supply/demand zones and market structure: used when the strategy (or no strategy at all) does not declare those indicators."""
    have = {it["type"] for it in inds}
    wanted = ["structure"] + ([] if have & {"zones", "engulf"} else ["engulf"])             # the default zones are the engulfing-candle zones; a strategy's own zone indicator wins
    extra = [{"id": t[:2] + "_auto", "type": t, **indreg.defaults(t)} for t in wanted if t not in have]
    return [*inds, *extra]


def close_through(closes: np.ndarray, kind: str, lo: float, hi: float, born: int) -> int | None:
    """The first candle after `born` that CLOSES beyond the zone: above a supply zone's top, below a demand zone's bottom. None = it is still standing.
    This is what ends a zone on the chart (the strategy indicators have their own, stricter rules: expiry, 'used after the first visit', a buffer beyond the edge)."""
    tail = closes[born + 1:]
    hit = np.flatnonzero(tail > hi) if kind == "supply" else np.flatnonzero(tail < lo)
    return int(hit[0]) + born + 1 if len(hit) else None


def _zone_and_swing_marks(inds: list[dict], df: pd.DataFrame, tail: int, limit: int = MAX_ZONES_DRAWN) -> tuple[list[dict], list[dict]]:
    """What the zone tracker and the structure indicator of the strategy would draw by hand: every supply / demand zone (band, base candles that formed it,
    departure, visits, whether it was broken) and every swing high / low labelled HH / LH / HL / LL. Times are candle times, so a chart can place them anywhere.
    A zone that expired or was 'used up' for the strategy is still sent while price has not closed through it: the chart keeps it going until a candle does."""
    n, first = len(df), len(df) - tail
    ts = df["t"].to_numpy().astype(np.int64)
    closes = df["close"].to_numpy(float)
    at = lambda i: int(ts[min(max(i, 0), n - 1)])  # noqa: E731

    def gone(r: dict) -> bool:                                               # closed through before the window starts: nothing of it can be seen
        born = r["dep"][1] if r.get("dep") else r["start"]
        b = close_through(closes, r["kind"], r["lo"], r["hi"], born)
        return b is not None and b < first
    zones_out: list[dict] = []
    swings_out: list[dict] = []
    for it in inds:
        params = {k: v for k, v in it.items() if k not in ("id", "type")}
        try:
            if it["type"] == "zones":
                recs = zones_mod.lifecycle(df, **params)
                for r in recs:
                    end = n - 1 if r["end"] is None else r["end"]
                    if gone(r):
                        continue
                    zones_out.append({
                        "id": it["id"], "kind": r["kind"], "lo": r["lo"], "hi": r["hi"], "t0": at(max(r["start"], first)), "t1": at(end), "alive": r["end"] is None,
                        "broken": r["broken"], "flip": r["flip"], "move": r["move"], "tests": r["tests"],
                        "base": [at(r["base"][0]), at(r["base"][1])] if r["base"] and r["base"][1] >= first else None,
                        "dep": [at(r["dep"][0]), at(r["dep"][1])] if r["dep"] and r["dep"][1] >= first else None,
                        "touches": [at(i) for i in r["touches"] if i >= first][:12]})
            elif it["type"] == "engulf":
                for r in engulf_mod.lifecycle(df, **params):
                    end = n - 1 if r["end"] is None else r["end"]
                    if gone(r):
                        continue
                    zones_out.append({
                        "id": it["id"], "kind": r["kind"], "lo": r["lo"], "hi": r["hi"], "t0": at(max(r["start"], first)), "t1": at(end), "alive": r["end"] is None,
                        "broken": r["why"] == "broken", "flip": False, "move": r["move"], "tests": len(r["touches"]), "src": "engulf", "why": r["why"], "run": r["run"],
                        "base": [at(r["base"][0]), at(r["base"][1])] if r["base"][1] >= first else None,
                        "dep": [at(r["dep"][0]), at(r["dep"][1])] if r["dep"][1] >= first else None,
                        "touches": [at(i) for i in r["touches"] if i >= first][:12]})
            elif it["type"] == "structure":
                for p in structure_mod.pivots(df, params.get("swing", 3)):
                    if p["i"] >= first:
                        swings_out.append({"id": it["id"], "t": at(p["i"]), "kind": p["kind"], "price": p["price"], "label": p["label"]})
        except Exception:  # noqa: BLE001 - a drawing aid must never fail the whole chart
            continue
    zones_out.sort(key=lambda z: z["t0"])
    return zones_out[-limit:], swings_out


def build_all(rows: list[dict], raw_spec: dict, tf: int, marks_always: bool = False, max_zones_total: int = 6000) -> dict:
    """Indicators, supply/demand zones and structure for EVERY loaded candle: the series is cut at data gaps (an indicator never spans one) and each uninterrupted
    stretch of 30+ candles is worked out on its own, then joined. Stretches shorter than that get nothing (and their candles are left out of `candles`)."""
    ts = np.array([r["t"] for r in rows], dtype=np.int64)
    out: dict = {"candles": [], "marks": {}, "signals": [], "factors": {}, "note": None, "errors": [], "need": 60, "have": len(rows), "runs": 0, "zones": [], "swings": []}
    acc: dict[str, dict] = {}
    total = 0
    for a, b in vec.contiguous_runs(ts, tf):
        part = rows[a:b]
        if len(part) < MIN_ROWS:
            continue
        r = build(part, raw_spec, None, len(part), None, marks_always, max_rows=len(part), zone_limit=max_zones_total)       # every zone of the stretch, not just the newest few
        n = len(r["candles"])
        for it in r["indicators"]:
            e = acc.setdefault(it["id"], {**it, "series": [{**s, "values": [None] * total} for s in it["series"]]})
            for k, s in enumerate(it["series"]):
                e["series"][k]["values"] += s["values"]
        for iid, e in acc.items():                                       # an indicator this stretch could not compute: blank for it
            if not any(it["id"] == iid for it in r["indicators"]):
                for s in e["series"]:
                    s["values"] += [None] * n
        out["candles"] += r["candles"]
        out["zones"] += r["zones"]
        out["swings"] += r["swings"]
        out["signals"] += [{**s, "i": s["i"] + total} for s in r["signals"]]
        out["errors"] += [e for e in r["errors"] if e not in out["errors"]]
        out["note"] = out["note"] or r["note"]
        out["need"] = r["need"]
        total += n
        out["runs"] += 1
    if not out["runs"]:
        raise ValueError(f"no stretch of {MIN_ROWS}+ uninterrupted candles in this pair's history (indicators never span a data gap)")
    out["indicators"] = list(acc.values())
    out["zones"] = sorted(out["zones"], key=lambda z: z["t0"])[-max_zones_total:]
    return out


def build(rows: list[dict], raw_spec: dict, exprs: dict[str, str] | None = None, count: int = 120, pats: dict[str, dict] | None = None, marks_always: bool = False,
          max_rows: int = MAX_ROWS, zone_limit: int = MAX_ZONES_DRAWN) -> dict:
    """rows: chronological closed candles. exprs: key -> condition text to evaluate on every candle (the builder's clauses)."""
    rows = rows[-max_rows:]
    if len(rows) < MIN_ROWS:
        raise ValueError(f"only {len(rows)} uninterrupted candles at the newest end of this pair's history (need {MIN_ROWS}+; indicators never span a data gap)")
    tail = max(20, min(int(count), len(rows)))
    df = evaluator.build_df(rows)
    errors: list[str] = []
    env, inds = _indicator_env(raw_spec if isinstance(raw_spec, dict) else {}, df, errors)

    marks: dict[str, dict] = {}
    for key, text in list((exprs or {}).items())[:MAX_EXPRS]:
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            marks[key] = {"hits": _bools(expr.evaluate(text, env, df.index), tail), "error": None}
        except Exception as e:  # noqa: BLE001
            marks[key] = {"hits": [], "error": str(e)}

    for key, raw in list((pats or {}).items())[:20]:                    # candle-sequence patterns: hits plus which candles played which step
        norm, errs = patterns.validate(raw, "pattern")
        if norm is None:
            marks[key] = {"hits": [], "error": "; ".join(errs), "spans": {}}
            continue
        series, found = patterns.match(df, env, norm, spans=True)
        first = len(df) - tail
        marks[key] = {"hits": _bools(series, tail), "error": None,
                      "spans": {str(t - first): [[k, a - first, b - first] for k, a, b in seq] for t, seq in found.items() if t >= first}}

    v = spec_mod.validate_spec(raw_spec)
    signals, note, per_factor = [], None, {}
    if v["ok"] and v["spec"]["kind"] == "factors":
        signals, per_factor = _signals(v["spec"], env, df, tail)
        skipped = [f["id"] for f in v["spec"]["factors"] if f["kind"] == "fn"]
        if skipped or v["spec"]["filters"]:
            note = "Markers use your condition factors and gates only; built-in factors and filters are applied by 'Evaluate now'."
    elif not v["ok"]:
        note = "Fix the validation errors to see signal markers."

    sub = df.iloc[-tail:]
    zones_out, swings_out = _zone_and_swing_marks(_with_default_marks(inds) if marks_always else inds, df, tail, zone_limit)
    return {
        "zones": zones_out, "swings": swings_out,
        "candles": [{"t": int(r.t), "o": float(r.open), "h": float(r.high), "l": float(r.low), "c": float(r.close),
                     "syn": bool(rows[len(rows) - tail + k].get("syn"))} for k, r in enumerate(sub.itertuples())],
        "indicators": [_plot_indicator(it, env, tail) for it in inds],
        "marks": marks, "signals": signals, "factors": per_factor, "note": note, "errors": errors,
        "need": int(v["spec"]["gates"]["min_candles"]) if v["ok"] else 60, "have": len(rows),
    }
