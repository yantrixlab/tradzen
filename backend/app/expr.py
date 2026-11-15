"""Safe expression language for strategy conditions. Never uses eval/exec: expressions are parsed with `ast`, checked against a
whitelist of node types, then evaluated vectorised on pandas Series (so the same expression serves live signals and backtests).

Examples:  rsi < 30 and close > ema200   |   cross_above(macd.hist, 0)   |   bull_engulf and low <= lowest(low, 20)[1] + 0.3 * atr
Lookback:  x[1] is the value one candle ago.   Fields:  bb.lower, macd.signal, adx.pdi ...
"""
from __future__ import annotations

import ast
import operator
from functools import lru_cache
from types import SimpleNamespace

import numpy as np
import pandas as pd

MAX_LEN = 400
MAX_NODES = 120
MAX_WINDOW = 500


class ExprError(ValueError):
    pass


_ALLOWED_NODES = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd, ast.BinOp, ast.Add, ast.Sub,
                  ast.Mult, ast.Div, ast.Mod, ast.Compare, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq, ast.Call,
                  ast.Name, ast.Load, ast.Attribute, ast.Subscript, ast.Constant)
_CMP = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Eq: operator.eq, ast.NotEq: operator.ne}
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Mod: operator.mod}
# function name -> (min args, max args)
FUNCS = {"cross_above": (2, 2), "cross_below": (2, 2), "highest": (2, 2), "lowest": (2, 2), "abs": (1, 1), "min": (2, 2), "max": (2, 2),
         "sma": (2, 2), "ema": (2, 2), "change": (1, 2), "sign": (1, 1), "count": (2, 2), "rising": (2, 2), "falling": (2, 2)}


@lru_cache(maxsize=512)
def parse(text: str) -> ast.Expression:
    if not isinstance(text, str) or not text.strip():
        raise ExprError("expression is empty")
    if len(text) > MAX_LEN:
        raise ExprError(f"expression longer than {MAX_LEN} characters")
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as e:
        raise ExprError(f"syntax error: {e.msg}") from None
    count = 0
    for node in ast.walk(tree):
        count += 1
        if not isinstance(node, _ALLOWED_NODES):
            raise ExprError(f"'{type(node).__name__}' is not allowed in expressions")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS:
                raise ExprError(f"unknown function (allowed: {', '.join(sorted(FUNCS))})")
            lo, hi = FUNCS[node.func.id]
            if node.keywords or not lo <= len(node.args) <= hi:
                raise ExprError(f"{node.func.id}() takes {lo}-{hi} positional arguments" if lo != hi else f"{node.func.id}() takes {lo} arguments")
        if isinstance(node, ast.Attribute) and not isinstance(node.value, ast.Name):
            raise ExprError("only simple 'name.field' attributes are allowed")
        if isinstance(node, ast.Subscript):
            if not (isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int) and 0 <= node.slice.value <= MAX_WINDOW):
                raise ExprError(f"lookback must be an integer constant 0-{MAX_WINDOW}, e.g. x[1]")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, bool)):
            raise ExprError("only numbers are allowed as constants")
    if count > MAX_NODES:
        raise ExprError("expression too complex")
    return tree


def names_used(text: str) -> set[tuple[str, str | None]]:
    """(name, field-or-None) pairs referenced by an expression (function names excluded)."""
    tree = parse(text)
    out: set[tuple[str, str | None]] = set()
    func_names = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    attr_bases = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute):
            attr_bases.add(id(n.value))
            out.add((n.value.id, n.attr))
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and id(n) not in func_names and id(n) not in attr_bases:
            out.add((n.id, None))
    return out


def _ser(x, idx) -> pd.Series:
    return x if isinstance(x, pd.Series) else pd.Series(x, index=idx)


def _bool(x, idx) -> pd.Series:
    return _ser(x, idx).fillna(False).astype(bool)


