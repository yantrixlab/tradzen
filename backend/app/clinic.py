"""Loss analysis ("clinic"): in which situations does a strategy lose, and what filter might fix it - with statistics, not anecdotes.

Protocol (guards against fooling ourselves):
  * findings are fitted on the OLDER part of the trades only and then re-checked on the NEWEST part (the validation slice);
  * every bucket tested counts toward a Benjamini-Hochberg false-discovery correction (hundreds of buckets are checked);
  * a bucket needs a minimum number of decided trades before it is even tested;
  * a suggestion is only marked 'holds' when the validation slice agrees with the training slice.
The same code analyses backtest trades and live/paper signals (trades_from_signals).
"""
from __future__ import annotations

import copy
import json
import math

import numpy as np
import pandas as pd

from .spec import validate_spec
from .stats_util import summarize, wilson

MIN_N = 30                 # decided trades a bucket needs in the fit slice
MIN_VALID_N = 15           # decided trades the validation slice needs to confirm a finding
SESSION_WINDOWS = {"asia": "00:00-07:00", "europe": "07:00-13:00", "eu_us_overlap": "13:00-16:00", "us": "16:00-21:00", "late": "21:00-00:00"}
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# key, label, column, kind ('cat' | 'bin'), bin edges
DIMENSIONS = [
    {"key": "direction", "label": "Direction", "col": "direction", "kind": "cat"},
    {"key": "tf", "label": "Timeframe", "col": "tf", "kind": "cat"},
    {"key": "tier", "label": "Tier", "col": "tier", "kind": "cat"},
    {"key": "symbol", "label": "Pair", "col": "symbol", "kind": "cat"},
    {"key": "session", "label": "Session (UTC)", "col": "session", "kind": "cat"},
    {"key": "hour", "label": "Hour (UTC)", "col": "hour_utc", "kind": "cat"},
    {"key": "weekday", "label": "Weekday", "col": "weekday", "kind": "cat"},
    {"key": "trend_align", "label": "Versus EMA 9/21/50 trend", "col": "trend_align", "kind": "cat"},
    {"key": "adx", "label": "ADX (trend strength)", "col": "adx14", "kind": "bin", "edges": [0, 15, 20, 25, 30, 40, 1000]},
    {"key": "atr_ratio", "label": "Volatility (ATR vs 50-candle average)", "col": "atr_ratio", "kind": "bin", "edges": [0, 0.7, 1.0, 1.5, 1000]},
    {"key": "bb_pctb", "label": "Position in Bollinger band (%B)", "col": "bb_pctb", "kind": "bin", "edges": [-1000, 0, 0.2, 0.8, 1, 1000]},
    {"key": "bb_bw_pctile", "label": "Band width percentile", "col": "bb_bw_pctile", "kind": "bin", "edges": [0, 20, 40, 60, 80, 100.01]},
    {"key": "rsi", "label": "RSI(14)", "col": "rsi14", "kind": "bin", "edges": [0, 30, 45, 55, 70, 100.01]},
    {"key": "stoch_k", "label": "Stochastic %K", "col": "stoch_k", "kind": "bin", "edges": [0, 20, 50, 80, 100.01]},
    {"key": "body_ratio", "label": "Signal candle body / range", "col": "body_ratio", "kind": "bin", "edges": [0, 0.2, 0.5, 0.8, 1.01]},
    {"key": "streak", "label": "Same-colour streak before entry", "col": "streak_c", "kind": "cat"},
    {"key": "strength", "label": "Signal strength", "col": "strength", "kind": "bin", "edges": [0, 40, 55, 70, 101]},
    {"key": "agree", "label": "Factors agreeing", "col": "agree", "kind": "cat"},
]


