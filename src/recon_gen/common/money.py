"""Money as integer cents — the storage-domain currency (Phase AO.1).

SQLite stores DECIMAL columns as REAL (float64), introducing float-dust
drift the L1 ``stored <> computed`` matview kept flagging (~700 false
positives on the sasquatch_pr fixture). Postgres + Oracle DECIMAL stay
exact but have to follow for uniform storage — the customer ETL feed
contract must be dialect-agnostic.

Use ``Cents.from_dollars(Decimal('75.00'))`` at authoring boundaries,
``Cents.from_db(int_from_cursor)`` at read boundaries, ``.to_dollars()``
for display / rendering. Arithmetic is cents-on-cents only (type-safe);
scalar multiplication for repeat-count semantics works (``cents * 5``).

Mixing with Decimal / float / bare int is intentionally NOT supported —
silent coercion would re-introduce the float dust the type was created
to eliminate. Pyright catches the cross-domain ops; at runtime they
raise ``AttributeError`` when the foreign operand is dereferenced for
``.value``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True, order=True)
class Cents:
    """Integer cents — the storage-domain currency type.

    Frozen + slotted for memory + immutability; ``order=True`` so
    comparisons (``<`` / ``>=`` / ``min`` / ``sorted``) work without
    extra boilerplate. The single field ``value`` carries the raw
    integer count of cents (signed; negative for debits / outflows).
    """

    value: int

    @classmethod
    def from_dollars(cls, dollars: Decimal | str | int) -> "Cents":
        """Authoring-boundary constructor: dollars (Decimal / str / int)
        → integer cents.

        Quantizes via ``Decimal("1")`` to round-trip without losing
        sub-cent precision the float path would; e.g.
        ``from_dollars("0.01")`` → ``Cents(1)`` (not ``Cents(0)``).
        Always accepts ``str`` shape (avoids float-init Decimals like
        ``Decimal(0.01) == Decimal('0.01000000000000000020816...')``).
        """
        return cls(int((Decimal(str(dollars)) * 100).quantize(Decimal("1"))))

    @classmethod
    def from_db(cls, raw: int) -> "Cents":
        """Read-boundary constructor: int from a DB cursor → ``Cents``.

        Explicit ``int()`` coerce in case the dbapi driver returns a
        long-shaped numeric (Oracle's NUMBER(19) cursors hand back
        ``int`` directly, but defensive). Pairs with
        ``cents_to_dollars_sql`` for the SQL-side projection.
        """
        return cls(int(raw))

    def to_dollars(self) -> Decimal:
        """Display-boundary projection: cents → ``Decimal`` dollars.

        Returns a ``Decimal`` (not float) so downstream formatters keep
        exact representation (e.g., ``f"${c.to_dollars():,.2f}"`` for
        ``$1,234.56`` shape).
        """
        return Decimal(self.value) / 100

    def __add__(self, other: "Cents") -> "Cents":
        return Cents(self.value + other.value)

    def __sub__(self, other: "Cents") -> "Cents":
        return Cents(self.value - other.value)

    def __neg__(self) -> "Cents":
        return Cents(-self.value)

    def __abs__(self) -> "Cents":
        return Cents(abs(self.value))

    def __mul__(self, n: int) -> "Cents":
        """Scalar multiplication: ``Cents(7500) * 3 == Cents(22500)``.

        Repeat-count semantics — "this fee, three times". Cents-on-cents
        multiplication is intentionally absent (dollars × dollars =
        dollars² is dimensionally wrong).
        """
        return Cents(self.value * n)

    __rmul__ = __mul__

    def __int__(self) -> int:
        """``int(Cents(7500)) == 7500`` — for DB write helpers that need
        the raw integer cents to bind."""
        return self.value
