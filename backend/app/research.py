"""Nightly research check: does ANY model find information in the last few closed candles about the next one?

Every night (and on demand) it rebuilds a dataset from all stored 1m candles - for each candle, the last N closed candles scaled by recent volatility plus the
hour of day, and the label "did the NEXT candle close up" - trains models on the older 70 % and scores them on the newest 30 %, which they never saw.
Because the answer so far has been "no", it is built to say so plainly and to refuse to be fooled:

* the yardstick is AUC on the unseen period; 0.5 = no information, and a chance band is shown from the sample size;
* a result counts only if it is statistically significant AFTER correcting for the number of models tried (Bonferroni), AND at least `min_auc`
  (default 0.52 - a significant 0.505 is real but far too small to trade), AND the test period is big enough;
* a control model trained on SHUFFLED labels must score ~0.5; if it does not, the pipeline is broken and the verdict is withheld;
* verdicts: no_signal / weak / signal / need_data / control_failed. A 'signal' is a reason to investigate with the sweep tool, never a promise.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import minigbm, vec
from . import sentiment as sentiment_mod
from .candles import is_display_only
from .backtest import BacktestError

DEFAULTS = {"windows": [5, 10], "tf": 60, "test_fraction": 0.3, "min_test": 5000, "min_auc": 0.52, "alpha": 0.05, "max_rows": 400_000,
            "breakeven": 0.54, "seed": 7, "min_rows": 3000}
EMBARGO_S = 600                      # candles this close before the split are dropped from training so no label overlaps the test period
MODELS = (("logistic regression", minigbm.logistic_fit), ("gradient boosting", minigbm.gbm_fit))
VERDICT_RANK = {"signal": 0, "weak": 1, "no_signal": 2}


# ----------------------------------------------------------------------------------------------- dataset
def window_features(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, t: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For every candle i: the last `n` closed candles (o,h,l,c) relative to candle i's close, in ATRs, + hour of day. Label: next candle closed up."""
    prev = np.r_[c[0], c[:-1]]
    tr = np.maximum.reduce([h - l, np.abs(h - prev), np.abs(l - prev)])
    atr = pd.Series(tr).rolling(14).mean().to_numpy()
    idx = np.arange(max(n - 1, 14), len(c) - 1)
    cols = []
    for k in range(n):
        j = idx - k
        for arr in (o, h, l, c):
            cols.append((arr[j] - c[idx]) / atr[idx])
    hour = ((t[idx] % 86400) / 86400) * 2 * np.pi
    X = np.column_stack(cols + [np.sin(hour), np.cos(hour)])
    move = c[idx + 1] - o[idx + 1]                                 # entry at the NEXT candle's open, settle at its close - same as every backtest here
    ok = np.isfinite(X).all(axis=1) & (move != 0)
    return X[ok], (move[ok] > 0).astype(float), t[idx][ok]


