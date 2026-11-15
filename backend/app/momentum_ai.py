"""AI-built strategy from the recent market momentum - no predefined strategy involved.

STEP 1  THE INITIAL POINT  The newest confirmed swing (a swing low starts an up-leg, a swing high a down-leg) is where the current momentum began. Everything is measured
        from that point: how long ago, how far price travelled in ATRs, how efficiently (net move / path travelled), how many of the candles went with it, how deep the
        pullback from the leg's extreme is, whether the last candles are accelerating, ADX.
STEP 2  THE STRATEGY IS LEARNED  The same measurements are made for every candle of every pair in the stored history (using only what was known at that candle), labelled
        "did the next candle continue the leg?". A small decision tree (depth 3, big leaves) splits that history into momentum situations; each leaf becomes a rule
        ("leg 4+ candles old, moved 1.8-3 ATR, efficiency 0.6+ -> follow / fade the leg"). It is fitted on the OLDER 70 % of the history only.
STEP 3  THE STRATEGY IS TESTED  Each rule is scored on the newest 30 % - candles it never saw - and compared with break-even, with a 95 % range corrected for the number of rules.
STEP 4  THE TRADE  The current momentum falls into one rule; its follow / fade call is the trade. The grade is the TEST result of that rule, not the agreement of anything:
        A only when the rule's unseen-data win rate is proven above break-even, B when it is above but not proven, C when the test shows no edge.
Nothing here is a prediction guarantee; with C the app says plainly that its own test found no edge.
"""
from __future__ import annotations

import math
import sqlite3
import threading
import time

import numpy as np
import pandas as pd

from . import marketdata, vec
from .stats_util import wilson

SW = 3                                       # a swing is confirmed SW candles after it happened (no look-ahead)
FEATS = ["leg_len", "move_atr", "eff", "body_share", "pullback_atr", "accel", "last_body_atr", "adx"]
LABELS = {"leg_len": "candles since the initial point", "move_atr": "distance travelled from the initial point (ATR)", "eff": "move efficiency (net / path)",
          "body_share": "share of leg candles that went with it", "pullback_atr": "pullback from the leg's extreme (ATR)", "accel": "acceleration, last 3 vs previous 3 candles (ATR)",
          "last_body_atr": "last candle body with the leg (ATR)", "adx": "ADX"}
MIN_LEAF = 400
DEPTH = 3
VALID_FRACTION = 0.3
MIN_ROWS = 4000
CACHE_SECONDS = 1800
_cache: dict = {}
_lock = threading.Lock()


