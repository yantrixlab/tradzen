"""Tradzen backend. Run:  .venv\\Scripts\\python -m uvicorn app.main:app --host 127.0.0.1 --port 8787"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import backup, candleadmin, expr, extpack, prefs as prefs_mod, seed as seedmod, indreg, routes_auth, routes_control, routes_markets, routes_pay, routes_sessions, payments as payments_mod, markets as markets_mod, feedsources
from .auth import COOKIE as COOKIE_NAME, from_env
from .strategy_access import Access, is_staff
from .feature_flags import Features
from .backtest import BacktestError
from .engine import Engine
from .marketdata import MarketData
from .spec import BASE_NAMES, FILTERS, TFS as TFS_SPEC
from .strategy import FN_FACTORS
from .strategy_repo import STATUSES, RepoError

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("QXS_DB", ROOT / "data" / "signals.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_seed = os.getenv("QXS_SEED", "")
if _seed and Path(_seed).is_file():
    # first start of a new server: the data collected elsewhere (candles, history, strategies ...) becomes this server's database. Ignored once a database exists.
    _loaded = seedmod.apply_seed(_seed, DB_PATH)
    if _loaded:
        print("Tradzen: loaded the seed " + _seed + ": " + ", ".join(f"{k} {v:,}" for k, v in _loaded["counts"].items() if k != "schema_version"), flush=True)
_pending = seedmod.apply_pending(DB_PATH)         # a seed uploaded on the System page and waiting for a restart (only when nobody but the admin has an account)
if _pending:
    print("Tradzen: loaded the uploaded seed: " + ", ".join(f"{k} {v:,}" for k, v in _pending["counts"].items() if k != "schema_version"), flush=True)
elif (DB_PATH.parent / "seed" / seedmod.PENDING_NAME).is_file():
    print("Tradzen: an uploaded seed is waiting but this server already has other users, so it was not loaded (use 'add the candles' on the System page)", flush=True)
BACKUP_ON = os.getenv("QXS_BACKUP", "on" if os.getenv("QXS_ENV", "").lower() == "production" else "off").lower() in ("on", "1", "true", "yes")
BACKUP_HOUR = max(0, min(23, int(os.getenv("QXS_BACKUP_HOUR", "4") or 4)))       # UTC
BACKUP_KEEP = max(1, int(os.getenv("QXS_BACKUP_KEEP", "7") or 7))

engine = Engine(DB_PATH)
marketdata = MarketData(DB_PATH, enabled=os.getenv("QXS_MARKETDATA", "on").lower() != "off")   # real-market candles for the AI (QXS_MARKETDATA=off disables)


async def _research_scheduler() -> None:
    """Once a minute: start the nightly research check when it is due (see app/research.py). It runs in its own thread, never on the event loop."""
    while True:
        await asyncio.sleep(60)
        try:
            engine.research.tick()
        except Exception:             # noqa: BLE001 - the scheduler must never die
            pass
        try:
            engine.sessions.sweep()
        except Exception:             # noqa: BLE001
            pass
        try:
            engine.ai.nightly()       # AI advisor: retrain every night on everything stored (incl. what was collected live) ...
            engine.ai.tick()          # ... and log / score its call on every closed candle of the streaming pairs
        except Exception:             # noqa: BLE001
            pass
        try:
            engine.risk.nightly()     # risk advisor: re-study every live / paper strategy's backtest losses every night ...
            engine.risk.tick()        # ... run the queue, start paper challengers for filters that hold, judge running challengers on their live results
        except Exception:             # noqa: BLE001
            pass
        try:
            engine.edgelab.tick()     # Edge Lab: weekly edge search over the stored candles; an edge becomes paper strategies
        except Exception:             # noqa: BLE001
            pass
        try:
            marketdata.tick()         # real-market candles for the AI to learn from: first run a minute after start-up, then every few hours
        except Exception:             # noqa: BLE001
            pass
        try:
            engine.users_gc()         # close the private databases of users who have not been seen for hours
        except Exception:             # noqa: BLE001
            pass
        if BACKUP_ON:
            try:
                made = await asyncio.to_thread(backup.maybe_nightly, DB_PATH, BACKUP_HOUR, BACKUP_KEEP)       # a copy of every database, once a day (off the event loop)
                if made:
                    engine._log("backup", f"{made['name']}: {made['files']} databases, {made['bytes'] // 1_000_000} MB, {made['removed']} old backups removed")
            except Exception as e:    # noqa: BLE001
                engine._log("backup-error", str(e)[:300])


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_research_scheduler())
    yield
    task.cancel()
    engine.close()                    # flush buffered candles/events before exit


auth = from_env(engine.db)                                       # accounts; sign-in is ON with QXS_ENV=production or QXS_AUTH=on (see app/auth.py)
print(f"Tradzen: {'PRODUCTION' if auth.production else 'development'} mode, sign-in {'ON' if auth.enabled else 'OFF (local machine only)'}, "
      f"database {DB_PATH}, nightly backup {'ON' if BACKUP_ON else 'off'}", flush=True)
access = Access(engine)                                          # who may see / change / receive which strategy (app/strategy_access.py)
features = Features(engine.db)                                   # menus / capabilities, plans and per-user overrides (see app/feature_flags.py)
pay = payments_mod.Payments(engine.db, DB_PATH.parent / "pay", features.plan, features.plans)       # plans bought with manual payments: methods, QR, orders, approval (app/payments.py)
markets = markets_mod.Markets(engine, features, auth)                      # the public chart platform: pair locks, market list, sign-up (app/markets.py)
feedsources.apply(engine)                                                 # the admin's stored choice about ordinary accounts' candles / live prices wins over QXS_CONTRIB / QXS_LIVE_FEED
_env_admin = engine.db.execute("SELECT id FROM users WHERE env_admin=1").fetchone()
if _env_admin:
    engine.set_primary(_env_admin[0])                          # the data that existed before accounts (the main database) belongs to the admin from the .env file
auth_router, guard = routes_auth.bind(auth, features, on_user_deleted=lambda uid: (engine.drop_user(uid), engine.purge_user_strategies(uid)))

app = FastAPI(title="Tradzen", docs_url=None if auth.production else "/docs", redoc_url=None, openapi_url=None if auth.production else "/openapi.json",
              lifespan=lifespan, dependencies=[guard])         # `guard` runs before every route
if not auth.enabled:                                           # local development only: the dev server (5173) and the extension talk to localhost; a hosted server is same-origin
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(chrome-extension://.*|http://(127\.0\.0\.1|localhost)(:\d+)?)$",
        allow_methods=["*"], allow_headers=["*"],
    )

if auth.production:
    SECURITY_HEADERS = [
        (b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"), (b"x-frame-options", b"DENY"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
        (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
                                     b"connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"),
    ]

    PANEL_HEADERS = [(k, v) for k, v in SECURITY_HEADERS if k not in (b"x-frame-options", b"content-security-policy")] + [
        (b"content-security-policy", dict(SECURITY_HEADERS)[b"content-security-policy"].replace(b"frame-ancestors 'none'", b"frame-ancestors chrome-extension:"))]       # /admin/...?panel=1: framed by the extension's side panel only

    class SecurityHeaders:
        """Adds the browser-hardening headers to every response (the reverse proxy adds HSTS)."""

        def __init__(self, inner) -> None:
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.inner(scope, receive, send)

            async def send_with_headers(message):
                if message["type"] == "http.response.start":
                    have = {k.lower() for k, _ in message.get("headers", [])}
                    wanted = PANEL_HEADERS if scope["path"].startswith("/admin") and b"panel=1" in scope.get("query_string", b"") else SECURITY_HEADERS
                    message["headers"] = [*message.get("headers", []), *[(k, v) for k, v in wanted if k not in have]]
                await send(message)

            await self.inner(scope, receive, send_with_headers)

    app.add_middleware(SecurityHeaders)

app.include_router(auth_router)


app.include_router(routes_pay.bind(engine, auth, pay))
app.include_router(routes_markets.bind(engine, auth, markets, pay))   # markets, sign-up, pricing (markets.py)               # plan payments (payments.py)
app.include_router(routes_sessions.bind(engine, auth, features))   # live sessions (live_session.py)
app.include_router(routes_control.bind(engine, lambda request, symbol: _pair_guard(request, symbol)))      # settings, bulk strategy control, presets, signal board


class NoteIn(BaseModel):
    tags: list[str] = Field(default_factory=list)
    note: str = ""


class StrategyIn(BaseModel):
    key: str
    name: str = ""
    description: str = ""
    spec: dict
    status: str = "paper"


class SpecIn(BaseModel):
    spec: dict
    notes: str = ""


class StatusIn(BaseModel):
    status: str


class CloneIn(BaseModel):
    key: str
    name: str | None = None


class MetaIn(BaseModel):
    name: str | None = None
    description: str | None = None


class PreviewIn(BaseModel):
    spec: dict
    symbol: str
    tf: int = 60


def _repo_call(fn, *args):
    try:
        return fn(*args)
    except RepoError as e:
        raise HTTPException(400, str(e))


def _user(request: Request) -> dict:
    return request.state.user


def _own_or_404(request: Request, sid: int) -> None:
    """The caller may change this strategy (their own; staff: the shared ones). Anything else looks like it does not exist."""
    if not access.can_edit(_user(request), sid):
        raise HTTPException(404, "strategy not found")


def _check_own_quota(user: dict) -> None:
    """A user's own strategies are limited by their plan (cap.strategies.own)."""
    if is_staff(user):
        return
    limit = features.limit(user, "cap.strategies.own")
    if limit is not None and engine.repo.owned_count(user["id"]) >= limit:
        raise HTTPException(403, "your plan does not allow more strategies of your own" if limit == 0 else f"your plan allows {limit} strategies of your own: delete or archive one first")


