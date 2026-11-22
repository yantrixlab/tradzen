"""Walk-forward strategy sweeps: score many candidate strategies the same strict way, so luck is not mistaken for an edge.

Why this exists: with ~1000 trades the margin of error on a win rate is +-3 points, and the best of N random strategies always looks good.
So every candidate is judged like this:

* ONE time cut for everybody - the newest `holdout_fraction` of the stored candles is the holdout ("verification") period, the rest is "fit".
* the bar is the break-even win rate of the payout (100 / (100 + payout)), not 50 %.
* a candidate only PASSES when its fit AND its holdout period both beat break-even, there are enough holdout trades, and the one-sided
  p-value of the holdout result - multiplied by the number of candidates EVER swept (Bonferroni, cumulative over all stored sweeps) - is below alpha.
* verdicts say what is missing: no_edge / need_data / overfit / promising / pass.
Nothing here can promise profit; "pass" means "not explained by luck so far, now paper-trade it".
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
import threading
import time

import numpy as np
import pandas as pd

from . import mtg
from .candles import is_display_only
from .backtest import DEFAULT_PAYOUT, BacktestError, compute_trades
from .spec import validate_spec
from .stats_util import wilson

MAX_CANDIDATES = 300
DEFAULTS = {"holdout_fraction": 0.3, "min_trades": 1500, "alpha": 0.05, "min_fit_trades": 100, "min_payout": None, "mtg_steps": 0, "mtg_mult": 2.2}
CFG_KEYS = ("tfs", "symbols", "holdout_fraction", "min_trades", "alpha", "min_fit_trades", "min_payout", "mtg_steps", "mtg_mult")
VERDICT_ORDER = {"pass": 0, "promising": 1, "overfit": 2, "need_data": 3, "no_edge": 4, "no_trades": 5, "error": 6}
Z_POWER = 1.645 + 0.84                      # one-sided 5 % test with 80 % power


# ----------------------------------------------------------------------------------------------- candidates
def set_path(obj, path: str, value) -> None:
    """obj['gates']['min_agree'] = v for 'gates.min_agree'; list items by index ('factors.0.params.n')."""
    parts = path.split(".")
    cur = obj
    for p in parts[:-1]:
        cur = cur[int(p)] if isinstance(cur, list) else cur[p]
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        if last not in cur:
            raise KeyError(f"no such field: {path}")
        cur[last] = value


def _fmt(v) -> str:
    return json.dumps(v, separators=(",", ":")) if isinstance(v, (list, dict)) else str(v)


def expand_candidates(items: list[dict], get_spec, all_strategies: list[tuple[str, dict]] | None = None) -> list[dict]:
    """items: {name?, strategy_id? | spec?, grid?: {path: [values]}}. Returns [{name, spec | None, error | None, params}] (grid = cartesian product)."""
    out: list[dict] = []
    for it in items:
        try:
            base_name, base = get_spec(it)
        except (BacktestError, ValueError, KeyError) as e:
            out.append({"name": it.get("name") or "candidate", "spec": None, "error": str(e), "params": {}})
            continue
        grid = it.get("grid") or {}
        if not grid:
            out.append({"name": it.get("name") or base_name, "spec": base, "error": None, "params": {}})
            continue
        keys = list(grid)
        for vals in itertools.product(*[grid[k] if isinstance(grid[k], list) else [grid[k]] for k in keys]):
            sp = copy.deepcopy(base)
            params = dict(zip(keys, vals))
            err = None
            try:
                for k, v in params.items():
                    set_path(sp, k, v)
            except (KeyError, IndexError, TypeError, ValueError) as e:
                err = f"bad grid path: {e}"
            name = f"{it.get('name') or base_name} [" + ", ".join(f"{k}={_fmt(v)}" for k, v in params.items()) + "]"
            out.append({"name": name, "spec": None if err else sp, "error": err, "params": params})
    for name, sp in all_strategies or []:
        out.append({"name": name, "spec": sp, "error": None, "params": {}})
    if len(out) > MAX_CANDIDATES:
        raise BacktestError(f"{len(out)} candidates - the limit is {MAX_CANDIDATES} per sweep (more candidates also make the luck correction harsher)")
    return out


def spec_hash(spec: dict, tfs: list[int], mtg_cfg: tuple[int, float] | None = None) -> str:
    """Identity of a hypothesis: the (normalised) strategy, the timeframes, and - when martingale is on - its steps and multiple (a different
    way of trading it). Re-running the same thing later is not a new hypothesis. Flat hashes are unchanged by the MTG option."""
    key: dict = {"s": spec, "tf": sorted(tfs)}
    if mtg_cfg and mtg_cfg[0]:
        key["mtg"] = [int(mtg_cfg[0]), float(mtg_cfg[1])]
    return hashlib.sha1(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:16]


def hashes_of_result(res: dict | None) -> set[str]:
    out: set[str] = set()
    cfg = (res or {}).get("config") or {}
    tfs = cfg.get("tfs") or [60]
    for c in (res or {}).get("candidates", []):
        h = c.get("spec_hash") or (spec_hash(c["spec"], tfs, (cfg.get("mtg_steps") or 0, cfg.get("mtg_mult") or 2.2)) if c.get("spec") else None)
        if h:
            out.add(h)
    return out


# ----------------------------------------------------------------------------------------------- statistics
def _p_above(wins: int, n: int, be: float) -> tuple[float, float]:
    """(z, one-sided p) for H0: true win rate == break-even."""
    if n <= 0 or not 0 < be < 1:
        return 0.0, 1.0
    z = (wins / n - be) / math.sqrt(be * (1 - be) / n)
    return z, 0.5 * math.erfc(z / math.sqrt(2))


def segment_stats(t: pd.DataFrame) -> dict:
    """Win rate vs the break-even of the trades' own payouts, expectancy with its interval, one-sided p-value."""
    wins = int((t["result"] == "win").sum())
    losses = int((t["result"] == "loss").sum())
    n = wins + losses
    out = {"trades": int(len(t)), "settled": n, "wins": wins, "accuracy": None, "ci_low": None, "ci_high": None, "breakeven": None,
           "z": None, "p": None, "expectancy": None, "exp_ci_low": None, "exp_ci_high": None}
    if not n:
        return out
    payout = float(t.loc[t["result"].isin(["win", "loss"]), "payout"].mean())
    be = 1 / (1 + payout / 100)
    z, p = _p_above(wins, n, be)
    lo, hi = wilson(wins, n)
    prof = t["profit"].to_numpy(float)
    se = float(prof.std(ddof=1) / math.sqrt(len(prof))) if len(prof) > 1 else 0.0
    m = float(prof.mean())
    out.update(accuracy=wins / n, ci_low=lo, ci_high=hi, breakeven=be, z=z, p=p, expectancy=m, exp_ci_low=m - 1.96 * se, exp_ci_high=m + 1.96 * se)
    return out