# ----------------------------------------------------------------------------------------------- data
def trades_from_signals(engine, strategy_id: int | None = None, since: float | None = None) -> pd.DataFrame:
    """Settled live/paper signals of a strategy in the same table shape as backtest trades."""
    q = ("SELECT s.symbol, s.tf, s.basis_t, s.direction, s.tier, s.strength, s.agree, s.score, s.entry, s.exit, s.status, f.features "
         "FROM signals s JOIN signal_features f ON f.signal_id = s.id WHERE s.status IN ('win','loss','draw')")
    args: list = []
    if strategy_id is not None:
        q += " AND s.strategy_id = ?"
        args.append(strategy_id)
    if since:
        q += " AND s.created >= ?"
        args.append(since)
    rows = engine.db.execute(q + " ORDER BY s.basis_t", args).fetchall()
    recs = []
    for sym, tf, t, direction, tier, strength, agree, score, entry, exit_, status, feat in rows:
        payout = (engine.instruments.get(sym) or {}).get("payout") or 85.0
        r = {"symbol": sym, "tf": tf, "t": t, "dir": 1 if direction == "CALL" else -1, "tier": tier, "strength": strength, "agree": agree,
             "score": score, "entry": entry, "exit": exit_, "result": status, "payout": payout,
             "profit": payout / 100 if status == "win" else -1.0 if status == "loss" else 0.0}
        r.update(json.loads(feat) if feat else {})
        recs.append(r)
    return pd.DataFrame(recs)


def _prepare(trades: pd.DataFrame) -> pd.DataFrame:
    t = trades.copy()
    t["direction"] = np.where(t["dir"] == 1, "CALL", "PUT")
    if "streak" in t:
        t["streak_c"] = t["streak"].clip(-5, 5).astype("Int64")
    if "hour_utc" in t:
        t["hour_utc"] = pd.to_numeric(t["hour_utc"], errors="coerce").astype("Int64")
    if "trend_align" not in t and "ema_stack" in t:
        t["trend_align"] = t["dir"] * t["ema_stack"].fillna(0)
    return t


def _bucket_labels(t: pd.DataFrame, dim: dict) -> tuple[pd.Series, dict]:
    """(label per trade or NaN, label -> meta)"""
    col = dim["col"]
    if col not in t:
        return pd.Series([None] * len(t), index=t.index), {}
    s = t[col]
    if dim["kind"] == "cat":
        lab = s.map(lambda v: None if pd.isna(v) else (f"{int(v):+d}" if dim["key"] in ("trend_align", "streak") else
                                                         str(int(v)) if dim["key"] in ("hour", "agree", "tf", "weekday") else str(v)))
        meta = {str(l): {"value": v} for l, v in zip(lab, s) if l is not None}
        return lab, meta
    edges = dim["edges"]
    cut = pd.cut(pd.to_numeric(s, errors="coerce"), bins=edges, right=False)
    meta = {}
    names = {}
    total = len(cut.cat.categories)
    for idx, iv in enumerate(cut.cat.categories):
        lo, hi = float(iv.left), float(iv.right)
        names[iv] = (f"< {hi:g}" if lo <= -1000 else f">= {lo:g}" if hi >= 1000 else f"{lo:g} - {hi:g}")
        meta[names[iv]] = {"lo": lo, "hi": hi, "idx": idx, "total": total}
    return cut.map(lambda iv: names.get(iv) if not pd.isna(iv) else None), meta


def _cell(g: pd.DataFrame) -> dict:
    wins, losses = int((g["result"] == "win").sum()), int((g["result"] == "loss").sum())
    n = wins + losses
    out = {"n": n, "wins": wins, "losses": losses, "acc": wins / n if n else None, "ci_low": None, "ci_high": None,
           "expectancy": round(float(g["profit"].mean()), 4) if len(g) else None}
    if n:
        out["ci_low"], out["ci_high"] = wilson(wins, n)
    return out


def _z_p(p_hat: float, p0: float, n: int) -> float:
    if n == 0 or p0 <= 0 or p0 >= 1:
        return 1.0
    z = (p_hat - p0) / math.sqrt(p0 * (1 - p0) / n)
    return math.erfc(abs(z) / math.sqrt(2))


