"""EB.1 — the symbolic-execution adapter.

The whole point of the Z3 lane, and the reason it can live on the chain
at all: the REAL ``residuals.py`` money-law functions run UNCHANGED over
z3 terms. There is no second copy of the law to keep in sync (the
"fifth-copy problem" the DS.0 investigation rejected) — the same Python
object that runs concrete in the KAT tests runs symbolic in the prover.

How the residual bodies already permit it (DS.1 built them for this):

- Every money value flows through ``Cents`` arithmetic, which delegates
  to ``.value`` — and z3 overloads ``+ - *`` on its Int terms, so
  ``Cents(a) - Cents(b)`` with z3-term values just works.
- Every value SELECTION goes through the ``when()`` combinator (both
  branches eager, no Python ``if`` on a money condition), so swapping
  ``when`` for a z3.If-emitting version is the ONE seam.
- The op set is a lint-enforced WHITELIST (add / sub / neg / int-mul /
  comparisons / ``when`` — no ``/ // % abs min max``), so nothing in a
  body has a symbolic-vs-concrete divergence (Python floor-div vs
  SMT-LIB euclidean would be silent).

The one thing the DS.0 doc undersold (found building this): ``Cents`` is
``@dataclass(order=True)``, so ``stored < ZERO`` is a TUPLE comparison
that calls ``bool()`` on the z3 term and raises. And residuals construct
``Cents(...)`` INTERNALLY (``over = flow - cap``) then compare the result
(``when(over > ZERO, ...)``) — so feeding symbolic INPUTS isn't enough;
the ``Cents`` class the body sees, its ``when``, and its ``ZERO`` must
all be z3-aware. The adapter monkeypatches exactly those three module
names for the duration of one symbolic run and restores them — no law
body is touched, and nothing leaks past the ``with`` block.

Scope: the MONEY family (``MONEY_FAMILY_RESIDUALS``). The threshold
residuals use floor division (out of the whitelist by design), the
cardinality residuals count over sets, and the derivation residuals walk
a BFS — none is symbolically executable this way, and all have
near-vacuous forall-Z theorems, so they stay concrete (DS.0 decision).
"""
# z3-solver ships no type stubs; relax ONLY the untyped-cascade rules
# for this z3-boundary file (the rest of tests/ stays fully strict).
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportAttributeAccessIssue=false, reportArgumentType=false
from __future__ import annotations

import contextlib
from collections.abc import Generator
from typing import Any

import z3

from recon_gen.common.spine import residuals as _residuals


class SymCents:
    """A ``Cents``-shaped money value whose magnitude is a z3 Int term.

    Duck-types the surface the money residuals touch: ``.value`` (the z3
    term), ``+ - * neg`` (return ``SymCents``) and the comparisons (return
    z3 ``BoolRef``). Accepts an int, a z3 ``ArithRef`` or another
    ``SymCents`` and normalizes to a single z3 Int term, so the residuals'
    internal ``Cents(sum(...))`` constructions — where the sum may already
    be symbolic — land cleanly."""

    __slots__ = ("_v",)

    def __init__(self, value: Any) -> None:
        if isinstance(value, SymCents):
            self._v: z3.ArithRef = value._v
        elif isinstance(value, bool):
            # Guard: a bool is an int in Python, but a money value is
            # never a bool — catch a mis-wire loudly instead of IntVal(1).
            raise TypeError("SymCents value must be int / z3 Int, not bool")
        elif isinstance(value, int):
            self._v = z3.IntVal(value)
        else:
            self._v = value  # z3 ArithRef

    @property
    def value(self) -> z3.ArithRef:
        return self._v

    @staticmethod
    def _term(other: Any) -> Any:
        if isinstance(other, SymCents):
            return other._v
        if isinstance(other, int) and not isinstance(other, bool):
            return z3.IntVal(other)
        return other  # z3 term

    def __add__(self, other: Any) -> "SymCents":
        return SymCents(self._v + SymCents._term(other))

    def __radd__(self, other: Any) -> "SymCents":
        # ``sum(<SymCents ...>)`` starts at int 0.
        return SymCents(SymCents._term(other) + self._v)

    def __sub__(self, other: Any) -> "SymCents":
        return SymCents(self._v - SymCents._term(other))

    def __neg__(self) -> "SymCents":
        return SymCents(-self._v)

    def __mul__(self, n: int) -> "SymCents":
        # Repeat-count / scalar (EB.2 scale-homogeneity uses this).
        return SymCents(self._v * n)

    def __lt__(self, other: Any) -> z3.BoolRef:
        return self._v < SymCents._term(other)

    def __le__(self, other: Any) -> z3.BoolRef:
        return self._v <= SymCents._term(other)

    def __gt__(self, other: Any) -> z3.BoolRef:
        return self._v > SymCents._term(other)

    def __ge__(self, other: Any) -> z3.BoolRef:
        return self._v >= SymCents._term(other)

    def __eq__(self, other: Any) -> z3.BoolRef:  # type: ignore[override]: money equality is a z3 constraint here, not Python identity — this object is never hashed / set-membered in a residual
        return self._v == SymCents._term(other)

    __hash__ = None  # type: ignore[assignment]: unhashable on purpose — a symbolic money value must never key a dict / set (that path is structural + stays concrete)

    def __repr__(self) -> str:
        return f"SymCents({self._v})"


