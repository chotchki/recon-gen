"""AA.A.6 — generic additive-pickers row-survival infrastructure.

Picker-anchor pattern for sheets with ≥2 pickers: query the *dataset's
own SQL* (the same emitter the deploy uses) for a known-good row that
satisfies every picker's bound column, drive each picker to that row's
values *additively* (no clear between picks), assert the anchor row
survives in the target table visual.

Why dataset-builder-driven (AA.A.9 refactor, locked 2026-05-17):
the original v1 (AA.A.6) hand-listed the matview name + columns per
spec and ran ``SELECT cols FROM table`` against the underlying
matview. Two codebases — the test queried the matview directly, the
visual queried the dataset's CustomSql; the two could (and did)
diverge silently. The U7 spec_example rename surfaced this: anchor
rows came from one source, dropdown options from another, and the
two stopped agreeing. User-flagged: "if we have the sql copied in
the test or a mess of duplicated strings from the code. We now have
2 code bases and have not proven the behavior."

AA.A.9 collapses the two: the spec carries a ``dataset_builder``
reference (the exact fn deploy uses), the helper extracts CustomSql
from the resulting ``DataSet`` object + walks ``DataSetParameters``
to substitute ``<<$pX>>`` with each param's declared default via
``apply_dataset_param_defaults`` (the same production substitutor
App2's ``_sql_executor`` uses on initial paint). The anchor row is
guaranteed to be a row the visual sees on load AND a row the
dropdown can pick — both read the same registered SQL.

Daily Statement keeps its bespoke ``find_account_day_with_data``
helper for the cascade-pick flow it tests; new sheets without
bespoke coverage land here.

Dialect-aware via the existing ``Dialect`` enum on cfg; only PG +
Oracle are wired (matches the runner's `aw` target shape — QS can't
reach a sqlite tempfile).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import psycopg

from recon_gen.common.browser.helpers import record_sql_trace
from recon_gen.common.config import Config
from recon_gen.common.dataset_contract import DatasetContract
from recon_gen.common.html._sql_executor import apply_dataset_param_defaults
from recon_gen.common.l2 import L2Instance
from recon_gen.common.models import DataSet
from recon_gen.common.sql.dialect import Dialect


PickerKind = Literal["dropdown", "datetime", "date_from", "date_to", "slider"]

DatasetBuilder = Callable[[Config, L2Instance], DataSet]
"""The ``build_*_dataset(cfg, l2)`` signature every L1 dataset uses.