def _bh(pvals: list[float]) -> list[float]:
    m = len(pvals)
    order = np.argsort(pvals)
    q = np.empty(m)
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, pvals[i] * m / rank)
        q[i] = prev
    return q.tolist()


# ----------------------------------------------------------------------------------------------- suggestions
def suggest(dim_key: str, bucket: str, meta: dict, worse: bool) -> dict | None:
    """A filter spec that would remove the bad bucket, or None when it cannot be expressed as one filter."""
    if not worse:
        return None
    m = meta.get(bucket, {})
    # a bin in the upper half of the scale is cut off together with everything above it (a max-filter), a lower-half bin together with
    # everything below it (a min-filter); the 'try' step then checks on newer data whether cutting that much was a good idea
    upper = "lo" in m and (m["hi"] >= 1000 or m.get("idx", 0) >= m.get("total", 1) / 2) and m["lo"] > -1000
    if dim_key == "adx" and "lo" in m:
        if upper:
            return {"filter": {"type": "adx_max", "value": m["lo"]}, "text": f"skip signals when ADX >= {m['lo']:g}"}
        return {"filter": {"type": "adx_min", "value": m["hi"]}, "text": f"skip signals when ADX < {m['hi']:g}"}
    if dim_key == "atr_ratio" and "lo" in m:
        if upper:
            return {"filter": {"type": "atr_ratio_max", "value": m["lo"]}, "text": f"skip signals when volatility >= {m['lo']:g}x average"}
        return {"filter": {"type": "atr_ratio_min", "value": m["hi"]}, "text": f"skip signals when volatility < {m['hi']:g}x average"}
    if dim_key == "hour":
        h = int(bucket)
        return {"filter": {"type": "hours", "exclude": [f"{h:02d}:00-{(h + 1) % 24:02d}:00"]}, "text": f"skip signals during {h:02d}:00-{(h + 1) % 24:02d}:00 UTC"}
    if dim_key == "session" and bucket in SESSION_WINDOWS:
        return {"filter": {"type": "hours", "exclude": [SESSION_WINDOWS[bucket]]}, "text": f"skip signals in the {bucket.replace('_', '/')} session ({SESSION_WINDOWS[bucket]} UTC)"}
    if dim_key == "weekday":
        d = int(float(bucket))
        return {"filter": {"type": "weekdays", "exclude": [d]}, "text": f"skip signals on {WEEKDAYS[d]}"}
    if dim_key == "trend_align":
        if bucket == "-1":
            return {"filter": {"type": "trend_alignment", "value": "not_against"}, "text": "skip signals against the EMA trend"}
        if bucket == "+1":
            return {"filter": {"type": "trend_alignment", "value": "not_with"}, "text": "skip signals that go with the EMA trend"}
    if dim_key == "direction" and bucket in ("CALL", "PUT"):
        keep = "PUT" if bucket == "CALL" else "CALL"
        return {"filter": {"type": "direction_only", "value": keep}, "text": f"only take {keep} signals"}
    return None


def candidate_spec(spec: dict, filt: dict) -> dict:
    """The spec with the suggested filter added (merged into an existing filter of the same kind when there is one)."""
    out = copy.deepcopy(spec)
    typ = filt["type"]
    same = next((f for f in out["filters"] if f["type"] == typ), None)
    if same is None:
        out["filters"].append(copy.deepcopy(filt))
    elif typ == "hours":
        same["exclude"] = sorted(set(same["exclude"]) | set(filt["exclude"]))
    elif typ == "weekdays":
        same["exclude"] = sorted(set(same["exclude"]) | set(filt["exclude"]))
    elif typ in ("adx_max", "atr_ratio_max"):
        same["value"] = min(same["value"], filt["value"])
    elif typ in ("adx_min", "atr_ratio_min"):
        same["value"] = max(same["value"], filt["value"])
    else:
        same.update(filt)
    v = validate_spec(out)
    if not v["ok"]:
        raise ValueError("candidate spec invalid: " + "; ".join(v["errors"][:3]))
    return v["spec"]


