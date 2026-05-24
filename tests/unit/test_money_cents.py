"""AO.1 — ``common.money.Cents`` type-safety + arithmetic + boundary
conversion tests.

Goal: encode the "money is integer cents, full stop" invariant in the
type system. These tests exercise both the runtime behavior (arithmetic
+ frozen-ness + ordering) AND the type-safety stance — confirming that
mixing ``Cents`` with bare numerics fails at runtime (proving the
``AttributeError`` is real, not just an annotation).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from recon_gen.common.money import Cents


# -- from_dollars boundary ---------------------------------------------------


def test_from_dollars_str_round_dollar() -> None:
    assert Cents.from_dollars("75.00") == Cents(7500)


def test_from_dollars_decimal_negative() -> None:
    assert Cents.from_dollars(Decimal("-1500.50")) == Cents(-150050)


def test_from_dollars_sub_cent_penny_kept() -> None:
    """``"0.01"`` must yield exactly ``Cents(1)`` — quantize must not
    drop the penny. The whole reason this type exists is to eliminate
    float dust; a from_dollars rounding bug would defeat that."""
    assert Cents.from_dollars("0.01") == Cents(1)


def test_from_dollars_int_dollar() -> None:
    assert Cents.from_dollars(100) == Cents(10000)


# -- to_dollars boundary -----------------------------------------------------


def test_to_dollars_exact() -> None:
    assert Cents(7500).to_dollars() == Decimal("75.00")


def test_to_dollars_negative() -> None:
    assert Cents(-150050).to_dollars() == Decimal("-1500.50")


# -- from_db ---------------------------------------------------------------


def test_from_db_int_passthrough() -> None:
    assert Cents.from_db(7500) == Cents(7500)


# -- Arithmetic ------------------------------------------------------------


def test_add() -> None:
    assert Cents(7500) + Cents(2500) == Cents(10000)


def test_sub_to_zero() -> None:
    assert Cents(7500) - Cents(7500) == Cents(0)


def test_mul_scalar() -> None:
    assert Cents(7500) * 3 == Cents(22500)


def test_rmul_scalar() -> None:
    assert 3 * Cents(7500) == Cents(22500)


def test_neg() -> None:
    assert -Cents(7500) == Cents(-7500)


def test_abs() -> None:
    assert abs(Cents(-7500)) == Cents(7500)


def test_int_coercion() -> None:
    assert int(Cents(7500)) == 7500


# -- Ordering --------------------------------------------------------------


def test_ordering_less_than() -> None:
    assert Cents(100) < Cents(200)


def test_ordering_sorting() -> None:
    assert sorted([Cents(300), Cents(100), Cents(200)]) == [
        Cents(100), Cents(200), Cents(300),
    ]


# -- Frozen ----------------------------------------------------------------


def test_frozen_assignment_rejected() -> None:
    cents = Cents(100)
    with pytest.raises(FrozenInstanceError):
        cents.value = 999  # type: ignore[misc]: testing frozen-dataclass mutation rejection at runtime


# -- Type safety (runtime confirmation) ------------------------------------


def test_add_bare_int_raises() -> None:
    """``Cents + int`` MUST fail at runtime. Pyright catches this
    statically; this test confirms the type discipline is real, not
    just an annotation that gets ignored at runtime — the wrong-type
    operand is dereferenced for ``.value`` and crashes with
    AttributeError. Without this check, a silent coercion path would
    re-introduce the float dust the type was created to eliminate.
    """
    with pytest.raises(AttributeError):
        _ = Cents(100) + 50  # type: ignore[operator]: confirming type-safety stance is real at runtime


def test_sub_bare_int_raises() -> None:
    with pytest.raises(AttributeError):
        _ = Cents(100) - 50  # type: ignore[operator]: confirming type-safety stance is real at runtime
