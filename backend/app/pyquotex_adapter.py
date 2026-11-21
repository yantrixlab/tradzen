"""The broker connection of the feed worker, built on the open-source PyQuotex client (https://github.com/cleitonleonel/pyquotex).

!!! UNVERIFIED AGAINST THE REAL LIBRARY. It is written from PyQuotex's published documentation, which does not say what several calls return (the asset list, the payout table, the
realtime price and the candle rows). Every parser below therefore accepts the plausible shapes and the first real run is `python -m app.feedworker --adapter pyquotex --selfcheck`, which
prints exactly what the library returned so the parsers can be fixed from facts before anything is sent to the server. Do not run the worker for real until the self-check looks right.

Needs: Python 3.12+, `pip install` of PyQuotex (read its code first: it is third-party and logs in with your Quotex account), and QX_EMAIL / QX_PASSWORD in the environment of THIS
process only - use a dedicated account, never your main one. Quotex has no official API: the account can be banned, the protocol can change, and its terms may forbid automated
access or redistributing its prices (see PLAN-PLATFORM.md). The worker keeps polite request rates (one request at a time for history, a pause between pairs).
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import time

from .feedworker import Adapter

CRYPTO = ("BTC", "ETH", "LTC", "XRP", "BCH", "BNB", "SOL", "DOT", "ETC", "DAS", "ZEC", "TON", "AVA", "AXS", "ATO", "LIN", "TRU", "DOGE", "ADA", "SHIB")
COMMODITY = ("XAU", "XAG", "UKBRENT", "USCRUDE", "BRENT", "CRUDE", "GOLD", "SILVER")
INDEX = ("AXJ", "CHIA", "F40", "FTS", "HSI", "IBX", "JPX", "STX", "DJI", "NDX", "SPX", "US30", "US100")


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _secs(t: float) -> float:
    return t / 1000.0 if t > 1e11 else float(t)


def guess_category(symbol: str) -> str:
    s = symbol.upper().replace("_OTC", "")
    if any(s.startswith(k) for k in CRYPTO):
        return "cryptocurrency"
    if any(s.startswith(k) for k in COMMODITY):
        return "commodity"
    if any(s.startswith(k) for k in INDEX):
        return "stock"
    return "currency"


def parse_assets(names, pay) -> list[tuple]:
    """[(symbol, name, category, payout, is_open)] from whatever get_all_asset_name() and get_payment() returned. A symbol is a string without spaces; the name is the nicer text beside it."""
    pairs: list[tuple[str, str]] = []
    if isinstance(names, dict):
        pairs = [(str(k), str(v)) for k, v in names.items()]
    elif isinstance(names, (list, tuple)):
        for it in names:
            if isinstance(it, (list, tuple)) and it:
                sym = str(it[0])
                name = str(it[1]) if len(it) > 1 else sym
                if " " in sym and len(it) > 1 and " " not in str(it[1]):              # the pair came the other way round
                    sym, name = str(it[1]), str(it[0])
                pairs.append((sym, name))
            elif isinstance(it, dict):
                sym = str(it.get("symbol") or it.get("asset") or it.get("id") or "")
                if sym:
                    pairs.append((sym, str(it.get("name") or sym)))
            elif isinstance(it, str):
                pairs.append((it, it))
    table = pay if isinstance(pay, dict) else {}
    out = []
    for sym, name in pairs:
        if not sym or " " in sym:
            continue
        entry = table.get(sym) or table.get(name) or {}
        payout, is_open = None, True
        if isinstance(entry, dict):
            is_open = bool(entry.get("open", True))
            for key in ("profit", "payment", "payout", "percent"):
                v = entry.get(key)
                if isinstance(v, dict):
                    v = next((x for x in (_num(a) for a in v.values()) if x is not None), None)
                v = _num(v)
                if v is not None:
                    payout = v
                    break
        out.append((sym, name, guess_category(sym), payout, is_open))
    return out


def price_rows(raw) -> list[tuple[float, float]]:
    """[(time in seconds, price)] from whatever get_realtime_price() returned: a list of {"time", "price"} dicts, a list of [time, price], or one dict."""
    items = raw if isinstance(raw, (list, tuple)) else [raw] if isinstance(raw, dict) else []
    out = []
    for it in items:
        t = p = None
        if isinstance(it, dict):
            t, p = _num(it.get("time") if it.get("time") is not None else it.get("timestamp") if it.get("timestamp") is not None else it.get("ts")), _num(it.get("price") if it.get("price") is not None else it.get("value"))
        elif isinstance(it, (list, tuple)) and len(it) >= 2:
            t, p = _num(it[0]), _num(it[1])
        if t is not None and p is not None and p > 0:
            out.append((_secs(t), p))
    return sorted(out)


def candle_rows(raw) -> list[list]:
    """[time, open, close, high, low, ticks, lastTick] from the candle rows get_candles() returned: dicts, or lists in Quotex's order [t, o, c, h, l] or [t, o, h, l, c] (whichever makes the
    high / low bound the body on most rows)."""
    rows = list(raw or [])
    if not rows:
        return []
    out: list[list] = []
    if all(isinstance(r, dict) for r in rows):
        for r in rows:
            def g(*k):
                for n in k:
                    v = _num(r.get(n))
                    if v is not None:
                        return v
                return None
            t, o, c, h, l = g("time", "t", "from", "timestamp"), g("open", "o"), g("close", "c"), g("high", "h", "max"), g("low", "l", "min")
            if None not in (t, o, c, h, l):
                out.append([int(_secs(t)), o, c, h, l, int(g("ticks", "n", "volume") or 0), _secs(t)])
        return sorted(out)
    cand = {"tochl": [], "tohlc": []}
    for r in rows:
        if isinstance(r, (list, tuple)) and len(r) >= 5:
            v = [_num(x) for x in r[:5]]
            if None in v:
                continue
            t = int(_secs(v[0]))
            cand["tochl"].append([t, v[1], v[2], v[3], v[4]])
            cand["tohlc"].append([t, v[1], v[4], v[2], v[3]])         # converted to [t, o, c, h, l]
    best = max(cand.values(), key=lambda lst: sum(1 for r in lst if r[3] >= max(r[1], r[2]) - 1e-12 and r[4] <= min(r[1], r[2]) + 1e-12), default=[])
    return sorted([*r, 0, float(r[0])] for r in best)


class PyQuotexAdapter(Adapter):
    name = "pyquotex"

    def __init__(self, host: str = "quotex.com", account: str = "PRACTICE") -> None:
        self.host, self.account = host, account
        self.client = None
        self.symbols: list[str] = []
        self.seen: dict[str, float] = {}

    async def connect(self) -> None:
        email, password = os.environ.get("QX_EMAIL"), os.environ.get("QX_PASSWORD")
        if not email or not password:
            raise SystemExit("Set QX_EMAIL and QX_PASSWORD in the environment of this process (a dedicated Quotex account).")
        try:
            from pyquotex.stable_api import Quotex
        except ImportError:
            raise SystemExit("PyQuotex is not installed here (Python 3.12+, see Dockerfile.feed / FEEDWORKER.md).")
        self.client = Quotex(email=email, password=password, lang="en", host=self.host)
        ok, msg = await self.client.connect()
        if not ok:
            raise ConnectionError(f"Quotex login failed: {str(msg)[:120]}")
        try:
            self.client.set_account_mode(self.account)                 # the docs and the README disagree on this call: a failure here is not fatal
        except Exception:                                             # noqa: BLE001
            pass
        self.seen.clear()

    async def close(self) -> None:
        if self.client is not None:
            try:
                await self.client.close()
            finally:
                self.client = None

    async def raw_assets(self):
        names = self.client.get_all_asset_name()
        pay = self.client.get_payment()
        return (await names if asyncio.iscoroutine(names) else names), (await pay if asyncio.iscoroutine(pay) else pay)

    async def instruments(self) -> list[tuple]:
        names, pay = await self.raw_assets()
        return parse_assets(names, pay)

    async def watch(self, symbols: list[str]) -> None:
        self.symbols = list(symbols)
        for s in self.symbols:
            await self.client.start_realtime_price(s, 60)
            await asyncio.sleep(0.15)                                  # one subscription at a time

    async def ticks(self) -> list[tuple[str, float, float]]:
        out: list[tuple[str, float, float]] = []
        for s in self.symbols:
            for t, p in price_rows(await self.client.get_realtime_price(s)):
                if t > self.seen.get(s, 0.0):
                    self.seen[s] = t
                    out.append((s, t, p))
        if not out:
            await asyncio.sleep(0.5)
        return out

    async def history(self, symbol: str, tf: int, minutes: int) -> list[list]:
        raw = await self.client.get_candles(symbol, int(time.time()), minutes * 60, tf)
        return candle_rows(raw if isinstance(raw, (list, tuple)) else (raw or {}).get("candles") if isinstance(raw, dict) else [])


async def selfcheck(adapter: PyQuotexAdapter, pair: str | None = None, seconds: float = 12.0) -> int:
    """Connect once and print what the library really returns, so the parsers above can be checked (and fixed) against facts. Prints no credentials."""
    def show(label, obj):
        txt = json.dumps(obj, default=str)[:600]
        print(f"- {label}: {type(obj).__name__} {txt}")
    await adapter.connect()
    try:
        names, pay = await adapter.raw_assets()
        show("get_all_asset_name()", names if not isinstance(names, (list, dict)) or len(names) < 4 else (list(names)[:4] if isinstance(names, list) else dict(list(names.items())[:4])))
        show("get_payment()", dict(list(pay.items())[:3]) if isinstance(pay, dict) else pay)
        inst = parse_assets(names, pay)
        print(f"- parsed instruments: {len(inst)}; first 5: {inst[:5]}")
        otc = [i for i in inst if i[0].endswith('_otc') and i[4]]
        sym = pair or (otc[0][0] if otc else inst[0][0] if inst else None)
        if not sym:
            print("!! no pair to test with")
            return 1
        print(f"- testing with {sym}")
        raw = await adapter.client.get_candles(sym, int(time.time()), 3600, 60)
        show("get_candles(1 h, 1 min)", raw if not isinstance(raw, list) or len(raw) < 3 else raw[:2] + ["..."] + raw[-1:])
        rows = candle_rows(raw if isinstance(raw, list) else [])
        print(f"- parsed candles: {len(rows)}; newest: {rows[-1] if rows else None}; age of the newest: {int(time.time() - rows[-1][0]) if rows else '?'} s")
        await adapter.watch([sym])
        got = 0
        end = time.time() + seconds
        first = None
        while time.time() < end:
            batch = await adapter.ticks()
            got += len(batch)
            first = first or (batch[0] if batch else None)
        raw_px = await adapter.client.get_realtime_price(sym)
        show("get_realtime_price()", raw_px if not isinstance(raw_px, list) or len(raw_px) < 4 else raw_px[:2] + ["..."] + raw_px[-1:])
        print(f"- prices parsed in {seconds:.0f} s: {got}; first: {first}")
        print("\nSend this output (it contains no credentials) to have the parsers adjusted, then start the worker.")
        return 0 if got else 1
    finally:
        await adapter.close()