# ----------------------------------------------------------------------------------------------- step 1: the momentum state of every candle
def momentum_frame(d: pd.DataFrame) -> pd.DataFrame:
    """For each candle (columns t, open, high, low, close, n): the current leg's direction, its initial point and the momentum measurements. Uses only the past."""
    n = len(d)
    o, h, l, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    f = vec.featurize(d)
    atr = np.where(f["atr14"].to_numpy(float) > 0, f["atr14"].to_numpy(float), np.nan)
    idx = np.arange(n)
    hmax = pd.Series(h).rolling(2 * SW + 1, center=True).max().to_numpy()
    lmin = pd.Series(l).rolling(2 * SW + 1, center=True).min().to_numpy()
    is_hi = (h == hmax) & np.isfinite(hmax)
    is_lo = (l == lmin) & np.isfinite(lmin)
    known_hi = np.full(n, -1)
    known_lo = np.full(n, -1)
    known_hi[SW:] = np.where(is_hi, idx, -1)[: n - SW]                       # the swing at j is known at j + SW
    known_lo[SW:] = np.where(is_lo, idx, -1)[: n - SW]
    last_hi, last_lo = np.maximum.accumulate(known_hi), np.maximum.accumulate(known_lo)
    dirn = np.where(last_lo > last_hi, 1, np.where(last_hi > last_lo, -1, 0))
    org = np.where(dirn > 0, last_lo, np.where(dirn < 0, last_hi, -1))
    ok = org >= 0
    oi = np.where(ok, org, 0)
    oprice = np.where(dirn > 0, l[oi], h[oi])
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    cum_tr = np.cumsum(tr)
    cum_up, cum_dn = np.cumsum(c > o), np.cumsum(c < o)
    leg_len = idx - oi
    path = cum_tr - cum_tr[oi]
    net = (c - oprice) * dirn
    # the leg's extreme since it was confirmed (running max of highs for an up-leg / min of lows for a down-leg inside the same leg)
    grp = pd.Series(np.where(ok, org * 3 + (dirn > 0), -1))
    ext_up = pd.Series(h).groupby(grp.to_numpy()).cummax().to_numpy()
    ext_dn = pd.Series(l).groupby(grp.to_numpy()).cummin().to_numpy()
    ext = np.where(dirn > 0, ext_up, ext_dn)
    own = np.where(dirn > 0, cum_up - cum_up[oi], cum_dn - cum_dn[oi])
    c3, c6 = pd.Series(c).shift(3).to_numpy(), pd.Series(c).shift(6).to_numpy()          # (shift, not slicing: also fine for a series shorter than 7 candles)
    out = pd.DataFrame({
        "d": np.where(ok, dirn, 0), "origin_i": np.where(ok, org, -1), "origin_price": np.where(ok, oprice, np.nan),
        "leg_len": np.where(ok, leg_len, np.nan), "move_atr": np.where(ok, net / atr, np.nan),
        "eff": np.where(ok & (path > 0), np.clip(np.abs(net) / np.where(path > 0, path, np.nan), 0, 1), np.nan),
        "body_share": np.where(ok & (leg_len > 0), own / np.where(leg_len > 0, leg_len, np.nan), np.nan),
        "pullback_atr": np.where(ok, np.maximum(0.0, (ext - c) * dirn) / atr, np.nan),
        "accel": np.where(ok, ((c - c3) - (c3 - c6)) * dirn / atr, np.nan),
        "last_body_atr": np.where(ok, (c - o) * dirn / atr, np.nan), "adx": f["adx14"].to_numpy(float)})
    return out


# ----------------------------------------------------------------------------------------------- step 2: learn the strategy (decision tree on momentum situations)
def _gini(y: np.ndarray) -> float:
    p = y.mean() if len(y) else 0.5
    return 2 * p * (1 - p)


def _grow(X: np.ndarray, y: np.ndarray, depth: int, path: list, min_leaf: int) -> list[dict]:
    """Greedy CART with big leaves; returns leaves [{path: [(feature index, '<='|'>', threshold)], ...}]."""
    if depth == 0 or len(y) < 2 * min_leaf:
        return [{"path": path}]
    best, base = None, _gini(y)
    for j in range(X.shape[1]):
        col = X[:, j]
        for thr in np.unique(np.quantile(col, np.linspace(0.15, 0.85, 8))):
            left = col <= thr
            nl, nr = int(left.sum()), int((~left).sum())
            if nl < min_leaf or nr < min_leaf:
                continue
            gain = base - (nl * _gini(y[left]) + nr * _gini(y[~left])) / len(y)
            if best is None or gain > best[0]:
                best = (gain, j, float(thr))
    if best is None or best[0] < 1e-5:
        return [{"path": path}]
    _, j, thr = best
    left = X[:, j] <= thr
    return _grow(X[left], y[left], depth - 1, path + [(j, "<=", thr)], min_leaf) + _grow(X[~left], y[~left], depth - 1, path + [(j, ">", thr)], min_leaf)


def _in_leaf(X: np.ndarray, path: list) -> np.ndarray:
    m = np.ones(len(X), bool)
    for j, op, thr in path:
        m &= (X[:, j] <= thr) if op == "<=" else (X[:, j] > thr)
    return m


