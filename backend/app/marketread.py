"""Market read - what a trader reads off a candle chart by eye, worked out candle by candle for the chart: the swings (HH / HL / LH / LL, down to single candles),
the breaks of structure (BOS = the trend continues, CHoCH = the trend changes), where supply / demand really sits, and who is in control (buyers or sellers).
It is a DRAWING and READING aid for the chart (nothing here trades); the strategy indicators in structure.py / engulf.py / zones.py are untouched.

SWINGS   a swing high (low) is a candle whose high (low) is the extreme of the candle on each side (`k` = 1: a single candle can be a swing, so a one-candle
         pull-back inside a trend gets its higher low / lower high). Swings alternate (two highs in a row keep the higher one). Each is labelled against the swing
         of its own kind before it: HH / LH (high higher / lower), HL / LL (low higher / lower). `major` = it is also the extreme of 3 candles on each side.
         A swing is only KNOWN `k` candles after it formed; everything below uses it from then on (no look-ahead).

STRUCTURE  trend up: a CLOSE above the newest swing high is a BOS (continuation); trend down: a close below the newest swing low. The trend CHANGES (CHoCH) only when
         a candle CLOSES beyond the PROTECTED level: in an up-trend the higher low that started the last push (a close below it), in a down-trend the lower high that started
         the last push. A wick through the level that closes back is a sweep (a grab of stop orders), not a change. A break is `strong` when its candle has a real
         body (>= 0.6 ATR) or closes clearly beyond the level (>= 0.25 ATR) - that is the confirmation.

ZONES    supply / demand is marked only where the market proved it:
           * at a SWING (HH, LH, LL, HL) that price then left by at least 0.8 ATR within 5 candles ("swing" zone: the swing candle's wick + body);
           * a SWING ENGULF: two or more candles of one colour (a swing leg) with no opposite candle inside, then candles of the other colour - again with no opposite
             candle inside - that CLOSE beyond the whole leg. The zone is the full high-low range of the leg ("swing_engulf"); a single candle engulfed counts when the move is
             strong (>= 1.2 ATR, "engulf");
           * a strong base-and-departure zone (>= 1.8 ATR) that no swing / engulf zone already covers ("base").
         A zone stays until a candle CLOSES through it (above a supply zone, below a demand zone); every visit back into it is a touch.

FLOW     who is in control: a score from -100 (sellers dominating) to +100 (buyers dominating) from six readings of the candles (this feed has no volume, so the
         pressure is read from the candles themselves): structure (trend, BOS streak, a fresh confirmed shift), candle pressure (where candles close inside their range,
         weighted by size), body balance, efficiency of the last 12 candles, impulse vs pull-back legs, and the zones price sits in / runs into, plus rejection wicks.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind
from . import zones as zones_mod

MICRO_K, MAJOR_K = 1, 3
WEIGHTS = {"structure": 0.28, "pressure": 0.16, "body": 0.14, "efficiency": 0.14, "legs": 0.10, "zones": 0.10, "rejection": 0.08}
COMPONENT_NAMES = {"structure": "Structure", "pressure": "Candle pressure", "body": "Body balance", "efficiency": "Momentum", "legs": "Impulse vs pull-back", "zones": "Zones", "rejection": "Wick rejection"}
SWING_LABELS = ("HH", "LH", "HL", "LL")

# Measured by tools/marketread_eval.py (run it again after any change to the weights): 17 OTC pairs, 1-minute candles, the 49,268 newest candles the weights were never tuned on.
# Shown beside the reading so nobody takes it for a promise: it says who is in control NOW; it is not a forecast (on these feeds a forecast is a coin flip).
ACCURACY: dict = {
    "sample": "17 OTC pairs, 1-minute candles, 49,268 candles", "date": "2026-11-19",
    "next_candle": 46.6, "after_5": 48.7, "after_10": 49.7,              # % of readings where price afterwards went the way the reading favoured (50 = a coin flip)
    "strong_break_follow": 40.5, "weak_break_follow": 16.2,              # % of breaks of structure that ran 1.5 ATR before a close back through the level: confirmed vs not
    "stretched_follow": 8.1,                                             # % of |score| >= 60 readings that got 1 ATR further before 1 ATR of pull-back
}


def _atr(df: pd.DataFrame) -> np.ndarray:
    a = ind.atr(df["high"], df["low"], df["close"], 14)
    rng = (df["high"] - df["low"]).rolling(14, min_periods=1).mean()
    return a.fillna(rng).bfill().clip(lower=1e-9).to_numpy(float)


def fractals(o, h, l, c, k: int) -> list[tuple[int, str, float]]:
    """(candle index, 'high' | 'low', price) of every swing, in order. A swing is the extreme of `k` candles on each side; known at index + k."""
    n, out = len(h), []
    for j in range(k, n - k):
        ph = h[j] > h[j - k:j].max() and h[j] >= h[j + 1:j + k + 1].max()
        pl = l[j] < l[j - k:j].min() and l[j] <= l[j + 1:j + k + 1].min()
        if ph and pl:                                              # an outside candle is both: a green one went down first, a red one up first
            pair = [("low", l[j]), ("high", h[j])] if c[j] >= o[j] else [("high", h[j]), ("low", l[j])]
            out += [(j, kind, float(p)) for kind, p in pair]
        elif ph:
            out.append((j, "high", float(h[j])))
        elif pl:
            out.append((j, "low", float(l[j])))
    return out


def _alternate(hist: list, j: int, kind: str, p: float) -> bool:
    """Add a swing to `hist` keeping highs and lows alternating (the more extreme of two in a row wins). False = the swing was not kept."""
    if hist and hist[-1][1] == kind:
        if (kind == "high" and p > hist[-1][2]) or (kind == "low" and p < hist[-1][2]):
            hist[-1] = (j, kind, p)
            return True
        return False
    hist.append((j, kind, p))
    return True


def label_swings(raw: list, major: set) -> list[dict]:
    """The alternating swings of `raw` with HH / LH / HL / LL against the swing of the same kind before."""
    hist: list = []
    for j, kind, p in raw:
        _alternate(hist, j, kind, p)
    out, last = [], {"high": None, "low": None}
    for j, kind, p in hist:
        prev = last[kind]
        lab = "" if prev is None else (("HH" if p > prev else "LH" if p < prev else "EH") if kind == "high" else ("HL" if p > prev else "LL" if p < prev else "EL"))
        out.append({"i": j, "kind": kind, "price": p, "label": lab, "major": (j, kind) in major})
        last[kind] = p
    return out


def structure_pass(o, h, l, c, a, raw: list, k: int) -> dict:
    """Walks the candles once: trend, protected level, BOS / CHoCH events, the impulse-vs-pull-back leg balance. Uses a swing only from the candle it is confirmed on."""
    n = len(c)
    by_conf: dict[int, list] = {}
    for r in raw:
        by_conf.setdefault(r[0] + k, []).append(r)
    trend, prot, age, streak, legs_f = np.zeros(n), np.full(n, np.nan), np.zeros(n), np.zeros(n), np.zeros(n)
    ev_strong = np.zeros(n)                                       # +1 / -1: a strong break happened on this candle (direction), else 0
    events: list[dict] = []
    hist: list = []
    last_high = last_low = prot_hi = prot_lo = None               # (index, price)
    tr, since, bos_n = 0, 0, 0
    for i in range(n):
        for j, kind, p in by_conf.get(i, []):
            if not _alternate(hist, j, kind, p):
                continue
            if kind == "high":
                last_high = (j, p)
                prev_hi = [x for x in hist[:-1] if x[1] == "high"]
                if tr == 1 and len(hist) >= 2 and hist[-2][1] == "low" and (not prev_hi or p > prev_hi[-1][2]):      # a higher high: the low right before it is what protects the trend
                    prot_lo = (hist[-2][0], hist[-2][2])
                if tr == 1 and prot_lo is None and len(hist) >= 2 and hist[-2][1] == "low":
                    prot_lo = (hist[-2][0], hist[-2][2])
            else:
                last_low = (j, p)
                prev_lo = [x for x in hist[:-1] if x[1] == "low"]
                if tr == -1 and len(hist) >= 2 and hist[-2][1] == "high" and (not prev_lo or p < prev_lo[-1][2]):
                    prot_hi = (hist[-2][0], hist[-2][2])
                if tr == -1 and prot_hi is None and len(hist) >= 2 and hist[-2][1] == "high":
                    prot_hi = (hist[-2][0], hist[-2][2])
        brk = None
        if tr == -1 and prot_hi and c[i] > prot_hi[1] and i > prot_hi[0]:
            brk = ("CHoCH", 1, prot_hi)
        elif tr == 1 and prot_lo and c[i] < prot_lo[1] and i > prot_lo[0]:
            brk = ("CHoCH", -1, prot_lo)
        elif tr in (0, 1) and last_high and c[i] > last_high[1] and i > last_high[0]:
            brk = ("BOS", 1, last_high)
        elif tr in (0, -1) and last_low and c[i] < last_low[1] and i > last_low[0]:
            brk = ("BOS", -1, last_low)
        if brk:
            typ, d, lv = brk
            strong = abs(c[i] - o[i]) >= 0.6 * a[i] or abs(c[i] - lv[1]) >= 0.25 * a[i]
            events.append({"i": i, "j": lv[0], "level": float(lv[1]), "type": typ, "dir": d, "strong": bool(strong)})
            ev_strong[i] = d if strong else 0
            if d == 1:                                                # the new protected low: after a change, the lowest swing low since the old protected high (where the old push ended)
                lows = [x for x in hist if x[1] == "low" and x[0] > lv[0]] if typ == "CHoCH" else []
                prot_lo = (lambda m: (m[0], m[2]))(min(lows, key=lambda x: x[2])) if lows else (last_low or prot_lo)
                prot_hi = last_high = None
            else:
                highs = [x for x in hist if x[1] == "high" and x[0] > lv[0]] if typ == "CHoCH" else []
                prot_hi = (lambda m: (m[0], m[2]))(max(highs, key=lambda x: x[2])) if highs else (last_high or prot_hi)
                prot_lo = last_low = None
            bos_n = (bos_n + 1) if (typ == "BOS" and tr == d) else (1 if typ == "BOS" else 0)
            tr, since = d, 0
        else:
            since += 1
        trend[i] = tr
        p_lv = prot_lo if tr == 1 else prot_hi if tr == -1 else None
        prot[i] = p_lv[1] if p_lv else np.nan
        age[i], streak[i] = min(since, 999), bos_n
        # impulse vs pull-back: the last four completed legs + the one price is travelling now
        pts = [x[2] for x in hist[-5:]]
        if pts:
            legs = [pts[x + 1] - pts[x] for x in range(len(pts) - 1)] + [c[i] - pts[-1]]
            up, dn = sum(x for x in legs if x > 0), -sum(x for x in legs if x < 0)
            legs_f[i] = (up - dn) / (up + dn) if up + dn > 0 else 0.0
    return {"trend": trend, "prot": prot, "age": age, "streak": streak, "legs": legs_f, "ev_strong": ev_strong, "events": events}


def _runs(o, h, l, c) -> list[tuple[int, int, int]]:
    """Runs of candles of one colour: (colour, first, last). A doji (a body under 8% of its range) does not break a run; an opposite candle does."""
    col = np.sign(c - o)
    col[np.abs(c - o) < 0.08 * np.maximum(h - l, 1e-12)] = 0
    runs, cur = [], None
    for i in range(len(c)):
        if col[i] == 0:
            if cur:
                cur[2] = i
            continue
        if cur and cur[0] == col[i]:
            cur[2] = i
        else:
            if cur:
                runs.append(tuple(cur))
            cur = [int(col[i]), i, i]
    if cur:
        runs.append(tuple(cur))
    return runs


def _overlap(a: dict, b: dict) -> float:
    lo, hi = max(a["lo"], b["lo"]), min(a["hi"], b["hi"])
    return max(0.0, hi - lo) / max(1e-12, min(a["hi"] - a["lo"], b["hi"] - b["lo"]))


def build_zones(df: pd.DataFrame, o, h, l, c, a, swings: list[dict], st: dict) -> tuple[list[dict], list[dict]]:
    """Every supply / demand zone of the series with how it ended (see the module docstring for the kinds): (the zones to draw, the zones the buyers-vs-sellers reading uses)."""
    n = len(c)
    trend = st["trend"]
    cand: list[dict] = []

    # 1) swing zones: the swing candle where price then LEFT fast
    for s in swings:
        if s["label"] not in SWING_LABELS:
            continue
        j = s["i"]
        if j + 1 >= n:
            continue
        sup = s["kind"] == "high"
        m = None
        for x in range(j + 1, min(n, j + 6)):
            if (h[j] - c[x] if sup else c[x] - l[j]) >= 0.8 * a[j]:
                m = x
                break
        if m is None:
            continue
        rng = h[j] - l[j]
        if sup:                                                      # the wick + body of the swing candle (the whole candle when it is small)
            top = h[j]
            bot = min(max(l[j] if rng <= 0.9 * a[j] else min(o[j], c[j]), top - 1.5 * a[j]), top - 0.12 * a[j])
        else:
            bot = l[j]
            top = max(min(h[j] if rng <= 0.9 * a[j] else max(o[j], c[j]), bot + 1.5 * a[j]), bot + 0.12 * a[j])
        move = (h[j] - c[m] if sup else c[m] - l[j]) / a[j]
        cand.append({"src": "swing", "kind": "supply" if sup else "demand", "lo": float(bot), "hi": float(top), "start": j, "base": (j, j), "dep": (j + 1, m), "born": m,
                     "move": float(move), "tag": s["label"], "run": 1, "major": s["major"], "prio": 2})

    # 2) swing engulf: a leg of one colour, then the other colour closing beyond the whole leg
    runs = _runs(o, h, l, c)
    for r, g in zip(runs, runs[1:]):
        if r[0] != -g[0] or r[2] + 1 != g[1]:
            continue
        ra, rb, ga, ge = r[1], r[2], g[1], g[2]
        hi_r, lo_r = float(h[ra:rb + 1].max()), float(l[ra:rb + 1].min())
        demand = r[0] == -1                                          # red leg engulfed by green: demand
        m = next((x for x in range(ga, ge + 1) if (c[x] > hi_r if demand else c[x] < lo_r)), None)
        if m is None:
            continue
        start_ok = (o[ga] <= c[rb] + 0.3 * a[ga]) if demand else (o[ga] >= c[rb] - 0.3 * a[ga])
        move = abs(c[m] - o[ga]) / a[ga]
        n_leg = rb - ra + 1
        if not start_ok or move < 0.5 or (n_leg == 1 and move < 1.2):
            continue
        lo, hi = lo_r, hi_r
        if hi - lo > 2.5 * a[ga]:                                    # a long leg: the end of it where the turn happened
            lo, hi = (lo, lo + 2.5 * a[ga]) if demand else (hi - 2.5 * a[ga], hi)
        cand.append({"src": "swing_engulf" if n_leg >= 2 else "engulf", "kind": "demand" if demand else "supply", "lo": lo, "hi": hi, "start": ra, "base": (ra, rb), "dep": (ga, m),
                     "born": m, "move": float(move), "tag": f"SWING ×{n_leg}" if n_leg >= 2 else "ENGULF", "run": m - ga + 1, "major": False, "prio": 3 if n_leg >= 2 else 2})

    # 3) a strong base-and-departure zone nothing above already covers
    try:
        for r in zones_mod.lifecycle(df):
            if r["flip"] or r["move"] < 1.8:
                continue
            born = r["dep"][1] if r.get("dep") else r["start"]
            cand.append({"src": "base", "kind": r["kind"], "lo": float(r["lo"]), "hi": float(r["hi"]), "start": r["base"][0] if r.get("base") else r["start"],
                         "base": tuple(r["base"]) if r.get("base") else (r["start"], r["start"]), "dep": tuple(r["dep"]) if r.get("dep") else (born, born), "born": born,
                         "move": float(r["move"]), "tag": "BASE", "run": 1, "major": False, "prio": 1})
    except Exception:  # noqa: BLE001 - an extra source must never fail the chart
        pass

    # for the READING (it must not look ahead): in birth order, a zone is dropped only when an EARLIER one of its kind already sits on it
    causal: list[dict] = []
    for z in sorted(cand, key=lambda z: z["born"]):
        if not any(k["kind"] == z["kind"] and z["born"] - k["born"] <= 12 and _overlap(k, z) >= 0.5 for k in causal):
            causal.append(z)

    # for the CHART: zones of one kind that sit on top of each other, born close together, become one: the higher-priority one stays (and says what else confirmed it)
    kept: list[dict] = []
    for z in sorted(cand, key=lambda z: (-z["prio"], z["born"])):
        twin = next((k for k in kept if k["kind"] == z["kind"] and abs(k["born"] - z["born"]) <= 12 and _overlap(k, z) >= 0.5), None)
        if twin:
            if z["tag"] not in twin["tag"].split(" · ") and len(twin["tag"]) < 24:
                twin["tag"] += f" · {z['tag']}"
            twin["move"] = max(twin["move"], z["move"])
            twin["major"] = twin["major"] or z["major"]
            twin["bonus"] = twin.get("bonus", 0) + 5
            continue
        kept.append(dict(z))
    kept.sort(key=lambda z: z["born"])

    def lifecycle(zs: list[dict]) -> list[dict]:
        out: list[dict] = []
        for z in zs:
            born, sup = z["born"], z["kind"] == "supply"
            tail = c[born + 1:]
            hit = np.flatnonzero(tail > z["hi"]) if sup else np.flatnonzero(tail < z["lo"])
            end = int(hit[0]) + born + 1 if len(hit) else None
            stop = end if end is not None else n
            touches, inside = [], False
            for i in range(born + 1, stop):
                now = (h[i] >= z["lo"]) if sup else (l[i] <= z["hi"])
                if now and not inside:
                    touches.append(i)
                inside = now
            near = [e for e in st["events"] if born - 2 <= e["i"] <= born + 3 and e["dir"] == (-1 if sup else 1)]
            # `cscore` uses only what was known on the candle the zone was born; `score` (for the chart) adds what came after
            cscore = {"swing": 45, "swing_engulf": 52, "engulf": 42, "base": 38}[z["src"]] + min(25.0, 12 * z["move"]) + (6 * min(z["run"], 3) if z["src"] != "swing" else 0)                 + (10 if trend[born] == (-1 if sup else 1) else 0)
            score = cscore + (8 if z["major"] else 0) + (12 if near else 0) + z.get("bonus", 0)
            out.append({**z, "end": end, "touches": touches, "score": int(min(100, round(score))), "cscore": int(min(100, round(cscore))), "origin_of": (near[0]["type"] if near else None)})
        return out

    return lifecycle(kept), lifecycle(causal)


def flow_pass(o, h, l, c, a, st: dict, zones: list[dict]) -> dict:
    """The six readings (each -1..+1) and the score. Series aligned to the candles."""
    n = len(c)
    rng = np.maximum(h - l, 1e-12)
    clv = ((c - l) - (h - c)) / rng
    w = np.minimum(rng / a, 2.0)
    s = lambda x, k: pd.Series(x).rolling(k, min_periods=1).sum().to_numpy(float)       # noqa: E731
    pressure = s(w * clv, 8) / np.maximum(s(w, 8), 1e-9)
    body = c - o
    body_bal = s(body, 10) / np.maximum(s(np.abs(body), 10), 1e-12)
    path = s(np.abs(np.diff(c, prepend=c[0])), 12)
    net = c - pd.Series(c).shift(11).bfill().to_numpy(float)
    eff = np.where(path > 0, net / np.maximum(path, 1e-12), 0.0)
    rej = s(np.minimum(o, c) - l - (h - np.maximum(o, c)), 6) / np.maximum(s(rng, 6), 1e-12)
    tr = st["trend"]
    base = np.clip(0.55 + 0.12 * st["streak"], 0, 1)
    fresh = np.zeros(n)
    ev = st["ev_strong"]
    for i in np.flatnonzero(ev != 0):                                # a freshly confirmed strong break adds weight for a few candles
        fresh[i:i + 6] = np.maximum(fresh[i:i + 6], 0.15 * ev[i] * np.linspace(1, 0.4, len(fresh[i:i + 6])))
    structure = np.clip(tr * base + np.where(tr != 0, fresh, 0.0), -1, 1)
    pr = st["prot"]
    near_prot = ~np.isnan(pr) & (np.abs(c - np.nan_to_num(pr)) < 0.35 * a)                # the protected level is being tested: the trend is on the line
    structure = np.where(near_prot, structure * 0.7, structure)
    support, resist = np.zeros(n), np.zeros(n)
    for z in zones:
        end = z["end"] if z["end"] is not None else n
        if end <= z["born"] + 1:
            continue
        sl = slice(z["born"] + 1, end)
        seen = np.searchsorted(np.asarray(z["touches"], dtype=int), np.arange(z["born"] + 1, end), side="right")          # visits so far on each candle: a zone wears out with every touch
        sc = z["cscore"] / 100 * np.maximum(0.25, 1 - 0.2 * seen)
        if z["kind"] == "demand":
            dist = np.maximum(0.0, c[sl] - z["hi"]) / a[sl]
            support[sl] = np.maximum(support[sl], np.clip(1 - dist / 1.5, 0, 1) * sc)
        else:
            dist = np.maximum(0.0, z["lo"] - c[sl]) / a[sl]
            resist[sl] = np.maximum(resist[sl], np.clip(1 - dist / 1.5, 0, 1) * sc)
    zf = np.clip(support - resist, -1, 1)
    comp = {"structure": structure, "pressure": np.clip(pressure, -1, 1), "body": np.clip(body_bal, -1, 1), "efficiency": np.clip(eff, -1, 1), "legs": np.clip(st["legs"], -1, 1),
            "zones": zf, "rejection": np.clip(rej * 1.5, -1, 1)}
    score = 100 * sum(WEIGHTS[k] * comp[k] for k in WEIGHTS)
    return {"score": np.clip(score, -100, 100), "comp": comp}


def tier(score: float) -> str:
    a = abs(score)
    return "balanced" if a < 15 else "leaning" if a < 35 else "in control" if a < 60 else "dominating"


def headline(score: float, trend: int, pull: bool) -> str:
    side = "Buyers" if score > 0 else "Sellers"
    t = tier(score)
    if t == "balanced":
        return "Balanced - nobody is in control"
    if pull:
        return f"{side} {t} - but the last candles push the other way (pull-back)"
    return f"{side} {t}"


def analyze(df: pd.DataFrame, k: int = MICRO_K, major_k: int = MAJOR_K) -> dict:
    """Everything for one uninterrupted stretch of candles. Index fields are positions in `df`."""
    o, h, l, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    n = len(c)
    a = _atr(df)
    raw = fractals(o, h, l, c, k)
    major = {(j, kind) for j, kind, _ in fractals(o, h, l, c, major_k)}
    swings = label_swings(raw, major)
    st = structure_pass(o, h, l, c, a, raw, k)
    zones, read_zones = build_zones(df, o, h, l, c, a, swings, st)
    fl = flow_pass(o, h, l, c, a, st, read_zones)
    return {"n": n, "atr": a, "swings": swings, "structure": st, "zones": zones, "flow": fl}


def summary(an: dict, df: pd.DataFrame, i: int | None = None) -> dict:
    """The reading at candle `i` (default: the newest) in words and numbers for the panel."""
    n = an["n"]
    i = n - 1 if i is None else i
    st, fl = an["structure"], an["flow"]
    score = float(fl["score"][i])
    trend = int(st["trend"][i])
    comp = {k: float(v[i]) for k, v in fl["comp"].items()}
    recent = float(np.mean([comp["pressure"], comp["body"]]))
    pull = trend != 0 and abs(score) >= 15 and np.sign(recent) == -np.sign(trend) and abs(recent) >= 0.25
    ev = [e for e in st["events"] if e["i"] <= i]
    last = ev[-1] if ev else None
    t = df["t"].to_numpy().astype(np.int64)
    reasons: list[str] = []
    if trend:
        run = int(st["streak"][i])
        reasons.append("Structure is " + ("UP: higher highs and higher lows" if trend == 1 else "DOWN: lower highs and lower lows")
                       + (f" ({run} break{'s' if run != 1 else ''} of structure in a row)" if run >= 1 else "") + ".")
    else:
        reasons.append("No clear structure yet: the swings have not broken either way.")
    if last:
        ago = i - last["i"]
        word = "CHANGE of direction (CHoCH)" if last["type"] == "CHoCH" else "continuation (BOS)"
        reasons.append(f"Last break: {word} {'up' if last['dir'] == 1 else 'down'} {ago} candle{'s' if ago != 1 else ''} ago, "
                       f"{'confirmed by a strong close' if last['strong'] else 'a weak close beyond the level, not yet confirmed'}.")
    if not np.isnan(st["prot"][i]):
        reasons.append(f"The trend is protected by {st['prot'][i]:.5g}: a close {'below' if trend == 1 else 'above'} it changes direction.")
    for k, v in sorted(comp.items(), key=lambda kv: -abs(kv[1]))[:2]:
        if abs(v) >= 0.25:
            reasons.append(f"{COMPONENT_NAMES[k]} favours {'buyers' if v > 0 else 'sellers'} ({v * 100:+.0f}).")
    alive = [z for z in an["zones"] if z["born"] <= i and (z["end"] is None or z["end"] > i)]
    c_now = float(df["close"].iloc[i])
    a_now = float(an["atr"][i])
    inside = [z for z in alive if z["lo"] <= c_now <= z["hi"]]
    if inside:
        z = max(inside, key=lambda z: z["score"])
        reasons.append(f"Price is inside a {z['kind']} zone ({z['tag']}, strength {z['score']}).")
    else:
        up = [z for z in alive if z["kind"] == "supply" and z["lo"] > c_now]
        dn = [z for z in alive if z["kind"] == "demand" and z["hi"] < c_now]
        if up and (min(z["lo"] for z in up) - c_now) < 2.5 * a_now:
            reasons.append(f"Supply sits {(min(z['lo'] for z in up) - c_now) / a_now:.1f} ATR above: room for buyers is limited.")
        if dn and (c_now - max(z["hi"] for z in dn)) < 2.5 * a_now:
            reasons.append(f"Demand sits {(c_now - max(z['hi'] for z in dn)) / a_now:.1f} ATR below: sellers have support to run into.")
    stretched = abs(score) >= 60
    if stretched:
        reasons.append(f"Stretched: readings this strong were followed by a pull-back first about {100 - ACCURACY['stretched_follow']:.0f}% of the time on the stored candles - do not chase it.")
    buyers = round(50 + score / 2, 1)
    return {"i": i, "t": int(t[i]), "score": round(score, 1), "buyers": buyers, "sellers": round(100 - buyers, 1), "tier": tier(score), "headline": headline(score, trend, pull), "stretched": bool(stretched),
            "side": 1 if score > 0 else -1 if score < 0 else 0, "trend": trend, "pullback": bool(pull), "since": int(st["age"][i]), "protected": None if np.isnan(st["prot"][i]) else float(st["prot"][i]),
            "last_break": None if not last else {"type": last["type"], "dir": last["dir"], "strong": last["strong"], "t": int(t[last["i"]]), "ago": int(i - last["i"]), "level": last["level"]},
            "components": [{"key": k, "name": COMPONENT_NAMES[k], "value": round(v, 2), "weight": WEIGHTS[k]} for k, v in comp.items()], "reasons": reasons, "accuracy": ACCURACY or None}
