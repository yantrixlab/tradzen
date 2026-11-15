"""Operator controls: master alert switch, bulk status, pause/resume all, presets, signal board, API.
Run from backend/:  .venv\\Scripts\\python tests\\test_control.py"""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from app import control  # noqa: E402
from app.engine import Engine  # noqa: E402
from test_runner import SIMPLE, SYM, session  # noqa: E402


def statuses(eng):
    return {s["key"]: s["status"] for s in eng.repo.list(include_archived=True)}


def test_bulk_status_subset_all_and_protected_states():
    eng = Engine(":memory:", seed_builtins=True)
    keys = {s["key"]: s["id"] for s in eng.repo.list()}
    draft = eng.create_strategy("my-draft", "Draft", "", SIMPLE, "draft")
    r = control.bulk_status(eng, [keys["macd-cross"], keys["rsi-extremes"]], "live")
    assert r["changed"] == 2 and statuses(eng)["macd-cross"] == "live" and statuses(eng)["bollinger-reversion"] == "paper"
    assert control.bulk_status(eng, [keys["macd-cross"]], "live")["changed"] == 0, "no-op changes are not counted"
    r = control.bulk_status(eng, None, "paused")
    assert r["changed"] >= 16 and all(v == "paused" for k, v in statuses(eng).items() if k != "my-draft")
    assert statuses(eng)["my-draft"] == "draft", "drafts are never touched by bulk actions"
    assert not eng.strategies, "nothing runs when everything is paused"
    for bad in ("draft", "archived", "nonsense"):
        try:
            control.bulk_status(eng, None, bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    assert draft


def test_pause_all_and_resume_all_restore_exactly():
    eng = Engine(":memory:", seed_builtins=True)
    keys = {s["key"]: s["id"] for s in eng.repo.list()}
    control.bulk_status(eng, [keys["macd-cross"]], "live")
    control.bulk_status(eng, [keys["supertrend-flip"]], "paused")          # was already paused: must stay paused after resume
    before = statuses(eng)
    r = control.pause_all(eng)
    assert r["can_resume"] and not eng.strategies and control.settings_view(eng)["can_resume"]
    control.pause_all(eng)                                                  # a second pause must not overwrite the snapshot
    r = control.resume_all(eng)
    assert r["restored"] >= 15
    assert statuses(eng) == before, "resume must restore every strategy to its previous mode"
    assert eng.strategies and not control.settings_view(eng)["can_resume"]
    try:
        control.resume_all(eng)
    except ValueError:
        pass
    else:
        raise AssertionError("resume without snapshot must fail")


def test_presets_builtin_and_custom():
    eng = Engine(":memory:", seed_builtins=True)
    keys = {s["key"]: s["id"] for s in eng.repo.list()}
    control.apply_preset(eng, "off")
    assert set(statuses(eng).values()) == {"paused"}
    control.apply_preset(eng, "standard")
    st = statuses(eng)
    assert st["confluence-5"] == "live" and all(v == "paper" for k, v in st.items() if k != "confluence-5")
    control.apply_preset(eng, "observe")
    assert set(statuses(eng).values()) == {"paper"}
    control.bulk_status(eng, [keys["macd-cross"], keys["rsi-extremes"]], "live")
    mine = statuses(eng)
    assert control.save_preset(eng, "Trend pair")["count"] >= 16
    control.apply_preset(eng, "off")
    control.apply_preset(eng, "Trend pair")
    assert statuses(eng) == mine
    assert [p["name"] for p in control.list_presets(eng)["custom"]] == ["Trend pair"]
    for bad in ("", "standard", "off", "x" * 60, "bad/name"):
        try:
            control.save_preset(eng, bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    try:
        control.apply_preset(eng, "missing")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown preset accepted")
    assert control.delete_preset(eng, "Trend pair") and not control.delete_preset(eng, "Trend pair")


def test_master_alert_switch_suppresses_alerts_but_not_recording():
    eng = Engine(":memory:", seed_builtins=False)
    control.set_alerts_enabled(eng, False)
    session(eng, minutes=300)
    issued = [s for s in eng.signals if s["direction"] != "SKIP"]
    assert issued and not [e for e in eng.notify_log if e["kind"] == "signal"], "no alerts while the master switch is off"
    assert all(s["status"] in ("win", "loss", "draw", "pending", "void") for s in issued), "signals are still recorded and settled"
    control.set_alerts_enabled(eng, True)
    n_before = len(eng.notify_log)
    session(eng, minutes=600, hist=False, t_start=1_700_000_040 + 300 * 60, seed=9)
    assert eng.state()["alerts_enabled"] is True
    assert len(eng.notify_log) >= n_before


def test_settings_survive_a_restart():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "s.db"
        a = Engine(path, seed_builtins=False)
        control.set_alerts_enabled(a, False)
        control.set_setting(a.db, "custom", {"x": 1})
        a.close()
        b = Engine(path, seed_builtins=False)
        assert b.alerts_enabled is False and control.get_setting(b.db, "custom") == {"x": 1}
        b.close()


def test_signal_board_lists_every_running_strategy():
    eng = Engine(":memory:", seed_builtins=True)
    session(eng, minutes=120)
    board = control.analyze_all(eng, SYM, 60)
    assert board["candles"] > 200 and len(board["strategies"]) >= 14
    assert board["calls"] + board["puts"] + board["neutral"] == len(board["strategies"])
    names = {x["name"] for x in board["strategies"]}
    assert "Confluence 5-Factor" in names and "Consensus (3 strategies agree)" in names
    dirs = [x["direction"] for x in board["strategies"]]
    assert dirs == sorted(dirs, key=lambda d: d == "SKIP"), "active opinions are listed first"
    # every row explains itself: factor votes with weights and notes (the 5 Confluence factors, or the single rule of a one-factor strategy)
    by_name = {x["name"]: x for x in board["strategies"]}
    conf = by_name["Confluence 5-Factor"]
    assert [f["key"] for f in conf["factors"]] == ["bollinger", "rsi_divergence", "stochastic", "cci", "candlestick"]
    assert all({"name", "weight", "vote", "note"} <= set(f) for f in conf["factors"]) and sum(f["weight"] for f in conf["factors"]) == 100
    macd = by_name["MACD Histogram Cross"]
    assert len(macd["factors"]) == 1 and macd["factors"][0]["weight"] == 100 and macd["status"] == "paper" and conf["status"] == "live"
    # cached per closed candle: identical call returns the very same object; a mode change or a new candle invalidates it
    assert control.analyze_all(eng, SYM, 60) is board
    eng.repo.set_status(eng.strategy_id, "paper")
    eng.reload_strategies()
    after_mode = control.analyze_all(eng, SYM, 60)
    assert after_mode is not board and {x["name"]: x for x in after_mode["strategies"]}["Confluence 5-Factor"]["status"] == "paper"
    session(eng, minutes=130, hist=False, t_start=1_700_000_040 + 120 * 60, seed=3)       # more candles close
    assert control.analyze_all(eng, SYM, 60) is not after_mode
    control.bulk_status(eng, None, "paused")
    assert control.analyze_all(eng, SYM, 60)["strategies"] == [], "paused strategies are not on the board"


def test_api_roundtrip():
    from fastapi.testclient import TestClient
    with tempfile.TemporaryDirectory() as d:
        os.environ["QXS_DB"] = str(Path(d) / "api3.db")
        sys.modules.pop("app.main", None)
        import app.main as main
        with TestClient(main.app) as c:
            assert c.get("/api/settings").json() == {"alerts_enabled": True, "can_resume": False}
            assert c.post("/api/settings", json={"alerts_enabled": False}).json()["alerts_enabled"] is False
            assert c.get("/api/state").json()["alerts_enabled"] is False
            assert c.post("/api/settings", json={"alerts_enabled": True}).json()["alerts_enabled"] is True
            lst = c.get("/api/strategies").json()
            ids = [s["id"] for s in lst if s["key"] in ("macd-cross", "rsi-extremes")]
            assert c.post("/api/strategies/bulk", json={"ids": ids, "status": "live"}).json()["changed"] == 2
            assert c.post("/api/strategies/bulk", json={"status": "bogus"}).status_code == 400
            assert c.post("/api/strategies/pause-all").json()["can_resume"] is True
            assert all(s["status"] == "paused" for s in c.get("/api/strategies").json())
            assert c.post("/api/strategies/resume-all").json()["restored"] >= 15
            live_keys = {s["key"] for s in c.get("/api/strategies").json() if s["status"] == "live"}
            assert live_keys == {"confluence-5", "macd-cross", "rsi-extremes"}
            assert c.post("/api/strategies/resume-all").status_code == 400
            assert c.post("/api/presets", json={"name": "mine"}).json()["name"] == "mine"
            assert c.post("/api/presets/apply", json={"name": "off"}).json()["changed"] >= 3
            assert c.post("/api/presets/apply", json={"name": "mine"}).json()["changed"] >= 3
            assert c.post("/api/presets/apply", json={"name": "nope"}).status_code == 400
            assert [p["name"] for p in c.get("/api/presets").json()["custom"]] == ["mine"]
            assert c.delete("/api/presets/mine").status_code == 200 and c.delete("/api/presets/mine").status_code == 404
            b = c.get("/api/analyze/all", params={"symbol": "NOPE_otc", "tf": 60}).json()
            assert b["candles"] == 0 and isinstance(b["strategies"], list)
        del os.environ["QXS_DB"]
        sys.modules.pop("app.main", None)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all control tests passed")