def trades_needed(p_true: float, be: float) -> int | None:
    """Settled trades needed to confirm (80 % power, 5 % one-sided) that a real win rate p_true beats break-even `be`."""
    if p_true is None or p_true <= be:
        return None
    return int(math.ceil((Z_POWER * math.sqrt(p_true * (1 - p_true)) / (p_true - be)) ** 2))


def noise_max_z(n_tried: int) -> float:
    """Roughly the best z-score the luckiest of `n_tried` coin-flip strategies reaches."""
    return math.sqrt(2 * math.log(max(2, n_tried)))


def judge(fit: dict, hold: dict, tried: int, cfg: dict) -> tuple[str, str, float | None]:
    """(verdict, plain-language reason, luck-corrected holdout p)."""
    if not fit["settled"] and not hold["settled"]:
        return "no_trades", "the strategy never signalled on the stored data", None
    adj = None if hold["p"] is None else min(1.0, hold["p"] * max(1, tried))
    if fit["settled"] < cfg["min_fit_trades"]:
        return "need_data", f"only {fit['settled']} fit trades (need {cfg['min_fit_trades']}) - too few to say anything", adj
    if fit["accuracy"] <= fit["breakeven"]:
        return "no_edge", f"fit win rate {fit['accuracy']:.1%} does not beat break-even {fit['breakeven']:.1%}", adj
    if hold["settled"] < cfg["min_trades"]:
        return "need_data", f"fit looks above break-even, but only {hold['settled']} holdout trades (need {cfg['min_trades']}) - cannot tell luck from edge", adj
    if hold["accuracy"] <= hold["breakeven"]:
        return "overfit", f"fit {fit['accuracy']:.1%} but holdout {hold['accuracy']:.1%} is below break-even {hold['breakeven']:.1%} - the fit edge did not repeat", adj
    if adj < cfg["alpha"]:
        return "pass", f"both periods beat break-even and the holdout is significant after correcting for {tried} candidates tried (p={adj:.3f})", adj
    return "promising", f"both periods beat break-even, but luck still explains it (corrected p={adj:.2f} for {tried} tried) - keep collecting data", adj


