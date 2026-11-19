"""Spike: can the SERVER fetch Quotex's candles itself (no user tab), and are they complete and fresh?

It uses the unofficial open-source client PyQuotex (https://github.com/cleitonleonel/pyquotex) with ONE dedicated Quotex account. Nothing here is wired into the
live engine: it only reads, compares with the candles Tradzen already stores (read-only) and writes a report.

Setup (do this yourself, in a SEPARATE virtualenv; PyQuotex needs Python 3.12+ and is third-party code, so read it before you install it):
    py -3.13 -m venv .venv-spike
    .venv-spike\\Scripts\\pip install git+https://github.com/cleitonleonel/pyquotex.git
Credentials go in environment variables of YOUR shell (never in a file, never in chat):
    $env:QX_EMAIL = "..."; $env:QX_PASSWORD = "..."
Run from backend/:
    .venv-spike\\Scripts\\python tools\\quotex_spike.py --pairs EURUSD_otc,EURJPY_otc --minutes 90 [--account PRACTICE] [--host quotex.com]
    .venv-spike\\Scripts\\python tools\\quotex_spike.py --selftest        (no network, no account: checks the comparison code)

What the report answers (data/spike/quotex-<time>.json, also printed):
  shape      the raw rows the library returns (the docs do not say), so the parser can be fixed from facts
  freshness  how old the newest candle is, and whether the running minute is included (the hole we are after is the newest few minutes)
  coverage   candles returned vs candles the database holds for the same minutes: missing on either side
  agreement  where both have a minute, do open / high / low / close match
  session    seconds to connect, and whether a second connect (a restart) reuses the saved session
Keep it to a handful of pairs and a few requests: the library's own docs warn that heavy parallel fetching can get an account banned.
"""
import argparse
import asyncio
import json
import math
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE.parent / "data" / "signals.db"
OUT = HERE.parent / "data" / "spike"
TOL = 2e-5                                     # relative price difference that still counts as "the same price" (float noise, 5th decimal)


# ---------------------------------------------------------------- parsing what the library returns (its shape is not documented)
def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _secs(t: float) -> int:
    return int(t / 1000) if t > 1e11 else int(t)


def _from_dict(r: dict):
    def pick(*names):
        for n in names:
            if n in r and _num(r[n]) is not None:
                return _num(r[n])
        return None
    t = pick("time", "t", "from", "timestamp", "ts")
    o, h, l, c = pick("open", "o"), pick("high", "h", "max"), pick("low", "l", "min"), pick("close", "c")
    if None in (t, o, h, l, c):
        return None
    return {"t": _secs(t), "o": o, "h": h, "l": l, "c": c}


def normalize(rows) -> tuple[list[dict], str]:
    """Rows from the library -> [{t,o,h,l,c}] sorted by time, plus which layout was recognised. Lists are tried as Quotex's own order [time, open, close, high, low, ...]
    and as [time, open, high, low, close, ...]; the one where high/low really bound open/close on most rows wins."""
    rows = list(rows or [])
    if not rows:
        return [], "empty"
    if all(isinstance(r, dict) for r in rows):
        out = [x for x in (_from_dict(r) for r in rows) if x]
        return sorted(out, key=lambda r: r["t"]), "dict"
    cand: dict[str, list[dict]] = {"list[t,o,c,h,l]": [], "list[t,o,h,l,c]": []}
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) < 5:
            continue
        v = [_num(x) for x in r[:5]]
        if None in v:
            continue
        t = _secs(v[0])
        cand["list[t,o,c,h,l]"].append({"t": t, "o": v[1], "c": v[2], "h": v[3], "l": v[4]})
        cand["list[t,o,h,l,c]"].append({"t": t, "o": v[1], "h": v[2], "l": v[3], "c": v[4]})
    best, best_ok = "", -1
    for name, lst in cand.items():
        ok = sum(1 for r in lst if r["h"] >= max(r["o"], r["c"]) - 1e-12 and r["l"] <= min(r["o"], r["c"]) + 1e-12)
        if ok > best_ok:
            best, best_ok = name, ok
    return sorted(cand[best], key=lambda r: r["t"]), best