def rule_text(path: list) -> str:
    if not path:
        return "any momentum situation"
    tight: dict[tuple[int, str], float] = {}                 # several splits on one measurement collapse into the tightest bound
    for j, op, thr in path:
        k = (j, op)
        tight[k] = min(tight[k], thr) if op == "<=" and k in tight else max(tight[k], thr) if k in tight else thr
    parts = [f"{LABELS[FEATS[j]]} {'≤' if op == '<=' else '>'} {thr:.2f}" for (j, op), thr in tight.items()]
    return "; ".join(parts)


def build_pool(conn, tf: int, payouts: dict, min_payout: float = 80.0, max_rows: int = 200_000, meta: bool = False):
    """Every candle of every pair with its momentum state (aligned with the leg) and the label: did the NEXT candle go with the leg?
    meta=True also returns, per row, the pair and the leg direction (+1 up / -1 down) - used to replay the AI's rule as trades."""
    xs, ys, ts, ss, dd = [], [], [], [], []
    pairs = [s for (s,) in conn.execute("SELECT DISTINCT symbol FROM candles WHERE tf=?", (tf,)).fetchall() if marketdata.payout_ok(s, payouts, min_payout)]
    for sym in sorted(pairs):
        q = pd.DataFrame(conn.execute("SELECT ts, o, h, l, c, ticks FROM candles WHERE symbol=? AND tf=? ORDER BY ts", (sym, tf)).fetchall(),
                         columns=["t", "open", "high", "low", "close", "n"]).astype(float)
        for a, b in vec.contiguous_runs(q["t"].to_numpy().astype(np.int64), tf):
            if b - a < 120:
                continue
            r = q.iloc[a:b].reset_index(drop=True)
            m = momentum_frame(r)
            move = (r["close"].shift(-1) - r["open"].shift(-1)).to_numpy() * m["d"].to_numpy()
            M = m[FEATS].to_numpy(float)
            ok = np.isfinite(M).all(axis=1) & np.isfinite(move) & (move != 0) & (m["d"].to_numpy() != 0)
            ok[:60], ok[-1] = False, False
            if ok.any():
                xs.append(M[ok]); ys.append((move[ok] > 0).astype(float)); ts.append(r["t"].to_numpy()[ok])
                ss.append(np.full(int(ok.sum()), sym, dtype=object)); dd.append(m["d"].to_numpy()[ok])
    if not xs:
        empty = (np.empty((0, len(FEATS))), np.empty(0), np.empty(0))
        return (*empty, np.empty(0, dtype=object), np.empty(0)) if meta else empty
    X, y, t = np.vstack(xs), np.concatenate(ys), np.concatenate(ts)
    o = np.argsort(t, kind="stable")
    if meta:
        s, d = np.concatenate(ss), np.concatenate(dd)
        return X[o][-max_rows:], y[o][-max_rows:], t[o][-max_rows:], s[o][-max_rows:], d[o][-max_rows:]
    return X[o][-max_rows:], y[o][-max_rows:], t[o][-max_rows:]


def learn(conn, tf: int, payouts: dict, breakeven: float = 0.54) -> dict:
    """Steps 2 + 3: grow the tree on the older 70 % of the history, then score every leaf on the newest 30 %."""
    X, y, t = build_pool(conn, tf, payouts)
    if len(y) < MIN_ROWS:
        return {"ok": False, "rows": int(len(y)), "why": f"only {len(y):,} candles with a measurable momentum leg are stored for this timeframe (need {MIN_ROWS:,}) - collect more history first"}
    cut = int(len(y) * (1 - VALID_FRACTION))
    leaves = _grow(X[:cut], y[:cut], DEPTH, [], MIN_LEAF)
    z = 1.96 if len(leaves) <= 1 else 1.96 + 0.32 * math.log(len(leaves))      # wider range the more rules were tried (Bonferroni-style)
    out = []
    for lf in leaves:
        mt, mv = _in_leaf(X[:cut], lf["path"]), _in_leaf(X[cut:], lf["path"])
        nt, nv = int(mt.sum()), int(mv.sum())
        pt = float(y[:cut][mt].mean()) if nt else 0.5
        follow = pt >= 0.5
        wv = int((y[cut:][mv] == (1.0 if follow else 0.0)).sum())              # wins of the rule's own side on the unseen candles
        acc = wv / nv if nv else None
        lo, hi = wilson(wv, nv, z) if nv else (None, None)
        verdict = "none" if nv < 150 or acc is None else "proven" if lo > breakeven else "weak" if acc > breakeven else "none"
        out.append({"path": lf["path"], "rule": rule_text(lf["path"]), "side": "follow" if follow else "fade", "n_train": nt, "p_follow_train": pt,
                    "n_valid": nv, "wins_valid": wv, "acc_valid": acc, "lo": lo, "hi": hi, "verdict": verdict})
    return {"ok": True, "rows": int(len(y)), "train": cut, "valid": int(len(y) - cut), "leaves": out, "z": z, "built": time.time(), "breakeven": breakeven,
            "span": [int(t[0]), int(t[-1])], "base_follow": float(y.mean())}


