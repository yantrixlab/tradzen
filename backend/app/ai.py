"""AI advisor: an open, dependency-free model that reads every candle the way a trader reads it, and says - with measured evidence - how much to trust itself.

WHAT IT LEARNS  For every stored candle: candlestick psychology (body / wick shapes, where the candle closed in its range, engulfing, hammer, star, doji, inside
bar, runs of green or red), indicators (RSI, MACD, Bollinger, ADX, EMA stack ...), market structure (higher highs / lower lows, distance to the last swing) and
engulfing-candle zones (inside a zone, room to the next one), plus the hour. Label: did the NEXT candle close up. Model: histogram gradient boosting (minigbm.py).

HOW IT IS CHECKED  Walk-forward: the data is cut into time blocks; each block is predicted by a model trained only on what came BEFORE it (with a gap), so the
score is always on unseen data. A control model trained on shuffled labels must score ~0.5. A model is "proven" only if its AUC is significantly above 0.5 AND at
least 0.52 AND it holds in most blocks. Anything less is reported as exactly that - "no proven skill" - and the advisor then confirms nothing.

WHAT IT DOES LIVE  Reads each closed candle of the streaming pairs (probability the next candle closes up), keeps a log, scores its own calls when the next candle
arrives (so its REAL live record is always visible next to the validated one), retrains every night on all stored data - including everything collected live -
and, from the unseen-data predictions, maps which hours / pairs / situations were reliably better or worse (only after correcting for the number of cells tried).
It never trades and is never a promise.
"""
from __future__ import annotations

import math
import os
import pickle
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import engulf, evaluator, marketdata, minigbm, research, structure, vec
from .backtest import BacktestError

TF = 60
DEFAULTS = {"folds": 4, "train_cap": 120_000, "rounds": 120, "depth": 3, "min_payout": 80.0, "min_auc": 0.52, "alpha": 0.05, "min_test": 5000,
            "confident_frac": 0.2, "breakeven": 0.54, "embargo_rows": 600, "seed": 7, "min_rows": 4000, "max_rows": 600_000, "fdr_q": 0.10, "min_cell": 150}
LIVE_MIN_FOR_VERDICT = 200           # scored live calls before the live record can overrule the validation
_COLS: list[str] | None = None


# ----------------------------------------------------------------------------------------------- features
def candle_features(df: pd.DataFrame) -> pd.DataFrame:
    """One row of features per candle, using only that candle and the ones before it. df columns: t, open, high, low, close, n."""
    f = vec.featurize(df).drop(columns=["session"], errors="ignore")
    env = evaluator.base_env(df)
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    rng = (h - l).replace(0, np.nan)
    for k in ("bull_engulf", "bear_engulf", "hammer", "star", "doji", "inside_bar"):
        f["p_" + k] = env[k].astype(float)
    f["clv"] = ((c - l) - (h - c)) / rng                                   # close location: +1 closed at the high (buyers in control), -1 at the low
    f["clv3"], f["clv5"] = f["clv"].rolling(3).mean(), f["clv"].rolling(5).mean()
    atr = f["atr14"].replace(0, np.nan)
    f["body_atr"], f["range_atr"] = (c - o) / atr, rng / atr
    f["gap_prev"] = (o - c.shift()) / atr
    f["green3"], f["green5"] = (c > o).astype(float).rolling(3).sum(), (c > o).astype(float).rolling(5).sum()
    f["prev_body_atr"], f["prev2_body_atr"] = f["body_atr"].shift(), f["body_atr"].shift(2)
    st = structure.compute(df)
    f["ms_bias"], f["ms_trend"], f["ms_hh"], f["ms_hl"] = st.bias.to_numpy(), st.trend.to_numpy(), st.hh.to_numpy(), st.hl.to_numpy()
    f["ms_dist_sh"], f["ms_dist_sl"] = (c - st.sh) / atr, (c - st.sl) / atr
    eg = engulf.compute(df)
    f["eg_in_dz"], f["eg_in_sz"] = eg.dz_visit.notna().astype(float), eg.sz_visit.notna().astype(float)
    f["eg_room_up"], f["eg_room_dn"] = eg.room_up.clip(upper=20), eg.room_dn.clip(upper=20)
    f["eg_dz_body"], f["eg_sz_body"] = eg.dz_body.fillna(0), eg.sz_body.fillna(0)
    return f


