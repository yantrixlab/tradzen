"""Crypto markets from Binance's public market data: history, live candles and prices for the chart platform. No account and no API key.

What it is for: the Markets page shows these pairs next to the Quotex ones (under "Crypto", locked or opened by plan like any other pair). Binance sends finished and forming candles itself
(open / high / low / close of the 1, 5 and 15 minute candles), so nothing is built from ticks and the chart matches Binance's own. Everything is stored under `<PAIR>_bn` (BTCUSDT_bn).

What it is NOT: a trading market. These symbols are SHOWN only: no strategy runs on them, no signal, alert or settlement comes from them, and the AI, the Edge Lab, the research and the
sweeps leave them out (candles.is_display_only). The prices are Binance's, not Quotex's.

How it runs: one asyncio task inside the server (so it shares the engine's thread). It holds ONE combined WebSocket (`<pair>@kline_1m/5m/15m` for every pair) and feeds each message to
`engine.feed_display`; at the same time (start-up, and again after every dropped connection) it fills what is missing from Binance's REST klines (`/api/v3/klines`), several requests at once,
so live prices do not wait for the history. A dropped connection retries with back-off.
`QXS_CRYPTO=off` switches it off; `QXS_BINANCE_REST` / `QXS_BINANCE_WS` (comma-separated) replace the hosts. The default hosts are Binance's market-data-only ones, with the main ones as a
fallback (Binance refuses some countries on the main hosts).

Limits, stated plainly: this is public data used under Binance's own terms of use, which you must read before you show it in a paid product (see CRYPTO.md); Binance may change or stop it;
a stream that is silent for a minute is treated as dead and reconnected.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .candles import TFS

SUFFIX = "_bn"
MARKET = "CRYPTO"
CATEGORY = "cryptocurrency"
PAIRS: tuple[tuple[str, str], ...] = (
    ("BTCUSDT", "Bitcoin"), ("ETHUSDT", "Ethereum"), ("BNBUSDT", "BNB"), ("XRPUSDT", "XRP"), ("SOLUSDT", "Solana"), ("ADAUSDT", "Cardano"),
    ("DOGEUSDT", "Dogecoin"), ("AVAXUSDT", "Avalanche"), ("LINKUSDT", "Chainlink"), ("LTCUSDT", "Litecoin"), ("TRXUSDT", "TRON"), ("DOTUSDT", "Polkadot"),
)
INTERVALS = {60: "1m", 300: "5m", 900: "15m"}
TF_OF = {v: k for k, v in INTERVALS.items()}
BACKFILL = 1000                                   # candles per request (Binance's maximum): 16 h of 1-minute candles, 3.5 days of 5-minute, 10 days of 15-minute
WORKERS = 6                                       # history requests at once (each is 2 of Binance's 6000 request weight a minute)
SILENCE = 60.0                                    # no message for this long = the connection is dead
REST_HOSTS = ("https://data-api.binance.vision", "https://api.binance.com")
WS_HOSTS = ("wss://data-stream.binance.vision", "wss://stream.binance.com:9443")


def _hosts(env: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(env, "").strip()
    return tuple(h.strip().rstrip("/") for h in raw.split(",") if h.strip()) or default


def stored(pair: str) -> str:
    return pair + SUFFIX


def _row(t_ms, o, h, l, c, n) -> dict | None:
    try:
        o, h, l, c = float(o), float(h), float(l), float(c)
        if not all(math.isfinite(x) and x > 0 for x in (o, h, l, c)):
            return None
        return {"t": int(t_ms) // 1000, "o": o, "h": max(h, o, c), "l": min(l, o, c), "c": c, "n": int(n or 0)}
    except (TypeError, ValueError):
        return None


def parse_rest_kline(k: list) -> dict | None:
    """One row of `/api/v3/klines`: [openTime, o, h, l, c, volume, closeTime, quoteVolume, trades, ...] -> a candle (plus `close_ms`)."""
    try:
        r = _row(k[0], k[1], k[2], k[3], k[4], k[8])
        if r is not None:
            r["close_ms"] = int(k[6])
        return r
    except (IndexError, TypeError, ValueError):
        return None


def parse_ws(msg: dict) -> tuple[str, int, dict, bool, float] | None:
    """A combined-stream message -> (pair, timeframe, candle, closed, event time in seconds), or None when it is not a candle of a timeframe we follow."""
    d = msg.get("data", msg) if isinstance(msg, dict) else None
    if not isinstance(d, dict) or d.get("e") != "kline":
        return None
    k = d.get("k")
    if not isinstance(k, dict):
        return None
    tf = TF_OF.get(k.get("i"))
    row = _row(k.get("t"), k.get("o"), k.get("h"), k.get("l"), k.get("c"), k.get("n"))
    pair = str(d.get("s") or k.get("s") or "").upper()
    if tf is None or row is None or not pair:
        return None
    try:
        ts = float(d.get("E")) / 1000.0
    except (TypeError, ValueError):
        ts = time.time()
    return pair, tf, row, bool(k.get("x")), ts


def fetch_klines(pair: str, tf: int, start_ms: int | None = None, limit: int = BACKFILL, timeout: float = 15.0, hosts: tuple[str, ...] | None = None) -> list[dict]:
    """Candles of one pair / timeframe from Binance (blocking: run it in a thread). Tries each host in turn."""
    q = {"symbol": pair, "interval": INTERVALS[tf], "limit": limit}
    if start_ms:
        q["startTime"] = int(start_ms)
    last: Exception | None = None
    for host in hosts or _hosts("QXS_BINANCE_REST", REST_HOSTS):
        req = urllib.request.Request(f"{host}/api/v3/klines?{urllib.parse.urlencode(q)}", headers={"User-Agent": "Tradzen/1 (market data)"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                doc = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = RuntimeError(f"{host}: HTTP {e.code}")
            if e.code in (418, 429):                      # banned / rate limited: the next host would be the same address in Binance's eyes
                break
            continue
        except (urllib.error.URLError, OSError, ValueError) as e:
            last = RuntimeError(f"{host}: {e}")
            continue
        if isinstance(doc, list):
            return [r for r in (parse_rest_kline(k) for k in doc) if r]
        last = RuntimeError(f"{host}: unexpected answer")
    raise last or RuntimeError("no host")


def start_for(last_ts: int | None, tf: int, now: float) -> int | None:
    """Where to resume: from the newest stored candle (refetched, it costs nothing), or None = the newest BACKFILL candles when nothing is stored or the hole is longer than one request."""
    if not last_ts or now - last_ts > BACKFILL * tf:
        return None
    return int(last_ts) * 1000


class CryptoFeed:
    def __init__(self, engine, enabled: bool = True, pairs: tuple[tuple[str, str], ...] = PAIRS, fetch=fetch_klines, connect=None, pause: float = 0.08) -> None:
        self.engine, self.enabled, self.pairs, self.fetch, self._connect, self.pause = engine, enabled, pairs, fetch, connect, pause
        self.known = {p for p, _ in pairs}
        self.state = {"connected": False, "endpoint": None, "since": None, "last_msg": 0.0, "messages": 0, "reconnects": 0, "error": None, "history_error": None, "backfilled": 0, "synced_at": None}

    # ------------------------------------------------------------------ what the admin sees
    def status(self) -> dict:
        now = time.time()
        s = self.state
        return {"enabled": self.enabled, "pairs": len(self.pairs), "connected": bool(s["connected"] and now - s["last_msg"] < SILENCE), "endpoint": s["endpoint"], "since": s["since"],
                "last_msg_age": round(now - s["last_msg"], 1) if s["last_msg"] else None, "messages": s["messages"], "reconnects": s["reconnects"], "error": s["error"],
                "history_error": s["history_error"], "backfilled": s["backfilled"], "synced_at": s["synced_at"], "source": "Binance public market data (no key)"}

    def register(self) -> None:
        for pair, name in self.pairs:
            self.engine.register_display(stored(pair), f"{name} ({pair[:-4]}/USDT)", CATEGORY, MARKET)

    # ------------------------------------------------------------------ history
    def plan(self, now: float) -> list[tuple[str, int, int | None]]:
        out = []
        for pair, _ in self.pairs:
            for tf in TFS:
                last = self.engine.db.execute("SELECT MAX(ts) FROM candles WHERE symbol=? AND tf=?", (stored(pair), tf)).fetchone()[0]
                out.append((pair, tf, start_for(last, tf, now)))
        return out

    def _fetch_all(self, plan: list[tuple[str, int, int | None]]) -> tuple[dict[tuple[str, int], list[dict]], list[str]]:
        got: dict[tuple[str, int], list[dict]] = {}
        errors: list[str] = []

        def one(item):
            pair, tf, start = item
            try:
                return (pair, tf), self.fetch(pair, tf, start), None
            except Exception as e:                        # noqa: BLE001 - one pair must not stop the others
                return (pair, tf), None, f"{pair} {INTERVALS[tf]}: {e}"
            finally:
                if self.pause:
                    time.sleep(self.pause)
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for key, rows, err in pool.map(one, plan):
                if err:
                    errors.append(err)
                else:
                    got[key] = rows
        return got, errors

    def apply_history(self, got: dict[tuple[str, int], list[dict]], now: float) -> int:
        n = 0
        for (pair, tf), rows in got.items():
            for r in rows:
                if r["close_ms"] >= now * 1000:           # still forming: the stream carries it
                    continue
                self.engine.feed_display(stored(pair), tf, r, closed=True)
                n += 1
        self.engine._flush(force=True)
        return n

    async def sync(self) -> None:
        now = time.time()
        got, errors = await asyncio.to_thread(self._fetch_all, self.plan(now))
        n = self.apply_history(got, now)
        self.state.update(backfilled=self.state["backfilled"] + n, synced_at=time.time(), history_error=errors[0] if errors else None)
        if errors:
            self.engine._log("crypto-feed", f"history: {len(errors)} request(s) failed, first: {errors[0]}")

    async def _fill(self) -> None:
        """The history in the background: a failure is reported, and never touches the live stream."""
        try:
            await self.sync()
        except asyncio.CancelledError:
            raise
        except Exception as e:                            # noqa: BLE001
            self.state["history_error"] = str(e)[:300] or e.__class__.__name__

    # ------------------------------------------------------------------ live
    def handle(self, msg: dict) -> bool:
        p = parse_ws(msg)
        if p is None or p[0] not in self.known:
            return False
        pair, tf, row, closed, ts = p
        self.engine.feed_display(stored(pair), tf, row, closed, ts)
        self.state["last_msg"] = time.time()
        self.state["messages"] += 1
        return True

    def ws_urls(self) -> list[str]:
        streams = "/".join(f"{p.lower()}@kline_{iv}" for p, _ in self.pairs for iv in INTERVALS.values())
        return [f"{h}/stream?streams={streams}" for h in _hosts("QXS_BINANCE_WS", WS_HOSTS)]

    async def _open(self, url: str):
        if self._connect is not None:                     # tests
            return await self._connect(url)
        import websockets
        return await websockets.connect(url, ping_interval=20, ping_timeout=20, open_timeout=15, max_size=2 ** 22)

    async def stream(self) -> None:
        """Hold one connection until it drops or goes silent. Raises when no host can be reached."""
        last: Exception | None = None
        for url in self.ws_urls():
            try:
                ws = await self._open(url)
            except Exception as e:                        # noqa: BLE001
                last = e
                continue
            host = url.split("/stream", 1)[0]
            self.state.update(connected=True, endpoint=host, since=time.time(), last_msg=time.time(), error=None)
            self.engine._log("crypto-feed", f"connected to {host}")
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=SILENCE)
                    try:
                        self.handle(json.loads(raw))
                    except ValueError:
                        continue
                    self.engine._flush()                  # throttled: writes the closed candles every few seconds
            finally:
                self.state["connected"] = False
                try:
                    await ws.close()
                except Exception:                         # noqa: BLE001
                    pass
        raise RuntimeError(f"cannot connect: {last}")

    async def run(self) -> None:
        if not self.enabled:
            return
        self.register()
        delay = 3.0
        while True:
            started = time.time()
            filler = asyncio.create_task(self._fill())                # the history is fetched while the stream is already live
            try:
                await self.stream()
            except asyncio.CancelledError:
                raise
            except Exception as e:                        # noqa: BLE001 - the feed must never take the server down
                self.state["error"] = str(e)[:300] or e.__class__.__name__
                self.engine._log("crypto-feed", f"dropped: {self.state['error']}")
            finally:
                filler.cancel()
            self.state["connected"] = False
            self.state["reconnects"] += 1
            delay = 3.0 if time.time() - started > 120 else min(60.0, delay * 2)      # a connection that lasted a while starts the back-off over
            await asyncio.sleep(delay)
