"""What is running in the background right now, and how to stop it: one list for the menu's "running" indicator and its panel.

The long jobs of the server each keep their own state (research, the AI training, the risk analysis, the Edge Lab, backtests, sweeps, the market-data refresh). This module only READS
that state into one list and routes a "stop" to the right place; it starts nothing and holds no state of its own.

Who sees what: an administrator sees everything. A staff account (editor) sees the sweeps and its own backtests; a trader sees its own backtests. The research, AI, risk analysis,
Edge Lab and market-data jobs are the server's own, so only an administrator sees them or can stop them (the same rule as their pages).

Stopping: research, AI training, risk analysis and the Edge Lab run in a separate process, which is killed. A backtest or a sweep is a thread of the server; it is asked to stop and
stops at its next step (a few seconds). The market-data refresh cannot be stopped (it is light: a few web requests).
"""
from __future__ import annotations

import time

from .backtest import BacktestError


def _is_admin(user: dict | None) -> bool:
    return user is None or user.get("role") == "admin"


def _is_staff(user: dict | None) -> bool:
    return user is None or user.get("role") in ("admin", "editor")


def _job(kind: str, ref, title: str, page: str, started: float | None, progress: float | None, detail: str = "", can_stop: bool = True) -> dict:
    return {"key": f"{kind}:{ref}", "kind": kind, "ref": str(ref), "title": title, "page": page, "started": started,
            "progress": None if progress is None else max(0.0, min(1.0, float(progress))), "detail": detail, "can_stop": can_stop}


def snapshot(engine, marketdata, user: dict | None) -> list[dict]:
    """The running jobs this user may see, oldest first."""
    out: list[dict] = []
    db = engine.db
    if _is_admin(user):
        rr = engine.research
        rr._reap()
        if rr.running_id is not None:
            row = db.execute("SELECT created, progress FROM research_runs WHERE id=?", (rr.running_id,)).fetchone()
            out.append(_job("research", rr.running_id, "Research check", "/research", row[0] if row else rr._started or None, row[1] if row else None,
                            "does any model find information in the last candles?"))
        ai = engine.ai
        ai._refresh_training()
        if ai.state.get("running"):
            out.append(_job("ai", "train", "AI advisor training", "/ai", ai.state.get("started"), ai.state.get("progress"), "learning from the stored candles"))
        risk = engine.risk
        rj = risk.job()
        if rj.get("running"):
            queued = len(risk._queue)
            sid = rj.get("strategy_id")
            d = engine.repo.get(sid) if sid is not None else None
            out.append(_job("risk", sid, f"Risk analysis{': ' + d['name'] if d else ''}", "/strategies", rj.get("started"), rj.get("progress"),
                            f"{queued} more waiting" if queued else ""))
        el = engine.edgelab.job()
        if el.get("running"):
            out.append(_job("edgelab", el.get("run_id", "run"), "Edge Lab search", "/edgelab", el.get("started"), el.get("progress"), str(el.get("stage") or "")))
        md = marketdata.status() if marketdata is not None else None
        if md and md.get("running"):
            out.append(_job("marketdata", "refresh", "Real-market data refresh", "/data", None, md.get("progress"), "free public chart data", can_stop=False))
    for rid, created, label, progress, owner in db.execute("SELECT id, created, label, progress, owner_id FROM backtest_runs WHERE status='running' ORDER BY id").fetchall():
        if engine.backtests.sees_run(user, owner):
            out.append(_job("backtest", rid, f"Backtest{': ' + label if label else ' #' + str(rid)}", f"/backtests/{rid}", created, progress,
                            "stopping…" if rid in engine.backtests._cancel else ""))
    if _is_staff(user):
        for rid, created, label, progress in db.execute("SELECT id, created, label, progress FROM sweep_runs WHERE status='running' ORDER BY id").fetchall():
            out.append(_job("sweep", rid, f"Sweep{': ' + label if label else ' #' + str(rid)}", "/sweeps", created, progress,
                            "stopping…" if rid in engine.sweeps._cancel else ""))
    out.sort(key=lambda j: (j["started"] is None, j["started"] or 0))
    return out


def _kill(proc) -> None:
    if proc is not None and proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except Exception:                                        # noqa: BLE001
            pass


def stop(engine, kind: str, ref: str, user: dict | None) -> str:
    """Stop one job; returns what was done. Raises LookupError when it is not running (or not visible to this user), BacktestError when it cannot be stopped."""
    visible = {j["key"]: j for j in snapshot(engine, None, user)}
    job = visible.get(f"{kind}:{ref}")
    if job is None:
        raise LookupError("that job is not running")
    if kind == "research":
        engine.research.stop()
    elif kind == "ai":
        engine.ai.stop()
    elif kind == "risk":
        engine.risk.stop()
    elif kind == "edgelab":
        engine.edgelab.stop()
    elif kind == "backtest":
        engine.backtests.cancel(int(ref))
        return "asked to stop"
    elif kind == "sweep":
        engine.sweeps.cancel(int(ref))
        return "asked to stop"
    else:
        raise BacktestError("this job cannot be stopped")
    return "stopped"