# ----------------------------------------------------------------------------------------------- martingale scoring
def segment_stats_mtg(t: pd.DataFrame) -> dict:
    """Average net profit per ladder (units of the first stake) with its t-statistic and the risk numbers martingale hides."""
    u = t[(~t["mtg_skip"]) & t["mtg_pl"].notna()].sort_values("t")
    n = int(len(u))
    skipped = int(t["mtg_skip"].sum())
    out = {"trades": int(len(t)), "settled": n, "skipped": skipped, "open_ladders": int(len(t)) - n - skipped, "mean": None, "sd": None, "ci_low": None, "ci_high": None, "z": None,
           "p": None, "flat_mean": None, "first_losses": 0, "lost_ladders": 0, "lost_rate": None, "recovered": 0, "worst": None, "stake_max": None, "max_drawdown": None,
           "accuracy": None, "breakeven": None, "expectancy": None}
    if not n:
        return out
    x = u["mtg_pl"].to_numpy(float)
    m = float(x.mean())
    sd = float(x.std(ddof=1)) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 1 else 0.0
    z = m / se if se > 0 else 0.0
    cum = np.cumsum(x)
    first_loss = u["result"] == "loss"
    lost = int((u["mtg_outcome"] == "lost").sum())
    out.update(mean=m, sd=sd, ci_low=m - 1.96 * se, ci_high=m + 1.96 * se, z=z, p=0.5 * math.erfc(z / math.sqrt(2)), expectancy=m,
               flat_mean=float(u["profit"].mean()), lost_ladders=lost, lost_rate=lost / n, recovered=int(u["mtg_outcome"].isin([f"mtg{k}" for k in range(1, 6)]).sum()),
               worst=float(x.min()), stake_max=float(u["mtg_stake"].max()), max_drawdown=float((np.maximum.accumulate(np.r_[0.0, cum])[1:] - cum).max()),
               first_losses=int(first_loss.sum()))
    return out


def trades_needed_mtg(mean: float | None, sd: float | None) -> int | None:
    if not mean or mean <= 0 or not sd:
        return None
    return int(math.ceil((Z_POWER * sd / mean) ** 2))


def judge_mtg(fit: dict, hold: dict, tried: int, cfg: dict) -> tuple[str, str, float | None]:
    """Same ladder of questions as judge(), but the yardstick is average profit per ladder > 0 (martingale outcomes are not win/lose)."""
    if not fit["settled"] and not hold["settled"]:
        return "no_trades", "the strategy never signalled on the stored data", None
    adj = None if hold["p"] is None else min(1.0, hold["p"] * max(1, tried))
    if fit["settled"] < cfg["min_fit_trades"]:
        return "need_data", f"only {fit['settled']} fit ladders (need {cfg['min_fit_trades']}) - too few to say anything", adj
    if fit["mean"] <= 0:
        return "no_edge", f"with MTG the fit period averages {fit['mean']:+.3f} units per ladder - it loses money", adj
    if hold["settled"] < cfg["min_trades"]:
        return "need_data", f"fit averages {fit['mean']:+.3f} per ladder, but only {hold['settled']} holdout ladders (need {cfg['min_trades']}) - cannot tell luck from edge", adj
    if hold["mean"] <= 0:
        return "overfit", f"fit {fit['mean']:+.3f} per ladder but holdout {hold['mean']:+.3f} - the fit result did not repeat", adj
    if adj < cfg["alpha"]:
        return "pass", f"both periods profitable with MTG and the holdout is significant after correcting for {tried} tried (p={adj:.3f}) - check the worst ladder before trusting it", adj
    return "promising", f"both periods profitable with MTG, but luck still explains it (corrected p={adj:.2f} for {tried} tried) - keep collecting data", adj