def feature_columns() -> list[str]:
    global _COLS
    if _COLS is None:
        rng = np.random.default_rng(0)
        p = 10 + np.cumsum(rng.normal(0, 0.05, 300))
        d = pd.DataFrame({"t": 1_700_000_000 + 60 * np.arange(300.0), "open": p, "high": p + 0.06, "low": p - 0.06, "close": p + rng.normal(0, 0.03, 300), "n": 80.0})
        _COLS = list(candle_features(d).columns)
    return _COLS


# ----------------------------------------------------------------------------------------------- dataset
def build_dataset(conn, tf: int, payouts: dict, cfg: dict, progress=None, cancelled=None) -> dict:
    """Every usable candle of every pair paying at least `min_payout`: features, label (next candle up), time, pair."""
    xs, ys, ts, ss = [], [], [], []
    pairs = [s for (s,) in conn.execute("SELECT DISTINCT symbol FROM candles WHERE tf=?", (tf,)).fetchall() if marketdata.payout_ok(s, payouts, cfg["min_payout"], unknown=0.0)]
    for k, sym in enumerate(sorted(pairs)):
        if cancelled and cancelled():
            raise BacktestError("cancelled")
        q = pd.DataFrame(conn.execute("SELECT ts, o, h, l, c, ticks FROM candles WHERE symbol=? AND tf=? ORDER BY ts", (sym, tf)).fetchall(),
                         columns=["t", "open", "high", "low", "close", "n"]).astype(float)
        for a, b in vec.contiguous_runs(q["t"].to_numpy().astype(np.int64), tf):
            if b - a < 200:
                continue
            d = q.iloc[a:b].reset_index(drop=True)
            f = candle_features(d)
            move = (d["close"].shift(-1) - d["open"].shift(-1)).to_numpy()
            M = f.to_numpy(float)
            ok = np.isfinite(M).all(axis=1) & np.isfinite(move) & (move != 0)
            ok[:60] = False
            ok[-1] = False
            if ok.any():
                xs.append(M[ok]); ys.append((move[ok] > 0).astype(float)); ts.append(d["t"].to_numpy()[ok]); ss.append(np.full(int(ok.sum()), sym, dtype=object))
        if progress:
            progress((k + 1) / max(1, len(pairs)))
    if not xs:
        return {"X": np.empty((0, len(feature_columns()))), "y": np.empty(0), "t": np.empty(0), "sym": np.empty(0, dtype=object), "cols": feature_columns(), "pairs": 0}
    X, y, t, sym = np.vstack(xs), np.concatenate(ys), np.concatenate(ts), np.concatenate(ss)
    order = np.argsort(t, kind="stable")
    X, y, t, sym = X[order], y[order], t[order], sym[order]
    if len(y) > cfg["max_rows"]:
        X, y, t, sym = X[-cfg["max_rows"]:], y[-cfg["max_rows"]:], t[-cfg["max_rows"]:], sym[-cfg["max_rows"]:]
    return {"X": X, "y": y, "t": t, "sym": sym, "cols": feature_columns(), "pairs": len(set(sym.tolist()))}


