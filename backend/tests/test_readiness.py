"""'How close is each strategy to a signal?' - the readiness reading used by the live progress bars.
Run from backend/:  .venv\\Scripts\\python tests\\test_readiness.py"""
import copy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app import control, evaluator, readiness  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.spec import validate_spec  # noqa: E402


def spec_of(call, put=None, factors=None, gates=None, filters=None):
    raw = {"engine": "spec/1", "kind": "factors", "timeframes": [60], "indicators": [],
           "factors": factors or [{"id": "r", "name": "rule", "weight": 100, "kind": "expr", "call": call, "put": put, "vote": 1}],
           "gates": {"min_candles": 30, "min_agree": 1, **(gates or {})}, "filters": filters or [], "expiry_candles": 1}
    v = validate_spec(raw)
    assert v["ok"], v["errors"]
    return v["spec"]


def rows(n=40, last=None):
    """a gentle zig-zag; `last` replaces the final candle (o, h, l, c)"""
    out = []
    for i in range(n):
        p = 10 + 0.1 * (i % 5)
        out.append({"t": 1_700_000_000 + 60 * i, "o": p, "h": p + 0.15, "l": p - 0.05, "c": p + 0.05, "n": 10})
    if last:
        out[-1].update(dict(zip(("o", "h", "l", "c"), last)))
    return out


def test_a_rule_is_split_into_its_conditions():
    assert readiness._clauses("green and body > 0.5 * range and close > high[1]") == ["green", "body > 0.5 * range", "close > high[1]"]
    assert readiness._clauses("a or b") == ["a or b"] and readiness._clauses("(a and b) and c") == ["a", "b", "c"]


def test_readiness_is_the_share_of_conditions_met_and_names_what_is_missing():
    s = spec_of("green and close > open and close > 50 and high > 40")                  # the last two can never hold on these prices
    r = readiness.of(s, rows(), {}, {})
    assert r["state"] == "building" and r["lead"] == "CALL" and abs(r["call"]["p"] - 0.5) < 1e-9 and r["call"]["met"] == 2 and r["call"]["total"] == 4
    assert r["call"]["missing"] == ["close > 50", "high > 40"]
    assert r["put"]["p"] == 0.0                                                          # no put rule at all


def test_ready_exactly_when_a_signal_would_be_issued_and_not_before():
    s = spec_of("green and body > 0.5 * range and close > high[1]")
    quiet = rows(last=(10.0, 10.2, 9.95, 10.01))                                        # a small candle: the strong-body condition fails
    r = readiness.of(s, quiet, {}, {})
    assert r["state"] == "building" and evaluator.evaluate(s, quiet, {}, {}).direction == "SKIP" and r["call"]["p"] < 1
    strong = rows(last=(10.0, 10.8, 9.98, 10.78))                                       # a strong green candle breaking the previous high
    r = readiness.of(s, strong, {}, {})
    assert r["state"] == "ready" and r["lead"] == "CALL" and r["p"] == 1.0 and r["direction"] == "CALL"
    assert evaluator.evaluate(s, strong, {}, {}).direction == "CALL"                   # the same answer the real evaluation gives


def test_gates_need_several_factors_ready():
    f = lambda i, call: {"id": i, "name": i, "weight": 50, "kind": "expr", "call": call, "put": None, "vote": 1}    # noqa: E731
    s = spec_of(None, factors=[f("a", "green"), f("b", "close > 99")], gates={"min_agree": 2})
    r = readiness.of(s, rows(last=(10.0, 10.5, 9.9, 10.4)), {}, {})
    assert r["state"] == "building" and abs(r["call"]["p"] - 0.5) < 1e-9                  # factor a ready (1.0), b not (0.0): the best two average 0.5
    one = spec_of(None, factors=[f("a", "green"), f("b", "close > 99")], gates={"min_agree": 1})
    assert readiness.of(one, rows(last=(10.0, 10.5, 9.9, 10.4)), {}, {})["state"] == "ready"


def test_warming_and_blocked_by_a_filter_are_told_apart():
    s = spec_of("green", filters=[{"type": "min_payout", "value": 80}])
    assert readiness.of(s, rows(10), {}, {})["state"] == "warming"
    r = readiness.of(s, rows(last=(10.0, 10.5, 9.9, 10.4)), {}, {"payout": 70.0})
    assert r["state"] == "blocked" and "payout" in r["reason"] and r["p"] == 1.0          # every rule holds but the payout filter refuses it
    assert readiness.of(s, rows(last=(10.0, 10.5, 9.9, 10.4)), {}, {"payout": 90.0})["state"] == "ready"


def test_the_endpoint_function_reads_the_forming_candle_for_every_running_strategy():
    eng = Engine(":memory:", seed_builtins=True)
    sym, tf = "T_otc", 60
    for r in rows(120):
        eng._series(sym, tf).put(r)
    last = eng._series(sym, tf).tail(1)[0]
    eng.open[(sym, tf)] = {"t": last["t"] + 60, "o": 10.0, "h": 10.4, "l": 9.95, "c": 10.35, "n": 5, "first": 0.0, "last": 0.0, "partial": False}
    out = control.readiness(eng, sym, tf)
    assert out["forming"] and out["candle_t"] == last["t"] + 60 and out["price"] == 10.35
    assert len(out["strategies"]) >= 10
    for sid, r in out["strategies"].items():
        assert r["state"] in ("building", "ready", "blocked", "warming", "error") and 0.0 <= r["p"] <= 1.0, (sid, r)
        if r["state"] == "ready":
            assert r["lead"] in ("CALL", "PUT") and r["p"] == 1.0
    again = control.readiness(eng, sym, tf)
    assert again is out                                                                  # served from the one-second cache
    cons = next(r for sid, r in out["strategies"].items() if eng.strategies[int(sid)]["spec"]["kind"] == "meta")      # the consensus strategy is read from its members
    assert cons["state"] in ("building", "ready", "blocked") and (cons["state"] != "blocked" or "disagree" in cons["reason"])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
