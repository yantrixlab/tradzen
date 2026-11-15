"""Risk advisor: learn from a strategy's OLD backtest trades which situations lose, tag live signals that walk into one, and build an improved version.

WHAT IT LEARNS  Every backtest trade of a strategy comes with the market context at entry (RSI, ADX, volatility, Bollinger position, candle shape, EMA trend, hour ...).
Two things are learned from them, both checked on data the learning never saw:
  1. a model of P(win | context): trained walk-forward (always scored on LATER trades), and the 25 % of trades it rates riskiest must really win less than the rest,
     in both halves of the unseen period - otherwise "no reliable risk signal" and the model is never used;
  2. plain-language rules from the clinic ("ADX >= 30", "hour 12:00-13:00", "against the EMA trend"): significant after correcting for the number of buckets
     tried (FDR) and confirmed on the newest 30 % of trades.
WHAT IT DOES  A live signal that matches a confirmed rule, or that the model rates in its riskiest group, gets a RISKY tag with the reasons. The same confirmed
rules are added, one at a time, to a copy of the strategy; a filter stays only if it also improves the newest 30 % of trades. The copy is a new PAPER strategy -
the original is never touched - so the improvement is measured live before anyone trusts it.
It is a filter on top of a strategy, not a source of edge: if the strategy has none, removing its worst situations rarely makes it profitable.
"""
from __future__ import annotations

import json
import math
import os
import pickle
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import ai, clinic, minigbm, research
from .backtest import FEATURE_COLS, BacktestError, compute_trades
from .spec import validate_spec
from .stats_util import summarize, wilson

MIN_LIVE = 300            # settled live signals a challenger needs before it is judged
MAX_LIVE = 1000           # ... and after which "no proof yet" means retire
MAX_NEW_PER_DAY = 3       # automatic challengers started per day (the more are tried, the likelier one looks good by luck)

CFG = {"folds": 4, "train_cap": 60_000, "rounds": 80, "depth": 3, "min_auc": 0.52, "alpha": 0.05, "min_test": 800, "breakeven": 0.54, "embargo_rows": 0,
       "seed": 7, "min_trades": 1500, "risky_frac": 0.25, "min_gap": 0.03, "risky_alpha": 0.01, "valid_fraction": 0.3, "max_filters": 5, "min_keep": 0.4}
NUM = [c for c in FEATURE_COLS if c != "session"] + ["trend_align"]
ALIGNED = ["macd_hist", "ema21_slope_pct", "ret1_pct", "ret5_pct", "ret15_pct", "candle_color", "streak", "cci20"]      # flipped for PUTs: "with the signal" is always positive


def _aligned_frame(df: pd.DataFrame) -> pd.DataFrame:
    d = pd.to_numeric(df["dir"], errors="coerce").fillna(0).to_numpy(float)
    out = pd.DataFrame(index=df.index)
    for c in NUM:
        out[c] = pd.to_numeric(df[c], errors="coerce") if c in df else np.nan
    for c in ALIGNED:
        out["w_" + c] = out[c] * d if c in out else np.nan
    out["w_rsi"] = (out["rsi14"] - 50) * d
    out["w_stoch"] = (out["stoch_k"] - 50) * d
    out["w_bbpos"] = (out["bb_pctb"] - 0.5) * d
    out["w_wick_for"] = np.where(d > 0, out["lower_wick_ratio"], out["upper_wick_ratio"])      # the wick that argues for the trade
    out["w_wick_against"] = np.where(d > 0, out["upper_wick_ratio"], out["lower_wick_ratio"])
    out["w_room"] = np.where(d > 0, out["dist_hi20_atr"], out["dist_lo20_atr"])
    out["w_di"] = (out["plus_di"] - out["minus_di"]) * d
    out["dir"] = d
    out["strength"] = pd.to_numeric(df["strength"], errors="coerce") if "strength" in df else np.nan
    out["agree"] = pd.to_numeric(df["agree"], errors="coerce") if "agree" in df else np.nan
    out["tier_a"] = (df["tier"] == "A").astype(float) if "tier" in df else np.nan
    out["tf"] = pd.to_numeric(df["tf"], errors="coerce") if "tf" in df else np.nan
    return out