# ----------------------------------------------------------------------------------------------- walk-forward validation
def walk_forward(ds: dict, cfg: dict, progress=None, cancelled=None) -> dict:
    """Predict each time block with a model trained only on what came before it. Returns the unseen-data scores and the headline statistics."""
    X, y, n = ds["X"], ds["y"], len(ds["y"])
    rng = np.random.default_rng(cfg["seed"])
    cuts = [int(n * f) for f in np.linspace(0.4, 1.0, cfg["folds"] + 1)]
    folds, oos_i, oos_s, ctrl_s = [], [], [], []
    for k in range(cfg["folds"]):
        if cancelled and cancelled():
            raise BacktestError("cancelled")
        lo, hi = cuts[k], cuts[k + 1]
        tr = np.arange(0, max(0, lo - cfg["embargo_rows"]))
        te = np.arange(lo, hi)
        sub = rng.choice(tr, size=min(len(tr), cfg["train_cap"]), replace=False)
        fit = minigbm.gbm_fit(X[sub], y[sub], rng=np.random.default_rng(cfg["seed"] + k), rounds=cfg["rounds"], depth=cfg["depth"])
        s = fit(X[te])
        c = minigbm.gbm_fit(X[sub], rng.permutation(y[sub]), rng=np.random.default_rng(cfg["seed"] + 50 + k), rounds=cfg["rounds"], depth=cfg["depth"])(X[te])     # shuffled labels
        folds.append({"fold": k + 1, "train": int(len(sub)), "test": int(len(te)), "from": int(ds["t"][lo]), "to": int(ds["t"][hi - 1]),
                      "auc": minigbm.auc(s, y[te]), "auc_control": minigbm.auc(c, y[te])})
        oos_i.append(te); oos_s.append(s); ctrl_s.append(c)
        if progress:
            progress(0.15 + 0.7 * (k + 1) / cfg["folds"])
    idx, score, ctrl = np.concatenate(oos_i), np.concatenate(oos_s), np.concatenate(ctrl_s)
    yt = y[idx]
    a, ac = minigbm.auc(score, yt), minigbm.auc(ctrl, yt)
    n1, n0 = int(yt.sum()), int((1 - yt).sum())
    st = research.auc_stats(a, n1, n0)
    control_ok = abs(ac - 0.5) <= 3 * (research.auc_stats(0.5, n1, n0)["band"] / 1.96)
    return {"idx": idx, "score": score, "auc": a, "auc_control": ac, "control_ok": control_ok, "stats": st, "n_test": int(len(idx)), "folds": folds,
            "top": {"top20": research.top_calls(score, yt, 0.2, cfg["breakeven"]), "top10": research.top_calls(score, yt, 0.1, cfg["breakeven"]), "top2": research.top_calls(score, yt, 0.02, cfg["breakeven"])}}


def verdict(wf: dict, cfg: dict) -> tuple[str, str]:
    """proven / weak / no_skill / need_data / control_failed - and why, in plain words."""
    a, st, nf = wf["auc"], wf["stats"], cfg["folds"]
    if wf["n_test"] < cfg["min_test"]:
        return "need_data", f"only {wf['n_test']:,} unseen candles (need {cfg['min_test']:,}) - too few to tell skill from luck"
    if not wf["control_ok"]:
        return "control_failed", f"the control model (shuffled labels) scored {wf['auc_control']:.3f} instead of ~0.5, so the pipeline cannot be trusted this run"
    good = sum(1 for f in wf["folds"] if f["auc"] > 0.5)
    if st["p"] < cfg["alpha"] and a >= cfg["min_auc"] and st["lo"] > 0.5 and good >= math.ceil(0.75 * nf):
        return "proven", f"AUC {a:.3f} on unseen data (95% range {st['lo']:.3f}-{st['hi']:.3f}), above 0.5 in {good} of {nf} time blocks"
    if st["p"] < cfg["alpha"] and a > 0.5:
        return "weak", f"AUC {a:.3f} is statistically real but below {cfg['min_auc']:.2f}: far too little to trade on"
    return "no_skill", f"AUC {a:.3f} (95% range {st['lo']:.3f}-{st['hi']:.3f}) is inside the chance band: on unseen data the model cannot tell up candles from down candles"


