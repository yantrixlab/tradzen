"""Risk advisor: it must find a planted losing situation in a strategy's trades, find nothing in pure noise, tag live signals only for confirmed patterns,
and only keep an added filter when the newest trades really improve.  Run from backend/:  .venv\\Scripts\\python tests\\test_risk.py"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import risk  # noqa: E402
from app.backtest import FEATURE_COLS  # noqa: E402
from app.engine import Engine  # noqa: E402
from test_backtest import put_candles, streak_series  # noqa: E402

FAST = {"folds": 3, "rounds": 40, "train_cap": 20_000, "min_trades": 1500, "min_test": 600}


def trades(n=7000, seed=1, planted=True):
    """Trades of an imaginary strategy that wins ~54 % - except in one situation (ADX >= 30 while MACD histogram goes AGAINST the trade) where it wins ~30 %."""
    r = np.random.default_rng(seed)
    d = r.choice([-1, 1], n)
    f = {"rsi14": r.uniform(10, 90, n), "rsi2": r.uniform(0, 100, n), "stoch_k": r.uniform(0, 100, n), "stoch_d": r.uniform(0, 100, n), "cci20": r.normal(0, 100, n),
         "macd_hist": r.normal(0, 1, n), "bb_pctb": r.uniform(-0.2, 1.2, n), "bb_bw": r.uniform(0, 0.02, n), "bb_bw_pctile": r.uniform(0, 100, n), "atr_ratio": r.uniform(0.4, 2, n),
         "adx14": r.uniform(8, 45, n), "plus_di": r.uniform(5, 40, n), "minus_di": r.uniform(5, 40, n), "ema_stack": r.choice([-1, 0, 1], n), "ema21_slope_pct": r.normal(0, 0.01, n),
         "dist_hi20_atr": r.uniform(0, 5, n), "dist_lo20_atr": r.uniform(0, 5, n), "body_ratio": r.uniform(0, 1, n), "upper_wick_ratio": r.uniform(0, 1, n),
         "lower_wick_ratio": r.uniform(0, 1, n), "candle_color": r.choice([-1, 1], n), "streak": r.integers(-4, 5, n), "ret1_pct": r.normal(0, 0.05, n), "ret5_pct": r.normal(0, 0.1, n),
         "ret15_pct": r.normal(0, 0.2, n), "hour_utc": r.integers(0, 24, n), "weekday": r.integers(0, 7, n)}
    bad = (f["adx14"] >= 30) & (f["macd_hist"] * d < 0)
    p = np.where(bad & planted, 0.30, 0.54 if planted else 0.50)
    win = r.random(n) < p
    t = pd.DataFrame({"symbol": "T_otc", "tf": 60, "t": 1_700_000_000 + 60 * np.arange(n), "dir": d, "tier": "A", "strength": r.uniform(30, 90, n), "agree": r.integers(1, 5, n),
                      "score": 0.0, "entry": 1.0, "exit": 1.0, "result": np.where(win, "win", "loss"), "profit": np.where(win, 0.85, -1.0), "payout": 85.0})
    for k, v in f.items():
        t[k] = v
    t["session"] = np.where(f["hour_utc"] < 8, "asia", np.where(f["hour_utc"] < 16, "europe", "us"))
    t["trend_align"] = t["dir"] * t["ema_stack"]
    return t


def test_it_finds_the_planted_losing_situation():
    t = trades(planted=True)
    res = risk.analyze_trades(t, FAST)
    assert res["model"]["verdict"] == "proven" and res["model"]["auc"] > 0.55, res["model"]
    g = res["risky"]
    assert g["useful"] and g["risky_win" if False else "all"]["risky_win"] < g["all"]["rest_win"] - 0.05, g["why"]
    assert any(r["dimension"] == "adx" for r in res["rules"]), [r["text"] for r in res["rules"]]
    assert all(r["validation"]["holds"] for r in res["rules"])


def test_pure_noise_gives_no_risk_model_and_no_rules():
    res = risk.analyze_trades(trades(planted=False, seed=2), FAST)
    assert not res["risky"]["useful"], res["risky"]["why"]
    assert res["model"]["verdict"] in ("no_skill", "weak") and res["rules"] == [], (res["model"]["verdict"], [r["text"] for r in res["rules"]])


def test_too_few_trades_are_reported_plainly():
    res = risk.analyze_trades(trades(n=900, seed=3), FAST)
    assert res["model"]["verdict"] == "need_data" and res["risky"] is None and "need" in res["model"]["why"]


def test_a_filter_is_kept_only_if_the_newest_trades_improve():
    base = trades(planted=True, seed=4)
    res = risk.analyze_trades(base, FAST)

    def compute(spec):                                                           # what a backtest of the spec would give: the same trades minus those the filters remove
        out = base
        for f in spec["filters"]:
            if f["type"] == "adx_max":
                out = out[out["adx14"] < f["value"]]
            elif f["type"] == "adx_min":
                out = out[out["adx14"] >= f["value"]]
        return out.reset_index(drop=True)
    spec = {"engine": "spec/1", "kind": "factors", "timeframes": [60], "scope": {"symbols": None, "market": None},
            "gates": {"min_candles": 60, "min_agree": 1, "max_opposing_weight": 100, "min_strength": 0, "tier_a": None}, "expiry_candles": 1, "indicators": [],
            "factors": [{"id": "f", "name": "f", "weight": 100, "kind": "expr", "call": "green", "put": "red", "vote": 1}], "filters": [{"type": "min_payout", "value": 80}]}
    o = risk.optimize(spec, base, res["rules"], compute, FAST)
    assert o["filters"] and o["spec"] is not None and o["verdict"] in ("holds", "weak")
    assert o["comparison"]["valid"]["candidate"]["accuracy"] > o["comparison"]["valid"]["base"]["accuracy"]
    # a rule whose filter changes nothing useful (random half of the trades dropped) must not be kept
    rng = np.random.default_rng(0)
    keep_mask = rng.random(len(base)) < 0.6
    o2 = risk.optimize(spec, base, res["rules"][:1], lambda s: base[keep_mask].reset_index(drop=True), FAST)
    assert not o2["filters"] and o2["spec"] is None and o2["tried"][0]["kept"] is False


def test_live_signals_are_tagged_only_for_confirmed_patterns():
    eng = Engine(":memory:")
    t = trades(planted=True, seed=5)
    res = risk.analyze_trades(t, FAST)
    pack = res.pop("_pack")
    res.update(strategy_id=1, trained_at=0)
    eng.risk.reports[1], eng.risk.packs[1] = res, pack

    def sig(direction):
        return {"strategy_id": 1, "symbol": "T_otc", "tf": 60, "direction": direction, "tier": "A", "strength": 60, "agree": 3}
    f = {c: float(t[c].median()) for c in FEATURE_COLS if c != "session"}
    f.update(session="us", trend_align=0, adx14=38.0, macd_hist=-1.5, hour_utc=14, weekday=2)
    bad = eng.risk.score(sig("CALL"), f)                                      # strong trend, MACD against the CALL: the planted losing situation
    assert bad["tag"] == "risky" and bad["reasons"], bad
    f2 = dict(f, adx14=14.0, macd_hist=1.5)
    good = eng.risk.score(sig("CALL"), f2)
    assert good["tag"] == "ok" and not good["reasons"], good
    assert eng.risk.score({**sig("CALL"), "strategy_id": 99}, f)["tag"] == "unchecked"          # a strategy never analysed gets no tag at all


def test_a_strategy_without_enough_history_analyses_without_crashing():
    eng = Engine(":memory:")
    put_candles(eng, "T_otc", streak_series(900, 3, 0.5, 1_700_000_000, 60))
    assert eng.risk.analyze(eng.strategy_id, FAST, sync=True)
    rep = eng.risk.report(eng.strategy_id)
    assert rep and rep["model"]["verdict"] == "need_data" and rep["n_decided"] < 1500
    st = eng.risk.status()
    assert any(s["id"] == eng.strategy_id and s["analyzed"] for s in st["strategies"]) and not st["job"]["running"]


def _signals(eng, sid, wins, losses, t0=1_800_000_000):
    for i in range(wins + losses):
        eng._save_signal({"created": t0 + i, "symbol": "T_otc", "tf": 60, "basis_t": t0 + 60 * i, "target_t": t0 + 60 * i + 60, "direction": "CALL", "score": 1.0, "strength": 50,
                          "agree": 1, "factors": [], "reason": "", "entry": 1.0, "status": "win" if i < wins else "loss", "exit": 1.0, "sentiment": None, "tier": "A",
                          "strategy_id": sid, "strategy_version": 1, "mode": "paper"})


def test_nightly_requeues_every_live_and_paper_strategy_once_a_day():
    import os
    os.environ["QXS_RESEARCH_HOUR"] = "3"
    eng = Engine(":memory:")
    spec = eng.repo.get(eng.strategy_id)["spec"]
    eng.create_strategy("extra-a", "A", "", spec, "paper")
    eng.create_strategy("extra-b", "B", "", spec, "paused")                        # paused / archived strategies are not re-studied
    now = 1_800_000_000 - (1_800_000_000 % 86400) + 4 * 3600                       # 04:00 UTC: tonight's run is due
    eng.risk.nightly(now)
    n = len([s for s in eng.repo.list() if s["status"] in ("live", "paper")])
    assert n == 2 and len(eng.risk._queue) == n and eng.risk._queue[0] == eng.strategy_id        # the live strategy first
    eng.risk.nightly(now + 600)
    assert len(eng.risk._queue) == n                                               # not queued twice while the queue is still being worked through
    eng.risk._queue = eng.risk._queue[:1]
    eng.risk.tick()                                                                # the last one starts: tonight is done
    eng.risk._last_nightly = now + 1                                               # (tick stamps the real clock; pin it to the test's clock)
    eng.risk.nightly(now + 600)
    assert eng.risk._queue == []                                                   # once per night


def test_a_challenger_is_created_once_as_paper_and_judged_only_on_live_results():
    eng = Engine(":memory:")
    parent = eng.strategy_id
    spec = eng.repo.get(parent)["spec"]
    eng.risk.reports[parent] = {"strategy_id": parent, "trained_at": 0, "optimized": {"verdict": "holds", "spec": spec, "filters": ["skip signals when ADX >= 30"], "tried": [], "comparison": {"valid": None}}}
    child = eng.risk.challenge(parent)
    assert child and eng.repo.get(child)["status"] == "paper"
    assert eng.risk.challenge(parent) is None                                      # one challenger at a time, and never the same filters twice
    eng.risk.reports[parent]["optimized"]["verdict"] = "weak"
    assert eng.risk.challenge(parent) is None                                      # only a filter that HELD on the newest trades is tried
    _signals(eng, child, 120, 100)                                                 # 220 settled: not enough yet, whatever the rate
    eng.risk.evaluate_upgrades()
    assert eng.risk.upgrades()[0]["status"] == "testing"
    _signals(eng, child, 0, 0)
    _signals(eng, child, 60, 40, t0=1_900_000_000)                                 # now 320 settled, 180 wins = 56 %: above break-even but the proof (95 % lower bound) is not there
    eng.risk.evaluate_upgrades()
    u = eng.risk.upgrades()[0]
    assert u["status"] in ("testing", "retired") and u["status"] != "promoted"
    if u["status"] == "retired":
        assert eng.repo.get(child)["status"] == "archived"


def test_a_challenger_with_proven_profit_is_promoted_and_a_proven_loser_retired():
    eng = Engine(":memory:")
    parent = eng.strategy_id
    spec = eng.repo.get(parent)["spec"]
    eng.risk.reports[parent] = {"strategy_id": parent, "trained_at": 0, "optimized": {"verdict": "holds", "spec": spec, "filters": ["a"], "tried": [], "comparison": {"valid": None}}}
    good = eng.risk.challenge(parent)
    _signals(eng, good, 240, 160)                                                  # 400 settled, 60 %: lower bound ~55 % > break-even ~54 %
    eng.risk.evaluate_upgrades()
    assert eng.risk.upgrades()[0]["status"] == "promoted" and eng.repo.get(good)["status"] == "live"
    assert eng.repo.get(parent)["status"] == "paused"                              # the promoted child replaced its live parent
    eng.set_strategy_status(parent, "paper")
    eng.risk.reports[parent]["optimized"]["filters"] = ["b"]
    bad = eng.risk.challenge(parent)
    _signals(eng, bad, 150, 250, t0=1_950_000_000)                                 # 400 settled, 37.5 %: proven unprofitable
    eng.risk.evaluate_upgrades()
    ups = {u["child_id"]: u for u in eng.risk.upgrades()}
    assert ups[bad]["status"] == "retired" and eng.repo.get(bad)["status"] == "archived"


def test_strategy_health_condemns_only_what_is_proven_and_offers_an_upgrade_only_when_one_exists():
    eng = Engine(":memory:")
    spec = eng.repo.get(eng.strategy_id)["spec"]
    a = eng.create_strategy("h-loser", "Loser", "", spec, "live")
    b = eng.create_strategy("h-lucky", "Short unlucky run", "", spec, "paper")
    c = eng.create_strategy("h-fixable", "Fixable", "", spec, "paper")
    d = eng.create_strategy("h-star", "Star", "", spec, "paper")
    _signals(eng, a, 190, 260, t0=1_800_000_000)                  # 450 settled, 42 %: even the best case is below break-even
    _signals(eng, b, 3, 9, t0=1_810_000_000)                      # 12 settled at 25 %: far too few to condemn
    _signals(eng, c, 200, 250, t0=1_820_000_000)
    _signals(eng, d, 300, 150, t0=1_830_000_000)                  # 450 settled, 67 %: proven profitable
    eng.risk.reports[c] = {"strategy_id": c, "trained_at": 1, "n_decided": 9000, "base_win": 0.47, "breakeven": 0.54, "rules": [{"x": 1}],
                           "optimized": {"verdict": "holds", "spec": spec, "filters": ["f"]}}
    eng.risk.reports[a] = {"strategy_id": a, "trained_at": 1, "n_decided": 9000, "base_win": 0.48, "breakeven": 0.54, "rules": [], "optimized": None}
    tags = {s["id"]: s["tag"] for s in eng.risk.health()["strategies"]}
    assert tags[a] == "pause" and tags[c] == "upgrade" and tags[d] == "keep"
    assert tags[b] in ("analyse", "collecting", "watch")          # never "pause" on a handful of results
    live_loser = next(s for s in eng.risk.health()["strategies"] if s["id"] == a)
    assert "nothing to fix" in live_loser["why"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
