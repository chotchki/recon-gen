"""Custom-SQL datasets for the Executives app (L.6.3).

Two datasets, both reading the shared base tables:

- ``exec_transaction_summary`` — one row per ``(posted_date,
  rail_name)`` aggregated from ``transactions``. Drives the
  Transaction Volume Over Time + Money Moved sheets.
- ``exec_account_summary`` — one row per ``account_id`` joined
  against an activity rollup over ``transactions``. Drives the
  Account Coverage sheet.

**Aggregation choices.** Both queries aggregate per ``transfer_id``
first, then roll up to (date, type). Aggregating at the leg grain
would double-count multi-leg transfers — e.g. a $100 ACH transfer
posts as a +$100 + a -$100 leg, both with ``amount=100``; raw
``SUM(amount)`` gives $200 of "money moved" when only $100 actually
moved. The per-transfer pre-aggregation collapses each transfer to
one row (``MAX(amount)`` since both legs share the magnitude;
``SUM(signed_amount)`` for the net flow which is 0 for balanced
multi-leg, non-zero for single-leg or unbalanced).

**Status filter.** Both datasets filter to ``status = 'Posted'`` —
the canonical settled-leg status across the v6 schema (matching the
L1 invariant matviews + Investigation datasets). Pending / Failed
legs are excluded; including them would inflate the executive trends
with operational noise.
"""

from __future__ import annotations

from recon_gen.common.config import Config
from recon_gen.common.dataset_contract import (
    ColumnShape,
    ColumnSpec,
    DatasetContract,
    build_dataset,
    register_contract,
)
from recon_gen.common.models import (
    DataSet,
    DatasetParameter,
    DateTimeDatasetParameter,
)
from recon_gen.common.sheets.app_info import (
    build_liveness_dataset,
    build_matview_status_dataset,
)
from recon_gen.common.sql import to_date, universal_date_range_clause
from recon_gen.common.sql.dialect import Dialect
from recon_gen.common.sql.money import cents_to_dollars_sql
from recon_gen.common.tree import DateView


# M.4.4.5 — Executives reads base tables only; no app-specific
# matviews. V.3 — but we still surface the base tables themselves on
# the App Info sheet so the operator can see ETL freshness at a
# glance. Z.C — sourced from cfg.db_table_prefix (now a required cfg
# field; loud-fails at load time when unset).
def exec_matview_specs(cfg: Config) -> list[tuple[str, str | None]]:
    """Tables Executives reads, paired with their date columns for
    App Info's ``latest_date`` KPI. No app-specific matviews — just
    the base tables (which is what the Executives sheets aggregate
    over)."""
    p = cfg.db_table_prefix
    return [
        (f"{p}_transactions", "posting"),
        (f"{p}_daily_balances", "business_day_start"),
    ]


# Identifier strings used as the DataSetIdentifier in visuals + filters.
DS_EXEC_TRANSACTION_SUMMARY = "exec-transaction-summary-ds"
# AO.5 — per-(posted_date) rollup of `exec-transaction-summary` so the
# "Average Daily Volume" KPI averages over ACTIVE DAYS, not over
# (date × rail_name) rows. Sasquatch's ~30 rails would make
# `AVG(transfer_count)` on the per-(date, rail) dataset ≈30× too small.
DS_EXEC_TRANSACTION_DAILY = "exec-transaction-daily-ds"
DS_EXEC_ACCOUNT_SUMMARY = "exec-account-summary-ds"
# Y.2.h — second account dataset with `WHERE activity_count > 0` baked
# into the SQL. Replaces the visual-pinned `NumericRangeFilter` (which
# QS applied but App2 didn't), so the active-only KPI + bar narrow
# correctly across both renderers. Same shape + columns as the base
# `exec-account-summary-ds` so visuals can be re-pointed without changes.
DS_EXEC_ACCOUNT_SUMMARY_ACTIVE = "exec-account-summary-active-ds"
#: BH.8 follow-up (2026-05-26) — per-leg / all-status counter dataset
#: bound to a sibling KPI on Transaction Volume to disclose the
#: documented gap vs the deduped-Posted-only "Total Transactions" KPI.
#: Cold-read agents read the difference between Total Transactions and
#: App Info's matview row_count as a bug; surfacing both numbers
#: side-by-side in the headline tile makes the predicate-mismatch
#: visible.
DS_EXEC_TRANSACTION_LEGS = "exec-transaction-legs-ds"


