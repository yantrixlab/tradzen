"""Feeds synthetic Quotex-shaped data to a running backend (for UI/engine checks without Quotex).

  .venv\\Scripts\\python tools\\simulate.py --minutes 120          # fast: 5 simulated seconds per post
  .venv\\Scripts\\python tools\\simulate.py --realtime             # ~2 ticks/s in real time, runs until Ctrl+C
"""
import argparse
import json
import random
import time
import urllib.request

URL = "http://127.0.0.1:8787/ingest"


def post(items):
    req = urllib.request.Request(URL, data=json.dumps({"items": items}).encode(), headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()


def main():
    global URL
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=120)
    ap.add_argument("--realtime", action="store_true")
    ap.add_argument("--symbols", default="AUDNZD_otc,EURUSD_otc")
    ap.add_argument("--url", default=URL, help="the backend's /ingest address (use a separate test backend with its own QXS_DB, never your real one)")
    a = ap.parse_args()
    URL = a.url
    syms = a.symbols.split(",")
    rnd = random.Random(5)
    now = time.time() if a.realtime else 1_800_000_000.0
    t0 = int(now // 60 * 60)
    price = {s: 1.2 + 0.05 * i for i, s in enumerate(syms)}

    inst = [[s, s[:3] + "/" + s[3:6] + " (OTC)", "currency", 80 + 5 * i, True] for i, s in enumerate(syms)]
    inst += [["EURUSD", "EUR/USD", "currency", 20, False], ["GBPJPY_otc", "GBP/JPY (OTC)", "currency", 88, True]]
    post([{"k": "i", "list": inst}])

    for s in syms:                                         # 150 closed 1m candles + the forming one, like history/list/v2
        rows, p = [], price[s]
        for i in range(150, 0, -1):
            o = p
            pts = [o + rnd.gauss(0, 0.0004) for _ in range(14)]
            p = pts[-1]
            rows.append([t0 - i * 60, round(o, 5), round(p, 5), round(max(pts + [o]), 5), round(min(pts + [o]), 5), 14, t0 - i * 60 + 59.5])
        rows.append([t0, round(p, 5), round(p, 5), round(p, 5), round(p, 5), 2, now - (now - t0) + 1.0])
        price[s] = p
        post([{"k": "h", "asset": s, "rows": rows}, {"k": "sel", "asset": s, "period": 60}])

    ts, n = t0 + 2.0, 0
    limit = None if a.realtime else a.minutes * 12
    while limit is None or n < limit:
        items = []
        for s in syms:
            price[s] += rnd.gauss(0, 0.0003)
            items.append({"k": "t", "d": [[s, ts, round(price[s], 5), 1 if rnd.random() > .5 else 0]]})
            items.append({"k": "s", "d": [[s, rnd.randint(30, 95)]]}) if n % 20 == 0 else None
        post([i for i in items if i])
        ts += 0.5 if a.realtime else 5.0
        n += 1
        time.sleep(0.5 if a.realtime else 0.02)
    print("done, ticks per symbol:", n)


if __name__ == "__main__":
    main()