# ----------------------------------------------------------------------------------------------- the checks
def _two_prop(w1: int, n1: int, w0: int, n0: int) -> float:
    """one-sided p that group 1 wins LESS than group 0"""
    if min(n1, n0) == 0:
        return 1.0
    p = (w1 + w0) / (n1 + n0)
    se = math.sqrt(max(p * (1 - p), 1e-9) * (1 / n1 + 1 / n0))
    z = (w1 / n1 - w0 / n0) / se
    return 0.5 * math.erfc(-z / math.sqrt(2))


def risky_group(score: np.ndarray, y: np.ndarray, cfg: dict) -> dict:
    """On the unseen scores: do the riskiest trades really win less than the rest (overall and in both halves of the unseen period)?"""
    cut = float(np.quantile(score, cfg["risky_frac"]))
    risky = score <= cut
    half = np.arange(len(y)) < len(y) // 2

    def part(m):
        r, o = m & risky, m & ~risky
        return {"risky_n": int(r.sum()), "risky_win": float(y[r].mean()) if r.any() else None, "rest_n": int(o.sum()), "rest_win": float(y[o].mean()) if o.any() else None}
    a, h1, h2 = part(np.ones(len(y), bool)), part(half), part(~half)
    p = _two_prop(int(y[risky].sum()), int(risky.sum()), int(y[~risky].sum()), int((~risky).sum()))
    gap = (a["rest_win"] - a["risky_win"]) if a["risky_win"] is not None and a["rest_win"] is not None else None
    gaps = [(h["rest_win"] - h["risky_win"]) for h in (h1, h2) if h["risky_win"] is not None and h["rest_win"] is not None]
    useful = bool(gap is not None and gap >= cfg["min_gap"] and p < cfg["risky_alpha"] and len(gaps) == 2 and all(g > 0 for g in gaps))
    if useful:
        why = f"the riskiest {cfg['risky_frac']:.0%} of trades won {a['risky_win']:.1%} vs {a['rest_win']:.1%} for the rest, on unseen trades, in both halves"
    elif gap is None:
        why = "not enough unseen trades"
    else:
        why = (f"the riskiest {cfg['risky_frac']:.0%} won {a['risky_win']:.1%} vs {a['rest_win']:.1%} for the rest (gap {gap * 100:+.1f} pts, p={p:.3f}): "
               "not a reliable difference, so the risk model is not used")
    return {"cut": cut, "frac": cfg["risky_frac"], "all": a, "first_half": h1, "second_half": h2, "gap": gap, "p": p, "useful": useful, "why": why}


def analyze_trades(trades: pd.DataFrame, cfg: dict | None = None, progress=None) -> dict:
    """Everything that can be learned from a strategy's trades: the risk model (walk-forward verdict + riskiest-group check) and the confirmed loss rules."""
    cfg = {**CFG, **(cfg or {})}
    dec = trades[trades["result"].isin(["win", "loss"])].sort_values("t", kind="stable").reset_index(drop=True)
    n = len(dec)
    base_win = float((dec["result"] == "win").mean()) if n else None
    out: dict = {"n_trades": int(len(trades)), "n_decided": n, "base_win": base_win, "breakeven": float(100 / (1 + dec["payout"].mean() / 100) / 100) if n else cfg["breakeven"],
                 "config": cfg, "model": None, "risky": None, "rules": [], "clinic": None}
    if n >= 200:                                                                      # rules need far fewer trades than a model does
        cl = clinic.analyze(dec, cfg["valid_fraction"])
        out["clinic"] = {"split_t": cl["split_t"], "tests": cl["tests"], "baseline": cl["baseline"]}
        for f in cl["findings"]:
            if f["significant"] and f["direction"] == "worse" and f["validation"]["holds"] and f["suggestion"]:
                out["rules"].append({"dimension": f["dimension"], "label": f["label"], "bucket": f["bucket"], "n": f["n"], "acc": f["acc"], "baseline": f["baseline"],
                                     "q": f["q"], "validation": f["validation"], "text": f["suggestion"]["text"], "filter": f["suggestion"]["filter"]})
    if n < cfg["min_trades"]:
        out["model"] = {"verdict": "need_data", "why": f"only {n:,} decided trades (need {cfg['min_trades']:,}) to train and test a risk model"}
        return out
    X = _aligned_frame(dec)
    med = X.median(numeric_only=True).fillna(0.0)
    M = X.fillna(med).to_numpy(float)
    y = (dec["result"] == "win").to_numpy(float)
    ds = {"X": M, "y": y, "t": dec["t"].to_numpy(), "cols": list(X.columns)}
    wf = ai.walk_forward(ds, cfg, progress=progress)
    v, why = ai.verdict(wf, cfg)
    grp = risky_group(wf["score"], y[wf["idx"]], cfg)
    out["model"] = {"verdict": v, "why": why, "auc": wf["auc"], "auc_lo": wf["stats"]["lo"], "auc_hi": wf["stats"]["hi"], "auc_control": wf["auc_control"], "n_test": wf["n_test"],
                    "folds": wf["folds"]}
    out["risky"] = grp
    rng = np.random.default_rng(cfg["seed"])
    sub = rng.choice(n, size=min(n, cfg["train_cap"]), replace=False)
    final = minigbm.gbm_fit(M[sub], y[sub], rng=np.random.default_rng(cfg["seed"] + 99), rounds=cfg["rounds"], depth=cfg["depth"])
    out["_pack"] = {"model": final, "cols": list(X.columns), "medians": med.to_dict(), "cut": grp["cut"], "useful": grp["useful"], "trained_at": time.time()}
    return out


