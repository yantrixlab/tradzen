"""The running-jobs list behind the menu's "running" indicator: who sees which job, and that "stop" really stops it.
Run from backend/:  .venv\\Scripts\\python tests\\test_jobs.py"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import jobs  # noqa: E402
from app.backtest import BacktestError  # noqa: E402
from app.engine import Engine  # noqa: E402

ADMIN = {"id": 1, "role": "admin", "username": "root"}
EDITOR = {"id": 2, "role": "editor", "username": "ed"}
TRADER = {"id": 3, "role": "trader", "username": "tr"}
OTHER = {"id": 4, "role": "trader", "username": "tr2"}


def sleeper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])


def add_backtest(eng: Engine, owner: int | None, label: str = "") -> int:
    cur = eng.db.execute("INSERT INTO backtest_runs (created, label, strategy_id, strategy_version, spec, config, status, progress, n_trades, owner_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (time.time(), label, None, None, "{}", "{}", "running", 0.25, 0, owner))
    eng.db.commit()
    return cur.lastrowid


def add_sweep(eng: Engine) -> int:
    cur = eng.db.execute("INSERT INTO sweep_runs (created, label, config, status, progress, n_candidates) VALUES (?,?,?,?,?,?)", (time.time(), "wide", "{}", "running", 0.5, 4))
    eng.db.commit()
    return cur.lastrowid


def keys(rows: list[dict]) -> set[str]:
    return {j["key"] for j in rows}


def test_nothing_running_is_an_empty_list():
    eng = Engine(":memory:", seed_builtins=False)
    assert jobs.snapshot(eng, None, ADMIN) == []


def test_each_role_sees_only_its_own_jobs():
    eng = Engine(":memory:", seed_builtins=False)
    mine = add_backtest(eng, TRADER["id"], "mine")
    theirs = add_backtest(eng, OTHER["id"])
    sweep = add_sweep(eng)
    eng.ai.state.update(running=True, progress=0.4, started=time.time() - 30)
    assert keys(jobs.snapshot(eng, None, ADMIN)) == {f"backtest:{mine}", f"backtest:{theirs}", f"sweep:{sweep}", "ai:train"}
    assert keys(jobs.snapshot(eng, None, TRADER)) == {f"backtest:{mine}"}, "a trader sees its own backtest, nothing of the server's"
    assert keys(jobs.snapshot(eng, None, OTHER)) == {f"backtest:{theirs}"}
    assert keys(jobs.snapshot(eng, None, EDITOR)) == {f"sweep:{sweep}"}, "staff see the sweeps (and own backtests), not the AI or the research"
    ai = next(j for j in jobs.snapshot(eng, None, ADMIN) if j["kind"] == "ai")
    assert ai["progress"] == 0.4 and ai["can_stop"] and ai["page"] == "/ai" and ai["title"]


def test_a_backtest_is_asked_to_stop_and_a_stranger_cannot_stop_it():
    eng = Engine(":memory:", seed_builtins=False)
    rid = add_backtest(eng, TRADER["id"])
    try:
        jobs.stop(eng, "backtest", str(rid), OTHER)
        raise AssertionError("another trader must not stop it")
    except LookupError:
        pass
    assert rid not in eng.backtests._cancel
    assert jobs.stop(eng, "backtest", str(rid), TRADER) == "asked to stop"
    assert rid in eng.backtests._cancel
    assert next(j for j in jobs.snapshot(eng, None, TRADER))["detail"] == "stopping…"


def test_a_server_job_cannot_be_stopped_by_a_trader():
    eng = Engine(":memory:", seed_builtins=False)
    eng.ai.state.update(running=True, progress=0.1, started=time.time())
    eng.ai._proc = sleeper()
    try:
        try:
            jobs.stop(eng, "ai", "train", TRADER)
            raise AssertionError("a trader must not stop the AI training")
        except LookupError:
            pass
        assert eng.ai._proc.poll() is None
        assert jobs.stop(eng, "ai", "train", ADMIN) == "stopped"
        assert eng.ai.state["running"] is False and "stopped" in eng.ai.state["error"]
        assert jobs.snapshot(eng, None, ADMIN) == []
    finally:
        if eng.ai._proc is not None:
            eng.ai._proc.kill()


def test_risk_analysis_and_edge_lab_stop_with_their_queue():
    eng = Engine(":memory:", seed_builtins=False)
    eng.risk._job = {"running": True, "strategy_id": None, "progress": 0.3, "error": None, "started": time.time()}
    eng.risk._queue = [7, 8]
    eng.risk._proc = sleeper()
    eng.edgelab._job = {"running": True, "stage": "Searching", "progress": 0.2, "error": None, "started": time.time(), "run_id": "20260101-000000"}
    eng.edgelab._proc = sleeper()
    procs = [eng.risk._proc, eng.edgelab._proc]
    try:
        got = jobs.snapshot(eng, None, ADMIN)
        assert keys(got) == {"risk:None", "edgelab:20260101-000000"}, keys(got)
        assert next(j for j in got if j["kind"] == "risk")["detail"] == "2 more waiting"
        assert next(j for j in got if j["kind"] == "edgelab")["detail"] == "Searching"
        jobs.stop(eng, "risk", "None", ADMIN)
        jobs.stop(eng, "edgelab", "20260101-000000", ADMIN)
        assert eng.risk._queue == [] and not eng.risk._job["running"] and not eng.edgelab._job["running"]
        assert all(p.poll() is not None for p in procs), "both processes are gone"
        assert jobs.snapshot(eng, None, ADMIN) == []
    finally:
        for p in procs:
            p.kill()


def test_a_research_check_stops_and_is_recorded():
    with tempfile.TemporaryDirectory() as d:
        eng = Engine(Path(d) / "j.db", seed_builtins=False)
        eng.research._command = lambda run_id, cfg: [sys.executable, "-c", "import time; time.sleep(120)"]
        rid = eng.research.start({"windows": [3]}, source="manual")
        proc = eng.research._proc
        assert keys(jobs.snapshot(eng, None, ADMIN)) == {f"research:{rid}"}
        assert jobs.stop(eng, "research", str(rid), ADMIN) == "stopped"
        assert proc.poll() is not None
        row = eng.db.execute("SELECT status, error FROM research_runs WHERE id=?", (rid,)).fetchone()
        assert row[0] == "error" and "stopped by an administrator" in row[1], row
        assert jobs.snapshot(eng, None, ADMIN) == []
        eng.db.close()


def test_what_cannot_be_stopped_is_refused():
    eng = Engine(":memory:", seed_builtins=False)

    class MD:
        def status(self):
            return {"running": True, "progress": 0.5}
    got = jobs.snapshot(eng, MD(), ADMIN)
    assert [(j["kind"], j["can_stop"]) for j in got] == [("marketdata", False)]
    try:
        jobs.stop(eng, "marketdata", "refresh", ADMIN)
        raise AssertionError("expected a refusal")
    except (LookupError, BacktestError):
        pass                                                    # stop() builds its list without the refresh (it is not stoppable), so it reports "not running"


def test_api():
    import os
    from fastapi.testclient import TestClient
    with tempfile.TemporaryDirectory() as d:
        os.environ["QXS_DB"] = str(Path(d) / "jobs_api.db")
        sys.modules.pop("app.main", None)
        import app.main as main
        with TestClient(main.app) as c:
            r = c.get("/api/jobs")
            assert r.status_code == 200 and r.json()["jobs"] == [], r.text
            rid = add_backtest(main.engine, None, "api")
            got = c.get("/api/jobs").json()["jobs"]
            assert [j["key"] for j in got] == [f"backtest:{rid}"]
            assert c.post("/api/jobs/backtest/99999/stop").status_code == 404
            s = c.post(f"/api/jobs/backtest/{rid}/stop")
            assert s.status_code == 200 and s.json()["result"] == "asked to stop"
            assert c.post("/api/jobs/nothing/1/stop").status_code == 404


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
