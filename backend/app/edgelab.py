"""Edge Lab - the AI's search for an edge in the stored candles, and what happens next. Runs in a separate low-priority process (python -m app.edgelab) so live signals are never slowed.

STAGE 1  IS THERE ANY EDGE AT ALL?  A walk-forward model (always scored on later candles, with a shuffled-label control) asks whether anything in the candles predicts the next
         candle. "none" / "weak" / "proven" - the same honest verdict the AI advisor gives.
STAGE 2  BUILD STRATEGIES FROM THE OLD DATA  ~40 plain-language conditions (trend, momentum, candle shape, bands, volatility, hour ...) are combined into thousands of candidate
         rules (1-3 conditions, follow or fade). The history is cut in time: DISCOVERY (oldest 50 %) ranks every rule; CONFIRMATION (next 20 %) must back the top 30 with a
         Bonferroni-corrected test; the newest 30 % is touched only by the survivors, through the full battery of tools/idea_test.py (real backtest of the real strategy spec, the
         ledger widening the 95 % range for every idea tried). If nothing survives, the best candidate is still built and tested - so the answer is always "this is what the old
         data gives, and here is how it did on candles it never saw".
STAGE 3  IF AN EDGE IS FOUND  the winning rule becomes a PAPER strategy, and every live / paper strategy is redesigned: a copy that must ALSO satisfy the edge rule is tested with the
         same battery and kept only if it improves on the newest data. The copies start as paper challengers, judged on real signals like every other upgrade.
If no edge is found it says so, lists the best candidates with their honest numbers, and changes nothing. It never promises a profit.
"""
from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import ai, battery, evaluator, marketdata, vec
from . import expr as expr_mod

CFG = {"tf": 60, "min_payout": 85.0, "top_k": 30, "min_n": 200, "final_max": 5, "redesign_max": 6, "global": True, "ledger": True,
       "discovery_end": 0.5, "confirm_end": 0.7, "pair_extend": 40, "max_atoms": 3}

