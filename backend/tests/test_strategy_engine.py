"""Phase B (part 1): expression language, indicator registry, spec validation, evaluator, built-in library, parity with the legacy engine.
Run from backend/:  .venv\\Scripts\\python tests\\test_strategy_engine.py"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import evaluator, expr, indreg, strategy  # noqa: E402
from app.builtins import BUILTINS, DEFAULT_KEY  # noqa: E402
from app.spec import validate_spec  # noqa: E402
from test_engine import mirror, walk  # noqa: E402


def df_of(rows):
    return evaluator.build_df(rows)


def ev(text, rows):
    df = df_of(rows)
    env = evaluator.base_env(df)
    return expr.evaluate(text, env, df.index), df


# ------------------------------------------------------------------ expression language
def test_expr_basics_and_lookback():
    rows = [{"t": 1_700_000_040 + 60 * i, "o": 1.0 + i * 0.01, "h": 1.02 + i * 0.01, "l": 0.99 + i * 0.01, "c": 1.01 + i * 0.01, "n": 5} for i in range(30)]
    s, df = ev("close > open and close[1] < close", rows)
    assert s.iloc[-1] and not s.iloc[0]
    s, _ = ev("cross_above(close, 1.2)", rows)
    assert s.sum() == 1                                           # crosses exactly once on a rising line
    s, _ = ev("highest(high, 5) == high", rows)
    assert s.iloc[-1] and s.iloc[10]
    s, _ = ev("not (close < open) and 1 < 2 < 3", rows)
    assert s.iloc[-1]
    s, _ = ev("count(green, 5) == 5", rows)
    assert s.iloc[-1] and not s.iloc[2]


def test_expr_rejects_unsafe_input():
    for bad in ("__import__('os').system('x')", "close.__class__", "open(close)", "[x for x in close]", "lambda: 1", "close if open else low",
                "a.b.c", "close[-1]", "close[open]", "'text'", "x" * 500, "", "  ", "close >", "sma(close, n)", "foo(close, 2)", "cross_above(close)"):
        try:
            expr.parse(bad)
            expr.evaluate(bad, {"close": pd.Series([1.0, 2.0]), "open": pd.Series([1.0, 1.0])}, pd.RangeIndex(2))
        except expr.ExprError:
            continue
        except Exception as e:                                    # anything else escaping would be a hole
            raise AssertionError(f"{bad!r} raised {type(e).__name__}: {e}")
        raise AssertionError(f"{bad!r} was accepted")


def test_expr_errors_are_helpful():
    rows = walk(80, 1)
    cache = {}
    df = df_of(rows)
    env = evaluator._ind_env({"indicators": [{"id": "bb", "type": "bollinger", "n": 20, "k": 2.0}]}, df, cache)
    for text, hint in (("bb > 1", "several fields"), ("bb.nope > 1", "no field"), ("zzz > 1", "unknown name")):
        try:
            expr.evaluate(text, env, df.index)
        except expr.ExprError as e:
            assert hint in str(e), (text, str(e))
        else:
            raise AssertionError(text)


# ------------------------------------------------------------------ indicators
def test_indicator_registry_shapes():
    df = df_of(walk(300, 4))
    cache = {}
    for typ, (builder, dflt, flds) in indreg.REGISTRY.items():
        r = indreg.compute(cache, df, typ, {})
        if flds is None:
            assert isinstance(r, pd.Series) and len(r) == len(df), typ
            assert r.iloc[-1] == r.iloc[-1] or typ in ("williams",), typ        # last value is a number (not NaN) once warmed up
        else:
            for f in flds:
                s = getattr(r, f)
                assert len(s) == len(df), (typ, f)
        assert indreg.compute(cache, df, typ, {}) is r, "second call must hit the cache"
    st = indreg.compute(cache, df, "supertrend", {})
    assert set(st.dir.dropna().unique()) <= {1.0, -1.0}
    ha = indreg.compute(cache, df, "heikin", {})
    assert ha.green.dtype == bool and not (ha.green & ha.red).any()


def test_supertrend_and_psar_follow_a_trend():
    up = [{"t": 1_700_000_040 + 60 * i, "o": 1 + i * 0.002, "h": 1.001 + i * 0.002 + 0.0005, "l": 1 + i * 0.002 - 0.0005, "c": 1.0015 + i * 0.002, "n": 5} for i in range(120)]
    df = df_of(up)
    cache = {}
    assert indreg.compute(cache, df, "supertrend", {}).dir.iloc[-1] == 1
    assert indreg.compute(cache, df, "psar", {}).dir.iloc[-1] == 1
    assert indreg.compute({}, df_of(mirror(up)), "supertrend", {}).dir.iloc[-1] == -1     # a cache belongs to ONE candle series
    assert indreg.compute({}, df_of(mirror(up)), "psar", {}).dir.iloc[-1] == -1


# ------------------------------------------------------------------ spec validation
def test_validation_accepts_builtins_and_rejects_bad_specs():
    for b in BUILTINS:
        v = validate_spec(b["spec"])
        assert v["ok"], (b["key"], v["errors"])
        assert validate_spec(v["spec"])["spec"] == v["spec"], f"{b['key']}: normalisation must be idempotent"
    good = BUILTINS[1]["spec"]
    bad_cases = [
        ({}, "factors"),
        ({**good, "factors": [{"id": "x", "weight": 100, "kind": "expr", "call": "nonexistent > 1"}]}, "unknown name"),
        ({**good, "factors": [{"id": "x", "weight": 100, "kind": "expr", "call": "bb > 1"}]}, "needs one of its fields"),
        ({**good, "factors": [{"id": "x", "weight": 100, "kind": "expr", "call": "close.foo > 1"}]}, "single series"),
        ({**good, "factors": [{"id": "X!", "weight": 100, "kind": "expr", "call": "close > 1"}]}, "identifier"),
        ({**good, "factors": [{"id": "x", "weight": -5, "kind": "expr", "call": "close > 1"}]}, "weight"),
        ({**good, "factors": [{"id": "x", "weight": 1, "kind": "fn", "fn": "nope"}]}, "unknown fn"),
        ({**good, "indicators": [{"id": "close", "type": "rsi"}]}, "unique"),
        ({**good, "indicators": [{"id": "r", "type": "magic"}]}, "unknown"),
        ({**good, "indicators": [{"id": "r", "type": "rsi", "zzz": 3}]}, "unknown parameter"),
        ({**good, "timeframes": [30]}, "timeframes"),
        ({**good, "gates": {"min_agree": 5}}, "min_agree"),
        ({**good, "gates": {"bogus": 1}}, "unknown gate"),
        ({**good, "filters": [{"type": "hours", "include": ["7-16"]}]}, "HH:MM"),
        ({**good, "filters": [{"type": "nope"}]}, "unknown"),
        ({"kind": "meta"}, "consensus"),
    ]
    for spec, hint in bad_cases:
        v = validate_spec(spec)
        assert not v["ok"] and any(hint in e for e in v["errors"]), (hint, v["errors"])


# ------------------------------------------------------------------ evaluator
def test_evaluator_gates_filters_and_tiers():
    spec = validate_spec({"indicators": [{"id": "rsi14", "type": "rsi", "n": 14}],
                          "factors": [{"id": "r", "name": "RSI low", "weight": 100, "kind": "expr", "call": "rsi14 < 101", "put": "rsi14 > 200"}],
                          "gates": {"min_agree": 1}})["spec"]
    rows = walk(120, 5)
    res = evaluator.evaluate(spec, rows)
    assert res.direction == "CALL" and res.tier == "A" and res.strength == 100 and res.factors[0]["vote"] == 1.0
    res = evaluator.evaluate(spec, rows[:30])
    assert res.direction == "SKIP" and "warming" in res.reason
    blocked = validate_spec({**spec, "filters": [{"type": "min_payout", "value": 90}]})["spec"]
    res = evaluator.evaluate(blocked, rows, ctx={"payout": 80})
    assert res.direction == "SKIP" and "filtered" in res.reason
    assert evaluator.evaluate(blocked, rows, ctx={"payout": 95}).direction == "CALL"
    hours = validate_spec({**spec, "filters": [{"type": "hours", "exclude": ["00:00-24:00".replace("24:00", "23:59")]}]})["spec"]
    assert evaluator.evaluate(hours, rows).direction in ("SKIP", "CALL")      # does not crash, window logic exercised
    broken = validate_spec({**spec})["spec"]
    broken["factors"][0]["call"] = "undefined_name > 1"                           # a broken factor abstains instead of raising
    res = evaluator.evaluate(broken, rows)
    assert res.direction == "SKIP" and "error" in res.factors[0]["note"]


def test_builtins_run_and_are_symmetric():
    asym = 0
    for b in BUILTINS:
        spec = validate_spec(b["spec"])["spec"]
        if spec["kind"] == "meta":
            continue
        for seed in range(12):
            rows = walk(300, seed)
            a, m = evaluator.evaluate(spec, rows), evaluator.evaluate(spec, mirror(rows))
            assert a.direction in ("CALL", "PUT", "SKIP")
            flip = {"CALL": "PUT", "PUT": "CALL", "SKIP": "SKIP"}
            if flip[a.direction] != m.direction:
                asym += 1
                assert b["key"] in ("psar-ema", "supertrend-flip", "heikin-ashi-trend"), (b["key"], seed, a.direction, m.direction)
    assert asym <= 12, asym               # recursive indicators may differ on warm-up edges; the rest must mirror exactly


def test_builtins_produce_signals_somewhere():
    fired = {}
    for b in BUILTINS:
        spec = validate_spec(b["spec"])["spec"]
        if spec["kind"] == "meta":
            continue
        n = 0
        for seed in range(25):
            rows = walk(400, 100 + seed)
            for cut in (400, 399, 398):
                if evaluator.evaluate(spec, rows[:cut]).direction != "SKIP":
                    n += 1
        fired[b["key"]] = n
    silent = [k for k, v in fired.items() if v == 0]
    assert len(silent) <= 2, f"strategies that never fire on random data: {silent}"


# ------------------------------------------------------------------ parity with the legacy hard-coded engine
def test_default_spec_matches_legacy_engine_exactly():
    default = next(b for b in BUILTINS if b["key"] == DEFAULT_KEY)
    spec = validate_spec(default["spec"])["spec"]
    for seed in range(80):
        rows = walk(150 + seed % 40, seed)
        old, new = strategy.evaluate(rows), evaluator.evaluate(spec, rows)
        assert (old.direction, old.score, old.strength, old.agree, old.tier) == (new.direction, new.score, new.strength, new.agree, new.tier), (seed, old, new)
        assert [(f["key"], f["vote"], f["weight"]) for f in old.factors] == [(f["key"], f["vote"], round(f["weight"])) for f in new.factors]
        assert old.reason == new.reason


def test_consensus():
    from app.strategy import Result
    spec = validate_spec({"kind": "meta", "consensus": {"members": "all_active", "min_agree": 2}})["spec"]
    r = lambda d, s=50: ("n", Result(d, 10.0 if d == "CALL" else -10.0, s, 1))  # noqa: E731
    out = evaluator.consensus(spec, {"a": r("CALL", 60), "b": r("CALL", 40), "c": r("SKIP")})
    assert out.direction == "CALL" and out.agree == 2 and out.strength == 50 and out.tier == "A"
    assert evaluator.consensus(spec, {"a": r("CALL"), "b": r("PUT"), "c": r("CALL")}).direction == "SKIP"        # disagreement vetoes
    assert evaluator.consensus(spec, {"a": r("CALL"), "b": r("SKIP")}).direction == "SKIP"
    pick = validate_spec({"kind": "meta", "consensus": {"members": ["a", "b"], "min_agree": 2}})["spec"]
    assert evaluator.consensus(pick, {"a": r("PUT"), "b": r("PUT"), "c": r("CALL")}).direction == "PUT"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all strategy-engine tests passed")