def platt(score: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """p(up) = sigmoid(a * score + b), fitted on the unseen-data scores (so the probabilities are as honest as the validation)."""
    a, b = 1.0, 0.0
    for _ in range(25):
        p = 1 / (1 + np.exp(-(a * score + b)))
        w = p * (1 - p) + 1e-9
        g = np.array([((p - y) * score).sum(), (p - y).sum()])
        H = np.array([[(w * score * score).sum(), (w * score).sum()], [(w * score).sum(), w.sum()]]) + np.eye(2) * 1e-6
        step = np.linalg.solve(H, g)
        a, b = a - step[0], b - step[1]
        if np.abs(step).max() < 1e-8:
            break
    return float(a), float(b)


# ----------------------------------------------------------------------------------------------- where is it better or worse? (from unseen-data predictions only)
def reliability_map(ds: dict, wf: dict, cfg: dict) -> dict:
    """For hours, pairs, volatility, structure and zone situations: how the model's most confident calls did on unseen data, against break-even.
    All cells are corrected together (Benjamini-Hochberg), and a cell must hold up in both halves of the unseen period. An empty list is the usual, honest answer."""
    cols = ds["cols"]
    ix = wf["idx"]
    y, score, sym = ds["y"][ix], wf["score"], ds["sym"][ix]
    X = ds["X"][ix]
    thr = np.quantile(np.abs(score), 1 - cfg["confident_frac"])
    conf = np.abs(score) >= thr
    call_up = score > 0
    right = (call_up == (y > 0.5)).astype(float)
    half = np.arange(len(y)) < len(y) // 2
    hour = ((ds["t"][ix] % 86400) // 3600).astype(int)
    col = lambda name: X[:, cols.index(name)]  # noqa: E731
    vol = col("atr_ratio")
    dims: list[tuple[str, np.ndarray]] = [("hour (UTC)", np.array([f"{(h // 3) * 3:02d}-{(h // 3) * 3 + 2:02d}" for h in hour])), ("pair", sym),
                                         ("volatility", np.where(vol < np.quantile(vol, 0.33), "quiet", np.where(vol > np.quantile(vol, 0.67), "busy", "normal"))),
                                         ("structure", np.where(col("ms_bias") > 0, "up-trend", np.where(col("ms_bias") < 0, "down-trend", "no trend"))),
                                         ("inside a zone", np.where(col("eg_in_dz") > 0, "demand zone", np.where(col("eg_in_sz") > 0, "supply zone", "no zone"))),
                                         ("last candle", np.where(col("candle_color") > 0, "green", "red"))]
    be = cfg["breakeven"]
    w0 = float(right[conf].mean()) if conf.any() else 0.5          # the model's typical confident accuracy: a cell is "avoid" only if it is clearly worse than this (below break-even alone would flag everything for a model with no skill)
    w0 = min(max(w0, 0.05), 0.95)
    cells = []
    for dim, labels in dims:
        for lab in sorted(set(labels.tolist())):
            m = (labels == lab) & conf
            n = int(m.sum())
            if n < cfg["min_cell"]:
                continue
            w = float(right[m].mean())
            z = max(0.0, (w - be) / math.sqrt(be * (1 - be) / n)) if w >= w0 else (w - w0) / math.sqrt(w0 * (1 - w0) / n)
            h1, h2 = m & half, m & ~half
            cells.append({"dim": dim, "cell": str(lab), "n": n, "win": w, "z": z, "p": math.erfc(abs(z) / math.sqrt(2)),
                          "win_first": float(right[h1].mean()) if h1.sum() > 30 else None, "win_second": float(right[h2].mean()) if h2.sum() > 30 else None})
    order = sorted(range(len(cells)), key=lambda i: cells[i]["p"])
    m_ = len(cells)
    cut = 0
    for rank, i in enumerate(order, 1):
        if cells[i]["p"] <= cfg["fdr_q"] * rank / max(1, m_):
            cut = rank
    for rank, i in enumerate(order, 1):
        c = cells[i]
        ref = be if c["z"] > 0 else w0
        sgn = 1 if c["z"] > 0 else -1
        consistent = c["win_first"] is not None and c["win_second"] is not None and sgn * (c["win_first"] - ref) > 0 and sgn * (c["win_second"] - ref) > 0
        c["flag"] = ("favour" if c["z"] > 0 else "avoid") if (rank <= cut and consistent) else None
    cells.sort(key=lambda c: c["p"])
    return {"confident_n": int(conf.sum()), "confident_win": float(right[conf].mean()) if conf.any() else None, "breakeven": be, "tested": m_, "cells": cells[:40],
            "favour": [c for c in cells if c["flag"] == "favour"], "avoid": [c for c in cells if c["flag"] == "avoid"]}


# ----------------------------------------------------------------------------------------------- the advisor
class Advisor:
    def __init__(self, engine) -> None:
        self.engine = engine
        self._lock = threading.Lock()
        self.model: dict | None = None
        self.report: dict | None = None
        self.state = {"running": False, "progress": 0.0, "error": None, "started": None}
        self._cancel = False
        self._last_log = 0.0
        db = Path(str(engine.db_path)).parent / "ai" if str(engine.db_path) != ":memory:" else None
        self.dir = db
        self._ensure_table()
        self._load()

    # ---- storage
    def _ensure_table(self) -> None:
        self.engine.db.execute("CREATE TABLE IF NOT EXISTS ai_log (symbol TEXT NOT NULL, tf INTEGER NOT NULL, candle_t INTEGER NOT NULL, p_up REAL NOT NULL, model_t REAL, "
                               "outcome INTEGER, PRIMARY KEY (symbol, tf, candle_t)) WITHOUT ROWID")
        self.engine.db.commit()

    def _load(self) -> None:
        if not self.dir:
            return
        try:
            with open(self.dir / "model.pkl", "rb") as fh:
                self.model = pickle.load(fh)
            import json
            self.report = json.loads((self.dir / "report.json").read_text())
        except Exception:  # noqa: BLE001 - no model yet, or an unreadable file: start without one
            self.model, self.report = None, None

    def _save(self) -> None:
        if not self.dir:
            return
        import json
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / "model.pkl", "wb") as fh:
            pickle.dump(self.model, fh)
        (self.dir / "report.json").write_text(json.dumps(self.report))

    # ---- training: in a SEPARATE low-priority process, so the live feed is never slowed down while the model learns
    def train(self, cfg: dict | None = None, sync: bool = False) -> bool:
        c = {**DEFAULTS, **(cfg or {})}
        with self._lock:
            if self._proc_alive() or self.state["running"]:
                return False
            self.state.update(running=True, progress=0.0, error=None, started=time.time())
        if sync or not self.dir:                                       # tests / an in-memory database: run right here
            conn, owned = self.engine.backtests._connect()
            try:
                payouts = {s: i.get("payout") for s, i in self.engine.instruments.items() if i.get("payout")}
                self.model, self.report = fit_and_report(conn, payouts, c, progress=lambda p: self.state.update(progress=p))
                self._save()
                self.state.update(running=False, progress=1.0)
            except Exception as e:  # noqa: BLE001
                self.state.update(running=False, error=str(e))
            finally:
                if owned:
                    conn.close()
            return True
        import json
        import subprocess
        import sys
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "progress.json").write_text(json.dumps({"progress": 0.0, "error": None, "done": False}))
        flags = 0x00004000 if os.name == "nt" else 0                    # BELOW_NORMAL_PRIORITY_CLASS
        root = Path(__file__).resolve().parent.parent
        log = open(self.dir / "train.log", "w")
        self._proc = subprocess.Popen([sys.executable, "-m", "app.ai", "--db", str(self.engine.db_path), "--out", str(self.dir), "--cfg", json.dumps(c)], cwd=root,
                                      stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
                                      preexec_fn=(lambda: os.nice(10)) if os.name != "nt" else None)          # low priority: the live feed must never wait for a training run
        return True

    _proc = None

    def _proc_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _refresh_training(self) -> None:
        """Mirror the training process into `state`; when it has finished, load what it saved."""
        if not self.dir or not self.state["running"] or self._proc is None:
            return
        import json
        try:
            prog = json.loads((self.dir / "progress.json").read_text())
        except Exception:  # noqa: BLE001
            prog = {}
        if self._proc.poll() is None:
            self.state.update(progress=float(prog.get("progress", 0.0)))
            return
        if prog.get("error") or not prog.get("done"):
            self.state.update(running=False, error=prog.get("error") or f"the training process stopped (exit code {self._proc.returncode}); see data/ai/train.log")
        else:
            self._load()
            self.state.update(running=False, progress=1.0, error=None)
        self._proc = None

    # ---- reading a candle
    def usable(self) -> bool:
        """The model may confirm or veto signals only while it has proven skill on unseen data AND its live record does not contradict that."""
        if not self.model or not self.report or self.report["verdict"] != "proven":
            return False
        live = self.live_record()
        return not (live["n"] >= LIVE_MIN_FOR_VERDICT and live["ci_hi"] is not None and live["ci_hi"] < 0.5)

    def read(self, sym: str, tf: int) -> dict:
        base = {"symbol": sym, "tf": tf, "available": False, "usable": False, "verdict": self.report["verdict"] if self.report else None}
        if not self.model:
            return {**base, "note": "No model trained yet. Train it on the AI page (it needs stored history)."}
        if tf != TF:
            return {**base, "note": "The AI is trained on 1-minute candles only."}
        rows, syn = self.engine._series(sym, tf).bridged_tail(tf)
        if len(rows) < 120:
            return {**base, "note": f"Only {len(rows)} candles stored for this pair (needs 120+ uninterrupted)."}
        df = evaluator.build_df(rows[-400:])
        f = candle_features(df)
        last = f.iloc[-1].to_numpy(float)
        if not np.isfinite(last).all():
            return {**base, "note": "Not enough clean data on the newest candle."}
        s = float(self.model["model"](last.reshape(1, -1))[0])
        a, b = self.model["platt"]
        p = float(1 / (1 + np.exp(-(a * s + b))))
        usable = self.usable()
        return {**base, "available": True, "p_up": p, "candle_t": int(rows[-1]["t"]), "usable": usable, "bridged": syn,
                "note": None if usable else (self.report["why"] if self.report else None)}

    @staticmethod
    def confirm(direction: str, rd: dict) -> dict:
        """What the AI says about a signal: confirmed / against / neutral - or unproven (the model has no proven skill, so it says nothing)."""
        if not rd.get("available") or not rd.get("usable"):
            return {"label": "unproven", "text": "AI: no proven skill - not used to confirm"}
        p = rd["p_up"] if direction == "CALL" else 1 - rd["p_up"]
        if p >= 0.53:
            return {"label": "confirmed", "text": f"AI agrees ({p * 100:.1f}% for this side)"}
        if p <= 0.47:
            return {"label": "against", "text": f"AI disagrees ({p * 100:.1f}% for this side)"}
        return {"label": "neutral", "text": f"AI is undecided ({p * 100:.1f}%)"}

    # ---- live record: log the call for every closed candle, score it when the next candle arrives
    def tick(self, now: float | None = None) -> None:
        if os.getenv("QXS_AI", "on").lower() in ("off", "0", "false", "no") or not self.model:
            return
        now = now or time.time()
        db = self.engine.db
        for sym, lt in list(self.engine.last_tick.items()):
            if now - lt["recv"] > 120:
                continue
            rows = self.engine._series(sym, TF).tail(1)
            if not rows:
                continue
            t = int(rows[-1]["t"])
            if db.execute("SELECT 1 FROM ai_log WHERE symbol=? AND tf=? AND candle_t=?", (sym, TF, t)).fetchone():
                continue
            rd = self.read(sym, TF)
            if rd.get("available") and rd.get("candle_t") == t:
                db.execute("INSERT OR IGNORE INTO ai_log (symbol, tf, candle_t, p_up, model_t) VALUES (?,?,?,?,?)", (sym, TF, t, rd["p_up"], self.model["trained_at"]))
        db.execute("UPDATE ai_log SET outcome = (SELECT CASE WHEN c.c > c.o THEN 1 WHEN c.c < c.o THEN 0 ELSE 2 END FROM candles c WHERE c.symbol = ai_log.symbol AND c.tf = ai_log.tf "
                   "AND c.ts = ai_log.candle_t + ai_log.tf) WHERE outcome IS NULL AND EXISTS (SELECT 1 FROM candles c WHERE c.symbol = ai_log.symbol AND c.tf = ai_log.tf AND c.ts = ai_log.candle_t + ai_log.tf)")
        db.commit()

    def live_record(self) -> dict:
        """How the model's calls really did on candles it only saw live: direction accuracy overall and for its most confident calls, with 95% ranges."""
        rows = self.engine.db.execute("SELECT p_up, outcome FROM ai_log WHERE outcome IN (0,1)").fetchall()
        n = len(rows)
        out = {"n": n, "acc": None, "ci_lo": None, "ci_hi": None, "confident_n": 0, "confident_acc": None}
        if not n:
            return out
        p = np.array([r[0] for r in rows]); o = np.array([r[1] for r in rows])
        hit = ((p > 0.5).astype(int) == o).astype(float)
        out["acc"] = float(hit.mean())
        z, ph = 1.96, out["acc"]                                           # Wilson interval: stays inside 0-100 % even for a handful of calls
        den = 1 + z * z / n
        mid = (ph + z * z / (2 * n)) / den
        half = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den
        out["ci_lo"], out["ci_hi"] = max(0.0, mid - half), min(1.0, mid + half)
        conf = np.abs(p - 0.5) >= np.quantile(np.abs(p - 0.5), 0.8)
        out["confident_n"] = int(conf.sum())
        out["confident_acc"] = float(hit[conf].mean()) if conf.any() else None
        return out

    # ---- status / schedule
    def status(self) -> dict:
        self._refresh_training()
        rep = self.report
        return {"trained": bool(self.model), "usable": self.usable(), "training": dict(self.state), "report": rep, "live": self.live_record(),
                "schedule_hour": research.schedule_hour()}

    def nightly(self, now: float | None = None) -> None:
        """Retrain every night (same hour as the research check) on all stored data - including what was collected live since the last run."""
        if os.getenv("QXS_AI", "on").lower() in ("off", "0", "false", "no") or self.state["running"]:
            return
        self._refresh_training()
        now = now or time.time()
        hour = research.schedule_hour()
        last = self.model["trained_at"] if self.model else None
        _, due = research.next_and_due(now, hour, last)
        if due and self._has_history():
            self.train()

    def _has_history(self) -> bool:
        return (self.engine.db.execute("SELECT COUNT(*) FROM candles WHERE tf=?", (TF,)).fetchone()[0] or 0) >= DEFAULTS["min_rows"] * 2