# ----------------------------------------------------------------------------------------------- the improved version
def optimize(spec: dict, base: pd.DataFrame, rules: list[dict], compute, cfg: dict | None = None) -> dict:
    """Add the confirmed rules to the spec ONE AT A TIME (strongest first); a filter stays only if the newest trades improve with it. compute(spec) -> trades."""
    cfg = {**CFG, **(cfg or {})}
    split = int(base["t"].quantile(1 - cfg["valid_fraction"])) if len(base) else None
    cur_spec, cur, kept, tried = spec, base, [], []
    for r in rules[: cfg["max_filters"] + 3]:
        if len(kept) >= cfg["max_filters"]:
            break
        try:
            cand_spec = clinic.candidate_spec(cur_spec, r["filter"])
        except ValueError as e:
            tried.append({"text": r["text"], "kept": False, "why": str(e)})
            continue
        if cand_spec == cur_spec:
            continue
        cand = compute(cand_spec)
        cmp = clinic.compare(cur, cand, split)
        ok = cmp["verdict"] == "holds"
        tried.append({"text": r["text"], "kept": ok, "why": cmp["reason"]})
        if ok:
            cur_spec, cur = cand_spec, cand
            kept.append(r["text"])
    final = clinic.compare(base, cur, split) if kept else None
    return {"spec": cur_spec if kept else None, "filters": kept, "tried": tried, "comparison": final,
            "verdict": final["verdict"] if final else ("none" if not rules else "nothing_held")}


# ----------------------------------------------------------------------------------------------- the whole job (also what the subprocess runs)
def analyze_strategy(conn, sid: int, cfg: dict | None = None, progress=None) -> tuple[dict, dict | None]:
    cfg = {**CFG, **(cfg or {})}
    r = conn.execute("SELECT s.name, v.spec FROM strategies s JOIN strategy_versions v ON v.strategy_id = s.id WHERE s.id=? ORDER BY v.version DESC LIMIT 1", (sid,)).fetchone()
    if not r:
        raise BacktestError("strategy not found")
    name, spec = r[0], validate_spec(json.loads(r[1]))["spec"]
    payouts = {s: p for s, p in conn.execute("SELECT symbol, payout FROM instruments WHERE payout IS NOT NULL")}
    if progress:
        progress(0.05)
    trades = compute_trades(conn, spec, {}, payouts)
    if progress:
        progress(0.25)
    res = analyze_trades(trades, cfg, progress=(lambda p: progress(0.25 + 0.4 * p)) if progress else None)
    pack = res.pop("_pack", None)
    res.update(strategy_id=sid, strategy=name, trained_at=time.time(), optimized=None)
    if res["rules"]:
        res["optimized"] = optimize(spec, trades[trades["result"].isin(["win", "loss"])].reset_index(drop=True), res["rules"],
                                    lambda s: compute_trades(conn, s, {}, payouts), cfg)
    if progress:
        progress(0.95)
    return res, pack


# ----------------------------------------------------------------------------------------------- live scoring
def _match_rules(rules: list[dict], row: dict) -> list[str]:
    df = clinic._prepare(pd.DataFrame([{**row, "t": 0, "result": "win", "profit": 0.0}]))
    dims = {d["key"]: d for d in clinic.DIMENSIONS}
    hits = []
    for r in rules:
        d = dims.get(r["dimension"])
        if not d:
            continue
        lab, _ = clinic._bucket_labels(df, d)
        if str(lab.iloc[0]) == r["bucket"]:
            hits.append(f"{r['label']}: {r['bucket']} - won {r['acc']:.0%} over {r['n']} backtest trades (strategy average {r['baseline']:.0%})")
    return hits