# ----------------------------------------------------------------------------------------------- the sweep
def holdout_cut(conn, tfs: list[int], symbols: list[str] | None, fraction: float) -> int | None:
    """Time that splits the candles of the selected pairs into fit (older) and holdout (newest `fraction`)."""
    q = "SELECT ts FROM candles WHERE tf IN (%s)" % ",".join("?" * len(tfs))
    args: list = list(tfs)
    if symbols:
        q += " AND symbol IN (%s)" % ",".join("?" * len(symbols))
        args += list(symbols)
    ts = np.array([r[0] for r in conn.execute(q, args).fetchall()], dtype=np.int64)
    return int(np.quantile(ts, 1 - fraction)) if len(ts) else None


def select_pairs(conn, tfs: list[int], symbols: list[str] | None, min_payout: float | None, payouts: dict) -> list[str] | None:
    """Pairs to test. With min_payout, only pairs paying at least that much (a 60 %-payout pair needs 62.5 % wins just to break even)."""
    if not min_payout:
        return symbols
    have = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM candles WHERE tf IN (%s)" % ",".join("?" * len(tfs)), tfs).fetchall()]
    keep = [s for s in have if not is_display_only(s) and (not symbols or s in symbols) and float(payouts.get(s) or DEFAULT_PAYOUT) >= float(min_payout)]
    if not keep:
        raise BacktestError(f"no stored pair pays {min_payout:g}% or more right now - lower the minimum payout")
    return keep