# id, plain-language label, expression for an UP situation, expression for the mirrored DOWN situation (None = not directional: the same condition for both sides)
ATOMS: list[tuple[str, str, str, str | None]] = [
    ("ema_stack", "EMAs stacked with the trade (9 > 21 > 50)", "e9 > e21 and e21 > e50", "e9 < e21 and e21 < e50"),
    ("fast_ema", "EMA9 on the trade's side of EMA21", "e9 > e21", "e9 < e21"),
    ("slow_ema", "price on the trade's side of EMA50", "close > e50", "close < e50"),
    ("structure", "market structure bias with the trade (HH/HL or LH/LL)", "ms.bias == 1", "ms.bias == -1"),
    ("macd_side", "MACD histogram on the trade's side", "mc.hist > 0", "mc.hist < 0"),
    ("macd_rising", "MACD histogram moving with the trade", "mc.hist > mc.hist[1]", "mc.hist < mc.hist[1]"),
    ("rsi_55", "RSI beyond 55 / 45 with the trade", "r14 > 55", "r14 < 45"),
    ("rsi_65", "RSI stretched beyond 65 / 35", "r14 > 65", "r14 < 35"),
    ("stoch_80", "Stochastic beyond 80 / 20", "st.k > 80", "st.k < 20"),
    ("stoch_cross", "Stochastic %K above / below %D", "st.k > st.d", "st.k < st.d"),
    ("di_side", "+DI above / below -DI", "ad.pdi > ad.mdi", "ad.pdi < ad.mdi"),
    ("bb_80", "Bollinger %B beyond 0.8 / 0.2", "bb.pctb > 0.8", "bb.pctb < 0.2"),
    ("bb_out", "close outside the Bollinger band", "bb.pctb > 1", "bb.pctb < 0"),
    ("stretch", "price 1 ATR beyond EMA21", "close > e21 + a14", "close < e21 - a14"),
    ("near_ema", "price within half an ATR of EMA21 on the trade's side", "close > e21 and close < e21 + 0.5 * a14", "close < e21 and close > e21 - 0.5 * a14"),
    ("color", "last candle with the trade", "green", "red"),
    ("color2", "last two candles with the trade", "green and green[1]", "red and red[1]"),
    ("big_body", "big body (60%+ of the range) with the trade", "body > 0.6 * range", "body < -0.6 * range"),
    ("close_ctrl", "closed in the top / bottom 30% of its range", "close >= low + 0.7 * range", "close <= low + 0.3 * range"),
    ("streak2", "2+ same-colour candles in a row", "streak >= 2", "streak <= -2"),
    ("streak3", "3+ same-colour candles in a row", "streak >= 3", "streak <= -3"),
    ("engulf", "engulfing candle", "bull_engulf", "bear_engulf"),
    ("hammer_star", "hammer / shooting star", "hammer", "star"),
    ("breakout10", "close beyond the previous 10-candle extreme", "close > highest(high, 10)[1]", "close < lowest(low, 10)[1]"),
    ("mom3", "price above / below 3 candles ago", "close > close[3]", "close < close[3]"),
    ("mom10", "price above / below 10 candles ago", "close > close[10]", "close < close[10]"),
    ("adx_22", "trending market (ADX > 22)", "ad.adx > 22", None),
    ("adx_30", "strong trend (ADX > 30)", "ad.adx > 30", None),
    ("adx_low", "sideways market (ADX < 18)", "ad.adx < 18", None),
    ("vol_hi", "volatility rising (ATR 30% above its 20-candle-ago level)", "a14 > 1.3 * a14[20]", None),
    ("vol_lo", "volatility falling (ATR 20% below its 20-candle-ago level)", "a14 < 0.8 * a14[20]", None),
    ("h_asia", "Asia session (00-07 UTC)", "hour >= 0 and hour < 7", None),
    ("h_europe", "Europe session (07-13 UTC)", "hour >= 7 and hour < 13", None),
    ("h_us", "US session (13-21 UTC)", "hour >= 13 and hour < 21", None),
    ("h_late", "late session (21-24 UTC)", "hour >= 21", None),
]


# ----------------------------------------------------------------------------------------------- the condition matrix
def build_matrix(conn, tf: int, payouts: dict, min_payout: float, progress=None) -> dict:
    """For every candle of every pair paying >= min_payout: which conditions hold (UP version and mirrored DOWN version), and whether the NEXT candle went up."""
    spec = {"indicators": battery.IND}
    pairs = [s for (s,) in conn.execute("SELECT DISTINCT symbol FROM candles WHERE tf=?", (tf,)).fetchall() if marketdata.payout_ok(s, payouts, min_payout)]
    A = len(ATOMS)
    Us, Ds, ys, ts, ss, ps = [], [], [], [], [], []
    for k, sym in enumerate(sorted(pairs)):
        q = pd.DataFrame(conn.execute("SELECT ts, o, h, l, c, ticks FROM candles WHERE symbol=? AND tf=? ORDER BY ts", (sym, tf)).fetchall(),
                         columns=["t", "open", "high", "low", "close", "n"]).astype(float)
        for a, b in vec.contiguous_runs(q["t"].to_numpy().astype(np.int64), tf):
            if b - a < 120:
                continue
            d = q.iloc[a:b].reset_index(drop=True)
            env = evaluator._ind_env(spec, d, {})
            n = len(d)
            U, D = np.zeros((n, A), bool), np.zeros((n, A), bool)
            for j, (_, _, up, dn) in enumerate(ATOMS):
                U[:, j] = expr_mod.evaluate(up, env, d.index).fillna(False).to_numpy(bool)
                D[:, j] = U[:, j] if dn is None else expr_mod.evaluate(dn, env, d.index).fillna(False).to_numpy(bool)
            move = (d["close"].shift(-1) - d["open"].shift(-1)).to_numpy()
            ok = np.isfinite(move) & (move != 0)
            ok[:60], ok[-1] = False, False
            if ok.any():
                Us.append(U[ok]); Ds.append(D[ok]); ys.append(move[ok] > 0); ts.append(d["t"].to_numpy()[ok].astype(np.int64))
                ss.append(np.full(int(ok.sum()), sym, dtype=object)); ps.append(np.full(int(ok.sum()), float(payouts.get(sym) or 85.0)))
        if progress:
            progress((k + 1) / max(1, len(pairs)))
    if not ys:
        return {"U": np.zeros((0, A), bool), "D": np.zeros((0, A), bool), "y": np.zeros(0, bool), "t": np.zeros(0, np.int64), "sym": np.zeros(0, dtype=object), "pay": np.zeros(0)}
    t = np.concatenate(ts)
    o = np.argsort(t, kind="stable")
    cat = lambda xs: np.concatenate(xs)[o]  # noqa: E731
    return {"U": np.vstack(Us)[o], "D": np.vstack(Ds)[o], "y": cat(ys), "t": t[o], "sym": cat(ss), "pay": cat(ps)}