# Phase BM — universal date-range filter param names. Pre-BM Exec used
# analysis-level ``TimeRangeFilter`` FilterGroups + an App2
# ``{date_filter}`` template slot. BM pushes the narrowing into dataset
# SQL via two ``DateTimeDatasetParameter``s named ``pExecDateStart`` /
# ``pExecDateEnd`` (the same names ``app.py`` bridges from the picker
# via ``MappedDataSetParameters``).
P_EXEC_DATE_START = "pExecDateStart"
P_EXEC_DATE_END = "pExecDateEnd"


def _exec_universal_range_view(cfg: Config) -> DateView:
    """AR.4 — 30-day window anchored at ``cfg.test_generator.as_of_frame()``'s
    as-of. One DateView per cfg drives both the analysis-param defaults
    (picker initial state) AND the dataset-param defaults (BM-shape
    pushdown defaults).
    """
    return DateView(frame=cfg.test_generator.as_of_frame(window_days=30))


def _exec_universal_range_params(cfg: Config) -> list[DatasetParameter]:
    """Phase BM — the two ``DateTimeDatasetParameter``s every Exec
    date-scoped dataset declares (mirrors L1's
    ``_l1_universal_range_params``).
    """
    view = _exec_universal_range_view(cfg)
    return [
        DatasetParameter(DateTimeDatasetParameter=DateTimeDatasetParameter(
            Name=P_EXEC_DATE_START, ValueType="SINGLE_VALUED",
            TimeGranularity="DAY",
            DefaultValues=view.emit_qs_dataset_default_start(),
        )),
        DatasetParameter(DateTimeDatasetParameter=DateTimeDatasetParameter(
            Name=P_EXEC_DATE_END, ValueType="SINGLE_VALUED",
            TimeGranularity="DAY",
            DefaultValues=view.emit_qs_dataset_default_end(),
        )),
    ]


def _exec_date_range_clause(date_column: str, cfg: Config) -> str:
    """Phase BM — day-inclusive predicate fragment narrowing
    ``date_column`` by ``<<$pExecDateStart>>`` / ``<<$pExecDateEnd>>``.
    """
    return universal_date_range_clause(
        date_column,
        start_param=P_EXEC_DATE_START,
        end_param=P_EXEC_DATE_END,
        dialect=cfg.dialect,
    )


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------

EXEC_TRANSACTION_SUMMARY_CONTRACT = DatasetContract(columns=[
    ColumnSpec("posted_date", "DATETIME"),
    ColumnSpec("rail_name", "STRING", shape=ColumnShape.RAIL_NAME),
    ColumnSpec("transfer_count", "INTEGER"),
    ColumnSpec("gross_amount", "DECIMAL"),
    ColumnSpec("net_amount", "DECIMAL"),
])


# AO.5 — one row per active day (no rail split). The "Average Daily
# Volume" KPI consumes this so its AVG denominator is days-with-activity
# rather than (days × rails). Same upstream `per_transfer` shape as
# `EXEC_TRANSACTION_SUMMARY_CONTRACT`, rolled up one level.
EXEC_TRANSACTION_DAILY_CONTRACT = DatasetContract(columns=[
    ColumnSpec("posted_date", "DATETIME"),
    ColumnSpec("daily_transfer_count", "INTEGER"),
    ColumnSpec("daily_gross_amount", "DECIMAL"),
    ColumnSpec("daily_net_amount", "DECIMAL"),
])


