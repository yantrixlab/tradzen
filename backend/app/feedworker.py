"""The server-side price feed: a process that logs in to the broker ITSELF, follows the pairs and sends their prices (and each pair's recent history) to the Tradzen server, so no user's
chart has to supply candles. It talks to the server exactly like the admin's Quotex tab does - POST /ingest with a price-feed (feeder) token - so everything downstream (candles, signals,
the chart platform) is the code that already runs; the worker only replaces the sender.

    QXS_FEED_TOKEN=<feeder token>  python -m app.feedworker --url https://tradzen.example.com --adapter pyquotex [--pairs EURUSD_otc,BRLUSD_otc] [--selfcheck]

Pieces: an ADAPTER (the broker connection: instruments, a price stream, history) - `fake` for tests and demos, `pyquotex` (pyquotex_adapter.py) for the real thing; a SINK (where batches go:
the HTTP server, or an engine directly in tests); the WORKER (reconnect with back-off, a silence watchdog that reconnects when the stream stops, polite history back-fill, health file).
Run it as its own process / container (Dockerfile.feed, FEEDWORKER.md): a crash or a reconnect never takes the site down, and the server keeps working from the admin's tab feed meanwhile.
The worker reads the feeder token from the environment (never from the command line, where `ps` shows it) and never writes it anywhere.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

AGENT = "feedworker/1"
log = logging.getLogger("feedworker")


class SilentFeed(Exception):
    """No price arrived for too long: the connection is dead even if it does not say so."""


# --------------------------------------------------------------------------------------------- adapters
class Adapter:
    """A broker connection. Every method may raise; the worker reconnects."""
    name = "base"

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def instruments(self) -> list[tuple]:
        """[(symbol, name, category, payout, is_open)]"""
        raise NotImplementedError

    async def watch(self, symbols: list[str]) -> None:
        """Start following these pairs."""
        raise NotImplementedError

    async def ticks(self) -> list[tuple[str, float, float]]:
        """The prices that arrived since the last call: [(symbol, server time in seconds, price)]. May wait up to about a second for some."""
        raise NotImplementedError

    async def history(self, symbol: str, tf: int, minutes: int) -> list[list]:
        """Closed candles, oldest first, as [time, open, close, high, low, ticks, lastTick]."""
        raise NotImplementedError


class FakeAdapter(Adapter):
    """A random-walk market for tests and demos (one price per pair every `interval` seconds). fail_after: raise after that many ticks() calls (a dropped connection); silent_after: stop producing prices after that many calls."""
    name = "fake"

    def __init__(self, pairs: int = 4, interval: float = 0.01, fail_after: int | None = None, silent_after: int | None = None, seed: int = 1, closed: tuple[str, ...] = ()) -> None:
        self.syms = [f"FAKE{i}_otc" for i in range(pairs)]
        self.interval, self.fail_after, self.silent_after, self.closed = interval, fail_after, silent_after, set(closed)
        self.rng = random.Random(seed)
        self.px = {s: 1.0 + i * 0.1 for i, s in enumerate(self.syms)}
        self.watching: list[str] = []
        self.calls = 0
        self.connects = 0
        self.history_calls: list[tuple[str, int, int]] = []
        self.now = time.time                                       # tests may replace the clock

    async def connect(self) -> None:
        self.connects += 1
        self.calls = 0

    async def instruments(self) -> list[tuple]:
        return [(s, f"Fake {i} (OTC)", "currency", 80 + i, s not in self.closed) for i, s in enumerate(self.syms)]

    async def watch(self, symbols: list[str]) -> None:
        self.watching = list(symbols)

    async def ticks(self) -> list[tuple[str, float, float]]:
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise ConnectionError("the fake connection dropped")
        await asyncio.sleep(self.interval)
        if self.silent_after is not None and self.calls > self.silent_after:
            return []
        t = self.now()
        out = []
        for s in self.watching:
            self.px[s] *= 1 + self.rng.uniform(-0.0003, 0.0003)
            out.append((s, t, round(self.px[s], 5)))
        return out

    async def history(self, symbol: str, tf: int, minutes: int) -> list[list]:
        self.history_calls.append((symbol, tf, minutes))
        end = int(self.now() // tf * tf)
        rows, p = [], self.px[symbol]
        for i in range(minutes * 60 // tf, 0, -1):
            t = end - i * tf
            o = p
            c = o * (1 + self.rng.uniform(-0.0006, 0.0006))
            rows.append([t, round(o, 5), round(c, 5), round(max(o, c) * 1.0002, 5), round(min(o, c) * 0.9998, 5), 120, t + tf - 0.5])
            p = c
        return rows


# --------------------------------------------------------------------------------------------- sinks
class HttpSink:
    """POST /ingest of a Tradzen server with the feeder token. Failures are counted and raised so the worker can decide; a tick that could not be sent is dropped (an old price is useless)."""

    def __init__(self, url: str, token: str, timeout: float = 10.0) -> None:
        self.url = url.rstrip("/") + "/ingest"
        self.token = token
        self.timeout = timeout
        self.sent = 0
        self.failed = 0

    def _post(self, body: bytes) -> dict:
        req = urllib.request.Request(self.url, data=body, method="POST", headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}", "X-Requested-With": "qxs"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:                       # noqa: S310 - the URL is the operator's own server
            return json.loads(r.read() or b"{}")

    async def send(self, items: list[dict]) -> dict:
        body = json.dumps({"items": items, "agent": AGENT}).encode()
        try:
            out = await asyncio.to_thread(self._post, body)
        except (urllib.error.URLError, OSError, ValueError) as e:
            self.failed += 1
            raise ConnectionError(f"could not reach the server: {e}") from None
        self.sent += 1
        return out


class EngineSink:
    """Straight into an Engine (tests and a worker living inside the server's process)."""

    def __init__(self, engine, uid: int = 1) -> None:
        self.engine, self.uid = engine, uid
        self.sent = 0
        self.failed = 0

    async def send(self, items: list[dict]) -> dict:
        self.sent += 1
        return self.engine.ingest({"items": items, "agent": AGENT}, uid=self.uid, feeder=True)


# --------------------------------------------------------------------------------------------- the worker
class Worker:
    def __init__(self, adapter: Adapter, sink, pairs: list[str] | None = None, max_pairs: int = 80, history_minutes: int = 90, history_gap: float = 1.0, silence: float = 45.0,
                 instruments_every: float = 300.0, health_file: str | None = None, backoff_max: float = 60.0, batch_every: float = 1.0, tfs: tuple[int, ...] = (60,)) -> None:
        self.adapter, self.sink = adapter, sink
        self.only = pairs
        self.max_pairs, self.history_minutes, self.history_gap = max_pairs, history_minutes, history_gap
        self.silence, self.instruments_every, self.health_file, self.backoff_max, self.batch_every, self.tfs = silence, instruments_every, health_file, backoff_max, batch_every, tfs
        self.stats = {"started": time.time(), "ticks": 0, "batches": 0, "errors": 0, "reconnects": 0, "history_pairs": 0, "last_tick": 0.0, "last_error": "", "connected": False, "pairs": 0}
        self.stop = asyncio.Event()
        self._sleep = asyncio.sleep                                # tests replace it to run back-off instantly

    # ---- health (a file the container's health check reads)
    def health(self) -> dict:
        now = time.time()
        return {**self.stats, "now": now, "adapter": self.adapter.name, "ok": bool(self.stats["connected"] and now - self.stats["last_tick"] < self.silence)}

    def _write_health(self) -> None:
        if not self.health_file:
            return
        try:
            Path(self.health_file).write_text(json.dumps(self.health()), encoding="utf-8")
        except OSError:
            pass

    # ---- one connected session
    async def _backfill(self, symbols: list[str]) -> None:
        for sym in symbols:
            if self.stop.is_set():
                return
            for tf in self.tfs:
                rows = await self.adapter.history(sym, tf, self.history_minutes * (tf // 60 or 1))
                if len(rows) >= 2:
                    await self.sink.send([{"k": "h", "asset": sym, "rows": rows}])
            self.stats["history_pairs"] += 1
            await self._sleep(self.history_gap)                   # polite: a spike, not a crawler

    async def _session(self) -> None:
        await self.adapter.connect()
        try:
            inst = await self.adapter.instruments()
            await self.sink.send([{"k": "i", "list": [[s, n, c, p, bool(o)] for s, n, c, p, o in inst]}])
            wanted = [s for s, _, _, _, o in inst if o and (not self.only or s in self.only)]
            if self.only:
                wanted = [s for s in self.only if s in {i[0] for i in inst}]
            wanted = wanted[: self.max_pairs]
            await self.adapter.watch(wanted)
            self.stats.update(connected=True, pairs=len(wanted), last_tick=time.time())
            self.stats["last_error"] = ""
            back = asyncio.create_task(self._backfill(wanted))
            last_inst = time.time()
            last_batch = 0.0
            pending: list[list] = []
            try:
                while not self.stop.is_set():
                    got = await self.adapter.ticks()
                    now = time.time()
                    if got:
                        pending.extend([s, t, p, 0] for s, t, p in got)
                        self.stats["last_tick"] = now
                    elif now - self.stats["last_tick"] > self.silence:
                        raise SilentFeed(f"no price for {now - self.stats['last_tick']:.1f} s")
                    if pending and now - last_batch >= self.batch_every:
                        batch, pending = pending, []
                        last_batch = now
                        await self.sink.send([{"k": "t", "d": batch}])
                        self.stats["ticks"] += len(batch)
                        self.stats["batches"] += 1
                    if now - last_inst > self.instruments_every:
                        last_inst = now
                        inst = await self.adapter.instruments()
                        await self.sink.send([{"k": "i", "list": [[s, n, c, p, bool(o)] for s, n, c, p, o in inst]}])
                    if back.done() and back.exception():
                        raise back.exception()                    # a failed back-fill means the connection is not healthy either
                    self._write_health()
            finally:
                back.cancel()
                await asyncio.gather(back, return_exceptions=True)
        finally:
            self.stats["connected"] = False

    async def run(self, max_sessions: int | None = None) -> None:
        """Reconnect forever (or `max_sessions` times, for tests) with back-off 1, 2, 4 ... up to backoff_max seconds; the back-off resets after a session that lasted a minute."""
        backoff, sessions = 1.0, 0
        while not self.stop.is_set():
            began = time.time()
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as e:                                # noqa: BLE001 - whatever the broker library throws, the answer is the same: reconnect
                self.stats["errors"] += 1
                self.stats["last_error"] = f"{type(e).__name__}: {e}"[:200]
                log.warning("session ended: %s", self.stats["last_error"])
            finally:
                try:
                    await self.adapter.close()
                except Exception:                                 # noqa: BLE001
                    pass
            sessions += 1
            self._write_health()
            if max_sessions is not None and sessions >= max_sessions or self.stop.is_set():
                return
            self.stats["reconnects"] += 1
            backoff = 1.0 if time.time() - began > 60 else min(backoff * 2, self.backoff_max)
            await self._sleep(backoff)


# --------------------------------------------------------------------------------------------- command line
def make_adapter(name: str, args) -> Adapter:
    if name == "fake":
        return FakeAdapter(interval=0.5)                              # about two prices a second per pair, like a real feed
    if name == "pyquotex":
        from .pyquotex_adapter import PyQuotexAdapter                 # imported only here: the library is third-party and optional
        return PyQuotexAdapter(host=args.host, account=args.account)
    raise SystemExit(f"unknown adapter '{name}' (fake | pyquotex)")


async def amain(args) -> int:
    token = os.environ.get("QXS_FEED_TOKEN", "")
    if not token and not args.selfcheck:
        raise SystemExit("Set QXS_FEED_TOKEN (a price-feed token made on the Tradzen Account page) in the environment.")
    adapter = make_adapter(args.adapter, args)
    if args.selfcheck:
        from .pyquotex_adapter import selfcheck
        return await selfcheck(adapter, args.pairs.split(",")[0] if args.pairs else None)
    worker = Worker(adapter, HttpSink(args.url, token), pairs=[p for p in args.pairs.split(",") if p] or None, max_pairs=args.max_pairs, history_minutes=args.history_minutes,
                    silence=args.silence, health_file=args.health_file)
    await worker.run()
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Tradzen server-side price feed")
    ap.add_argument("--url", default=os.environ.get("QXS_URL", "http://127.0.0.1:8787"))
    ap.add_argument("--adapter", default=os.environ.get("QXS_FEED_ADAPTER", "pyquotex"), choices=["fake", "pyquotex"])
    ap.add_argument("--pairs", default=os.environ.get("QXS_FEED_PAIRS", ""), help="comma separated; empty = every open pair")
    ap.add_argument("--max-pairs", type=int, default=int(os.environ.get("QXS_FEED_MAX_PAIRS", "60")))
    ap.add_argument("--history-minutes", type=int, default=90)
    ap.add_argument("--silence", type=float, default=45.0, help="seconds without a price before the worker reconnects")
    ap.add_argument("--host", default=os.environ.get("QX_HOST", "quotex.com"))
    ap.add_argument("--account", default="PRACTICE", choices=["PRACTICE", "REAL"])
    ap.add_argument("--health-file", default=os.environ.get("QXS_FEED_HEALTH", "/tmp/feedworker-health.json"))
    ap.add_argument("--selfcheck", action="store_true", help="connect once, print what the broker library returns (shapes, a few prices) and exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        sys.exit(asyncio.run(amain(args)))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
