"""HTTP routes for the operator controls (see control.py). Included by main.py with the shared engine."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import chartdata, control, generator

router = APIRouter(prefix="/api")
_engine = None


def bind(engine) -> APIRouter:
    global _engine
    _engine = engine
    return router


class SettingsIn(BaseModel):
    alerts_enabled: bool | None = None


class BulkIn(BaseModel):
    ids: list[int] | None = None
    status: str


class PresetIn(BaseModel):
    name: str


def _run(fn, *args):
    try:
        return fn(_engine, *args)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/settings")
async def get_settings() -> dict:
    return control.settings_view(_engine)


@router.post("/settings")
async def post_settings(body: SettingsIn) -> dict:
    if body.alerts_enabled is not None:
        control.set_alerts_enabled(_engine, body.alerts_enabled)
    return control.settings_view(_engine)


@router.post("/strategies/bulk")
async def bulk(body: BulkIn) -> dict:
    return _run(control.bulk_status, body.ids, body.status)


@router.post("/strategies/pause-all")
async def pause_all() -> dict:
    return _run(control.pause_all)


@router.post("/strategies/resume-all")
async def resume_all() -> dict:
    return _run(control.resume_all)


@router.get("/presets")
async def presets() -> dict:
    return control.list_presets(_engine)


@router.post("/presets")
async def save_preset(body: PresetIn) -> dict:
    return _run(control.save_preset, body.name)


@router.post("/presets/apply")
async def apply_preset(body: PresetIn) -> dict:
    return _run(control.apply_preset, body.name)


@router.delete("/presets/{name}")
async def delete_preset(name: str) -> dict:
    if not control.delete_preset(_engine, name):
        raise HTTPException(404, "preset not found")
    return {"ok": True}


class GenerateIn(BaseModel):
    symbol: str
    tf: int = 60
    at: float | None = None                 # entry time (epoch seconds); omitted = the candle right after the latest closed one
    scope: str = "running"                  # running | all | ai (no predefined strategy: the AI builds one from the recent momentum)
    ids: list[int] | None = None
    min_grade: str = "C"
    save: bool = True


@router.post("/generate")
async def generate_trade(body: GenerateIn) -> dict:
    if body.scope == "ai":                  # the AI learns its strategy from the stored history in a worker thread (cached afterwards), so the live feed is never held up
        from . import momentum_ai
        await asyncio.to_thread(momentum_ai.model_for, _engine, body.tf)
    # a candle that is about to close (or a few seconds late from the feed) is waited for instead of failing the click
    for attempt in range(13):
        try:
            return generator.generate(_engine, body.symbol, body.tf, body.at, body.scope, body.ids, body.min_grade, body.save)
        except generator.NotClosedYet as e:
            if e.wait > 12 or attempt == 12:
                raise HTTPException(400, str(e))
            await asyncio.sleep(1)
        except ValueError as e:
            raise HTTPException(400, str(e))


@router.get("/generate/history")
async def generate_history(limit: int = 50) -> dict:
    return generator.history(_engine, limit)


@router.delete("/generate/history/{tid}")
async def generate_delete(tid: int) -> dict:
    if not generator.delete_trade(_engine, tid):
        raise HTTPException(404, "trade not found")
    return {"ok": True}


@router.get("/generate/calibration")
async def generate_calibration(tf: int = 60) -> dict:
    return generator.calibration(_engine, tf)


class ChartIn(BaseModel):
    spec: dict
    symbol: str
    tf: int = 60
    count: int = 120
    exprs: dict[str, str] = {}
    patterns: dict[str, dict] = {}
    all: bool = False            # indicators / zones / structure over every loaded candle (count = how many), one uninterrupted stretch at a time, not just the newest stretch
    marks: bool = False          # also draw the chart's own supply/demand zones and HH/HL/LH/LL structure when the strategy has none (or there is no strategy)


@router.post("/strategies/chart")
async def strategy_chart(body: ChartIn) -> dict:
    """Candles + the draft strategy's indicator lines + where each condition is true, for the builder's chart."""
    if body.tf not in (60, 300, 900):
        raise HTTPException(400, "tf must be 60, 300 or 900")
    try:
        if body.all:
            out = chartdata.build_all(_engine.chart_rows_all(body.symbol, body.tf, max(60, min(body.count, 20000))), body.spec, body.tf, marks_always=body.marks)
        else:
            rows, _ = _engine.chart_rows(body.symbol, body.tf)
            out = chartdata.build(rows, body.spec, body.exprs, body.count, body.patterns, marks_always=body.marks)
    except ValueError as e:
        raise HTTPException(400, str(e))
    out.update(symbol=body.symbol, tf=body.tf)
    return out


@router.get("/analyze/all")
async def analyze_all(symbol: str, tf: int = 60) -> dict:
    return control.analyze_all(_engine, symbol, tf)


@router.get("/ai/status")
async def ai_status() -> dict:
    """The AI advisor: whether it is trained, its unseen-data (walk-forward) validation, the reliability map, and its real live record."""
    return _engine.ai.status()


@router.post("/ai/train")
async def ai_train() -> dict:
    """Start (re)training in the background: builds the dataset from every stored candle, validates walk-forward, saves the model. Progress is in /ai/status."""
    started = _engine.ai.train()
    return {"started": started, "training": _engine.ai.state}


@router.get("/ai/read")
async def ai_read(symbol: str, tf: int = 60, direction: str | None = None) -> dict:
    """The AI's reading of the newest closed candle of a pair (probability the next candle closes up) and, given a signal direction, whether it confirms it."""
    rd = _engine.ai.read(symbol, tf)
    if direction in ("CALL", "PUT"):
        rd["confirmation"] = _engine.ai.confirm(direction, rd)
    return rd