# ----------------------------------------------------------------------------------------------- stage 2: mine, confirm
def _z(win: float, n: int, be: float) -> float:
    return (win - be) / math.sqrt(be * (1 - be) / n) if n else 0.0


def _p_above(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2))


class _Slice:
    """One time slice with the conditions transposed for fast AND-ing."""

    def __init__(self, M: dict, mask: np.ndarray) -> None:
        self.UT = np.ascontiguousarray(M["U"][mask].T)
        self.DT = np.ascontiguousarray(M["D"][mask].T)
        self.up = M["y"][mask]
        self.n_rows = int(mask.sum())
        self.be = float((1 / (1 + M["pay"][mask] / 100)).mean()) if self.n_rows else 0.54

    def score(self, idx: tuple[int, ...]) -> tuple[int, int]:
        """(trades, wins if every trade FOLLOWS the conditions); the fade side wins n - wins."""
        su, sd = self.UT[idx[0]].copy(), self.DT[idx[0]].copy()
        for i in idx[1:]:
            su &= self.UT[i]
            sd &= self.DT[i]
        n = int(su.sum() + sd.sum())
        w = int((su & self.up).sum() + (sd & ~self.up).sum())
        return n, w


def rule_exprs(idx: tuple[int, ...], side: str) -> tuple[str, str]:
    """(call expression, put expression) of a rule. follow: UP conditions -> CALL, DOWN conditions -> PUT; fade: the opposite."""
    up = " and ".join(f"({ATOMS[i][2]})" for i in idx)
    dn = " and ".join(f"({ATOMS[i][3] or ATOMS[i][2]})" for i in idx)
    return (up, dn) if side == "follow" else (dn, up)


def rule_text(idx: tuple[int, ...], side: str) -> str:
    conds = " AND ".join(ATOMS[i][1] for i in idx)
    return conds + ("  ->  follow (CALL when it holds upward, PUT when it holds downward)" if side == "follow" else "  ->  fade (PUT when it holds upward, CALL when it holds downward)")


