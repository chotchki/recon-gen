"""Unit tests for the promoted spine types (AS.1).

Covers the three new `common/spine/` modules: `Violation` (smart
constructor + identity equality), `Invariant` Protocol (runtime_check
+ shape), `ViolationGenerator` Protocol (runtime_check + shape).

Concrete invariants/generators land in AS.2; this file proves the
abstract layer composes cleanly and `runtime_checkable` does its job.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from recon_gen.common.spine import Invariant, Violation, ViolationGenerator


# ---------------------------------------------------------------------------
# Violation — smart constructor + identity-based equality.
# ---------------------------------------------------------------------------


def test_violation_of_normalizes_kwargs_into_frozenset() -> None:
    # The smart constructor flattens kwargs into a frozenset of
    # (column, value) pairs — the canonical identity shape.
    v = Violation.of("drift", account_id="acct-1", drift=5.0)
    assert v.invariant == "drift"
    assert ("account_id", "acct-1") in v.identity
    assert ("drift", 5.0) in v.identity


def test_violation_equality_is_kwarg_order_invariant() -> None:
    # Two callers passing the same kwargs in any order produce equal
    # Violations — the frozenset canonicalization is what enables
    # set-membership checks (`v in detect(conn)`) to work.
    a = Violation.of("drift", account_id="x", business_day=date(2030, 1, 1))
    b = Violation.of("drift", business_day=date(2030, 1, 1), account_id="x")
    assert a == b
    assert hash(a) == hash(b)


def test_violations_with_different_identity_are_distinct() -> None:
    a = Violation.of("drift", account_id="x", drift=5.0)
    b = Violation.of("drift", account_id="x", drift=7.0)
    assert a != b
    # Set semantics — both can live in the same detect() output.
    assert len({a, b}) == 2


def test_violations_carry_their_invariant_name() -> None:
    # The `invariant` field IS the spine link to the producing matview;
    # two breaches with the same identity but different invariant names
    # are different violations (drift on day X vs ledger_drift on day X
    # are real separate events even when their identity columns agree).
    a = Violation.of("drift", account_id="x", day=date(2030, 1, 1))
    b = Violation.of("ledger_drift", account_id="x", day=date(2030, 1, 1))
    assert a != b


def test_violation_is_frozen() -> None:
    # Hash + set membership require frozen identity. Pyright catches
    # the static violation; this asserts the runtime FrozenInstanceError.
    import dataclasses
    import pytest
    v = Violation.of("drift", account_id="x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.invariant = "ledger_drift"  # type: ignore[misc]: pyright correctly rejects assignment to a frozen dataclass — test asserts the runtime FrozenInstanceError fires


# ---------------------------------------------------------------------------
# Invariant Protocol — runtime_checkable + minimal shape (name + detect).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _TrivialInvariant:
    """Smallest possible Invariant impl — for shape testing only.
    AS.2 promotes the real concretes (DriftInvariant etc.)."""

    name: str = "trivial"

    def detect(self, conn: sqlite3.Connection) -> set[Violation]:
        return set()  # never fires; just satisfies the Protocol


def test_concrete_invariant_satisfies_protocol() -> None:
    # runtime_checkable means isinstance() works — AS.2's taxonomy
    # bookkeeping (the `invariant → {generators, views}` map) needs
    # runtime lookup, not just static type checking.
    inv = _TrivialInvariant()
    assert isinstance(inv, Invariant)


def test_invariant_protocol_rejects_missing_methods() -> None:
    @dataclass
    class _IncompleteInvariant:
        name: str = "incomplete"
        # no detect method
    assert not isinstance(_IncompleteInvariant(), Invariant)


def test_invariant_detect_returns_violations() -> None:
    # The shape contract — detect returns a set[Violation], no other
    # types. Sanity-check that the trivial impl conforms.
    inv = _TrivialInvariant()
    conn = sqlite3.connect(":memory:")
    try:
        result = inv.detect(conn)
        assert isinstance(result, set)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ViolationGenerator Protocol — intended + emit, runtime_checkable.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _TrivialGenerator:
    """Smallest possible ViolationGenerator impl — for shape testing
    only. AS.2/AS.3 promote the real concretes (DriftGenerator etc.)."""

    @property
    def intended(self) -> Violation:
        return Violation.of("trivial", marker="trivial")

    def emit(self, conn: sqlite3.Connection) -> None:
        # No rows; never actually causes a violation.
        return None


def test_concrete_generator_satisfies_protocol() -> None:
    gen = _TrivialGenerator()
    assert isinstance(gen, ViolationGenerator)


def test_generator_intended_is_a_violation() -> None:
    gen = _TrivialGenerator()
    intended = gen.intended
    assert isinstance(intended, Violation)
    assert intended.invariant == "trivial"


def test_generator_protocol_rejects_missing_methods() -> None:
    @dataclass
    class _IncompleteGen:
        @property
        def intended(self) -> Violation:
            return Violation.of("x")
        # no emit method
    assert not isinstance(_IncompleteGen(), ViolationGenerator)


# ---------------------------------------------------------------------------
# The spine composes — Protocol types thread together cleanly.
# ---------------------------------------------------------------------------


def test_invariant_and_generator_compose() -> None:
    # The link: gen.intended is a Violation, inv.detect returns
    # set[Violation], so `gen.intended in inv.detect(conn)` is the
    # spine's core round-trip type-correct shape. (Doesn't fire here —
    # the trivial impls don't write anything — but the types align.)
    inv = _TrivialInvariant()
    gen = _TrivialGenerator()
    conn = sqlite3.connect(":memory:")
    try:
        detected = inv.detect(conn)
        # No actual breach (trivial impls), so intended isn't IN detected.
        # The point is the membership check type-checks at all.
        assert gen.intended not in detected
    finally:
        conn.close()
