"""Strategy spec validation + normalisation. A spec is plain JSON (never code); see PLAN-ADMIN.md section 2.

validate_spec(raw) -> {"ok": bool, "errors": [...], "warnings": [...], "spec": normalised_spec_or_None}
"""
from __future__ import annotations

import re

from . import expr, indreg, patterns
from .strategy import FN_FACTORS

TFS = (60, 300, 900)
ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
HOURS_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")
# names always available inside expressions (all plain series)
BASE_NAMES = ["open", "high", "low", "close", "ticks", "body", "range", "upper_wick", "lower_wick", "green", "red", "streak", "hlc3",
              "hour", "weekday", "bull_engulf", "bear_engulf", "hammer", "star", "doji", "inside_bar"]
FILTERS = {                      # type -> default params
    "adx_min": {"value": 20, "n": 14}, "adx_max": {"value": 30, "n": 14},
    "atr_ratio_min": {"value": 0.7}, "atr_ratio_max": {"value": 2.0},
    "hours": {"include": [], "exclude": []}, "weekdays": {"include": [], "exclude": []},
    "trend_alignment": {"value": "with"}, "min_payout": {"value": 80}, "direction_only": {"value": "CALL"},
    # skip a signal when the same direction already signalled within the previous `gap` candles: only the FIRST signal of a back-to-back run is kept
    "one_per_run": {"gap": 1},
}
RUN_GAP_MAX = 5
TREND_VALUES = ("with", "against", "not_against", "not_with")
GATE_DEFAULTS = {"min_candles": 60, "min_agree": 1, "max_opposing_weight": 100, "min_strength": 0, "tier_a": None}


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate_spec(raw) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(raw, dict):
        return {"ok": False, "errors": ["spec must be a JSON object"], "warnings": [], "spec": None}

    kind = raw.get("kind", "factors")
    if kind not in ("factors", "meta"):
        errors.append("kind must be 'factors' or 'meta'")
    tfs = raw.get("timeframes", list(TFS))
    if not (isinstance(tfs, list) and tfs and all(t in TFS for t in tfs)):
        errors.append(f"timeframes must be a non-empty list drawn from {list(TFS)}")
        tfs = list(TFS)
    scope_in = raw.get("scope") or {}
    scope = {"symbols": scope_in.get("symbols") or None, "market": scope_in.get("market") or None}
    if scope["symbols"] is not None and not (isinstance(scope["symbols"], list) and all(isinstance(s, str) for s in scope["symbols"])):
        errors.append("scope.symbols must be a list of symbols or null")
    if scope["market"] not in (None, "OTC", "LIVE"):
        errors.append("scope.market must be 'OTC', 'LIVE' or null")
    if raw.get("expiry_candles", 1) != 1:
        errors.append("expiry_candles other than 1 is not supported yet")

    out = {"engine": "spec/1", "kind": kind, "timeframes": sorted(set(tfs)), "scope": scope, "indicators": [], "factors": [],
           "gates": dict(GATE_DEFAULTS), "filters": [], "expiry_candles": 1}

    # ---- gates
    g_in = raw.get("gates") or {}
    if not isinstance(g_in, dict):
        errors.append("gates must be an object")
        g_in = {}
    for k, v in g_in.items():
        if k not in GATE_DEFAULTS:
            errors.append(f"unknown gate '{k}'")
    gates = {**GATE_DEFAULTS, **{k: v for k, v in g_in.items() if k in GATE_DEFAULTS}}
    for k, lo, hi in (("min_candles", 20, 400), ("min_agree", 1, 12), ("max_opposing_weight", 0, 100), ("min_strength", 0, 100)):
        if not _num(gates[k]) or not lo <= gates[k] <= hi:
            errors.append(f"gates.{k} must be a number between {lo} and {hi}")
    ta = gates["tier_a"]
    if ta is not None and not (isinstance(ta, dict) and _num(ta.get("agree")) and _num(ta.get("strength"))):
        errors.append("gates.tier_a must be {\"agree\": n, \"strength\": n} or null")
    out["gates"] = gates

    # ---- meta (consensus) strategies
    if kind == "meta":
        c = raw.get("consensus")
        if not isinstance(c, dict):
            errors.append("a meta strategy needs a 'consensus' object")
        else:
            members = c.get("members", "all_active")
            if members != "all_active" and not (isinstance(members, list) and members and all(isinstance(m, str) for m in members)):
                errors.append("consensus.members must be 'all_active' or a list of strategy keys")
            ma = c.get("min_agree", 2)
            if not _num(ma) or not 1 <= ma <= 20:
                errors.append("consensus.min_agree must be 1-20")
            out["consensus"] = {"members": members, "min_agree": int(ma) if _num(ma) else 2}
        return {"ok": not errors, "errors": errors, "warnings": warnings, "spec": None if errors else out}

    # ---- indicators
    known: dict[str, list[str] | None] = {n: None for n in BASE_NAMES}
    inds = raw.get("indicators") or []
    if not isinstance(inds, list) or len(inds) > 12:
        errors.append("indicators must be a list of at most 12 items")
        inds = []
    for i, it in enumerate(inds):
        if not isinstance(it, dict):
            errors.append(f"indicators[{i}] must be an object")
            continue
        iid, typ = it.get("id"), it.get("type")
        if not isinstance(iid, str) or not ID_RE.match(iid) or iid in known:
            errors.append(f"indicators[{i}].id must be a unique lowercase identifier not clashing with built-in names ({iid!r})")
            continue
        if typ not in indreg.REGISTRY:
            errors.append(f"indicators[{i}].type '{typ}' unknown (available: {', '.join(sorted(indreg.REGISTRY))})")
            continue
        params = {k: v for k, v in it.items() if k not in ("id", "type")}
        dflt = indreg.defaults(typ)
        bad = [k for k in params if k not in dflt]
        if bad:
            errors.append(f"indicators[{i}] ({iid}): unknown parameter(s) {bad}; allowed {list(dflt)}")
        if any(not _num(v) or v <= 0 for v in params.values()):
            errors.append(f"indicators[{i}] ({iid}): parameters must be positive numbers")
        merged = {**dflt, **{k: v for k, v in params.items() if k in dflt}}
        out["indicators"].append({"id": iid, "type": typ, **merged})
        known[iid] = indreg.fields(typ)

    # ---- factors
    facs = raw.get("factors")
    if not isinstance(facs, list) or not 1 <= len(facs) <= 12:
        errors.append("factors must be a list of 1-12 items")
        facs = []
    seen = set()
    for i, f in enumerate(facs):
        if not isinstance(f, dict):
            errors.append(f"factors[{i}] must be an object")
            continue
        fid = f.get("id")
        if not isinstance(fid, str) or not ID_RE.match(fid) or fid in seen:
            errors.append(f"factors[{i}].id must be a unique lowercase identifier ({fid!r})")
            continue
        seen.add(fid)
        name, w, fkind = f.get("name") or fid, f.get("weight", 100), f.get("kind", "expr")
        if not _num(w) or not 0 < w <= 1000:
            errors.append(f"factors[{i}] ({fid}): weight must be a number in (0, 1000]")
            continue
        nf = {"id": fid, "name": str(name)[:60], "weight": w, "kind": fkind}
        if fkind == "fn":
            fn = f.get("fn")
            if fn not in FN_FACTORS:
                errors.append(f"factors[{i}] ({fid}): unknown fn '{fn}' (available: {', '.join(FN_FACTORS)})")
                continue
            dflt = FN_FACTORS[fn][1]
            params = f.get("params") or {}
            bad = [k for k in params if k not in dflt]
            if bad:
                errors.append(f"factors[{i}] ({fid}): unknown parameter(s) {bad}; allowed {list(dflt)}")
            nf.update(fn=fn, params={**dflt, **{k: v for k, v in params.items() if k in dflt}})
        elif fkind == "expr":
            call, put = f.get("call"), f.get("put")
            if not call and not put:
                errors.append(f"factors[{i}] ({fid}): needs a 'call' and/or 'put' condition")
                continue
            for side, text in (("call", call), ("put", put)):
                if not text:
                    continue
                try:
                    for nm, attr in expr.names_used(text):
                        if nm not in known:
                            raise expr.ExprError(f"unknown name '{nm}' (declare it under indicators)")
                        flds = known[nm]
                        if flds is None and attr:
                            raise expr.ExprError(f"'{nm}' is a single series, it has no field '{attr}'")
                        if flds is not None and (attr is None or attr not in flds):
                            raise expr.ExprError(f"'{nm}' needs one of its fields: {nm}.{{{', '.join(flds)}}}")
                except expr.ExprError as e:
                    errors.append(f"factors[{i}] ({fid}).{side}: {e}")
            vote = f.get("vote", 1)
            if not _num(vote) or not 0 < vote <= 1:
                errors.append(f"factors[{i}] ({fid}): vote must be in (0, 1]")
                vote = 1
            nf.update(call=call or None, put=put or None, vote=vote)
        elif fkind == "pattern":
            call, put = f.get("call"), f.get("put")
            if not call and not put:
                errors.append(f"factors[{i}] ({fid}): needs a 'call' and/or 'put' candle pattern")
                continue
            norm = {}
            for side, pat in (("call", call), ("put", put)):
                if not pat:
                    norm[side] = None
                    continue
                norm[side], errs = patterns.validate(pat, f"factors[{i}] ({fid}).{side}")
                errors.extend(errs)
            vote = f.get("vote", 1)
            if not _num(vote) or not 0 < vote <= 1:
                errors.append(f"factors[{i}] ({fid}): vote must be in (0, 1]")
                vote = 1
            nf.update(call=norm["call"], put=norm["put"], vote=vote)
        else:
            errors.append(f"factors[{i}] ({fid}): kind must be 'fn', 'expr' or 'pattern'")
            continue
        out["factors"].append(nf)

    if out["factors"] and gates["min_agree"] > len(out["factors"]):
        errors.append("gates.min_agree is larger than the number of factors")
    if len(out["factors"]) == 1 and gates["min_agree"] > 1:
        errors.append("a single-factor strategy needs min_agree = 1")

    # ---- filters
    fl = raw.get("filters") or []
    if not isinstance(fl, list) or len(fl) > 8:
        errors.append("filters must be a list of at most 8 items")
        fl = []
    for i, f in enumerate(fl):
        typ = f.get("type") if isinstance(f, dict) else None
        if typ not in FILTERS:
            errors.append(f"filters[{i}].type '{typ}' unknown (available: {', '.join(FILTERS)})")
            continue
        params = {k: v for k, v in f.items() if k != "type"}
        bad = [k for k in params if k not in FILTERS[typ]]
        if bad:
            errors.append(f"filters[{i}] ({typ}): unknown parameter(s) {bad}")
        nf = {"type": typ, **FILTERS[typ], **{k: v for k, v in params.items() if k in FILTERS[typ]}}
        if typ == "hours":
            for k in ("include", "exclude"):
                if not (isinstance(nf[k], list) and all(isinstance(h, str) and HOURS_RE.match(h) for h in nf[k])):
                    errors.append(f"filters[{i}] (hours).{k} must be a list of 'HH:MM-HH:MM' (UTC) strings")
        elif typ == "weekdays":
            for k in ("include", "exclude"):
                if not (isinstance(nf[k], list) and all(isinstance(d, int) and 0 <= d <= 6 for d in nf[k])):
                    errors.append(f"filters[{i}] (weekdays).{k} must be a list of 0-6 (Mon=0)")
        elif typ == "trend_alignment":
            if nf["value"] not in TREND_VALUES:
                errors.append(f"filters[{i}] (trend_alignment).value must be one of {list(TREND_VALUES)}")
        elif typ == "direction_only":
            if nf["value"] not in ("CALL", "PUT"):
                errors.append(f"filters[{i}] (direction_only).value must be 'CALL' or 'PUT'")
        elif typ == "one_per_run":
            if not (isinstance(nf["gap"], int) and not isinstance(nf["gap"], bool) and 1 <= nf["gap"] <= RUN_GAP_MAX):
                errors.append(f"filters[{i}] (one_per_run).gap must be a whole number from 1 to {RUN_GAP_MAX}")
        elif not _num(nf["value"]):
            errors.append(f"filters[{i}] ({typ}).value must be a number")
        out["filters"].append(nf)

    uses = {t for f in out["factors"] if f["kind"] == "expr" for t in (f.get("call"), f.get("put")) if t}
    if gates["min_candles"] < 60 and any("ema" in (i["type"]) and i.get("n", 0) > 50 for i in out["indicators"]):
        warnings.append("a long EMA with min_candles < 60 may not be warmed up")
    if not uses and all(f["kind"] == "expr" for f in out["factors"]):
        warnings.append("no conditions defined")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "spec": None if errors else out}