def model_for(engine, tf: int, force: bool = False) -> dict:
    """The learned strategy for this timeframe, cached (rebuilt every 30 minutes or when the stored history grew by 5 %)."""
    db_path = getattr(engine, "db_path", ":memory:")
    conn, owned = (engine.db, False) if db_path == ":memory:" else (sqlite3.connect(f"file:{str(db_path).replace(chr(92), '/')}?mode=ro", uri=True, timeout=60, check_same_thread=False), True)
    try:                                                              # its own read-only connection: safe to call from a worker thread, never touches the live one
        count = conn.execute("SELECT COUNT(*) FROM candles WHERE tf=?", (tf,)).fetchone()[0]
        with _lock:
            hit = _cache.get((db_path, tf))
            if hit and not force and time.time() - hit["built_at"] < CACHE_SECONDS and count < hit["count"] * 1.05 + 50:
                return hit["m"]
            payouts = {s: i.get("payout") for s, i in engine.instruments.items() if i.get("payout")}
            m = learn(conn, tf, payouts)
            _cache[(db_path, tf)] = {"built_at": time.time(), "count": count, "m": m}
            return m
    finally:
        if owned:
            conn.close()


AI_KEY = "ai-momentum"           # the built-in strategy slot whose signals are made by this module (its spec never fires; the engine asks auto_result instead)
_building: set = set()
_skip_log: dict[str, str] = {}


def warm_model(engine, tf: int) -> dict | None:
    """The learned strategy if it is ready, WITHOUT ever blocking: when it is missing or stale a worker thread (re)builds it and None is returned meanwhile
    (a candle-close handler must never wait seconds for the learning)."""
    db_path = getattr(engine, "db_path", ":memory:")
    hit = _cache.get((db_path, tf))
    fresh = hit and time.time() - hit["built_at"] < CACHE_SECONDS
    if not fresh and (db_path, tf) not in _building:
        if db_path == ":memory:":                                   # in-memory test databases only work on their own thread: build inline
            return model_for(engine, tf, force=True)
        _building.add((db_path, tf))

        def work():
            try:
                model_for(engine, tf, force=True)
            except Exception as e:                                  # noqa: BLE001 - try again at a later candle, and say why
                try:
                    engine._log("ai-momentum-error", f"learning failed: {e}")
                except Exception:                                   # noqa: BLE001
                    pass
            finally:
                _building.discard((db_path, tf))
        threading.Thread(target=work, daemon=True, name="ai-momentum-learn").start()
    return hit["m"] if hit else None