class RiskBook:
    """Reports and models per strategy (data/ai/risk/<id>.json + .pkl); tags live signals; starts the analysis in a separate low-priority process."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.dir = Path(str(engine.db_path)).parent / "ai" / "risk" if str(engine.db_path) != ":memory:" else None
        self.reports: dict[int, dict] = {}
        self.packs: dict[int, dict] = {}
        self._proc: subprocess.Popen | None = None
        self._seen: dict[int, float] = {}
        self._queue: list[int] = []
        self._done: list[int] = []
        self._last_nightly: float | None = None
        self._last_eval = 0.0
        self._job: dict = {"running": False, "strategy_id": None, "progress": 0.0, "error": None}
        self._ensure_table()
        self.reload()

    def _ensure_table(self) -> None:
        self.engine.db.execute("CREATE TABLE IF NOT EXISTS signal_risk (signal_id INTEGER PRIMARY KEY, tag TEXT NOT NULL, p_win REAL, reasons TEXT)")
        self.engine.db.execute("CREATE TABLE IF NOT EXISTS upgrades (id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER NOT NULL, child_id INTEGER NOT NULL, created REAL NOT NULL, "
                               "status TEXT NOT NULL, filters TEXT, backtest TEXT, decided REAL, decision TEXT)")
        self.engine.db.commit()

    def reload(self) -> None:
        if not self.dir or not self.dir.exists():
            return
        for f in self.dir.glob("*.json"):
            try:
                sid = int(f.stem)
                mt = f.stat().st_mtime
                if self._seen.get(sid) == mt:
                    continue
                self._seen[sid] = mt
                self.reports[sid] = json.loads(f.read_text())
                pk = self.dir / f"{sid}.pkl"
                if pk.exists():
                    with open(pk, "rb") as fh:
                        self.packs[sid] = pickle.load(fh)
            except Exception:  # noqa: BLE001
                continue

    # ---- scoring
    def score(self, sig: dict, f: dict) -> dict:
        sid = sig.get("strategy_id")
        rep, pack = self.reports.get(sid), self.packs.get(sid)
        if not rep:
            return {"tag": "unchecked", "p_win": None, "reasons": []}
        row = {**f, "symbol": sig["symbol"], "tf": sig["tf"], "dir": 1 if sig["direction"] == "CALL" else -1, "tier": sig.get("tier"),
               "strength": sig.get("strength"), "agree": sig.get("agree")}
        reasons = _match_rules(rep.get("rules") or [], row)
        p = None
        if pack and pack.get("useful"):
            X = _aligned_frame(pd.DataFrame([row]))
            v = X.fillna(pd.Series(pack["medians"])).reindex(columns=pack["cols"]).fillna(0.0).to_numpy(float)
            s = float(pack["model"](v)[0])
            p = s
            if s <= pack["cut"]:
                reasons.append(f"risk model: this context is in the riskiest {rep['risky']['frac']:.0%} of past trades "
                               f"(those won {rep['risky']['all']['risky_win']:.0%} vs {rep['risky']['all']['rest_win']:.0%} for the rest)")
        return {"tag": "risky" if reasons else "ok", "p_win": p, "reasons": reasons}

    def tag(self, sig: dict, f: dict) -> None:
        try:
            r = self.score(sig, f)
        except Exception as e:  # noqa: BLE001      # a risk bug must never block a live signal
            self.engine._log("risk-error", str(e))
            return
        sig["risk"] = r
        if r["tag"] != "unchecked":
            self.engine.db.execute("INSERT OR REPLACE INTO signal_risk (signal_id, tag, p_win, reasons) VALUES (?,?,?,?)", (sig["id"], r["tag"], r["p_win"], json.dumps(r["reasons"])))
            self.engine.db.commit()

    def risk_for(self, ids: list[int]) -> dict[int, dict]:
        if not ids:
            return {}
        q = ",".join("?" * len(ids))
        return {i: {"tag": t, "p_win": p, "reasons": json.loads(rs or "[]")} for i, t, p, rs in
                self.engine.db.execute(f"SELECT signal_id, tag, p_win, reasons FROM signal_risk WHERE signal_id IN ({q})", ids)}

    # ---- analysis job
    def analyze(self, sid: int, cfg: dict | None = None, sync: bool = False) -> bool:
        if self._job["running"]:
            return False
        if not self.engine.repo.get(sid):
            raise ValueError("strategy not found")
        self._job = {"running": True, "strategy_id": sid, "progress": 0.0, "error": None, "started": time.time()}
        if sync or not self.dir:
            try:
                res, pack = analyze_strategy(self.engine.db, sid, cfg)
                self._store(sid, res, pack)
                self._job.update(running=False, progress=1.0)
                self._done.append(sid)
            except Exception as e:  # noqa: BLE001
                self._job.update(running=False, error=str(e))
            return True
        self.dir.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "app.risk", "--db", str(self.engine.db_path), "--out", str(self.dir), "--strategy-id", str(sid), "--cfg", json.dumps(cfg or {})]
        flags = 0x00004000 | (0x08000000 if os.name == "nt" else 0)          # BELOW_NORMAL priority, no console window
        log = open(self.dir / "risk.log", "ab")
        self._proc = subprocess.Popen(cmd, cwd=str(Path(__file__).resolve().parent.parent), stdout=log, stderr=log, creationflags=flags if os.name == "nt" else 0,
                                      preexec_fn=(lambda: os.nice(10)) if os.name != "nt" else None)
        return True

    def _store(self, sid: int, res: dict, pack: dict | None) -> None:
        self.reports[sid] = res
        if pack:
            self.packs[sid] = pack
        else:
            self.packs.pop(sid, None)

    def job(self) -> dict:
        if self._proc is not None and self.dir:
            try:
                prog = json.loads((self.dir / "progress.json").read_text())
            except Exception:  # noqa: BLE001
                prog = {}
            if self._proc.poll() is None:
                self._job["progress"] = float(prog.get("progress", 0.0))
            else:
                if prog.get("error") or not prog.get("done"):
                    self._job.update(running=False, error=prog.get("error") or f"the analysis process stopped (exit code {self._proc.returncode}); see data/ai/risk/risk.log")
                else:
                    self.reload()
                    self._job.update(running=False, progress=1.0, error=None)
                    self._done.append(self._job["strategy_id"])
                self._proc = None
        return dict(self._job)

    def status(self) -> dict:
        job = self.job()
        self.reload()
        out = []
        for s in self.engine.repo.list(system_only=True):
            rep = self.reports.get(s["id"])
            out.append({"id": s["id"], "name": s["name"], "status": s["status"], "analyzed": rep["trained_at"] if rep else None,
                        "n": rep["n_decided"] if rep else None, "base_win": rep["base_win"] if rep else None, "model": (rep["model"] or {}).get("verdict") if rep else None,
                        "risk_model_used": bool(self.packs.get(s["id"], {}).get("useful")), "rules": len(rep["rules"]) if rep else 0,
                        "optimized": (rep.get("optimized") or {}).get("verdict") if rep else None})
        return {"job": job, "strategies": out}

    def report(self, sid: int) -> dict | None:
        self.job()
        self.reload()
        rep = self.reports.get(sid)
        if not rep:
            return None
        o = rep.get("optimized")
        return {**rep, "optimized": ({k: v for k, v in o.items() if k != "spec"} | {"has_spec": bool(o.get("spec"))}) if o else None}

    # ---- automatic re-checks and the champion / challenger upgrade loop
    @staticmethod
    def _on(name: str, default: str = "on") -> bool:
        return os.getenv(name, default).strip().lower() not in ("off", "0", "false", "no")

    def nightly(self, now: float | None = None) -> None:
        """Every night (same hour as Research): queue a re-analysis of every live and paper strategy - newest data included. They run one after the other in the
        separate low-priority process, so it never competes with the live feed."""
        if not self._on("QXS_RISK_NIGHTLY"):
            return
        now = now or time.time()
        hour = research.schedule_hour()
        stamp = self.dir / "nightly.json" if self.dir else None
        try:
            last = json.loads(stamp.read_text())["t"] if stamp and stamp.exists() else self._last_nightly
        except Exception:  # noqa: BLE001
            last = self._last_nightly
        _, due = research.next_and_due(now, hour, last)
        if not due or self._queue:
            return
        todo = [s for s in self.engine.repo.list(system_only=True) if s["status"] in ("live", "paper") and s["key"] != "ai-momentum"]       # the AI source has no preset rules to backtest
        todo.sort(key=lambda s: (s["status"] != "live", s["id"]))
        self._queue = [s["id"] for s in todo]              # the "done tonight" stamp is written when the LAST one starts (see tick): a restart mid-queue then simply requeues

    def tick(self) -> None:
        """Called every minute: start the next queued analysis, handle finished ones (maybe create a challenger), judge running challengers."""
        job = self.job()
        while self._done:
            self._after(self._done.pop(0))
        if self._queue and not job["running"]:
            sid = self._queue.pop(0)
            try:
                self.analyze(sid)
            except ValueError:
                pass
            if not self._queue:
                self._last_nightly = time.time()
                if self.dir:
                    self.dir.mkdir(parents=True, exist_ok=True)
                    (self.dir / "nightly.json").write_text(json.dumps({"t": self._last_nightly}))
        if time.time() - self._last_eval > 600:
            self._last_eval = time.time()
            self.evaluate_upgrades()

    def _after(self, sid: int) -> None:
        self.reload()
        if self._on("QXS_AUTO_UPGRADE"):
            self.challenge(sid)

    def challenge(self, sid: int) -> int | None:
        """If the analysis found filters that improve the strategy's newest backtest trades, start a PAPER challenger (never live) - at most one at a time per strategy,
        never the same filters twice, never a challenger of a challenger of a challenger."""
        rep = self.reports.get(sid)
        o = (rep or {}).get("optimized")
        d = self.engine.repo.get(sid)
        if not o or not o.get("spec") or o.get("verdict") != "holds" or not d or d["status"] not in ("live", "paper"):
            return None
        if d["key"].count("-r") >= 2:
            return None
        text = "; ".join(o["filters"])
        if self.engine.db.execute("SELECT COUNT(*) FROM upgrades WHERE created > ?", (time.time() - 86400,)).fetchone()[0] >= MAX_NEW_PER_DAY:
            return None                                               # many tries by chance would otherwise flood the strategy list
        busy = self.engine.db.execute("SELECT 1 FROM upgrades WHERE parent_id=? AND (status='testing' OR filters=?)", (sid, text)).fetchone()
        if busy:
            return None
        child = self.apply(sid)
        self.engine.db.execute("INSERT INTO upgrades (parent_id, child_id, created, status, filters, backtest) VALUES (?,?,?,?,?,?)",
                               (sid, child, time.time(), "testing", text, json.dumps((o.get("comparison") or {}).get("valid"))))
        self.engine.db.commit()
        self.engine._log("upgrade", f"challenger #{child} created for '{d['name']}': {text}")
        return child

    def _live_record(self, sid: int, since: float = 0.0) -> tuple[int, int, float]:
        """(settled, wins, average break-even) of a strategy's real settled signals since `since`."""
        rows = self.engine.db.execute("SELECT symbol, status FROM signals WHERE strategy_id=? AND created>=? AND status IN ('win','loss')", (sid, since)).fetchall()
        if not rows:
            return 0, 0, 0.54
        be = np.mean([1 / (1 + float((self.engine.instruments.get(s) or {}).get("payout") or 85.0) / 100) for s, _ in rows])
        return len(rows), sum(1 for _, st in rows if st == "win"), float(be)

    def evaluate_upgrades(self) -> None:
        """Judge each challenger on what it really did live. PROMOTED only with proof it is profitable (the 95% lower bound of its win rate above break-even);
        RETIRED when it is proven unprofitable, not better than its parent over the same period, or still unproven after MAX signals."""
        for uid, parent, child, created in self.engine.db.execute("SELECT id, parent_id, child_id, created FROM upgrades WHERE status='testing'").fetchall():
            n, w, be = self._live_record(child)
            if n < MIN_LIVE:
                continue
            lo, hi = wilson(w, n)
            pn, pw, _ = self._live_record(parent, created)
            parent_acc = pw / pn if pn else None
            if lo > be:
                status, why = "promoted", f"won {w}/{n} ({w / n:.1%}), 95% lower bound {lo:.1%} is above break-even {be:.1%}: profit proven live"
            elif hi < be:
                status, why = "retired", f"won {w}/{n} ({w / n:.1%}); even the best case ({hi:.1%}) is below break-even {be:.1%}"
            elif parent_acc is not None and pn >= MIN_LIVE and w / n <= parent_acc:
                status, why = "retired", f"not better than its parent over the same period ({w / n:.1%} vs {parent_acc:.1%})"
            elif n >= MAX_LIVE:
                status, why = "retired", f"still no proof of profit after {n} signals ({w / n:.1%}, range {lo:.1%}-{hi:.1%}, break-even {be:.1%})"
            else:
                continue
            if status == "promoted":
                self.engine.set_strategy_status(child, "live")
                pd_ = self.engine.repo.get(parent)
                if pd_ and pd_["status"] == "live":
                    self.engine.set_strategy_status(parent, "paused")
            else:
                self.engine.set_strategy_status(child, "archived")
            self.engine.db.execute("UPDATE upgrades SET status=?, decided=?, decision=? WHERE id=?", (status, time.time(), why, uid))
            self.engine.db.commit()
            self.engine._log("upgrade", f"challenger #{child} {status}: {why}")

    def health(self) -> dict:
        """One verdict per strategy from its REAL settled signals and its backtest: keep / keep watching / upgrade available / improvement candidate / pause. 'Losing' is only
        said when even the best case of the win rate (95% upper bound) is below break-even, so luck cannot get a strategy condemned."""
        eng = self.engine
        rows = eng.db.execute("SELECT strategy_id, symbol, SUM(status='win'), SUM(status='loss') FROM signals WHERE direction != 'SKIP' AND status IN ('win','loss') "
                              "GROUP BY strategy_id, symbol").fetchall()
        live: dict[int, list] = {}
        for sid, sym, w, l_ in rows:
            be = 1 / (1 + float((eng.instruments.get(sym) or {}).get("payout") or 85.0) / 100)
            a = live.setdefault(sid, [0, 0, 0.0])
            a[0] += int(w); a[1] += int(l_); a[2] += be * (int(w) + int(l_))
        recent = {sid: (w, n) for sid, w, n in eng.db.execute(
            "SELECT strategy_id, SUM(status='win'), COUNT(*) FROM (SELECT strategy_id, status, ROW_NUMBER() OVER (PARTITION BY strategy_id ORDER BY target_t DESC) rn "
            "FROM signals WHERE direction != 'SKIP' AND status IN ('win','loss')) WHERE rn <= 50 GROUP BY strategy_id")}
        testing = {p for (p,) in eng.db.execute("SELECT parent_id FROM upgrades WHERE status='testing'")}
        out = []
        for s in eng.repo.list(system_only=True):
            if s["status"] == "archived":
                continue
            sid = s["id"]
            w, l_, bsum = live.get(sid, [0, 0, 0.0])
            decided = w + l_
            be = bsum / decided if decided else 0.54
            lo, hi = wilson(w, decided) if decided else (None, None)
            rep = self.reports.get(sid)
            bt = None
            if rep and rep.get("n_decided"):
                bw = rep["base_win"]
                blo, bhi = wilson(int(round(bw * rep["n_decided"])), rep["n_decided"])
                bt = {"n": rep["n_decided"], "win": bw, "lo": blo, "hi": bhi, "breakeven": rep["breakeven"], "losing": bhi < rep["breakeven"], "profitable": blo > rep["breakeven"]}
            opt = (rep or {}).get("optimized") or {}
            live_losing = bool(decided >= 50 and hi is not None and hi < be)
            live_profit = bool(decided >= 100 and lo is not None and lo > be)
            # tens of thousands of backtest trades are far stronger evidence than a few hundred live ones: a strategy is "losing" when either is proven below break-even,
            # unless its real signals clearly say otherwise
            losing = live_losing or bool(bt and bt["losing"] and not live_profit)
            if live_profit:
                tag, why = "keep", "its real signals beat break-even with 95% confidence"
            elif s["id"] in testing:
                tag, why = "testing", "an improved copy is being tested live right now"
            elif losing and opt.get("verdict") == "holds" and opt.get("spec"):
                tag, why = "upgrade", "losing, but a filter held up on its newest backtest trades: an improved copy can be created"
            elif losing and rep and rep.get("rules"):
                tag, why = "improve", "losing; some loss situations were found but no filter improved it on newer trades yet"
            elif losing and s["status"] == "paused":
                tag, why = "paused", "already paused - proven below break-even"
            elif losing:
                tag, why = "pause", ("proven below break-even and the advisor found nothing to fix - there is no edge to upgrade" if rep else "proven below break-even; run the analysis to see if anything can be fixed")
            elif not rep and s["status"] in ("live", "paper"):
                tag, why = "analyse", "not studied yet"
            elif decided < 50 and not bt:
                tag, why = "collecting", "too few real signals so far"
            else:
                tag, why = "watch", "not proven either way yet - keep collecting real signals"
            out.append({"id": sid, "name": s["name"], "status": s["status"], "tag": tag, "why": why,
                        "live": {"n": decided, "wins": w, "acc": w / decided if decided else None, "lo": lo, "hi": hi, "breakeven": be,
                                 "recent_acc": (recent[sid][0] / recent[sid][1]) if sid in recent and recent[sid][1] else None, "recent_n": recent.get(sid, (0, 0))[1]},
                        "backtest": bt, "rules": len(rep["rules"]) if rep else None, "analysed": rep["trained_at"] if rep else None})
        order = {"upgrade": 0, "pause": 1, "improve": 2, "analyse": 3, "testing": 4, "watch": 5, "collecting": 6, "keep": 7, "paused": 8}
        out.sort(key=lambda x: (order[x["tag"]], x["status"] != "live", -(x["live"]["n"])))
        return {"strategies": out, "counts": {t: sum(1 for x in out if x["tag"] == t) for t in order}}

    def upgrades(self) -> list[dict]:
        out = []
        for uid, parent, child, created, status, filters, bt, decided, decision in self.engine.db.execute(
                "SELECT id, parent_id, child_id, created, status, filters, backtest, decided, decision FROM upgrades ORDER BY id DESC LIMIT 50"):
            n, w, be = self._live_record(child)
            pn, pw, _ = self._live_record(parent, created)
            out.append({"id": uid, "parent_id": parent, "parent": self.engine.names.get(parent), "child_id": child, "child": self.engine.names.get(child), "created": created,
                        "status": status, "filters": filters, "backtest": json.loads(bt) if bt else None, "decided": decided, "decision": decision,
                        "live": {"n": n, "wins": w, "breakeven": be, "need": MIN_LIVE}, "parent_live": {"n": pn, "wins": pw}})
        return out

    def apply(self, sid: int) -> int:
        """Create the improved version as a NEW paper strategy (the original is untouched)."""
        rep = self.reports.get(sid)
        o = (rep or {}).get("optimized")
        if not o or not o.get("spec"):
            raise ValueError("no improved version: no filter held up on the newest trades")
        d = self.engine.repo.get(sid)
        base_key, n = f"{d['key']}-r", 1
        key = base_key
        while self.engine.db.execute("SELECT 1 FROM strategies WHERE key=?", (key,)).fetchone():
            n += 1
            key = f"{base_key}{n}"
        text = "; ".join(o["filters"])
        new = self.engine.create_strategy(key, f"{d['name']} + risk filters", f"{d['name']} with filters learned from its backtest losses: {text}", o["spec"], "paper")
        self.engine.db.execute("UPDATE strategy_versions SET notes=? WHERE strategy_id=? AND version=1", (f"Risk advisor: {text}. Verdict on the newest trades: {o['verdict']}.", new))
        self.engine.db.commit()
        return new


