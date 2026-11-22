"""The crypto markets from Binance: parsing, history, live candles into the chart, and - the part that matters most - that a shown-only market never reaches a strategy, a signal,
the AI or the research. Run from backend/:  .venv\\Scripts\\python tests\\test_cryptofeed.py"""
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import cryptofeed as cf  # noqa: E402
from app import marketdata  # noqa: E402
from app.candles import is_display_only  # noqa: E402
from app.engine import Engine  # noqa: E402

NOW = (int(time.time()) // 900) * 900 + 5         # a moment just after a 15-minute boundary
BTC = "BTCUSDT_bn"


def make() -> tuple[Engine, cf.CryptoFeed]:
    e = Engine(Path(tempfile.mkdtemp()) / "c.db")
    f = cf.CryptoFeed(e, pairs=(("BTCUSDT", "Bitcoin"), ("ETHUSDT", "Ethereum")), pause=0)
    f.register()
    return e, f


def rest(t, o=100.0, h=101.0, l=99.0, c=100.5, n=50):
    """A row of /api/v3/klines (strings, like Binance sends them)."""
    return [t * 1000, str(o), str(h), str(l), str(c), "1.5", t * 1000 + 59999, "150", n, "0.7", "70", "0"]


def ws(pair, i, t, o, h, l, c, closed, ev=None, n=10):
    return {"stream": f"{pair.lower()}@kline_{i}", "data": {"e": "kline", "E": int((ev or t + 1) * 1000), "s": pair, "k": {"t": t * 1000, "T": t * 1000 + 59999, "s": pair, "i": i,
            "o": str(o), "c": str(c), "h": str(h), "l": str(l), "n": n, "x": closed}}}


def stored(e: Engine, sym=BTC, tf=60) -> list[tuple]:
    e._flush(force=True)
    return e.db.execute("SELECT ts, o, h, l, c, ticks, source FROM candles WHERE symbol=? AND tf=? ORDER BY ts", (sym, tf)).fetchall()


def test_binance_rows_are_parsed_and_junk_is_ignored():
    r = cf.parse_rest_kline(rest(NOW - 120, o=100, h=99, l=101, c=100.5))
    assert r["t"] == NOW - 120 and r["o"] == 100.0 and r["c"] == 100.5 and r["n"] == 50 and r["close_ms"] == (NOW - 120) * 1000 + 59999
    assert r["h"] == 101.0 and r["l"] == 99.0 or (r["h"] >= max(r["o"], r["c"]) and r["l"] <= min(r["o"], r["c"])), "high / low always contain the body"
    assert cf.parse_rest_kline(["x"]) is None and cf.parse_rest_kline(rest(NOW, o="nan")) is None and cf.parse_rest_kline(rest(NOW, o=0)) is None
    p = cf.parse_ws(ws("BTCUSDT", "5m", NOW, 1, 2, 0.5, 1.5, True, ev=NOW + 300))
    assert p[0] == "BTCUSDT" and p[1] == 300 and p[2]["o"] == 1.0 and p[3] is True and p[4] == NOW + 300
    assert cf.parse_ws(ws("BTCUSDT", "1h", NOW, 1, 2, 0.5, 1.5, False)) is None, "a timeframe we do not follow"
    assert cf.parse_ws({"data": {"e": "trade"}}) is None and cf.parse_ws("junk") is None and cf.parse_ws({}) is None


def test_history_resumes_from_what_is_stored():
    assert cf.start_for(None, 60, NOW) is None, "nothing stored: the newest candles"
    assert cf.start_for(NOW - 600, 60, NOW) == (NOW - 600) * 1000, "resume at the newest stored candle"
    assert cf.start_for(NOW - 100 * 86400, 60, NOW) is None, "a hole longer than one request: just the newest ones"


def test_history_is_stored_exactly_and_the_forming_candle_is_left_to_the_stream():
    e, f = make()
    forming = NOW - 60
    rows = [cf.parse_rest_kline(rest(NOW - 600 + 60 * i, o=100 + i, h=102 + i, l=99 + i, c=101 + i, n=40 + i)) for i in range(5)]
    rows.append({**cf.parse_rest_kline(rest(forming)), "close_ms": int((NOW + 30) * 1000)})          # its close time is still ahead
    n = f.apply_history({("BTCUSDT", 60): rows}, NOW)
    db = stored(e)
    assert n == 5 and len(db) == 5 and db[0][:5] == (NOW - 600, 100.0, 102.0, 99.0, 101.0) and db[0][5] == 40 and db[0][6] == "binance"
    assert all(r[0] != forming for r in db), "the candle still forming is not stored as if it were finished"
    assert e._series(BTC, 60).tail(1)[0]["t"] == NOW - 360, "the chart's series has them too"
    f.apply_history({("BTCUSDT", 60): rows}, NOW)
    assert len(stored(e)) == 5, "fetching the same candles again changes nothing"


def test_live_candles_reach_the_chart_and_the_pair_list():
    e, f = make()
    t = NOW - 30
    assert f.handle(ws("BTCUSDT", "1m", t - t % 60, 100, 103, 99, 102, False, ev=NOW)) is True
    sel = e.chart_of(BTC, 60)
    assert sel["forming"] == {"t": t - t % 60, "o": 100.0, "h": 103.0, "l": 99.0, "c": 102.0, "n": 10}, "the forming candle is Binance's own, not built from ticks"
    assert sel["last_price"] == 102.0
    f.handle(ws("BTCUSDT", "5m", NOW - NOW % 300, 98, 104, 97, 102, False, ev=NOW))
    assert e.chart_of(BTC, 300)["forming"]["h"] == 104.0, "every timeframe has its own forming candle"
    f.handle(ws("BTCUSDT", "1m", t - t % 60, 100, 103, 99, 102.5, True, ev=NOW))
    db = stored(e)
    assert db == [(t - t % 60, 100.0, 103.0, 99.0, 102.5, 10, "binance")] and e.chart_of(BTC, 60)["forming"] is None, "a closed candle is stored and no longer forming"
    assert e.chart_of(BTC, 60)["candles"][-1]["c"] == 102.5
    assert f.handle(ws("XRPUSDT", "1m", t, 1, 1, 1, 1, False)) is False, "a pair we do not follow"
    pl = {p["symbol"]: p for p in e.pair_list()}
    assert pl[BTC]["market"] == "CRYPTO" and pl[BTC]["category"] == "cryptocurrency" and pl[BTC]["streaming"] is True and pl[BTC]["name"] == "Bitcoin (BTC/USDT)"
    assert e.db.execute("SELECT market FROM instruments WHERE symbol=?", (BTC,)).fetchone()[0] == "CRYPTO", "kept across a restart"


def test_a_shown_only_market_never_reaches_a_strategy_a_signal_or_the_learning_jobs():
    e, f = make()
    assert is_display_only(BTC) and not is_display_only("EURUSD_otc") and not is_display_only("EURUSD_mkt") and not is_display_only(None)
    for d in e.strategies.values():
        assert e._in_scope(d["spec"], BTC) is False, "no strategy runs on it, whatever its scope says"
    for i in range(300):                                                       # 300 closed candles of a clear trend: a strategy that ran would find something to say
        px = 100 + i * 0.5
        f.engine.feed_display(BTC, 60, {"t": NOW - 60 * (300 - i), "o": px, "h": px + 1, "l": px - 0.2, "c": px + 0.8, "n": 5}, closed=True)
    e._run_strategies(BTC, 60, {"t": NOW - 60, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "n": 5})
    assert not [s for s in e.signals if s["symbol"] == BTC], "no signal, alert or settlement comes from it"
    assert marketdata.payout_ok(BTC, {}, 0, unknown=100.0) is False, "the AI, the Edge Lab and the momentum job leave it out"
    assert marketdata.payout_ok("EURUSD_mkt", {}, 0) is True
    e.ingest_stats["last_recv"] = time.time()                                   # the Quotex feed is alive, but no Quotex pair is streaming; only crypto is
    codes = [r["code"] for r in e.feed_health(None, [{"symbol": BTC, "streaming": True, "name": "Bitcoin"}], time.time())["reasons"]]
    assert "no_pair" in codes, "a streaming crypto pair must not hide that no Quotex pair is streaming"


class FakeWS:
    def __init__(self, msgs):
        self.msgs, self.closed = list(msgs), False

    async def recv(self):
        if self.msgs:
            return json.dumps(self.msgs.pop(0))
        await asyncio.sleep(30)

    async def close(self):
        self.closed = True


def test_the_feed_loads_history_then_streams_and_reports_its_state():
    e, f = make()
    f.fetch = lambda pair, tf, start: [cf.parse_rest_kline(rest((NOW // tf) * tf - tf * (3 - i), n=20 + i)) for i in range(3)]
    sock = FakeWS([ws("BTCUSDT", "1m", NOW - NOW % 60, 100, 105, 99, 104, False, ev=NOW), ws("ETHUSDT", "1m", NOW - NOW % 60, 10, 11, 9, 10.5, False, ev=NOW)])
    urls = []

    async def connect(url):
        urls.append(url)
        return sock
    f._connect = connect

    async def go():
        task = asyncio.create_task(f.run())
        for _ in range(100):
            await asyncio.sleep(0.05)
            if f.state["messages"] >= 2:
                break
        st = f.status()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return st
    st = asyncio.run(go())
    assert st["connected"] is True and st["messages"] == 2 and st["backfilled"] == 2 * 3 * 3 and st["error"] is None and st["history_error"] is None and st["synced_at"], st
    assert urls and "btcusdt@kline_1m" in urls[0] and "ethusdt@kline_15m" in urls[0] and urls[0].startswith(cf.WS_HOSTS[0])
    assert len(stored(e, BTC, 60)) == 3 and len(stored(e, "ETHUSDT_bn", 900)) == 3 and e.chart_of(BTC, 60)["forming"]["c"] == 104.0 and sock.closed is True


def test_a_failing_history_is_reported_and_does_not_stop_the_live_stream():
    e, f = make()

    def boom(pair, tf, start):
        raise RuntimeError("HTTP 451")
    f.fetch = boom
    sock = FakeWS([ws("BTCUSDT", "1m", NOW - NOW % 60, 100, 105, 99, 104, False, ev=NOW)])

    async def connect(url):
        return sock
    f._connect = connect

    async def go():
        task = asyncio.create_task(f.run())
        await asyncio.sleep(0.5)
        st = f.status()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return st
    st = asyncio.run(go())
    assert st["connected"] is True and st["messages"] == 1 and "451" in (st["history_error"] or "") and st["error"] is None, st
    assert e.chart_of(BTC, 60)["last_price"] == 104.0, "live prices flow although the history could not be loaded"


def test_a_failing_connection_is_reported_and_retried_without_crashing():
    e, f = make()
    f.fetch = lambda pair, tf, start: []

    async def connect(url):
        raise OSError("connection refused")
    f._connect = connect

    async def go():
        task = asyncio.create_task(f.run())
        await asyncio.sleep(0.4)
        st = f.status()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return st
    st = asyncio.run(go())
    assert st["connected"] is False and "connection refused" in (st["error"] or "") and st["reconnects"] >= 1
    assert cf.CryptoFeed(e, enabled=False).status()["enabled"] is False
    asyncio.run(cf.CryptoFeed(e, enabled=False).run())                    # switched off: returns at once


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all crypto feed tests passed")
