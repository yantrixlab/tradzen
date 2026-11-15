"""The 'why are no signals coming?' report: plain reasons, worst first, each with a fix.
Run from backend/:  .venv\\Scripts\\python tests\\test_feed_health.py"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app.engine import Engine  # noqa: E402


def syms(*items):
    return [{"symbol": s, "name": n, "streaming": st, "warm": w} for s, n, st, w in items]


def codes(h):
    return [r["code"] for r in h["reasons"]]


def test_nothing_received_yet():
    eng = Engine(":memory:")
    h = eng.feed_health(None, [], time.time())
    assert h["paused"] and codes(h)[0] == "no_data" and h["reasons"][0]["level"] == "bad" and "extension" in h["reasons"][0]["fix"]


def test_a_silent_feed_blames_a_hidden_tab_when_that_is_what_happened():
    eng = Engine(":memory:")
    now = time.time()
    eng.ingest_stats["last_recv"] = now - 300
    eng.feed_events.append({"t": now - 290, "e": "visibility", "d": "hidden"})
    h = eng.feed_health(None, syms(("A_otc", "A", False, {})), now)
    r = h["reasons"][0]
    assert h["paused"] and r["code"] == "silent" and "hidden" in r["detail"] and "visible" in r["fix"] and "300" in r["title"]
    eng.feed_events.append({"t": now - 280, "e": "visibility", "d": "visible"})                   # the tab came back: the cause is no longer 'hidden'
    assert "hidden" not in eng.feed_health(None, syms(("A_otc", "A", False, {})), now)["reasons"][0]["detail"].split("closed")[0]


def test_a_gap_explains_the_wait_and_how_to_end_it():
    eng = Engine(":memory:")
    now = time.time()
    eng.ingest_stats["last_recv"] = now - 1
    sel = {"symbol": "USDNGN_otc", "tf": 60, "analysis": {"candles": 19, "need": 60, "bridged": False}, "gap": {"missing": 4, "before": now - 19 * 60}}
    symbols = syms(("USDNGN_otc", "USD/NGN (OTC)", True, {"60": 19}))
    h = eng.feed_health(sel, symbols, now)
    gap = next(r for r in h["reasons"] if r["code"] == "gap")
    assert h["paused"] and gap["level"] == "warn" and gap["have"] == 19 and gap["need"] == 60
    assert "41 more" in gap["title"] and "4 candle(s) are missing" in gap["detail"] and "F5" in gap["fix"]
    assert "one_pair" in codes(h)                                                                 # only one pair streams: a note, not a pause reason


def test_a_healthy_feed_is_not_paused():
    eng = Engine(":memory:")
    now = time.time()
    eng.ingest_stats["last_recv"] = now - 1
    sel = {"symbol": "A_otc", "tf": 60, "analysis": {"candles": 200, "need": 60, "bridged": False}, "gap": None}
    h = eng.feed_health(sel, syms(("A_otc", "A", True, {"60": 200}), ("B_otc", "B", True, {"60": 200})), now)
    assert not h["paused"] and not [r for r in h["reasons"] if r["level"] in ("bad", "warn")]
    eng.alerts_enabled = False
    assert "alerts_off" in codes(eng.feed_health(sel, syms(("A_otc", "A", True, {"60": 200}), ("B_otc", "B", True, {"60": 200})), now))


def test_a_bridged_gap_and_cold_neighbours_are_notes():
    eng = Engine(":memory:")
    now = time.time()
    eng.ingest_stats["last_recv"] = now - 1
    sel = {"symbol": "A_otc", "tf": 60, "analysis": {"candles": 120, "need": 60, "bridged": True}, "gap": None}
    h = eng.feed_health(sel, syms(("A_otc", "A", True, {"60": 120}), ("B_otc", "B", True, {"60": 12})), now)
    assert {"bridged", "others_warming"} <= set(codes(h)) and not h["paused"]


def test_state_carries_the_health_and_the_live_strategies_newest_signals():
    eng = Engine(":memory:", seed_builtins=True)
    st = eng.state()
    assert "health" in st and "recent_live" in st and st["health"]["paused"] and st["health"]["reasons"][0]["code"] == "no_data"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
