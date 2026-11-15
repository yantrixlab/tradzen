"""Real-market candles for the AI to learn from.

Why: the Quotex OTC pairs are priced by the broker and behave close to a fair coin, so the AI has nothing to learn there. The regular (live-market) pairs follow the
real forex market, but they are only recorded while a Quotex tab shows them and the market is open. This module adds 1-minute candles of the same pairs from a free
public source (Yahoo Finance's chart endpoint, no account or key) and stores them next to the Quotex data under the name `<PAIR>_mkt` (e.g. EURUSD_mkt), so they
never mix with, or overwrite, what the extension recorded. The AI advisor, the AI-built momentum strategy, the Edge Lab and the research pass all read every pair in
the candles table, so they pick these up on their next run; nothing else changes.

Limits, stated plainly: the source gives about 28 days of 1-minute history and nothing before it, so the table fills up as this keeps running; the prices are the
interbank market's, not Quotex's own quote (a few pips apart at times); and Yahoo may change or stop its endpoint - then a refresh reports the error and the rest keeps working.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SUFFIX = "_mkt"
TF = 60
SOURCE = "yahoo"
KEEP_DAYS = 28                       # the source allows 1-minute data for the last 30 days, 8 days per request
WINDOW = 7 * 86400
REFRESH_EVERY = 6 * 3600
# The live-market forex pairs Quotex offers (indices and metals are left out: the source has no matching spot quote for them).
PAIRS = ("AUDCAD", "AUDCHF", "AUDJPY", "AUDUSD", "CADJPY", "CHFJPY", "EURAUD", "EURCAD", "EURCHF", "EURGBP",
         "EURJPY", "EURUSD", "GBPAUD", "GBPCAD", "GBPCHF", "GBPJPY", "GBPUSD", "USDCAD", "USDCHF", "USDJPY")
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&period1={p1}&period2={p2}"


def stored_symbol(pair: str) -> str:
    return pair + SUFFIX


def payout_ok(sym: str, payouts: dict, min_payout: float, unknown: float = 85.0) -> bool:
    """May this pair's candles be used for learning? Market-data pairs always; Quotex pairs when they pay at least `min_payout`.
    A closed live market shows a placeholder payout of 20 %, which says nothing about the pair, so a payout under 50 is treated as a normal 85 %.
    A pair with no payout on record at all counts as `unknown` (0 = leave it out)."""
    if sym.endswith(SUFFIX):
        return True
    p = payouts.get(sym)
    if not p:
        return unknown >= min_payout
    p = float(p)
    return (85.0 if p < 50 else p) >= min_payout


def parse_chart(doc: dict, p1: int, p2: int) -> list[tuple]:
    """(ts, o, h, l, c, volume) of a Yahoo chart answer, only the candles inside [p1, p2) and with every price present (the answer ends with a stray 'now' row)."""
    res = (doc.get("chart") or {}).get("result")
    if not res:
        err = (doc.get("chart") or {}).get("error") or {}
        raise RuntimeError(err.get("description") or "no data")
    r = res[0]
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    out = []
    for i, t in enumerate(r.get("timestamp") or []):
        try:
            o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        except (KeyError, IndexError):
            continue
        if None in (o, h, l, c) or not p1 <= t < p2 or t % 60:
            continue
        h, l = max(h, o, c), min(l, o, c)
        v = q.get("volume", [None] * (i + 1))[i] if q.get("volume") else None
        out.append((int(t), float(o), float(h), float(l), float(c), int(v or 0)))
    return out


def fetch_window(pair: str, p1: int, p2: int, timeout: float = 25.0) -> list[tuple]:
    req = urllib.request.Request(URL.format(ticker=urllib.parse.quote(pair + "=X"), p1=p1, p2=p2), headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            doc = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:                       # Yahoo answers a refused request with a JSON body too
        try:
            doc = json.loads(e.read().decode("utf-8"))
        except Exception:                                      # noqa: BLE001
            raise RuntimeError(f"HTTP {e.code}") from e
    return parse_chart(doc, p1, p2)


def collect(conn, pairs=PAIRS, now: float | None = None, fetch=fetch_window, pause: float = 0.4, progress=None) -> dict:
    """Fetch what is missing for every pair and store it (INSERT OR IGNORE: running it twice changes nothing). Returns what happened, per pair."""
    now = int(now if now is not None else time.time())
    report = {"started": now, "pairs": {}, "added": 0, "errors": []}
    for k, pair in enumerate(pairs):
        sym = stored_symbol(pair)
        last = conn.execute("SELECT MAX(ts) FROM candles WHERE symbol=? AND tf=?", (sym, TF)).fetchone()[0]
        start = max(now - KEEP_DAYS * 86400, (last - 3600) if last else 0)
        added, p1 = 0, start
        while p1 < now:
            p2 = min(p1 + WINDOW, now + 60)
            try:
                rows = fetch(pair, p1, p2)
            except Exception as e:                             # noqa: BLE001 - one bad pair or window must not stop the rest
                report["errors"].append(f"{pair}: {e}")
                break
            before = conn.total_changes
            conn.executemany("INSERT OR IGNORE INTO candles (symbol, tf, ts, o, h, l, c, ticks, source) VALUES (?,?,?,?,?,?,?,?,?)",
                             [(sym, TF, t, o, h, l, c, v, SOURCE) for t, o, h, l, c, v in rows])
            conn.commit()
            added += conn.total_changes - before
            p1 = p2
            if pause:
                time.sleep(pause)
        total, t0, t1 = conn.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM candles WHERE symbol=? AND tf=?", (sym, TF)).fetchone()
        report["pairs"][pair] = {"added": added, "candles": total, "first": t0, "last": t1}
        report["added"] += added
        if progress:
            progress((k + 1) / len(pairs))
    report["finished"] = int(time.time())
    return report


class MarketData:
    """Runs `collect` in its own thread (its own database connection), at start-up and then every few hours, or on demand."""

    def __init__(self, db_path: str | Path, enabled: bool = True) -> None:
        self.db_path, self.enabled = db_path, enabled
        self.lock = threading.Lock()
        self.running = False
        self.progress = 0.0
        self.last: dict | None = None
        self.last_run = 0.0
        self.error: str | None = None

    def tick(self) -> None:
        if self.enabled and not self.running and time.time() - self.last_run >= REFRESH_EVERY:
            self.start()

    def start(self) -> bool:
        with self.lock:
            if self.running:
                return False
            self.running, self.progress, self.error = True, 0.0, None
        threading.Thread(target=self._run, name="marketdata", daemon=True).start()
        return True

    def _run(self) -> None:
        from .db import open_db
        conn = None
        try:
            conn = open_db(self.db_path)
            conn.execute("PRAGMA busy_timeout=30000")
            self.last = collect(conn, progress=lambda p: setattr(self, "progress", round(p, 3)))
            if self.last["errors"] and not self.last["added"]:
                self.error = self.last["errors"][0]
        except Exception as e:                                 # noqa: BLE001
            self.error = str(e)
        finally:
            if conn is not None:
                conn.close()
            self.last_run = time.time()
            self.running = False

    def status(self) -> dict:
        return {"enabled": self.enabled, "running": self.running, "progress": self.progress, "error": self.error, "last_run": self.last_run or None,
                "refresh_every_hours": REFRESH_EVERY / 3600, "source": "Yahoo Finance (free public chart data)", "keep_days": KEEP_DAYS,
                "last": self.last and {"added": self.last["added"], "errors": self.last["errors"][:5],
                                       "pairs": {p: v for p, v in self.last["pairs"].items()}}}