# ----------------------------------------------------------------------------------------------- the whole pipeline (also what the training process runs)
def fit_and_report(conn, payouts: dict, cfg: dict, progress=None, cancelled=None) -> tuple[dict, dict]:
    ds = build_dataset(conn, TF, payouts, cfg, progress=(lambda p: progress(0.15 * p)) if progress else None, cancelled=cancelled)
    if len(ds["y"]) < cfg["min_rows"]:
        raise BacktestError(f"only {len(ds['y']):,} usable candles (need {cfg['min_rows']:,}) - collect more history first")
    wf = walk_forward(ds, cfg, progress=progress, cancelled=cancelled)
    v, why = verdict(wf, cfg)
    a, b = platt(wf["score"], ds["y"][wf["idx"]])
    rng = np.random.default_rng(cfg["seed"])
    n = len(ds["y"])
    sub = rng.choice(n, size=min(n, cfg["train_cap"]), replace=False)
    final = minigbm.gbm_fit(ds["X"][sub], ds["y"][sub], rng=np.random.default_rng(cfg["seed"] + 99), rounds=cfg["rounds"], depth=cfg["depth"])
    now = time.time()
    report = {"trained_at": now, "config": dict(cfg), "rows": int(n), "pairs": int(ds["pairs"]), "first_t": int(ds["t"][0]), "last_t": int(ds["t"][-1]),
              "verdict": v, "why": why, "auc": wf["auc"], "auc_lo": wf["stats"]["lo"], "auc_hi": wf["stats"]["hi"], "p": wf["stats"]["p"], "auc_control": wf["auc_control"],
              "n_test": wf["n_test"], "folds": wf["folds"], "top": wf["top"], "map": reliability_map(ds, wf, cfg), "platt": [a, b], "features": len(ds["cols"])}
    return {"model": final, "platt": (a, b), "cols": ds["cols"], "trained_at": now, "verdict": v}, report


def _main() -> None:
    """python -m app.ai --db signals.db --out data/ai --cfg '{...}': train and save, writing progress.json as it goes (run by Advisor.train)."""
    import argparse
    import json
    import sqlite3
    import traceback
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cfg", default="{}")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    def write(**kw):
        (out / "progress.json").write_text(json.dumps({"progress": 0.0, "error": None, "done": False, **kw}))
    try:
        conn = sqlite3.connect(f"file:{Path(a.db).as_posix()}?mode=ro", uri=True, timeout=60)
        payouts = {s: p for s, p in conn.execute("SELECT symbol, payout FROM instruments WHERE payout IS NOT NULL")}
        model, report = fit_and_report(conn, payouts, {**DEFAULTS, **json.loads(a.cfg)}, progress=lambda p: write(progress=round(float(p), 3)))
        tmp = out / "model.pkl.tmp"
        with open(tmp, "wb") as fh:
            pickle.dump(model, fh)
        (out / "report.json.tmp").write_text(json.dumps(report))
        os.replace(out / "report.json.tmp", out / "report.json")           # model last: a half-written pair is never read as complete
        os.replace(tmp, out / "model.pkl")
        write(progress=1.0, done=True)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        write(error=str(e))


if __name__ == "__main__":
    _main()