# ----------------------------------------------------------------------------------------------- analysis
def analyze(trades: pd.DataFrame, valid_fraction: float = 0.3, min_n: int = MIN_N, q: float = 0.10) -> dict:
    if trades.empty:
        return {"n_trades": 0, "baseline": None, "dimensions": [], "findings": [], "split_t": None, "tests": 0}
    t = _prepare(trades)
    decided = t[t["result"].isin(["win", "loss"])].copy()
    split = int(decided["t"].quantile(1 - valid_fraction)) if len(decided) >= 2 * MIN_VALID_N and 0 < valid_fraction < 1 else None
    fit = decided[decided["t"] < split] if split is not None else decided
    val = decided[decided["t"] >= split] if split is not None else decided.iloc[0:0]
    base_fit, base_val, base_all = _cell(fit), _cell(val), _cell(decided)
    p0 = base_fit["acc"] if base_fit["acc"] is not None else 0.5

    dims_out, tests = [], []
    for dim in DIMENSIONS:
        labels, meta = _bucket_labels(decided, dim)
        if labels.isna().all():
            continue
        buckets = []
        for label in sorted({l for l in labels.dropna()}, key=lambda x: (len(x) > 8, x)):
            sel = labels == label
            g_all, g_fit, g_val = decided[sel], fit[sel[fit.index]], val[sel[val.index]]
            ca, cf, cv = _cell(g_all), _cell(g_fit), _cell(g_val)
            row = {"bucket": label, "all": ca, "fit": cf, "valid": cv, "lift_fit": None if cf["acc"] is None else round(cf["acc"] - p0, 4)}
            buckets.append(row)
            if cf["n"] >= min_n and base_fit["acc"] is not None:
                p = _z_p(cf["acc"], p0, cf["n"])
                tests.append({"dimension": dim["key"], "label": dim["label"], "bucket": label, "fit": cf, "valid": cv, "p": p, "meta": meta})
        dims_out.append({"key": dim["key"], "label": dim["label"], "buckets": buckets})

    qs = _bh([x["p"] for x in tests]) if tests else []
    findings = []
    for x, qv in zip(tests, qs):
        cf, cv = x["fit"], x["valid"]
        worse = cf["acc"] < p0
        sig = qv <= q
        holds = None
        if split is not None and cv["n"] >= MIN_VALID_N and base_val["acc"] is not None:
            holds = (cv["acc"] <= base_val["acc"] - 0.03) if worse else (cv["acc"] >= base_val["acc"] + 0.03)
        findings.append({"dimension": x["dimension"], "label": x["label"], "bucket": x["bucket"], "direction": "worse" if worse else "better",
                         "n": cf["n"], "wins": cf["wins"], "acc": cf["acc"], "ci_low": cf["ci_low"], "ci_high": cf["ci_high"],
                         "baseline": p0, "lift": round(cf["acc"] - p0, 4), "p": round(x["p"], 6), "q": round(qv, 6), "significant": sig,
                         "validation": {"n": cv["n"], "acc": cv["acc"], "baseline": base_val["acc"], "holds": holds},
                         "suggestion": suggest(x["dimension"], x["bucket"], x["meta"], worse) if sig else None})
    findings.sort(key=lambda f: (not f["significant"], f["q"], -abs(f["lift"])))
    return {"n_trades": len(trades), "n_decided": len(decided), "split_t": split, "tests": len(tests), "min_n": min_n, "q": q,
            "baseline": {"fit": base_fit, "valid": base_val, "all": base_all}, "dimensions": dims_out, "findings": findings}


