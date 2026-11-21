"""The server-side price feed: the parsers of the broker library's answers, the worker (stream, history back-fill, reconnect with back-off, silence watchdog), the HTTP sink, and the admin's
switches for ordinary accounts' candles / live prices with the status of who feeds the market. Run from backend/:  .venv\\Scripts\\python tests\\test_feedworker.py"""
import asyncio
import json
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import feedsources  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.feedworker import AGENT, EngineSink, FakeAdapter, HttpSink, Worker  # noqa: E402
from app.pyquotex_adapter import candle_rows, guess_category, parse_assets, price_rows  # noqa: E402
from test_auth import ADMIN, ADMIN_PW, H, _cleanup, _client, _main  # noqa: E402


def engine() -> Engine:
    return Engine(Path(tempfile.mkdtemp()) / "t.db")


async def run_until(w: Worker, cond, timeout: float = 5.0, **kw) -> None:
    task = asyncio.create_task(w.run(**kw))
    end = time.time() + timeout
    while time.time() < end and not cond() and not task.done():
        await asyncio.sleep(0.02)
    w.stop.set()
    await asyncio.wait_for(task, 5)


# ------------------------------------------------------------------ parsers
def test_asset_lists_in_the_plausible_shapes():
    names = [["EURUSD_otc", "EUR/USD (OTC)"], ["BTCUSD_otc", "Bitcoin (OTC)"], ["XAUUSD", "Gold"], ["F40EUR", "CAC 40"]]
    pay = {"EURUSD_otc": {"profit": {"1M": 88, "5M": 84}, "open": True}, "BTCUSD_otc": {"payment": 53, "open": False}, "XAUUSD": {}}
    got = {r[0]: r for r in parse_assets(names, pay)}
    assert got["EURUSD_otc"] == ("EURUSD_otc", "EUR/USD (OTC)", "currency", 88.0, True)
    assert got["BTCUSD_otc"][2:] == ("cryptocurrency", 53.0, False) and got["XAUUSD"][2] == "commodity" and got["XAUUSD"][3] is None and got["F40EUR"][2] == "stock"
    assert [r[0] for r in parse_assets({"A_otc": "A", "B_otc": "B"}, None)] == ["A_otc", "B_otc"]
    assert [r[0] for r in parse_assets(["X_otc", "Y_otc"], {})] == ["X_otc", "Y_otc"]
    assert [r[0] for r in parse_assets([{"symbol": "Z_otc", "name": "Z"}], {})] == ["Z_otc"]
    assert parse_assets([["EUR/USD (OTC)", "EURUSD_otc"]], {})[0][:2] == ("EURUSD_otc", "EUR/USD (OTC)"), "a pair given the other way round is put right"
    assert parse_assets(None, None) == [] and guess_category("USDJPY_otc") == "currency"


def test_prices_and_candles_in_the_plausible_shapes():
    assert price_rows([{"time": 1700000000, "price": 1.5}, {"time": 1700000001000, "price": 1.6}, {"time": "x", "price": 1}]) == [(1700000000.0, 1.5), (1700000001.0, 1.6)]
    assert price_rows([[1700000000, 2.5]]) == [(1700000000.0, 2.5)] and price_rows({"time": 1, "price": 2}) == [(1.0, 2.0)] and price_rows(None) == [] and price_rows([[1, -3]]) == []
    # Quotex's own order [time, open, close, high, low]
    r = candle_rows([[1700000000, 1.0, 1.1, 1.2, 0.9], [1700000060, 1.1, 1.0, 1.15, 0.95]])
    assert r[0][:5] == [1700000000, 1.0, 1.1, 1.2, 0.9] and r[1][:5] == [1700000060, 1.1, 1.0, 1.15, 0.95]
    # [time, open, high, low, close] is recognised by the high / low bounding the body, and converted to the same layout
    r2 = candle_rows([[1700000000, 1.0, 1.2, 0.9, 1.1], [1700000060, 1.1, 1.15, 0.95, 1.0]])
    assert r2[0][:5] == [1700000000, 1.0, 1.1, 1.2, 0.9], r2
    r3 = candle_rows([{"time": 1700000000000, "open": 1, "close": 1.1, "high": 1.2, "low": 0.9}])
    assert r3[0][0] == 1700000000 and r3[0][2] == 1.1 and candle_rows(None) == [] and candle_rows([]) == []