def build_dataset(conn, n: int, tf: int, max_rows: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    xs, ys, ts, pairs = [], [], [], 0
    for (sym,) in conn.execute("SELECT DISTINCT symbol FROM candles WHERE tf=?", (tf,)).fetchall():
        if is_display_only(sym):
            continue
        rows = conn.execute("SELECT ts, o, h, l, c FROM candles WHERE symbol=? AND tf=? ORDER BY ts", (sym, tf)).fetchall()
        if len(rows) < n + 60:
            continue
        a = np.array(rows, dtype=float)
        got = False
        for s, e in vec.contiguous_runs(a[:, 0].astype(np.int64), tf):          # never build a window across a data gap
            if e - s < n + 40:
                continue
            r = a[s:e]
            X, y, t = window_features(r[:, 1], r[:, 2], r[:, 3], r[:, 4], r[:, 0], n)
            if len(y):
                xs.append(X), ys.append(y), ts.append(t)
                got = True
        pairs += got
    if not xs:
        return np.empty((0, 4 * n + 2)), np.empty(0), np.empty(0), 0
    X, y, t = np.vstack(xs), np.concatenate(ys), np.concatenate(ts)
    order = np.argsort(t, kind="stable")
    X, y, t = X[order], y[order], t[order]
    if len(y) > max_rows:                                           # keep the newest rows: the question is what works NOW
        X, y, t = X[-max_rows:], y[-max_rows:], t[-max_rows:]
    return X, y, t, pairs


# ----------------------------------------------------------------------------------------------- statistics
def auc_stats(a: float, n1: int, n0: int) -> dict:
    """Hanley-McNeil standard error for display, and the one-sided p-value against the null AUC = 0.5."""
    q1, q2 = a / (2 - a), 2 * a * a / (1 + a)
    var = (a * (1 - a) + (n1 - 1) * (q1 - a * a) + (n0 - 1) * (q2 - a * a)) / (n1 * n0)
    se = math.sqrt(max(var, 0.0))
    se_null = math.sqrt((n1 + n0 + 1) / (12 * n1 * n0))
    z = (a - 0.5) / se_null
    return {"se": se, "lo": a - 1.96 * se, "hi": a + 1.96 * se, "z": z, "p": 0.5 * math.erfc(z / math.sqrt(2)), "band": 1.96 * se_null}


def top_calls(score: np.ndarray, y: np.ndarray, frac: float, be: float) -> dict:
    conf = np.abs(score)
    k = conf >= np.quantile(conf, 1 - frac)
    n = int(k.sum())
    win = float(((score[k] > 0).astype(float) == y[k]).mean()) if n else None
    z = None if not n else (win - be) / math.sqrt(be * (1 - be) / n)
    return {"n": n, "win": win, "z_vs_breakeven": z}


def judge_model(a: float, p_adj: float, n_test: int, cfg: dict) -> tuple[str, str]:
    if n_test < cfg["min_test"]:
        return "need_data", f"only {n_test:,} test candles (need {cfg['min_test']:,}) - too few to tell skill from luck"
    if p_adj < cfg["alpha"] and a >= cfg["min_auc"]:
        return "signal", f"AUC {a:.3f} on unseen data, significant after correcting for {cfg['_k']} models tried (p={p_adj:.4f})"
    if p_adj < cfg["alpha"] and a > 0.5:
        return "weak", f"AUC {a:.3f} is statistically real but below {cfg['min_auc']:.2f} - far too little to trade on"
    return "no_signal", f"AUC {a:.3f} is inside the chance band for this sample size"


# ----------------------------------------------------------------------------------------------- the check
def run_check(conn, cfg: dict | None = None, progress=None, cancelled=None) -> dict:
    cfg = {**DEFAULTS, **(cfg or {})}
    windows = [int(w) for w in cfg["windows"]]
    if not windows or any(not 2 <= w <= 40 for w in windows):
        raise BacktestError("windows must be between 2 and 40 candles")
    cfg["_k"] = len(windows) * len(MODELS)                           # models tried this run: the multiple-testing correction
    rng = np.random.default_rng(cfg["seed"])
    models, data, control = [], None, None
    steps = len(windows) * len(MODELS) + 1
    done = 0
    for n in windows:
        if cancelled and cancelled():
            raise BacktestError("cancelled")
        X, y, t, pairs = build_dataset(conn, n, cfg["tf"], cfg["max_rows"])
        if len(y) < cfg["min_rows"]:
            raise BacktestError(f"only {len(y):,} usable candles (need {cfg['min_rows']:,}) - collect more history first")
        cut = float(np.quantile(t, 1 - cfg["test_fraction"]))
        tr, te = t < cut - EMBARGO_S, t >= cut
        if data is None:
            data = {"rows": int(len(y)), "pairs": int(pairs), "train": int(tr.sum()), "test": int(te.sum()), "split_t": int(cut), "first_t": int(t[0]), "last_t": int(t[-1]),
                    "up_share_test": float(y[te].mean())}
        n1, n0 = int(y[te].sum()), int((1 - y[te]).sum())
        for name, fit in MODELS:
            f = fit(X[tr], y[tr], rng)
            s_te = f(X[te])
            a_tr, a_te = minigbm.auc(f(X[tr]), y[tr]), minigbm.auc(s_te, y[te])
            st = auc_stats(a_te, n1, n0)
            p_adj = min(1.0, st["p"] * cfg["_k"])
            verdict, reason = judge_model(a_te, p_adj, int(te.sum()), cfg)
            models.append({"window": n, "model": name, "auc_train": a_tr, "auc_test": a_te, "ci_low": st["lo"], "ci_high": st["hi"], "band": st["band"], "p_adj": p_adj,
                           "top10": top_calls(s_te, y[te], 0.10, cfg["breakeven"]), "top2": top_calls(s_te, y[te], 0.02, cfg["breakeven"]), "verdict": verdict, "reason": reason})
            done += 1
            if progress:
                progress(done / steps)
        if control is None:
            # control: shuffle the labels of BOTH periods, so they are independent of every feature however much real structure the data has. The same
            # pipeline must then score ~0.5; if it does not, the split / scoring machinery is biased and no result from this run can be trusted.
            yp = rng.permutation(y)
            f = minigbm.logistic_fit(X[tr], yp[tr], rng)
            ca = minigbm.auc(f(X[te]), yp[te])
            cs = auc_stats(0.5, int(yp[te].sum()), int((1 - yp[te]).sum()))
            control = {"auc_test": ca, "ok": abs(ca - 0.5) <= 3 * (cs["band"] / 1.96)}
    if progress:
        progress(1.0)
    best = min(models, key=lambda m: (VERDICT_RANK.get(m["verdict"], 3), -m["auc_test"]))
    if not control["ok"]:
        verdict, summary = "control_failed", f"The control model (shuffled labels) scored {control['auc_test']:.3f} instead of ~0.5, so this run's numbers cannot be trusted. Re-run; if it repeats, the pipeline has a bug."
    elif best["verdict"] == "need_data":
        verdict, summary = "need_data", best["reason"]
    else:
        verdict = best["verdict"]
        summary = {"no_signal": f"No model found information in the last few candles: the best unseen-data AUC was {best['auc_test']:.3f} (chance band 0.500 +/- {best['band']:.3f}).",
                   "weak": f"One model found a real but tiny effect (AUC {best['auc_test']:.3f}) - too small to trade; keep watching it.",
                   "signal": f"A model cleared every gate: {best['model']} on the last {best['window']} candles, AUC {best['auc_test']:.3f} on unseen data. Investigate it with a sweep before trusting it."}[verdict]
    return {"config": {k: v for k, v in cfg.items() if not k.startswith("_")}, "data": data, "models": models, "control": control, "verdict": verdict, "summary": summary,
            "best": {k: best[k] for k in ("window", "model", "auc_test", "verdict")}, "tried": cfg["_k"]}


# ----------------------------------------------------------------------------------------------- persistence, schedule
def schedule_hour() -> int | None:
    """Hour (UTC) of the nightly run from QXS_RESEARCH_HOUR; 'off' disables it. Default 03."""
    raw = os.getenv("QXS_RESEARCH_HOUR", "3").strip().lower()
    if raw in ("", "off", "none", "false", "no"):
        return None
    try:
        return max(0, min(23, int(raw)))
    except ValueError:
        return 3


def next_and_due(now: float, hour: int | None, last_created: float | None) -> tuple[float | None, bool]:
    """(next scheduled run time, is a run due now). A missed run (PC was off at the hour) is made up as soon as the backend is running again."""
    if hour is None:
        return None, False
    today = dt.datetime.fromtimestamp(now, dt.timezone.utc).replace(hour=hour, minute=0, second=0, microsecond=0)
    sched = today.timestamp()
    due = now >= sched and (last_created is None or last_created < sched)
    nxt = sched if now < sched else sched + 86400 if (last_created is not None and last_created >= sched) else now
    return nxt, due


def max_run_seconds() -> float:
    """A run that takes longer than this is killed (QXS_RESEARCH_MAX_MIN, default 90 minutes): a normal run is minutes, and a stuck one must never hold the machine."""
    try:
        return max(1.0, float(os.getenv("QXS_RESEARCH_MAX_MIN", "90")) * 60)
    except ValueError:
        return 90 * 60.0


def execute_run(conn, run_id: int, cfg: dict, sentiment) -> None:
    """The work of one run, on `conn` (its own connection): progress and result go to the research_runs row; a failure is recorded on the row, never raised.
    `sentiment`: a function returning the sentiment-study dict (the server passes its engine's, the separate process builds it from the database)."""
    try:
        def progress(p):
            conn.execute("UPDATE research_runs SET progress=? WHERE id=?", (round(p, 3), run_id))
            conn.commit()

        res = run_check(conn, cfg, progress)
        try:                                                      # the one other information source: how far the sentiment test has got
            s = sentiment()
            res["sentiment"] = {"matched": s["matched"], "edge_found": s["edge_found"], "needed_per_bucket": s["needed_per_bucket"]}
        except Exception:                                         # noqa: BLE001 - a side note must never fail the run
            res["sentiment"] = None
        conn.execute("UPDATE research_runs SET status='done', progress=1, finished=?, result=?, verdict=?, best_auc=? WHERE id=?",
                     (time.time(), json.dumps(res), res["verdict"], res["best"]["auc_test"], run_id))
        conn.execute("DELETE FROM research_runs WHERE id <= (SELECT MAX(id) FROM research_runs) - 200")      # keep the last 200 runs
        conn.commit()
    except Exception as e:                                         # noqa: BLE001 - report, never crash the server
        conn.rollback()
        conn.execute("UPDATE research_runs SET status='error', finished=?, error=? WHERE id=?", (time.time(), str(e)[:500], run_id))
        conn.commit()


class ResearchRunner:
    def __init__(self, engine) -> None:
        self.engine = engine
        self._lock = threading.Lock()
        self.running_id: int | None = None
        self._proc: subprocess.Popen | None = None
        self._started = 0.0
        # a run that was going when the server stopped can never finish: close it, or it would show as running for ever
        try:
            engine.db.execute("UPDATE research_runs SET status='error', error='interrupted by a restart', finished=? WHERE status='running'", (time.time(),))
            engine.db.commit()
        except Exception:                                          # noqa: BLE001 - no table yet / read-only: nothing to close
            pass

    def _last_created(self) -> float | None:
        r = self.engine.db.execute("SELECT MAX(created) FROM research_runs").fetchone()
        return r[0]

    def _command(self, run_id: int, cfg: dict) -> list[str]:
        return [sys.executable, "-m", "app.research", "--db", str(self.engine.db_path), "--run-id", str(run_id), "--cfg", json.dumps(cfg)]

    def _fail(self, run_id: int | None, why: str) -> None:
        if run_id is not None:
            self.engine.db.execute("UPDATE research_runs SET status='error', error=?, finished=? WHERE id=? AND status='running'", (why[:500], time.time(), run_id))
            self.engine.db.commit()

    def _reap(self) -> None:
        """Called every minute (and by status): notice a finished, crashed or overlong research process. The row is closed here only when the process could not do it itself."""
        p = self._proc
        if p is None:
            return
        rc = p.poll()
        if rc is None:
            limit = max_run_seconds()
            if time.time() - self._started <= limit:
                return
            p.kill()
            try:
                p.wait(timeout=10)
            except Exception:                                      # noqa: BLE001
                pass
            self._fail(self.running_id, f"stopped: it ran longer than {limit / 60:.0f} minutes (QXS_RESEARCH_MAX_MIN)")
            self.engine._log("research-timeout", f"run {self.running_id} stopped after {limit / 60:.0f} minutes")
        elif rc != 0:
            self._fail(self.running_id, f"the research process stopped (exit code {rc}); see data/ai/research.log")
        else:
            self._fail(self.running_id, "the research process ended without a result; see data/ai/research.log")     # no-op when it wrote its result
        self._proc = None
        self.running_id = None

    def stop(self) -> bool:
        """Stop the running check (the process is killed; the run is recorded as stopped). False when none is running. A thread run (tests) cannot be stopped."""
        p = self._proc
        if p is None or self.running_id is None:
            return False
        p.kill()
        try:
            p.wait(timeout=10)
        except Exception:                                          # noqa: BLE001
            pass
        self._fail(self.running_id, "stopped by an administrator")
        self._proc = None
        self.running_id = None
        return True

    def status(self, now: float | None = None) -> dict:
        self._reap()
        now = now or time.time()
        hour = schedule_hour()
        nxt, due = next_and_due(now, hour, self._last_created())
        return {"hour_utc": hour, "running": self.running_id is not None, "running_id": self.running_id, "next_run": nxt, "due": due, "last_run": self._last_created()}

    def tick(self) -> int | None:
        """Called every minute by the server: starts the nightly run when it is due and none is running."""
        self._reap()
        if self.running_id is None and self.status()["due"]:
            try:
                return self.start({}, source="nightly")
            except BacktestError:
                return None
        return None

    def start(self, cfg: dict | None = None, source: str = "manual", sync: bool = False) -> int:
        with self._lock:
            if self.running_id is not None:
                raise BacktestError("a research check is already running")
            run_cfg = {**DEFAULTS, **{k: v for k, v in (cfg or {}).items() if v is not None}}
            if not run_cfg["windows"] or any(not 2 <= int(w) <= 40 for w in run_cfg["windows"]):
                raise BacktestError("windows must be between 2 and 40 candles")
            cur = self.engine.db.execute("INSERT INTO research_runs (created, source, config, status, progress) VALUES (?,?,?,?,?)",
                                         (time.time(), source, json.dumps(run_cfg), "running", 0.0))
            self.engine.db.commit()
            run_id = cur.lastrowid
            self.running_id = run_id
        if sync or self.engine.db_path == ":memory:":
            self._execute(run_id, run_cfg)
        else:
            self._spawn(run_id, run_cfg)
        return run_id

    def _spawn(self, run_id: int, cfg: dict) -> None:
        """The run goes to a SEPARATE low-priority process: the check is heavy pure-Python/numpy work, and in a thread of the server it starves the web server of the CPU."""
        try:
            self.engine._flush(force=True)                         # the process reads the database: everything buffered must be in it
            log_dir = Path(str(self.engine.db_path)).parent / "ai"
            log_dir.mkdir(parents=True, exist_ok=True)
            flags = 0x00004000 | (0x08000000 if os.name == "nt" else 0)      # BELOW_NORMAL priority, no console window
            log = open(log_dir / "research.log", "ab")
            self._proc = subprocess.Popen(self._command(run_id, cfg), cwd=str(Path(__file__).resolve().parent.parent), stdout=log, stderr=log,
                                          creationflags=flags if os.name == "nt" else 0, preexec_fn=(lambda: os.nice(10)) if os.name != "nt" else None)
            self._started = time.time()
        except Exception as e:                                     # noqa: BLE001
            self._fail(run_id, f"could not start the research process: {e}")
            self.running_id = None
            raise BacktestError(f"could not start the research process: {e}") from e

    def _execute(self, run_id: int, cfg: dict) -> None:
        """In this process (tests and an in-memory database)."""
        conn, owned = self.engine.backtests._connect()
        try:
            execute_run(conn, run_id, cfg, lambda: self.engine.sentiment_study(60, 30, None))
        finally:
            self.running_id = None
            if owned:
                conn.close()

    def get(self, run_id: int) -> dict | None:
        r = self.engine.db.execute("SELECT id, created, finished, source, config, status, progress, error, result, verdict, best_auc FROM research_runs WHERE id=?", (run_id,)).fetchone()
        if not r:
            return None
        d = dict(zip(["id", "created", "finished", "source", "config", "status", "progress", "error", "result", "verdict", "best_auc"], r))
        d["config"] = json.loads(d["config"]) if d["config"] else None
        d["result"] = json.loads(d["result"]) if d["result"] else None
        return d

    def list(self, limit: int = 30) -> list[dict]:
        rows = self.engine.db.execute("SELECT id, created, finished, source, status, progress, error, verdict, best_auc, result FROM research_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for id_, created, finished, source, status, progress, error, verdict, best_auc, result in rows:
            res = json.loads(result) if result else {}
            out.append({"id": id_, "created": created, "finished": finished, "source": source, "status": status, "progress": progress, "error": error, "verdict": verdict,
                        "best_auc": best_auc, "rows": (res.get("data") or {}).get("rows"), "test": (res.get("data") or {}).get("test"), "summary": res.get("summary")})
        return out


# ----------------------------------------------------------------------------------------------- the separate process
def _main() -> None:
    """python -m app.research --db signals.db --run-id N --cfg '{...}': run one check on its own connection and store it in the run's row (started by ResearchRunner._spawn)."""
    import argparse
    import sqlite3
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--run-id", type=int, required=True)
    ap.add_argument("--cfg", default="{}")
    a = ap.parse_args()
    conn = sqlite3.connect(a.db, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    try:
        payouts = {s: p for s, p in conn.execute("SELECT symbol, payout FROM instruments WHERE payout IS NOT NULL") if p}
    except sqlite3.Error:
        payouts = {}
    try:
        execute_run(conn, a.run_id, json.loads(a.cfg), lambda: sentiment_mod.study(conn, 60, 30, payouts, None))
    finally:
        conn.close()


if __name__ == "__main__":
    _main()