def search(M: dict, cfg: dict, progress=None) -> dict:
    """Discovery on the oldest 50 %, confirmation on the next 20 %. The newest 30 % is not touched here."""
    t = M["t"]
    if len(t) < 3000:
        return {"ok": False, "why": f"only {len(t):,} usable candles (need 3,000)", "rows": int(len(t))}
    q1, q2 = np.quantile(t, [cfg["discovery_end"], cfg["confirm_end"]])
    disc, conf = _Slice(M, t < q1), _Slice(M, (t >= q1) & (t < q2))
    directional = np.array([a[3] is not None for a in ATOMS])
    A = len(ATOMS)
    scored: dict[tuple, tuple[int, int]] = {}

    def add(idx):
        if idx in scored or not directional[list(idx)].any():
            return
        n, w = disc.score(idx)
        if n >= cfg["min_n"]:
            scored[idx] = (n, w)
    for i in range(A):
        add((i,))
    for i in range(A):
        for j in range(i + 1, A):
            add((i, j))
        if progress:
            progress(0.5 * (i + 1) / A)

    def best_z(item):
        (idx, (n, w)) = item
        return max(abs(_z(w / n, n, disc.be)), abs(_z(1 - w / n, n, disc.be)))
    pairs = sorted([it for it in scored.items() if len(it[0]) == 2], key=best_z, reverse=True)[: cfg["pair_extend"]]
    if cfg["max_atoms"] >= 3:
        for (i, j), _ in pairs:
            for k in range(A):
                if k not in (i, j):
                    add(tuple(sorted((i, j, k))))
    if progress:
        progress(0.9)
    cands = []
    for idx, (n, w) in scored.items():
        for side, wins in (("follow", w), ("fade", n - w)):
            cands.append({"idx": idx, "side": side, "n": n, "win": wins / n, "z": _z(wins / n, n, disc.be)})
    cands.sort(key=lambda c: c["z"], reverse=True)
    top = cands[: cfg["top_k"]]
    alpha = 0.05 / max(1, len(top))
    for c in top:
        n, w = conf.score(c["idx"])
        wins = w if c["side"] == "follow" else n - w
        z = _z(wins / n, n, conf.be) if n else 0.0
        c["confirm"] = {"n": n, "win": wins / n if n else None, "z": z, "p": _p_above(z), "be": conf.be, "ok": bool(n >= 100 and wins / n > conf.be and _p_above(z) < alpha)}
        c["discovery"] = {"n": c.pop("n"), "win": c.pop("win"), "z": c.pop("z"), "be": disc.be}
    return {"ok": True, "rows": int(len(t)), "candidates_tested": len(cands) // 2, "rules_scored": len(cands), "top": top, "alpha": alpha, "span": [int(t[0]), int(t[-1])],
            "slices": {"discovery": disc.n_rows, "confirm": conf.n_rows, "final": int((t >= q2).sum())}, "pairs": int(len(set(M["sym"].tolist())))}


# ----------------------------------------------------------------------------------------------- stage 1: global scan
def global_scan(conn, payouts: dict, cfg: dict) -> dict:
    c = {**ai.DEFAULTS, "folds": 3, "rounds": 60, "train_cap": 40_000, "min_test": 3000, "min_rows": 3000, "embargo_rows": 300, "min_payout": cfg["min_payout"]}
    ds = ai.build_dataset(conn, cfg["tf"], payouts, c)
    if len(ds["y"]) < c["min_rows"]:
        return {"verdict": "need_data", "why": f"only {len(ds['y']):,} usable candles"}
    wf = ai.walk_forward(ds, c)
    v, why = ai.verdict(wf, c)
    return {"verdict": v, "why": why, "auc": float(wf["auc"]), "lo": float(wf["stats"]["lo"]), "hi": float(wf["stats"]["hi"]), "control": float(wf["auc_control"]),
            "n_test": int(wf["n_test"]), "rows": int(len(ds["y"]))}


# ----------------------------------------------------------------------------------------------- stage 3: redesign
def with_edge(spec: dict, call: str, put: str) -> dict | None:
    """A copy of a strategy that must ALSO satisfy the edge rule (one more agreeing factor, nobody against). None when its indicator ids clash with the rule's."""
    if spec.get("kind") != "factors":
        return None
    s = copy.deepcopy(spec)
    have = {i["id"]: i for i in s.get("indicators", [])}
    for it in battery.IND:
        if it["id"] in have and have[it["id"]] != it:
            return None
        if it["id"] not in have:
            s["indicators"].append(copy.deepcopy(it))
    s["factors"].append({"id": "edge_rule", "name": "Edge Lab rule", "weight": 25, "kind": "expr", "call": call, "put": put, "vote": 1})
    s["gates"] = {**s["gates"], "min_agree": int(s["gates"].get("min_agree", 1)) + 1, "max_opposing_weight": 0}
    return s


