"""Run a walk-forward sweep on the running backend and print the verdicts.

    .venv\\Scripts\\python tools\\sweep.py --all                       # every strategy you have, 1m, default holdout
    .venv\\Scripts\\python tools\\sweep.py --config tools\\sweep_example.json
    .venv\\Scripts\\python tools\\sweep.py --all --tfs 60 300 --label "weekly check"

The config file is the body of POST /api/sweeps (see app/sweep.py):
  {"candidates": [{"strategy_id": 1, "grid": {"gates.min_agree": [2, 3]}}], "tfs": [60], "holdout_fraction": 0.3}
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

VERDICT_HELP = {
    "pass": "survived the luck correction - paper-trade it",
    "promising": "both periods beat break-even, luck not ruled out",
    "overfit": "fit edge vanished in the holdout",
    "need_data": "too few trades to judge",
    "no_edge": "does not beat break-even",
    "no_trades": "never signalled",
    "error": "could not be evaluated",
}


def call(base: str, method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())


def pct(x):
    return "   -  " if x is None else f"{x * 100:5.1f}%"


def show(run: dict) -> None:
    res = run["result"]
    cfg = res["config"]
    print(f"\nSweep #{run['id']} {run['label'] or ''}  | timeframes {cfg['tfs'] or [60]}  | holdout = newest {cfg['holdout_fraction']:.0%} of the data"
          + (f"  | pairs paying >= {cfg['min_payout']:g}% ({cfg.get('pairs_used')} pairs)" if cfg.get("min_payout") else "  | all pairs, any payout"))
    print(f"Candidates now: {res['tried_now']}   ever tried: {res['tried_total']}   "
          f"best holdout z: {res['best_holdout_z']}   (pure luck alone reaches about z = {res['noise_max_z']} at this many tries)")
    print(f"Verdicts: {res['counts']}\n")
    rows = sorted(res["candidates"], key=lambda c: c["rank"])
    mtg = cfg.get("mtg_steps")
    if mtg:
        print(f"MARTINGALE: up to {mtg} step(s), stake x{cfg['mtg_mult']:g} per step. Units = the first stake. A strategy is judged on its AVERAGE PROFIT PER LADDER (>0 needed in both periods).")
        print(f"{'#':>3} {'verdict':9} {'fit n':>6} {'fit avg':>8} {'hold n':>6} {'hold avg':>8} {'flat avg':>8} {'lost':>6} {'worst':>6} {'hold z':>6} {'adj p':>6}  strategy")
    else:
        print(f"{'#':>3} {'verdict':9} {'fit n':>6} {'fit win':>7} {'hold n':>6} {'hold win':>8} {'break-even':>10} {'hold z':>6} {'adj p':>6} {'pairs+':>7}  strategy")
    for c in rows:
        f, h = c["fit"] or {}, c["holdout"] or {}
        pairs = "-" if c["pairs_tested"] is None else f"{c['pairs_above_breakeven']}/{c['pairs_tested']}"
        z = "-" if h.get("z") is None else f"{h['z']:+.1f}"
        adj = "-" if c["adj_p"] is None else f"{c['adj_p']:.2f}"
        if mtg:
            num = lambda x, d=3: "-" if x is None else f"{x:+.{d}f}"  # noqa: E731
            lost = "-" if h.get("lost_rate") is None else f"{h['lost_rate'] * 100:.0f}%"
            print(f"{c['rank']:>3} {c['verdict']:9} {f.get('settled', 0):>6} {num(f.get('mean')):>8} {h.get('settled', 0):>6} {num(h.get('mean')):>8} {num(h.get('flat_mean')):>8} "
                  f"{lost:>6} {num(h.get('worst'), 1):>6} {z:>6} {adj:>6}  {c['name'][:55]}")
            continue
        print(f"{c['rank']:>3} {c['verdict']:9} {f.get('settled', 0):>6} {pct(f.get('accuracy')):>7} {h.get('settled', 0):>6} {pct(h.get('accuracy')):>8} "
              f"{pct(h.get('breakeven')):>10} {z:>6} {adj:>6} {pairs:>7}  {c['name'][:60]}")
    print()
    for c in rows[:3]:
        print(f"  {c['name'][:50]}: {c['reason']}" + (f"  [~{c['trades_needed']} settled trades would confirm it if the fit rate is real]" if c.get("trades_needed") else ""))
    print("\nA 'pass' is evidence, not a promise: paper-trade it for a few hundred live signals before trusting it.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8787")
    ap.add_argument("--config", help="JSON file with the sweep body")
    ap.add_argument("--all", action="store_true", help="include every strategy in the library")
    ap.add_argument("--tfs", type=int, nargs="*", help="timeframes in seconds (default 60)")
    ap.add_argument("--holdout", type=float, help="holdout fraction (default 0.3)")
    ap.add_argument("--min-trades", type=int, help="minimum holdout trades before a verdict other than need_data")
    ap.add_argument("--min-payout", type=float, help="only pairs paying at least this many %% (e.g. 80) - low-payout pairs need unrealistic win rates")
    ap.add_argument("--mtg", type=int, help="score as a martingale ladder with this many steps (e.g. 2)")
    ap.add_argument("--mtg-mult", type=float, help="stake multiple per MTG step (default 2.2)")
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    body: dict = json.load(open(a.config, encoding="utf-8")) if a.config else {}
    if a.all:
        body["all_strategies"] = True
    if a.tfs:
        body["tfs"] = a.tfs
    if a.holdout:
        body["holdout_fraction"] = a.holdout
    if a.min_trades:
        body["min_trades"] = a.min_trades
    if a.min_payout:
        body["min_payout"] = a.min_payout
    if a.mtg:
        body["mtg_steps"] = a.mtg
    if a.mtg_mult:
        body["mtg_mult"] = a.mtg_mult
    if a.label:
        body["label"] = a.label
    if not body.get("candidates") and not body.get("all_strategies"):
        print("nothing to sweep: give --all or --config", file=sys.stderr)
        return 2
    try:
        run = call(a.base, "POST", "/api/sweeps", body)
        while run["status"] == "running":
            time.sleep(2)
            run = call(a.base, "GET", f"/api/sweeps/{run['id']}")
            print(f"\r  running... {run['progress']:.0%}", end="", flush=True)
    except Exception as e:                                   # noqa: BLE001
        print(f"sweep failed: {e}", file=sys.stderr)
        return 1
    if run["status"] != "done":
        print(f"\nsweep {run['status']}: {run.get('error')}", file=sys.stderr)
        return 1
    show(run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
