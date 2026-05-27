"""QuickSight DataSet builders for the L1 Dashboard app.

Each builder wraps one M.1a.7 L1 invariant view. The SQL is intentionally
trivial (`SELECT * FROM <prefix>_<view>`) — the views already do the
filtering, computation, and shape work. Datasets here are thin façades
that surface columns to QuickSight visuals via the dataset contract.

The visual_identifier convention is ``l1-<viewname>-ds`` so every
dataset's logical name traces back to the underlying L1 invariant.

Substep landmarks:
    M.2a.3 — drift + ledger_drift datasets
    M.2a.4 — overdraft dataset
    M.2a.5 — limit_breach dataset
    M.2a.6 — today's exceptions UNION dataset (this commit)
"""

from __future__ import annotations

from recon_gen.common.config import Config
from recon_gen.common.dataset_contract import (
    ColumnShape,
    ColumnSpec,
    DatasetContract,
    build_dataset,
)
from recon_gen.common.l2 import L2Instance
from recon_gen.common.models import (
    DataSet,
    DatasetParameter,
    DateTimeDatasetParameter,
    StringDatasetParameter,
    StringDatasetParameterDefaultValues,
)
from recon_gen.common.sheets.app_info import (
    build_liveness_dataset,
    build_matview_status_dataset,
)
from recon_gen.common.sql import Dialect, date_trunc_day, day_text
from recon_gen.common.sql.money import cents_to_dollars_sql
from recon_gen.common.tree import DateView


def l1_matview_specs(cfg: Config) -> list[tuple[str, str | None]]:
    """The L2-prefixed matviews + base tables the L1 dashboard reads,
    paired with the date column the App Info ``latest_date`` KPI takes
    its MAX from.

    Includes both base tables (so the operator can spot ETL freshness
    against the matviews' staleness) and every L1 invariant matview.
    Date columns chosen to match what each table tracks "as-of":
      - transactions / current_transactions / stuck_* → ``posting``
      - daily_balances / current_daily_balances / daily_statement_summary
        → ``business_day_start``
      - drift / ledger_drift / overdraft → ``business_day_end``
      - limit_breach / todays_exceptions → ``business_day``

    Z.C — was ``l1_matview_specs(l2_instance)`` reading
    ``l2_instance.instance``; now reads ``cfg.db_table_prefix``.
    """
    p = cfg.db_table_prefix
    return [
        (f"{p}_transactions", "posting"),
        (f"{p}_daily_balances", "business_day_start"),
        (f"{p}_current_transactions", "posting"),
        (f"{p}_current_daily_balances", "business_day_start"),
        (f"{p}_drift", "business_day_end"),
        (f"{p}_ledger_drift", "business_day_end"),
        (f"{p}_overdraft", "business_day_end"),
        (f"{p}_limit_breach", "business_day"),
        (f"{p}_todays_exceptions", "business_day"),
        (f"{p}_stuck_pending", "posting"),
        (f"{p}_stuck_unbundled", "posting"),
        (f"{p}_daily_statement_summary", "business_day_start"),
    ]


# -- Y.2.g + AA.A.3 pushdown sentinels + enum-value helpers -----------------
#
# Y.2.g converts the L1 dashboard's ~22 per-sheet category-filter
# dropdowns from ``CategoryFilter.with_values(values=[], FILTER_ALL_VALUES)``
# (the X.1.g cold-fetch footgun — it lazy-fetches the column's distinct
# values from QS's ``tenK-sample-values-V2`` endpoint, which 404s on cold
# per-CI-run dashboards) to dataset-SQL pushdown.
#
# AA.A.3 flipped every pushdown dropdown from MULTI to SINGLE (audit at
# ``docs/audits/aa_a_dropdown_audit.md``): the SQL guards
# ``('__l1_all__' = <<$pX>> OR col = <<$pX>>)`` (see ``_data_value_clause``)
# and the dataset param is SINGLE_VALUED with the bare ``L1_ALL_SENTINEL``
# default (1-element list per the dataclass shape). On load the sentinel
# passes the first disjunct so all rows pass; once the analyst picks a
# real value the second disjunct narrows to that single value. The
# pre-AA.A.3 MULTI shape (sentinel-IN-list guard from X.2.t.2) lacked a
# one-click "pick this value and clear the rest" gesture — operators
# pivot drill-to-one 99% of the time, so SINGLE is the right default and
# every dropdown opted into it.
#
# Dropdown OPTIONS are still separate from the dataset-param default:
# enum dropdowns get their options from the control's ``StaticValues``
# (the full declared list — AWS doesn't cap that), data-value dropdowns
# (``account_id`` / ``transfer_id`` / ``status`` / ``origin``) from a
# companion DISTINCT-over-matview dataset via ``LinkedValues``.

# ``col IN ('__no_match__')`` is valid SQL returning zero rows — the
# right outcome for a SINGLE_VALUED dropdown that should start empty
# (Daily Statement account). All other pushdown dropdowns default to
# the show-all sentinel L1_ALL_SENTINEL instead — see the block above.
PUSHDOWN_NO_MATCH_SENTINEL = "__no_match__"

# "Show everything" sentinel — the static default for every SINGLE_VALUED
# pushdown dataset param post-AA.A.3. See the block comment above for the
# SQL-guard shape (``_data_value_clause``).
L1_ALL_SENTINEL = "__l1_all__"
# Pre-quoted form for splicing into SQL (the sentinel is alnum +
# underscores only, so f-string quoting is safe — no escaping needed).
_L1_ALL_SENTINEL_SQL = f"'{L1_ALL_SENTINEL}'"

# Daily Statement is a single-account view (the analyst picks one
# account-day). Its account dataset param is SINGLE_VALUED with this
# sentinel as the static default — matches no real account_id, so a
# freshly-loaded statement is empty until the analyst picks (mirrors
# Investigation's Account Network anchor sentinel, K.4.8k).
_L1_DS_ACCOUNT_SENTINEL = "__l1_no_account_selected__"
_L1_DS_ACCOUNT_SENTINEL_SQL = f"'{_L1_DS_ACCOUNT_SENTINEL}'"

# Fixed ``check_type`` discriminator values the ``<prefix>_todays_exceptions``
# matview's UNION ALL projects (common/l2/schema.py). Schema-level, not
# L2-dependent.
_L1_CHECK_TYPE_VALUES: tuple[str, ...] = (
    "drift",
    "expected_eod_balance_breach",
    "ledger_drift",
    "limit_breach",
    "overdraft",
    "stuck_pending",
    "stuck_unbundled",
)

# v1 ``SupersedeReason`` vocabulary (common/l2/primitives.py). The
# storage column is open enum, but the loader pins this set at load
# time; the demo data only ever produces these three.
_L1_SUPERSEDE_REASON_VALUES: tuple[str, ...] = (
    "BundleAssignment",
    "Inflight",
    "TechnicalCorrection",
)


def l1_rail_universe_values(l2_instance: L2Instance) -> list[str]:
    """Sorted distinct ``rail_name`` values declared across the L2's
    rails + limit schedules — the universe the L1 matviews' ``rail_name``
    column draws from (Z.B 2026-05-15 subsumed ``transfer_type`` into the
    rail). Drives the StaticValues default + dropdown options on every L1
    sheet with a rail-keyed dropdown. Broader source set than
    :func:`l1_rail_values` because limit_breach can surface rows for a
    rail declared only via a LimitSchedule.
    """
    types: set[str] = {str(r.name) for r in l2_instance.rails}
    types |= {str(ls.rail) for ls in l2_instance.limit_schedules}
    return sorted(types)


def l1_rail_values(l2_instance: L2Instance) -> list[str]:
    """Sorted distinct declared Rail names — the universe the L1 matviews'
    ``rail_name`` column draws from. Drives the Rail dropdowns on the
    Pending / Unbundled Aging sheets.
    """
    return sorted(str(r.name) for r in l2_instance.rails)


def l1_account_role_values(l2_instance: L2Instance) -> list[str]:
    """Sorted distinct account roles declared by singleton Accounts +
    AccountTemplates — the universe the L1 matviews' ``account_role``
    column draws from. Drives the Account-Role dropdowns on the Drift /
    Drift Timelines / Overdraft sheets.
    """
    roles: set[str] = {str(a.role) for a in l2_instance.accounts if a.role}
    roles |= {str(t.role) for t in l2_instance.account_templates}
    return sorted(roles)


def l1_supersede_reason_values() -> list[str]:
    """The v1 ``SupersedeReason`` vocabulary. Static dropdown source on
    the Supersession Audit sheet — see ``_L1_SUPERSEDE_REASON_VALUES``.
    """
    return list(_L1_SUPERSEDE_REASON_VALUES)


def l1_check_type_values() -> list[str]:
    """The ``check_type`` discriminator values the Today's Exceptions
    matview projects. Static dropdown source — see ``_L1_CHECK_TYPE_VALUES``.
    """
    return list(_L1_CHECK_TYPE_VALUES)


def _all_sentinel_sv_param(name: str) -> DatasetParameter:
    """AA.A.3 — a SINGLE_VALUED string dataset param whose static default
    is the bare ``L1_ALL_SENTINEL`` (wrapped in a 1-element list per the
    ``StringDatasetParameterDefaultValues`` dataclass shape; the
    ``ValueType="SINGLE_VALUED"`` is the semantic flip). On load the
    bridged scalar dropdown reverts to this default and ``_data_value_clause``
    turns it into a "match everything" predicate.

    Renamed from ``_all_sentinel_mv_param`` in AA.A.3 when every pushdown
    dropdown flipped from MULTI to SINGLE. The dropdown's *options* still
    come from elsewhere (the control's ``StaticValues`` for enum
    dropdowns; a companion ``LinkedValues`` dataset for data-value
    dropdowns)."""
    return _sv_dataset_param(name, L1_ALL_SENTINEL)


