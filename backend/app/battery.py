"""The fixed test battery every idea faces (shared by tools/idea_test.py and the Edge Lab): trades cut by time (older 70 % / newest 30 %), into 5 time blocks and two pair halves,
a pre-registered PASS rule, and a ledger that widens the 95 % range with the number of ideas tried. See tools/idea_test.py for the rules in plain words."""
from __future__ import annotations

import json
import math
import statistics
import time
from pathlib import Path

import numpy as np

from . import backtest
from .spec import validate_spec
from .stats_util import wilson

BACKEND = Path(__file__).resolve().parent.parent
OUT = BACKEND / "data" / "ideas"
LEDGER = OUT / "ledger.jsonl"
MIN_TRADES = 1500
BLOCKS = 5

IND = [{"id": "e9", "type": "ema", "n": 9}, {"id": "e21", "type": "ema", "n": 21}, {"id": "e50", "type": "ema", "n": 50}, {"id": "a14", "type": "atr", "n": 14},
       {"id": "r14", "type": "rsi", "n": 14}, {"id": "ad", "type": "adx", "n": 14}, {"id": "mc", "type": "macd", "fast": 12, "slow": 26, "signal": 9},
       {"id": "bb", "type": "bollinger", "n": 20, "k": 2.0}, {"id": "st", "type": "stoch", "k": 14, "smooth": 3, "d": 3}, {"id": "ms", "type": "structure", "swing": 3, "max_age": 120}]


def spec_from_expr(call: str, put: str, tfs: list[int], min_payout: float) -> dict:
    return {"engine": "spec/1", "kind": "factors", "timeframes": tfs, "scope": {"symbols": None, "market": None},
            "gates": {"min_candles": 60, "min_agree": 1, "max_opposing_weight": 100, "min_strength": 0, "tier_a": None}, "expiry_candles": 1, "indicators": IND,
            "factors": [{"id": "idea", "name": "Idea", "weight": 100, "kind": "expr", "call": call, "put": put, "vote": 1}],
            "filters": [{"type": "min_payout", "value": min_payout}]}


# ----------------------------------------------------------------------------------------------- the battery
def ideas_tried(ledger: Path | None, days: int = 90) -> int:
    if not ledger or not ledger.exists():
        return 0
    since = time.time() - days * 86400
    n = 0
    for line in ledger.read_text().splitlines():
        try:
            n += json.loads(line)["t"] >= since
        except Exception:  # noqa: BLE001
            continue
    return n


def _seg(t, z: float = 1.96) -> dict:
    d = t[t["result"].isin(["win", "loss"])]
    n = len(d)
    w = int((d["result"] == "win").sum())
    be = float((1 / (1 + d["payout"] / 100)).mean()) if n else 0.54
    lo, hi = wilson(w, n, z) if n else (None, None)
    return {"n": n, "wins": w, "win": w / n if n else None, "lo": lo, "hi": hi, "be": be, "expectancy": float(d["profit"].mean()) if n else None}