def _brief(res: dict) -> dict:
    keep = ("verdict", "why", "checks", "overall", "older", "newest", "blocks", "pair_halves", "by_direction", "noise_band", "ideas_counted", "z", "seconds")
    return {k: res[k] for k in keep if k in res}


# ----------------------------------------------------------------------------------------------- the whole run
def run(conn, cfg: dict | None = None, progress=None) -> dict:
    cfg = {**CFG, **(cfg or {})}
    ledger = battery.LEDGER if cfg["ledger"] else None
    payouts = {s: p for s, p in conn.execute("SELECT symbol, payout FROM instruments WHERE payout IS NOT NULL")}
    prog = lambda stage, p: progress(stage, p) if progress else None  # noqa: E731
    out: dict = {"started": time.time(), "cfg": {k: v for k, v in cfg.items() if k != "ledger"}, "tf": cfg["tf"]}
    tf = cfg["tf"]
    out["candles"] = int(conn.execute("SELECT COUNT(*) FROM candles WHERE tf=?", (tf,)).fetchone()[0])

    prog("Stage 1 - is there any edge at all? (walk-forward model)", 0.02)
    out["global"] = global_scan(conn, payouts, cfg) if cfg["global"] else None

    prog("Stage 2 - measuring ~40 conditions on every candle", 0.2)
    M = build_matrix(conn, tf, payouts, cfg["min_payout"], lambda p: prog("Stage 2 - measuring ~40 conditions on every candle", 0.2 + 0.25 * p))
    prog("Stage 2 - mining rules from the old data", 0.5)
    S = search(M, cfg, lambda p: prog("Stage 2 - mining rules from the old data", 0.5 + 0.15 * p))
    out["search"] = {k: v for k, v in S.items() if k != "top"}
    if not S["ok"]:
        out.update(outcome={"status": "no_data", "why": S["why"]}, candidates=[], redesign=[], finished=time.time())
        return out

    survivors = [c for c in S["top"] if c["confirm"]["ok"]]
    out["funnel"] = {"candidates": S["candidates_tested"], "rules_scored": S["rules_scored"], "kept_after_discovery": len(S["top"]), "confirmed": len(survivors)}
    to_test = sorted(survivors, key=lambda c: c["confirm"]["z"], reverse=True)[: cfg["final_max"]]
    best_effort = False
    if not to_test:                                   # nothing survived: still build and test the best rule the old data gives - so the answer is concrete
        to_test, best_effort = [max(S["top"], key=lambda c: c["confirm"]["z"])], True
    cands = []
    for n, c in enumerate(to_test):
        prog("Stage 2 - testing the strongest rules on unseen candles (full battery)", 0.66 + 0.18 * n / max(1, len(to_test)))
        call, put = rule_exprs(c["idx"], c["side"])
        spec = battery.spec_from_expr(call, put, [tf], cfg["min_payout"])
        name = f"EdgeLab: {' + '.join(ATOMS[i][0] for i in c['idx'])} ({c['side']})"
        res = battery.evaluate(conn, spec, {"tfs": [tf]}, ledger, name, "edgelab")
        cands.append({"rank": n + 1, "atoms": [ATOMS[i][0] for i in c["idx"]], "side": c["side"], "rule": rule_text(c["idx"], c["side"]), "call": call, "put": put,
                      "discovery": c["discovery"], "confirm": c["confirm"], "battery": _brief(res), "spec": res["spec"], "best_effort": best_effort})
    # also report the top of the discovery list that did NOT survive, so the funnel is visible (numbers only, no extra test)
    tested = {c["idx"] for c in to_test}
    others = [{"atoms": [ATOMS[i][0] for i in c["idx"]], "side": c["side"], "rule": rule_text(c["idx"], c["side"]), "discovery": c["discovery"], "confirm": c["confirm"]}
              for c in S["top"] if c["idx"] not in tested]
    out["candidates"], out["rejected"] = cands, others[:12]

    passed = [c for c in cands if c["battery"]["verdict"] == "PASS"]
    promising = [c for c in cands if c["battery"]["verdict"] == "PROMISING"]
    if passed:
        best = max(passed, key=lambda c: c["battery"]["newest"]["lo"])
        out["outcome"] = {"status": "edge_found", "best": best["rank"], "why": f"{len(passed)} rule(s) passed every check on candles the search never saw"}
    elif promising:
        best = max(promising, key=lambda c: c["battery"]["newest"]["win"])
        out["outcome"] = {"status": "possible_edge", "best": best["rank"], "why": "above break-even on the newest data but not proven - needs live paper results"}
    else:
        best = cands[0]
        nw = best["battery"].get("newest") or {}
        out["outcome"] = {"status": "no_edge", "best": best["rank"], "why": "no rule built from the old data beat break-even on unseen candles" +
                          (f" (the best one won {nw['win'] * 100:.1f}% vs break-even {nw['be'] * 100:.1f}%)" if nw.get("win") is not None else "")}
    out["strategy_spec"] = best["spec"]

    out["redesign"] = []
    if out["outcome"]["status"] in ("edge_found", "possible_edge"):
        prog("Stage 3 - redesigning strategies with the edge", 0.86)
        rows = conn.execute("SELECT s.id, s.name, s.status, s.key, v.spec FROM strategies s JOIN strategy_versions v ON v.strategy_id = s.id "
                            "WHERE s.status IN ('live','paper') AND v.version = (SELECT MAX(version) FROM strategy_versions WHERE strategy_id = s.id) ORDER BY s.status, s.id").fetchall()
        for sid, sname, status, key, raw in rows:
            if key == "ai-momentum" or len(out["redesign"]) >= cfg["redesign_max"]:
                continue
            spec = json.loads(raw)
            new = with_edge(spec, best["call"], best["put"])
            if new is None:
                continue
            base = battery.evaluate(conn, spec, {"tfs": spec.get("timeframes")}, None, f"base {sname}", "redesign-base")
            res = battery.evaluate(conn, new, {"tfs": new.get("timeframes")}, ledger, f"{sname} + edge rule", "redesign")
            bn, rn = (base.get("newest") or {}).get("win"), (res.get("newest") or {}).get("win")
            out["redesign"].append({"strategy_id": sid, "name": sname, "status": status, "base": _brief(base), "redesigned": _brief(res), "spec": new,
                                    "improved": bool(res["verdict"] in ("PASS", "PROMISING") and rn is not None and (bn is None or rn > bn))})
    out["finished"] = time.time()
    prog("Done", 1.0)
    return out