def _sv_dataset_param(
    name: str, default: str,
) -> DatasetParameter:
    """A SINGLE_VALUED string dataset parameter with a sentinel default
    (AA.A.3 — every pushdown dropdown uses this shape; pre-AA.A only the
    Daily Statement per-account narrow did)."""
    return DatasetParameter(StringDatasetParameter=StringDatasetParameter(
        Name=name, ValueType="SINGLE_VALUED",
        DefaultValues=StringDatasetParameterDefaultValues(
            StaticValues=[default],
        ),
    ))


def _account_display_clause(param_name: str) -> str:
    """AA.E.2 — WHERE-fragment that matches the dropdown-picked
    ``account_display`` value (``"Sasquatch Cash Master (external-001)"``)
    against the source-view's ``account_name`` + ``account_id`` columns
    inline. Same sentinel-guard shape as :func:`_data_value_clause` so a
    show-all default (``L1_ALL_SENTINEL``) on initial load still passes
    every row.

    Pattern:
        ``('__l1_all__' = <<$p>> OR (account_name || ' (' || account_id || ')') = <<$p>>)``

    QS's ``LinkToDataSetColumn`` carries one column, which drives BOTH
    the dropdown label AND the bound value (no separate label-vs-value
    columns) — so when the dropdown options come from
    ``DS_L1_ACCOUNTS.account_display`` the value bridged through the
    ``MappedDataSetParameters`` IS the display string, and the consuming
    dataset's WHERE clause must match against the same shape. Inlining
    the concat avoids requiring every consuming dataset to expose an
    ``account_display`` column of its own — every L1 matview already
    surfaces ``account_name`` and ``account_id``, so this works
    everywhere AA.E.2 needs it.

    Portable across all three supported dialects (PG / Oracle / SQLite
    all accept ``||`` for string concat with the same semantics).
    """
    expr = "(account_name || ' (' || account_id || ')')"
    return (
        f"({_L1_ALL_SENTINEL_SQL} = <<${param_name}>>"
        f" OR {expr} = <<${param_name}>>)"
    )


def _data_value_clause(col: str, param_name: str) -> str:
    """WHERE-fragment for a SINGLE_VALUED pushdown dropdown: ``('__l1_all__'
    = <<$p>> OR col = <<$p>>)``. On load (and when the dropdown is emptied,
    reverting the bridged dataset param to its ``L1_ALL_SENTINEL`` default
    — see ``_all_sentinel_sv_param``) the first disjunct is true so every
    row passes; once the analyst picks a real value the second disjunct
    narrows to that single value. Used for *both* the data-value dropdowns
    (``account_id`` / ``transfer_id`` / ``status`` / ``origin``, options
    from a companion dataset) and the enum dropdowns (``rail_name`` /
    ``account_role`` / ``check_type`` / ``supersedes``, options from the
    control's ``StaticValues``) — the only difference is where the options
    come from, not the WHERE shape.

    AA.A.3 collapsed the prior multi-value form (``IN (...)``) into this
    single-value scalar form when drill-to-one became the default
    operator workflow (X.2.t.2's MULTI sentinel-guard pattern lacked a
    one-click value picker; analysts deselected every other value to
    drill — see ``docs/audits/aa_a_dropdown_audit.md``). The name
    ``_data_value_clause`` carried over from the dual-purpose helper
    days; the function emits the value-anchored pushdown shape (was
    always a misnomer for "value-anchored pushdown")."""
    return (
        f"({_L1_ALL_SENTINEL_SQL} = <<${param_name}>>"
        f" OR {col} = <<${param_name}>>)"
    )


# Visual identifiers — keys for the Dataset registry on App.
DS_DRIFT = "l1-drift-ds"
DS_LEDGER_DRIFT = "l1-ledger-drift-ds"
DS_OVERDRAFT = "l1-overdraft-ds"
DS_LIMIT_BREACH = "l1-limit-breach-ds"
DS_TODAYS_EXCEPTIONS = "l1-todays-exceptions-ds"
DS_DAILY_STATEMENT_SUMMARY = "l1-daily-statement-summary-ds"
DS_DAILY_STATEMENT_TRANSACTIONS = "l1-daily-statement-transactions-ds"
DS_TRANSACTIONS = "l1-transactions-ds"
DS_DRIFT_TIMELINE = "l1-drift-timeline-ds"
DS_LEDGER_DRIFT_TIMELINE = "l1-ledger-drift-timeline-ds"
DS_STUCK_PENDING = "l1-stuck-pending-ds"
DS_STUCK_UNBUNDLED = "l1-stuck-unbundled-ds"
DS_SUPERSESSION_TRANSACTIONS = "l1-supersession-transactions-ds"
DS_SUPERSESSION_DAILY_BALANCES = "l1-supersession-daily-balances-ds"
# Y.2.g — shared companion datasets feeding the data-value dropdowns
# (account_id / transfer_id / status / origin) across every sheet. Each
# is a DISTINCT projection over the base matview so the dropdown's
# option fetch is a cheap, well-formed query (not the lazy sample-values
# endpoint). `status` / `origin` are open-set in the L1 schema (no fixed
# enum), so they get a companion rather than StaticValues.
DS_L1_ACCOUNTS = "l1-accounts-ds"
DS_L1_TX_IDS = "l1-tx-ids-ds"
DS_L1_TX_FACETS = "l1-tx-facets-ds"
# AA.B.1 — Daily Statement Role cascade: distinct ``account_role`` over
# the daily-balances universe. Feeds the new Role dropdown on Daily
# Statement, which cascades into ``DS_L1_ACCOUNTS`` via the
# ``pL1DsRole`` dataset parameter (role-narrowed account picker).
DS_L1_DS_ROLES = "l1-ds-roles-ds"


# Contracts — column shapes the M.1a.7 views project.
# X.2.u.4.c — aging-bucket bands, moved here from ``app.py`` when the
# Pending / Unbundled Aging sheets' bucket logic became dataset SQL
# (a CASE expr) instead of an analysis-level CalcField — App2's
# column-only fetcher can't evaluate calc-field ``ifelse`` chains, so
# the visuals 500'd on App2; pushing the CASE into the dataset SQL
# (parallel to Y.3 for Investigation) fixes that and the QS side reads
# the same real column.
#
# Each ``(cutoff_seconds, label)`` band: ``age <= cutoff`` ⇒ that label;
# anything bigger ⇒ the overflow label. Labels are number-prefixed so
# the BarChart category sorts the bars chronologically.
_PENDING_AGING_BUCKETS: tuple[tuple[int, str], ...] = (
    (6 * 3600,       "1: 0-6h"),
    (24 * 3600,      "2: 6-24h"),
    (3 * 24 * 3600,  "3: 1-3d"),
    (7 * 24 * 3600,  "4: 3-7d"),
)
_PENDING_AGING_OVERFLOW = "5: >7d"
_UNBUNDLED_AGING_BUCKETS: tuple[tuple[int, str], ...] = (
    (24 * 3600,      "1: <1d"),
    (2 * 24 * 3600,  "2: 1-2d"),
    (7 * 24 * 3600,  "3: 2-7d"),
)
_UNBUNDLED_AGING_OVERFLOW = "4: >7d"


def _aging_bucket_case_sql(
    age_col: str,
    *,
    buckets: tuple[tuple[int, str], ...],
    overflow_label: str,
) -> str:
    """Build a portable ``CASE`` expression bucketing a numeric age
    column (seconds) into labelled bands — the SQL form of the old QS
    calc-field ``ifelse`` chain (X.2.u.4.c)."""
    whens = " ".join(
        f"WHEN {age_col} <= {cutoff} THEN '{label}'"
        for cutoff, label in buckets
    )
    return f"CASE {whens} ELSE '{overflow_label}' END"