def auto_result(engine, sym: str, tf: int, rows: list[dict], syn: bool, min_grade: str = "C"):
    """The AI-built strategy as a normal engine Result for the candle that just closed (so it is recorded, settled and scored like every other strategy)."""
    from . import strategy
    def skip(why: str):
        last = _skip_log.get(sym)
        if last != why:                                            # skips are not stored as signals, so say WHY once per change in the feed log
            _skip_log[sym] = why
            try:
                engine._log("ai-momentum", f"{sym}: {why}")
            except Exception:                                      # noqa: BLE001
                pass
        return strategy.Result("SKIP", 0.0, 0, 0, [], why, "")
    if len(rows) < 60:
        return skip(f"only {len(rows)} uninterrupted candles since the last data gap - the AI needs 60")
    if syn:
        return skip("recent data gap - the AI waits for clean candles")
    m = warm_model(engine, tf)
    if m is None:
        return skip("warming up: the AI is learning from the stored history")
    if not m["ok"]:
        return skip(m["why"])
    inst = engine.instruments.get(sym) or {}
    pay = inst.get("payout")
    be = 1 / (1 + float(pay) / 100) if pay else None
    rd = read_now(rows, m, be)
    if not rd["ok"]:
        return skip(rd["why"])
    lf = rd["leaf"]
    acc = lf["acc_valid"] or 0.5
    grade = "A" if lf["verdict"] == "proven" else "B" if lf["verdict"] == "weak" and acc >= 0.53 else "C"
    if {"C": 1, "B": 2, "A": 3}[grade] < {"C": 1, "B": 2, "A": 3}.get(min_grade, 1):
        return skip(f"grade {grade} is below the minimum ({min_grade}): the AI's test found no edge for this situation")
    conviction = max(0, min(100, round((acc - 0.5) * 1000)))
    sign = 1 if rd["trade"] == "CALL" else -1
    o = rd["origin"]
    fac = [{"key": "ai_rule", "name": f"AI rule: {lf['side']} the {rd['leg'].lower()}-leg", "weight": 100.0, "vote": float(sign),
            "note": f"leg began {o['candles_ago']} candles ago; {lf['rule']}; tested {acc * 100:.1f}% on {lf['n_valid']:,} unseen candles (break-even {rd['breakeven'] * 100:.1f}%)"}]
    return strategy.Result(rd["trade"], float(sign * conviction), conviction, 1, fac,
                           f"{rd['leg']}-leg from {o['candles_ago']} candles ago -> {lf['side']} (grade {grade})", "A" if grade == "A" else "B")


# ----------------------------------------------------------------------------------------------- step 4: the current momentum -> a rule -> the trade
def read_now(rows: list[dict], m: dict, breakeven: float | None = None) -> dict:
    """The momentum state after the last closed candle in `rows`, the rule it falls into, and the trade that rule calls."""
    d = pd.DataFrame(rows[-400:])[["t", "o", "h", "l", "c", "n"]].rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"}).astype(float).reset_index(drop=True)
    fr = momentum_frame(d)
    last = fr.iloc[-1]
    base = rows[-400:]
    if last["d"] == 0 or not np.isfinite(last[FEATS].to_numpy(float)).all():
        return {"ok": False, "why": "no confirmed swing yet in the stored candles, so the initial point of the momentum is unknown - wait for more candles"}
    oi = int(last["origin_i"])
    state = {k: float(last[k]) for k in FEATS}
    origin = {"t": int(base[oi]["t"]), "price": float(last["origin_price"]), "direction": "UP" if last["d"] > 0 else "DOWN", "candles_ago": int(last["leg_len"])}
    x = last[FEATS].to_numpy(float).reshape(1, -1)
    leaf = next((lf for lf in m["leaves"] if _in_leaf(x, lf["path"])[0]), None)
    if leaf is None:
        return {"ok": False, "why": "the momentum state matches none of the learned rules", "origin": origin, "state": state}
    d_ = int(last["d"])
    trade = ("CALL" if d_ > 0 else "PUT") if leaf["side"] == "follow" else ("PUT" if d_ > 0 else "CALL")
    be = breakeven if breakeven else m["breakeven"]
    acc = leaf["acc_valid"]
    verdict = "none" if leaf["n_valid"] < 150 or acc is None else "proven" if leaf["lo"] > be else "weak" if acc > be else "none"
    return {"ok": True, "origin": origin, "state": state, "leaf": {**leaf, "verdict": verdict, "path": None}, "trade": trade, "leg": "UP" if d_ > 0 else "DOWN", "breakeven": be}
