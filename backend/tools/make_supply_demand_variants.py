"""Writes looser versions of examples/supply-demand-zone.json to examples/supply-demand/*.json (import them in Strategies -> New -> JSON tab).

The list is FIXED before any of them was tested. Each variant relaxes ONE rule of the strict strategy so you can see which rule costs the signals, plus two
combinations. Every variant keeps the core of the idea: a zone, a pullback that returns INTO the zone without breaking it, a confirmation candle in the trade's
direction, and (except the last) trading only with the trend.

    .venv\\Scripts\\python tools\\make_supply_demand_variants.py
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = json.loads((ROOT / "examples" / "supply-demand-zone.json").read_text(encoding="utf-8"))
OUT = ROOT / "examples" / "supply-demand"
ZONE, MOVE, PULL, CONFIRM = 0, 1, 2, 3                                   # step numbers of the pattern


def tweak(name: str, steps: dict[int, dict] | None = None, trend: str | None = None) -> dict:
    spec = copy.deepcopy(BASE)
    f = spec["factors"][0]
    f["name"] = name
    for side in ("call", "put"):
        for idx, change in (steps or {}).items():
            f[side]["steps"][idx].update(change)
    if trend:
        for flt in spec["filters"]:
            if flt["type"] == "trend_alignment":
                flt["value"] = trend
    return spec


VARIANTS: dict[str, tuple[str, dict]] = {
    "sd1-loose-confirmation": ("Confirmation candle need not be 'strong': any candle in the trade's colour that closes beyond the pullback",
        tweak("S/D zone - loose confirmation", {CONFIRM: {"shape": "any"}})),
    "sd2-any-base": ("The zone may be any candle before the move (not only a small-body base)",
        tweak("S/D zone - any base candle", {ZONE: {"shape": "any"}})),
    "sd3-strong-move": ("The move away from the zone is judged as 'strong' (body is 60%+ of the candle's range) instead of 'long' (body 1.3x the recent average) - a different strength test, so it can add AND drop signals",
        tweak("S/D zone - strong (not long) move", {MOVE: {"shape": "strong"}})),
    "sd4-all-loose": ("All three loosenings together, still trend-aligned",
        tweak("S/D zone - all loosened", {CONFIRM: {"shape": "any"}, ZONE: {"shape": "any"}, MOVE: {"shape": "strong"}})),
    "sd5-all-loose-no-counter-trend": ("All loosenings, and the trend rule relaxed to 'not against' (sideways markets allowed, counter-trend still refused)",
        tweak("S/D zone - all loosened, not against trend", {CONFIRM: {"shape": "any"}, ZONE: {"shape": "any"}, MOVE: {"shape": "strong"}}, trend="not_against")),
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for key, (desc, spec) in VARIANTS.items():
        (OUT / f"{key}.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        print(f"{key:34s} {desc}")
    print(f"\nwritten to {OUT}")


if __name__ == "__main__":
    main()
