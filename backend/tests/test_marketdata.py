"""Real-market candles: parsing, idempotent storage under <PAIR>_mkt, incremental refresh, and which pairs the AI may learn from."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import marketdata  # noqa: E402
from app.db import open_db  # noqa: E402

NOW = 1_794_400_020
T0 = NOW // 60 * 60 - 3 * 86400


def doc(ts, price=1.1):
    n = len(ts)
    q = {"open": [price] * n, "high": [price + 0.0002] * n, "low": [price - 0.0002] * n, "close": [price + 0.0001] * n, "volume": [0] * n}
    return {"chart": {"result": [{"timestamp": ts, "indicators": {"quote": [q]}}], "error": None}}


def test_parse_keeps_only_whole_candles_inside_the_window():
    ts = [T0, T0 + 60, T0 + 61, NOW + 5000]
    d = doc(ts)
    d["chart"]["result"][0]["indicators"]["quote"][0]["close"][1] = None          # a candle without a price is dropped
    rows = marketdata.parse_chart(d, T0, NOW + 60)
    assert [r[0] for r in rows] == [T0]                                            # :61 is off the minute, the last one is outside the window


def test_parse_reports_the_source_error():
    try:
        marketdata.parse_chart({"chart": {"result": None, "error": {"description": "No data found"}}}, 0, 1)
    except RuntimeError as e:
        assert "No data found" in str(e)
    else:
        raise AssertionError("expected an error")


def test_collect_stores_under_mkt_name_and_is_idempotent_and_incremental():
    conn = open_db(Path(tempfile.mkdtemp()) / "t.db")
    calls = []

    def fetch(pair, p1, p2):
        calls.append((pair, p1, p2))
        return marketdata.parse_chart(doc(list(range(p1 - p1 % 60 + 60, min(p2, NOW) - 60, 60))), p1, p2)

    rep = marketdata.collect(conn, pairs=("EURUSD", "USDJPY"), now=NOW, fetch=fetch, pause=0)
    assert rep["added"] > 1000 and not rep["errors"]
    syms = {s for (s,) in conn.execute("SELECT DISTINCT symbol FROM candles")}
    assert syms == {"EURUSD_mkt", "USDJPY_mkt"}
    assert {src for (src,) in conn.execute("SELECT DISTINCT source FROM candles")} == {"yahoo"}
    n1 = conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
    first_calls = len(calls)
    assert marketdata.collect(conn, pairs=("EURUSD",), now=NOW, fetch=fetch, pause=0)["added"] == 0     # nothing new
    assert conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == n1
    assert len(calls) - first_calls == 1                                                              # only the newest window is asked for again


def test_one_failing_pair_does_not_stop_the_others():
    conn = open_db(Path(tempfile.mkdtemp()) / "t.db")

    def fetch(pair, p1, p2):
        if pair == "EURUSD":
            raise RuntimeError("boom")
        return marketdata.parse_chart(doc([p1 - p1 % 60 + 60]), p1, p2)

    rep = marketdata.collect(conn, pairs=("EURUSD", "USDJPY"), now=NOW, fetch=fetch, pause=0)
    assert rep["errors"] == ["EURUSD: boom"] and rep["pairs"]["USDJPY"]["added"] >= 1


def test_which_pairs_the_ai_may_learn_from():
    ok = marketdata.payout_ok
    assert ok("EURUSD_mkt", {}, 80, unknown=0.0)                  # market data: always
    assert ok("EURUSD", {"EURUSD": 20}, 80)                       # a closed live market shows 20 %: that is a placeholder, not a payout
    assert not ok("EURUSD", {"EURUSD": 60}, 80)                   # a real, low payout still excludes the pair
    assert ok("AUDCAD_otc", {"AUDCAD_otc": 91}, 80)
    assert not ok("OLD_otc", {}, 80, unknown=0.0)                 # unknown pair: left out where that was the rule before


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