def _sym_int(x: Any) -> Any:
    """Extract the Int-sorted z3 term from a when() branch (SymCents /
    int / raw z3 term)."""
    if isinstance(x, SymCents):
        return x._v
    if isinstance(x, int) and not isinstance(x, bool):
        return z3.IntVal(x)
    return x


def sym_when(cond: Any, then: Any, otherwise: Any) -> Any:
    """The symbolic ``when``: a z3 ``BoolRef`` condition becomes a
    ``z3.If`` (wrapped back into ``SymCents`` — every BoolRef-cond call
    site in the money family selects between Int-sorted money values); a
    concrete ``bool`` condition (a STRUCTURAL predicate — status, date,
    rail — that stayed concrete) selects normally."""
    if isinstance(cond, z3.BoolRef):
        return SymCents(z3.If(cond, _sym_int(then), _sym_int(otherwise)))
    return then if cond else otherwise


@contextlib.contextmanager
def _symbolic_residual_module() -> Generator[None, None, None]:
    """Swap ``residuals.Cents`` / ``when`` / ``ZERO`` for their z3-aware
    forms for the duration of one symbolic run, then restore. No law body
    changes; the swap is invisible outside this block."""
    saved_cents = _residuals.Cents
    saved_when = _residuals.when
    saved_zero = _residuals.ZERO
    try:
        _residuals.Cents = SymCents  # type: ignore[misc]: deliberate test-time swap — the residual bodies see a z3-aware Cents; restored in finally
        _residuals.when = sym_when  # type: ignore[assignment]: same seam swap, restored in finally
        _residuals.ZERO = SymCents(0)  # type: ignore[assignment]: symbolic zero for the money comparisons; restored in finally
        yield
    finally:
        _residuals.Cents = saved_cents  # type: ignore[misc]: restore the real Cents
        _residuals.when = saved_when  # type: ignore[assignment]: restore the real when
        _residuals.ZERO = saved_zero  # type: ignore[assignment]: restore the real ZERO


def symbolic_execute(fn: Any, *args: Any, **kwargs: Any) -> z3.ArithRef | None:
    """Run a money residual over z3 terms and return its result as a z3
    Int term (or ``None`` when the residual has no cell for the inputs —
    the cell-existence guards are STRUCTURAL, so they still decide
    concretely). The caller supplies a ``ResidualState`` whose money
    fields are ``SymCents`` (see ``symstate``)."""
    with _symbolic_residual_module():
        result = fn(*args, **kwargs)
    if result is None:
        return None
    if isinstance(result, SymCents):
        return result.value
    # A residual that returned a bare int/term (e.g. a fully-concrete
    # cell) — normalize to a z3 term.
    return _sym_int(result)


def bind_and_eval(term: z3.ArithRef, bindings: list[tuple[Any, int]]) -> int:
    """Substitute concrete ints for the free vars, simplify to a numeral,
    and return it as a plain ``int``. The single place z3's untyped
    numeral API is touched — raises loudly if the term doesn't reduce
    (a bug in the symbolic execution, not an expected outcome)."""
    substituted = z3.substitute(
        term, *[(var, z3.IntVal(val)) for var, val in bindings],
    )
    simplified: Any = z3.simplify(substituted)
    if not z3.is_int_value(simplified):
        raise AssertionError(
            f"symbolic term did not reduce to a numeral after binding: "
            f"{simplified}",
        )
    return int(simplified.as_long())
