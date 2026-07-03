"""DS.5.2 — the definition-site annotation for math invariants.

Every concrete ``Invariant`` detector carries ``@math_invariant(...)`` at
its class definition, naming the matview it reads, its ``MathKind``, its
canonical residual (the DS.1 law), and its KAT file. The annotation is
the DECLARATION site — but it is NOT the source of truth. The completeness
gate (``tests/unit/test_ds52_completeness_gate.py``) cross-checks it BOTH
directions against the emitted-artifact walk:

- every annotation's ``matview`` is actually emitted by ``emit_schema``
  AND refreshed by ``refresh_matviews_sql`` (an annotation pointing at a
  renamed / dropped / never-refreshed matview fails loud);
- every emitted matview that is not on the explicit PLUMBING exclusion
  list has an annotation (a detector matview emitted with no annotated
  class fails loud — the balance_cadence_gap canary, inventory row 14).

That second direction is the whole point: emitted artifacts stay the
ground truth, so an annotation nothing cross-checks would be "a registry
in another costume" (operator, DS.0 sign-off — "I really hate registries,
they fail silently; annotation with an AST check"). The AST half of the
gate enforces that every ``class *Invariant`` in ``common/spine/`` carries
the decorator; the emitted-walk half enforces that the set of decorated
matviews is EXACTLY the emitted detector set. Neither can drift silently:
a new detector matview with no annotation breaks the build, and an
annotation with no matview breaks the build.

The KAT + residual fields are consumed downstream (the DS.1 KAT gate can
discover vectors by annotation; the DST proof cache keys laws by it), but
their PRESENCE is the gate's concern here: MONEY / THRESHOLD /
CARDINALITY / DERIVATION kinds must carry a residual + KAT; PROBABILISTIC
carries neither (its contract is DS.4's exact-Q tolerance, named in
``note``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from recon_gen.common.spine.residuals import MathKind

#: Class attribute the decorator stamps; read via ``math_invariant_spec``.
MATH_INVARIANT_ATTR = "__math_invariant__"

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class MathInvariantSpec:
    """The declaration a detector class carries. ``matview`` is the
    emitted-matview suffix (cross-checked ⊆ emitted ∩ refreshed);
    ``residual`` + ``kat_file`` are the DS.1 law and its hand-derived
    vectors (both ``None`` only for ``PROBABILISTIC``, whose contract is
    DS.4's tolerance sweep, named in ``note``)."""

    matview: str
    kind: MathKind
    residual: Callable[..., object] | None
    kat_file: str | None
    note: str

    def __post_init__(self) -> None:
        probabilistic = self.kind is MathKind.PROBABILISTIC
        if probabilistic:
            if self.residual is not None or self.kat_file is not None:
                raise ValueError(
                    f"{self.matview}: PROBABILISTIC carries no residual / "
                    f"KAT (its contract is DS.4's tolerance sweep)",
                )
            if not self.note:
                raise ValueError(
                    f"{self.matview}: PROBABILISTIC must name its contract "
                    f"in `note`",
                )
        else:
            if self.residual is None:
                raise ValueError(
                    f"{self.matview}: {self.kind.name} must carry a residual",
                )
            if self.kat_file is None:
                raise ValueError(
                    f"{self.matview}: {self.kind.name} must carry a KAT file",
                )


def math_invariant(
    *,
    matview: str,
    kind: MathKind,
    residual: Callable[..., object] | None = None,
    kat_file: str | None = None,
    note: str = "",
) -> Callable[[type[_T]], type[_T]]:
    """Stamp a detector class with its ``MathInvariantSpec``. The
    completeness gate reads it back off every ``*Invariant`` class."""
    spec = MathInvariantSpec(
        matview=matview, kind=kind, residual=residual,
        kat_file=kat_file, note=note,
    )

    def wrap(cls: type[_T]) -> type[_T]:
        setattr(cls, MATH_INVARIANT_ATTR, spec)
        return cls

    return wrap


def math_invariant_spec(cls: type[object]) -> MathInvariantSpec | None:
    """Return the class's own spec, or ``None`` if unannotated. Reads
    ``__dict__`` (not ``getattr``) so a subclass never inherits a
    parent's spec by accident — each detector declares its own."""
    return cls.__dict__.get(MATH_INVARIANT_ATTR)