def _owner_for(user: dict) -> int | None:
    return None if is_staff(user) else user["id"]


@app.get("/api/strategies")
async def list_strategies(request: Request) -> list[dict]:
    """The strategies the caller can change: their own (staff: the shared ones too). The published ones of others are in /api/library."""
    u = _user(request)
    return engine.strategy_summary(ids={sid for sid in engine.strategy_meta if access.can_edit(u, sid)}, viewer=u)


@app.post("/api/strategies")
async def create_strategy(body: StrategyIn, request: Request) -> dict:
    u = _user(request)
    _check_own_quota(u)
    sid = _repo_call(engine.create_strategy, body.key, body.name, body.description, body.spec, body.status, _owner_for(u))
    return engine.strategy_detail(sid, u)


@app.post("/api/strategies/validate")
async def validate_strategy(body: dict) -> dict:
    return engine.validate_strategy_spec(body.get("spec", body))


@app.post("/api/strategies/preview")
async def preview_spec(body: PreviewIn) -> dict:
    v = engine.validate_strategy_spec(body.spec)
    if not v["ok"]:
        raise HTTPException(400, "invalid spec: " + "; ".join(v["errors"][:5]))
    if v["spec"]["kind"] == "meta":
        raise HTTPException(400, "meta (consensus) strategies cannot be previewed on a single pair")
    return {"warnings": v["warnings"], "analysis": engine.analyze_spec(v["spec"], body.symbol, body.tf)}


@app.get("/api/strategies/{sid}")
async def strategy_detail(sid: int, request: Request) -> dict:
    u = _user(request)
    if not access.can_view(u, sid):
        raise HTTPException(404, "strategy not found")
    d = engine.strategy_detail(sid, u)
    if d is None:
        raise HTTPException(404, "strategy not found")
    if not access.can_edit(u, sid):                            # a published strategy: its record, never its logic
        d.update(spec=None, versions=[], notes="", error=None, tags=[])
    return d


@app.get("/api/strategies/{sid}/versions/{version}")
async def strategy_version(sid: int, version: int, request: Request) -> dict:
    _own_or_404(request, sid)
    v = engine.repo.get_version(sid, version)
    if v is None:
        raise HTTPException(404, "version not found")
    return v


@app.put("/api/strategies/{sid}")
async def rename_strategy(sid: int, body: MetaIn, request: Request) -> dict:
    _own_or_404(request, sid)
    _repo_call(engine.rename_strategy, sid, body.name, body.description)
    return engine.strategy_detail(sid, _user(request))


@app.post("/api/strategies/{sid}/versions")
async def new_version(sid: int, body: SpecIn, request: Request) -> dict:
    _own_or_404(request, sid)
    _repo_call(engine.new_strategy_version, sid, body.spec, body.notes)
    return engine.strategy_detail(sid, _user(request))


@app.post("/api/strategies/{sid}/status")
async def set_status(sid: int, body: StatusIn, request: Request) -> dict:
    _own_or_404(request, sid)
    _repo_call(engine.set_strategy_status, sid, body.status)
    return engine.strategy_detail(sid, _user(request))


class AudienceIn(BaseModel):
    audience: str


@app.post("/api/strategies/{sid}/audience")
async def set_audience(sid: int, body: AudienceIn, request: Request) -> dict:
    """Publish a shared strategy: admin (testing, staff only) | optin (listed in the library, a user adds it) | everyone (every user gets its signals)."""
    u = _user(request)
    if not access.can_edit(u, sid):
        raise HTTPException(404, "strategy not found")
    before = (engine.strategy_meta.get(sid) or {}).get("audience")
    _repo_call(engine.set_strategy_audience, sid, body.audience)
    auth.audit(u["username"], "strategy-audience", engine.names.get(sid, str(sid)), {"from": before, "to": body.audience})
    return engine.strategy_detail(sid, u)


@app.post("/api/strategies/{sid}/clone")
async def clone_strategy(sid: int, body: CloneIn, request: Request) -> dict:
    u = _user(request)
    _own_or_404(request, sid)                                  # only whoever can read the logic can copy it
    _check_own_quota(u)
    new = _repo_call(engine.clone_strategy, sid, body.key, body.name, _owner_for(u))
    return engine.strategy_detail(new, u)


@app.delete("/api/strategies/{sid}")
async def delete_strategy(sid: int, request: Request) -> dict:
    _own_or_404(request, sid)
    return {"result": _repo_call(engine.delete_strategy, sid)}


@app.get("/api/strategies/{sid}/preview")
async def preview_strategy(sid: int, symbol: str, request: Request, tf: int = 60) -> dict:
    _own_or_404(request, sid)
    d = engine.strategy_detail(sid)
    if d is None:
        raise HTTPException(404, "strategy not found")
    return await preview_spec(PreviewIn(spec=d["spec"], symbol=symbol, tf=tf))


# ---- the library: what the team published, and which of it a user receives
@app.get("/api/library")
async def library(request: Request) -> list[dict]:
    """Published strategies with their measured record (never their logic). `receiving`: this user gets its signals; `always`: sent to everybody (switch it off for yourself)."""
    return access.library(_user(request))


def _library_item(request: Request, sid: int) -> dict:
    m = engine.strategy_meta.get(sid)
    if not m or not access.in_library(m):
        raise HTTPException(404, "that strategy is not in the library")
    return m


@app.post("/api/library/{sid}/subscribe")
async def library_subscribe(sid: int, request: Request) -> dict:
    u = _user(request)
    _library_item(request, sid)
    if not features.can(u, "cap.strategies.use_published"):
        raise HTTPException(403, "“Use published strategies” is not included in your plan")
    access.subscribe(u["id"], sid)
    return {"ok": True}


@app.delete("/api/library/{sid}/subscribe")
async def library_unsubscribe(sid: int, request: Request) -> dict:
    _library_item(request, sid)
    access.unsubscribe(_user(request)["id"], sid)
    return {"ok": True}


