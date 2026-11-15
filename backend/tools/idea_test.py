"""One-command test of a trading idea - the same honest battery every time, so a result can be compared with every earlier one.

  idea  spec  examples\\my-idea.json                      test a strategy spec file
  idea  expr  --call "<expr>" --put "<expr>"            test an idea written as two conditions (no file needed)
  idea  model                                           the model bake-off (LightGBM / CNN / GRU / Transformer on the next candle)
  idea  ledger                                          every idea tried so far and how each did

Options: --name NAME  --tfs 60 300 900  --min-payout 85  --install   (--install creates a PAPER strategy if the idea passed or looks promising)

Expressions can use: open high low close body range upper_wick lower_wick green red streak hour, patterns hammer star doji bull_engulf bear_engulf inside_bar, and the ready-made
indicators  e9 e21 e50 (EMAs)  a14 (ATR)  r14 (RSI)  ad (ADX: ad.adx ad.pdi ad.mdi)  mc (MACD: mc.line mc.signal mc.hist)  bb (Bollinger: bb.lower bb.mid bb.upper bb.pctb)
st (Stochastic: st.k st.d)  ms (market structure: ms.bias ms.trend ms.sh ms.sl).   Example:
  idea expr --call "ms.bias == 1 and green and close > e9 and ad.adx > 22" --put "ms.bias == -1 and red and close < e9 and ad.adx > 22"

THE BATTERY (fixed in advance - an idea cannot pick the test it passes):
  * every pair paying >= min-payout, every chosen timeframe, entry at the next open, settle at its close (exactly like live);
  * the trades are cut by TIME into an older 70 % and a newest 30 % that the idea never "saw", and into 5 time blocks; and by PAIR into two halves;
  * PASS needs ALL of: >= 1,500 trades; the newest 30 % above break-even with the 95 % range clear of it; the older 70 % above break-even; at least 4 of 5 time blocks
    above break-even; both pair halves above break-even;
  * the 95 % range is widened for the number of ideas tried in the last 90 days (the ledger): the more ideas you try, the more likely one looks good by luck, so proof gets harder;
  * PROMISING = newest 30 % above break-even but not proven; everything else FAILS, and says by how much.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sqlite3
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

DB = BACKEND / "data" / "signals.db"
from app.battery import BLOCKS, IND, LEDGER, MIN_TRADES, OUT, _seg, evaluate, ideas_tried, judge, spec_from_expr  # noqa: E402,F401


def ai_momentum_trades(conn, tf: int = 60, min_payout: float = 85.0):
    """Replay the AI-built momentum strategy as trades. Its rules are learned ONLY from candles before the battery's 70 % time cut, then applied to every candle of every
    pair: the newest 30 % are candles the AI never saw. (The older 70 % is in-sample for it - the newest 30 % is the real test.)"""
    import pandas as pd
    from app import momentum_ai as M
    payouts = {s: p for s, p in conn.execute("SELECT symbol, payout FROM instruments WHERE payout IS NOT NULL")}
    X, y, t, sym, d = M.build_pool(conn, tf, payouts, min_payout=min_payout, max_rows=10 ** 9, meta=True)
    if len(y) < M.MIN_ROWS:
        raise ValueError(f"only {len(y):,} candles with a measurable momentum leg - collect more history first")
    cut = np.quantile(t, 0.7)
    tr = t < cut
    leaves = M._grow(X[tr], y[tr], M.DEPTH, [], M.MIN_LEAF)
    follow = np.zeros(len(y), bool)
    seen = np.zeros(len(y), bool)
    for lf in leaves:
        m = M._in_leaf(X, lf["path"])
        follow[m] = float(y[tr & m].mean()) >= 0.5 if (tr & m).any() else True
        seen |= m
    win = np.where(follow, y == 1.0, y == 0.0)
    pay = np.array([float(payouts.get(s) or 85.0) for s in sym])
    trade_dir = np.where(follow, d, -d)
    return pd.DataFrame({"t": t.astype(np.int64), "symbol": sym, "dir": trade_dir.astype(int), "result": np.where(win, "win", "loss"), "payout": pay,
                         "profit": np.where(win, pay / 100, -1.0)})[seen]


def render(res: dict) -> str:
    o = res["overall"]
    lines = [f"IDEA  {res['name']}   [{res['kind']}]   {res['seconds']}s", "",
             f"  VERDICT: {res['verdict']}  - {res.get('why', '')}", ""]
    if o["n"]:
        lines.append(f"  overall   {o['n']:7,} trades   {o['win'] * 100:5.1f}% win   break-even {o['be'] * 100:4.1f}%   expectancy {o['expectancy']:+.3f} per unit   "
                     f"(luck alone: {res['noise_band'][0] * 100:.1f}-{res['noise_band'][1] * 100:.1f}%)" if "noise_band" in res else f"  overall {o['n']} trades")
    for key, label in (("older", "older 70%"), ("newest", "newest 30%")):
        if key in res:
            s = res[key]
            lines.append(f"  {label:10s}{s['n']:7,} trades   {s['win'] * 100:5.1f}% win   95% range {s['lo'] * 100:.1f}-{s['hi'] * 100:.1f}%   break-even {s['be'] * 100:.1f}%")
    if "by_direction" in res:
        lines.append("  by side   " + "   ".join(f"{k} {v['win'] * 100:.1f}% of {v['n']:,}" for k, v in res["by_direction"].items() if v["n"]))
    lines += ["", "  checks:"] + [f"   [{'x' if c['ok'] else ' '}] {c['name']}: {c['text']}" for c in res["checks"]]
    lines += ["", f"  ideas counted in the last 90 days: {res['ideas_counted']} (95% range widened to z={res['z']:.2f})"]
    return "\n".join(lines)


def save_report(res: dict) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", res["name"].lower()).strip("-") or "idea"
    p = OUT / f"{dt.datetime.now(dt.timezone.utc):%Y%m%d-%H%M}-{slug}.json"
    p.write_text(json.dumps({k: v for k, v in res.items()}, indent=1, default=str))
    return p


def install_paper(res: dict, name: str, host: str = "http://127.0.0.1:8787") -> str:
    key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:36] or "idea"
    key = "idea-" + key if not key.startswith("idea") else key
    body = json.dumps({"key": key, "name": f"IDEA: {name}", "description": f"Created by the idea test ({res['verdict']}): {res.get('why', '')}", "spec": res["spec"], "status": "paper"}).encode()
    req = urllib.request.Request(host + "/api/strategies", data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return f"installed as paper strategy '{key}' (id {json.loads(r.read())['id']})"


def show_ledger() -> str:
    if not LEDGER.exists():
        return "No ideas tested yet."
    rows = [json.loads(x) for x in LEDGER.read_text().splitlines() if x.strip()]
    out = [f"{'when (UTC)':16s} {'verdict':10s} {'trades':>7s} {'win':>6s} {'BE':>6s} {'newest':>7s}  idea"]
    for r in rows:
        out.append(f"{dt.datetime.fromtimestamp(r['t'], dt.timezone.utc):%Y-%m-%d %H:%M} {r['verdict']:10s} {r['trades']:7,} {(r['win'] or 0) * 100:5.1f}% {(r['be'] or 0) * 100:5.1f}% "
                   f"{'' if r['newest'] is None else format(r['newest'] * 100, '6.1f') + '%':>7s}  {r['name']} [{r['kind']}]")
    out.append(f"\n{len(rows)} ideas tried in total; the more that are tried, the more a lucky result is expected - that is why proof gets harder with every entry.")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="One-command honest test of a trading idea", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("mode", choices=["spec", "expr", "model", "ledger", "ai-momentum"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--call"), ap.add_argument("--put")
    ap.add_argument("--name"), ap.add_argument("--tfs", type=int, nargs="*", default=[60, 300, 900])
    ap.add_argument("--min-payout", type=float, default=85.0)
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--tf", type=int, default=60, help="timeframe for ai-momentum (60, 300 or 900 seconds)")
    ap.add_argument("--db", default=str(DB))
    a = ap.parse_args(argv)
    if a.mode == "ledger":
        print(show_ledger())
        return 0
    if a.mode == "model":
        py = BACKEND / ".venv-ml" / "Scripts" / "python.exe"
        return subprocess.call([str(py), str(BACKEND / "tools" / "ml_bakeoff.py"), "--out", str(OUT / "bakeoff.json")])
    if a.mode == "ai-momentum":
        conn = sqlite3.connect(f"file:{Path(a.db).as_posix()}?mode=ro", uri=True, timeout=60)
        t0 = time.time()
        trades = ai_momentum_trades(conn, a.tf, a.min_payout)
        res = judge(trades, ideas_tried(LEDGER))
        res.update(name=a.name or f"AI momentum {a.tf // 60}m (learned rule, 70/30 time split)", kind="ai-momentum", seconds=round(time.time() - t0, 1), trades_total=int(len(trades)), spec=None,
                   when=time.time())
        with open(LEDGER, "a", encoding="utf-8") as fh:
            OUT.mkdir(parents=True, exist_ok=True)
            fh.write(json.dumps({"t": time.time(), "name": res["name"], "kind": "ai-momentum", "trades": res["overall"]["n"], "win": res["overall"]["win"], "be": res["overall"]["be"],
                                 "newest": (res.get("newest") or {}).get("win"), "verdict": res["verdict"]}) + "\n")
        print(render(res))
        print("\n  report:", save_report(res))
        return 0 if res["verdict"] != "FAIL" else 1
    if a.mode == "spec":
        if not a.file:
            ap.error("spec needs a file: idea spec examples\\my-idea.json")
        spec = json.loads(Path(a.file).read_text())
        name = a.name or Path(a.file).stem
    else:
        if not (a.call and a.put):
            ap.error("expr needs --call and --put")
        spec, name = spec_from_expr(a.call, a.put, a.tfs, a.min_payout), a.name or "expr idea"
    conn = sqlite3.connect(f"file:{Path(a.db).as_posix()}?mode=ro", uri=True, timeout=60)
    res = evaluate(conn, spec, {"tfs": a.tfs}, LEDGER, name, a.mode)
    print(render(res))
    print("\n  report:", save_report(res))
    if a.install:
        if res["verdict"] in ("PASS", "PROMISING"):
            print(" ", install_paper(res, name))
        else:
            print("  not installed: only ideas that PASS or look PROMISING are installed (paper).")
    return 0 if res["verdict"] != "FAIL" else 1


if __name__ == "__main__":
    sys.exit(main())
