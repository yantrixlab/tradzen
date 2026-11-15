"""Phase C (part 1): the vectorised evaluator must make the same decisions as the live evaluator, candle by candle.
Run from backend/:  .venv\\Scripts\\python tests\\test_vec.py"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402

from app import evaluator, features, vec  # noqa: E402
from app.builtins import BUILTINS  # noqa: E402
from app.spec import validate_spec  # noqa: E402
from test_engine import walk  # noqa: E402


def specs():
    return [(b["key"], validate_spec(b["spec"])["spec"]) for b in BUILTINS if b["spec"].get("kind") != "meta"]


def test_vector_decisions_match_live_decisions():
    mismatches, checked = [], 0
    for key, spec in specs():
        for seed in range(4):
            rows = walk(420, 40 + seed)
            df = evaluator.build_df(rows)
            series = vec.evaluate_series(spec, df)
            need = spec["gates"]["min_candles"]
            for i in range(max(need, 80), len(rows), 7):                       # sample candles across the series
                live = evaluator.evaluate(spec, rows[max(0, i - 399): i + 1])
                want = {"CALL": 1, "PUT": -1, "SKIP": 0}[live.direction]
                got = int(series["dir"].iloc[i])
                checked += 1
                if want != got:
                    mismatches.append((key, seed, i, live.direction, got, live.reason))
                elif want:
                    assert series["tier"].iloc[i] == (1 if live.tier == "A" else 2), (key, i, live.tier, series["tier"].iloc[i])
                    assert abs(series["score"].iloc[i] - live.score) <= 0.3, (key, i, series["score"].iloc[i], live.score)
    # recursive indicators (EMA/RSI/ATR) start from different points in the two paths; allow a tiny fraction of edge disagreements
    assert checked > 2000 and len(mismatches) <= max(3, checked // 200), (len(mismatches), checked, mismatches[:6])


def test_default_strategy_votes_match_scalar_factors_exactly():
    from app import strategy
    spec = validate_spec(next(b for b in BUILTINS if b["key"] == "confluence-5")["spec"])["spec"]
    for seed in range(6):
        rows = walk(300, seed + 70)
        df = evaluator.build_df(rows)
        for f in spec["factors"]:
            fn, _ = strategy.FN_FACTORS[f["fn"]]
            series = vec.FN_VEC[f["fn"]](df, **f["params"])
            for i in range(80, len(rows), 3):
                scalar, _ = fn(evaluator.build_df(rows[max(0, i - 399): i + 1]), **f["params"])
                assert abs(series[i] - scalar) < 1e-9, (f["id"], seed, i, series[i], scalar)


def test_feature_frame_matches_snapshot():
    for seed in range(4):
        rows = walk(260, seed + 90)
        frame = vec.featurize(evaluator.build_df(rows))
        for i in (150, 200, 259):
            snap = features.base_features(rows[: i + 1])
            for k in ("rsi14", "rsi2", "stoch_k", "cci20", "bb_pctb", "adx14", "atr_ratio", "ema_stack", "streak", "body_ratio",
                      "upper_wick_ratio", "ret5_pct", "hour_utc", "weekday", "session", "candle_color"):
                a, b = frame[k].iloc[i], snap[k]
                if isinstance(b, str):
                    assert a == b, (k, a, b)
                else:
                    assert b is not None and abs(float(a) - float(b)) <= max(0.02, abs(float(b)) * 0.02), (k, i, a, b)


def test_contiguous_runs_and_filters():
    t = np.array([0, 60, 120, 300, 360, 1000])
    assert vec.contiguous_runs(t, 60) == [(0, 3), (3, 5), (5, 6)]
    spec = validate_spec({"indicators": [{"id": "rsi14", "type": "rsi", "n": 14}],
                          "factors": [{"id": "r", "weight": 100, "kind": "expr", "call": "close > 0"}],
                          "filters": [{"type": "direction_only", "value": "PUT"}]})["spec"]
    df = evaluator.build_df(walk(120, 5))
    assert (vec.evaluate_series(spec, df)["dir"] == 0).all(), "CALL-only strategy under a PUT-only filter never signals"
    spec["filters"] = [{"type": "hours", "include": [], "exclude": ["00:00-23:59"], "type_": 0}]
    spec = validate_spec({**spec, "filters": [{"type": "hours", "exclude": ["00:00-23:59"]}]})["spec"]
    assert (vec.evaluate_series(spec, df)["dir"] == 0).sum() >= len(df) - 1


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all vector tests passed")
