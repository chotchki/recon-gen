"""Typed drill-source value types + shape resolver.

K.2 spike caught a sneaky bug class: a drill action bound a DATETIME
source column (``exception_date``) to a SINGLE_VALUED string parameter
(``pArActivityDate``), and the renderer silently coerced it to a full
timestamp text ``"2026-04-07 00:00:00.000"`` that never matched the
destination's TO_CHAR-formatted ``posted_date`` column. Both ends were
"STRING" at the coarse-type level, but the textual encodings differed
and the destination filter quietly produced zero rows. Bugs like this
look like missing data, not broken wiring — corrosive to user trust.

Per the user's "encode invariants in the type system" preference, the
fix isn't a validation test that walks the generated output; it's typed
value wrappers — ``DrillParam`` carries the destination ``ColumnShape``,
``DrillSourceField`` carries the source's — so a wrong wiring is a typed
mismatch, not a silent coercion.

This module holds those value types (``DrillParam`` / ``DrillSourceField``
/ ``DrillResetSentinel`` / ``DrillStaticDateTime``) plus ``field_source``,
which resolves a column's shape from its registered ``DatasetContract``.
The shape COMPARISON (is the source assignable to the destination?) lives
on the tree's ``Drill.resolve_source_shapes`` (``common/tree/actions.py``),
walked by ``App.validate()`` so it gates every renderer. Pre-DW the
comparison lived in the QS-emit helper ``set_drill_parameters``; the
emitter is gone, the invariant moved to the validation walk.
"""

from __future__ import annotations

from dataclasses import dataclass

from recon_gen.common.dataset_contract import (
    ColumnShape,
    get_contract,
)
from recon_gen.common.ids import ParameterName


# Sentinel value for the K.2 calc-field PASS pattern. Any drill that
# wants to clear a parameter to "no filter" writes this literal value;
# the destination calc-field expression special-cases it to PASS.
DRILL_RESET_SENTINEL_VALUE = "__ALL__"


@dataclass(frozen=True)
class DrillParam:
    """Destination parameter on a drill action — name + expected shape.

    The shape captures the parameter's value semantics;
    ``Drill.resolve_source_shapes`` refuses to write a source field whose
    shape isn't assignable to it.
    """

    name: ParameterName
    shape: ColumnShape


@dataclass(frozen=True)
class DrillSourceField:
    """Source field on a drill action — visual field id + resolved shape.

    Build via ``field_source(field_id, dataset_id, column_name)`` so the
    shape is read from the dataset contract, not duplicated by hand.
    """

    field_id: str
    shape: ColumnShape


@dataclass(frozen=True)
class DrillResetSentinel:
    """Marker that a drill should reset a parameter to the sentinel value.

    The destination calc-field filter recognizes the sentinel as PASS,
    so writing this clears the filter without needing an empty-string
    or null-value path that QuickSight's drill-action code path won't
    deliver to calc fields cleanly.
    """

    value: str = DRILL_RESET_SENTINEL_VALUE


@dataclass(frozen=True)
class DrillStaticDateTime:
    """Marker that a drill should write a fixed ISO-8601 datetime literal
    to a ``DateTimeParam`` destination.

    Use case: cross-sheet drills where the destination sheet has a
    universal date-range filter the source sheet doesn't share — e.g.
    L1's Pending Aging → Transactions. The aging sheet is a
    current-state view (rows can be arbitrarily old); the Transactions
    sheet's universal-filter window defaults to last 7 days. Without
    a date write the drill-target leg falls outside the destination's
    window and the table renders empty. Writing
    ``DrillStaticDateTime("1990-01-01T00:00:00.000Z")`` to the
    destination's date-start param widens the window so the drill-
    target row is always visible.

    QuickSight has no "now" or "rolling" expression you can write via
    SetParametersOperation — the only options are SourceField (column
    ref) or static CustomValues. Pick the static value carefully so
    the picker-shown date isn't misleading; the L1 app uses
    ``"1990-01-01T..."`` for start and ``"2099-12-31T..."`` for end,
    framing the explicit "all time" intent.

    Format: ISO-8601 with millisecond precision and the trailing
    ``Z``, matching what the L2FT app already uses for its static
    StaticValues defaults.
    """

    value: str


def field_source(
    field_id: str,
    dataset_id: str,
    column_name: str,
) -> DrillSourceField:
    """Resolve ``column_name``'s shape from its registered dataset contract.

    Raises ``TypeError`` if the column has no shape tag (it isn't drill-
    eligible), pointing at the call site so the developer can either
    tag the column in the contract or pick a different source column.
    Raises ``KeyError`` if the dataset_id isn't registered (usually
    means the dataset hasn't been built in this process yet — ensure
    ``build_all_datasets`` runs before visuals).
    """
    contract = get_contract(dataset_id)
    col = contract.column(column_name)
    if col.shape is None:
        raise TypeError(
            f"{dataset_id}.{column_name} is not drill-eligible (no "
            f"ColumnShape tag in its DatasetContract). Tag it in the "
            f"contract — and pick the shape carefully — or pick a "
            f"different source column for field_id {field_id!r}."
        )
    return DrillSourceField(field_id=field_id, shape=col.shape)