def run_sweep(conn, candidates: list[dict], payouts: dict, cfg: dict, prior_hashes: set[str] | None = None, progress=None, cancelled=None) -> dict:
    cfg = {**DEFAULTS, **{k: v for k, v in cfg.items() if v is not None}}
    if not 0 < cfg["holdout_fraction"] < 0.9:
        raise BacktestError("holdout_fraction must be between 0 and 0.9")
    tfs = cfg.get("tfs") or [60]
    symbols = select_pairs(conn, tfs, cfg.get("symbols"), cfg.get("min_payout"), payouts)
    cfg["symbols"] = symbols
    cut = holdout_cut(conn, tfs, symbols, cfg["holdout_fraction"])
    if cut is None:
        raise BacktestError("no stored candles match the requested pairs/timeframes yet")
    steps, mult = int(cfg.get("mtg_steps") or 0), float(cfg.get("mtg_mult") or 2.2)
    if not 0 <= steps <= 5 or not 1 <= mult <= 10:
        raise BacktestError("mtg_steps must be 0-5 and mtg_mult 1-10")
    cfg["mtg_steps"], cfg["mtg_mult"] = steps, mult
    own = {spec_hash(c["spec"], tfs, (steps, mult)) for c in candidates if c.get("spec") is not None}
    tried = len((prior_hashes or set()) | own)                       # distinct hypotheses ever tested - a re-run of the same one adds nothing
    rows = []
    for i, c in enumerate(candidates):
        if cancelled and cancelled():
            raise BacktestError("cancelled")
        row = {"name": c["name"], "params": c.get("params") or {}, "verdict": "error", "reason": c.get("error") or "", "fit": None, "holdout": None,
               "adj_p": None, "trades_needed": None, "pairs_above_breakeven": None, "pairs_tested": None, "spec": None}
        if c.get("spec") is not None:
            try:
                tr = compute_trades(conn, c["spec"], {"symbols": cfg.get("symbols"), "tfs": tfs}, payouts)
            except BacktestError as e:
                tr = None
                row["reason"] = str(e)
            if tr is not None and steps:
                if not tr.empty:
                    tr = mtg.add_ladders(conn, tr, steps, mult)
                else:
                    for col, v in (("mtg_pl", np.nan), ("mtg_skip", False), ("mtg_outcome", ""), ("mtg_stake", 1.0)):
                        tr[col] = v
                fit, hold = segment_stats_mtg(tr[tr["t"] < cut]), segment_stats_mtg(tr[tr["t"] >= cut])
                verdict, reason, adj = judge_mtg(fit, hold, tried, cfg)
                row.update(verdict=verdict, reason=reason, fit=fit, holdout=hold, adj_p=adj, spec=c["spec"], spec_hash=spec_hash(c["spec"], tfs, (steps, mult)),
                           trades_needed=trades_needed_mtg(fit["mean"], fit["sd"]) if fit["settled"] >= cfg["min_fit_trades"] else None,
                           flat_fit=segment_stats(tr[tr["t"] < cut]), flat_holdout=segment_stats(tr[tr["t"] >= cut]))       # the same trades without MTG, for comparison
                if not tr.empty:
                    per = [(s, segment_stats_mtg(g)) for s, g in tr[tr["t"] >= cut].groupby("symbol")]
                    per = [x for x in per if x[1]["settled"] >= 30]
                    row["pairs_tested"] = len(per)
                    row["pairs_above_breakeven"] = sum(1 for _, s in per if s["mean"] > 0)
            elif tr is not None:
                fit, hold = segment_stats(tr[tr["t"] < cut]), segment_stats(tr[tr["t"] >= cut])
                verdict, reason, adj = judge(fit, hold, tried, cfg)
                row.update(verdict=verdict, reason=reason, fit=fit, holdout=hold, adj_p=adj, spec=c["spec"], spec_hash=spec_hash(c["spec"], tfs),
                           trades_needed=trades_needed(fit["accuracy"], fit["breakeven"]) if fit["accuracy"] and fit["settled"] >= cfg["min_fit_trades"] else None)
                if not tr.empty:
                    h = tr[tr["t"] >= cut]
                    per = [(s, segment_stats(g)) for s, g in h.groupby("symbol")]
                    per = [x for x in per if x[1]["settled"] >= 30]
                    row["pairs_tested"] = len(per)
                    row["pairs_above_breakeven"] = sum(1 for _, s in per if s["accuracy"] > s["breakeven"])
        rows.append(row)
        if progress:
            progress((i + 1) / max(1, len(candidates)))
    def order(i: int):                                   # best verdict first, then the highest holdout z (a z from <30 trades is noise: last)
        h = rows[i]["holdout"] or {}
        z = h.get("z") if h.get("settled", 0) >= 30 else None
        return VERDICT_ORDER[rows[i]["verdict"]], -(z if z is not None else -99.0)

    for rank, i in enumerate(sorted(range(len(rows)), key=order), 1):
        rows[i]["rank"] = rank
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    best = max((r["holdout"]["z"] for r in rows if r["holdout"] and r["holdout"]["z"] is not None), default=None)
    return {"config": {k: cfg.get(k) for k in CFG_KEYS} | {"pairs_used": len(symbols) if symbols else None}, "split_t": cut,
            "tried_now": len(candidates), "tried_total": tried, "noise_max_z": round(noise_max_z(tried), 2),
            "best_holdout_z": None if best is None else round(best, 2), "counts": counts, "candidates": rows}