class BacktestIn(BaseModel):
    strategy_id: int | None = None
    spec: dict | None = None
    symbols: list[str] | None = None
    tfs: list[int] | None = None
    start: int | None = None
    end: int | None = None
    valid_fraction: float = 0.3
    label: str = ""
    sync: bool = False


class ClinicIn(BaseModel):
    source: str = "live"
    run_id: int | None = None
    strategy_id: int | None = None
    valid_fraction: float = 0.3
    min_n: int = 30
    q: float = 0.10
    save: bool = False


class TryIn(BaseModel):
    strategy_id: int
    suggestion: dict
    symbols: list[str] | None = None
    tfs: list[int] | None = None
    start: int | None = None
    end: int | None = None
    valid_fraction: float = 0.3


class ApplyIn(BaseModel):
    strategy_id: int
    suggestion: dict
    key: str | None = None
    name: str | None = None


class InsightStatusIn(BaseModel):
    status: str


def _bad_request(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (BacktestError, ValueError, RepoError) as e:
        raise HTTPException(400, str(e))


def _check_backtest_quota(user: dict) -> None:
    """A user's backtests are limited by their plan: runs per day and runs at the same time. Staff are not limited."""
    if is_staff(user):
        return
    used = engine.backtests.usage(user["id"])
    day, jobs = features.limit(user, "cap.backtest.daily"), features.limit(user, "cap.backtest.jobs")
    if day is not None and used["day"] >= day:
        raise HTTPException(429, "your plan's backtests for the last 24 hours are used up" if day else "your plan does not include backtests")
    if jobs is not None and used["running"] >= jobs:
        raise HTTPException(429, f"you already have {used['running']} backtest{'s' if used['running'] != 1 else ''} running: wait for it to finish")


def _run_or_404(request: Request, run_id: int) -> None:
    exists, owner = engine.backtests.run_owner(run_id)
    if not exists or not engine.backtests.sees_run(_user(request), owner):
        raise HTTPException(404, "run not found")


@app.post("/api/backtests")
async def start_backtest(body: BacktestIn, request: Request) -> dict:
    u = _user(request)
    cfg = body.model_dump()
    if body.strategy_id is not None and not access.can_edit(u, body.strategy_id):
        raise HTTPException(404, "strategy not found")
    _check_backtest_quota(u)
    run_id = _bad_request(engine.backtests.start, cfg, body.sync, u["id"])
    return engine.backtests.get_run(run_id)


@app.get("/api/backtests")
async def list_backtests(request: Request, limit: int = 50) -> list[dict]:
    return engine.backtests.list_runs(limit, _user(request))


class BtChartIn(BaseModel):
    symbol: str
    tf: int = 60
    strategy_id: int | None = None
    run_id: int | None = None
    limit: int = Field(4000, ge=100, le=20000)
    mtg_steps: int = Field(0, ge=0, le=5)          # martingale steps after a losing trade (0 = off)
    mtg_mult: float = Field(2.2, ge=1, le=10)      # stake multiple per step


@app.post("/api/backtests/chart")
async def backtest_chart(body: BtChartIn, request: Request) -> dict:
    """Candles + the strategy trades for one pair (one pair replays in well under a second, so it runs on the loop like the other reads)."""
    _pair_guard(request, body.symbol)
    if body.strategy_id is not None and not access.can_edit(_user(request), body.strategy_id):
        raise HTTPException(404, "strategy not found")
    if body.run_id is not None:
        _run_or_404(request, body.run_id)
    return _bad_request(engine.backtests.chart, body.symbol, body.tf, body.strategy_id, body.run_id, body.limit, body.mtg_steps, body.mtg_mult)


@app.get("/api/backtests/{run_id}")
async def get_backtest(run_id: int, request: Request) -> dict:
    _run_or_404(request, run_id)
    r = engine.backtests.get_run(run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    return r


@app.get("/api/backtests/{run_id}/trades")
async def backtest_trades(run_id: int, request: Request, limit: int = 100, offset: int = 0) -> dict:
    _run_or_404(request, run_id)
    return engine.backtests.trades_page(run_id, limit, offset)


@app.post("/api/backtests/{run_id}/cancel")
async def cancel_backtest(run_id: int, request: Request) -> dict:
    _run_or_404(request, run_id)
    engine.backtests.cancel(run_id)
    return {"ok": True}


@app.delete("/api/backtests")
async def clear_backtests(request: Request, confirm: str = "") -> dict:
    if _user(request)["role"] != "admin":
        raise HTTPException(403, "only an admin can delete every backtest")
    """Delete ALL backtest runs and trades. Needs ?confirm=all so it can never happen by accident."""
    if confirm != "all":
        raise HTTPException(400, "add ?confirm=all to delete every backtest run")
    return _bad_request(engine.backtests.clear_all)


@app.delete("/api/backtests/{run_id}")
async def delete_backtest(run_id: int, request: Request) -> dict:
    _run_or_404(request, run_id)
    if not engine.backtests.delete_run(run_id):
        raise HTTPException(404, "run not found")
    return {"ok": True}


class ResearchIn(BaseModel):
    windows: list[int] | None = None          # candles of history the models see (default 5 and 10)
    sync: bool = False


@app.get("/api/research/status")
async def research_status() -> dict:
    """Schedule: hour (UTC) of the nightly check, when it runs next, whether one is running now."""
    return engine.research.status()


@app.post("/api/research/run")
async def research_run(body: ResearchIn) -> dict:
    """Run the next-candle research check now (a few minutes on a laptop). 400 if one is already running or there is too little data."""
    run_id = _bad_request(engine.research.start, {"windows": body.windows}, "manual", body.sync)
    return engine.research.get(run_id)


@app.get("/api/research")
async def research_list(limit: int = 30) -> list[dict]:
    return engine.research.list(max(1, min(limit, 200)))


@app.get("/api/research/{run_id}")
async def research_get(run_id: int) -> dict:
    r = engine.research.get(run_id)
    if r is None:
        raise HTTPException(404, "research run not found")
    return r


class SweepIn(BaseModel):
    label: str = ""
    candidates: list[dict] = Field(default_factory=list)          # {name?, strategy_id? | spec?, grid?: {"gates.min_agree": [2, 3]}}
    all_strategies: bool = False
    tfs: list[int] | None = None
    symbols: list[str] | None = None
    holdout_fraction: float | None = None
    min_trades: int | None = None
    alpha: float | None = None
    min_fit_trades: int | None = None
    min_payout: float | None = Field(None, ge=0, le=100)      # only pairs paying at least this many % right now
    mtg_steps: int | None = Field(None, ge=0, le=5)           # score every strategy as a martingale ladder (0/None = flat trades)
    mtg_mult: float | None = Field(None, ge=1, le=10)
    sync: bool = False


@app.post("/api/sweeps")
async def start_sweep(body: SweepIn) -> dict:
    """Walk-forward scoring of many candidate strategies at once (see app/sweep.py). Runs in the background unless sync=true."""
    run_id = _bad_request(engine.sweeps.start, body.model_dump(exclude={"sync"}), body.sync)
    return engine.sweeps.get(run_id)


@app.get("/api/sweeps")
async def list_sweeps(limit: int = 30) -> list[dict]:
    return engine.sweeps.list(limit)


@app.get("/api/sweeps/{run_id}")
async def get_sweep(run_id: int) -> dict:
    r = engine.sweeps.get(run_id)
    if r is None:
        raise HTTPException(404, "sweep not found")
    return r


@app.post("/api/sweeps/{run_id}/cancel")
async def cancel_sweep(run_id: int) -> dict:
    engine.sweeps.cancel(run_id)
    return {"ok": True}


@app.delete("/api/sweeps/{run_id}")
async def delete_sweep(run_id: int) -> dict:
    if not engine.sweeps.delete(run_id):
        raise HTTPException(404, "sweep not found")
    return {"ok": True}


@app.post("/api/clinic/analyze")
async def clinic_analyze(body: ClinicIn) -> dict:
    return _bad_request(engine.clinic_analyze, body.source, body.run_id, body.strategy_id, body.valid_fraction, body.min_n, body.q, body.save)


@app.post("/api/clinic/try")
async def clinic_try(body: TryIn) -> dict:
    cfg = body.model_dump(exclude={"strategy_id", "suggestion"})
    return _bad_request(engine.clinic_try, body.strategy_id, body.suggestion, cfg)


@app.post("/api/clinic/apply")
async def clinic_apply(body: ApplyIn) -> dict:
    sid = _bad_request(engine.clinic_apply, body.strategy_id, body.suggestion, body.key, body.name)
    return engine.strategy_detail(sid)


@app.get("/api/insights")
async def insights(strategy_id: int | None = None, status: str | None = None) -> list[dict]:
    return engine.list_insights(strategy_id, status)


@app.patch("/api/insights/{insight_id}")
async def patch_insight(insight_id: int, body: InsightStatusIn) -> dict:
    if not _bad_request(engine.set_insight_status, insight_id, body.status):
        raise HTTPException(404, "insight not found")
    return {"ok": True}


@app.get("/api/signals/{signal_id}/similar")
async def similar_signals(signal_id: int, k: int = 15) -> dict:
    r = engine.similar_signals(signal_id, k)
    if r is None:
        raise HTTPException(404, "signal not found")
    return r


@app.get("/api/spec/catalog")
async def spec_catalog() -> dict:
    """Everything a strategy builder UI needs: indicator types (+defaults/fields), fn factors, filters, names and functions usable in conditions."""
    return {"indicators": {k: {"defaults": indreg.defaults(k), "fields": indreg.fields(k)} for k in sorted(indreg.REGISTRY)},
            "fn_factors": {k: v[1] for k, v in FN_FACTORS.items()},
            "filters": FILTERS, "base_names": BASE_NAMES, "functions": {k: list(v) for k, v in expr.FUNCS.items()},
            "timeframes": list(TFS_SPEC), "statuses": list(STATUSES)}


@app.get("/healthz")
async def healthz() -> dict:
    """For the container / load balancer: the process is up and the database answers."""
    try:
        engine.db.execute("SELECT 1").fetchone()
    except Exception:                                                  # noqa: BLE001
        raise HTTPException(503, "database unavailable")
    return {"ok": True}


MAX_INGEST_BYTES = 5_000_000
_ingest_times: dict[int, list[float]] = {}


def _ingest_ok(uid: int, limit: int = 100, window: float = 10.0) -> bool:
    """At most `limit` batches per `window` seconds for one (non-feeder) account."""
    now = time.time()
    hits = [t for t in _ingest_times.get(uid, []) if now - t < window]
    if len(hits) >= limit:
        _ingest_times[uid] = hits
        return False
    hits.append(now)
    _ingest_times[uid] = hits
    if len(_ingest_times) > 5000:
        for k in [k for k, v in _ingest_times.items() if not v or now - v[-1] > window]:
            del _ingest_times[k]
    return True


@app.post("/ingest")
async def ingest(request: Request) -> dict:
    user = request.state.user
    try:
        if int(request.headers.get("content-length") or 0) > MAX_INGEST_BYTES:
            raise HTTPException(413, "batch too large")
    except ValueError:
        raise HTTPException(400, "bad content-length")
    if not user.get("feeder") and not _ingest_ok(user["id"]):
        raise HTTPException(429, "too many requests")                 # an extension sends about one batch a second; anything near the limit is a bug or abuse
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(400, "expected an object")
    if not isinstance(payload.get("items", []), list):
        raise HTTPException(400, "items must be a list")
    items = [i for i in payload.get("items", []) if isinstance(i, dict)]
    if not features.can(user, "cap.trades.track"):
        items = [i for i in items if i.get("k") not in ("o", "b")]            # this user's plan does not record their trades
    # One Tradzen account <-> one Quotex account. The extension says which Quotex account its tab is logged into (a hash of the account number). Orders and balance are
    # accepted only from the linked account; if the tab is on another account, nothing private is stored and no alerts are sent until it is the linked one again.
    qx = payload.get("qx") if isinstance(payload.get("qx"), dict) else {}
    link = "off"
    if auth.enabled and user.get("via") == "token" and auth.link_quotex:
        link = auth.qx_check(user["id"], qx.get("h"), qx.get("hint"))
        if link != "ok" and link != "bound":
            items = [i for i in items if i.get("k") not in ("o", "b")]        # unknown (no account number seen yet: the extension holds them) / mismatch / taken
    # Only the price-feed token feeds the shared market; every sender's own orders and balance go to their own private data. Alerts come back filtered for the sender.
    res = engine.ingest({**payload, "items": items}, uid=user["id"], feeder=bool(user.get("feeder")))
    res["events"] = [e for e in res.get("events", []) if routes_auth.event_visible(user, e, features.can, access)]
    if link in ("mismatch", "taken"):
        res["events"] = []                                                  # the wrong Quotex account gets no alerts either
    if link != "off":
        res["qx"] = {"state": link, "hint": (auth.get_user(user["id"]) or {}).get("qx_hint")}
    res["as"] = {"username": user.get("username"), "role": user.get("role")}
    if not user.get("feeder") and auth.enabled and user.get("via") == "token":
        res["contrib"] = _contribute(user, link, [i for i in items if i.get("k") == "h"])        # shared candles: the history this tab's page loaded (see app/contrib.py)
        res["feed"] = _live_feed(user, link, [i for i in items if i.get("k") in ("t", "tp")])    # live prices: one account holds each pair (see app/leases.py)
    return res


def _live_feed(user: dict, link: str, ticks: list[dict]) -> dict:
    """Live prices from an ordinary account: state off | wait | paused | busy | on, the pairs to send ticks for now, and the pairs this account holds."""
    lz = engine.leases
    if not lz.enabled or not features.can(user, "cap.ticks.feed"):
        return {"state": "off"}
    if link in ("unknown", "mismatch", "taken"):
        return {"state": "wait"}                                          # prices are accepted only from a tab whose Quotex account is the linked one
    return lz.answer(user["id"], ticks)


def _contribute(user: dict, link: str, hist: list[dict]) -> dict:
    """What an ordinary extension may add to the shared candle store, and the answer it gets: state on | wait | paused | busy | off, the result per history item
    (so it does not send the same range again) and the ranges the server would like to have."""
    c = engine.contrib
    if not c.enabled or not features.can(user, "cap.candles.contribute"):
        return {"state": "off"}
    if link in ("unknown", "mismatch", "taken"):
        return {"state": "wait"}                                          # candles are accepted only from a tab whose Quotex account is the linked one
    out = c.intake(user["id"], hist) if hist else {"state": "paused" if c.blocked(user["id"]) else "on", "results": []}
    if out["state"] == "on":
        out["want"] = c.wants()[:5]
    return out


class PauseIn(BaseModel):
    reason: str = Field("paused by the admin", max_length=200)


@app.get("/api/contrib/mine")
async def contrib_mine(request: Request) -> dict:
    """The caller's own contributions to the shared candle store."""
    return engine.contrib.mine(request.state.user["id"])


@app.get("/api/contrib")
async def contrib_overview() -> dict:
    """Admin: who contributed what (7 days), candles that disagreed with stored ones, paused accounts, and the ranges the server wants."""
    return engine.contrib.overview()


def _contrib_target(uid: int) -> str:
    row = engine.db.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        raise HTTPException(404, "no such user")
    return row[0]


@app.post("/api/contrib/{uid}/pause")
async def contrib_pause(uid: int, request: Request, body: PauseIn) -> dict:
    name = _contrib_target(uid)
    engine.contrib.pause(uid, body.reason)
    auth.audit(request.state.user["username"], "contrib-pause", name, {"reason": body.reason})
    return {"ok": True}


@app.post("/api/contrib/{uid}/resume")
async def contrib_resume(uid: int, request: Request) -> dict:
    name = _contrib_target(uid)
    done = engine.contrib.resume(uid)
    auth.audit(request.state.user["username"], "contrib-resume", name)
    return {"ok": True, "was_paused": done}


@app.delete("/api/contrib/{uid}")
async def contrib_revoke(uid: int, request: Request) -> dict:
    """Remove every candle this account contributed (the feed's own and other accounts' candles are not touched)."""
    name = _contrib_target(uid)
    removed = engine.contrib.revoke(uid)
    auth.audit(request.state.user["username"], "contrib-revoke", name, {"candles": removed})
    return {"ok": True, "removed": removed}


@app.get("/api/livefeed/mine")
async def livefeed_mine(request: Request) -> dict:
    """The pairs the caller streams live prices for, and their counters."""
    return engine.leases.mine(request.state.user["id"])


@app.get("/api/livefeed")
async def livefeed_overview() -> dict:
    """Admin: who streams which pair, which pairs the price feed has, and each account's counters (7 days)."""
    return engine.leases.overview()


@app.post("/api/livefeed/{uid}/pause")
async def livefeed_pause(uid: int, request: Request, body: PauseIn) -> dict:
    name = _contrib_target(uid)
    engine.leases.pause(uid, body.reason)
    auth.audit(request.state.user["username"], "livefeed-pause", name, {"reason": body.reason})
    return {"ok": True}


@app.post("/api/livefeed/{uid}/resume")
async def livefeed_resume(uid: int, request: Request) -> dict:
    name = _contrib_target(uid)
    done = engine.leases.resume(uid)
    auth.audit(request.state.user["username"], "livefeed-resume", name)
    return {"ok": True, "was_paused": done}


@app.delete("/api/livefeed/{uid}")
async def livefeed_revoke(uid: int, request: Request) -> dict:
    """Stop this account's leases and remove the candles built from its ticks (the feed's own candles are not touched)."""
    name = _contrib_target(uid)
    removed = engine.leases.revoke(uid)
    auth.audit(request.state.user["username"], "livefeed-revoke", name, {"candles": removed})
    return {"ok": True, "removed": removed}


@app.get("/api/events")
async def events(request: Request, after: int | None = None) -> dict:
    user = request.state.user
    lr = engine.ingest_stats["last_recv"]
    return {"seq": engine.seq, "events": [e for e in engine.events_since(after, user["id"]) if routes_auth.event_visible(user, e, features.can, access)], "feed_age": (time.time() - lr) if lr else None}


class PrefsIn(BaseModel):
    prefs: dict


@app.get("/api/prefs")
async def get_prefs(request: Request) -> dict:
    """The caller's own display preferences (which chart layers are on, the indicators picked ...), kept per account so they follow the person between browsers."""
    return {"prefs": prefs_mod.get_all(engine.db, request.state.user["id"])}


@app.put("/api/prefs")
async def put_prefs(request: Request, body: PrefsIn) -> dict:
    try:
        return {"prefs": prefs_mod.apply(engine.db, request.state.user["id"], body.prefs)}
    except ValueError as e:
        raise HTTPException(400, str(e))


def _pair_guard(request: Request, symbol: str | None, own_ok: bool = False) -> None:
    """A paid pair stays closed to a free account on EVERY route that returns its candles (markets.py). own_ok: the pair open on the caller's own Quotex tab is theirs to see."""
    u = request.state.user
    if symbol and own_ok:
        mine = engine.user_sel.get(int(u["id"])) if u.get("id") is not None else None
        if mine and mine.get("symbol") == symbol:
            return
    try:
        markets.require(u, symbol)
    except markets_mod.Locked as e:
        raise HTTPException(403, str(e))


@app.get("/api/mychart")
async def mychart(request: Request, symbol: str | None = None, tf: int | None = None) -> dict:
    """The chart of the pair open on the caller's own Quotex tab (or of the pair asked for): every account sees its own."""
    _pair_guard(request, symbol, own_ok=True)
    return engine.mychart(request.state.user["id"], symbol, tf)


@app.get("/api/state")
async def state(symbol: str | None = None, tf: int | None = None) -> dict:
    return engine.state(symbol, tf)


@app.get("/api/analyze")
async def analyze(symbol: str, tf: int = 60) -> dict:
    return engine.analyze(symbol, tf)


@app.get("/api/stats")
async def stats(request: Request, symbol: str | None = None, tf: int | None = None, tier: str | None = None, strategy_id: int | None = None,
                mode: str | None = None) -> dict:
    allowed = access.allowed_signal_ids(_user(request))
    if strategy_id is not None and strategy_id not in allowed:
        raise HTTPException(404, "strategy not found")
    return engine.stats(symbol, tf, tier, strategy_id, mode, allowed)


@app.get("/api/signals")
async def signals(request: Request, symbol: str | None = None, tf: int | None = None, tier: str | None = None, status: str | None = None,
                  direction: str | None = None, strategy_id: int | None = None, include_skips: bool = False,
                  limit: int = 100, offset: int = 0, sort: str | None = None, order: str = "desc") -> dict:
    """symbol: one pair or several separated by commas (EURUSD_otc,USDJPY_otc). sort: candle | pair | tf | strategy | signal | tier | strength | adx | hour | mode | result | margin | mtg1 | mtg2 (default: newest first); order: asc | desc.
    Every row also carries its martingale outcome (mtg1 / mtg2) and the response a summary over everything matching the filter (`mtg`)."""
    if sort and sort not in engine.SIGNAL_SORTS and sort not in ("mtg1", "mtg2"):
        raise HTTPException(400, f"sort must be one of: {', '.join([*engine.SIGNAL_SORTS, 'mtg1', 'mtg2'])}")
    u = _user(request)
    out = engine.query_signals(symbol, tf, tier, status, direction, strategy_id, include_skips, limit, offset, sort, order, allowed=access.allowed_signal_ids(u))
    for r in out["rows"]:                                      # the reasons behind a signal are the strategy's logic: only its owner (or staff, for shared ones) sees them
        if not access.can_edit(u, r["strategy_id"]):
            r["factors"], r["reason"], r["risk"] = [], "", None
    return out


def _ctx(request: Request):
    """The private trades / guard / style of the signed-in user (a local server: the local admin's, i.e. the main database)."""
    return engine.user(request.state.user["id"])


@app.get("/api/trades")
async def my_trades(request: Request, account: str = "all", days: float = 0, tz: int = 0, limit: int = 200, offset: int = 0,
                    since: float = 0, until: float = 0, sort: str = "", desc: bool = True) -> dict:
    """The trades you placed on the broker (read from the order events the extension sees) with totals, win rate, profit / loss, per day, per pair and
    whether each followed a Tradzen signal. account: all | real | demo. days: only the last N days (0 = everything; fractions allowed, 1 = the last 24 h).
    since / until: an exact range in epoch seconds (the Trades page sends your own midnights: yesterday, last month ...); they win over `days`.
    sort: t | symbol | direction | amount | price | status | profit | source - orders the table of trades only, `desc` picks the direction.
    tz: your UTC offset in minutes, so 'per day' follows your own calendar."""
    if account not in ("all", "real", "demo"):
        raise HTTPException(400, "account must be all, real or demo")
    if sort and sort not in _ctx(request).trades.SORT_KEYS:
        raise HTTPException(400, "sort must be one of " + ", ".join(_ctx(request).trades.SORT_KEYS))
    import time as _t
    lo = since if since > 0 else ((_t.time() - days * 86400) if days > 0 else None)
    return _ctx(request).trades.query(account, lo, tz, limit, offset, engine.names, until=until if until > 0 else None, sort=sort, desc=desc)


@app.get("/api/trades/calendar")
async def my_trades_calendar(request: Request, account: str = "all", tz: int = 0, since: float = 0, until: float = 0) -> dict:
    """The profit / loss calendar: per calendar day (your own days: `tz` = your UTC offset in minutes) the finished trades, wins, losses, staked, net, best / worst trade and
    top pair, plus a summary of the period (green / red days, best / worst day, streaks) and the pairs ranked by net. since / until: epoch seconds (until exclusive)."""
    if account not in ("all", "real", "demo"):
        raise HTTPException(400, "account must be all, real or demo")
    return _ctx(request).trades.calendar(account, since if since > 0 else None, until if until > 0 else None, tz)


class GuardSettings(BaseModel):
    """Any subset of the guard's settings (see app/guard.py DEFAULTS); the server clamps every value."""
    enabled: bool | None = None
    account: str | None = None
    target: dict | None = None
    loss: dict | None = None
    streak: dict | None = None
    cooldown_min: int | None = None
    count_cycles: bool | None = None
    max_trades: dict | None = None
    per_hour: dict | None = None
    after_target: dict | None = None
    stake: dict | None = None
    mtg: dict | None = None
    warn_at: float | None = None
    day_start_hour: int | None = None
    tz_min: int | None = None
    sound: bool | None = None


@app.get("/api/guard")
async def guard_status(request: Request, tz: int | None = None) -> dict:
    """Today's target / loss limit / streak / volume / stake status computed from your real trades, the suggested stake, the last 14 days and the alert history."""
    return _ctx(request).guard.status(tz_min=tz)


@app.put("/api/guard/settings")
async def guard_save(body: GuardSettings, request: Request) -> dict:
    g = _ctx(request).guard
    g.save_settings(body.model_dump(exclude_none=True))
    return g.status()


@app.post("/api/guard/stop-today")
async def guard_stop_today(request: Request) -> dict:
    """'I am done for today': any trade opened afterwards is flagged as overtrading."""
    g = _ctx(request).guard
    g.stop_today()
    return g.status()


@app.post("/api/guard/resume")
async def guard_resume(request: Request) -> dict:
    g = _ctx(request).guard
    g.resume()
    return g.status()


@app.post("/api/guard/test")
async def guard_test(request: Request, level: str = "warn") -> dict:
    """Send a sample guard alert to the Quotex page so you can see and hear what it looks like."""
    samples = {"success": ("Daily target reached ✓", "You are up today and your target is reached. This is the moment to stop and keep the win."),
               "info": ("Close to your daily target", "80% of the way. One more careful trade at most."),
               "warn": ("Approaching your daily loss limit", "80% of the limit used. Reduce the stake or stop."),
               "danger": ("Daily loss limit reached - stop for today", "Trading on after this is how small losses become big ones.")}
    if level not in samples:
        raise HTTPException(400, "level must be success, info, warn or danger")
    t, m = samples[level]
    ev = engine._emit_guard({"level": level, "rule": "test", "title": t, "message": m, "chips": [{"l": "Today", "v": "sample"}], "progress": 0.8, "sound": True}, request.state.user["id"])
    return {"ok": True, "seq": ev["seq"]}


class StyleSettings(BaseModel):
    enabled: bool | None = None
    alert: str | None = None
    min_payout: float | None = None


@app.get("/api/style")
async def style_status(request: Request) -> dict:
    """What your own trades have in common, how well each habit works (with its uncertainty), the live rule learned from them and that rule's measured record."""
    return _ctx(request).style.status()


@app.put("/api/style/settings")
async def style_save(body: StyleSettings, request: Request) -> dict:
    st = _ctx(request).style
    st.save_settings(body.model_dump(exclude_none=True))
    return st.status()


@app.get("/api/account")
async def account(request: Request) -> dict:
    """The broker account's balances (live and demo, with when each was seen) and currency {code, symbol}, as the extension last reported them; null until seen."""
    return _ctx(request).trades.account_info()


@app.get("/api/trades/raw")
async def my_trades_raw(request: Request, limit: int = 60) -> dict:
    """The last raw order events the extension forwarded (whitelisted fields only), newest first: what the parser works from."""
    return {"events": _ctx(request).trades.raw(limit)}


@app.get("/api/trades/{trade_id}/detail")
async def my_trade_detail(trade_id: str, request: Request, before: int = 40, after: int = 8) -> dict:
    """One trade with the candles around it, the position / martingale cycle it belongs to and the signal it followed (the trade window of the Trades page)."""
    trades = _ctx(request).trades
    t = trades.db.execute("SELECT symbol FROM broker_trades WHERE id=?", (trade_id,)).fetchone()
    forming = engine.open.get((t[0], 60)) if t else None
    r = trades.detail(trade_id, engine.names, forming, max(5, min(before, 400)), max(2, min(after, 60)))
    if r is None:
        raise HTTPException(404, "trade not found")
    return r


@app.post("/api/trades/restore")
async def my_trades_restore_all(request: Request) -> dict:
    """Bring back every trade that was removed from the page."""
    return {"ok": True, "restored": _ctx(request).trades.restore()}


@app.post("/api/trades/{trade_id}/restore")
async def my_trades_restore(trade_id: str, request: Request) -> dict:
    if not _ctx(request).trades.restore(trade_id):
        raise HTTPException(404, "that trade is not in the removed list")
    return {"ok": True}


@app.delete("/api/trades/{trade_id}")
async def my_trades_delete(trade_id: str, request: Request) -> dict:
    if not _ctx(request).trades.delete(trade_id):
        raise HTTPException(404, "trade not found")
    return {"ok": True}


@app.get("/api/signal-pairs")
async def signal_pairs(request: Request, strategy_id: int | None = None) -> list[dict]:
    """The pairs a signal filter can pick, with how many signals each has: [{symbol, n, payout}] (most signals first). strategy_id: only that strategy's pairs."""
    allowed = access.allowed_signal_ids(_user(request))
    if strategy_id is not None and strategy_id not in allowed:
        raise HTTPException(404, "strategy not found")
    return engine.signal_pairs(strategy_id, allowed)


@app.get("/api/signals/{signal_id}")
async def signal_detail(signal_id: int, request: Request) -> dict:
    u = _user(request)
    d = engine.signal_detail(signal_id)
    if d is None or not access.receives(u, d["signal"]["strategy_id"]):
        raise HTTPException(404, "signal not found")
    if not access.can_edit(u, d["signal"]["strategy_id"]):                 # the logic behind a signal is the strategy's: hidden from users who only receive it
        d["signal"].update(factors=[], reason="", risk=None)
        d.update(features=None, notes=[])
    return d


@app.post("/api/signals/{signal_id}/notes")
async def add_note(signal_id: int, body: NoteIn) -> dict:
    n = engine.add_note(signal_id, body.tags, body.note)
    if n is None:
        raise HTTPException(404, "signal not found")
    return n


@app.delete("/api/notes/{note_id}")
async def delete_note(note_id: int) -> dict:
    if not engine.delete_note(note_id):
        raise HTTPException(404, "note not found")
    return {"ok": True}


@app.get("/api/data/coverage")
async def coverage() -> list[dict]:
    return engine.coverage()


@app.get("/api/data/plan")
async def data_plan(min_payout: float = 80.0, target_total: int = 100_000, per_pair_target: int = 5_000, tf: int = 60) -> dict:
    """What to collect next: progress toward a data target on well-paying pairs, and per-pair 'open it / scroll back to' instructions."""
    return engine.collection_plan(max(0.0, min(min_payout, 100.0)), max(1000, min(target_total, 5_000_000)), max(100, min(per_pair_target, 200_000)), tf)


@app.get("/api/data/sentiment")
async def data_sentiment(window: int = 600) -> dict:
    """Which pairs we are receiving trader sentiment for right now, and how much of it is stored."""
    return engine.sentiment_coverage(max(60, min(window, 86400)))


@app.get("/api/data/sentiment/study")
async def data_sentiment_study(tf: int = 60, tolerance: int = 30, min_payout: float | None = None) -> dict:
    """P(candle closes up | crowd sentiment known before it opened), by sentiment range, with both halves of the data."""
    return engine.sentiment_study(tf, max(5, min(tolerance, 300)), min_payout)


@app.get("/api/data/market")
async def data_market() -> dict:
    """Real-market (<PAIR>_mkt) candles the AI learns from: source, last refresh, what each pair holds."""
    return marketdata.status()


@app.post("/api/data/market/refresh")
async def data_market_refresh() -> dict:
    """Fetch the missing real-market candles now (runs in the background; poll GET /api/data/market)."""
    return {"started": marketdata.start(), **marketdata.status()}


@app.get("/api/data/gaps")
async def data_gaps(symbol: str, tf: int = 60, limit: int = 200) -> dict:
    return engine.gaps(symbol, tf, max(1, min(limit, 2000)))


class CandleDeleteIn(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=40)            # a pair, or * for every pair (then another filter is required)
    tf: int | None = None
    kind: str | None = None                                          # tick | history | contrib | yahoo | derived | other
    since: int | None = None
    until: int | None = None
    with_derived: bool = True
    dry_run: bool = True                                             # the default only counts
    confirm: str = ""                                                # a real removal needs the pair's name (or DELETE for *) typed here


@app.get("/api/data/inventory")
async def data_inventory() -> list[dict]:
    """Admin: what the candle store holds per pair, timeframe and kind of source (live feed, Quotex history, shared, real-market, derived), with the flat-candle share."""
    return candleadmin.inventory(engine)


@app.get("/api/data/check")
async def data_check(symbol: str, tf: int = 60, kind: str | None = None) -> dict:
    """Admin: one series examined candle by candle (broken shapes, spikes, jumps, flat candles) with the worst examples."""
    try:
        return candleadmin.check(engine, symbol, tf, kind)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/data/delete")
async def data_delete(body: CandleDeleteIn, request: Request) -> dict:
    """Admin: count (dry_run, the default) or remove stored candles. A real removal needs `confirm` = the pair's name (DELETE for pair *), saves what it removes to
    data/deleted/ as a gzip CSV first, and is written to the audit log."""
    if not body.dry_run and body.confirm != (body.symbol if body.symbol != "*" else "DELETE"):
        raise HTTPException(400, "type the pair's name (or DELETE for every pair) in `confirm` to remove candles")
    try:
        res = candleadmin.delete(engine, DB_PATH.parent / "deleted", body.symbol, body.tf, body.kind, body.since, body.until, body.with_derived, body.dry_run)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not body.dry_run:
        auth.audit(request.state.user["username"], "candles-delete", body.symbol,
                   {"tf": body.tf, "kind": body.kind, "since": body.since, "until": body.until, "removed": res.get("removed", 0), "archive": res.get("archive")})
    return res


@app.get("/api/data/deleted/{name}")
async def data_deleted_file(name: str):
    """Admin: download a file written by a removal (the candles it took out)."""
    from fastapi.responses import FileResponse
    folder = (DB_PATH.parent / "deleted").resolve()
    path = (folder / name).resolve()
    if path.parent != folder or not path.is_file() or not name.endswith(".csv.gz"):
        raise HTTPException(404, "no such file")
    return FileResponse(path, filename=name)


@app.get("/api/data/export")
async def data_export(symbol: str | None = None, tf: int | None = None, since: float | None = None, until: float | None = None,
                      fmt: str = "csv"):
    """Download stored candles (all pairs when `symbol` is omitted). CSV columns: symbol,tf,time,iso_time,open,high,low,close,ticks,source."""
    from datetime import datetime, timezone
    from fastapi.responses import StreamingResponse
    if fmt not in ("csv", "json"):
        raise HTTPException(400, "fmt must be csv or json")
    name = f"candles_{symbol or 'all'}_{tf or 'all'}.{fmt}"

    def lines():
        first = True
        if fmt == "csv":
            yield "symbol,tf,time,iso_time,open,high,low,close,ticks,source\n"
        else:
            yield "["
        for sym, tfv, ts, o, h, l, c, n, src in engine.export_rows(symbol, tf, since, until):
            if fmt == "csv":
                iso = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                yield f"{sym},{tfv},{ts},{iso},{o},{h},{l},{c},{n or 0},{src}\n"
            else:
                yield ("" if first else ",") + f'{{"symbol":"{sym}","tf":{tfv},"t":{ts},"o":{o},"h":{h},"l":{l},"c":{c},"n":{n or 0}}}'
                first = False
        if fmt == "json":
            yield "]"

    return StreamingResponse(lines(), media_type="text/csv" if fmt == "csv" else "application/json",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/clear")
async def clear() -> dict:
    engine.clear()
    return {"ok": True}


@app.get("/api/equity")
async def equity(request: Request, strategy_id: int | None = None, mode: str | None = None) -> dict:
    allowed = access.allowed_signal_ids(_user(request))
    if strategy_id is not None and strategy_id not in allowed:
        raise HTTPException(404, "strategy not found")
    return engine.equity(strategy_id, mode, allowed=allowed)


SEED_DIR = DB_PATH.parent / "seed"
SEED_PENDING = SEED_DIR / seedmod.PENDING_NAME
SEED_INFO = SEED_DIR / "pending.json"
MAX_UPLOAD = 4 * 1024 ** 3
_import_state: dict = {"state": "idle", "done": 0, "total": 0, "result": None, "error": None}


def _exit_now() -> None:
    """Stop the process after answering: the container's restart policy starts it again (used after loading a seed)."""
    try:
        engine.close()
    except Exception:                                                  # noqa: BLE001
        pass
    os._exit(0)


_restart_hook = _exit_now


def _seed_counts() -> dict:
    return {t: engine.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("candles", "signals", "strategies", "backtest_runs", "users")}


@app.get("/api/system/seed")
async def seed_status() -> dict:
    """The uploaded seed that is waiting (what is in it), whether the server could be replaced by it, what the server holds now, and the state of a running merge."""
    pending = None
    if SEED_PENDING.is_file():
        try:
            pending = json.loads(SEED_INFO.read_text(encoding="utf-8")) if SEED_INFO.is_file() else None
            if pending is None:
                pending = await asyncio.to_thread(seedmod.inspect_seed, SEED_PENDING)
                SEED_INFO.write_text(json.dumps(pending), encoding="utf-8")
        except (seedmod.SeedError, ValueError) as e:
            pending = {"error": str(e)}
    return {"pending": pending, "fresh": await asyncio.to_thread(seedmod.is_fresh, DB_PATH), "server": _seed_counts(), "merge": dict(_import_state)}


@app.put("/api/system/seed")
async def seed_upload(request: Request) -> dict:
    """Upload a seed (the file made by `python -m app.seed make`) as the raw request body. It is checked, then kept as the pending seed until you merge or load it."""
    try:
        if int(request.headers.get("content-length") or 0) > MAX_UPLOAD:
            raise HTTPException(413, "that file is too large")
    except ValueError:
        raise HTTPException(400, "bad content-length")
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    part = SEED_DIR / "upload.part"
    size = 0
    try:
        with open(part, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "that file is too large")
                f.write(chunk)
        if size == 0:
            raise HTTPException(400, "empty upload")
        info = await asyncio.to_thread(seedmod.inspect_seed, part)
    except seedmod.SeedError as e:
        part.unlink(missing_ok=True)
        raise HTTPException(400, str(e))
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    os.replace(part, SEED_PENDING)
    SEED_INFO.write_text(json.dumps(info), encoding="utf-8")
    auth.audit(request.state.user["username"], "seed-upload", "", {"bytes": size, "candles": info["counts"].get("candles")})
    return info


@app.delete("/api/system/seed")
async def seed_discard() -> dict:
    SEED_PENDING.unlink(missing_ok=True)
    SEED_INFO.unlink(missing_ok=True)
    return {"ok": True}


@app.post("/api/system/seed/merge")
async def seed_merge(request: Request) -> dict:
    """Add the candles, sentiment and instruments of the pending seed to this server (rows it already has are kept; accounts, strategies, signals are never touched)."""
    if not SEED_PENDING.is_file():
        raise HTTPException(400, "upload a seed first")
    if _import_state["state"] == "running":
        raise HTTPException(409, "a merge is already running")
    _import_state.update(state="running", done=0, total=0, result=None, error=None)

    def work() -> None:
        try:
            res = seedmod.merge_market(SEED_PENDING, DB_PATH, lambda i, n, sym, tf: _import_state.update(done=i, total=n))
            _import_state.update(state="done", result=res)
            engine._log("seed-merge", ", ".join(f"{k} {v:,}" for k, v in res.items()))
        except Exception as e:                                         # noqa: BLE001
            _import_state.update(state="error", error=str(e)[:300])

    threading.Thread(target=work, daemon=True, name="seed-merge").start()
    auth.audit(request.state.user["username"], "seed-merge", "")
    return dict(_import_state)


@app.post("/api/system/seed/replace")
async def seed_replace(request: Request) -> dict:
    """Load the pending seed as THE database. Allowed only while nobody but the admin has an account. The old database is kept next to it (signals.db.before-seed-<time>)
    and the server restarts to load the seed (the container's restart policy brings it back)."""
    if not SEED_PENDING.is_file():
        raise HTTPException(400, "upload a seed first")
    if _import_state["state"] == "running":
        raise HTTPException(409, "a merge is running")
    if not await asyncio.to_thread(seedmod.is_fresh, DB_PATH):
        raise HTTPException(409, "this server already has other users: use 'add the candles' instead (replacing would delete their accounts)")
    auth.audit(request.state.user["username"], "seed-replace", "", {})
    asyncio.get_running_loop().call_later(1.5, lambda: _restart_hook())
    return {"restarting": True}


@app.get("/api/system/backups")
async def backups_list() -> dict:
    """Backups on the server's disk (newest first) and whether the nightly one is on."""
    return {"nightly": BACKUP_ON, "hour_utc": BACKUP_HOUR, "keep": BACKUP_KEEP, "backups": backup.list_backups(DB_PATH)}


@app.post("/api/system/backup")
async def backup_now(request: Request) -> dict:
    """Make a backup right now (admin)."""
    try:
        res = await asyncio.to_thread(backup.run_backup, DB_PATH, BACKUP_KEEP)
    except ValueError as e:
        raise HTTPException(400, str(e))
    auth.audit(request.state.user["username"], "backup", res["name"], {"bytes": res["bytes"]})
    return res


@app.get("/api/system")
async def system() -> dict:
    return engine.system_info()


# The pages themselves must be re-checked on every load (a cheap 304 when nothing changed), or a browser - above all the extension's side panel - keeps showing an
# old copy for hours. The admin's JS / CSS files have a new name whenever they change, so those cache normally.
NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}


LANDING_FILE = ROOT / "static" / "landing" / "index.html"

# ---- the extension, packaged for this server (a download button on the landing page and in the admin panel)
EXT_DIR = Path(os.getenv("QXS_EXTENSION_DIR") or (ROOT.parent / "extension"))
_ext_cache: dict[str, bytes] = {}


def _public_origin(request: Request) -> str | None:
    """The https address users reach this server at: QXS_PUBLIC_URL, else https://QXS_DOMAIN, else the address of this very request (when it is https)."""
    for raw in (os.getenv("QXS_PUBLIC_URL", "").strip(), f"https://{os.getenv('QXS_DOMAIN', '').strip()}" if os.getenv("QXS_DOMAIN", "").strip() else ""):
        if raw:
            try:
                return extpack.origin_of(raw)
            except extpack.PackError:
                continue
    try:
        return extpack.origin_of(f"{request.url.scheme}://{request.url.netloc}")
    except extpack.PackError:
        return None


def _extension_zip(request: Request) -> tuple[str, bytes] | None:
    origin = _public_origin(request)
    if not origin or not extpack.available(EXT_DIR):
        return None
    key = origin + "|" + str(extpack.version(EXT_DIR))
    if key not in _ext_cache:
        if len(_ext_cache) >= 8:
            _ext_cache.clear()
        _ext_cache[key] = extpack.build_zip(EXT_DIR, origin)
    return origin, _ext_cache[key]


@app.get("/download/extension.json")
async def extension_info(request: Request) -> dict:
    """Is the extension offered here, and which version (public: the landing page and the admin panel ask)."""
    origin = _public_origin(request)
    return {"available": bool(origin and extpack.available(EXT_DIR)), "version": extpack.version(EXT_DIR), "url": "/download/extension.zip", "server": origin}


@app.get("/download/extension.zip")
async def extension_download(request: Request) -> Response:
    """The Chrome extension for THIS server (it already points at it and may reach only it). Public: it holds no secrets; signing in still needs an account."""
    got = await asyncio.to_thread(_extension_zip, request)
    if got is None:
        raise HTTPException(404, "the extension is not offered here (it needs an https address and the extension files on the server)")
    origin, data = got
    host = origin.split("//", 1)[1].replace(":", "-")
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="tradzen-extension-{host}.zip"', "Cache-Control": "no-cache"})


def _landing_html(request: Request | None = None) -> str:
    """The public marketing page. QXS_CONTACT (a mailto: or https:// link) turns on the "Request access" buttons; accounts are created by the admin, there is no sign-up."""
    import html as _html
    page = LANDING_FILE.read_text(encoding="utf-8")
    ext = request is not None and bool(_public_origin(request)) and extpack.available(EXT_DIR)
    page = page.replace("{{GET_EXTENSION}}", '<a class="btn big" href="/download/extension.zip" download>Download the Chrome extension</a>' if ext else "")
    page = page.replace("{{GET_EXTENSION_NAV}}", '<a href="/download/extension.zip" download>Extension</a>' if ext else "")
    contact = os.getenv("QXS_CONTACT", "").strip()
    if contact.startswith(("mailto:", "https://")):
        href = _html.escape(contact, quote=True)
        return page.replace("{{REQUEST_ACCESS}}", f'<a class="btn big" href="{href}">Request access</a>').replace("{{HOW_TO_ACCESS}}", "Use the Request access button and tell us a little about how you trade.")
    return page.replace("{{REQUEST_ACCESS}}", "").replace("{{HOW_TO_ACCESS}}", "Ask the person who invited you, or whoever runs this site.")


@app.get("/landing")
async def landing(request: Request) -> Response:
    """The marketing page, always reachable (a preview on a local machine too)."""
    return HTMLResponse(_landing_html(request), headers=NO_CACHE)


@app.get("/robots.txt")
async def robots() -> Response:
    lines = ["User-agent: *", "Allow: /$", "Allow: /landing", "Disallow: /admin", "Disallow: /api", "Disallow: /ingest"]
    return Response(chr(10).join(lines) + chr(10), media_type="text/plain")


@app.get("/")
async def index(request: Request) -> Response:
    if auth.enabled:
        cookie = request.cookies.get(COOKIE_NAME)
        if cookie and auth.user_from_session(cookie):               # already signed in: straight to the dashboard
            return RedirectResponse("/admin", status_code=302)
        return HTMLResponse(_landing_html(request), headers=NO_CACHE)       # everybody else sees the marketing page (the classic page has no sign-in)
    return FileResponse(ROOT / "static" / "index.html", headers=NO_CACHE)


# ---- admin panel (React build output in static/admin; run `npm run build` in /admin) ----
ADMIN_DIR = ROOT / "static" / "admin"
(ADMIN_DIR / "assets").mkdir(parents=True, exist_ok=True)          # always mounted, so rebuilding needs no server restart
app.mount("/admin/assets", StaticFiles(directory=ADMIN_DIR / "assets"), name="admin-assets")


@app.get("/admin")
@app.get("/admin/{path:path}")
async def admin(path: str = "") -> Response:
    index_file = ADMIN_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h3>Admin panel not built yet</h3><p>Run <code>npm install &amp;&amp; npm run build</code> in the <code>admin</code> folder.</p>", status_code=503)
    return FileResponse(index_file, headers=NO_CACHE)