DRIFT_CONTRACT = DatasetContract(columns=[
    ColumnSpec("account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
    ColumnSpec("account_name", "STRING"),
    ColumnSpec("account_role", "STRING"),
    ColumnSpec("account_parent_role", "STRING"),
    ColumnSpec("business_day_start", "DATETIME", shape=ColumnShape.DATETIME_DAY),
    ColumnSpec("business_day_end", "DATETIME", shape=ColumnShape.DATETIME_DAY),
    ColumnSpec("stored_balance", "DECIMAL"),
    ColumnSpec("computed_balance", "DECIMAL"),
    ColumnSpec("drift", "DECIMAL"),
])


LEDGER_DRIFT_CONTRACT = DatasetContract(columns=[
    ColumnSpec("account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
    ColumnSpec("account_name", "STRING"),
    ColumnSpec("account_role", "STRING"),
    ColumnSpec("business_day_start", "DATETIME", shape=ColumnShape.DATETIME_DAY),
    ColumnSpec("business_day_end", "DATETIME", shape=ColumnShape.DATETIME_DAY),
    ColumnSpec("stored_balance", "DECIMAL"),
    ColumnSpec("computed_balance", "DECIMAL"),
    ColumnSpec("drift", "DECIMAL"),
])


# Overdraft view exposes only the stored balance (no computed/drift) —
# the violation IS the negative stored balance, no comparison needed.
OVERDRAFT_CONTRACT = DatasetContract(columns=[
    ColumnSpec("account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
    ColumnSpec("account_name", "STRING"),
    ColumnSpec("account_role", "STRING"),
    ColumnSpec("account_parent_role", "STRING"),
    ColumnSpec("business_day_start", "DATETIME", shape=ColumnShape.DATETIME_DAY),
    ColumnSpec("business_day_end", "DATETIME", shape=ColumnShape.DATETIME_DAY),
    ColumnSpec("stored_balance", "DECIMAL"),
])


# Limit breach view groups by (account, day, rail_name), so each
# row is one (parent-account, day, type) cell where the cumulative
# debit total exceeded the L2-configured cap. `business_day` is the
# truncated day (DATETIME, not the start/end pair the daily-balance
# views carry — the M.1a.7 view uses DATE_TRUNC on transaction posting).
LIMIT_BREACH_CONTRACT = DatasetContract(columns=[
    ColumnSpec("account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
    ColumnSpec("account_name", "STRING"),
    ColumnSpec("account_role", "STRING"),
    ColumnSpec("account_parent_role", "STRING"),
    ColumnSpec("business_day", "DATETIME", shape=ColumnShape.DATETIME_DAY),
    ColumnSpec("rail_name", "STRING", shape=ColumnShape.RAIL_NAME),
    # AB.1 (2026-05-19): new column — 'Outbound' / 'Inbound' literal,
    # emitted by the per-direction matview UNION ALL. Visual surfaces
    # in AB.1.8; contract listed here so `SELECT * FROM matview`
    # projects all 9 columns into the dataset's declared shape.
    ColumnSpec("direction", "STRING"),
    ColumnSpec("outbound_total", "DECIMAL"),
    ColumnSpec("cap", "DECIMAL"),
])


# Today's Exceptions UNION across the 5 L1 invariant views. The
# `check_type` discriminator carries the originating constraint name;
# `magnitude` is the per-branch "how bad is it" number normalized to
# absolute value so the bar chart + sort-by-magnitude reads consistently:
#   - drift / ledger_drift / expected_eod_balance_breach: ABS(<delta>)
#   - overdraft: ABS(stored_balance) (always positive — how far below 0)
#   - limit_breach: outbound_total - cap (always positive — overflow over cap)
# `account_parent_role` and `rail_name` are NULL for branches that
# don't carry them (ledger_drift has no parent; only limit_breach has
# rail_name).
TODAYS_EXCEPTIONS_CONTRACT = DatasetContract(columns=[
    ColumnSpec("check_type", "STRING"),
    ColumnSpec("account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
    ColumnSpec("account_name", "STRING"),
    ColumnSpec("account_role", "STRING"),
    ColumnSpec("account_parent_role", "STRING"),
    ColumnSpec("business_day", "DATETIME", shape=ColumnShape.DATETIME_DAY),
    ColumnSpec("rail_name", "STRING", shape=ColumnShape.RAIL_NAME),
    # AO.4 — split: amount populated for money branches (drift, ledger_drift,
    # overdraft, expected_eod_balance_breach, limit_breach, stuck_pending,
    # stuck_unbundled); count populated for transfer-keyed cardinality
    # branches (chain_parent_disagreement, xor_group_violation,
    # fan_in_disagreement, multi_xor_violation). Exactly one non-NULL per row.
    ColumnSpec("magnitude_amount", "DECIMAL"),
    ColumnSpec("magnitude_count", "INTEGER"),
])


# Daily Statement summary — one row per (account_id, business_day_start)
# across every internal account (and external if scoped). Sheet-level
# filters narrow to a single (account, day) for KPIs + detail; the
# dataset itself is unfiltered so the dropdown can browse all accounts.
# `opening_balance` = LAG(money) from prior business_day; `total_debits`
# / `_credits` from per-day transaction sums on `<prefix>_current_transactions`;
# `closing_balance_recomputed` = opening + signed-net of the day; `drift`
# = stored − recomputed. Drift is the single visual cue that the feed
# is consistent (= 0 on a healthy day).
DAILY_STATEMENT_SUMMARY_CONTRACT = DatasetContract(columns=[
    ColumnSpec("account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
    ColumnSpec("account_name", "STRING"),
    ColumnSpec("account_role", "STRING"),
    ColumnSpec("account_parent_role", "STRING"),
    ColumnSpec("account_scope", "STRING"),
    ColumnSpec("business_day_start", "DATETIME", shape=ColumnShape.DATETIME_DAY),
    ColumnSpec("business_day_end", "DATETIME", shape=ColumnShape.DATETIME_DAY),
    ColumnSpec("opening_balance", "DECIMAL"),
    ColumnSpec("total_debits", "DECIMAL"),
    ColumnSpec("total_credits", "DECIMAL"),
    ColumnSpec("net_flow", "DECIMAL"),
    ColumnSpec("leg_count", "INTEGER"),
    ColumnSpec("closing_balance_stored", "DECIMAL"),
    ColumnSpec("closing_balance_recomputed", "DECIMAL"),
    ColumnSpec("drift", "DECIMAL"),
])


# Daily Statement transactions — one row per Money record (leg) across
# every account-day. Same per-account-day filter pattern as the summary;
# detail table on the sheet renders the day's legs once both filters are
# applied. `business_day` = DATE_TRUNC('day', posting) so the
# business_day_start filter on the summary side aligns with this column.
DAILY_STATEMENT_TRANSACTIONS_CONTRACT = DatasetContract(columns=[
    ColumnSpec("transaction_id", "STRING"),
    ColumnSpec("account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
    ColumnSpec("account_name", "STRING"),
    ColumnSpec("business_day", "DATETIME", shape=ColumnShape.DATETIME_DAY),
    ColumnSpec("posting", "DATETIME"),
    ColumnSpec("transfer_id", "STRING", shape=ColumnShape.TRANSFER_ID),
    ColumnSpec("rail_name", "STRING", shape=ColumnShape.RAIL_NAME),
    ColumnSpec("amount_money", "DECIMAL"),
    ColumnSpec("amount_direction", "STRING"),
    ColumnSpec("status", "STRING"),
    ColumnSpec("origin", "STRING"),
])


# Transactions sheet — raw posting ledger, every leg in the L2's
# current_transactions matview. Supersession-aware via Current* (no
# superseded entries). Columns are a subset of the matview shape: drop
# entry / account_scope / transfer_completion / metadata / template_name
# / bundle_id / supersedes since they're internal-only and the per-leg
# table doesn't need them. account_id + transfer_id stay for drill
# wiring (M.2b.7).
TRANSACTIONS_CONTRACT = DatasetContract(columns=[
    ColumnSpec("transaction_id", "STRING"),
    ColumnSpec("account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
    ColumnSpec("account_name", "STRING"),
    ColumnSpec("account_role", "STRING"),
    ColumnSpec("account_parent_role", "STRING"),
    ColumnSpec("transfer_id", "STRING", shape=ColumnShape.TRANSFER_ID),
    ColumnSpec("transfer_parent_id", "STRING"),
    ColumnSpec("rail_name", "STRING", shape=ColumnShape.RAIL_NAME),
    ColumnSpec("amount_money", "DECIMAL"),
    ColumnSpec("amount_direction", "STRING"),
    ColumnSpec("status", "STRING"),
    ColumnSpec("origin", "STRING"),
    ColumnSpec("posting", "DATETIME"),
    ColumnSpec("transfer_completion", "DATETIME"),
])


# Drift timelines pre-aggregate ABS(drift) by (business_day_end,
# account_role) — one point per role per day. Sourced from the small
# drift / ledger_drift matviews (already tiny — only violations); the
# (account_role) index supports the GROUP BY at sub-ms latency. The
# dashboard uses these for the LineChart primitive (one line per role).
DRIFT_TIMELINE_CONTRACT = DatasetContract(columns=[
    ColumnSpec("business_day_end", "DATETIME", shape=ColumnShape.DATETIME_DAY),
    ColumnSpec("account_role", "STRING"),
    ColumnSpec("abs_drift", "DECIMAL"),
])


# Stuck Pending / Unbundled — both M.2b.8/9 matviews share the same
# 13-col shape (only the cap column name differs:
# `max_pending_age_seconds` vs `max_unbundled_age_seconds`). Defined as
# 2 contracts so the L.1.17 typed Column refs catch column-name
# mismatches at the wiring site (one contract collapsing both would
# require optional cols, which the contract intentionally doesn't model).
STUCK_PENDING_CONTRACT = DatasetContract(columns=[
    ColumnSpec("transaction_id", "STRING"),
    ColumnSpec("account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
    ColumnSpec("account_name", "STRING"),
    ColumnSpec("account_role", "STRING"),
    ColumnSpec("account_parent_role", "STRING"),
    ColumnSpec("transfer_id", "STRING", shape=ColumnShape.TRANSFER_ID),
    ColumnSpec("rail_name", "STRING", shape=ColumnShape.RAIL_NAME),
    ColumnSpec("amount_money", "DECIMAL"),
    ColumnSpec("amount_direction", "STRING"),
    ColumnSpec("posting", "DATETIME"),
    ColumnSpec("max_pending_age_seconds", "INTEGER"),
    ColumnSpec("age_seconds", "DECIMAL"),
    ColumnSpec("stuck_pending_aging_bucket", "STRING"),
])


STUCK_UNBUNDLED_CONTRACT = DatasetContract(columns=[
    ColumnSpec("transaction_id", "STRING"),
    ColumnSpec("account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
    ColumnSpec("account_name", "STRING"),
    ColumnSpec("account_role", "STRING"),
    ColumnSpec("account_parent_role", "STRING"),
    ColumnSpec("transfer_id", "STRING", shape=ColumnShape.TRANSFER_ID),
    ColumnSpec("rail_name", "STRING", shape=ColumnShape.RAIL_NAME),
    ColumnSpec("amount_money", "DECIMAL"),
    ColumnSpec("amount_direction", "STRING"),
    ColumnSpec("posting", "DATETIME"),
    ColumnSpec("max_unbundled_age_seconds", "INTEGER"),
    ColumnSpec("age_seconds", "DECIMAL"),
    ColumnSpec("stuck_unbundled_aging_bucket", "STRING"),
])


# Supersession Audit datasets — surface logical keys whose append-only
# `entry` column has multiple rows (the audit trail of technical-error
# corrections, inflight-bundling reposts, etc). Read from the BASE
# tables, NOT Current* (Current* hides superseded entries by design —
# that's the whole point of the max-Entry-per-logical-key view). The
# `supersedes` column tells you why the higher entry exists per L1's
# v1 SupersedeReason vocabulary (Inflight / BundleAssignment /
# TechnicalCorrection).
SUPERSESSION_TRANSACTIONS_CONTRACT = DatasetContract(columns=[
    ColumnSpec("entry", "INTEGER"),
    ColumnSpec("transaction_id", "STRING"),
    ColumnSpec("supersedes", "STRING"),
    ColumnSpec("l1_supersession_no_reason", "INTEGER"),
    ColumnSpec("account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
    ColumnSpec("account_name", "STRING"),
    ColumnSpec("transfer_id", "STRING", shape=ColumnShape.TRANSFER_ID),
    ColumnSpec("rail_name", "STRING", shape=ColumnShape.RAIL_NAME),
    ColumnSpec("amount_money", "DECIMAL"),
    ColumnSpec("amount_direction", "STRING"),
    ColumnSpec("status", "STRING"),
    ColumnSpec("posting", "DATETIME"),
    ColumnSpec("bundle_id", "STRING"),
])


SUPERSESSION_DAILY_BALANCES_CONTRACT = DatasetContract(columns=[
    ColumnSpec("entry", "INTEGER"),
    ColumnSpec("account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
    ColumnSpec("account_name", "STRING"),
    ColumnSpec("account_role", "STRING"),
    ColumnSpec("supersedes", "STRING"),
    ColumnSpec("business_day_start", "DATETIME", shape=ColumnShape.DATETIME_DAY),
    ColumnSpec("business_day_end", "DATETIME", shape=ColumnShape.DATETIME_DAY),
    ColumnSpec("money", "DECIMAL"),
])


# Y.2.g — companion datasets feeding the data-value dropdowns. One row
# per distinct id; one column. The bridged dataset param substitutes
# the selected id(s) into the consuming dataset's ``col IN (...)``.
L1_ACCOUNTS_CONTRACT = DatasetContract(columns=[
    ColumnSpec("account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
    # AA.B.1 — account_role threads through so the Daily Statement Role
    # cascade can narrow the account picker via the ``pL1DsRole`` dataset
    # param. The column isn't surfaced in the dropdown; it just enables
    # the WHERE-clause filter.
    ColumnSpec("account_role", "STRING"),
    # AA.E.2 — account_name + account_display drive the searchable
    # ``Sasquatch Cash Master (external-001)`` dropdown labels. The
    # dropdown's ``LinkedValues`` reads ``account_display``; QS's
    # one-column LinkToDataSetColumn means the bound value is the same
    # display string, so consuming datasets match against
    # ``_account_display_clause(...)``.
    ColumnSpec("account_name", "STRING"),
    ColumnSpec("account_display", "STRING", shape=ColumnShape.ACCOUNT_DISPLAY),
])

# AA.B.1 — distinct account_role for the Daily Statement Role dropdown.
L1_DS_ROLES_CONTRACT = DatasetContract(columns=[
    ColumnSpec("account_role", "STRING"),
])

L1_TX_IDS_CONTRACT = DatasetContract(columns=[
    ColumnSpec("transfer_id", "STRING", shape=ColumnShape.TRANSFER_ID),
])

L1_TX_FACETS_CONTRACT = DatasetContract(columns=[
    ColumnSpec("status", "STRING"),
    ColumnSpec("origin", "STRING"),
])


# -- Builders ----------------------------------------------------------------


# Y.2.g — the Drift sheet's Account + Account-Role dropdowns are
# cross_dataset=ALL_DATASETS: one control narrows BOTH the leaf-drift
# and ledger-drift tables. The analysis param bridges to a same-named
# dataset param on each dataset (mirrors L2FT's Y.2.e TT sheet). The
# Drift Timelines sheet's Account-Role dropdown likewise narrows both
# timeline datasets.
P_L1_DRIFT_ACCOUNT = "pL1DriftAccount"
P_L1_DRIFT_ROLE = "pL1DriftRole"
P_L1_DRIFT_TL_ROLE = "pL1DriftTlRole"


def build_drift_dataset(cfg: Config, l2_instance: L2Instance) -> DataSet:
    """Wrap the leaf-account drift view from M.1a.7.

    Rows in this dataset are leaf-account drift violations only — the
    M.1a.7 view pre-filters to ``stored_balance != computed_balance``.
    No `drift_status='in_balance'` rows; if the dashboard wants to show
    "all accounts including no-drift", it queries the underlying
    Current* view directly, not this dataset.

    Y.2.g — the Account / Account-Role dropdowns push down here via
    ``account_id`` (data-value, sentinel-OR) + ``account_role`` (enum,
    ``IN (...)``). Same dataset-param names on ``build_ledger_drift_dataset``
    so one ALL_DATASETS dropdown narrows both.

    Y.2.f — App2-side date pushdown via ``{date_filter}`` template
    slot (X.2.g.1.b dual-SQL pattern). QS continues to filter via the
    analysis-level ``TimeRangeFilter`` FG (zero behavior change); App2
    binds ``:date_from`` / ``:date_to`` from the URL into
    ``business_day_start``.

    AO.1.impl — money columns (stored_balance / computed_balance /
    drift) project as BIGINT cents from the matview; wrap each with
    ``cents_to_dollars_sql`` at this read boundary so the dashboard
    receives dollars. SELECT * is replaced with the explicit column
    list because the wrap needs per-column control.
    """
    prefix = cfg.db_table_prefix
    sb = cents_to_dollars_sql("stored_balance", dialect=cfg.dialect)
    cb = cents_to_dollars_sql("computed_balance", dialect=cfg.dialect)
    drift = cents_to_dollars_sql("drift", dialect=cfg.dialect)
    sql_template = (
        f"SELECT account_id, account_name, account_role,"
        f" account_parent_role, business_day_start, business_day_end,"
        f" {sb} AS stored_balance,"
        f" {cb} AS computed_balance,"
        f" {drift} AS drift\n"
        f"FROM {prefix}_drift\n"
        f"WHERE {_account_display_clause(P_L1_DRIFT_ACCOUNT)}\n"
        f"  AND {_data_value_clause('account_role', P_L1_DRIFT_ROLE)}\n"
        f"  {{date_filter}}"
    )
    return build_dataset(
        cfg, cfg.prefixed("l1-drift-dataset"),
        "L1 Drift", "l1-drift",
        sql_template, DRIFT_CONTRACT,
        visual_identifier=DS_DRIFT,
        dataset_parameters=[
            _all_sentinel_sv_param(P_L1_DRIFT_ACCOUNT),
            _all_sentinel_sv_param(P_L1_DRIFT_ROLE),
        ],
        app2_date_column="business_day_start",
    )


def build_ledger_drift_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """Wrap the parent-account drift view from M.1a.7.

    Same shape as ``build_drift_dataset`` minus ``account_parent_role``
    (parent accounts ARE the parents — no parent_role column on this
    view). Carries the same Y.2.g dataset-param names as the leaf-drift
    dataset so the Drift sheet's ALL_DATASETS dropdowns narrow both.

    Y.2.f — App2-side date pushdown matches ``build_drift_dataset``.

    AO.1.impl — cents → dollars wrap mirrors ``build_drift_dataset``.
    """
    prefix = cfg.db_table_prefix
    sb = cents_to_dollars_sql("stored_balance", dialect=cfg.dialect)
    cb = cents_to_dollars_sql("computed_balance", dialect=cfg.dialect)
    drift = cents_to_dollars_sql("drift", dialect=cfg.dialect)
    sql_template = (
        f"SELECT account_id, account_name, account_role,"
        f" business_day_start, business_day_end,"
        f" {sb} AS stored_balance,"
        f" {cb} AS computed_balance,"
        f" {drift} AS drift\n"
        f"FROM {prefix}_ledger_drift\n"
        f"WHERE {_account_display_clause(P_L1_DRIFT_ACCOUNT)}\n"
        f"  AND {_data_value_clause('account_role', P_L1_DRIFT_ROLE)}\n"
        f"  {{date_filter}}"
    )
    return build_dataset(
        cfg, cfg.prefixed("l1-ledger-drift-dataset"),
        "L1 Ledger Drift", "l1-ledger-drift",
        sql_template, LEDGER_DRIFT_CONTRACT,
        visual_identifier=DS_LEDGER_DRIFT,
        dataset_parameters=[
            _all_sentinel_sv_param(P_L1_DRIFT_ACCOUNT),
            _all_sentinel_sv_param(P_L1_DRIFT_ROLE),
        ],
        app2_date_column="business_day_start",
    )


# Y.2.g — per-sheet single-dataset pushdown param names + IDs.
P_L1_OVERDRAFT_ACCOUNT = "pL1OverdraftAccount"
P_L1_OVERDRAFT_ROLE = "pL1OverdraftRole"
P_L1_LIMIT_BREACH_ACCOUNT = "pL1LimitBreachAccount"
P_L1_LIMIT_BREACH_TYPE = "pL1LimitBreachType"


def build_overdraft_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """Wrap the internal-account overdraft view from M.1a.7.

    Rows are accounts with negative stored balance — the L1 invariant
    is "no internal account holds negative money." External accounts
    are excluded by the view (filtered to ``account_scope = 'internal'``).

    Y.2.g — Account dropdown pushes down via ``account_id`` (data-value,
    sentinel-OR); Account-Role dropdown via ``account_role IN (...)``.

    Y.2.f — App2-side date pushdown via ``business_day_start``.

    AO.1.impl — wrap ``stored_balance`` (BIGINT cents) → dollars.
    """
    prefix = cfg.db_table_prefix
    sb = cents_to_dollars_sql("stored_balance", dialect=cfg.dialect)
    sql_template = (
        f"SELECT account_id, account_name, account_role,"
        f" account_parent_role, business_day_start, business_day_end,"
        f" {sb} AS stored_balance\n"
        f"FROM {prefix}_overdraft\n"
        f"WHERE {_account_display_clause(P_L1_OVERDRAFT_ACCOUNT)}\n"
        f"  AND {_data_value_clause('account_role', P_L1_OVERDRAFT_ROLE)}\n"
        f"  {{date_filter}}"
    )
    return build_dataset(
        cfg, cfg.prefixed("l1-overdraft-dataset"),
        "L1 Overdraft", "l1-overdraft",
        sql_template, OVERDRAFT_CONTRACT,
        visual_identifier=DS_OVERDRAFT,
        dataset_parameters=[
            _all_sentinel_sv_param(P_L1_OVERDRAFT_ACCOUNT),
            _all_sentinel_sv_param(P_L1_OVERDRAFT_ROLE),
        ],
        app2_date_column="business_day_start",
    )


def build_limit_breach_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """Wrap the per-(account, day, type) limit-breach view from M.1a.7.

    Each row is one cell where the cumulative outbound debit exceeded
    the L2-configured cap. Caps are inlined in the view at emit-time
    from the L2 LimitSchedules — no JSON path lookups in the dataset
    SQL.

    Y.2.g — Account dropdown pushes down via ``account_id`` (data-value,
    sentinel-OR); Transfer Type dropdown via ``rail_name IN (...)``.

    Y.2.f — App2-side date pushdown via ``business_day``.

    AO.1.impl — both ``outbound_total`` (SUM(ABS(amount_money)) in
    matview) and ``cap`` (multiplied ×100 in the matview per foundation
    so cents-vs-cents comparison holds) project as BIGINT cents; wrap
    each → dollars at the dataset boundary.
    """
    prefix = cfg.db_table_prefix
    outbound = cents_to_dollars_sql("outbound_total", dialect=cfg.dialect)
    cap = cents_to_dollars_sql("cap", dialect=cfg.dialect)
    sql_template = (
        f"SELECT account_id, account_name, account_role,"
        f" account_parent_role, business_day, rail_name, direction,"
        f" {outbound} AS outbound_total,"
        f" {cap} AS cap\n"
        f"FROM {prefix}_limit_breach\n"
        f"WHERE {_account_display_clause(P_L1_LIMIT_BREACH_ACCOUNT)}\n"
        f"  AND {_data_value_clause('rail_name', P_L1_LIMIT_BREACH_TYPE)}\n"
        f"  {{date_filter}}"
    )
    return build_dataset(
        cfg, cfg.prefixed("l1-limit-breach-dataset"),
        "L1 Limit Breach", "l1-limit-breach",
        sql_template, LIMIT_BREACH_CONTRACT,
        visual_identifier=DS_LIMIT_BREACH,
        dataset_parameters=[
            _all_sentinel_sv_param(P_L1_LIMIT_BREACH_ACCOUNT),
            _all_sentinel_sv_param(P_L1_LIMIT_BREACH_TYPE),
        ],
        app2_date_column="business_day",
    )


# Y.2.g — Today's Exceptions pushdown params.
P_L1_TODAYS_EXC_CHECK_TYPE = "pL1TodaysExcCheckType"
P_L1_TODAYS_EXC_ACCOUNT = "pL1TodaysExcAccount"
P_L1_TODAYS_EXC_TYPE = "pL1TodaysExcType"


def build_todays_exceptions_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """Wrap the `<prefix>_todays_exceptions` matview from M.1a.9.

    M.1a.9 promoted the UNION ALL from inline CustomSql to a per-instance
    MATERIALIZED VIEW so each visual on the Today's Exceptions sheet
    reads a precomputed table instead of re-running the 5-branch UNION.
    Refresh contract: integrators MUST call `refresh_matviews_sql()`
    after every batch insert into the base tables.

    Y.2.g + AA.A.3 — three dropdowns push down, all SINGLE_VALUED post-AA.A.3
    sentinel-guard form: ``check_type`` (enum), ``account_id`` (data-value),
    and ``rail_name`` (enum). ``rail_name`` is NULL for every branch except
    limit / stuck rows, so the predicate keeps the NULL-type rows on load
    (and while narrowing) — matching the FILTER_ALL_VALUES behavior it
    replaces.

    Y.2.f — App2-side date pushdown via ``business_day``.

    AO.4 — the matview now splits ``magnitude`` into two columns by
    source-branch unit: ``magnitude_amount`` (BIGINT cents — money
    branches) and ``magnitude_count`` (INT — transfer-keyed cardinality
    branches). Exactly one is non-NULL per row. Wrap ``magnitude_amount``
    cents → dollars; pass ``magnitude_count`` through bare.
    """
    prefix = cfg.db_table_prefix
    magnitude_amount = cents_to_dollars_sql(
        "magnitude_amount", dialect=cfg.dialect,
    )
    sql_template = (
        f"SELECT check_type, account_id, account_name, account_role,"
        f" account_parent_role, business_day, rail_name,"
        f" {magnitude_amount} AS magnitude_amount,"
        f" magnitude_count\n"
        f"FROM {prefix}_todays_exceptions\n"
        f"WHERE {_data_value_clause('check_type', P_L1_TODAYS_EXC_CHECK_TYPE)}\n"
        f"  AND {_account_display_clause(P_L1_TODAYS_EXC_ACCOUNT)}\n"
        f"  AND ({_data_value_clause('rail_name', P_L1_TODAYS_EXC_TYPE)}"
        f" OR rail_name IS NULL)\n"
        f"  {{date_filter}}"
    )
    return build_dataset(
        cfg, cfg.prefixed("l1-todays-exceptions-dataset"),
        "L1 Today's Exceptions", "l1-todays-exceptions",
        sql_template, TODAYS_EXCEPTIONS_CONTRACT,
        visual_identifier=DS_TODAYS_EXCEPTIONS,
        dataset_parameters=[
            # AA.A.3 — all three dropdowns flipped from MULTI to SINGLE per
            # the drill-to-one default (audit row pL1TodaysExcCheckType +
            # pL1TodaysExcAccount + pL1TodaysExcType). check_type was the
            # one fixed enum that kept its value-list default pre-flip; now
            # uses the sentinel-default + match-all guard like the others.
            _all_sentinel_sv_param(P_L1_TODAYS_EXC_CHECK_TYPE),
            _all_sentinel_sv_param(P_L1_TODAYS_EXC_ACCOUNT),
            _all_sentinel_sv_param(P_L1_TODAYS_EXC_TYPE),
        ],
        app2_date_column="business_day",
    )


# Y.2.g — Daily Statement is single-account: one SINGLE_VALUED dataset
# param (the same name on the summary + transactions datasets) bridged
# from the analysis-level account picker. Sentinel default → empty
# statement until the analyst picks.
P_L1_DS_ACCOUNT_DSP = "pL1DsAccount"

# AO.2 — the balance-date narrow is SQL-pushed-down. The dataset param's
# name matches the analysis-level ``pL1DsBalanceDate`` so QS's
# ``MappedDataSetParameters`` bridge substitutes the picked day.
#
# AR.2 (D5 view rollout) — both defaults (analysis + dataset) now derive
# from one ``DateView`` (`common/tree/date_view.py`), so the C1 dual-
# default split is structurally unrepresentable. The pre-AR.2
# ``_L1_DS_LATEST_SENTINEL`` ("2999-12-31") + the SQL OR-clause that
# fired on it ("if param ≥ sentinel, fall back to MAX(day)") are gone:
# under strict collapse the picker default IS the data anchor, so the
# operator picks the account (and, if needed, a different day) rather
# than relying on a SQL safety net. See `docs/audits/date_range_model_
# audit.md` §5 "AR.1 result" + the AR.2 operator call (strict collapse).
P_L1_DS_BALANCE_DATE_DSP = "pL1DsBalanceDate"


def _date_dataset_param(name: str, view: DateView) -> DatasetParameter:
    """A SINGLE_VALUED DateTime dataset param defaulting to the view's
    anchor day. ONE source of truth — the same view also emits the
    analysis-param default and the App2 binding."""
    return DatasetParameter(
        DateTimeDatasetParameter=DateTimeDatasetParameter(
            Name=name, ValueType="SINGLE_VALUED", TimeGranularity="DAY",
            DefaultValues=view.emit_qs_dataset_default(),
        ),
    )

# AA.B.1 — Role cascade dataset param. Lives on the DS_L1_ACCOUNTS
# companion (the account-picker's option source) so picking a role
# narrows the account dropdown to that role's accounts; the bridged
# analysis param is ``pL1DsRole`` and reverts to ``L1_ALL_SENTINEL``
# (the "show all roles" default) on load.
P_L1_DS_ROLE_DSP = "pL1DsRole"


def build_daily_statement_summary_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """Wrap the `<prefix>_daily_statement_summary` matview from M.1a.9.

    M.1a.9 promoted the LAG window + LEFT JOIN + GROUP BY CTE from
    inline CustomSql to a per-instance MATERIALIZED VIEW so each KPI
    on the Daily Statement sheet reads a precomputed table instead of
    re-evaluating the multi-CTE per visual (5 KPIs × CTE = 5
    re-evaluations otherwise). Refresh contract via
    `refresh_matviews_sql()` after every batch insert.

    Y.2.g — the per-account narrow pushes down via ``account_id =
    <<$pL1DsAccount>>`` instead of an analysis-level CategoryFilter; the
    sheet's account dropdown reads its options from the ``DS_L1_ACCOUNTS``
    companion (not this parameterized dataset).
    """
    prefix = cfg.db_table_prefix
    view = DateView(frame=cfg.test_generator.as_of_frame())
    # AO.10 / AR.2 — compare the balance date as YYYY-MM-DD text on both
    # sides. The param substitutes TWO shapes: a string LITERAL in the
    # api/smoke path (smoke reads the dataset's StaticValues default and
    # inlines it as text) and a typed timestamp VALUE in the QS runtime
    # path (MappedDataSetParameters bridges the typed analysis value
    # through). PG can't disambiguate `to_char(<unknown-typed string>,
    # text)` AND can't `substr(timestamp,…)`. Cast the param to DATE
    # first — PG's `CAST(<expr> AS DATE)` accepts both ISO-T string
    # literals AND timestamp values; Oracle's the same. SQLite has no
    # real DATE type — strftime parses ISO strings directly — so the
    # cast is dialect-skipped to keep the no-op out of SQLite's path.
    day = day_text("business_day_start", cfg.dialect)
    _param_ref = f"<<${P_L1_DS_BALANCE_DATE_DSP}>>"
    bdate_param = (
        _param_ref
        if cfg.dialect is Dialect.SQLITE
        else f"CAST({_param_ref} AS DATE)"
    )
    bdate = day_text(bdate_param, cfg.dialect)
    acct = "(account_name || ' (' || account_id || ')')"
    # AO.2 / AR.2 — balance-date narrow is a strict day equality. The
    # pre-AR.2 ``OR (bdate ≥ sentinel ...)`` latest-on-empty fallback is
    # gone: the picker default is the view's anchor day, never the
    # sentinel, so the fallback never fired anyway. Strict-collapse
    # behavior: anchor-day with no data ⇒ blank statement; operator
    # adjusts the picker (paired with the account selection they already
    # have to do).
    # AO.1.impl — money columns (opening_balance / total_debits /
    # total_credits / net_flow / closing_balance_stored /
    # closing_balance_recomputed / drift) project as BIGINT cents from
    # the daily-statement-summary matview; wrap each into dollars at the
    # dataset boundary so dashboard KPIs receive dollars.
    opening = cents_to_dollars_sql("opening_balance", dialect=cfg.dialect)
    debits = cents_to_dollars_sql("total_debits", dialect=cfg.dialect)
    credits = cents_to_dollars_sql("total_credits", dialect=cfg.dialect)
    net = cents_to_dollars_sql("net_flow", dialect=cfg.dialect)
    closing_st = cents_to_dollars_sql(
        "closing_balance_stored", dialect=cfg.dialect,
    )
    closing_rc = cents_to_dollars_sql(
        "closing_balance_recomputed", dialect=cfg.dialect,
    )
    drift_d = cents_to_dollars_sql("drift", dialect=cfg.dialect)
    sql = (
        f"SELECT account_id, account_name, account_role,"
        f" account_parent_role, account_scope,"
        f" business_day_start, business_day_end,"
        f" {opening} AS opening_balance,"
        f" {debits} AS total_debits,"
        f" {credits} AS total_credits,"
        f" {net} AS net_flow,"
        f" leg_count,"
        f" {closing_st} AS closing_balance_stored,"
        f" {closing_rc} AS closing_balance_recomputed,"
        f" {drift_d} AS drift\n"
        f"FROM {prefix}_daily_statement_summary\n"
        f"WHERE {acct} = <<${P_L1_DS_ACCOUNT_DSP}>>\n"
        f"  AND {day} = {bdate}"
    )
    return build_dataset(
        cfg, cfg.prefixed("l1-daily-statement-summary-dataset"),
        "L1 Daily Statement Summary", "l1-daily-statement-summary",
        sql, DAILY_STATEMENT_SUMMARY_CONTRACT,
        visual_identifier=DS_DAILY_STATEMENT_SUMMARY,
        dataset_parameters=[
            _sv_dataset_param(P_L1_DS_ACCOUNT_DSP,
                              _L1_DS_ACCOUNT_SENTINEL),
            _date_dataset_param(P_L1_DS_BALANCE_DATE_DSP, view),
        ],
    )


def _daily_statement_transactions_sql(prefix: str, dialect: Dialect) -> str:
    """Per-leg projection from `<prefix>_current_transactions` carrying
    everything the Daily Statement detail table renders. Narrowed to one
    ``account_id`` via the ``pL1DsAccount`` dataset param (Y.2.g); the
    date filter stays analysis-level.

    ``business_day`` is a day-truncation of ``posting``; built via
    ``date_trunc_day`` so the projection stays a TIMESTAMP-shaped value
    on both Postgres (DATE_TRUNC) and Oracle (CAST(TRUNC(...) AS
    TIMESTAMP)) — keeps QuickSight's date column-type inference stable
    across dialects.
    """
    # Projected column stays a TIMESTAMP-shaped trunc so QuickSight's date
    # column-type inference is stable across dialects (see docstring).
    business_day = date_trunc_day("tx.posting", dialect)
    # AO.10 / AR.2 — see build_daily_statement_summary_dataset for the
    # CAST-then-day_text rationale (param substitutes as a string in the
    # api/smoke path + as a typed timestamp at QS runtime; CAST AS DATE
    # accepts both on PG/Oracle, SQLite's strftime handles strings
    # directly so its branch skips the cast).
    day_txt = day_text("tx.posting", dialect)
    _param_ref = f"<<${P_L1_DS_BALANCE_DATE_DSP}>>"
    bdate_param = (
        _param_ref
        if dialect is Dialect.SQLITE
        else f"CAST({_param_ref} AS DATE)"
    )
    bdate = day_text(bdate_param, dialect)
    # AO.2 / AR.2 — same balance-date narrow as the summary; strict day
    # equality (pre-AR.2 latest-on-empty fallback removed per the
    # view-primitive strict-collapse decision).
    # AO.1.impl — tx.amount_money is BIGINT cents; project as dollars.
    amount = cents_to_dollars_sql("tx.amount_money", dialect=dialect)
    return (
        f"SELECT tx.id AS transaction_id,"
        f"       tx.account_id, tx.account_name,"
        f"       {business_day} AS business_day,"
        f"       tx.posting,"
        f"       tx.transfer_id, tx.rail_name,"
        f"       {amount} AS amount_money, tx.amount_direction,"
        f"       tx.status, tx.origin"
        f" FROM {prefix}_current_transactions tx"
        f" WHERE (tx.account_name || ' (' || tx.account_id || ')') = <<${P_L1_DS_ACCOUNT_DSP}>>"
        f"   AND {day_txt} = {bdate}"
    )


def build_daily_statement_transactions_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """Wrap the per-leg ledger feed for Daily Statement detail rows."""
    sql = _daily_statement_transactions_sql(cfg.db_table_prefix, cfg.dialect)
    view = DateView(frame=cfg.test_generator.as_of_frame())
    return build_dataset(
        cfg, cfg.prefixed("l1-daily-statement-transactions-dataset"),
        "L1 Daily Statement Transactions",
        "l1-daily-statement-transactions",
        sql, DAILY_STATEMENT_TRANSACTIONS_CONTRACT,
        visual_identifier=DS_DAILY_STATEMENT_TRANSACTIONS,
        dataset_parameters=[
            _sv_dataset_param(P_L1_DS_ACCOUNT_DSP,
                              _L1_DS_ACCOUNT_SENTINEL),
            _date_dataset_param(P_L1_DS_BALANCE_DATE_DSP, view),
        ],
    )


# Y.2.g — Transactions sheet pushdown params. account_id / transfer_id /
# status / origin are data-value (sentinel-OR; status + origin are
# open-set in the L1 schema); rail_name is the bounded enum.
P_L1_TX_ACCOUNT = "pL1TxAccount"
P_L1_TX_TRANSFER_ID = "pL1TxTransferId"
P_L1_TX_STATUS = "pL1TxStatus"
P_L1_TX_ORIGIN = "pL1TxOrigin"
P_L1_TX_TYPE = "pL1TxType"


def build_transactions_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """Wrap `<prefix>_current_transactions` matview for the Transactions
    sheet's raw posting ledger.

    Reads from the matview directly (M.1a.9 made it a MATERIALIZED VIEW
    + indexed) so per-account / per-transfer / per-status / per-origin /
    per-rail_name filter dropdowns hit indexed lookups. Projects
    only the analyst-visible columns; internal columns (entry,
    account_scope, supersedes, bundle_id, template_name, metadata) stay
    in the matview but aren't surfaced.

    Y.2.g — all five dropdowns push into this SQL: account_id /
    transfer_id / status / origin via the sentinel-OR data-value guard,
    rail_name via ``IN (...)``.

    Y.2.f — App2-side date pushdown via ``posting``.
    """
    prefix = cfg.db_table_prefix
    # AO.1.impl — amount_money is BIGINT cents on the matview; wrap to
    # dollars at projection.
    amount = cents_to_dollars_sql("amount_money", dialect=cfg.dialect)
    sql_template = (
        f"SELECT id AS transaction_id, account_id, account_name,"
        f" account_role, account_parent_role,"
        f" transfer_id, transfer_parent_id, rail_name,"
        f" {amount} AS amount_money, amount_direction, status, origin,"
        f" posting, transfer_completion"
        f" FROM {prefix}_current_transactions\n"
        f"WHERE {_account_display_clause(P_L1_TX_ACCOUNT)}\n"
        f"  AND {_data_value_clause('transfer_id', P_L1_TX_TRANSFER_ID)}\n"
        f"  AND {_data_value_clause('status', P_L1_TX_STATUS)}\n"
        f"  AND {_data_value_clause('origin', P_L1_TX_ORIGIN)}\n"
        f"  AND {_data_value_clause('rail_name', P_L1_TX_TYPE)}\n"
        f"  {{date_filter}}"
    )
    return build_dataset(
        cfg, cfg.prefixed("l1-transactions-dataset"),
        "L1 Transactions", "l1-transactions",
        sql_template, TRANSACTIONS_CONTRACT,
        visual_identifier=DS_TRANSACTIONS,
        dataset_parameters=[
            _all_sentinel_sv_param(P_L1_TX_ACCOUNT),
            _all_sentinel_sv_param(P_L1_TX_TRANSFER_ID),
            _all_sentinel_sv_param(P_L1_TX_STATUS),
            _all_sentinel_sv_param(P_L1_TX_ORIGIN),
            _all_sentinel_sv_param(P_L1_TX_TYPE),
        ],
        app2_date_column="posting",
    )


def build_drift_timeline_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """Pre-aggregate leaf-account drift by (business_day_end, account_role).

    One row per (day, role) carrying SUM(ABS(drift)). Source matview is
    already small (only violations) and indexed on `account_role`, so the
    GROUP BY runs at indexed-scan latency. Backs the leaf-drift LineChart.

    Y.2.g — the Drift Timelines sheet's Account-Role dropdown narrows
    BOTH timeline datasets via the same ``pL1DriftTlRole`` dataset param;
    the predicate sits before the GROUP BY.

    Y.2.f — App2-side date pushdown via ``business_day_end``; the
    ``{date_filter}`` slot lands BEFORE the GROUP BY so the AND-clause
    is part of the WHERE.
    """
    prefix = cfg.db_table_prefix
    # AO.1.impl — SUM(ABS(drift)) is BIGINT cents from the matview;
    # wrap the aggregate to dollars at projection.
    abs_drift = cents_to_dollars_sql(
        "SUM(ABS(drift))", dialect=cfg.dialect,
    )
    sql_template = (
        f"SELECT business_day_end,"
        f"       account_role,"
        f"       {abs_drift} AS abs_drift"
        f" FROM {prefix}_drift"
        f" WHERE {_data_value_clause('account_role', P_L1_DRIFT_TL_ROLE)}"
        f" {{date_filter}}"
        f" GROUP BY business_day_end, account_role"
    )
    return build_dataset(
        cfg, cfg.prefixed("l1-drift-timeline-dataset"),
        "L1 Drift Timeline", "l1-drift-timeline",
        sql_template, DRIFT_TIMELINE_CONTRACT,
        visual_identifier=DS_DRIFT_TIMELINE,
        dataset_parameters=[
            _all_sentinel_sv_param(P_L1_DRIFT_TL_ROLE),
        ],
        app2_date_column="business_day_end",
    )


def build_ledger_drift_timeline_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """Pre-aggregate ledger drift by (business_day_end, account_role).

    Same shape as the leaf-drift timeline, sourced from the parent-account
    drift matview. Backs the ledger-drift LineChart. Carries the same
    ``pL1DriftTlRole`` Y.2.g param so the ALL_DATASETS dropdown narrows
    both timelines.

    Y.2.f — App2-side date pushdown matches ``build_drift_timeline_dataset``.
    """
    prefix = cfg.db_table_prefix
    # AO.1.impl — same as build_drift_timeline_dataset: aggregate of
    # BIGINT-cents drift wrapped to dollars at projection.
    abs_drift = cents_to_dollars_sql(
        "SUM(ABS(drift))", dialect=cfg.dialect,
    )
    sql_template = (
        f"SELECT business_day_end,"
        f"       account_role,"
        f"       {abs_drift} AS abs_drift"
        f" FROM {prefix}_ledger_drift"
        f" WHERE {_data_value_clause('account_role', P_L1_DRIFT_TL_ROLE)}"
        f" {{date_filter}}"
        f" GROUP BY business_day_end, account_role"
    )
    return build_dataset(
        cfg, cfg.prefixed("l1-ledger-drift-timeline-dataset"),
        "L1 Ledger Drift Timeline", "l1-ledger-drift-timeline",
        sql_template, DRIFT_TIMELINE_CONTRACT,
        visual_identifier=DS_LEDGER_DRIFT_TIMELINE,
        dataset_parameters=[
            _all_sentinel_sv_param(P_L1_DRIFT_TL_ROLE),
        ],
        app2_date_column="business_day_end",
    )


# Y.2.g — Pending / Unbundled Aging pushdown params (same three vectors
# over their respective matviews).
P_L1_PENDING_ACCOUNT = "pL1PendingAccount"
P_L1_PENDING_TYPE = "pL1PendingType"
P_L1_PENDING_RAIL = "pL1PendingRail"
P_L1_UNBUNDLED_ACCOUNT = "pL1UnbundledAccount"
P_L1_UNBUNDLED_TYPE = "pL1UnbundledType"
P_L1_UNBUNDLED_RAIL = "pL1UnbundledRail"


def build_stuck_pending_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """Wrap the M.2b.8 `<prefix>_stuck_pending` matview.

    Pending transactions whose age exceeds the per-rail
    `max_pending_age` cap. Backs the M.2b.10 Pending Aging sheet —
    aging buckets come from a calc field on the dataset, so the SQL
    stays a thin SELECT * passthrough plus the Y.2.g pushdown WHERE
    (account_id data-value + rail_name / rail_name enums).
    """
    prefix = cfg.db_table_prefix
    # AO.1.impl — t.amount_money projects from the matview as BIGINT
    # cents (the stuck-pending matview SELECTs ct.amount_money straight
    # through); wrap to dollars at the dataset boundary. SELECT t.* is
    # expanded to explicit columns so the per-money-column wrap can be
    # applied without touching non-money columns.
    amount = cents_to_dollars_sql("t.amount_money", dialect=cfg.dialect)
    sql = (
        f"SELECT t.transaction_id, t.account_id, t.account_name,"
        f" t.account_role, t.account_parent_role,"
        f" t.transfer_id, t.rail_name,"
        f" {amount} AS amount_money,"
        f" t.amount_direction, t.posting,"
        f" t.max_pending_age_seconds, t.age_seconds,\n"
        f"  {_aging_bucket_case_sql('age_seconds', buckets=_PENDING_AGING_BUCKETS, overflow_label=_PENDING_AGING_OVERFLOW)}"
        f" AS stuck_pending_aging_bucket\n"
        f"FROM {prefix}_stuck_pending t\n"
        f"WHERE {_account_display_clause(P_L1_PENDING_ACCOUNT)}\n"
        f"  AND {_data_value_clause('rail_name', P_L1_PENDING_TYPE)}\n"
        f"  AND {_data_value_clause('rail_name', P_L1_PENDING_RAIL)}"
    )
    return build_dataset(
        cfg, cfg.prefixed("l1-stuck-pending-dataset"),
        "L1 Stuck Pending", "l1-stuck-pending",
        sql, STUCK_PENDING_CONTRACT,
        visual_identifier=DS_STUCK_PENDING,
        dataset_parameters=[
            _all_sentinel_sv_param(P_L1_PENDING_ACCOUNT),
            _all_sentinel_sv_param(P_L1_PENDING_TYPE),
            _all_sentinel_sv_param(P_L1_PENDING_RAIL),
        ],
    )


def build_stuck_unbundled_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """Wrap the M.2b.9 `<prefix>_stuck_unbundled` matview.

    Posted transactions where bundle_id IS NULL and age exceeds the
    per-rail `max_unbundled_age` cap. Backs the M.2b.11 Unbundled
    Aging sheet. Same Y.2.g pushdown vectors as the Pending Aging
    dataset.
    """
    prefix = cfg.db_table_prefix
    # AO.1.impl — same shape as build_stuck_pending_dataset; expand
    # SELECT t.* to wrap amount_money cents → dollars.
    amount = cents_to_dollars_sql("t.amount_money", dialect=cfg.dialect)
    sql = (
        f"SELECT t.transaction_id, t.account_id, t.account_name,"
        f" t.account_role, t.account_parent_role,"
        f" t.transfer_id, t.rail_name,"
        f" {amount} AS amount_money,"
        f" t.amount_direction, t.posting,"
        f" t.max_unbundled_age_seconds, t.age_seconds,\n"
        f"  {_aging_bucket_case_sql('age_seconds', buckets=_UNBUNDLED_AGING_BUCKETS, overflow_label=_UNBUNDLED_AGING_OVERFLOW)}"
        f" AS stuck_unbundled_aging_bucket\n"
        f"FROM {prefix}_stuck_unbundled t\n"
        f"WHERE {_account_display_clause(P_L1_UNBUNDLED_ACCOUNT)}\n"
        f"  AND {_data_value_clause('rail_name', P_L1_UNBUNDLED_TYPE)}\n"
        f"  AND {_data_value_clause('rail_name', P_L1_UNBUNDLED_RAIL)}"
    )
    return build_dataset(
        cfg, cfg.prefixed("l1-stuck-unbundled-dataset"),
        "L1 Stuck Unbundled", "l1-stuck-unbundled",
        sql, STUCK_UNBUNDLED_CONTRACT,
        visual_identifier=DS_STUCK_UNBUNDLED,
        dataset_parameters=[
            _all_sentinel_sv_param(P_L1_UNBUNDLED_ACCOUNT),
            _all_sentinel_sv_param(P_L1_UNBUNDLED_TYPE),
            _all_sentinel_sv_param(P_L1_UNBUNDLED_RAIL),
        ],
    )


# Y.2.g — Supersession Audit's lone dropdown (the `supersedes`
# cause-class). `supersedes` is NULL on the entry-1 (original) rows of
# every audit trail, so the predicate keeps the originals visible on
# load and while narrowing — you always see the trail, the dropdown
# just narrows which cause class you're auditing.
P_L1_SUPERSEDE_REASON = "pL1SupersedeReason"


def build_supersession_transactions_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """Pull rows from `<prefix>_transactions` whose logical `id` has
    multiple `entry` values — the audit trail for superseded postings.

    Reads from the BASE table (not `<prefix>_current_transactions`)
    because Current* hides superseded entries by design. Uses a window
    function (`COUNT(*) OVER (PARTITION BY id) > 1`) instead of an
    `id IN (... GROUP BY id HAVING COUNT(*) > 1)` subquery — the
    window form survives QuickSight's distinct-values dropdown query
    rewriting (which wraps the dataset SQL in `SELECT DISTINCT col
    FROM (...)` for filter dropdowns; QS chokes on the IN-subquery +
    `ORDER BY` combo). Sort handled by the dashboard, not the
    dataset.

    Y.2.g + AA.A.3 — the Supersedes-Reason dropdown pushes down via
    ``((sentinel = <<$p>> OR supersedes = <<$p>>) OR supersedes IS NULL)``.
    AA.A.3 flipped it from MULTI to SINGLE per the drill-to-one default
    (audit row pL1SupersedeReason); the ``OR supersedes IS NULL`` keeps
    the entry-1 rows of every audit trail visible on load and while
    narrowing (you always see the trail, the dropdown narrows which
    cause class you're auditing).
    """
    prefix = cfg.db_table_prefix
    # AO.1.impl — amount_money lives in the BASE table as BIGINT cents;
    # wrap the outer projection so the dataset surfaces dollars.
    amount = cents_to_dollars_sql("amount_money", dialect=cfg.dialect)
    sql = (
        f"SELECT entry, transaction_id, supersedes,"
        f" CASE WHEN entry > 1 AND supersedes IS NULL THEN 1 ELSE 0 END"
        f"   AS l1_supersession_no_reason,"
        f" account_id, account_name,"
        f" transfer_id, rail_name,"
        f" {amount} AS amount_money,"
        f" amount_direction, status, posting, bundle_id"
        f" FROM ("
        f"   SELECT entry, id AS transaction_id, supersedes,"
        f"   account_id, account_name,"
        f"   transfer_id, rail_name,"
        f"   amount_money, amount_direction, status, posting, bundle_id,"
        f"   COUNT(*) OVER (PARTITION BY id) AS entry_count"
        f"   FROM {prefix}_transactions"
        f" ) sub"
        f" WHERE entry_count > 1"
        f" AND ({_data_value_clause('supersedes', P_L1_SUPERSEDE_REASON)}"
        f" OR supersedes IS NULL)"
    )
    return build_dataset(
        cfg, cfg.prefixed("l1-supersession-transactions-dataset"),
        "L1 Supersession — Transactions",
        "l1-supersession-transactions",
        sql, SUPERSESSION_TRANSACTIONS_CONTRACT,
        visual_identifier=DS_SUPERSESSION_TRANSACTIONS,
        dataset_parameters=[
            _all_sentinel_sv_param(P_L1_SUPERSEDE_REASON),
        ],
    )


def build_supersession_daily_balances_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """Pull rows from `<prefix>_daily_balances` whose logical key
    `(account_id, business_day_start)` has multiple `entry` values.

    Same window-function pattern as the transactions audit dataset
    — `COUNT(*) OVER (PARTITION BY account_id, business_day_start)`
    + outer `WHERE > 1` filter. Sort handled by the dashboard.
    """
    prefix = cfg.db_table_prefix
    # AO.1.impl — daily_balances.money is BIGINT cents under the
    # foundation; wrap to dollars at the outer projection.
    money = cents_to_dollars_sql("money", dialect=cfg.dialect)
    sql = (
        f"SELECT entry,"
        f" account_id, account_name, account_role, supersedes,"
        f" business_day_start, business_day_end,"
        f" {money} AS money"
        f" FROM ("
        f"   SELECT entry,"
        f"   account_id, account_name, account_role, supersedes,"
        f"   business_day_start, business_day_end, money,"
        f"   COUNT(*) OVER (PARTITION BY account_id, business_day_start)"
        f"     AS entry_count"
        f"   FROM {prefix}_daily_balances"
        f" ) sub"
        f" WHERE entry_count > 1"
    )
    return build_dataset(
        cfg, cfg.prefixed("l1-supersession-daily-balances-dataset"),
        "L1 Supersession — Daily Balances",
        "l1-supersession-daily-balances",
        sql, SUPERSESSION_DAILY_BALANCES_CONTRACT,
        visual_identifier=DS_SUPERSESSION_DAILY_BALANCES,
    )


def build_l1_accounts_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """Y.2.g companion — distinct ``(account_id, account_role)`` over
    the universe of accounts. Feeds every L1 sheet's Account dropdown
    via ``LinkedValues`` (the Daily Statement sheet's account dropdown
    re-points here too).

    Reads the UNION of ``<prefix>_current_daily_balances`` and
    ``<prefix>_current_transactions`` so every account that's reachable
    from ANY L1 sheet's matview appears in the dropdown. The earlier
    daily-balances-only source missed accounts that only show up in
    Pending-state transactions (StuckPendingGenerator emits a Pending
    transaction with NO balance row — see
    ``spine/stuck_pending.py:10``). Spine-planted ``tmpl-cust-*``
    accounts only have Pending rows, so the old dropdown excluded
    them — picking them was impossible even though the Pending Aging
    sheet's table surfaced the matching matview rows. Same
    DISTINCT-inside-UNION shape stays cheap as the matview grows
    (DISTINCT on the (small) account column, not on full rows).

    AA.B.1 — carries ``account_role`` so the Daily Statement Role
    cascade can narrow the account picker via the ``pL1DsRole``
    dataset param. Default = ``L1_ALL_SENTINEL`` (show every account
    regardless of role); picking a role in the Role dropdown narrows
    the account dropdown to that role's accounts. The companion stays
    used by *every* L1 sheet's Account dropdown — sheets that don't
    bridge a role param into ``pL1DsRole`` get the sentinel default
    and see every account, preserving today's behaviour.
    """
    prefix = cfg.db_table_prefix
    sql = (
        f"SELECT DISTINCT account_id, account_role, account_name,"
        f" (account_name || ' (' || account_id || ')') AS account_display"
        f" FROM ("
        f"   SELECT account_id, account_role, account_name"
        f"   FROM {prefix}_current_daily_balances"
        f"   UNION"
        f"   SELECT account_id, account_role, account_name"
        f"   FROM {prefix}_current_transactions"
        f"   UNION"
        # BL.3 — `<prefix>_todays_exceptions` is a UNION ALL of 5
        # invariant matviews; some branches (multi_xor_violation,
        # chain_parent_disagreement, etc.) carry account_id values
        # that aren't in either daily_balances OR current_transactions
        # (they key off a template/rail rather than a transaction). The
        # Today's Exceptions sheet's Account dropdown needs to surface
        # those accounts too — picker tests timed out before this
        # third union term landed.
        f"   SELECT account_id, account_role, account_name"
        f"   FROM {prefix}_todays_exceptions"
        f" ) accounts_universe"
        f" WHERE {_data_value_clause('account_role', P_L1_DS_ROLE_DSP)}"
    )
    return build_dataset(
        cfg, cfg.prefixed("l1-accounts-dataset"),
        "L1 Accounts", "l1-accounts",
        sql, L1_ACCOUNTS_CONTRACT,
        visual_identifier=DS_L1_ACCOUNTS,
        dataset_parameters=[
            _all_sentinel_sv_param(P_L1_DS_ROLE_DSP),
        ],
    )


def build_l1_ds_roles_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """AA.B.1 — distinct ``account_role`` over the daily-balances
    universe. Feeds the Daily Statement Role dropdown (the new
    cascade source) via ``LinkedValues``. Unparameterized — every
    role the deployed institution actually uses shows up.

    Same shape as ``build_l1_accounts_dataset``: cheap DISTINCT over
    ``current_daily_balances``; the matview's per-account-day
    granularity guarantees every role with at least one tracked
    account appears.
    """
    prefix = cfg.db_table_prefix
    sql = f"SELECT DISTINCT account_role FROM {prefix}_current_daily_balances"
    return build_dataset(
        cfg, cfg.prefixed("l1-ds-roles-dataset"),
        "L1 Daily Statement Roles", "l1-ds-roles",
        sql, L1_DS_ROLES_CONTRACT,
        visual_identifier=DS_L1_DS_ROLES,
    )


def build_l1_tx_ids_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """Y.2.g companion — distinct ``transfer_id`` over the current
    ledger. Feeds the Transactions sheet's Transfer dropdown via
    ``LinkedValues``.
    """
    prefix = cfg.db_table_prefix
    sql = (
        f"SELECT DISTINCT transfer_id FROM {prefix}_current_transactions"
        f" WHERE transfer_id IS NOT NULL"
    )
    return build_dataset(
        cfg, cfg.prefixed("l1-tx-ids-dataset"),
        "L1 Transfer IDs", "l1-tx-ids",
        sql, L1_TX_IDS_CONTRACT,
        visual_identifier=DS_L1_TX_IDS,
    )


def build_l1_tx_facets_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """Y.2.g companion — distinct ``(status, origin)`` over the current
    ledger. Feeds the Transactions sheet's Status + Origin dropdowns via
    ``LinkedValues``. Both columns are open-set in the L1 schema, so a
    companion (cheap, well-formed query) replaces the StaticValues path
    used for the bounded enum columns.
    """
    prefix = cfg.db_table_prefix
    sql = f"SELECT DISTINCT status, origin FROM {prefix}_current_transactions"
    return build_dataset(
        cfg, cfg.prefixed("l1-tx-facets-dataset"),
        "L1 Transaction Facets", "l1-tx-facets",
        sql, L1_TX_FACETS_CONTRACT,
        visual_identifier=DS_L1_TX_FACETS,
    )


def build_all_l1_dashboard_datasets(
    cfg: Config, l2_instance: L2Instance,
) -> list[DataSet]:
    """Return every dataset the L1 dashboard's sheets reference.

    `build_l1_dashboard_app` calls this and registers each result on the
    App tree. Per Z.C, the cfg arrives fully populated (``deployment_name``
    + ``db_table_prefix`` are required cfg fields), so dataset IDs carry
    the deployment prefix via ``cfg.prefixed(...)`` directly — no auto-
    stamping dance.
    """
    return [
        build_drift_dataset(cfg, l2_instance),
        build_ledger_drift_dataset(cfg, l2_instance),
        build_overdraft_dataset(cfg, l2_instance),
        build_limit_breach_dataset(cfg, l2_instance),
        build_todays_exceptions_dataset(cfg, l2_instance),
        build_daily_statement_summary_dataset(cfg, l2_instance),
        build_daily_statement_transactions_dataset(cfg, l2_instance),
        build_transactions_dataset(cfg, l2_instance),
        build_drift_timeline_dataset(cfg, l2_instance),
        build_ledger_drift_timeline_dataset(cfg, l2_instance),
        build_stuck_pending_dataset(cfg, l2_instance),
        build_stuck_unbundled_dataset(cfg, l2_instance),
        build_supersession_transactions_dataset(cfg, l2_instance),
        build_supersession_daily_balances_dataset(cfg, l2_instance),
        # Y.2.g — companion datasets for the data-value dropdowns.
        build_l1_accounts_dataset(cfg, l2_instance),
        build_l1_ds_roles_dataset(cfg, l2_instance),
        build_l1_tx_ids_dataset(cfg, l2_instance),
        build_l1_tx_facets_dataset(cfg, l2_instance),
        # M.4.4.5 — App Info ("i") sheet datasets, ALWAYS LAST.
        # M.4.4.7 — per-app segment so deploy <single-app> doesn't
        # delete-then-create another app's App Info dataset.
        build_liveness_dataset(cfg, app_segment="l1"),
        build_matview_status_dataset(
            cfg, app_segment="l1",
            view_specs=l1_matview_specs(cfg),
        ),
    ]