# ----------------------------------------------------------------------------------------------- subprocess entry
def _main() -> None:
    import argparse
    import sqlite3
    import traceback
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--strategy-id", type=int, required=True)
    ap.add_argument("--cfg", default="{}")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    def write(**kw):
        (out / "progress.json").write_text(json.dumps({"progress": 0.0, "error": None, "done": False, **kw}))
    try:
        conn = sqlite3.connect(f"file:{Path(a.db).as_posix()}?mode=ro", uri=True, timeout=60)
        res, pack = analyze_strategy(conn, a.strategy_id, json.loads(a.cfg), progress=lambda p: write(progress=round(float(p), 3)))
        pk = out / f"{a.strategy_id}.pkl"
        if pack:
            with open(str(pk) + ".tmp", "wb") as fh:
                pickle.dump(pack, fh)
            os.replace(str(pk) + ".tmp", pk)
        elif pk.exists():
            pk.unlink()
        (out / f"{a.strategy_id}.json.tmp").write_text(json.dumps(res, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
        os.replace(out / f"{a.strategy_id}.json.tmp", out / f"{a.strategy_id}.json")
        write(progress=1.0, done=True)
    except Exception as e:  # noqa: BLE001
        (out / "risk.log").open("a").write(traceback.format_exc())
        write(error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    _main()
