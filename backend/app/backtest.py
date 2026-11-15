"""Backtester: replays stored candles through a strategy spec with the same decision logic as live (vectorised, see vec.py).

No look-ahead: a decision after candle i closes enters at the OPEN of candle i+1 and settles at its CLOSE (win if price closed in the signalled
direction, draw if unchanged). Candles are processed per contiguous run, so indicators never span a data gap. Results are stored in
backtest_runs / backtest_trades and can be re-analysed by the clinic without recomputing.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time

import numpy as np
import pandas as pd

from . import evaluator, mtg, vec
from .spec import validate_spec
from .stats_util import summarize

DEFAULT_PAYOUT = 85.0
MTG_MULT = 2.2                                  # stake multiple of the one-step martingale shown next to every run's accuracy
FEATURE_COLS = ["rsi14", "rsi2", "stoch_k", "stoch_d", "cci20", "macd_hist", "bb_pctb", "bb_bw", "bb_bw_pctile", "atr_ratio", "adx14",
                "plus_di", "minus_di", "ema_stack", "ema21_slope_pct", "dist_hi20_atr", "dist_lo20_atr", "body_ratio", "upper_wick_ratio",
                "lower_wick_ratio", "candle_color", "streak", "ret1_pct", "ret5_pct", "ret15_pct", "hour_utc", "weekday", "session"]
MAX_TRADES = 200_000


class BacktestError(ValueError):
    pass


def compute_trades(conn: sqlite3.Connection, spec: dict, config: dict, payouts: dict[str, float], progress=None, cancelled=None) -> pd.DataFrame:
    """Run `spec` over the stored candles selected by config {symbols, tfs, start, end}; returns one row per trade."""
    tfs = config.get("tfs") or spec["timeframes"]
    tfs = [t for t in tfs if t in spec["timeframes"]]
    if not tfs:
        raise BacktestError("none of the requested timeframes is enabled for this strategy")
    pairs = [(s, tf) for s, tf in conn.execute("SELECT DISTINCT symbol, tf FROM candles").fetchall() if tf in tfs]
    if config.get("symbols"):
        pairs = [p for p in pairs if p[0] in config["symbols"]]
    sc = spec.get("scope") or {}
    pairs = [p for p in pairs if (not sc.get("symbols") or p[0] in sc["symbols"])
             and (not sc.get("market") or sc["market"] == ("OTC" if p[0].endswith("_otc") else "LIVE"))]
    if not pairs:
        raise BacktestError("no stored candles match the requested pairs/timeframes yet")
    start, end = config.get("start"), config.get("end")
    out: list[pd.DataFrame] = []
    for idx, (sym, tf) in enumerate(sorted(pairs)):
        if cancelled and cancelled():
            raise BacktestError("cancelled")
        q = "SELECT ts, o, h, l, c, ticks FROM candles WHERE symbol=? AND tf=?"
        args: list = [sym, tf]
        if start:
            q += " AND ts >= ?"
            args.append(int(start))
        if end:
            q += " AND ts <= ?"
            args.append(int(end))
        rows = conn.execute(q + " ORDER BY ts", args).fetchall()
        if len(rows) > 70:
            df_all = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "n"]).astype(float)
            payout = float(payouts.get(sym) or DEFAULT_PAYOUT)
            for a, b in vec.contiguous_runs(df_all["t"].to_numpy().astype(np.int64), tf):
                run = df_all.iloc[a:b].reset_index(drop=True)
                if len(run) < spec["gates"]["min_candles"] + 2:
                    continue
                sig = vec.evaluate_series(spec, run, payout)
                idxs = np.flatnonzero((sig["dir"].to_numpy() != 0))
                idxs = idxs[idxs < len(run) - 1]                      # the settling candle must exist
                if not len(idxs):
                    continue
                feats = vec.featurize(run)
                d = sig["dir"].to_numpy()[idxs]
                entry, exit_ = run["open"].to_numpy()[idxs + 1], run["close"].to_numpy()[idxs + 1]
                move = (exit_ - entry) * d
                result = np.where(move > 0, "win", np.where(move < 0, "loss", "draw"))
                profit = np.where(result == "win", payout / 100, np.where(result == "loss", -1.0, 0.0))
                t = pd.DataFrame({"symbol": sym, "tf": tf, "t": run["t"].to_numpy()[idxs].astype(np.int64), "dir": d,
                                  "tier": np.where(sig["tier"].to_numpy()[idxs] == 1, "A", "B"), "strength": sig["strength"].to_numpy()[idxs],
                                  "agree": sig["agree"].to_numpy()[idxs], "score": sig["score"].to_numpy()[idxs], "entry": entry, "exit": exit_,
                                  "result": result, "profit": profit, "payout": payout})
                f = feats.iloc[idxs].reset_index(drop=True)
                for col in FEATURE_COLS:
                    t[col] = f[col].to_numpy()
                t["trend_align"] = t["dir"] * t["ema_stack"].fillna(0).astype(int)
                out.append(t)
        if progress:
            progress((idx + 1) / len(pairs))
    if not out:
        return pd.DataFrame(columns=["symbol", "tf", "t", "dir", "tier", "strength", "agree", "score", "entry", "exit", "result", "profit", "payout",
                                     *FEATURE_COLS, "trend_align"])
    trades = pd.concat(out, ignore_index=True).sort_values("t").reset_index(drop=True)
    if len(trades) > MAX_TRADES:
        raise BacktestError(f"more than {MAX_TRADES} trades - narrow the pairs, timeframes or dates")
    return trades


def split_time(trades: pd.DataFrame, valid_fraction: float) -> int | None:
    """Time that separates the older (fit) part from the newest (verification) part of the trades."""
    if trades.empty or not 0 < valid_fraction < 1:
        return None
    return int(trades["t"].quantile(1 - valid_fraction))


def build_result(trades: pd.DataFrame, valid_fraction: float) -> dict:
    split = split_time(trades, valid_fraction)
    res = {"summary": summarize(trades), "split_t": split, "valid_fraction": valid_fraction}
    if split is not None:
        res["train"] = summarize(trades[trades["t"] < split])
        res["valid"] = summarize(trades[trades["t"] >= split])
    res["by_tf"] = {str(tf): {k: v for k, v in summarize(g).items() if k != "equity"} for tf, g in trades.groupby("tf")}
    res["by_tier"] = {k: {kk: vv for kk, vv in summarize(g).items() if kk != "equity"} for k, g in trades.groupby("tier")}
    res["by_direction"] = {("CALL" if k == 1 else "PUT"): {kk: vv for kk, vv in summarize(g).items() if kk != "equity"} for k, g in trades.groupby("dir")}
    by_sym = sorted(((s, summarize(g)) for s, g in trades.groupby("symbol")), key=lambda x: -x[1]["trades"])[:20]
    res["by_symbol"] = {s: {k: v for k, v in st.items() if k != "equity"} for s, st in by_sym}
    return res


class BacktestRunner:
    def __init__(self, engine) -> None:
        self.engine = engine
        self._cancel: set[int] = set()
        self._threads: dict[int, threading.Thread] = {}
        # a run that was going when the server stopped can never finish: close it, or it would count as running for ever (the limits count running runs)
        engine.db.execute("UPDATE backtest_runs SET status='error', error='interrupted by a restart', finished=? WHERE status='running'", (time.time(),))
        engine.db.commit()

    # ------------------------------------------------------------------ helpers
    def _payouts(self) -> dict[str, float]:
        return {s: i.get("payout") for s, i in self.engine.instruments.items() if i.get("payout")}

    def _connect(self) -> tuple[sqlite3.Connection, bool]:
        """(connection, owned). In-memory databases (tests) are only reachable through the engine's own connection."""
        if self.engine.db_path == ":memory:":
            return self.engine.db, False
        conn = sqlite3.connect(self.engine.db_path, timeout=60, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout=60000")
        return conn, True

    def resolve_spec(self, config: dict) -> tuple[dict, int | None, int | None]:
        if config.get("strategy_id") is not None:
            d = self.engine.repo.get(int(config["strategy_id"]))
            if not d:
                raise BacktestError("strategy not found")
            v = validate_spec(d["spec"])
            sid, ver = d["id"], d["version"]
        else:
            v, sid, ver = validate_spec(config.get("spec")), None, None
        if not v["ok"]:
            raise BacktestError("invalid spec: " + "; ".join(v["errors"][:4]))
        if v["spec"]["kind"] == "meta":
            raise BacktestError("consensus (meta) strategies cannot be backtested on their own")
        return v["spec"], sid, ver

    # ------------------------------------------------------------------ inline (no persistence): used by 'try this filter'
    def run_inline(self, spec: dict, config: dict) -> pd.DataFrame:
        conn, owned = self._connect()
        try:
            return compute_trades(conn, spec, config, self._payouts())
        finally:
            if owned:
                conn.close()

    # ------------------------------------------------------------------ persisted runs
    MAX_RUNNING = 3                          # backtests running at the same time on the whole server (each one is a thread that reads the candles)

    def usage(self, uid: int) -> dict:
        """What a user has used: runs started in the last 24 h and runs going right now."""
        db = self.engine.db
        return {"day": db.execute("SELECT COUNT(*) FROM backtest_runs WHERE owner_id=? AND created>?", (uid, time.time() - 86400)).fetchone()[0],
                "running": db.execute("SELECT COUNT(*) FROM backtest_runs WHERE owner_id=? AND status='running'", (uid,)).fetchone()[0]}

    def running_now(self) -> int:
        return self.engine.db.execute("SELECT COUNT(*) FROM backtest_runs WHERE status='running'").fetchone()[0]

    def run_owner(self, run_id: int):
        """(exists, owner_id)."""
        r = self.engine.db.execute("SELECT owner_id FROM backtest_runs WHERE id=?", (run_id,)).fetchone()
        return (r is not None, r[0] if r else None)

    @staticmethod
    def sees_run(viewer: dict | None, owner) -> bool:
        if viewer is None or viewer.get("role") == "admin":
            return True
        if viewer.get("role") == "editor":
            return owner is None or owner == viewer["id"]
        return owner == viewer["id"]

    def start(self, config: dict, sync: bool = False, owner: int | None = None) -> int:
        spec, sid, ver = self.resolve_spec(config)
        if self.running_now() >= self.MAX_RUNNING and self.engine.db_path != ":memory:":
            raise BacktestError("the server is busy with other backtests - try again in a minute")
        vf = float(config.get("valid_fraction", 0.3))
        if not 0 < vf < 1:
            raise BacktestError("valid_fraction must be between 0 and 1")
        cfg = {"symbols": config.get("symbols"), "tfs": config.get("tfs"), "start": config.get("start"), "end": config.get("end"), "valid_fraction": vf}
        db = self.engine.db
        cur = db.execute("INSERT INTO backtest_runs (created, label, strategy_id, strategy_version, spec, config, status, progress, n_trades, owner_id) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (time.time(), (config.get("label") or "")[:80], sid, ver, json.dumps(spec), json.dumps(cfg), "running", 0.0, 0, owner))
        db.commit()
        run_id = cur.lastrowid
        if sync or self.engine.db_path == ":memory:":
            self._execute(run_id, spec, cfg)
        else:
            th = threading.Thread(target=self._execute, args=(run_id, spec, cfg), daemon=True, name=f"backtest-{run_id}")
            self._threads[run_id] = th
            th.start()
        return run_id

    def cancel(self, run_id: int) -> None:
        self._cancel.add(run_id)

    def _execute(self, run_id: int, spec: dict, cfg: dict) -> None:
        conn, owned = self._connect()
        try:
            def progress(p):
                conn.execute("UPDATE backtest_runs SET progress=? WHERE id=?", (round(p, 3), run_id))
                conn.commit()

            trades = compute_trades(conn, spec, cfg, self._payouts(), progress, lambda: run_id in self._cancel)
            result = build_result(trades, cfg["valid_fraction"])
            result["mtg1"] = mtg.run_summary(conn, trades, 1, MTG_MULT)          # accuracy with a one-step martingale, shown in the runs table
            conn.execute("DELETE FROM backtest_trades WHERE run_id=?", (run_id,))
            conn.executemany(
                "INSERT INTO backtest_trades (run_id, symbol, tf, t, dir, tier, strength, agree, score, entry, exit, result, profit, payout, features) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(run_id, r.symbol, int(r.tf), int(r.t), int(r.dir), r.tier, int(r.strength), int(r.agree), float(r.score), float(r.entry), float(r.exit),
                  r.result, float(r.profit), float(r.payout), json.dumps({c: _clean(getattr(r, c)) for c in FEATURE_COLS + ["trend_align"]}))
                 for r in trades.itertuples(index=False)])
            conn.execute("UPDATE backtest_runs SET status='done', progress=1, finished=?, result=?, n_trades=? WHERE id=?",
                         (time.time(), json.dumps(result), len(trades), run_id))
            conn.commit()
        except Exception as e:                                        # report, never crash the server thread
            conn.rollback()
            conn.execute("UPDATE backtest_runs SET status=?, finished=?, error=? WHERE id=?",
                         ("cancelled" if str(e) == "cancelled" else "error", time.time(), str(e)[:500], run_id))
            conn.commit()
        finally:
            self._cancel.discard(run_id)
            self._threads.pop(run_id, None)
            if owned:
                conn.close()

    # ------------------------------------------------------------------ reads
    def list_runs(self, limit: int = 50, viewer: dict | None = None) -> list[dict]:
        """viewer: whose runs (admin / no viewer: all; editor: the shared ones and their own; trader: their own)."""
        if viewer is None or viewer.get("role") == "admin":
            where, args = "", []
        elif viewer.get("role") == "editor":
            where, args = "WHERE owner_id IS NULL OR owner_id=?", [viewer["id"]]
        else:
            where, args = "WHERE owner_id=?", [viewer["id"]]
        rows = self.engine.db.execute(
            "SELECT id, created, finished, label, strategy_id, strategy_version, config, status, progress, error, n_trades, result "
            f"FROM backtest_runs {where} ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        out, need = [], False
        for id_, created, finished, label, sid, ver, config, status, progress, error, n, result in rows:
            res = json.loads(result) if result else {}
            summ = res.get("summary", {})
            m = res.get("mtg1")
            pending = status == "done" and m is None                   # an older run, finished before this column existed: filled in by the backfill
            need = need or pending
            out.append({"id": id_, "created": created, "finished": finished, "label": label, "strategy_id": sid, "strategy_version": ver,
                        "strategy": self.engine.names.get(sid), "config": json.loads(config), "status": status, "progress": progress, "error": error,
                        "n_trades": n, "accuracy": summ.get("accuracy"), "expectancy": summ.get("expectancy"), "breakeven": summ.get("breakeven"),
                        "mtg1": m, "mtg1_pending": pending})
        if need:
            self.start_mtg_backfill()
        return out

    # ------------------------------------------------------------------ one-step martingale numbers for runs that predate them
    def start_mtg_backfill(self) -> None:
        """Computes the missing one-step-MTG numbers of finished runs in a background thread (a run's trades + candles are re-read once, then cached in the run)."""
        th = getattr(self, "_backfill", None)
        if th is not None and th.is_alive():
            return
        if self.engine.db_path == ":memory:":                          # tests: no second connection exists, so do it inline
            self.backfill_mtg()
            return
        self._backfill = threading.Thread(target=self.backfill_mtg, daemon=True, name="mtg-backfill")
        self._backfill.start()

    def backfill_mtg(self) -> int:
        conn, owned = self._connect()
        done = 0
        try:
            ids = [r[0] for r in conn.execute("SELECT id FROM backtest_runs WHERE status='done' AND result IS NOT NULL AND result NOT LIKE '%\"mtg1\"%' ORDER BY id DESC").fetchall()]
            for run_id in ids:
                res_json = conn.execute("SELECT result FROM backtest_runs WHERE id=?", (run_id,)).fetchone()[0]
                res = json.loads(res_json)
                rows = conn.execute("SELECT symbol, tf, t, dir, tier, strength, agree, score, entry, exit, result, profit, payout, features FROM backtest_trades WHERE run_id=? ORDER BY t",
                                    (run_id,)).fetchall()
                try:
                    res["mtg1"] = mtg.run_summary(conn, trades_frame(rows), 1, MTG_MULT)
                except Exception as e:                                 # noqa: BLE001 - one odd run must not stop the others; mark it so it is not retried forever
                    res["mtg1"] = {"error": str(e)[:120], "taken": 0, "accuracy": None, "rescued": 0}
                conn.execute("UPDATE backtest_runs SET result=? WHERE id=?", (json.dumps(res), run_id))
                conn.commit()
                done += 1
        finally:
            if owned:
                conn.close()
        return done

    def get_run(self, run_id: int) -> dict | None:
        r = self.engine.db.execute("SELECT id, created, finished, label, strategy_id, strategy_version, spec, config, status, progress, error, result, n_trades "
                                   "FROM backtest_runs WHERE id=?", (run_id,)).fetchone()
        if not r:
            return None
        keys = ["id", "created", "finished", "label", "strategy_id", "strategy_version", "spec", "config", "status", "progress", "error", "result", "n_trades"]
        d = dict(zip(keys, r))
        for k in ("spec", "config", "result"):
            d[k] = json.loads(d[k]) if d[k] else None
        d["strategy"] = self.engine.names.get(d["strategy_id"])
        return d

    def chart(self, symbol: str, tf: int, strategy_id: int | None = None, run_id: int | None = None, limit: int = 4000,
              mtg_steps: int = 0, mtg_mult: float = 2.2) -> dict:
        """Candles of one pair/timeframe plus the trades a strategy takes on them (for the lab's charts).

        run_id      -> the trades that run stored (exactly what it reported), plus its validation split
        strategy_id -> a fresh replay of that strategy on this pair's whole stored series (nothing is saved)
        neither     -> just candles
        mtg_steps>0 -> every losing trade also gets its martingale ladder (see app/mtg.py) and the result carries the ladder summary
        """
        limit = max(100, min(int(limit), 20000))
        mtg_steps = max(0, min(int(mtg_steps), 5))
        if mtg_mult < 1 or mtg_mult > 10:
            raise BacktestError("mtg_mult must be between 1 and 10")
        conn, owned = self._connect()
        try:
            total, first_t, last_t = conn.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM candles WHERE symbol=? AND tf=?", (symbol, tf)).fetchone()
            rows = conn.execute("SELECT ts, o, h, l, c FROM candles WHERE symbol=? AND tf=? ORDER BY ts DESC LIMIT ?", (symbol, tf, limit)).fetchall()[::-1]
            out: dict = {"symbol": symbol, "tf": tf, "total": total, "first": first_t, "last": last_t, "candles": [list(r) for r in rows],
                         "trades": [], "split_t": None, "note": None, "strategy": None, "payout": None}
            if not rows:
                return out
            t0 = rows[0][0]
            trades = pd.DataFrame()
            if run_id is not None:
                run = self.get_run(run_id)
                if not run:
                    raise BacktestError("run not found")
                out["strategy"], out["split_t"] = run["strategy"], (run.get("result") or {}).get("split_t")
                trades = trades_frame(conn.execute(
                    "SELECT symbol, tf, t, dir, tier, strength, agree, score, entry, exit, result, profit, payout, features FROM backtest_trades "
                    "WHERE run_id=? AND symbol=? AND tf=? AND t>=? ORDER BY t", (run_id, symbol, tf, t0)).fetchall())
            elif strategy_id is not None:
                spec, _, _ = self.resolve_spec({"strategy_id": strategy_id})
                out["strategy"] = self.engine.names.get(strategy_id)
                try:
                    trades = compute_trades(conn, spec, {"symbols": [symbol], "tfs": [tf]}, self._payouts())
                    trades = trades[trades["t"] >= t0]
                except BacktestError as e:           # e.g. the strategy is not enabled for this timeframe / pair scope
                    out["note"] = str(e)
            if len(trades):
                out["payout"] = float(trades["payout"].iloc[0])
                out["trades"] = [{"t": int(r.t), "dir": int(r.dir), "tier": r.tier, "strength": int(r.strength), "entry": float(r.entry), "exit": float(r.exit),
                                  "result": r.result, "profit": float(r.profit)} for r in trades.itertuples(index=False)]
                if mtg_steps:
                    out["mtg"] = mtg.apply(out["trades"], {r[0]: (r[1], r[4]) for r in rows}, tf, out["payout"], mtg_steps, float(mtg_mult))
            return out
        finally:
            if owned:
                conn.close()

    def delete_run(self, run_id: int) -> bool:
        self.engine.db.execute("DELETE FROM backtest_trades WHERE run_id=?", (run_id,))
        cur = self.engine.db.execute("DELETE FROM backtest_runs WHERE id=?", (run_id,))
        self.engine.db.commit()
        return cur.rowcount > 0

    def clear_all(self) -> dict:
        """Delete EVERY backtest run and all their trades (and the clinic findings that came from them). Candles, strategies, live / paper signals and
        everything else are untouched. Refused while a run is still going - cancel it first."""
        running = self.engine.db.execute("SELECT COUNT(*) FROM backtest_runs WHERE status='running'").fetchone()[0]
        if running:
            raise ValueError(f"{running} backtest{' is' if running == 1 else 's are'} still running - cancel {'it' if running == 1 else 'them'} first")
        db = self.engine.db
        runs = db.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0]
        trades = db.execute("SELECT COUNT(*) FROM backtest_trades").fetchone()[0]
        db.execute("DELETE FROM backtest_trades")
        db.execute("DELETE FROM backtest_runs")
        insights = db.execute("DELETE FROM insights WHERE source='backtest'").rowcount
        db.commit()
        return {"runs": runs, "trades": trades, "insights": insights}

    def load_trades(self, run_id: int) -> pd.DataFrame:
        return trades_frame(self.engine.db.execute(
            "SELECT symbol, tf, t, dir, tier, strength, agree, score, entry, exit, result, profit, payout, features "
            "FROM backtest_trades WHERE run_id=? ORDER BY t", (run_id,)).fetchall())

    def trades_page(self, run_id: int, limit: int = 100, offset: int = 0) -> dict:
        total = self.engine.db.execute("SELECT COUNT(*) FROM backtest_trades WHERE run_id=?", (run_id,)).fetchone()[0]
        rows = self.engine.db.execute(
            "SELECT symbol, tf, t, dir, tier, strength, agree, score, entry, exit, result, profit, payout FROM backtest_trades WHERE run_id=? "
            "ORDER BY t DESC LIMIT ? OFFSET ?", (run_id, max(1, min(limit, 1000)), max(0, offset))).fetchall()
        keys = ["symbol", "tf", "t", "dir", "tier", "strength", "agree", "score", "entry", "exit", "result", "profit", "payout"]
        return {"total": total, "rows": [dict(zip(keys, r)) for r in rows]}


def _clean(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return None if np.isnan(v) or np.isinf(v) else round(float(v), 5)
    return v


def trades_frame(rows: list[tuple]) -> pd.DataFrame:
    cols = ["symbol", "tf", "t", "dir", "tier", "strength", "agree", "score", "entry", "exit", "result", "profit", "payout"]
    df = pd.DataFrame([r[:13] for r in rows], columns=cols)
    if rows:
        feats = pd.DataFrame([json.loads(r[13]) if r[13] else {} for r in rows])
        df = pd.concat([df, feats], axis=1)
    return df