Specs carry one of these — the helper calls it to get the production
DataSet object, then extracts the SQL the visual will run."""


@dataclass(frozen=True)
class PickerSpec:
    """One picker's wiring on a sheet.

    ``label`` matches the UI control title (``ParameterDropdown.title``
    or equivalent) — what the driver sees as the picker name.

    ``kind`` selects how the picker is driven:

    - ``"dropdown"`` — single-value pick via ``driver.pick_filter``.
    - ``"date_from"`` / ``"date_to"`` — bounds of the universal date
      range; both drive the same row's date column. Both must appear
      in the spec when the sheet has a universal date range; one alone
      would set an open-bound which isn't what "narrow to the anchor's
      day" means.
    - ``"datetime"`` — single-value date picker via ``driver.set_date``
      (e.g. Daily Statement's Business Day).
    - ``"slider"`` — single-value slider via ``driver.set_slider``. The
      anchor value sets both bounds (lo == hi == value) so the slider
      narrows to exactly the anchor's value.

    ``column`` is the column name in the **dataset's projection** (NOT
    the underlying matview) — i.e. a key into the dict returned by
    ``fetch_anchor_row`` whose contents come from the dataset's
    CustomSql output. For the typical "picker's bound column == anchor
    source column" shape this is just the column name; for derived
    values (e.g. account_display concat) use ``format`` to derive the
    picker value from multiple anchor columns. AA.A.10 (stretch) plans
    to derive this from the tree walk; until then it stays hand-mapped.

    ``format`` (optional) maps the anchor dict to the picker's expected
    value. Default = ``str(anchor[column])``. Use it for:

    - Account-display dropdowns: ``f"{row['account_name']} ({row['account_id']})"``
    - Dates that need ISO formatting: ``row['business_day'].isoformat()``
    - Any other anchor-to-picker-value transformation.
    """
    label: str
    kind: PickerKind
    column: str
    format: "Callable[[Mapping[str, Any]], str] | None" = field(default=None)


@dataclass(frozen=True)
class SheetAnchorSpec:
    """A sheet's anchor + pickers config for the generic survival test.

    ``sheet_name`` matches the L1/L2FT/Inv/Exec sheet's display name
    (== ``Sheet.name`` from the tree).

    ``target_visual`` is the table whose row-count we assert ``>= 1``
    after all pickers are driven.

    ``dataset_builder`` is the production ``build_*_dataset`` function
    backing the target visual (e.g. ``build_drift_dataset`` from
    ``apps/l1_dashboard/datasets.py``). The helper calls it with the
    test's cfg + L2 instance, pulls the CustomSql from the resulting
    DataSet, applies each ``DataSetParameter``'s declared default to
    its ``<<$pX>>`` placeholders, wraps the result with ``ORDER BY
    {anchor_order} LIMIT 1``, and executes. AA.A.9 — replaces the v1
    ``anchor_table`` + ``anchor_columns`` fields which duplicated
    matview knowledge in the spec.

    ``contract`` is the matching ``DatasetContract`` (e.g.
    ``DRIFT_CONTRACT``) — the source-of-truth for column → display-
    label resolution. Passed alongside the builder because
    ``build_dataset`` consumes the contract to size the projection but
    doesn't attach it to the returned AWS-shape ``DataSet`` — so the
    test has to be told both. Used by ``visual_column_label`` to bridge
    SQL column names → table header text for the row-identity check
    in the inverse-pickers test.

    ``anchor_order`` biases the anchor pick: typically a column-name
    ASC/DESC clause that picks a recent / lowest-cust-N / highest-
    magnitude row. Empty string = arbitrary first row from the dataset
    SQL output.

    ``pickers`` is the tuple of picker wirings. All ``column`` values
    must be keys in the dict ``fetch_anchor_row`` returns (i.e.
    projection columns from the dataset's CustomSql output).

    ``anchor_where_template`` (optional) is an extra ``WHERE`` clause
    appended to the anchor-row SELECT, formatted with
    ``{prefix}=cfg.db_table_prefix`` at fetch time. AA.A.993 — needed
    when the dataset's universe is wider than a picker's dropdown
    universe. The Transactions dataset, for instance, queries
    ``<prefix>_current_transactions`` which includes internal control
    accounts (``clearing-suspense``, ``customer-ledger``) that have
    transactions but no ``<prefix>_current_daily_balances`` rows. The
    Account dropdown is sourced from ``current_daily_balances`` (see
    ``build_l1_accounts_dataset``), so an anchor picked from the
    transactions matview alone can be an account the dropdown never
    advertises — the picker click times out (QS: option not in DOM;
    App2: TomSelect setValue no-ops, no HTMX refetch fires). The fix:
    intersect the anchor universe with the dropdown universe via this
    template. Empty string = no extra constraint.
    """
    sheet_name: str
    target_visual: str
    dataset_builder: DatasetBuilder
    contract: DatasetContract
    anchor_order: str
    pickers: tuple[PickerSpec, ...]
    anchor_where_template: str = ""


def fetch_anchor_row(
    cfg: Config, l2: L2Instance, spec: SheetAnchorSpec,
) -> Mapping[str, Any]:
    """Run ``spec.dataset_builder``'s SQL against ``cfg.demo_database_url``
    (with declared param defaults applied) and return the first row as a
    column→value dict.

    The SQL is the exact CustomSql the deploy registers — extracted
    from ``ds.PhysicalTableMap[<key>].CustomSql.SqlQuery`` after calling
    the builder. ``apply_dataset_param_defaults`` substitutes each
    ``<<$pX>>`` placeholder with its ``DataSetParameter``'s declared
    static default (the same production substitutor App2 uses on
    initial page load). The result is then wrapped:

        SELECT * FROM (<dataset-sql-with-defaults>) sub
        ORDER BY {spec.anchor_order}
        LIMIT 1

    Column names come from ``cursor.description`` — no hand-listing.
    Picker ``column`` values must be projection columns from this
    output.

    Raises ``RuntimeError`` when the dataset SQL returns zero rows
    (matview legitimately empty, or the dataset's default-param state
    filters everything out — e.g. Money Trail's sentinel chain-root
    default).

    Only Postgres + Oracle are wired; the AW-target browser e2e cells
    only run against those two dialects.
    """
    if cfg.dialect not in (Dialect.POSTGRES, Dialect.ORACLE):
        raise RuntimeError(
            f"fetch_anchor_row: unsupported dialect {cfg.dialect!r} — "
            f"only Postgres + Oracle wired"
        )
    if not cfg.demo_database_url:
        raise RuntimeError("fetch_anchor_row: cfg.demo_database_url is unset")

    ds = spec.dataset_builder(cfg, l2)
    if not ds.PhysicalTableMap:
        raise RuntimeError(
            f"fetch_anchor_row: {spec.sheet_name!r}'s dataset has no "
            f"PhysicalTableMap entries — builder returned an empty "
            f"shape"
        )
    _, table = next(iter(ds.PhysicalTableMap.items()))
    if table.CustomSql is None:
        raise RuntimeError(
            f"fetch_anchor_row: {spec.sheet_name!r}'s dataset table "
            f"has no CustomSql — non-custom-SQL datasets aren't wired"
        )
    qs_sql = table.CustomSql.SqlQuery
    resolved = apply_dataset_param_defaults(
        qs_sql, ds.DatasetParameters or [], {},
    )
    order_clause = f"ORDER BY {spec.anchor_order} " if spec.anchor_order else ""
    limit_clause = (
        "LIMIT 1" if cfg.dialect is Dialect.POSTGRES else "FETCH FIRST 1 ROWS ONLY"
    )
    # AA.A.993 — anchor_where_template intersects the anchor universe
    # with a narrower dropdown universe when the dataset's own SQL
    # returns rows for accounts (or other entities) the picker dropdown
    # doesn't advertise. ``{prefix}`` is the only substitution; bare
    # ``str.format`` keeps the spec authoring shape one-liner-simple.
    where_clause = (
        f"WHERE {spec.anchor_where_template.format(prefix=cfg.db_table_prefix)} "
        if spec.anchor_where_template
        else ""
    )
    wrapped = f"SELECT * FROM ({resolved}) sub {where_clause}{order_clause}{limit_clause}"

    with psycopg.connect(cfg.demo_database_url, connect_timeout=60) as conn:
        with conn.cursor() as cur:
            cur.execute(wrapped)  # pyright: ignore[reportCallIssue]: psycopg.execute overload tolerance
            row = cur.fetchone()
            cols = [d.name for d in cur.description] if cur.description else []

    # AA.A.qs-triage.5.followon — record the anchor SQL + result to the
    # failure-capture bundle. Without it, a downstream picker failure
    # (e.g. MuiAutocomplete-noOptions on a value the test typed) can't be
    # split into "DB didn't return what we expected" vs "QS lost the
    # option" without re-running the query by hand. record_sql_trace is
    # sidecar-safe (swallows its own errors) so it can't mask a real
    # fixture failure.
    record_sql_trace(
        label=f"anchor [{spec.sheet_name}]",
        sql=wrapped,
        summary=(
            f"returned: {dict(zip(cols, row, strict=True)) if row else 'no rows'}"
        ),
    )

    if row is None:
        raise RuntimeError(
            f"fetch_anchor_row: {spec.sheet_name!r}'s dataset SQL "
            f"returned zero rows with default params applied. Deploy "
            f"skipped? Seed plants nothing for this dataset's matview? "
            f"Or the dataset's default param state legitimately renders "
            f"empty (e.g. Money Trail's sentinel chain-root) — this "
            f"sheet may not fit AA.A.6's anchor-survives-narrow contract."
        )
    return dict(zip(cols, row, strict=True))


def picker_value(
    spec: PickerSpec, anchor: Mapping[str, Any],
) -> str:
    """Resolve a picker's drive-value from the anchor row.

    Calls ``spec.format(anchor)`` when set; otherwise stringifies
    ``anchor[spec.column]``. ISO-formats date/datetime values for the
    driver protocol (which expects ``YYYY-MM-DD``).
    """
    if spec.format is not None:
        return spec.format(anchor)  # type: ignore[operator,no-any-return]: format is callable-shaped at the call site

    value = anchor[spec.column]
    if hasattr(value, "isoformat"):  # date / datetime
        iso: str = value.isoformat()
        # date_from/date_to/datetime want YYYY-MM-DD; trim time part if present
        return iso.split("T")[0]
    return str(value)


def visual_column_label(spec: SheetAnchorSpec, sql_column: str) -> str:
    """Resolve a SQL column name (as declared on ``PickerSpec.column``)
    to the display label QuickSight/App2 actually renders as the
    Table column header.

    Reads ``spec.contract`` (the ``DatasetContract`` carried alongside
    the builder — ``build_dataset`` consumes the contract to size the
    projection but doesn't attach it to the returned AWS-shape
    ``DataSet``, so the spec has to declare both). Returns the
    column's ``human_name`` — explicit ``display_name`` when set, else
    the auto-derived title-case form (``rail_name`` → "Rail Name",
    with initialism preservation so ``account_id`` → "Account ID").
    Raises ``KeyError`` if the column isn't in the contract — surfaces
    a wired-wrong picker spec loudly (e.g. ``column="raul_name"`` typo)
    instead of silently mismatching at the row-identity assertion.

    Used by the inverse-pickers test (AA.A.l2ft-rails-inverse.2) to
    bridge SQL column names → rendered header text for the
    ``row[header] == anchor_value`` check.
    """
    return spec.contract.column(sql_column).human_name


def non_matching_dropdown_value(
    driver: Any, picker_label: str, matching_value: str,
) -> str:
    """AA.A.7 — return an advertised dropdown option that differs from
    ``matching_value``, for the inverse-exclusion test.

    Raises ``RuntimeError`` if the dropdown only advertises one option
    (no inverse value to pick — the dropdown can't distinguish in
    either direction, the inverse test is meaningless and should be
    skipped at the call site).
    """
    options = [
        o for o in driver.filter_options(picker_label)
        if o and o != matching_value
    ]
    if not options:
        raise RuntimeError(
            f"non_matching_dropdown_value: {picker_label!r} only "
            f"advertises {matching_value!r}; no inverse value to pick "
            f"— inverse-exclusion test can't run for this picker."
        )
    return options[0]


def apply_anchor_to_pickers(
    driver: Any, spec: SheetAnchorSpec, anchor: Mapping[str, Any],
) -> None:
    """Drive every picker in ``spec.pickers`` to the anchor's values,
    *additively* (don't clear between picks).

    The driver verb depends on each picker's ``kind``:

    - ``dropdown`` → ``driver.pick_filter(label, [value])``
    - ``date_from`` / ``date_to`` → batched into one
      ``driver.set_date_range(from_, to)`` call after both bounds are
      collected (the protocol takes both bounds at once).
    - ``datetime`` → ``driver.set_date(label, iso)``
    - ``slider`` → ``driver.set_slider(label, value, value)`` (lo==hi
      narrows to exactly the anchor value).

    Blocks until each affected visual re-fetches (the driver verbs all
    do their own WS-settle waits — see ``DashboardDriver`` docstring).
    """
    date_from: str | None = None
    date_to: str | None = None

    for p in spec.pickers:
        value = picker_value(p, anchor)
        if p.kind == "dropdown":
            driver.pick_filter(p.label, [value])
        elif p.kind == "datetime":
            driver.set_date(p.label, value)
        elif p.kind == "slider":
            num = float(value)
            driver.set_slider(p.label, num, num)
        elif p.kind == "date_from":
            date_from = value
        elif p.kind == "date_to":
            date_to = value
        else:  # pragma: no cover — Literal exhausted above
            raise ValueError(f"unknown picker kind: {p.kind!r}")

    # Universal date range collapses to one call; date_from + date_to
    # must arrive together (an open-bound on one side wouldn't narrow
    # to the anchor's day).
    if date_from is not None or date_to is not None:
        if date_from is None or date_to is None:
            raise ValueError(
                f"spec {spec.sheet_name!r} has only one of "
                f"date_from / date_to. Both required for the anchor "
                f"narrow to be well-defined."
            )
        driver.set_date_range(date_from, date_to)
