"""DQ.4.2b — build a ``DatasetContract`` by DERIVING its sourced columns
from the emitting matview's ``DbObject.columns``, so a matview column
RENAME fails at IMPORT, not silently at live-query.

The reconciliation gate (DQ.4.2a) validates that a contract column SHARING
a name with its source reconciles (storage / type / shape). But it can't
catch a rename: rename ``drift.stored_balance`` → ``drift.stored_bal`` and
the contract's now-orphaned ``stored_balance`` shares a name with NOTHING,
so the gate skips it as "dataset-computed" and stays green while the live
``SELECT stored_balance`` errors. ``contract_from`` closes that gap by
RESOLVING every sourced column name against ``SCHEMA_GRAPH[source]`` at
construction — a rename turns the missing name into a ``KeyError`` the
moment the app module imports (the invariants-in-types discipline: the
wrong state is unrepresentable, not merely tested).

The builder also makes the contract DERIVE its kept columns from the one
source of truth (``DbObject.columns``) rather than re-authoring them — a
shape/type fix on the matview propagates to the contract for free. Three
column kinds, in projection order:

- ``keep(name, **overrides)`` — a passthrough column: inherit the source
  matview's emitted ``ColumnSpec`` verbatim (name resolved → ``KeyError``
  on rename). ``**overrides`` apply contract-side enrichment — the
  DQ.4.2a shape-enrich pattern (a ``None``-shape source column the reading
  contract tags ``ACCOUNT_ID`` / ``RAIL_NAME``), a ``display_name``, a
  ``hidden`` flag.
- ``dollars(name, **overrides)`` — a money column the dataset SQL
  pre-divides cents→dollars: resolve NAME against the source (which MUST
  emit it as money-CENTS) but re-type to ``DECIMAL`` / ``DOLLARS`` /
  ``currency=False`` (the house pattern — SQL owns ``$``-formatting;
  override ``currency=`` / ``type=`` where a contract differs).
- ``added(name, type_, **kw)`` — a dataset-COMPUTED column with no source
  (``account_display`` = ``"<name> (<id>)"``, ``abs_drift`` = ``ABS(...)``,
  the ``*_aging_bucket`` CASE arms): authored inline, NOT name-checked.

Scope: SINGLE-source contracts only. A contract reading a JOIN of two
matviews stays hand-authored — the DQ.4.2a reconciliation gate is the
universal backstop for those; ``contract_from`` is the stronger
rename-gate for the clean single-source majority.

Import cone: this imports ``dataset_contract`` + ``db_objects`` (NOT the
reverse — ``db_objects`` knows nothing of contracts), so there's no cycle.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from recon_gen.common.dataset_contract import ColumnSpec, DatasetContract, Storage
from recon_gen.common.db_objects import SCHEMA_GRAPH
from recon_gen.common.ids import DbObjectId


@dataclass(frozen=True)
class _Sourced:
    """A column resolved against the source matview (``keep`` / ``dollars``)."""

    name: str
    money_widen: bool
    overrides: tuple[tuple[str, object], ...]


@dataclass(frozen=True)
class _Added:
    """A dataset-computed column with no source (``added``)."""

    spec: ColumnSpec


_Entry = _Sourced | _Added


def keep(name: str, **overrides: object) -> _Sourced:
    """A passthrough column — inherit the source's emitted ``ColumnSpec``
    (name resolved at build → ``KeyError`` on a matview rename); apply any
    contract-side ``**overrides`` (``shape=`` enrichment, ``display_name=``,
    ``hidden=``)."""
    return _Sourced(name, money_widen=False, overrides=tuple(overrides.items()))


def dollars(name: str, **overrides: object) -> _Sourced:
    """A money column the dataset SQL pre-divides cents→dollars — resolve
    NAME against the source (must be money-CENTS) but re-type to
    ``DECIMAL`` / ``DOLLARS`` / ``currency=False`` (override any of those
    via ``**overrides``)."""
    return _Sourced(name, money_widen=True, overrides=tuple(overrides.items()))


def added(name: str, type_: str, **kw: object) -> _Added:
    """A dataset-computed column (no source; not name-checked)."""
    return _Added(ColumnSpec(name, type_, **kw))  # type: ignore[arg-type]: heterogeneous ColumnSpec field overrides forwarded, dataclass validates names at runtime


def contract_from(source: str, entries: Sequence[_Entry]) -> DatasetContract:
    """Assemble a ``DatasetContract`` from ``entries`` (in projection
    order), resolving every ``keep`` / ``dollars`` name against
    ``SCHEMA_GRAPH[source].columns`` — a rename raises ``KeyError`` HERE,
    at import, naming the source object + the known columns."""
    obj = SCHEMA_GRAPH.by_id(DbObjectId(source))
    columns: list[ColumnSpec] = []
    for entry in entries:
        if isinstance(entry, _Added):
            columns.append(entry.spec)
            continue
        src_col = obj[entry.name]  # KeyError at import on a matview rename
        overrides: dict[str, object] = dict(entry.overrides)
        if entry.money_widen:
            if not (src_col.currency and src_col.storage is Storage.CENTS):
                raise ValueError(
                    f"contract_from({source!r}): dollars({entry.name!r}) is "
                    f"for the money cents→dollars widen, but "
                    f"{source}.{entry.name} emits "
                    f"currency={src_col.currency} storage={src_col.storage.value} "
                    f"— use keep() for a non-money passthrough"
                )
            columns.append(replace(
                src_col,
                type=overrides.pop("type", "DECIMAL"),  # type: ignore[arg-type]: override value is a type-string by the dollars() factory contract
                currency=bool(overrides.pop("currency", False)),
                storage=Storage.DOLLARS,
                **overrides,  # type: ignore[arg-type]: remaining heterogeneous field overrides forwarded to replace(), field names validated at runtime
            ))
        else:
            columns.append(replace(src_col, **overrides))  # type: ignore[arg-type]: heterogeneous field overrides forwarded to replace(), field names validated at runtime
    return DatasetContract(columns=columns)