def _const_int(x, what: str) -> int:
    if isinstance(x, pd.Series) or not isinstance(x, (int, float)) or int(x) != x or not 1 <= int(x) <= MAX_WINDOW:
        raise ExprError(f"{what} must be an integer constant 1-{MAX_WINDOW}")
    return int(x)


def evaluate(text: str, env: dict, idx: pd.Index) -> pd.Series:
    tree = parse(text)

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            if n.id not in env:
                raise ExprError(f"unknown name '{n.id}'")
            v = env[n.id]
            if isinstance(v, SimpleNamespace):
                raise ExprError(f"'{n.id}' has several fields, use {n.id}.<field> (fields: {', '.join(vars(v))})")
            return v
        if isinstance(n, ast.Attribute):
            obj = env.get(n.value.id)
            if not isinstance(obj, SimpleNamespace):
                raise ExprError(f"'{n.value.id}' has no fields")
            if not hasattr(obj, n.attr):
                raise ExprError(f"'{n.value.id}' has no field '{n.attr}' (fields: {', '.join(vars(obj))})")
            return getattr(obj, n.attr)
        if isinstance(n, ast.Subscript):
            base = ev(n.value)
            if not isinstance(base, pd.Series):
                raise ExprError("lookback [n] only works on series")
            return base.shift(n.slice.value)
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            if isinstance(n.op, ast.Not):
                return ~_bool(v, idx)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp):
            r = _BIN[type(n.op)](ev(n.left), ev(n.right))
            return r.replace([np.inf, -np.inf], np.nan) if isinstance(r, pd.Series) else r
        if isinstance(n, ast.Compare):
            left, res = ev(n.left), None
            for op, comp in zip(n.ops, n.comparators):
                right = ev(comp)
                part = _bool(_CMP[type(op)](left, right), idx)
                res = part if res is None else res & part
                left = right
            return res
        if isinstance(n, ast.BoolOp):
            vals = [_bool(ev(v), idx) for v in n.values]
            out = vals[0]
            for v in vals[1:]:
                out = (out & v) if isinstance(n.op, ast.And) else (out | v)
            return out
        if isinstance(n, ast.Call):
            return call(n.func.id, [ev(a) for a in n.args])
        raise ExprError(f"cannot evaluate {type(n).__name__}")

    def call(name, a):
        if name in ("cross_above", "cross_below"):
            x, y = _ser(a[0], idx), _ser(a[1], idx)
            if name == "cross_above":
                return (x > y) & (x.shift(1) <= y.shift(1))
            return (x < y) & (x.shift(1) >= y.shift(1))
        if name in ("highest", "lowest"):
            w = _const_int(a[1], "window")
            r = _ser(a[0], idx).rolling(w)
            return r.max() if name == "highest" else r.min()
        if name == "abs":
            return abs(a[0])
        if name in ("min", "max"):
            return np.minimum(_ser(a[0], idx), a[1]) if name == "min" else np.maximum(_ser(a[0], idx), a[1])
        if name == "sma":
            return _ser(a[0], idx).rolling(_const_int(a[1], "window")).mean()
        if name == "ema":
            w = _const_int(a[1], "window")
            return _ser(a[0], idx).ewm(span=w, adjust=False, min_periods=w).mean()
        if name == "change":
            return _ser(a[0], idx).diff(_const_int(a[1], "lag") if len(a) > 1 else 1)
        if name == "sign":
            return np.sign(_ser(a[0], idx))
        if name == "count":
            return _bool(a[0], idx).astype(int).rolling(_const_int(a[1], "window")).sum()
        if name == "rising":
            x = _ser(a[0], idx)
            return x > x.shift(_const_int(a[1], "lag"))
        if name == "falling":
            x = _ser(a[0], idx)
            return x < x.shift(_const_int(a[1], "lag"))
        raise ExprError(f"unknown function {name}")

    return _bool(ev(tree), idx)
