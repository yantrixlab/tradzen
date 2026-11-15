"""Collection plan: counts only well-paying pairs, orders the work sensibly, tells you how far to scroll back.
Run from backend/:  .venv\\Scripts\\python tests\\test_plan.py"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from app.engine import Engine  # noqa: E402
from test_backtest import put_candles, streak_series  # noqa: E402


def inst(sym, payout, open_=True):
    return {"symbol": sym, "name": sym, "payout": payout, "open": open_, "market": "OTC" if sym.endswith("_otc") else "LIVE", "category": ""}


def make():
    eng = Engine(":memory:", seed_builtins=False)
    for sym, n in (("BIG_otc", 3000), ("SMALL_otc", 400), ("CHEAP_otc", 2000), ("DONE_otc", 5200), ("SHUT", 1000)):
        put_candles(eng, sym, streak_series(n, hash(sym) % 1000, 0.5))
    for sym, pay, op in (("BIG_otc", 90, True), ("SMALL_otc", 85, True), ("CHEAP_otc", 60, True), ("DONE_otc", 88, True), ("SHUT", 92, False), ("EMPTY_otc", 91, True)):
        eng.instruments[sym] = inst(sym, pay, op)
    eng.last_tick["SMALL_otc"] = {"price": 1.0, "recv": time.time(), "ts": time.time()}
    return eng


def test_counts_only_pairs_paying_enough():
    p = make().collection_plan(min_payout=80, target_total=10_000, per_pair_target=5_000)
    names = [x["symbol"] for x in p["pairs"]]
    assert "CHEAP_otc" not in names                                         # 60 % payout: never counted
    assert p["have_total"] == 3000 + 400 + 5200 + 1000 and p["pairs_total"] == 5
    assert p["pairs_done"] == 1 and abs(p["progress"] - 0.96) < 1e-9
    assert make().collection_plan(min_payout=0, target_total=10_000)["have_total"] == 3000 + 400 + 2000 + 5200 + 1000


def test_order_and_instructions():
    p = make().collection_plan(min_payout=80, target_total=100_000, per_pair_target=5_000)
    names = [x["symbol"] for x in p["pairs"]]
    assert names == ["BIG_otc", "SMALL_otc", "EMPTY_otc", "DONE_otc", "SHUT"]  # tradable first, unfinished before done, most-started first, closed last
    big = p["pairs"][0]
    assert big["need"] == 2000 and big["scroll_to"] == big["last"] - 5000 * 60 and not big["done"]
    empty = next(x for x in p["pairs"] if x["symbol"] == "EMPTY_otc")
    assert empty["candles"] == 0 and empty["need"] == 5000 and empty["scroll_to"] is None and empty["last"] is None
    done = next(x for x in p["pairs"] if x["symbol"] == "DONE_otc")
    assert done["done"] and done["need"] == 0 and done["scroll_to"] is None


def test_live_rate_and_eta():
    p = make().collection_plan(min_payout=80, target_total=10_000, per_pair_target=5_000)
    assert p["streaming_pairs"] == 1 and p["live_rate_per_hour"] == 60
    assert p["eta_hours_live_only"] == round((10_000 - p["have_total"]) / 60, 1)
    done = make().collection_plan(min_payout=80, target_total=1000, per_pair_target=100)
    assert done["progress"] == 1.0 and done["eta_hours_live_only"] == 0.0
    idle = Engine(":memory:", seed_builtins=False).collection_plan()
    assert idle["pairs"] == [] and idle["have_total"] == 0 and idle["eta_hours_live_only"] is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