EXEC_ACCOUNT_SUMMARY_CONTRACT = DatasetContract(columns=[
    ColumnSpec("account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
    ColumnSpec("account_name", "STRING"),
    ColumnSpec("account_type", "STRING"),
    ColumnSpec("last_activity_date", "DATETIME"),
    ColumnSpec("activity_count", "INTEGER"),
])


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def build_transaction_summary_dataset(cfg: Config) -> DataSet:
    """Per-(date, rail_name) aggregates: transfer count, gross + net dollars.

    Aggregates per ``transfer_id`` first so multi-leg transfers are
    counted once, not once per leg. ``gross_amount`` is the per-transfer
    handle; ``net_amount`` is the per-transfer net flow (0 for balanced
    multi-leg, non-zero for single-leg or unbalanced transfers).

    N.4.a: reads from ``<prefix>_transactions`` (per-instance prefixed
    base table). v6 column rename: posted_at → posting; amount →
    ``ABS(amount_money)`` (the per-leg signed Decimal — magnitude is
    abs); signed_amount → amount_money (already signed in v6).

    BQ.6 (cold-read F7) — top-N + Other rollup on ``rail_name``. The
    executive dashboards display this dataset stacked by ``rail_name``
    (Daily Stacked) or grouped by ``rail_name`` (Period Total). With
    60-80 distinct rails in sasquatch_pr, the legend takes ~30% of the
    canvas and the long tail of small rails is illegible. Rolling
    everything past the top-20 by gross volume into ``"Other"`` caps
    the legend at 21 entries and keeps the long-tail aggregate visible
    instead of invisibly dispersed. Rank is by GROSS (the most
    operator-meaningful sort for executive scanning); counts +
    net-amount aggregate cleanly under the same partition.
    """
    # Phase BM — single SQL form via ``<<$pExecDate*>>`` pushdown over
    # ``t.posting`` (TIMESTAMP); the helper's upper bound expands to
    # "+1 day" so same-day non-midnight rows on the end day are included.
    p = cfg.db_table_prefix
    posted_date_expr = to_date("MIN(t.posting)", cfg.dialect)
    # AO.1.impl — per_transfer's transfer_amount / transfer_net are
    # cents (derived from t.amount_money BIGINT cents). The outer
    # SUM(...) over both stays cents-cents (integer-safe); wrap to
    # dollars at the outermost projection so the executive dashboard
    # receives dollars on the two money columns.
    gross = cents_to_dollars_sql(
        "SUM(pt.transfer_amount)", dialect=cfg.dialect,
    )
    net = cents_to_dollars_sql(
        "SUM(pt.transfer_net)", dialect=cfg.dialect,
    )
    date_clause = _exec_date_range_clause("t.posting", cfg)
    # BQ.6 top-N + Other rollup. DENSE_RANK over the per-rail gross
    # total (cents-cents math stays integer-safe). CASE folds non-top
    # rails to the string literal "Other" so QS sees one extra series
    # at the bottom of the legend. Top-N is 20 — chosen to keep the
    # legend chunked but not aggressive enough to flatten interesting
    # mid-volume rails (sasquatch_pr has ~30 rails firing; 20 covers
    # the visible top, 10 collapse).
    sql = f"""\
WITH per_transfer AS (
    SELECT
        {posted_date_expr}     AS posted_date,
        t.transfer_id,
        t.rail_name,
        MAX(ABS(t.amount_money)) AS transfer_amount,
        SUM(t.amount_money)      AS transfer_net
    FROM {p}_transactions t
    WHERE t.status = 'Posted'
      AND {date_clause}
    GROUP BY t.transfer_id, t.rail_name
),
rail_totals AS (
    SELECT
        rail_name,
        SUM(transfer_amount) AS rail_gross_cents
    FROM per_transfer
    GROUP BY rail_name
),
rail_ranks AS (
    SELECT
        rail_name,
        DENSE_RANK() OVER (ORDER BY rail_gross_cents DESC) AS rail_rank
    FROM rail_totals
)
SELECT
    pt.posted_date                 AS posted_date,
    CASE WHEN rr.rail_rank <= 20 THEN pt.rail_name ELSE 'Other' END AS rail_name,
    COUNT(*)                       AS transfer_count,
    {gross} AS gross_amount,
    {net}   AS net_amount
FROM per_transfer pt
JOIN rail_ranks rr ON pt.rail_name = rr.rail_name
GROUP BY pt.posted_date, CASE WHEN rr.rail_rank <= 20 THEN pt.rail_name ELSE 'Other' END"""
    return build_dataset(
        cfg,
        cfg.prefixed("exec-transaction-summary-dataset"),
        "Executives Transaction Summary",
        "exec-transaction-summary",
        sql,
        EXEC_TRANSACTION_SUMMARY_CONTRACT,
        visual_identifier=DS_EXEC_TRANSACTION_SUMMARY,
        dataset_parameters=_exec_universal_range_params(cfg),
    )


def build_transaction_daily_dataset(cfg: Config) -> DataSet:
    """Per-(posted_date) rollup of `exec-transaction-summary`.

    AO.5 fix: the "Average Daily Volume" KPI in the Volume sheet used
    to consume `exec-transaction-summary` (one row per (date, rail))
    and ask QS for `AVG(transfer_count)` — that's the average across
    (date × rail) rows, which is days-with-activity × distinct-rails-
    on-that-day in the denominator. With Sasquatch's ~30 declared
    rails, the KPI read ≈30× too small vs the analyst's
    `total / active-days` expectation (cold-read MAJOR 2/4, reported
    as "~67× off"). This dataset collapses the per-(date, rail)
    breakdown to a single row per active day so `AVG(daily_transfer_
    count)` gets the right denominator structurally — no calc-field
    expression DSL gymnastics.

    Shares the upstream `per_transfer` shape with
    `build_transaction_summary_dataset` (so multi-leg transfers are
    counted once, not once per leg).
    """
    p = cfg.db_table_prefix
    posted_date_expr = to_date("MIN(t.posting)", cfg.dialect)
    gross = cents_to_dollars_sql(
        "SUM(transfer_amount)", dialect=cfg.dialect,
    )
    net = cents_to_dollars_sql(
        "SUM(transfer_net)", dialect=cfg.dialect,
    )
    # Phase BM — single SQL form via ``<<$pExecDate*>>`` pushdown.
    date_clause = _exec_date_range_clause("t.posting", cfg)
    sql = f"""\
WITH per_transfer AS (
    SELECT
        {posted_date_expr}     AS posted_date,
        t.transfer_id,
        MAX(ABS(t.amount_money)) AS transfer_amount,
        SUM(t.amount_money)      AS transfer_net
    FROM {p}_transactions t
    WHERE t.status = 'Posted'
      AND {date_clause}
    GROUP BY t.transfer_id
)
SELECT
    posted_date,
    COUNT(*)            AS daily_transfer_count,
    {gross}             AS daily_gross_amount,
    {net}               AS daily_net_amount
FROM per_transfer
GROUP BY posted_date"""
    return build_dataset(
        cfg,
        cfg.prefixed("exec-transaction-daily-dataset"),
        "Executives Transaction Daily Rollup",
        "exec-transaction-daily",
        sql,
        EXEC_TRANSACTION_DAILY_CONTRACT,
        visual_identifier=DS_EXEC_TRANSACTION_DAILY,
        dataset_parameters=_exec_universal_range_params(cfg),
    )


def _account_summary_sql_template(prefix: str, dialect: Dialect) -> str:
    """Shared SQL template for both the base + active variants.

    Carries a ``{date_filter}`` slot (interpolated to ``""`` for the
    base date-independent snapshot, or to the App2 bind-clause for
    the active variant) and a ``{active_only}`` slot (interpolated to
    ``""`` for the base or to ``WHERE COALESCE(act.activity_count, 0) > 0``
    for the active variant). Single template lets both builders
    share one body (Y.2.h split, was X.2.g.1.b dual-SQL).
    """
    last_activity_expr = to_date("t.posting", dialect)
    return f"""\
WITH activity AS (
    SELECT
        t.account_id,
        MAX({last_activity_expr})    AS last_activity_date,
        COUNT(*)                AS activity_count
    FROM {prefix}_transactions t
    WHERE t.status = 'Posted'
      {{date_filter}}
    GROUP BY t.account_id
),
accounts AS (
    SELECT DISTINCT
        d.account_id,
        d.account_name,
        d.account_role          AS account_type
    FROM {prefix}_daily_balances d
)
SELECT
    a.account_id,
    a.account_name,
    a.account_type,
    act.last_activity_date,
    COALESCE(act.activity_count, 0)  AS activity_count
FROM accounts a
LEFT JOIN activity act ON act.account_id = a.account_id
{{active_only}}"""


def build_account_summary_dataset(cfg: Config) -> DataSet:
    """One row per account that has ever appeared in ``daily_balances``.

    Y.2.h — pure date-independent snapshot. Used by visuals whose
    semantic IS "every account that exists" (Total Open Accounts KPI,
    Open Accounts by Type bar, Account Detail table). The activity
    rollup columns (``last_activity_date`` / ``activity_count``)
    reflect ALL-TIME activity, NOT the date-window — that's the
    difference vs the ``exec-account-summary-active-ds`` variant.

    Without ``:date_from``, the date-sensitive count-KPI test heuristic
    correctly skips Total Open Accounts (its expected behavior IS
    date-independent). Active KPIs use the ``_active`` variant which
    keeps the date filter + bakes ``WHERE activity_count > 0``.

    N.4.a: reads from ``<prefix>_transactions`` + ``<prefix>_daily_balances``.
    v6 column rename: posted_at → posting; account_type → account_role
    (output column kept as ``account_type`` so dashboard-side consumers
    don't need to follow the rename — only the SELECT does).
    """
    p = cfg.db_table_prefix
    template = _account_summary_sql_template(p, cfg.dialect)
    sql = template.format(date_filter="", active_only="")
    return build_dataset(
        cfg,
        cfg.prefixed("exec-account-summary-dataset"),
        "Executives Account Summary",
        "exec-account-summary",
        sql,
        EXEC_ACCOUNT_SUMMARY_CONTRACT,
        visual_identifier=DS_EXEC_ACCOUNT_SUMMARY,
    )


def build_account_summary_active_dataset(cfg: Config) -> DataSet:
    """Y.2.h — same shape as ``exec-account-summary-ds`` but narrowed
    to accounts with at least one Posted transaction in the date
    window (``WHERE COALESCE(act.activity_count, 0) > 0`` baked into
    the outer SELECT).

    Replaces the visual-pinned ``NumericRangeFilter`` (``activity_count
    >= 1`` scoped to the active-only KPI + bar) — that filter narrowed
    correctly in QuickSight but App2's renderer doesn't apply
    visual-scoped filters yet (X.2.g.4 territory). Baking the predicate
    into a second dataset and re-pointing the visuals fixes both
    renderers without growing App2's filter coverage.

    Phase BM — date narrowing pushes down via ``<<$pExecDate*>>`` over
    ``t.posting`` (one SQL form across QS + App2; the day-edge quirk
    dissolves).
    """
    p = cfg.db_table_prefix
    date_clause = _exec_date_range_clause("t.posting", cfg)
    sql = _account_summary_sql_template(p, cfg.dialect).format(
        date_filter=f"AND {date_clause}",
        active_only="WHERE COALESCE(act.activity_count, 0) > 0",
    )
    return build_dataset(
        cfg,
        cfg.prefixed("exec-account-summary-active-dataset"),
        "Executives Account Summary — Active",
        "exec-account-summary-active",
        sql,
        EXEC_ACCOUNT_SUMMARY_CONTRACT,
        visual_identifier=DS_EXEC_ACCOUNT_SUMMARY_ACTIVE,
        dataset_parameters=_exec_universal_range_params(cfg),
    )


EXEC_TRANSACTION_LEGS_CONTRACT = DatasetContract(columns=[
    ColumnSpec("leg_count", "INTEGER"),
])


def build_transaction_legs_dataset(cfg: Config) -> DataSet:
    """BH.8 follow-up (2026-05-26) — single-row dataset returning the
    per-leg / all-status row count of `<prefix>_transactions`. Used by
    the Transaction Volume sheet's sibling "Transfer Legs (all statuses)"
    KPI to surface the documented gap vs the deduped-Posted-only "Total
    Transactions" KPI. Cold-read agents (v11.22.1) read the
    Total-Transactions-vs-App-Info-row-count delta as a defect; putting
    both numbers in the headline row makes the predicate mismatch
    visible instead of mysterious.

    Two reasons NOT to add this measure on the existing transaction-
    summary dataset:
    1. The summary dataset filters `status = 'Posted'` + GROUPs by
       (transfer_id, rail_name). Asking it for an all-leg / all-status
       count means dropping those filters → different dataset.
    2. The point of THIS KPI is the App Info parity. App Info reads
       `<prefix>_transactions` raw; this dataset matches that exactly.
    """
    return build_dataset(
        cfg,
        cfg.prefixed("exec-transaction-legs-dataset"),
        "Executives Transaction Legs (all statuses)",
        "exec-transaction-legs",
        f"SELECT COUNT(*) AS leg_count FROM {cfg.db_table_prefix}_transactions",
        EXEC_TRANSACTION_LEGS_CONTRACT,
        visual_identifier=DS_EXEC_TRANSACTION_LEGS,
    )


def build_all_datasets(cfg: Config) -> list[DataSet]:
    """Return every dataset used by the Executives app."""
    return [
        build_transaction_summary_dataset(cfg),
        build_transaction_daily_dataset(cfg),
        build_transaction_legs_dataset(cfg),
        build_account_summary_dataset(cfg),
        build_account_summary_active_dataset(cfg),
        # M.4.4.5 — App Info ("i") sheet datasets, ALWAYS LAST.
        # M.4.4.7 — per-app segment so deploy <single-app> doesn't
        # delete-then-create another app's App Info dataset.
        build_liveness_dataset(cfg, app_segment="exec"),
        build_matview_status_dataset(
            cfg, app_segment="exec", view_specs=exec_matview_specs(cfg),
        ),
    ]


# Register contracts at module import so the L.1.17 emit-time validator
# can resolve every ds["col"] ref in the visuals below. ``build_dataset()``
# re-registers each contract too — idempotent for the same
# (visual_identifier, contract) pair.
_CONTRACT_REGISTRATIONS: tuple[tuple[str, DatasetContract], ...] = (
    (DS_EXEC_TRANSACTION_SUMMARY, EXEC_TRANSACTION_SUMMARY_CONTRACT),
    (DS_EXEC_TRANSACTION_DAILY, EXEC_TRANSACTION_DAILY_CONTRACT),
    (DS_EXEC_TRANSACTION_LEGS, EXEC_TRANSACTION_LEGS_CONTRACT),
    (DS_EXEC_ACCOUNT_SUMMARY, EXEC_ACCOUNT_SUMMARY_CONTRACT),
    # Y.2.h — same shape as the base; reuses the contract.
    (DS_EXEC_ACCOUNT_SUMMARY_ACTIVE, EXEC_ACCOUNT_SUMMARY_CONTRACT),
)
for _vid, _contract in _CONTRACT_REGISTRATIONS:
    register_contract(_vid, _contract)