@router.get("/risk")
async def risk_status() -> dict:
    """Risk advisor: per strategy, whether its backtest losses have been studied, what was found, and the analysis job's progress."""
    return _engine.risk.status()


@router.get("/edgelab")
async def edgelab_status() -> dict:
    """Edge Lab: the running job, the run history and the latest result (the AI's search for an edge in the stored candles)."""
    lab = _engine.edgelab
    runs = lab.runs()
    latest = lab.get(runs[0]["id"]) if runs else None
    candles = _engine.db.execute("SELECT COUNT(*), COUNT(DISTINCT symbol) FROM candles WHERE tf=60").fetchone()
    return {"job": lab.job(), "runs": runs, "latest": latest and {**latest, "id": runs[0]["id"]}, "candles_1m": candles[0], "pairs_1m": candles[1],
            "weekly": lab._on("QXS_EDGELAB_WEEKLY"), "auto": lab._on("QXS_EDGELAB_AUTO")}


@router.get("/edgelab/runs/{run_id}")
async def edgelab_run(run_id: str) -> dict:
    r = _engine.edgelab.get(run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    return {**r, "id": run_id}


@router.post("/edgelab/run")
async def edgelab_start() -> dict:
    """Start an edge search in a separate low-priority process (about a minute or two on 100k candles)."""
    return {"started": _engine.edgelab.start(), "job": _engine.edgelab.job()}


@router.post("/edgelab/runs/{run_id}/apply")
async def edgelab_apply(run_id: str, force: bool = False) -> dict:
    try:
        return _engine.edgelab.apply(run_id, force)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/strategy-health")
async def strategy_health() -> dict:
    """Which strategies to keep, watch, upgrade or pause - from their real settled signals and their backtest, with 95% ranges (never from a short unlucky run)."""
    return _engine.risk.health()


@router.get("/upgrades")
async def upgrades() -> dict:
    """Champion / challenger log: every filtered copy of a strategy the advisor created, how it is doing live, and why it was promoted or retired."""
    return {"upgrades": _engine.risk.upgrades(), "auto_upgrade": _engine.risk._on("QXS_AUTO_UPGRADE"), "nightly": _engine.risk._on("QXS_RISK_NIGHTLY"),
            "queue": len(_engine.risk._queue), "min_live": 300, "max_live": 1000}


@router.get("/risk/{sid}")
async def risk_report(sid: int) -> dict:
    rep = _engine.risk.report(sid)
    if rep is None:
        raise HTTPException(404, "this strategy has not been analysed yet")
    return rep


@router.post("/risk/{sid}/analyze")
async def risk_analyze(sid: int) -> dict:
    """Start the analysis in a separate low-priority process: backtest the strategy over all stored candles, learn which situations lose, test the improved version."""
    try:
        started = _engine.risk.analyze(sid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"started": started, "job": _engine.risk.job()}


@router.post("/risk/{sid}/apply")
async def risk_apply(sid: int) -> dict:
    """Create the improved version as a new PAPER strategy (the original is never changed)."""
    try:
        return {"strategy_id": _engine.risk.apply(sid)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/readiness")
async def readiness(symbol: str, tf: int = 60) -> dict:
    """How close each running strategy is to a signal on the candle that is forming right now (as if it closed as it is): {strategies: {id: {state, lead, p, call, put}}}."""
    if tf not in (60, 300, 900):
        raise HTTPException(400, "tf must be 60, 300 or 900")
    return control.readiness(_engine, symbol, tf)