# ---------------------------------------------------------------- comparing with what the database holds
def stored(db_path: Path, sym: str, t0: int, t1: int, tf: int = 60) -> dict[int, dict]:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {ts: {"t": ts, "o": o, "h": h, "l": l, "c": c} for ts, o, h, l, c in db.execute(
            "SELECT ts, o, h, l, c FROM candles WHERE symbol=? AND tf=? AND ts BETWEEN ? AND ? AND source NOT LIKE 'yahoo%'", (sym, tf, t0, t1))}
    finally:
        db.close()


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= TOL * max(abs(a), abs(b), 1e-12)


def compare(got: list[dict], have: dict[int, dict], now: float, tf: int = 60) -> dict:
    """The facts: freshness of the newest candle, minutes only one side has, and disagreements where both have the minute."""
    mine = {r["t"]: r for r in got}
    out: dict = {"returned": len(got), "stored": len(have)}
    if got:
        newest = got[-1]["t"]
        out["newest"] = newest
        out["newest_is_running"] = newest + tf > now                   # the minute still in progress is part of the answer
        out["newest_closed_age_s"] = round(now - (newest + tf if newest + tf <= now else newest))   # how long ago the last CLOSED candle ended
        lo, hi = got[0]["t"], newest
        out["span_minutes"] = (hi - lo) // tf + 1
        out["holes_in_returned"] = [t for t in range(lo, hi + 1, tf) if t not in mine][:20]
    both = sorted(set(mine) & set(have))
    out["in_both"] = len(both)
    window = [t for t in have if got and got[0]["t"] <= t <= got[-1]["t"]]
    out["only_in_quotex"] = len([t for t in mine if t not in have and (not have or min(have) <= t)])
    out["only_in_database"] = len([t for t in window if t not in mine])
    bad = []
    for t in both:
        a, b = mine[t], have[t]
        diff = [k for k in "ohlc" if not _same(a[k], b[k])]
        if diff:
            bad.append({"t": t, "fields": diff, "quotex": [a[k] for k in "ohlc"], "database": [b[k] for k in "ohlc"]})
    out["mismatches"] = len(bad)
    out["mismatch_examples"] = bad[:5]
    out["agreement_pct"] = round(100 * (len(both) - len(bad)) / len(both), 1) if both else None
    return out


# ---------------------------------------------------------------- the live part (needs the library and an account)
async def run(args) -> dict:
    email, password = os.environ.get("QX_EMAIL"), os.environ.get("QX_PASSWORD")
    if not email or not password:
        sys.exit("Set QX_EMAIL and QX_PASSWORD in your own shell first (see the top of this file). They are only read from the environment.")
    try:
        from pyquotex.stable_api import Quotex
    except ImportError:
        sys.exit("PyQuotex is not installed in this environment. See 'Setup' at the top of this file (a separate virtualenv, Python 3.12+).")
    report: dict = {"at": int(time.time()), "host": args.host, "account": args.account, "pairs": {}, "session": {}}
    t_start = time.time()
    client = Quotex(email=email, password=password, lang="en", host=args.host)
    ok, msg = await client.connect()
    report["session"]["connect_seconds"] = round(time.time() - t_start, 1)
    report["session"]["connected"] = bool(ok)
    report["session"]["message"] = str(msg)[:200]
    if not ok:
        return report
    try:
        client.set_account_mode(args.account)
    except Exception as e:                                           # the docs and the README disagree on this call: note it, carry on
        report["session"]["account_mode_error"] = repr(e)[:200]
    for sym in args.pairs:
        entry: dict = {}
        report["pairs"][sym] = entry
        try:
            now = time.time()
            raw = await client.get_candles(sym, int(now), args.minutes * 60, 60)
        except Exception as e:
            entry["error"] = repr(e)[:300]
            continue
        entry["raw_type"] = type(raw).__name__
        sample = raw if isinstance(raw, list) else list(raw.items())[:2] if isinstance(raw, dict) else [raw]
        entry["raw_first_rows"] = json.loads(json.dumps(sample[:2], default=str))
        entry["raw_last_row"] = json.loads(json.dumps(sample[-1:], default=str))
        got, layout = normalize(raw if isinstance(raw, list) else (raw.get("candles") or raw.get("data") or []) if isinstance(raw, dict) else [])
        entry["layout"] = layout
        if got:
            entry.update(compare(got, stored(DB, sym, got[0]["t"], got[-1]["t"]), now))
        await asyncio.sleep(2)                                       # slow and polite: a spike, not a crawler
    try:
        await client.close()
    except Exception as e:
        report["session"]["close_error"] = repr(e)[:200]
    t2 = time.time()                                                 # a restart: does it log in again, or reuse the saved session?
    client2 = Quotex(email=email, password=password, lang="en", host=args.host)
    ok2, msg2 = await client2.connect()
    report["session"]["reconnect_seconds"] = round(time.time() - t2, 1)
    report["session"]["reconnected"] = bool(ok2)
    try:
        await client2.close()
    except Exception:
        pass
    return report