# ----------------------------------------------------------------------------------------------- the job (separate process) and what the server does with its result
class EdgeLab:
    """Starts the search in a low-priority process, reports its progress, and turns an edge into paper strategies (and redesigned paper challengers)."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.dir = Path(str(engine.db_path)).parent / "edgelab" if str(engine.db_path) != ":memory:" else None
        self._proc: subprocess.Popen | None = None
        self._job: dict = {"running": False, "stage": "", "progress": 0.0, "error": None, "started": None}
        self._finished: list[str] = []

    @staticmethod
    def _on(name: str, default: str = "on") -> bool:
        return os.getenv(name, default).strip().lower() not in ("off", "0", "false", "no")

    def start(self, cfg: dict | None = None, sync: bool = False) -> bool:
        if self._job["running"]:
            return False
        self._job = {"running": True, "stage": "starting", "progress": 0.0, "error": None, "started": time.time()}
        run_id = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        self._job["run_id"] = run_id
        if sync or not self.dir:
            try:
                res = run(self.engine.db, {**(cfg or {}), "ledger": False}, None)
                self._keep(run_id, res)
                self._job.update(running=False, progress=1.0)
                self._finished.append(run_id)
            except Exception as e:  # noqa: BLE001
                self._job.update(running=False, error=str(e))
            return True
        self.dir.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "app.edgelab", "--db", str(self.engine.db_path), "--out", str(self.dir), "--run-id", run_id, "--cfg", json.dumps(cfg or {})]
        flags = 0x00004000 | 0x08000000 if os.name == "nt" else 0
        log = open(self.dir / "edgelab.log", "ab")
        self._proc = subprocess.Popen(cmd, cwd=str(Path(__file__).resolve().parent.parent), stdout=log, stderr=log, creationflags=flags,
                                      preexec_fn=(lambda: os.nice(10)) if os.name != "nt" else None)
        return True

    def _keep(self, run_id: str, res: dict) -> None:
        self._mem = getattr(self, "_mem", {})
        self._mem[run_id] = res

    def job(self) -> dict:
        if self._proc is not None and self.dir:
            try:
                prog = json.loads((self.dir / "progress.json").read_text())
            except Exception:  # noqa: BLE001
                prog = {}
            if self._proc.poll() is None:
                self._job.update(stage=prog.get("stage", ""), progress=float(prog.get("progress", 0.0)))
            else:
                if prog.get("error") or not prog.get("done"):
                    self._job.update(running=False, error=prog.get("error") or f"the search stopped (exit code {self._proc.returncode}); see data/edgelab/edgelab.log")
                else:
                    self._job.update(running=False, progress=1.0, stage="Done", error=None)
                    self._finished.append(self._job.get("run_id", ""))
                self._proc = None
        return dict(self._job)

    def runs(self) -> list[dict]:
        out = []
        files = sorted(self.dir.glob("run-*.json"), reverse=True) if self.dir and self.dir.exists() else []
        for f in files[:20]:
            try:
                r = json.loads(f.read_text())
                out.append({"id": f.stem[4:], "started": r.get("started"), "outcome": (r.get("outcome") or {}).get("status"), "why": (r.get("outcome") or {}).get("why"),
                            "funnel": r.get("funnel"), "global": (r.get("global") or {}).get("verdict"), "applied": r.get("applied", False)})
            except Exception:  # noqa: BLE001
                continue
        for rid, r in getattr(self, "_mem", {}).items():
            out.insert(0, {"id": rid, "started": r.get("started"), "outcome": (r.get("outcome") or {}).get("status"), "why": (r.get("outcome") or {}).get("why"),
                           "funnel": r.get("funnel"), "global": (r.get("global") or {}).get("verdict"), "applied": r.get("applied", False)})
        return out

    def get(self, run_id: str) -> dict | None:
        if run_id in getattr(self, "_mem", {}):
            return self._mem[run_id]
        f = self.dir / f"run-{run_id}.json" if self.dir else None
        if not f or not f.exists():
            return None
        return json.loads(f.read_text())

    def _save(self, run_id: str, res: dict) -> None:
        if run_id in getattr(self, "_mem", {}):
            return
        (self.dir / f"run-{run_id}.json").write_text(json.dumps(res, default=_jsonable))

    def tick(self) -> None:
        """Every minute: handle a finished run (auto-create the paper strategies when an edge was found) and start the weekly run when due."""
        self.job()
        while self._finished:
            rid = self._finished.pop(0)
            if rid and self._on("QXS_EDGELAB_AUTO"):
                try:
                    res = self.get(rid)
                    if res and (res.get("outcome") or {}).get("status") == "edge_found" and not res.get("applied"):
                        self.apply(rid)
                except Exception as e:  # noqa: BLE001
                    self.engine._log("edgelab-error", str(e))
        if self.dir and self._on("QXS_EDGELAB_WEEKLY") and not self._job["running"]:
            stamp = self.dir / "weekly.json"
            try:
                last = json.loads(stamp.read_text())["t"] if stamp.exists() else 0
            except Exception:  # noqa: BLE001
                last = 0
            if time.time() - last > 7 * 86400 and self.engine.db.execute("SELECT COUNT(*) FROM candles WHERE tf=60").fetchone()[0] >= 20000:
                self.dir.mkdir(parents=True, exist_ok=True)
                stamp.write_text(json.dumps({"t": time.time()}))
                self.start()

    def apply(self, run_id: str, force: bool = False) -> dict:
        """Create the winning rule as a PAPER strategy and the improved redesigns as paper challengers. Without an edge nothing is created unless force=True
        (then only the best rule, for measuring it live)."""
        res = self.get(run_id)
        if not res:
            raise ValueError("run not found")
        status = (res.get("outcome") or {}).get("status")
        if status not in ("edge_found", "possible_edge") and not force:
            raise ValueError("this run found no edge, so nothing is created (use 'install anyway' to measure the best rule on paper)")
        spec = res.get("strategy_spec")
        if not spec:
            raise ValueError("no strategy was built in this run")
        base, n, key = f"edge-{run_id[4:12]}", 1, f"edge-{run_id[4:12]}"
        while self.engine.db.execute("SELECT 1 FROM strategies WHERE key=?", (key,)).fetchone():
            n += 1
            key = f"{base}-{n}"
        best = next((c for c in res["candidates"] if c["rank"] == res["outcome"]["best"]), res["candidates"][0])
        sid = self.engine.create_strategy(key, f"EDGE LAB: {' + '.join(best['atoms'])} ({best['side']})", f"Built by the Edge Lab from the old candles ({status}): {best['rule']}", spec, "paper")
        made = [sid]
        for r in res.get("redesign", []):
            if not r.get("improved") or not self.engine.repo.get(r["strategy_id"]):
                continue
            ck = f"{self.engine.repo.get(r['strategy_id'])['key']}-edge"
            m = 1
            while self.engine.db.execute("SELECT 1 FROM strategies WHERE key=?", (ck,)).fetchone():
                m += 1
                ck = f"{self.engine.repo.get(r['strategy_id'])['key']}-edge{m}"
            child = self.engine.create_strategy(ck, f"{r['name']} + EDGE RULE", f"{r['name']} redesigned to also satisfy the Edge Lab rule ({best['rule']})", r["spec"], "paper")
            self.engine.db.execute("INSERT INTO upgrades (parent_id, child_id, created, status, filters, backtest) VALUES (?,?,?,?,?,?)",
                                   (r["strategy_id"], child, time.time(), "testing", f"Edge Lab rule: {' + '.join(best['atoms'])}", json.dumps(r["redesigned"].get("newest"))))
            self.engine.db.commit()
            made.append(child)
        res["applied"] = True
        res["applied_ids"] = made
        self._save(run_id, res)
        self.engine._log("edgelab", f"run {run_id}: created {len(made)} paper strateg{'y' if len(made) == 1 else 'ies'}")
        return {"created": made}


def _jsonable(o):
    if hasattr(o, "item"):
        return o.item()
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def _main() -> None:
    import argparse
    import sqlite3
    import traceback
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--cfg", default="{}")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    def write(**kw):
        (out / "progress.json").write_text(json.dumps({"stage": "", "progress": 0.0, "error": None, "done": False, **kw}))
    try:
        conn = sqlite3.connect(f"file:{Path(a.db).as_posix()}?mode=ro", uri=True, timeout=60)
        res = run(conn, json.loads(a.cfg), lambda stage, p: write(stage=stage, progress=round(float(p), 3)))
        (out / f"run-{a.run_id}.json.tmp").write_text(json.dumps(res, default=_jsonable))
        os.replace(out / f"run-{a.run_id}.json.tmp", out / f"run-{a.run_id}.json")
        write(stage="Done", progress=1.0, done=True)
    except Exception as e:  # noqa: BLE001
        (out / "edgelab.log").open("a").write(traceback.format_exc())
        write(error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    _main()
