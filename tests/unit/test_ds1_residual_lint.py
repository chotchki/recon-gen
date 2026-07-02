"""DS.1 — the money-family op-whitelist / branch-free lint.

The money residuals must be symbolically executable by DST.1's z3
adapter running the SAME function bodies over ArithRef terms. Two
failure classes motivate the rules:

- SILENT concrete/symbolic divergence: ``/``, ``//`` and ``%`` differ
  between Python (floor) and SMT-LIB (Euclidean) on negative operands —
  the same source line quietly means two different things.
- LOUD-but-late failures: data-dependent ``if``/``IfExp`` on symbolic
  values raise at solve time, far from the law that owns them.

Rules over every function in ``MONEY_FAMILY_RESIDUALS``:
  1. an ``ast.If`` is allowed ONLY as a cell-existence guard — its body
     is exactly ``return None`` and it has no ``else``; all data
     selection goes through ``when()`` (both branches eagerly
     evaluated, selection is data-flow);
  2. no ``IfExp`` / ``While`` anywhere;
  3. no ``/ // % **`` operators;
  4. no calls to ``abs / min / max / sorted / round / float / divmod``
     (express |x| as ``when(x < ZERO, -x, x)``).
"""
from __future__ import annotations

import ast
import inspect

import pytest

from recon_gen.common.spine import residuals

BANNED_CALLS = frozenset({"abs", "min", "max", "sorted", "round", "float", "divmod"})
BANNED_OPS = (ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)


def _is_none_guard(node: ast.If) -> bool:
    """``if <cond>: return None`` with no else — the only If allowed."""
    if node.orelse:
        return False
    if len(node.body) != 1:
        return False
    stmt = node.body[0]
    return (
        isinstance(stmt, ast.Return)
        and (
            stmt.value is None
            or (isinstance(stmt.value, ast.Constant) and stmt.value.value is None)
        )
    )


@pytest.mark.parametrize(
    "fn",
    residuals.MONEY_FAMILY_RESIDUALS,
    ids=[getattr(f, "__name__", repr(f)) for f in residuals.MONEY_FAMILY_RESIDUALS],
)
def test_money_residual_is_branch_free_and_whitelisted(fn: object) -> None:
    source = inspect.getsource(fn)  # type: ignore[arg-type]: registry tuple is object-typed; entries are functions by construction
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            assert _is_none_guard(node), (
                f"{fn} line {node.lineno}: data-dependent `if` — only "
                "`if <cond>: return None` cell-existence guards are allowed; "
                "use when() for selection"
            )
        assert not isinstance(node, ast.IfExp), (
            f"{fn} line {getattr(node, 'lineno', '?')}: conditional expression — use when()"
        )
        assert not isinstance(node, ast.While), f"{fn}: while loop in a residual body"
        if isinstance(node, ast.BinOp):
            assert not isinstance(node.op, BANNED_OPS), (
                f"{fn} line {node.lineno}: banned operator "
                f"{type(node.op).__name__} — silent concrete/symbolic divergence"
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in BANNED_CALLS, (
                f"{fn} line {node.lineno}: banned call {node.func.id}() — "
                "express |x| as when(x < ZERO, -x, x)"
            )


def test_money_family_registry_is_nonempty() -> None:
    """The lint boundary walks this tuple — an empty tuple lints nothing
    and reads as green."""
    assert len(residuals.MONEY_FAMILY_RESIDUALS) >= 5