# ------------------------------------------------------------------ the worker
def test_the_worker_feeds_instruments_history_and_live_prices_into_an_engine():
    async def go():
        e = engine()
        ad = FakeAdapter(pairs=3, closed=("FAKE2_otc",))
        w = Worker(ad, EngineSink(e), history_minutes=30, history_gap=0.01, batch_every=0.05)
        await run_until(w, lambda: w.stats["ticks"] >= 12 and w.stats["history_pairs"] >= 2, max_sessions=1)
        return e, ad, w
    e, ad, w = asyncio.run(go())
    assert set(e.instruments) == {"FAKE0_otc", "FAKE1_otc", "FAKE2_otc"} and e.instruments["FAKE0_otc"]["payout"] == 80 and e.instruments["FAKE2_otc"]["open"] is False
    assert ad.watching == ["FAKE0_otc", "FAKE1_otc"], "a market that is shut is not followed"
    assert {s for s, tf, m in ad.history_calls} == {"FAKE0_otc", "FAKE1_otc"} and all(tf == 60 for s, tf, m in ad.history_calls)
    assert "FAKE0_otc" in e.last_tick and "FAKE2_otc" not in e.last_tick
    stored = e.db.execute("SELECT COUNT(*) FROM candles WHERE symbol='FAKE0_otc' AND tf=60 AND source='history'").fetchone()[0]
    assert stored >= 25, stored
    rec = e.feeder_seen[1]
    assert rec["agent"] == AGENT and rec["ticks"] >= 12 and set(rec["pairs"]) == {"FAKE0_otc", "FAKE1_otc"}
    st = feedsources.status(e)
    assert st["worker"]["alive"] is True and st["worker"]["pairs_live"] == 2 and st["streaming"]["feed"] == 2 and st["feeders"][0]["worker"] is True
    assert w.health()["ok"] is True or w.stats["connected"] is False


def test_it_reconnects_with_backoff_when_the_connection_drops():
    async def go():
        e = engine()
        ad = FakeAdapter(pairs=2, fail_after=3)
        w = Worker(ad, EngineSink(e), history_minutes=5, history_gap=0.0, batch_every=0.0)
        waits = []

        async def no_sleep(s):
            waits.append(s)
            await asyncio.sleep(0)
        w._sleep = no_sleep
        await w.run(max_sessions=4)
        return ad, w, waits
    ad, w, waits = asyncio.run(go())
    assert ad.connects == 4 and w.stats["errors"] == 4 and "ConnectionError" in w.stats["last_error"]
    backoffs = [x for x in waits if x >= 1.0]
    assert backoffs[:3] == [2.0, 4.0, 8.0], backoffs                 # grows 2, 4, 8 ... (history pauses are the 0.0 waits)
    assert w.stats["reconnects"] == 3 and w.stats["connected"] is False


def test_a_silent_stream_is_treated_as_a_dead_connection():
    async def go():
        ad = FakeAdapter(pairs=1, silent_after=2)
        w = Worker(ad, EngineSink(engine()), history_minutes=5, history_gap=0.0, batch_every=0.0, silence=0.2)

        async def no_sleep(s):
            await asyncio.sleep(0)
        w._sleep = no_sleep
        await w.run(max_sessions=2)
        return w, ad
    w, ad = asyncio.run(go())
    assert "SilentFeed" in w.stats["last_error"] and ad.connects == 2


def test_the_health_file_says_when_the_worker_is_ok():
    async def go():
        f = Path(tempfile.mkdtemp()) / "h.json"
        w = Worker(FakeAdapter(pairs=1), EngineSink(engine()), history_minutes=5, history_gap=0.0, batch_every=0.05, health_file=str(f))
        live = []

        def cond():                                                # read it while the worker runs: the last write, after it stops, says connected = false
            try:
                h = json.loads(f.read_text())
            except (OSError, ValueError):
                return False
            if h["ticks"] >= 5:
                live.append(h)
            return bool(live)
        await run_until(w, cond, max_sessions=1)
        stopped = json.loads(f.read_text())
        return live[0], stopped
    h, stopped = asyncio.run(go())
    assert h["adapter"] == "fake" and h["ticks"] >= 5 and h["pairs"] == 1 and h["ok"] is True
    assert stopped["connected"] is False and stopped["ok"] is False, "a worker that has stopped is not ok"


# ------------------------------------------------------------------ the HTTP sink
def test_the_http_sink_sends_the_feeder_token_and_agent_and_reports_a_dead_server():
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            seen.update(path=self.path, auth=self.headers.get("Authorization"), body=json.loads(self.rfile.read(n)))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        sink = HttpSink(f"http://127.0.0.1:{srv.server_port}/", "tok-123")
        out = asyncio.run(sink.send([{"k": "t", "d": [["A_otc", 1.0, 2.0, 0]]}]))
    finally:
        srv.shutdown()
    assert out == {"ok": True} and seen["path"] == "/ingest" and seen["auth"] == "Bearer tok-123" and seen["body"]["agent"] == AGENT and seen["body"]["items"][0]["k"] == "t"
    dead = HttpSink("http://127.0.0.1:9", "x", timeout=1.0)
    try:
        asyncio.run(dead.send([]))
        raise AssertionError("a dead server must raise")
    except ConnectionError:
        assert dead.failed == 1