def judge(trades, ideas_before: int = 0, min_trades: int = MIN_TRADES) -> dict:
    """The fixed battery on a trades table (columns t, symbol, result, payout, profit, dir)."""
    k = max(1, ideas_before + 1)
    z = statistics.NormalDist().inv_cdf(1 - 0.025 / k)                      # 95% range widened for the number of ideas tried (Bonferroni)
    dec = trades[trades["result"].isin(["win", "loss"])].sort_values("t")
    out: dict = {"ideas_counted": k, "z": z, "overall": _seg(dec), "checks": [], "verdict": "FAIL"}
    if len(dec) < 50:
        out["checks"].append({"name": "enough trades", "ok": False, "text": f"only {len(dec)} decided trades"})
        out["why"] = "the idea produced (almost) no trades on the stored data"
        return out
    cut = int(dec["t"].quantile(0.7))
    old, new = dec[dec["t"] < cut], dec[dec["t"] >= cut]
    out["older"], out["newest"] = _seg(old, z), _seg(new, z)
    blocks = np.array_split(np.arange(len(dec)), BLOCKS)
    out["blocks"] = [_seg(dec.iloc[b]) for b in blocks]
    syms = sorted(dec["symbol"].unique())
    half = {s: i % 2 for i, s in enumerate(syms)}
    out["pair_halves"] = [_seg(dec[dec["symbol"].map(half) == h]) for h in (0, 1)]
    out["by_direction"] = {name: _seg(dec[dec["dir"] == v]) for name, v in (("CALL", 1), ("PUT", -1))}
    o, nw = out["older"], out["newest"]
    be = out["overall"]["be"]
    above = sum(1 for b in out["blocks"] if b["win"] is not None and b["win"] > b["be"])
    checks = [
        ("enough trades", len(dec) >= min_trades, f"{len(dec):,} decided trades (need {min_trades:,})"),
        ("newest 30% clearly above break-even", nw["lo"] is not None and nw["lo"] > nw["be"],
         f"{nw['win'] * 100:.1f}% of {nw['n']:,} (95% range {nw['lo'] * 100:.1f}-{nw['hi'] * 100:.1f}%, widened for {k} idea{'s' if k > 1 else ''}) vs break-even {nw['be'] * 100:.1f}%"),
        ("older 70% above break-even", o["win"] is not None and o["win"] > o["be"], f"{o['win'] * 100:.1f}% of {o['n']:,} vs {o['be'] * 100:.1f}%"),
        (f"at least 4 of {BLOCKS} time blocks above break-even", above >= 4, f"{above} of {BLOCKS}: " + ", ".join(f"{b['win'] * 100:.1f}%" for b in out["blocks"])),
        ("both pair halves above break-even", all(p["win"] is not None and p["win"] > p["be"] for p in out["pair_halves"]),
         " / ".join(f"{p['win'] * 100:.1f}%" for p in out["pair_halves"] if p["win"] is not None)),
    ]
    out["checks"] = [{"name": n, "ok": bool(ok), "text": t} for n, ok, t in checks]
    if all(ok for _, ok, _ in checks):
        out["verdict"] = "PASS"
        out["why"] = "every check passed on data the idea never saw"
    elif checks[0][1] and nw["win"] is not None and nw["win"] > nw["be"]:
        out["verdict"] = "PROMISING"
        out["why"] = "above break-even on the newest data but not proven - collect live paper results before trusting it"
    else:
        gap = (be - out["overall"]["win"]) * 100
        out["why"] = f"{out['overall']['win'] * 100:.1f}% overall is {gap:+.1f} points {'below' if gap > 0 else 'above'} break-even ({be * 100:.1f}%)"
        if not checks[0][1]:
            out["why"] = f"too few trades ({len(dec):,}); " + out["why"]
    # what luck alone does: the 95% band of a fair coin for this many trades, and the win rate needed to prove an edge
    se = math.sqrt(0.25 / len(dec))
    out["noise_band"] = [0.5 - 1.96 * se, 0.5 + 1.96 * se]
    return out


def evaluate(conn, spec: dict, cfg: dict | None = None, ledger: Path | None = LEDGER, name: str = "idea", kind: str = "spec") -> dict:
    cfg = cfg or {}
    v = validate_spec(spec)
    if not v["ok"]:
        raise ValueError("invalid spec: " + "; ".join(v["errors"][:4]))
    payouts = {s: p for s, p in conn.execute("SELECT symbol, payout FROM instruments WHERE payout IS NOT NULL")}
    t0 = time.time()
    trades = backtest.compute_trades(conn, v["spec"], {"tfs": cfg.get("tfs")}, payouts)
    res = judge(trades, ideas_tried(ledger), cfg.get("min_trades", MIN_TRADES))
    res.update(name=name, kind=kind, seconds=round(time.time() - t0, 1), trades_total=int(len(trades)), spec=v["spec"], when=time.time())
    if ledger is not None:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with open(ledger, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": time.time(), "name": name, "kind": kind, "trades": res["overall"]["n"], "win": res["overall"]["win"], "be": res["overall"]["be"],
                                 "newest": (res.get("newest") or {}).get("win"), "verdict": res["verdict"]}) + "\n")
    return res
