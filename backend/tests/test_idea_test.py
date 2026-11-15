"""The one-command idea test: noise must FAIL, a real habit must PASS, the ledger must make proof harder, and the verdict rules must be the pre-registered ones.
Run from backend/:  .venv\\Scripts\\python tests\\test_idea_test.py"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE / ".." / "tools"))
sys.path.insert(0, str(HERE))

import idea_test as I  # noqa: E402
from app.engine import Engine  # noqa: E402
from test_backtest import put_candles  # noqa: E402
from test_momentum_ai import waves  # noqa: E402


def _engine(persist, pairs=4, n=4500):
    eng = Engine(":memory:")
    for i in range(pairs):
        sym = f"P{i}_otc"
        eng.instruments[sym] = {"symbol": sym, "name": sym, "payout": 88, "open": True, "market": "OTC"}
        put_candles(eng, sym, waves(n, 10 + i, persist))
    return eng


FOLLOW = I.spec_from_expr("green", "red", [60], 80)                          # "the next candle goes the way the last one went"


def test_a_fair_coin_idea_fails_and_says_by_how_much():
    res = I.evaluate(_engine(0.5).db, FOLLOW, {"tfs": [60]}, ledger=None, name="noise")
    assert res["verdict"] == "FAIL" and "below" in res["why"], (res["verdict"], res["why"])
    assert not all(c["ok"] for c in res["checks"]) and res["noise_band"][0] < 0.5 < res["noise_band"][1]


def test_a_real_habit_passes_every_check_on_unseen_data():
    res = I.evaluate(_engine(0.75).db, FOLLOW, {"tfs": [60]}, ledger=None, name="real")
    assert res["verdict"] == "PASS" and all(c["ok"] for c in res["checks"]), [(c["name"], c["text"]) for c in res["checks"] if not c["ok"]]
    assert res["newest"]["win"] > 0.7 and res["newest"]["lo"] > res["newest"]["be"]


def test_too_few_trades_cannot_pass_however_good_they_look():
    res = I.evaluate(_engine(0.9, pairs=1, n=700).db, FOLLOW, {"tfs": [60]}, ledger=None, name="tiny")
    assert res["verdict"] != "PASS" and not res["checks"][0]["ok"]


def test_every_idea_tried_makes_the_proof_harder():
    eng = _engine(0.5)
    led = Path(tempfile.mkdtemp()) / "ledger.jsonl"
    first = I.evaluate(eng.db, FOLLOW, {"tfs": [60]}, ledger=led, name="a")
    for i in range(30):
        I.evaluate(eng.db, FOLLOW, {"tfs": [60]}, ledger=led, name=f"b{i}")
    last = I.evaluate(eng.db, FOLLOW, {"tfs": [60]}, ledger=led, name="z")
    assert I.ideas_tried(led) == 32 and last["ideas_counted"] == 32 and first["ideas_counted"] == 1
    assert last["z"] > first["z"] + 0.8                                              # 95% range is wider after 32 ideas (z 1.96 -> ~3.1)
    assert json.loads(led.read_text().splitlines()[-1])["name"] == "z"


def test_an_invalid_idea_is_rejected_with_the_reason():
    bad = I.spec_from_expr("nonsense_name > 1", "red", [60], 80)
    try:
        I.evaluate(_engine(0.5, pairs=1, n=300).db, bad, {"tfs": [60]}, ledger=None)
        raise AssertionError("should have been rejected")
    except ValueError as e:
        assert "invalid spec" in str(e) and "nonsense_name" in str(e)


def test_the_report_text_names_the_verdict_and_the_checks():
    res = I.evaluate(_engine(0.5, pairs=2, n=3000).db, FOLLOW, {"tfs": [60]}, ledger=None, name="text")
    txt = I.render(res)
    assert "VERDICT" in txt and "checks:" in txt and "break-even" in txt and "luck alone" in txt


def test_the_ai_momentum_replay_fails_on_noise_and_passes_when_momentum_really_persists():
    noise = I.judge(I.ai_momentum_trades(_engine(0.5, pairs=4, n=5000).db, 60, 80))
    assert noise["verdict"] == "FAIL", noise["why"]
    real = I.judge(I.ai_momentum_trades(_engine(0.75, pairs=4, n=5000).db, 60, 80))
    assert real["verdict"] == "PASS" and real["newest"]["win"] > 0.65, [(c["name"], c["text"]) for c in real["checks"] if not c["ok"]]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