def compare(base: pd.DataFrame, cand: pd.DataFrame, split_t: int | None) -> dict:
    """Baseline vs candidate on the fit and validation slices, with a plain-language verdict."""
    def seg(df, a, b):
        sel = df[(df["t"] >= a) & (df["t"] < b)] if len(df) else df
        s = summarize(sel)
        return {k: s[k] for k in ("trades", "settled", "accuracy", "ci_low", "ci_high", "breakeven", "expectancy", "profit", "max_drawdown")}
    lo, hi = -10 ** 12, 10 ** 12
    mid = split_t if split_t is not None else hi
    out = {"fit": {"base": seg(base, lo, mid), "candidate": seg(cand, lo, mid)},
           "valid": {"base": seg(base, mid, hi), "candidate": seg(cand, mid, hi)} if split_t is not None else None}
    if out["valid"] is None:
        out["verdict"], out["reason"] = "inconclusive", "not enough trades to hold back a validation period"
        return out
    b, c = out["valid"]["base"], out["valid"]["candidate"]
    if c["settled"] < 30:
        out["verdict"], out["reason"] = "too_few", f"only {c['settled']} validation trades remain after the filter (need 30)"
    elif b["expectancy"] is None or c["expectancy"] is None:
        out["verdict"], out["reason"] = "inconclusive", "no expectancy available"
    elif c["expectancy"] - b["expectancy"] >= 0.02 and c["trades"] >= 0.4 * b["trades"]:
        out["verdict"] = "holds"
        out["reason"] = f"on the newest data, expectancy per trade improved {b['expectancy']:+.3f} -> {c['expectancy']:+.3f} while keeping {c['trades']}/{b['trades']} trades"
    elif c["expectancy"] <= b["expectancy"]:
        out["verdict"], out["reason"] = "noise", "no improvement on the newest data: the pattern did not hold up (likely noise)"
    else:
        out["verdict"], out["reason"] = "weak", "slight improvement on the newest data, within what chance can produce"
    return out


# ----------------------------------------------------------------------------------------------- similar signals
SIMILAR_COLS = ["adx14", "atr_ratio", "bb_pctb", "rsi14", "stoch_k", "streak", "body_ratio", "ema_stack", "trend_align", "bb_bw_pctile"]


def similar(engine, signal_id: int, k: int = 15) -> dict | None:
    row = engine.db.execute("SELECT strategy_id, direction, symbol, tf FROM signals WHERE id=?", (signal_id,)).fetchone()
    if not row:
        return None
    sid, direction, symbol, tf = row
    pool = trades_from_signals(engine, sid)
    target = engine.db.execute("SELECT features FROM signal_features WHERE signal_id=?", (signal_id,)).fetchone()
    if pool.empty or not target:
        return {"signal_id": signal_id, "neighbours": [], "summary": None, "note": "no settled signals with a feature snapshot to compare against"}
    tf_ = json.loads(target[0])
    pool = pool[(pool["dir"] == (1 if direction == "CALL" else -1))]
    cols = [c for c in SIMILAR_COLS if c in pool and tf_.get(c) is not None]
    if len(pool) < 3 or not cols:
        return {"signal_id": signal_id, "neighbours": [], "summary": None, "note": "not enough comparable signals yet"}
    x = pool[cols].astype(float)
    std = x.std().replace(0, 1).fillna(1)
    dist = (((x - pd.Series({c: float(tf_[c]) for c in cols})) / std) ** 2).sum(axis=1) ** 0.5
    best = pool.assign(distance=dist).sort_values("distance").head(k)
    wins = int((best["result"] == "win").sum())
    losses = int((best["result"] == "loss").sum())
    neigh = [{"t": int(r.t), "symbol": r.symbol, "tf": int(r.tf), "result": r.result, "distance": round(float(r.distance), 3)} for r in best.itertuples()]
    return {"signal_id": signal_id, "features_used": cols, "neighbours": neigh,
            "summary": {"n": wins + losses, "wins": wins, "losses": losses, "acc": wins / (wins + losses) if wins + losses else None}}
