"""Typed analysis-level calculated fields (L.1.8).

A ``CalcField`` is the typed wrapper around the existing per-app
``CalculatedField`` dict (``{Name, DataSetIdentifier, Expression}``).
Visuals and filters reference calc fields the same way they reference
real dataset columns — by passing the column to ``Dim`` / ``Measure``
/ ``CategoryFilter`` / ``NumericRangeFilter``. The column slot accepts
either a bare ``str`` (a real column or a calc-field name) OR a
``CalcField`` object reference; the typed ref carries the validated
calc-field identity through the type checker.

Validation (L.1.8):

- ``Analysis.add_calc_field`` rejects duplicate calc-field names
  within an analysis.
- ``App._validate_calc_field_references`` (added in L.1.8) raises if
  any tree-referenced ``CalcField`` isn't registered on the Analysis.
  Catches "filter references calc field that doesn't exist" and
  "calc field declared but never used".

Dependency graph (L.1.7 + L.1.8):

- Each ``CalcField`` carries a ``Dataset`` ref. The CalcField's
  dataset participates in ``App.dataset_dependencies()`` so
  declaring a calc field on dataset D establishes D as a dep even
  when no visual directly references D's columns.

Auto-name (L.2.6 follow-up): ``name`` is Optional. When omitted, the
App walker assigns ``calc-{idx}`` at emit time based on the calc
field's index in ``analysis.calc_fields``. Pass an explicit ``name=``
when the calc field's column header text matters to analysts (the name
becomes the underlying ColumnName in the data model — analyst-facing
unless a visual's label options override it).
"""

from __future__ import annotations

from dataclasses import dataclass

from quicksight_gen.common.dataset_contract import ColumnShape
from quicksight_gen.common.tree._helpers import AUTO, AutoResolved, _AutoSentinel
from quicksight_gen.common.tree.datasets import Column, Dataset


@dataclass(eq=False)
class CalcField:
    """Tree node for one analysis-level calculated field.

    ``name`` is the column-style identifier visuals/filters reference
    (e.g. ``"is_anchor_edge"``). Optional — auto-derived as
    ``calc-{idx}`` at emit time when not specified.

    ``dataset`` is the ``Dataset`` object ref the expression evaluates
    against. ``expression`` is the QuickSight calc expression
    (e.g. ``"ifelse({source} = ${pAnchor}, 'yes', 'no')"``).

    ``shape`` is Optional and only matters for drill sources: when a
    drill action reads this calc field's value (via a ``Dim`` /
    ``Measure`` object ref in the drill's ``writes``), the tree needs
    a ``ColumnShape`` to type-check the drill parameter binding. Tag
    here once rather than re-passing the shape at every drill site.

    Identity-keyed (``eq=False``) so the auto-name resolver can mutate
    the ``name`` field at emit time. CalcFields stay hashable via the
    default object identity hash, which is what the dependency-graph
    set membership needs anyway.

    Emits a plain dict that drops straight into
    ``AnalysisDefinition.CalculatedFields`` — same shape the existing
    builders write today.
    """
    dataset: Dataset
    expression: str
    name: str | AutoResolved = AUTO
    shape: ColumnShape | None = None

    def emit(self) -> dict[str, str]:
        assert not isinstance(self.name, _AutoSentinel), (
            "name wasn't resolved — App.resolve_auto_ids() must run "
            "before CalcField.emit()."
        )
        return {
            "Name": self.name,
            "DataSetIdentifier": self.dataset.identifier,
            "Expression": self.expression,
        }


# Type alias used everywhere a tree node accepts a column reference.
# Three forms (the resolver below pulls the column name out at emit
# time):
#
# - ``str`` — bare column name. Escape hatch — no contract validation.
#   Use sparingly (e.g., test fixtures or datasets without a contract).
# - ``Column`` — typed ref produced by ``ds["column_name"]``. Validated
#   against the dataset's contract at construction. Preferred form.
# - ``CalcField`` — analysis-level calc field. Carries its own dataset
#   ref + name; the dependency-graph walk picks up the calc field's
#   dataset.
ColumnRef = str | CalcField | Column


def resolve_column(column: ColumnRef) -> str:
    """Read the column-name string off a ``ColumnRef``.

    For a ``CalcField``, the name is set by ``App.resolve_auto_ids()``;
    callers asserting the resolver ran can rely on this returning ``str``.
    """
    if isinstance(column, CalcField):
        assert not isinstance(column.name, _AutoSentinel), (
            "CalcField.name wasn't resolved — App.resolve_auto_ids() "
            "must run before resolve_column() on a CalcField ref."
        )
        return column.name
    if isinstance(column, Column):
        return column.name
    return column


def calc_field_in(column: ColumnRef) -> CalcField | None:
    """Return the CalcField if ``column`` is one, else ``None``.

    Used by the dependency-graph walk to harvest CalcField refs from
    Dim / Measure / Filter column slots. ``Column`` refs return None
    (they reference a real dataset column, not a calc field).
    """
    if isinstance(column, CalcField):
        return column
    return None
