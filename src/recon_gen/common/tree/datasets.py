"""Dataset tree nodes (L.1.7) + typed Column refs (L.1.17).

Dataset is a first-class tree concept: visuals and filters reference a
``Dataset`` instance by object ref instead of by string identifier,
and the ``App`` walks the tree to derive the precise dependency
graph — which Sheet / Visual / FilterGroup uses which Dataset.

The dependency graph drives:
- Selective deploy (only re-create datasets that downstream changes
  touch).
- Matview REFRESH ordering (REFRESH only the matviews backing
  Datasets that an updated deploy surface depends on).

Construction-time check (in App.emit_analysis): every Dataset
referenced from the tree must be registered on the App via
``app.add_dataset()``. Catches "visual references undeclared dataset"
at emit time, where the existing string-keyed pattern lets the
mismatch flow through to deploy.

**Typed Column refs (L.1.17 — fragility fix).** Bare-string column
names in ``Dim(ds, "column_name")`` were silently typo-able. The
new path:

- ``ds["column_name"]`` validates ``column_name`` against the
  dataset's registered ``DatasetContract`` (raises ``KeyError`` at
  the wiring site on typos) and returns a typed ``Column`` wrapper.
- ``Column`` chains into the field-well factories: ``ds["col"].dim()``,
  ``ds["col"].sum()``, ``ds["col"].distinct_count()``, etc. The
  chained form is the preferred new style — single source of truth
  for the (dataset, column) pair, validated.
- Bare strings still work as the escape hatch for cases where no
  contract is registered (test fixtures, kitchen-sink) — the resolver
  treats string and Column refs uniformly at emit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from recon_gen.common.dataset_contract import get_contract
from recon_gen.common.models import DataSetIdentifierDeclaration
from recon_gen.common.tree._helpers import AUTO, AutoResolved, TimeGranularity

if TYPE_CHECKING:
    from recon_gen.common.tree.fields import Dim, DimKind, Measure


@dataclass(frozen=True)
class Dataset:
    """Tree node for one dataset registration on the App.

    ``identifier`` is the logical identifier visuals/filters reference
    (the existing per-app DS_INV_ACCOUNT_NETWORK / DS_AR_TRANSACTIONS
    strings — values like ``"inv-account-network-ds"``). ``arn`` is
    the AWS QuickSight DataSetArn the deployed analysis points at.

    Frozen because Dataset acts as the dependency-graph KEY: it must
    be hashable so visuals/filters that reference it can be collected
    into ``set[Dataset]`` for the dependency walk.

    ``ds["column_name"]`` returns a typed ``Column`` ref (validated
    against the dataset's registered ``DatasetContract`` if one exists)
    — see Column docstring for the chained factory pattern.
    """
    identifier: str
    arn: str

    def __getitem__(self, name: str) -> Column:
        """Return a typed ``Column`` ref for ``name``.

        Validates ``name`` against the registered ``DatasetContract``
        when one exists. Raises ``KeyError`` at the wiring site on
        typos — that turns a silent "broken visual at deploy" into a
        loud "broken column at construction".

        When no contract is registered (early test fixtures or the
        kitchen-sink, which doesn't carry a contract), validation is
        skipped — the Column ref is built without checking, same as
        the bare-string escape hatch.
        """
        try:
            contract = get_contract(self.identifier)
        except KeyError:
            return Column(dataset=self, name=name)
        if name not in contract.column_names:
            raise KeyError(
                f"Column {name!r} not in dataset {self.identifier!r}'s "
                f"contract. Known columns: "
                f"{sorted(contract.column_names)}"
            )
        return Column(dataset=self, name=name)

    def emit_declaration(self) -> DataSetIdentifierDeclaration:
        return DataSetIdentifierDeclaration(
            Identifier=self.identifier, DataSetArn=self.arn,
        )


@dataclass(frozen=True)
class Column:
    """Typed column reference — dataset object ref + column name.

    Authors construct via ``ds["col_name"]`` (which validates against
    the contract). Pass to Dim/Measure constructors directly, or use
    the chained factories below for the most concise wiring:

        ds["amount"].sum()                 # Measure.sum
        ds["recipient_id"].dim()           # categorical Dim
        ds["window_end"].date()            # date Dim
        ds["depth"].numerical()            # numerical Dim
        ds["recipient_id"].distinct_count()

    Frozen + hashable so a Column can be reused across visual slots
    (the chain ``ds["col"]`` returns a value-equal Column each time;
    ``ds["col"] == ds["col"]`` is True, useful for set membership in
    column-coverage tests).

    Imports are lazy inside the factory methods to break the
    Dataset → Column → Dim/Measure → Dataset circular import.
    """
    dataset: Dataset
    name: str

    def dim(self, *, kind: DimKind = "categorical", field_id: str | AutoResolved = AUTO) -> Dim:
        from recon_gen.common.tree.fields import Dim
        return Dim(self.dataset, self, kind=kind, field_id=field_id)

    def date(
        self,
        *,
        date_granularity: TimeGranularity | None = "DAY",
        field_id: str | AutoResolved = AUTO,
    ) -> Dim:
        from recon_gen.common.tree.fields import Dim
        return Dim.date(
            self.dataset, self,
            date_granularity=date_granularity,
            field_id=field_id,
        )

    def numerical(
        self, *, field_id: str | AutoResolved = AUTO, currency: bool = False,
    ) -> Dim:
        from recon_gen.common.tree.fields import Dim
        return Dim.numerical(
            self.dataset, self, field_id=field_id, currency=currency,
        )

    def sum(
        self, *, field_id: str | AutoResolved = AUTO, currency: bool = False,
        decimals: int | None = None,
    ) -> Measure:
        from recon_gen.common.tree.fields import Measure
        return Measure.sum(
            self.dataset, self, field_id=field_id, currency=currency,
            decimals=decimals,
        )

    def max(
        self, *, field_id: str | AutoResolved = AUTO, currency: bool = False,
        decimals: int | None = None,
    ) -> Measure:
        from recon_gen.common.tree.fields import Measure
        return Measure.max(
            self.dataset, self, field_id=field_id, currency=currency,
            decimals=decimals,
        )

    def min(
        self, *, field_id: str | AutoResolved = AUTO, currency: bool = False,
        decimals: int | None = None,
    ) -> Measure:
        from recon_gen.common.tree.fields import Measure
        return Measure.min(
            self.dataset, self, field_id=field_id, currency=currency,
            decimals=decimals,
        )

    def average(
        self, *, field_id: str | AutoResolved = AUTO, currency: bool = False,
        decimals: int | None = None,
    ) -> Measure:
        from recon_gen.common.tree.fields import Measure
        return Measure.average(
            self.dataset, self, field_id=field_id, currency=currency,
            decimals=decimals,
        )

    def count(self, *, field_id: str | AutoResolved = AUTO) -> Measure:
        from recon_gen.common.tree.fields import Measure
        return Measure.count(self.dataset, self, field_id=field_id)

    def distinct_count(self, *, field_id: str | AutoResolved = AUTO) -> Measure:
        from recon_gen.common.tree.fields import Measure
        return Measure.distinct_count(self.dataset, self, field_id=field_id)

    @property
    def human_name(self) -> str:
        """Plain-English header label for this column (v8.5.0).

        Looks up the column on the dataset's registered contract and
        returns the contract's ``human_name`` (override or auto-derived
        title-case). Returns the title-cased column name as a fallback
        if the dataset has no contract — keeps the test fixtures (which
        construct Datasets directly without going through
        ``build_dataset``) usable without forcing a registry round-trip.
        """
        from recon_gen.common.dataset_contract import (
            _smart_title, get_contract,
        )
        try:
            contract = get_contract(self.dataset.identifier)
        except KeyError:
            return _smart_title(self.name)
        try:
            return contract.column(self.name).human_name
        except KeyError:
            return _smart_title(self.name)