# ----------------------------------------------------------------------------------------------- persistence / background runs
class SweepRunner:
    def __init__(self, engine) -> None:
        self.engine = engine
        self._cancel: set[int] = set()

    def _spec_of(self, it: dict) -> tuple[str, dict]:
        if it.get("strategy_id") is not None:
            d = self.engine.repo.get(int(it["strategy_id"]))
            if not d:
                raise BacktestError(f"strategy {it['strategy_id']} not found")
            name, raw = d["name"], d["spec"]
        elif it.get("spec") is not None:
            name, raw = it.get("name") or "custom spec", it["spec"]
        else:
            raise BacktestError("a candidate needs a strategy_id or a spec")
        v = validate_spec(raw)
        if not v["ok"]:
            raise BacktestError("invalid spec: " + "; ".join(v["errors"][:3]))
        if v["spec"]["kind"] == "meta":
            raise BacktestError("consensus (meta) strategies cannot be swept")
        return name, v["spec"]

    def build_candidates(self, config: dict) -> list[dict]:
        extra: list[tuple[str, dict]] = []
        if config.get("all_strategies"):
            for s in self.engine.repo.list(system_only=True):
                try:
                    extra.append(self._spec_of({"strategy_id": s["id"]}))
                except BacktestError:
                    continue
        cands = expand_candidates(config.get("candidates") or [], self._spec_of, extra)
        # a candidate whose grid made it invalid is reported, never silently dropped
        for c in cands:
            if c["spec"] is not None:
                v = validate_spec(c["spec"])
                if not v["ok"]:
                    c["spec"], c["error"] = None, "invalid spec: " + "; ".join(v["errors"][:3])
                else:
                    c["spec"] = v["spec"]
        if not cands:
            raise BacktestError("no candidates: list some, or set all_strategies")
        return cands

    def start(self, config: dict, sync: bool = False) -> int:
        cands = self.build_candidates(config)
        cfg = {k: config.get(k) for k in CFG_KEYS}
        if not 0 < (cfg["holdout_fraction"] or DEFAULTS["holdout_fraction"]) < 0.9:
            raise BacktestError("holdout_fraction must be between 0 and 0.9")
        db = self.engine.db
        cur = db.execute("INSERT INTO sweep_runs (created, label, config, status, progress, n_candidates) VALUES (?,?,?,?,?,?)",
                         (time.time(), (config.get("label") or "")[:80], json.dumps(cfg), "running", 0.0, len(cands)))
        db.commit()
        run_id = cur.lastrowid
        if sync or self.engine.db_path == ":memory:":
            self._execute(run_id, cands, cfg)
        else:
            threading.Thread(target=self._execute, args=(run_id, cands, cfg), daemon=True, name=f"sweep-{run_id}").start()
        return run_id

    def _execute(self, run_id: int, cands: list[dict], cfg: dict) -> None:
        conn, owned = self.engine.backtests._connect()
        try:
            def progress(p):
                conn.execute("UPDATE sweep_runs SET progress=? WHERE id=?", (round(p, 3), run_id))
                conn.commit()

            prior: set[str] = set()
            for (blob,) in conn.execute("SELECT result FROM sweep_runs WHERE status='done' AND id<>?", (run_id,)).fetchall():
                prior |= hashes_of_result(json.loads(blob) if blob else None)
            res = run_sweep(conn, cands, self.engine.backtests._payouts(), cfg, prior, progress, lambda: run_id in self._cancel)
            conn.execute("UPDATE sweep_runs SET status='done', progress=1, finished=?, result=? WHERE id=?", (time.time(), json.dumps(res), run_id))
            conn.commit()
        except Exception as e:                                              # report, never crash the server thread
            conn.rollback()
            conn.execute("UPDATE sweep_runs SET status=?, finished=?, error=? WHERE id=?",
                         ("cancelled" if str(e) == "cancelled" else "error", time.time(), str(e)[:500], run_id))
            conn.commit()
        finally:
            self._cancel.discard(run_id)
            if owned:
                conn.close()

    def cancel(self, run_id: int) -> None:
        self._cancel.add(run_id)

    def get(self, run_id: int) -> dict | None:
        r = self.engine.db.execute("SELECT id, created, finished, label, config, status, progress, error, result, n_candidates FROM sweep_runs WHERE id=?",
                                   (run_id,)).fetchone()
        if not r:
            return None
        d = dict(zip(["id", "created", "finished", "label", "config", "status", "progress", "error", "result", "n_candidates"], r))
        d["config"] = json.loads(d["config"]) if d["config"] else None
        d["result"] = json.loads(d["result"]) if d["result"] else None
        return d

    def list(self, limit: int = 30) -> list[dict]:
        rows = self.engine.db.execute("SELECT id, created, finished, label, status, progress, error, n_candidates, result FROM sweep_runs "
                                      "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for id_, created, finished, label, status, progress, error, n, result in rows:
            res = json.loads(result) if result else {}
            out.append({"id": id_, "created": created, "finished": finished, "label": label, "status": status, "progress": progress, "error": error,
                        "n_candidates": n, "counts": res.get("counts"), "best_holdout_z": res.get("best_holdout_z"), "noise_max_z": res.get("noise_max_z")})
        return out

    def delete(self, run_id: int) -> bool:
        cur = self.engine.db.execute("DELETE FROM sweep_runs WHERE id=?", (run_id,))
        self.engine.db.commit()
        return cur.rowcount > 0