# ---------------------------------------------------------------- self test (no network)
def selftest() -> None:
    now = 1_794_400_000 // 60 * 60 + 25                           # 25 s into a minute
    run_t = now // 60 * 60
    times = [run_t - 60 * i for i in range(30, 0, -1)]            # 30 closed minutes, the newest ended 0 s ago
    px = lambda i: 100 + i * 0.01                                  # noqa: E731
    quotex_order = [[t, px(i), px(i) + 0.004, px(i) + 0.008, px(i) - 0.003, 90] for i, t in enumerate(times)]
    quotex_order.append([run_t, 101.0, 101.001, 101.002, 100.999, 12])          # the running minute is in the answer
    got, layout = normalize(quotex_order)
    assert layout == "list[t,o,c,h,l]" and len(got) == 31, layout
    ohlc_order = [[t, px(i), px(i) + 0.008, px(i) - 0.003, px(i) + 0.004] for i, t in enumerate(times)]
    assert normalize(ohlc_order)[1] == "list[t,o,h,l,c]"
    ms = [{"time": t * 1000, "open": 1, "high": 2, "low": 0.5, "close": 1.5} for t in times[:3]]
    assert [r["t"] for r in normalize(ms)[0]] == times[:3], "millisecond times are read as seconds"
    d = Path(tempfile.mkdtemp()) / "t.db"
    db = sqlite3.connect(d)
    db.execute("CREATE TABLE candles (symbol TEXT, tf INTEGER, ts INTEGER, o REAL, h REAL, l REAL, c REAL, ticks INTEGER, source TEXT)")
    for i, t in enumerate(times):
        if i in (27, 28):                                          # the database misses two minutes just before the running one
            continue
        row = got[i]
        c = row["c"] + (0.5 if i == 5 else 0)                      # and disagrees on one close
        db.execute("INSERT INTO candles VALUES ('X', 60, ?, ?, ?, ?, ?, 90, 'tick')", (t, row["o"], row["h"], row["l"], c))
    db.commit()
    db.close()
    rep = compare(got, stored(d, "X", got[0]["t"], got[-1]["t"]), now)
    assert rep["returned"] == 31 and rep["newest_is_running"] is True and rep["newest_closed_age_s"] == 25, rep
    assert rep["only_in_quotex"] == 3 and rep["only_in_database"] == 0 and rep["mismatches"] == 1 and rep["in_both"] == 28, rep
    assert rep["holes_in_returned"] == [] and rep["span_minutes"] == 31
    late = compare(got[:-6], stored(d, "X", got[0]["t"], got[-1]["t"]), now)     # an answer that stops 6 minutes early
    assert late["newest_is_running"] is False and late["newest_closed_age_s"] == 325, late
    print("quotex_spike self test passed")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pairs", default="EURUSD_otc,EURJPY_otc")
    ap.add_argument("--minutes", type=int, default=90, help="how far back to ask (one request per pair)")
    ap.add_argument("--account", default="PRACTICE", choices=["PRACTICE", "REAL"])
    ap.add_argument("--host", default="quotex.com")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    args.pairs = [p.strip() for p in args.pairs.split(",") if p.strip()][:6]
    rep = asyncio.run(run(args))
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"quotex-{rep['at']}.json"
    path.write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    print(json.dumps(rep, indent=2, default=str))
    print("\nreport saved:", path)


if __name__ == "__main__":
    main()