# ------------------------------------------------------------------ over HTTP in hosted mode: the feeder token, the status and the switches
def test_a_worker_batch_is_accepted_with_a_feeder_token_and_the_switches_close_the_other_senders():
    main = _main(QXS_ENV="production", QXS_SECRET_KEY="k" * 32, QXS_ADMIN_USER=ADMIN, QXS_ADMIN_PASSWORD=ADMIN_PW)
    eng = main.engine
    adm = _client(main)
    assert adm.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW}).status_code == 200
    assert adm.post("/api/users", json={"username": "anna", "password": "anna-pass-12345", "role": "trader", "must_change_pw": False}, headers=H).status_code == 200
    feeder = adm.post("/api/tokens", json={"label": "feed worker", "feeder": True}, headers=H).json()["secret"]
    now = time.time()
    batch = {"agent": AGENT, "items": [{"k": "i", "list": [["EURUSD_otc", "EUR/USD (OTC)", "currency", 88, True]]}, {"k": "t", "d": [["EURUSD_otc", now - 1, 1.1, 0], ["EURUSD_otc", now, 1.1001, 0]]}]}
    ext = _client(main)
    r = ext.post("/ingest", json=batch, headers={"Authorization": f"Bearer {feeder}"})
    assert r.status_code == 200, r.text
    assert "EURUSD_otc" in eng.instruments and "EURUSD_otc" in eng.last_tick

    st = adm.get("/api/platform-admin/sources").json()
    assert st["worker"]["alive"] is True and st["feeders"][0]["agent"] == AGENT and st["streaming"]["feed"] == 1 and st["total_streaming"] == 1
    assert st["switches"]["contrib"]["on"] is True and st["switches"]["leases"]["on"] is True and st["ready_to_switch_off"] is True
    assert adm.get("/api/platform-admin/sources").status_code == 200 and _client(main).get("/api/platform-admin/sources").status_code == 401

    # an ordinary account's extension: its history is taken while the switch is on, and refused (state off) once the admin turns it off
    tok = _client(main).post("/api/auth/extension-login", json={"username": "anna", "password": "anna-pass-12345", "device": "d", "feeder": False}).json()["token"]
    hist = {"k": "h", "asset": "EURUSD_otc", "rows": [[int(now // 60 * 60) - 60 * i, 1.1, 1.1, 1.1, 1.1, 5, 0] for i in range(20, 5, -1)]}
    anna = _client(main)
    assert anna.post("/ingest", json={"items": [hist], "after_seq": 0}, headers={"Authorization": f"Bearer {tok}"}).json()["contrib"]["state"] in ("on", "wait")
    trader = _client(main)
    assert trader.post("/api/auth/login", json={"username": "anna", "password": "anna-pass-12345"}).status_code == 200
    assert trader.put("/api/platform-admin/sources", json={"contrib": False, "leases": False}, headers=H).status_code == 403
    off = adm.put("/api/platform-admin/sources", json={"contrib": False, "leases": False}, headers=H).json()
    assert off["switches"]["contrib"]["on"] is False and off["switches"]["leases"]["on"] is False
    assert anna.post("/ingest", json={"items": [hist], "after_seq": 0}, headers={"Authorization": f"Bearer {tok}"}).json()["contrib"]["state"] == "off"
    live = anna.post("/ingest", json={"items": [{"k": "t", "d": [["EURUSD_otc", now, 9.9, 0]]}], "after_seq": 0}, headers={"Authorization": f"Bearer {tok}"}).json()
    assert live["feed"]["state"] == "off" and eng.last_tick["EURUSD_otc"]["price"] != 9.9, "an ordinary account's prices are not taken any more"
    # the choice survives a restart: apply() puts the stored switches back on a fresh engine state
    eng.contrib.enabled = eng.leases.enabled = True
    feedsources.apply(eng)
    assert eng.contrib.enabled is False and eng.leases.enabled is False
    assert any(a["action"] == "sources-switch" for a in main.auth.audit_tail(10))
    on = adm.put("/api/platform-admin/sources", json={"contrib": True, "leases": True}, headers=H).json()
    assert on["switches"]["contrib"]["on"] is True and on["switches"]["leases"]["on"] is True


if __name__ == "__main__":
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                fn()
                print("ok", name)
        print("all feed worker tests passed")
    finally:
        _cleanup()
