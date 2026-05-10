"""QuickSight DataSet builders for the L2 Flow Tracing app.

The Chains / L2 Exceptions tabs join L2-declared values (static, from
the L2 instance) to runtime activity (from the prefixed
``<prefix>_current_transactions`` matview). The L2 declarations are
inlined into the SQL as a CTE of literal rows — no per-rail dataset
proliferation, no per-instance database table.

The Rails tab is a transactions explorer (M.3.10c rewrite — the
M.3.5 declared-rails table moves to a future Docs tab). It uses two
new datasets that participate in the metadata cascade:

- ``l2ft-postings-ds``: one row per leg, parameterized on ``pKey`` +
  ``pValues`` so the metadata cascade filters it via QS ``<<$param>>``
  substitution into a JSONPath.
- ``l2ft-meta-values-ds``: distinct metadata values for the chosen
  key, parameterized on ``pKey`` so the Value dropdown narrows when
  the Key dropdown changes.

Substep landmarks:

- M.3.4 — skeleton (no datasets)
- M.3.5 — Rails dataset (later DROPPED in M.3.10c — moves to Docs tab)
- M.3.6 — Chains dataset
- M.3.7 — L2 Exceptions datasets (six small KPI-backers)
- M.3.8 — Auto metadata-driven filter dropdown sources
  (later DROPPED in M.3.10c — replaced by the cascade)
- M.3.10c — Rails tab redesign on dataset parameters
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from quicksight_gen.common.config import Config
from quicksight_gen.common.dataset_contract import (
    ColumnShape,
    ColumnSpec,
    DatasetContract,
    build_dataset,
)
from quicksight_gen.common.l2 import (
    L2Instance,
    SingleLegRail,
    TwoLegRail,
    posted_requirements_for,
)
from quicksight_gen.common.models import (
    DataSet,
    DatasetParameter,
    StringDatasetParameter,
    StringDatasetParameterDefaultValues,
)
from quicksight_gen.common.sheets.app_info import (
    build_liveness_dataset,
    build_matview_status_dataset,
)
from quicksight_gen.common.sql import (
    Dialect,
    dual_from,
    greatest,
    json_value,
    typed_null,
)
from quicksight_gen.common.tree import Dataset


def l2ft_matview_specs(
    l2_instance: L2Instance,
) -> list[tuple[str, str | None]]:
    """Matviews + base tables the L2 Flow Tracing dashboard reads,
    paired with the date column for App Info's ``latest_date`` KPI.

    Includes the base tables (transactions / daily_balances) so the
    operator can spot stale matviews against fresh ETL loads at a
    glance. Mirrors ``l1_matview_specs`` / ``inv_matview_specs``.
    """
    p = str(l2_instance.instance)
    return [
        (f"{p}_transactions", "posting"),
        (f"{p}_daily_balances", "business_day_start"),
        (f"{p}_current_transactions", "posting"),
        (f"{p}_current_daily_balances", "business_day_start"),
    ]


# Visual identifiers — keys for the Dataset registry on App.
DS_POSTINGS = "l2ft-postings-ds"
DS_META_VALUES = "l2ft-meta-values-ds"
DS_CHAINS = "l2ft-chains-ds"
DS_CHAIN_INSTANCES = "l2ft-chain-instances-ds"
DS_TT_INSTANCES = "l2ft-tt-instances-ds"
DS_TT_LEGS = "l2ft-tt-legs-ds"
DS_UNIFIED_L2_EXCEPTIONS = "l2ft-unified-exceptions-ds"
# M.3.7 — six L2 exception sections, each backed by its own narrow dataset.
DS_EXC_CHAIN_ORPHANS = "l2ft-exc-chain-orphans-ds"
DS_EXC_UNMATCHED_TRANSFER_TYPE = "l2ft-exc-unmatched-transfer-type-ds"
DS_EXC_DEAD_RAILS = "l2ft-exc-dead-rails-ds"
DS_EXC_DEAD_BUNDLES_ACTIVITY = "l2ft-exc-dead-bundles-activity-ds"
DS_EXC_DEAD_METADATA = "l2ft-exc-dead-metadata-ds"
DS_EXC_DEAD_LIMIT_SCHEDULES = "l2ft-exc-dead-limit-schedules-ds"


# Sentinel value for the metadata Key parameter's default. The
# transactions dataset's WHERE clause short-circuits to "no metadata
# filter" when the picked key equals this sentinel, so a freshly-
# loaded dashboard renders all rows even before the analyst engages
# the cascade. Placed at module scope so app.py + tests can reference
# it from a single source of truth.
META_KEY_ALL_SENTINEL = "__ALL__"

# Sentinel default for the multi-valued Value parameter. When the
# Key has been picked but no Value has yet been chosen, this default
# matches no real metadata value → the table goes empty as a hint
# the analyst still needs to pick a Value. Lives in CamelCase-safe
# form (no underscores other than at the boundaries — QS parameter
# *names* require alphanumerics, but parameter *values* can be
# anything the SQL accepts).
META_VALUE_PLACEHOLDER_SENTINEL = "__placeholder__"

# Y.2.c/d — fallback value for a multi-valued pushdown parameter whose
# declared-value list is empty for this L2 instance (e.g. an instance
# with no Chains declared → ``declared_chain_parents`` returns ``[]``).
# An empty ``StaticValues`` default would substitute as ``IN ()`` —
# invalid SQL on every dialect. This sentinel matches no real value, so
# ``col IN ('__no_match__')`` is valid SQL that returns zero rows — the
# correct outcome (nothing to show) without a syntax error.
PUSHDOWN_NO_MATCH_SENTINEL = "__no_match__"

# Dataset-parameter IDs are stable UUIDs so re-deploying produces
# byte-identical DatasetParameters JSON. QS-issued IDs would re-rotate
# on every regenerate.
_DSP_ID_PKEY = "11111111-1111-4111-8111-111111111111"
_DSP_ID_PVALUES = "22222222-2222-4222-8222-222222222222"
# Y.2.c — postings-dataset pushdown params (rail / status / bundle).
# MULTI_VALUED; bridged from the Rails sheet's MULTI_SELECT dropdowns.
_DSP_ID_PRAIL = "33333333-3333-4333-8333-333333333333"
_DSP_ID_PSTATUS = "44444444-4444-4444-8444-444444444444"
_DSP_ID_PBUNDLE = "55555555-5555-4555-8555-555555555555"
# Y.2.d — chain-instances-dataset pushdown params (chain / completion).
_DSP_ID_PCHAINSCHAIN = "66666666-6666-4666-8666-666666666666"
_DSP_ID_PCHAINSCOMPLETION = "77777777-7777-4777-8777-777777777777"
# Y.2.e — Transfer Templates pushdown params (template / completion).
# Declared on BOTH tt-instances + tt-legs (the Template/Completion
# dropdowns narrow the Table + the Sankey together); same Id reused
# across the two datasets is fine — IDs are unique per-dataset, not global.
_DSP_ID_PTTTEMPLATE = "88888888-8888-4888-8888-888888888888"
_DSP_ID_PTTCOMPLETION = "99999999-9999-4999-8999-999999999999"


# Per-ChainEntry edge — declared parent→child relationship + runtime
# parent firing counts + matched-child counts + orphan rate. A row IS
# one Sankey edge in the Chains visual.
#
# M.3.10d: no longer wired into the Chains sheet (the Sankey + edge
# details moved out in favor of a per-instance explorer); kept in the
# module for the M.7 Docs render of declared topology.
CHAINS_CONTRACT = DatasetContract(columns=[
    ColumnSpec("parent_name", "STRING"),
    ColumnSpec("child_name", "STRING"),
    ColumnSpec("required", "STRING"),     # 'Required' / 'Optional' for display
    ColumnSpec("xor_group", "STRING"),    # NULL when no XOR membership
    ColumnSpec("source_node", "STRING"),  # display label for the parent node
    ColumnSpec("target_node", "STRING"),  # display label for the child node
    ColumnSpec("parent_firing_count", "INTEGER"),
    ColumnSpec("child_firing_count", "INTEGER"),
    ColumnSpec("orphan_count", "INTEGER"),
    ColumnSpec("orphan_rate", "DECIMAL"),
])


# Per-parent-firing chain-instance row backing the Chains sheet's
# explorer (M.3.10d). One row per distinct parent transfer firing of
# any L2-declared chain-parent name; ``completion_status`` is computed
# inline from required-child firings against the parent's transfer_id.
# Parameterized on pKey + pValues for the metadata cascade.
CHAIN_INSTANCES_CONTRACT = DatasetContract(columns=[
    # parent_chain_name is a drill destination for the L2 Exceptions
    # table's "View in Chains" right-click — see
    # UNIFIED_L2_EXCEPTIONS_CONTRACT for the full drill story. Holds
    # either a rail OR a template name per SPEC.
    ColumnSpec(
        "parent_chain_name", "STRING",
        shape=ColumnShape.L2_DECLARED_NAME,
    ),
    ColumnSpec("parent_transfer_id", "STRING"),
    ColumnSpec("parent_posting", "DATETIME"),
    ColumnSpec("parent_status", "STRING"),
    ColumnSpec("parent_amount_money", "DECIMAL"),
    ColumnSpec("required_total", "INTEGER"),
    ColumnSpec("required_fired", "INTEGER"),
    ColumnSpec("completion_status", "STRING"),
])


# Per-shared-Transfer row backing the Transfer Templates sheet's
# Table (M.3.10f, completion_status reshaped in M.3.10j).
#
# ``completion_status`` (M.3.10j) combines the L1 conservation check
# (``actual_net`` ≈ ``expected_net``) with the L2 chain completeness
# check (every Required child fired AND every XOR group has exactly
# one fired). Three states cover the meaningful outcomes:
#
# - 'Complete' — balanced AND no chain orphans / XOR violations
# - 'Imbalanced' — legs don't sum to expected_net (L1 break)
# - 'Orphaned' — balanced but a Required child missing OR an XOR
#   group has 0 or > 1 fired members (L2 chain break)
#
# Mirrors chain-instances completion_status semantics so the analyst
# sees consistent language across the Chains and Transfer Templates
# sheets.
TT_INSTANCES_CONTRACT = DatasetContract(columns=[
    ColumnSpec("template_name", "STRING"),
    ColumnSpec("transfer_id", "STRING"),
    ColumnSpec("posting", "DATETIME"),
    ColumnSpec("expected_net", "DECIMAL"),
    ColumnSpec("actual_net", "DECIMAL"),
    ColumnSpec("net_diff", "DECIMAL"),
    ColumnSpec("leg_count", "INTEGER"),
    ColumnSpec("completion_status", "STRING"),
])


# Per-leg row backing the Transfer Templates sheet's Sankey (M.3.10f).
# One row per leg of any current_transactions row carrying a
# template_name (i.e., legs that joined a TransferTemplate's shared
# Transfer). ``flow_source`` / ``flow_target`` derive from
# ``amount_direction`` so the Sankey reads as:
#
#   debit account → template_name → credit account
#                         │
#                         ├──> matched chain child rail (M.3.10i)
#                         └──> orphan chain child rail (M.3.10i,
#                              synthetic row for declared edges
#                              the runtime didn't fire)
#
# Width = ABS(amount_money). Each leg contributes one segment to one
# side of the template middle-node. The shared template middle-node
# means a 4-leg shared Transfer renders as 2 source nodes + the
# template + 2 target nodes — natural multi-leg flow visualization.
#
# M.3.10i adds chain-child edges as additional flow segments coming
# OUT of the template node. ``edge_kind`` = 'template_leg' for the
# original legs; 'chain_matched' / 'chain_orphan' for the L2-declared
# chain children flowing from the template. Synthetic orphan rows
# are emitted per (parent firing, declared chain child) where no
# matched child exists, so the Sankey shows the FULL declared topology
# even when runtime data is incomplete.
#
# Shares ``template_name`` + ``posting`` + ``completion_status``
# columns with tt-instances so cross_dataset='ALL_DATASETS' filter
# groups apply BOTH the date + template + completion dropdowns to
# both datasets in lockstep — picking 'Complete' on the Completion
# dropdown narrows the Sankey and the Table together to just the
# matching firings (M.3.10k).
#
# Parameterized on pKey + pValues for the metadata cascade.
TT_LEGS_CONTRACT = DatasetContract(columns=[
    ColumnSpec("template_name", "STRING"),
    ColumnSpec("transfer_id", "STRING"),
    ColumnSpec("posting", "DATETIME"),
    ColumnSpec("account_name", "STRING"),
    ColumnSpec("account_role", "STRING"),
    ColumnSpec("amount_money", "DECIMAL"),
    ColumnSpec("amount_direction", "STRING"),
    ColumnSpec("amount_abs", "DECIMAL"),
    ColumnSpec("flow_source", "STRING"),
    ColumnSpec("flow_target", "STRING"),
    ColumnSpec("edge_kind", "STRING"),
    ColumnSpec("completion_status", "STRING"),
])




# -- L2 Exception contracts (M.3.7) ------------------------------------------

# L2.1 — required Chain entries where parent fired but child didn't.
# Subset of CHAINS_CONTRACT pre-filtered to (required + orphan_count > 0).
EXC_CHAIN_ORPHANS_CONTRACT = DatasetContract(columns=[
    ColumnSpec("parent_name", "STRING"),
    ColumnSpec("child_name", "STRING"),
    ColumnSpec("parent_firing_count", "INTEGER"),
    ColumnSpec("child_firing_count", "INTEGER"),
    ColumnSpec("orphan_count", "INTEGER"),
])


# L2.2 — Posted Transactions whose transfer_type doesn't match any
# declared Rail.transfer_type.
EXC_UNMATCHED_TRANSFER_TYPE_CONTRACT = DatasetContract(columns=[
    ColumnSpec("transfer_type", "STRING"),
    ColumnSpec("posting_count", "INTEGER"),
])


# L2.3 — Rails declared in L2 with zero postings in the window.
EXC_DEAD_RAILS_CONTRACT = DatasetContract(columns=[
    ColumnSpec("rail_name", "STRING"),
    ColumnSpec("transfer_type", "STRING"),
    ColumnSpec("leg_shape", "STRING"),
])


# L2.4 — Aggregating-rail bundles_activity targets with zero matching
# activity in the window. Bundles_activity refs are Identifiers that
# the SPEC says match either rail_name OR transfer_type — the SQL
# checks both attributions.
EXC_DEAD_BUNDLES_ACTIVITY_CONTRACT = DatasetContract(columns=[
    ColumnSpec("aggregating_rail", "STRING"),
    ColumnSpec("bundle_target", "STRING"),
])


# L2.5 — Rail.metadata_keys declared in L2 that no posting carries a
# non-null value for in the window. Each row is one (rail_name,
# metadata_key) pair the L2 declared but the runtime never populated.
EXC_DEAD_METADATA_CONTRACT = DatasetContract(columns=[
    ColumnSpec("rail_name", "STRING"),
    ColumnSpec("metadata_key", "STRING"),
])


# L2.6 — LimitSchedule (parent_role, transfer_type) cells with zero
# outbound debit flow in the window. Means the cap is effectively dead
# — either nobody routes that role/type combination, or the L2 declared
# a cap nobody enforces against.
EXC_DEAD_LIMIT_SCHEDULES_CONTRACT = DatasetContract(columns=[
    ColumnSpec("parent_role", "STRING"),
    ColumnSpec("transfer_type", "STRING"),
    ColumnSpec("cap", "DECIMAL"),
])


# Unified L2 Exceptions (M.3.10l) — UNION ALL across all 6 L2 hygiene
# checks with a shared shape so a single KPI + bar chart + detail
# table can present the whole L2-hygiene picture in one place.
# Mirrors the L1 dashboard's `_todays_exceptions` pattern (one row =
# one violation; check_type is the discriminator).
#
# - check_type: which L2 hygiene check produced the row.
# - entity_a / entity_b: the primary and secondary subject of the
#   violation (e.g., parent rail + child rail for Chain Orphans;
#   rail_name + metadata_key for Dead Metadata; transfer_type alone
#   for Unmatched Transfer Type with entity_b NULL).
# - detail: optional extra context (leg_shape, cap, etc.) — STRING
#   regardless of source type so the unified projection works.
# - magnitude: "how bad is it", used for the bar chart's count
#   weighting + the table's sort order. Per check:
#     * Chain Orphans → orphan_count (parent firings without a child)
#     * Unmatched Transfer Type → posting_count (count of leaking legs)
#     * Dead Rails / Dead Bundles / Dead Metadata / Dead Limit
#       Schedules → 1 (each row IS one dead declaration)
UNIFIED_L2_EXCEPTIONS_CONTRACT = DatasetContract(columns=[
    ColumnSpec("check_type", "STRING"),
    # entity_a holds the L2-declared name relevant to each row's
    # check_type — rail/template name for 4 of 6 checks, transfer_type
    # for L2.2, parent_role for L2.6. The shape lets the L2 Exceptions
    # table's right-click drills wire entity_a → Rails sheet / Chains
    # sheet filter parameters; the destination filters return zero
    # rows for the 2 check_types whose entity_a isn't actually a rail
    # or template name (transparent "this drill doesn't apply" UX).
    ColumnSpec("entity_a", "STRING", shape=ColumnShape.L2_DECLARED_NAME),
    ColumnSpec("entity_b", "STRING"),
    ColumnSpec("detail", "STRING"),
    ColumnSpec("magnitude", "INTEGER"),
])


# -- Rails tab (M.3.10c) — postings explorer + cascade source ---------------

# Per-leg view from <prefix>_current_transactions, parameterized on
# pKey + pValues so the metadata cascade filter applies via QS
# CustomSql substitution. The Rails sheet's transactions Table reads
# directly from this dataset.
POSTINGS_CONTRACT = DatasetContract(columns=[
    ColumnSpec("id", "STRING"),
    ColumnSpec("transfer_id", "STRING"),
    ColumnSpec("transfer_parent_id", "STRING"),
    # rail_name is a drill destination for the L2 Exceptions table's
    # "View in Rails" right-click — see UNIFIED_L2_EXCEPTIONS_CONTRACT.
    ColumnSpec("rail_name", "STRING", shape=ColumnShape.L2_DECLARED_NAME),
    ColumnSpec("transfer_type", "STRING"),
    ColumnSpec("account_id", "STRING"),
    ColumnSpec("account_name", "STRING"),
    ColumnSpec("account_role", "STRING"),
    ColumnSpec("account_scope", "STRING"),
    ColumnSpec("posting", "DATETIME"),
    ColumnSpec("amount_money", "DECIMAL"),
    ColumnSpec("amount_direction", "STRING"),
    ColumnSpec("status", "STRING"),
    ColumnSpec("bundle_id", "STRING"),
    ColumnSpec("bundle_status", "STRING"),  # 'Bundled' / 'Unbundled' calc
    ColumnSpec("origin", "STRING"),
])


# Long-form (metadata_key, metadata_value) for the cascade. QS's
# CascadingControlConfiguration uses the metadata_key column to
# filter rows by the Key dropdown's selection — picking 'customer_id'
# in Key narrows the dataset to rows WHERE metadata_key='customer_id',
# then DISTINCT metadata_value populates the Value dropdown.
# (Earlier single-column shape with dataset-parameter substitution
# DIDN'T work — QS's cascade is a column-match filter, not a
# parameter-driven re-query.)
META_VALUES_CONTRACT = DatasetContract(columns=[
    ColumnSpec("metadata_key", "STRING"),
    ColumnSpec("metadata_value", "STRING"),
])


# -- Builders ----------------------------------------------------------------


def build_all_l2_flow_tracing_datasets(
    cfg: Config, l2_instance: L2Instance,
) -> list[Dataset]:
    """Return every Dataset the L2 Flow Tracing app needs.

    Mirrors `build_all_l1_dashboard_datasets`: derives an L2-aware
    ``cfg`` (so dataset IDs carry the L2 instance prefix as their
    middle segment per M.2d.3) when the caller hasn't pre-stamped it.
    Idempotent — re-deriving an already-L2-aware cfg is a no-op.

    M.3.6 ships Chains; M.3.7 adds the 6 L2 Exceptions sections;
    M.3.10c adds the postings explorer + meta-values cascade source
    for the Rails tab (replacing M.3.5's declared-rails table — moves
    to a future Docs tab — and M.3.8's 28 per-key metadata dropdowns
    — replaced by the cascade); M.3.10d swaps the chains aggregate
    dataset for a per-parent-firing explorer (chain-instances);
    M.3.10f adds the Transfer Templates sheet with tt-instances (per
    shared Transfer) + tt-legs (per leg, backing the multi-leg flow
    Sankey); M.3.10l replaces the 6 separate L2 exception datasets
    with one unified UNION-ALL dataset (mirrors L1's todays-exceptions
    pattern — single KPI + bar chart + detail table).
    """
    if cfg.l2_instance_prefix is None:
        cfg = cfg.with_l2_instance_prefix(str(l2_instance.instance))
    return [
        build_postings_dataset(cfg, l2_instance),
        build_meta_values_dataset(cfg, l2_instance),
        build_chain_instances_dataset(cfg, l2_instance),
        build_tt_instances_dataset(cfg, l2_instance),
        build_tt_legs_dataset(cfg, l2_instance),
        build_unified_l2_exceptions_dataset(cfg, l2_instance),
        # M.4.4.5 — App Info ("i") sheet datasets, ALWAYS LAST.
        # M.4.4.7 — per-app segment so deploy <single-app> doesn't
        # delete-then-create another app's App Info dataset.
        build_liveness_dataset(cfg, app_segment="l2ft"),
        build_matview_status_dataset(
            cfg, app_segment="l2ft",
            view_specs=l2ft_matview_specs(l2_instance),
        ),
    ]


def declared_metadata_keys(l2_instance: L2Instance) -> list[str]:
    """Sorted list of distinct metadata keys declared across every
    rail in the L2 instance. Drives both the dropdown-source dataset
    list and the analysis-level parameter list — single source of
    truth for "what metadata keys does this L2 expose?".
    """
    keys: set[str] = set()
    for r in l2_instance.rails:
        for k in r.metadata_keys:
            keys.add(str(k))
    return sorted(keys)


def declared_rail_names(l2_instance: L2Instance) -> list[str]:
    """Sorted list of declared Rail names. Drives the Rail dropdown's
    selectable values on the L2FT Rails sheet (X.1.b).

    Pre-X.1.b the Rail dropdown carried no selectable_values and
    relied on QS's auto-distinct fetch (``tenK-sample-values-V2``
    endpoint), which 404s on cold per-CI-run dashboards. Static
    enumeration from the L2 sidesteps the lazy fetch entirely —
    rail names are bounded + known at deploy time.
    """
    return sorted(str(r.name) for r in l2_instance.rails)


# X.1.b — bounded enums for the L2FT Rails sheet's Status + Bundle
# dropdowns. Hardcoded because:
# - ``status`` values are part of the L1 schema's ``CHECK``-style
#   constraint (only ``Pending`` / ``Posted`` / ``Failed`` are valid).
# - ``bundle_status`` is a calc field defined as
#   ``CASE WHEN bundle_id IS NULL THEN 'Unbundled' ELSE 'Bundled' END``
#   — exactly two values, ever.
# StaticValues sourcing eliminates the QS auto-distinct fetch path
# (the X.1.b ``tenK-sample-values-V2`` 404 source).
# X.1.i — `status` is open-set in the L1 schema (any string), but only
# `Pending` / `Posted` carry first-class meaning in this tool (drives
# Aging, Conservation, Completion checks). Every other raw status
# (Failed, Cancelled, Rejected, ...) projects to `Other` via a CASE in
# the L2FT postings dataset SQL so this static enum matches what the
# column actually produces and the dropdown e2e (which asserts every
# advertised value has rows) doesn't choke on a stale enum.
_TRANSACTION_STATUS_VALUES: tuple[str, ...] = ("Pending", "Posted", "Other")
_BUNDLE_STATUS_VALUES: tuple[str, ...] = ("Bundled", "Unbundled")
# X.1.g — chain + TT completion_status enums. Mirror the CASE branches
# in build_chain_instances_dataset / build_tt_instances_dataset so QS
# dropdown options match the projected column values exactly.
_CHAIN_COMPLETION_STATUS_VALUES: tuple[str, ...] = (
    "Completed", "Incomplete",
)
_TT_COMPLETION_STATUS_VALUES: tuple[str, ...] = (
    "Complete", "Imbalanced", "Orphaned",
)


def transaction_status_values() -> list[str]:
    """Bounded enum of transaction ``status`` values. Static dropdown
    source — see ``_TRANSACTION_STATUS_VALUES`` for rationale.
    """
    return list(_TRANSACTION_STATUS_VALUES)


def bundle_status_values() -> list[str]:
    """Bounded enum of ``bundle_status`` calc-field values. Static
    dropdown source — see ``_BUNDLE_STATUS_VALUES`` for rationale.
    """
    return list(_BUNDLE_STATUS_VALUES)


def chain_completion_status_values() -> list[str]:
    """Bounded enum of chain-instances ``completion_status`` values.
    Static dropdown source on the Chains sheet (X.1.g).
    """
    return list(_CHAIN_COMPLETION_STATUS_VALUES)


def tt_completion_status_values() -> list[str]:
    """Bounded enum of transfer-template ``completion_status`` values.
    Static dropdown source on the Transfer Templates sheet (X.1.g).
    """
    return list(_TT_COMPLETION_STATUS_VALUES)


def metadata_filter_clause(
    l2_instance: L2Instance, metadata_col: str, dialect: Dialect,
) -> str:
    """WHERE-fragment that filters by the metadata key/value cascade,
    portable across Postgres + Oracle + SQLite.

    The natural form ``JSON_VALUE(metadata, '$.' || <<$pKey>>) IN (...)``
    works on Postgres but fails on Oracle: Oracle's ``JSON_VALUE``
    requires the path argument to be a string literal at parse time
    (ORA-40597 — JSON path expression syntax error). The runtime
    concatenation ``'$.' || pKey`` is rejected even when the planner
    could constant-fold it.

    Workaround: emit one branch per declared metadata key with the
    JSON path as a literal, gated by ``<<$pKey>>`` matching that key.
    The L2 instance enumerates the keys at generate time, so the
    fan-out is bounded and static. Sentinel ``__ALL__`` short-circuits
    the cascade so a freshly-loaded dashboard renders all rows.

    ``metadata_col`` is the column the JSON_VALUE reads from
    (typically ``metadata`` or ``parent_metadata``). ``dialect``
    routes through ``json_value`` so SQLite gets ``json_extract``
    (the JSON1 equivalent) instead of the SQL/JSON-standard form.
    """
    keys = declared_metadata_keys(l2_instance)
    lines = [f"  <<$pKey>> = {_sql_str(META_KEY_ALL_SENTINEL)}"]
    for k in keys:
        path = f"$.{k}"
        lines.append(
            f"  OR (<<$pKey>> = {_sql_str(k)} "
            f"AND {json_value(metadata_col, _sql_str(path), dialect)} "
            f"IN (<<$pValues>>))"
        )
    return "\n".join(lines)


def declared_chain_parents(l2_instance: L2Instance) -> list[str]:
    """Sorted list of distinct ChainEntry parent names. Drives the
    Chain dropdown's selectable values on the Chains sheet (M.3.10d).
    """
    return sorted({str(c.parent) for c in l2_instance.chains})


def declared_template_names(l2_instance: L2Instance) -> list[str]:
    """Sorted list of declared TransferTemplate names. Drives the
    Template dropdown's selectable values on the Transfer Templates
    sheet (M.3.10f).
    """
    return sorted(str(t.name) for t in l2_instance.transfer_templates)


def build_postings_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """One row per leg from ``<prefix>_current_transactions``,
    parameterized so the Rails sheet's filters push down into SQL via
    QS ``<<$param>>`` substitution.

    Two parameter families, both server-side:

    - **Metadata cascade** — ``pKey`` (single) + ``pValues`` (single).
      ``pKey = '__ALL__'`` short-circuits the metadata WHERE to "no
      filter" (freshly-loaded dashboard renders every leg);
      ``pValues = '__placeholder__'`` matches no real value, so picking
      a Key without a Value goes empty (UX hint to pick both). Stays in
      the inner query's WHERE.
    - **Category pushdown (Y.2.c)** — ``pL2ftRail`` / ``pL2ftStatus`` /
      ``pL2ftBundle`` (all multi-valued). Defaults span every declared
      rail / the bounded status enum / both bundle states, so a
      freshly-loaded dashboard matches every row. Bridged from the
      Rails sheet's MULTI_SELECT dropdowns; emptying a dropdown reverts
      to the default (QS does not emit ``IN ()`` — verified Y.2.c.0).
      Pushed into the OUTER WHERE because ``status`` and
      ``bundle_status`` are CASE-aliases (not visible to a WHERE in the
      same SELECT) so the projection wraps in a subquery.

    Date range stays an analysis-level TimeRangeFilter (Y.2.f territory).
    """
    prefix = l2_instance.instance
    sql = (
        f"SELECT * FROM (\n"
        f"  SELECT\n"
        f"    id, transfer_id, transfer_parent_id, rail_name, transfer_type,\n"
        f"    account_id, account_name, account_role, account_scope,\n"
        f"    posting, amount_money, amount_direction,\n"
        # X.1.i — collapse open-set `status` into the bounded set the
        # tool reasons about. Pending / Posted carry first-class meaning
        # (drives Aging, Conservation, Completion checks); every other
        # raw status (Failed, Cancelled, Rejected, ...) projects to
        # 'Other' so the static dropdown enum matches what the column
        # produces and the analyst can still narrow to the unhealthy
        # tail without enumerating every possible terminal state.
        f"    CASE WHEN status IN ('Pending', 'Posted') THEN status "
        f"ELSE 'Other' END AS status,\n"
        f"    bundle_id,\n"
        f"    CASE WHEN bundle_id IS NULL THEN 'Unbundled' ELSE 'Bundled' END "
        f"AS bundle_status,\n"
        f"    origin\n"
        f"  FROM {prefix}_current_transactions\n"
        f"  WHERE\n"
        # The metadata cascade short-circuit: when pKey is the sentinel,
        # this sub-clause always evaluates true (no filtering); otherwise
        # the per-key branches compare the leg's metadata against the
        # picked values. See `metadata_filter_clause` for the
        # per-dialect-safe WHERE shape.
        f"{metadata_filter_clause(l2_instance, 'metadata', cfg.dialect)}"
        f") postings\n"
        # Y.2.c — rail / status / bundle pushed into SQL via multi-valued
        # dataset parameters. Defaults span all declared values so the
        # freshly-loaded dashboard matches every row; emptying a dropdown
        # reverts to the default (QS does not emit `IN ()`).
        f"WHERE rail_name IN (<<$pL2ftRail>>)\n"
        f"  AND status IN (<<$pL2ftStatus>>)\n"
        f"  AND bundle_status IN (<<$pL2ftBundle>>)\n"
    )
    return build_dataset(
        cfg, cfg.prefixed("l2ft-postings-dataset"),
        "L2FT Postings", "l2ft-postings",
        sql, POSTINGS_CONTRACT,
        visual_identifier=DS_POSTINGS,
        dataset_parameters=[
            DatasetParameter(StringDatasetParameter=StringDatasetParameter(
                Id=_DSP_ID_PKEY,
                Name="pKey",
                ValueType="SINGLE_VALUED",
                DefaultValues=StringDatasetParameterDefaultValues(
                    StaticValues=[META_KEY_ALL_SENTINEL],
                ),
            )),
            DatasetParameter(StringDatasetParameter=StringDatasetParameter(
                Id=_DSP_ID_PVALUES,
                Name="pValues",
                # Y.1.m: SINGLE_VALUED to match the analysis-level
                # parameter shape (text-field control). Was MULTI_VALUED
                # but the text-field control couldn't commit non-empty
                # values to multi-valued params — broke the cascade.
                ValueType="SINGLE_VALUED",
                DefaultValues=StringDatasetParameterDefaultValues(
                    StaticValues=[META_VALUE_PLACEHOLDER_SENTINEL],
                ),
            )),
            # Y.2.c — rail / status / bundle multi-valued pushdown.
            DatasetParameter(StringDatasetParameter=StringDatasetParameter(
                Id=_DSP_ID_PRAIL,
                Name="pL2ftRail",
                ValueType="MULTI_VALUED",
                DefaultValues=StringDatasetParameterDefaultValues(
                    StaticValues=declared_rail_names(l2_instance),
                ),
            )),
            DatasetParameter(StringDatasetParameter=StringDatasetParameter(
                Id=_DSP_ID_PSTATUS,
                Name="pL2ftStatus",
                ValueType="MULTI_VALUED",
                DefaultValues=StringDatasetParameterDefaultValues(
                    StaticValues=transaction_status_values(),
                ),
            )),
            DatasetParameter(StringDatasetParameter=StringDatasetParameter(
                Id=_DSP_ID_PBUNDLE,
                Name="pL2ftBundle",
                ValueType="MULTI_VALUED",
                DefaultValues=StringDatasetParameterDefaultValues(
                    StaticValues=bundle_status_values(),
                ),
            )),
        ],
    )


def build_meta_values_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """Long-form ``(metadata_key, metadata_value)`` for the cascade.

    Built as a UNION ALL across declared metadata keys, projecting one
    row per (transaction, key) combination where that key has a
    non-null value. The Value dropdown's ``LinkedValues`` sources
    from the ``metadata_value`` column; QS's
    ``CascadingControlConfiguration`` filters rows by the Key
    dropdown's selection matched against the ``metadata_key`` column
    — picking 'customer_id' narrows the dataset to that key's rows,
    then DISTINCT metadata_value populates the dropdown.

    No dataset parameters needed — the cascade is purely column-match
    on the analysis side. The earlier single-column +
    parameter-substituted shape didn't work because QS's cascade is
    a column-match filter, not a parameter-driven dataset re-query
    (M.3.10c finding).

    For an L2 instance with no declared metadata keys, the SELECT
    is replaced with `WHERE FALSE` so the dataset emits valid SQL
    that returns no rows.
    """
    prefix = l2_instance.instance
    keys = declared_metadata_keys(l2_instance)
    if not keys:
        nt = typed_null("varchar(4000)", cfg.dialect)
        df = dual_from(cfg.dialect)
        sql = (
            f"SELECT {nt} AS metadata_key, "
            f"{nt} AS metadata_value"
            f"{df}\n"
            "WHERE 1=0"
        )
    else:
        # One SELECT per declared key. Each projects (key, value)
        # for transactions where that key has a non-null metadata
        # value. UNION ALL stitches them; DISTINCT happens at the
        # visual level via the dropdown's distinct-values semantics.
        branches = []
        for k in keys:
            json_path = f"$.{k}"
            jv = json_value("metadata", _sql_str(json_path), cfg.dialect)
            branches.append(
                f"  SELECT {_sql_str(k)} AS metadata_key, "
                f"{jv} AS metadata_value\n"
                f"  FROM {prefix}_current_transactions\n"
                f"  WHERE metadata IS NOT NULL\n"
                f"    AND {jv} IS NOT NULL"
            )
        sql = "\n  UNION ALL\n".join(branches)
    return build_dataset(
        cfg, cfg.prefixed("l2ft-meta-values-dataset"),
        "L2FT Metadata Values", "l2ft-meta-values",
        sql, META_VALUES_CONTRACT,
        visual_identifier=DS_META_VALUES,
        # No dataset parameters — the cascade is column-match, not
        # parameter-driven SQL substitution.
    )


def build_chains_dataset(cfg: Config, l2_instance: L2Instance) -> DataSet:
    """One row per declared ChainEntry — the L2's parent→child topology
    joined to runtime parent firing counts + matched-child counts.

    A row IS one Sankey edge in the Chains visual. Counts come from
    ``<prefix>_current_transactions`` matched on the parent's name
    (which can be a Rail's ``rail_name`` OR a TransferTemplate's
    ``template_name`` — every leg row carries both, with template_name
    taking precedence when a rail is part of a template). Child
    matches require ``transfer_parent_id`` to point at one of the
    parent's transfer_ids — that's the runtime "did this child fire
    in response to this parent" relation.

    Orphan rate = (parent_firings without a matched child) /
    parent_firings. A required Chain entry with non-zero orphan rate
    is the seed for M.3.7's L2.1 'Chain orphans' exception.

    Note on portability: uses correlated subqueries instead of
    ARRAY_AGG (PG-only) to keep the SQL portable. The chains table is
    small (typically tens of entries), so the cost is bounded.
    """
    prefix = l2_instance.instance
    declared = _declared_chains_cte(l2_instance, cfg.dialect)
    sql = (
        f"WITH declared AS (\n{declared}\n),\n"
        f"edge_runtime AS (\n"
        f"  SELECT\n"
        f"    d.parent_name,\n"
        f"    d.child_name,\n"
        f"    d.required,\n"
        f"    d.xor_group,\n"
        f"    d.source_node,\n"
        f"    d.target_node,\n"
        f"    COALESCE((\n"
        f"      SELECT COUNT(DISTINCT t.transfer_id)\n"
        f"      FROM {prefix}_current_transactions t\n"
        f"      WHERE COALESCE(t.template_name, t.rail_name) = d.parent_name\n"
        f"    ), 0) AS parent_firing_count,\n"
        f"    COALESCE((\n"
        f"      SELECT COUNT(DISTINCT c.transfer_id)\n"
        f"      FROM {prefix}_current_transactions c\n"
        f"      WHERE COALESCE(c.template_name, c.rail_name) = d.child_name\n"
        f"        AND c.transfer_parent_id IN (\n"
        f"          SELECT t2.transfer_id\n"
        f"          FROM {prefix}_current_transactions t2\n"
        f"          WHERE COALESCE(t2.template_name, t2.rail_name) "
        f"= d.parent_name\n"
        f"        )\n"
        f"    ), 0) AS child_firing_count\n"
        f"  FROM declared d\n"
        f")\n"
        f"SELECT\n"
        f"  e.parent_name,\n"
        f"  e.child_name,\n"
        f"  e.required,\n"
        f"  e.xor_group,\n"
        f"  e.source_node,\n"
        f"  e.target_node,\n"
        f"  e.parent_firing_count,\n"
        f"  e.child_firing_count,\n"
        # GREATEST clamps at 0 — child can fire more than parent in some
        # patterns (e.g., one parent triggers many children); negative
        # orphans don't read intuitively in the visual.
        f"  {greatest('e.parent_firing_count - e.child_firing_count', '0', dialect=cfg.dialect)} "
        f"AS orphan_count,\n"
        f"  CASE\n"
        f"    WHEN e.parent_firing_count > 0\n"
        f"      THEN CAST(\n"
        f"        {greatest('e.parent_firing_count - e.child_firing_count', '0', dialect=cfg.dialect)} "
        f"AS DECIMAL(20,4)\n"
        f"      ) / e.parent_firing_count\n"
        f"    ELSE 0\n"
        f"  END AS orphan_rate\n"
        f"FROM edge_runtime e\n"
        f"ORDER BY e.parent_name, e.child_name"
    )
    return build_dataset(
        cfg, cfg.prefixed("l2ft-chains-dataset"),
        "L2FT Chains", "l2ft-chains",
        sql, CHAINS_CONTRACT,
        visual_identifier=DS_CHAINS,
    )


def build_chain_instances_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """One row per parent transfer firing of a declared chain parent
    (M.3.10d, completion_status extended in M.3.10i for XOR groups).
    Backs the Chains sheet's per-instance explorer.

    Columns:

    - ``parent_chain_name`` — the L2-declared parent rail / template
      name. Drives the Chain dropdown's selectable values.
    - ``parent_transfer_id`` — DISTINCT transfer_id of the parent
      firing. Multiple legs of one transfer collapse to one row via
      GROUP BY.
    - ``completion_status`` — computed inline; one of:

      * ``'Completed'`` — every Required child fired AND every XOR
        group has exactly one member fired.
      * ``'Incomplete'`` — at least one Required child missing OR
        any XOR group has 0 fired (orphan) OR > 1 fired (violation).

      The pre-X.1.j third branch ``'No Required Children'`` is gone —
      validator rule C5 rejects all-optional / no-XOR chains at L2
      load, so the SQL never produces that case.

    - ``parent_metadata`` is read in the WHERE only — kept off the
      contract so users don't see raw JSON. ``pKey`` / ``pValues``
      substitute into a JSONPath ``IN (...)`` predicate same as the
      postings dataset; the ``__ALL__`` sentinel short-circuits to
      "no metadata filter".

    SQL portability: correlated subqueries (no ``ARRAY_AGG``); no
    JSONB; ``MAX`` aggregates over varchar status / metadata which
    isn't perfect but the parent transfer's legs share these values
    in practice. The chains table is bounded by L2 declarations
    (typically tens of entries) so the cost stays predictable.
    """
    prefix = l2_instance.instance
    declared = _declared_chains_cte(l2_instance, cfg.dialect)
    sql = (
        f"WITH declared AS (\n{declared}\n),\n"
        f"parent_chains AS (\n"
        f"  SELECT\n"
        f"    parent_name,\n"
        f"    SUM(CASE WHEN required = 'Required' THEN 1 ELSE 0 END) "
        f"AS required_total,\n"
        f"    COUNT(DISTINCT CASE WHEN xor_group IS NOT NULL "
        f"THEN xor_group END) AS xor_group_count\n"
        f"  FROM declared\n"
        f"  GROUP BY parent_name\n"
        f"),\n"
        f"parent_firings AS (\n"
        f"  SELECT\n"
        f"    pc.parent_name AS parent_chain_name,\n"
        f"    pc.required_total,\n"
        f"    pc.xor_group_count,\n"
        f"    t.transfer_id AS parent_transfer_id,\n"
        f"    MIN(t.posting) AS parent_posting,\n"
        f"    MAX(t.status) AS parent_status,\n"
        f"    MAX(t.amount_money) AS parent_amount_money,\n"
        f"    MAX(t.metadata) AS parent_metadata\n"
        f"  FROM parent_chains pc\n"
        f"  JOIN {prefix}_current_transactions t\n"
        f"    ON COALESCE(t.template_name, t.rail_name) = pc.parent_name\n"
        f"  GROUP BY pc.parent_name, pc.required_total, pc.xor_group_count, "
        f"t.transfer_id\n"
        f"),\n"
        f"firing_completion AS (\n"
        f"  SELECT\n"
        f"    pf.parent_chain_name,\n"
        f"    pf.parent_transfer_id,\n"
        f"    pf.parent_posting,\n"
        f"    pf.parent_status,\n"
        f"    pf.parent_amount_money,\n"
        f"    pf.required_total,\n"
        f"    pf.xor_group_count,\n"
        f"    pf.parent_metadata,\n"
        f"    (\n"
        f"      SELECT COUNT(DISTINCT d.child_name)\n"
        f"      FROM declared d\n"
        f"      WHERE d.parent_name = pf.parent_chain_name\n"
        f"        AND d.required = 'Required'\n"
        f"        AND EXISTS (\n"
        f"          SELECT 1 FROM {prefix}_current_transactions c\n"
        f"          WHERE COALESCE(c.template_name, c.rail_name) "
        f"= d.child_name\n"
        f"            AND c.transfer_parent_id = pf.parent_transfer_id\n"
        f"        )\n"
        f"    ) AS required_fired,\n"
        # XOR violation count = number of declared XOR groups under this
        # parent where fired-children count != 1. SPEC: "exactly one of
        # them SHOULD fire per parent instance"; 0 fired = orphan, > 1
        # fired = violation. Both flagged as Incomplete.
        f"    (\n"
        f"      SELECT COUNT(*)\n"
        f"      FROM (\n"
        f"        SELECT d.xor_group,\n"
        f"          SUM(CASE WHEN EXISTS (\n"
        f"            SELECT 1 FROM {prefix}_current_transactions c\n"
        f"            WHERE COALESCE(c.template_name, c.rail_name) "
        f"= d.child_name\n"
        f"              AND c.transfer_parent_id = pf.parent_transfer_id\n"
        f"          ) THEN 1 ELSE 0 END) AS fired_in_group\n"
        f"        FROM declared d\n"
        f"        WHERE d.parent_name = pf.parent_chain_name\n"
        f"          AND d.xor_group IS NOT NULL\n"
        f"        GROUP BY d.xor_group\n"
        f"      ) g\n"
        f"      WHERE g.fired_in_group <> 1\n"
        f"    ) AS xor_violations\n"
        f"  FROM parent_firings pf\n"
        f")\n"
        # Y.2.d — wrap the projection in a subquery so the CASE-aliased
        # `completion_status` is visible to the outer WHERE; `parent_chain_name`
        # joins it there. Metadata cascade on `parent_metadata` stays inner
        # (the column isn't projected, so the WHERE that reads it must be).
        f"SELECT * FROM (\n"
        f"  SELECT\n"
        f"    parent_chain_name,\n"
        f"    parent_transfer_id,\n"
        f"    parent_posting,\n"
        f"    parent_status,\n"
        f"    parent_amount_money,\n"
        f"    required_total,\n"
        f"    required_fired,\n"
        f"    CASE\n"
        f"      WHEN required_fired >= required_total "
        f"AND xor_violations = 0 THEN 'Completed'\n"
        f"      ELSE 'Incomplete'\n"
        f"    END AS completion_status\n"
        f"  FROM firing_completion\n"
        f"  WHERE\n"
        f"{metadata_filter_clause(l2_instance, 'parent_metadata', cfg.dialect)}\n"
        f") chain_instances\n"
        # Y.2.d — chain / completion pushed into SQL via multi-valued dataset
        # params. Defaults span all declared values so the freshly-loaded
        # dashboard matches every row; emptying a dropdown reverts to the
        # default (QS does not emit `IN ()`).
        f"WHERE parent_chain_name IN (<<$pL2ftChainsChain>>)\n"
        f"  AND completion_status IN (<<$pL2ftChainsCompletion>>)\n"
        f"ORDER BY parent_posting DESC"
    )
    return build_dataset(
        cfg, cfg.prefixed("l2ft-chain-instances-dataset"),
        "L2FT Chain Instances", "l2ft-chain-instances",
        sql, CHAIN_INSTANCES_CONTRACT,
        visual_identifier=DS_CHAIN_INSTANCES,
        dataset_parameters=[
            DatasetParameter(StringDatasetParameter=StringDatasetParameter(
                Id=_DSP_ID_PKEY,
                Name="pKey",
                ValueType="SINGLE_VALUED",
                DefaultValues=StringDatasetParameterDefaultValues(
                    StaticValues=[META_KEY_ALL_SENTINEL],
                ),
            )),
            DatasetParameter(StringDatasetParameter=StringDatasetParameter(
                Id=_DSP_ID_PVALUES,
                Name="pValues",
                # Y.1.m: SINGLE_VALUED to match the analysis-level
                # parameter shape (text-field control). Was MULTI_VALUED
                # but the text-field control couldn't commit non-empty
                # values to multi-valued params — broke the cascade.
                ValueType="SINGLE_VALUED",
                DefaultValues=StringDatasetParameterDefaultValues(
                    StaticValues=[META_VALUE_PLACEHOLDER_SENTINEL],
                ),
            )),
            # Y.2.d — chain / completion multi-valued pushdown.
            DatasetParameter(StringDatasetParameter=StringDatasetParameter(
                Id=_DSP_ID_PCHAINSCHAIN,
                Name="pL2ftChainsChain",
                ValueType="MULTI_VALUED",
                DefaultValues=StringDatasetParameterDefaultValues(
                    # An instance with no Chains declared → empty list →
                    # `IN ()` (invalid). Sentinel keeps the SQL valid /
                    # zero-row. See PUSHDOWN_NO_MATCH_SENTINEL.
                    StaticValues=(
                        declared_chain_parents(l2_instance)
                        or [PUSHDOWN_NO_MATCH_SENTINEL]
                    ),
                ),
            )),
            DatasetParameter(StringDatasetParameter=StringDatasetParameter(
                Id=_DSP_ID_PCHAINSCOMPLETION,
                Name="pL2ftChainsCompletion",
                ValueType="MULTI_VALUED",
                DefaultValues=StringDatasetParameterDefaultValues(
                    StaticValues=chain_completion_status_values(),
                ),
            )),
        ],
    )


# -- L2 Exception builders (M.3.7) -------------------------------------------


def build_exc_chain_orphans_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """L2.1 — Required Chain entries where parent fired but no
    matched child fired in the window.

    Reuses the chains dataset's CTE shape (declared edges + edge
    runtime) and filters to ``required = 'Required' AND orphan_count
    > 0``. XOR-group multi/none violations are deferred to a follow-on
    substep — a precise XOR check needs per-Transfer-id grouping that
    the simpler aggregate doesn't capture.
    """
    prefix = l2_instance.instance
    declared = _declared_chains_cte(l2_instance, cfg.dialect)
    sql = (
        f"WITH declared AS (\n{declared}\n),\n"
        f"edge_runtime AS (\n"
        f"  SELECT\n"
        f"    d.parent_name,\n"
        f"    d.child_name,\n"
        f"    d.required,\n"
        f"    COALESCE((\n"
        f"      SELECT COUNT(DISTINCT t.transfer_id)\n"
        f"      FROM {prefix}_current_transactions t\n"
        f"      WHERE COALESCE(t.template_name, t.rail_name) = d.parent_name\n"
        f"    ), 0) AS parent_firing_count,\n"
        f"    COALESCE((\n"
        f"      SELECT COUNT(DISTINCT c.transfer_id)\n"
        f"      FROM {prefix}_current_transactions c\n"
        f"      WHERE COALESCE(c.template_name, c.rail_name) = d.child_name\n"
        f"        AND c.transfer_parent_id IN (\n"
        f"          SELECT t2.transfer_id\n"
        f"          FROM {prefix}_current_transactions t2\n"
        f"          WHERE COALESCE(t2.template_name, t2.rail_name) "
        f"= d.parent_name\n"
        f"        )\n"
        f"    ), 0) AS child_firing_count\n"
        f"  FROM declared d\n"
        f")\n"
        f"SELECT\n"
        f"  e.parent_name,\n"
        f"  e.child_name,\n"
        f"  e.parent_firing_count,\n"
        f"  e.child_firing_count,\n"
        f"  {greatest('e.parent_firing_count - e.child_firing_count', '0', dialect=cfg.dialect)} "
        f"AS orphan_count\n"
        f"FROM edge_runtime e\n"
        f"WHERE e.required = 'Required'\n"
        f"  AND e.parent_firing_count > e.child_firing_count\n"
        f"ORDER BY orphan_count DESC, e.parent_name, e.child_name"
    )
    return build_dataset(
        cfg, cfg.prefixed("l2ft-exc-chain-orphans-dataset"),
        "L2 Exc — Chain Orphans", "l2ft-exc-chain-orphans",
        sql, EXC_CHAIN_ORPHANS_CONTRACT,
        visual_identifier=DS_EXC_CHAIN_ORPHANS,
    )


def build_exc_unmatched_transfer_type_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """L2.2 — Posted Transactions whose ``transfer_type`` doesn't
    match any declared ``Rail.transfer_type``.

    The runtime version of M.2d.1's deferred validator check
    ('every Transfer MUST match a Rail'). LEFT JOIN to a CTE of
    declared types + filter to NULL surfaces the unmatched rows.
    Output is per-transfer-type with a count of postings carrying
    that type — the table reveals what's leaking past the L2's rails.
    """
    prefix = l2_instance.instance
    declared = _declared_transfer_types_cte(l2_instance, cfg.dialect)
    sql = (
        f"WITH declared_types AS (\n{declared}\n)\n"
        f"SELECT\n"
        f"  t.transfer_type,\n"
        f"  COUNT(*) AS posting_count\n"
        f"FROM {prefix}_current_transactions t\n"
        f"LEFT JOIN declared_types d ON d.transfer_type = t.transfer_type\n"
        f"WHERE d.transfer_type IS NULL\n"
        f"GROUP BY t.transfer_type\n"
        f"ORDER BY posting_count DESC, t.transfer_type"
    )
    return build_dataset(
        cfg, cfg.prefixed("l2ft-exc-unmatched-transfer-type-dataset"),
        "L2 Exc — Unmatched Transfer Type",
        "l2ft-exc-unmatched-transfer-type",
        sql, EXC_UNMATCHED_TRANSFER_TYPE_CONTRACT,
        visual_identifier=DS_EXC_UNMATCHED_TRANSFER_TYPE,
    )


def build_exc_dead_rails_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """L2.3 — Rails declared in L2 with zero postings in the window.

    Same shape as the Rails dataset but pre-filtered to
    ``COALESCE(r.total_postings, 0) = 0``. The KPI shows the count;
    the detail table lists the dead rails so the integrator can
    decide whether to retire the declaration or fix the ETL.
    """
    prefix = l2_instance.instance
    declared = _declared_rails_cte(l2_instance, cfg.dialect)
    sql = (
        f"WITH declared AS (\n{declared}\n),\n"
        f"runtime AS (\n"
        f"  SELECT rail_name, COUNT(*) AS total_postings\n"
        f"  FROM {prefix}_current_transactions\n"
        f"  GROUP BY rail_name\n"
        f")\n"
        f"SELECT\n"
        f"  d.rail_name,\n"
        f"  d.transfer_type,\n"
        f"  d.leg_shape\n"
        f"FROM declared d\n"
        f"LEFT JOIN runtime r ON r.rail_name = d.rail_name\n"
        f"WHERE COALESCE(r.total_postings, 0) = 0\n"
        f"ORDER BY d.rail_name"
    )
    return build_dataset(
        cfg, cfg.prefixed("l2ft-exc-dead-rails-dataset"),
        "L2 Exc — Dead Rails", "l2ft-exc-dead-rails",
        sql, EXC_DEAD_RAILS_CONTRACT,
        visual_identifier=DS_EXC_DEAD_RAILS,
    )


def build_exc_dead_bundles_activity_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """L2.4 — Aggregating-rail bundles_activity targets that no
    posting matched (by either ``rail_name`` OR ``transfer_type``)
    in the window.

    Per SPEC: a bundles_activity ref MAY name either a rail or a
    transfer_type; the SQL checks both attributions to avoid
    false positives. Each row is one (aggregating_rail, target)
    pair the L2 declared but the runtime never realized.
    """
    prefix = l2_instance.instance
    declared = _declared_bundles_activity_cte(l2_instance, cfg.dialect)
    sql = (
        f"WITH declared_bundles AS (\n{declared}\n)\n"
        f"SELECT\n"
        f"  db.aggregating_rail,\n"
        f"  db.bundle_target\n"
        f"FROM declared_bundles db\n"
        f"WHERE NOT EXISTS (\n"
        f"  SELECT 1\n"
        f"  FROM {prefix}_current_transactions t\n"
        f"  WHERE t.rail_name = db.bundle_target\n"
        f"     OR t.transfer_type = db.bundle_target\n"
        f")\n"
        f"ORDER BY db.aggregating_rail, db.bundle_target"
    )
    return build_dataset(
        cfg, cfg.prefixed("l2ft-exc-dead-bundles-activity-dataset"),
        "L2 Exc — Dead Bundles Activity",
        "l2ft-exc-dead-bundles-activity",
        sql, EXC_DEAD_BUNDLES_ACTIVITY_CONTRACT,
        visual_identifier=DS_EXC_DEAD_BUNDLES_ACTIVITY,
    )


def build_exc_dead_metadata_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """L2.5 — Rail.metadata_keys declared in L2 that no posting
    carries a non-null value for in the window.

    Each (rail, metadata_key) pair gets its own SQL fragment in the
    UNION ALL. Static JSON paths sidestep PostgreSQL's reluctance to
    accept dynamic JSONPath arguments to ``JSON_VALUE`` — keeps the
    SQL portable per the project's no-JSONB constraint.
    """
    prefix = l2_instance.instance
    fragments = _dead_metadata_check_fragments(l2_instance, prefix, cfg.dialect)
    if not fragments:
        # No rails declare metadata_keys — empty result, valid SQL.
        nt = typed_null("varchar(4000)", cfg.dialect)
        df = dual_from(cfg.dialect)
        sql = (
            f"SELECT {nt} AS rail_name, {nt} AS metadata_key{df} "
            "WHERE 1=0"
        )
    else:
        sql = "\n  UNION ALL\n".join(fragments) + "\n"
        sql = sql + "ORDER BY rail_name, metadata_key"
    return build_dataset(
        cfg, cfg.prefixed("l2ft-exc-dead-metadata-dataset"),
        "L2 Exc — Dead Metadata Declarations",
        "l2ft-exc-dead-metadata",
        sql, EXC_DEAD_METADATA_CONTRACT,
        visual_identifier=DS_EXC_DEAD_METADATA,
    )


def build_exc_dead_limit_schedules_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """L2.6 — LimitSchedule (parent_role, transfer_type) cells with
    zero outbound debit flow in the window.

    Means the cap is effectively dead — either nobody routes that
    role/type combination, or the L2 declared a cap nobody enforces
    against. NOT EXISTS over the prefixed transactions matview keeps
    the query bounded by the (small) limit-schedule count.
    """
    prefix = l2_instance.instance
    declared = _declared_limit_schedules_cte(l2_instance, cfg.dialect)
    sql = (
        f"WITH declared_limits AS (\n{declared}\n)\n"
        f"SELECT\n"
        f"  dl.parent_role,\n"
        f"  dl.transfer_type,\n"
        f"  dl.cap\n"
        f"FROM declared_limits dl\n"
        f"WHERE NOT EXISTS (\n"
        f"  SELECT 1\n"
        f"  FROM {prefix}_current_transactions t\n"
        f"  WHERE t.account_parent_role = dl.parent_role\n"
        f"    AND t.transfer_type = dl.transfer_type\n"
        f"    AND t.amount_direction = 'Debit'\n"
        f")\n"
        f"ORDER BY dl.parent_role, dl.transfer_type"
    )
    return build_dataset(
        cfg, cfg.prefixed("l2ft-exc-dead-limit-schedules-dataset"),
        "L2 Exc — Dead Limit Schedules",
        "l2ft-exc-dead-limit-schedules",
        sql, EXC_DEAD_LIMIT_SCHEDULES_CONTRACT,
        visual_identifier=DS_EXC_DEAD_LIMIT_SCHEDULES,
    )


# -- Unified L2 Exceptions (M.3.10l) -----------------------------------------


def build_unified_l2_exceptions_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """UNION ALL across the 6 L2 hygiene checks into one row-per-
    violation dataset (M.3.10l).

    Mirrors L1's `todays_exceptions` pattern: one row = one violation;
    `check_type` is the discriminator; `magnitude` is the
    "how-bad-is-it" metric used for sort + bar weighting. Each branch
    inlines its own CTEs as a subquery so the outer SELECT can do
    consistent typing across branches without colliding CTE names.

    Each branch is functionally equivalent to one of the 6 retired
    `build_exc_*` queries, just projected to the unified shape via
    CASTs + literal `check_type` labels.
    """
    prefix = l2_instance.instance
    declared_chains = _declared_chains_cte(l2_instance, cfg.dialect)
    declared_types = _declared_transfer_types_cte(l2_instance, cfg.dialect)
    declared_rails = _declared_rails_cte(l2_instance, cfg.dialect)
    declared_bundles = _declared_bundles_activity_cte(l2_instance, cfg.dialect)
    declared_limits = _declared_limit_schedules_cte(l2_instance, cfg.dialect)
    dead_metadata_fragments = _dead_metadata_check_fragments(
        l2_instance, prefix, cfg.dialect,
    )
    if dead_metadata_fragments:
        dead_metadata_inner = "\n  UNION ALL\n".join(dead_metadata_fragments)
    else:
        nt = typed_null("varchar(4000)", cfg.dialect)
        df = dual_from(cfg.dialect)
        dead_metadata_inner = (
            f"  SELECT {nt} AS rail_name, "
            f"{nt} AS metadata_key{df} WHERE 1=0"
        )
    sql = (
        # Branch 1: Chain Orphans
        f"SELECT\n"
        f"  CAST('Chain Orphans' AS VARCHAR(50)) AS check_type,\n"
        f"  parent_name AS entity_a,\n"
        f"  child_name AS entity_b,\n"
        f"  CAST(NULL AS VARCHAR(255)) AS detail,\n"
        f"  CAST(orphan_count AS INTEGER) AS magnitude\n"
        f"FROM (\n"
        f"  WITH declared AS (\n{declared_chains}\n),\n"
        f"  edge_runtime AS (\n"
        f"    SELECT\n"
        f"      d.parent_name, d.child_name, d.required,\n"
        f"      COALESCE((\n"
        f"        SELECT COUNT(DISTINCT t.transfer_id)\n"
        f"        FROM {prefix}_current_transactions t\n"
        f"        WHERE COALESCE(t.template_name, t.rail_name) "
        f"= d.parent_name\n"
        f"      ), 0) AS parent_firing_count,\n"
        f"      COALESCE((\n"
        f"        SELECT COUNT(DISTINCT c.transfer_id)\n"
        f"        FROM {prefix}_current_transactions c\n"
        f"        WHERE COALESCE(c.template_name, c.rail_name) "
        f"= d.child_name\n"
        f"          AND c.transfer_parent_id IN (\n"
        f"            SELECT t2.transfer_id\n"
        f"            FROM {prefix}_current_transactions t2\n"
        f"            WHERE COALESCE(t2.template_name, t2.rail_name) "
        f"= d.parent_name\n"
        f"          )\n"
        f"      ), 0) AS child_firing_count\n"
        f"    FROM declared d\n"
        f"  )\n"
        f"  SELECT parent_name, child_name,\n"
        f"    {greatest('parent_firing_count - child_firing_count', '0', dialect=cfg.dialect)} "
        f"AS orphan_count\n"
        f"  FROM edge_runtime\n"
        f"  WHERE required = 'Required'\n"
        f"    AND parent_firing_count > child_firing_count\n"
        f") sub_chain_orphans\n"
        # Branch 2: Unmatched Transfer Type
        f"UNION ALL\n"
        f"SELECT\n"
        f"  CAST('Unmatched Transfer Type' AS VARCHAR(50)),\n"
        f"  CAST(transfer_type AS VARCHAR(255)),\n"
        f"  CAST(NULL AS VARCHAR(255)),\n"
        f"  CAST(NULL AS VARCHAR(255)),\n"
        f"  CAST(posting_count AS INTEGER)\n"
        f"FROM (\n"
        f"  WITH declared_types AS (\n{declared_types}\n)\n"
        f"  SELECT t.transfer_type, COUNT(*) AS posting_count\n"
        f"  FROM {prefix}_current_transactions t\n"
        f"  LEFT JOIN declared_types d "
        f"ON d.transfer_type = t.transfer_type\n"
        f"  WHERE d.transfer_type IS NULL\n"
        f"  GROUP BY t.transfer_type\n"
        f") sub_unmatched\n"
        # Branch 3: Dead Rails
        f"UNION ALL\n"
        f"SELECT\n"
        f"  CAST('Dead Rails' AS VARCHAR(50)),\n"
        f"  CAST(rail_name AS VARCHAR(255)),\n"
        f"  CAST(transfer_type AS VARCHAR(255)),\n"
        f"  CAST(leg_shape AS VARCHAR(255)),\n"
        f"  1\n"
        f"FROM (\n"
        f"  WITH declared AS (\n{declared_rails}\n),\n"
        f"  runtime AS (\n"
        f"    SELECT rail_name, COUNT(*) AS total_postings\n"
        f"    FROM {prefix}_current_transactions GROUP BY rail_name\n"
        f"  )\n"
        f"  SELECT d.rail_name, d.transfer_type, d.leg_shape\n"
        f"  FROM declared d\n"
        f"  LEFT JOIN runtime r ON r.rail_name = d.rail_name\n"
        f"  WHERE COALESCE(r.total_postings, 0) = 0\n"
        f") sub_dead_rails\n"
        # Branch 4: Dead Bundles Activity
        f"UNION ALL\n"
        f"SELECT\n"
        f"  CAST('Dead Bundles Activity' AS VARCHAR(50)),\n"
        f"  CAST(aggregating_rail AS VARCHAR(255)),\n"
        f"  CAST(bundle_target AS VARCHAR(255)),\n"
        f"  CAST(NULL AS VARCHAR(255)),\n"
        f"  1\n"
        f"FROM (\n"
        f"  WITH declared_bundles AS (\n{declared_bundles}\n)\n"
        f"  SELECT db.aggregating_rail, db.bundle_target\n"
        f"  FROM declared_bundles db\n"
        f"  WHERE NOT EXISTS (\n"
        f"    SELECT 1 FROM {prefix}_current_transactions t\n"
        f"    WHERE t.rail_name = db.bundle_target "
        f"OR t.transfer_type = db.bundle_target\n"
        f"  )\n"
        f") sub_dead_bundles\n"
        # Branch 5: Dead Metadata Declarations
        f"UNION ALL\n"
        f"SELECT\n"
        f"  CAST('Dead Metadata Declarations' AS VARCHAR(50)),\n"
        f"  CAST(rail_name AS VARCHAR(255)),\n"
        f"  CAST(metadata_key AS VARCHAR(255)),\n"
        f"  CAST(NULL AS VARCHAR(255)),\n"
        f"  1\n"
        f"FROM (\n{dead_metadata_inner}\n) sub_dead_metadata\n"
        # Branch 6: Dead Limit Schedules
        f"UNION ALL\n"
        f"SELECT\n"
        f"  CAST('Dead Limit Schedules' AS VARCHAR(50)),\n"
        f"  CAST(parent_role AS VARCHAR(255)),\n"
        f"  CAST(transfer_type AS VARCHAR(255)),\n"
        f"  CAST(cap AS VARCHAR(255)),\n"
        f"  1\n"
        f"FROM (\n"
        f"  WITH declared_limits AS (\n{declared_limits}\n)\n"
        f"  SELECT dl.parent_role, dl.transfer_type, dl.cap\n"
        f"  FROM declared_limits dl\n"
        f"  WHERE NOT EXISTS (\n"
        f"    SELECT 1 FROM {prefix}_current_transactions t\n"
        f"    WHERE t.account_parent_role = dl.parent_role\n"
        f"      AND t.transfer_type = dl.transfer_type\n"
        f"      AND t.amount_direction = 'Debit'\n"
        f"  )\n"
        f") sub_dead_limits\n"
        # Position-based — Oracle's ORDER BY after UNION ALL doesn't
        # recognize aliases when each branch carries WITH+CTE subqueries.
        # Columns: 1=check_type, 2=entity_a, 3=entity_b, 4=detail, 5=magnitude.
        f"ORDER BY 5 DESC, 1, 2, 3"
    )
    return build_dataset(
        cfg, cfg.prefixed("l2ft-unified-exceptions-dataset"),
        "L2 Unified Exceptions", "l2ft-unified-exceptions",
        sql, UNIFIED_L2_EXCEPTIONS_CONTRACT,
        visual_identifier=DS_UNIFIED_L2_EXCEPTIONS,
    )


# -- Internals ---------------------------------------------------------------


def _declared_rails_cte(l2_instance: L2Instance, dialect: Dialect) -> str:
    """Render the L2-declared rails as a UNION ALL of SELECT-literal rows.

    UNION ALL of single-row SELECTs is used instead of ``VALUES (...)``
    so each column gets a CAST (or naked literal) that's resolved per
    row, avoiding the "type of column N is text but row M is null"
    inference problem PostgreSQL's planner sometimes hits with VALUES
    when most rows have NULL for a column.

    ``dialect`` selects the typed-NULL form for the empty-rails fallback
    branch (``NULL::TEXT`` on Postgres, ``CAST(NULL AS CLOB)`` on
    Oracle).
    """
    df = dual_from(dialect)
    if not l2_instance.rails:
        # Should not happen for a valid L2 (there must be some rails);
        # the validator would catch it. Return a safe empty CTE that
        # produces zero rows so the LEFT JOIN works.
        nt = typed_null("varchar(4000)", dialect)
        return (
            "  SELECT\n"
            f"    {nt} AS rail_name,\n"
            f"    {nt} AS transfer_type,\n"
            f"    {nt} AS leg_shape,\n"
            f"    {nt} AS source_role,\n"
            f"    {nt} AS destination_role,\n"
            f"    {nt} AS leg_role,\n"
            f"    {nt} AS max_pending_age,\n"
            f"    {nt} AS max_unbundled_age,\n"
            f"    {nt} AS posted_requirements"
            f"{df}\n"
            "  WHERE 1=0"
        )
    rows: list[str] = []
    for r in l2_instance.rails:
        leg_shape = _leg_shape(r)
        if isinstance(r, TwoLegRail):
            source_role = _role_str(r.source_role)
            destination_role = _role_str(r.destination_role)
            leg_role = None
        else:
            source_role = None
            destination_role = None
            leg_role = _role_str(r.leg_role)
        max_pending = _duration_label(r.max_pending_age)
        max_unbundled = _duration_label(r.max_unbundled_age)
        posted_reqs = ",".join(
            sorted(str(k) for k in posted_requirements_for(l2_instance, r.name))
        )
        rows.append(
            "  SELECT "
            f"{_sql_str(str(r.name))} AS rail_name, "
            f"{_sql_str(str(r.transfer_type))} AS transfer_type, "
            f"{_sql_str(leg_shape)} AS leg_shape, "
            f"{_sql_nullable_str(source_role)} AS source_role, "
            f"{_sql_nullable_str(destination_role)} AS destination_role, "
            f"{_sql_nullable_str(leg_role)} AS leg_role, "
            f"{_sql_nullable_str(max_pending)} AS max_pending_age, "
            f"{_sql_nullable_str(max_unbundled)} AS max_unbundled_age, "
            f"{_sql_str(posted_reqs)} AS posted_requirements"
            f"{df}"
        )
    return "\n  UNION ALL\n".join(rows)


def _declared_chains_cte(l2_instance: L2Instance, dialect: Dialect) -> str:
    """Render the L2-declared ChainEntry list as a UNION ALL of
    SELECT-literal rows.

    ``source_node`` / ``target_node`` are the display strings the
    Sankey reads — currently identical to the parent / child name,
    but kept as separate columns so M.3.6+ can attach a "(Required)"
    or "(XOR: <group>)" suffix without breaking the join semantics.

    ``dialect`` selects the typed-NULL form for the empty-chains
    fallback branch.
    """
    df = dual_from(dialect)
    if not l2_instance.chains:
        nt = typed_null("varchar(4000)", dialect)
        return (
            "  SELECT\n"
            f"    {nt} AS parent_name,\n"
            f"    {nt} AS child_name,\n"
            f"    {nt} AS required,\n"
            f"    {nt} AS xor_group,\n"
            f"    {nt} AS source_node,\n"
            f"    {nt} AS target_node"
            f"{df}\n"
            "  WHERE 1=0"
        )
    rows: list[str] = []
    for c in l2_instance.chains:
        required_label = "Required" if c.required else "Optional"
        xor_group = str(c.xor_group) if c.xor_group is not None else None
        # Source / target node display strings — same as the names today;
        # M.3.6+ may suffix with required / xor info if visual readability
        # demands. Keeping the seam so the SQL stays stable.
        source_node = str(c.parent)
        target_node = str(c.child)
        rows.append(
            "  SELECT "
            f"{_sql_str(str(c.parent))} AS parent_name, "
            f"{_sql_str(str(c.child))} AS child_name, "
            f"{_sql_str(required_label)} AS required, "
            f"{_sql_nullable_str(xor_group)} AS xor_group, "
            f"{_sql_str(source_node)} AS source_node, "
            f"{_sql_str(target_node)} AS target_node"
            f"{df}"
        )
    return "\n  UNION ALL\n".join(rows)


def _leg_shape(rail: TwoLegRail | SingleLegRail) -> str:
    """Compact label combining leg arity + aggregating flag.

    Examples: "1-leg" / "2-leg" / "1-leg-aggregating" / "2-leg-aggregating".
    """
    arity = "2-leg" if isinstance(rail, TwoLegRail) else "1-leg"
    return f"{arity}-aggregating" if rail.aggregating else arity


def _role_str(role: tuple) -> str:
    """Render a RoleExpression (tuple of Identifiers) as a display string.

    Single-role: the name. UNION (multi-role): joined with " | " (the
    SPEC's union-set notation)."""
    parts = [str(r) for r in role]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return " | ".join(parts)


def _duration_label(td: timedelta | None) -> str | None:
    """Render a timedelta as a compact label ("24h", "1d", "30m").

    None → None (the SQL emits NULL). Non-evenly-divisible durations
    fall back to seconds.
    """
    if td is None:
        return None
    s = int(td.total_seconds())
    if s == 0:
        return "0s"
    if s % 86400 == 0:
        return f"{s // 86400}d"
    if s % 3600 == 0:
        return f"{s // 3600}h"
    if s % 60 == 0:
        return f"{s // 60}m"
    return f"{s}s"


def _sql_str(value: str) -> str:
    """Escape a Python string for embedding as a SQL string literal.

    Doubles single quotes per SQL standard (works on PostgreSQL +
    portable to other RDBMS per the project's portability constraint)."""
    return "'" + value.replace("'", "''") + "'"


def _sql_nullable_str(value: str | None) -> str:
    """SQL literal for an optional string — emits NULL when None."""
    if value is None:
        return "NULL"
    return _sql_str(value)


# -- M.3.7 CTE helpers -------------------------------------------------------


def _declared_transfer_types_cte(
    l2_instance: L2Instance, dialect: Dialect,
) -> str:
    """Distinct ``Rail.transfer_type`` values, one per SELECT row.

    Distinct because multiple Rails MAY share a transfer_type (the
    M.2d.1-deferred validator rule); the L2.2 'Unmatched transfer_type'
    check just wants the SET of declared types so it can find what's
    NOT in it.
    """
    df = dual_from(dialect)
    types = sorted({str(r.transfer_type) for r in l2_instance.rails})
    if not types:
        return (
            f"  SELECT {typed_null('varchar(4000)', dialect)} AS transfer_type"
            f"{df} WHERE 1=0"
        )
    rows = [
        f"  SELECT {_sql_str(t)} AS transfer_type{df}"
        for t in types
    ]
    return "\n  UNION ALL\n".join(rows)


def _declared_bundles_activity_cte(
    l2_instance: L2Instance, dialect: Dialect,
) -> str:
    """All (aggregating_rail, bundle_target) pairs the L2 declares.

    bundle_target is whatever Identifier the rail's
    ``bundles_activity`` lists — per SPEC, that resolves to either a
    rail_name or a transfer_type at runtime.
    """
    pairs: list[tuple[str, str]] = []
    for r in l2_instance.rails:
        if not r.aggregating:
            continue
        for target in r.bundles_activity:
            pairs.append((str(r.name), str(target)))
    df = dual_from(dialect)
    if not pairs:
        nt = typed_null("varchar(4000)", dialect)
        return (
            f"  SELECT {nt} AS aggregating_rail, "
            f"{nt} AS bundle_target{df} WHERE 1=0"
        )
    rows = [
        f"  SELECT {_sql_str(agg)} AS aggregating_rail, "
        f"{_sql_str(target)} AS bundle_target{df}"
        for agg, target in pairs
    ]
    return "\n  UNION ALL\n".join(rows)


def _dead_metadata_check_fragments(
    l2_instance: L2Instance, prefix: str, dialect: Dialect,
) -> list[str]:
    """One SELECT per declared (rail, metadata_key) pair guarded by
    NOT EXISTS against the prefixed transactions matview.

    Static JSON paths (``$.<literal>``) keep the SQL portable —
    PostgreSQL doesn't accept dynamic JSONPath arguments to
    ``JSON_VALUE`` without the v17+ JSON_TABLE syntax, and the
    project's no-JSONB constraint rules out the ``->>`` shortcut.
    """
    df = dual_from(dialect)
    fragments: list[str] = []
    for r in l2_instance.rails:
        for key in r.metadata_keys:
            rail_name = str(r.name)
            key_name = str(key)
            json_path = f"$.{key_name}"
            jv = json_value("t.metadata", _sql_str(json_path), dialect)
            fragments.append(
                f"  SELECT {_sql_str(rail_name)} AS rail_name, "
                f"{_sql_str(key_name)} AS metadata_key"
                f"{df}\n"
                f"  WHERE NOT EXISTS (\n"
                f"    SELECT 1\n"
                f"    FROM {prefix}_current_transactions t\n"
                f"    WHERE t.rail_name = {_sql_str(rail_name)}\n"
                f"      AND t.metadata IS NOT NULL\n"
                f"      AND {jv} IS NOT NULL\n"
                f"  )"
            )
    return fragments


def _declared_limit_schedules_cte(
    l2_instance: L2Instance, dialect: Dialect,
) -> str:
    """One SELECT row per LimitSchedule entry. The cap stays as a
    numeric literal; the parent_role + transfer_type are quoted
    string literals (they're Identifiers in the L2 model)."""
    df = dual_from(dialect)
    if not l2_instance.limit_schedules:
        nt = typed_null("varchar(4000)", dialect)
        nn = typed_null("numeric", dialect)
        return (
            f"  SELECT {nt} AS parent_role, "
            f"{nt} AS transfer_type, "
            f"{nn} AS cap{df} WHERE 1=0"
        )
    rows: list[str] = []
    for ls in l2_instance.limit_schedules:
        rows.append(
            f"  SELECT {_sql_str(str(ls.parent_role))} AS parent_role, "
            f"{_sql_str(str(ls.transfer_type))} AS transfer_type, "
            # Cap is a Decimal; render as a SQL numeric literal.
            f"CAST({ls.cap} AS DECIMAL(20,2)) AS cap{df}"
        )
    return "\n  UNION ALL\n".join(rows)


# -- Transfer Templates sheet (M.3.10f) ------------------------------------


def _declared_templates_cte(
    l2_instance: L2Instance, dialect: Dialect,
) -> str:
    """Render L2-declared TransferTemplate names + expected_net as a
    UNION ALL of SELECT-literal rows. The tt-instances builder joins
    against this CTE so only declared templates appear in the dataset
    (any rogue ``template_name`` value in current_transactions that
    doesn't correspond to a declared TransferTemplate is excluded —
    surfaced separately by the L2.2 unmatched-transfer-type check).
    """
    df = dual_from(dialect)
    if not l2_instance.transfer_templates:
        return (
            f"  SELECT {typed_null('varchar(4000)', dialect)} AS template_name, "
            f"{typed_null('numeric', dialect)} AS expected_net"
            f"{df} WHERE 1=0"
        )
    rows: list[str] = []
    for t in l2_instance.transfer_templates:
        rows.append(
            f"  SELECT {_sql_str(str(t.name))} AS template_name, "
            f"CAST({t.expected_net} AS DECIMAL(20,2)) AS expected_net{df}"
        )
    return "\n  UNION ALL\n".join(rows)


def build_tt_instances_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """One row per shared Transfer that matches a declared
    TransferTemplate (M.3.10f, completion_status reshaped M.3.10j).

    A "shared Transfer" is one ``transfer_id`` from
    ``<prefix>_current_transactions`` whose legs all carry the same
    ``template_name`` matching a declared template. Per SPEC: every
    firing of a ``leg_rails`` rail with the same ``transfer_key``
    Metadata values posts to the same shared Transfer, so the
    transfer_id distinct-count = number of TransferTemplate
    instances.

    ``completion_status`` is one of:

    - 'Imbalanced' — ``ABS(actual_net - expected_net) >= 0.01``
      (L1 Conservation break).
    - 'Orphaned' — balanced, but a Required chain child didn't fire
      OR an XOR group has 0 or > 1 fired members (L2 chain break).
    - 'Complete' — balanced AND every Required child fired AND every
      XOR group has exactly one fired member.

    Mirrors the chain-instances completion_status semantics so the
    analyst sees consistent language across both sheets.

    Parameterized on pKey + pValues for the metadata cascade.
    """
    prefix = l2_instance.instance
    declared_tt = _declared_templates_cte(l2_instance, cfg.dialect)
    declared_ch = _declared_chains_cte(l2_instance, cfg.dialect)
    sql = (
        f"WITH templates AS (\n{declared_tt}\n),\n"
        f"declared AS (\n{declared_ch}\n),\n"
        # Chain-shape per template: counts of declared Required children
        # + count of distinct XOR groups. Templates with no chain entries
        # (parent_name not in declared) get NULL for the LEFT JOIN, which
        # the COALESCEs below treat as zero.
        f"template_chain_shape AS (\n"
        f"  SELECT\n"
        f"    t.template_name,\n"
        f"    COALESCE(SUM(CASE WHEN d.required = 'Required' "
        f"THEN 1 ELSE 0 END), 0) AS required_total,\n"
        f"    COUNT(DISTINCT CASE WHEN d.xor_group IS NOT NULL "
        f"THEN d.xor_group END) AS xor_group_count\n"
        f"  FROM templates t\n"
        f"  LEFT JOIN declared d ON d.parent_name = t.template_name\n"
        f"  GROUP BY t.template_name\n"
        f"),\n"
        f"firings AS (\n"
        f"  SELECT\n"
        f"    t.template_name,\n"
        f"    t.expected_net,\n"
        f"    tcs.required_total,\n"
        f"    tcs.xor_group_count,\n"
        f"    ct.transfer_id,\n"
        f"    MIN(ct.posting) AS posting,\n"
        f"    SUM(ct.amount_money) AS actual_net,\n"
        f"    COUNT(*) AS leg_count,\n"
        f"    MAX(ct.metadata) AS parent_metadata\n"
        f"  FROM templates t\n"
        f"  JOIN template_chain_shape tcs ON tcs.template_name = t.template_name\n"
        f"  JOIN {prefix}_current_transactions ct\n"
        f"    ON ct.template_name = t.template_name\n"
        f"  GROUP BY t.template_name, t.expected_net, tcs.required_total, "
        f"tcs.xor_group_count, ct.transfer_id\n"
        f"),\n"
        # Chain completeness per firing — same shape as chain-instances:
        # required_fired = how many declared-Required children were
        # matched via transfer_parent_id; xor_violations = how many
        # declared XOR groups had ≠ 1 fired members.
        f"firing_completion AS (\n"
        f"  SELECT\n"
        f"    f.*,\n"
        f"    (\n"
        f"      SELECT COUNT(DISTINCT d.child_name)\n"
        f"      FROM declared d\n"
        f"      WHERE d.parent_name = f.template_name\n"
        f"        AND d.required = 'Required'\n"
        f"        AND EXISTS (\n"
        f"          SELECT 1 FROM {prefix}_current_transactions c\n"
        f"          WHERE COALESCE(c.template_name, c.rail_name) "
        f"= d.child_name\n"
        f"            AND c.transfer_parent_id = f.transfer_id\n"
        f"        )\n"
        f"    ) AS required_fired,\n"
        f"    (\n"
        f"      SELECT COUNT(*)\n"
        f"      FROM (\n"
        f"        SELECT d.xor_group,\n"
        f"          SUM(CASE WHEN EXISTS (\n"
        f"            SELECT 1 FROM {prefix}_current_transactions c\n"
        f"            WHERE COALESCE(c.template_name, c.rail_name) "
        f"= d.child_name\n"
        f"              AND c.transfer_parent_id = f.transfer_id\n"
        f"          ) THEN 1 ELSE 0 END) AS fired_in_group\n"
        f"        FROM declared d\n"
        f"        WHERE d.parent_name = f.template_name\n"
        f"          AND d.xor_group IS NOT NULL\n"
        f"        GROUP BY d.xor_group\n"
        f"      ) g\n"
        f"      WHERE g.fired_in_group <> 1\n"
        f"    ) AS xor_violations\n"
        f"  FROM firings f\n"
        f")\n"
        # Y.2.e — wrap the projection so the CASE-aliased
        # `completion_status` is visible to the outer WHERE; `template_name`
        # joins it there. Metadata cascade on `parent_metadata` stays inner.
        f"SELECT * FROM (\n"
        f"  SELECT\n"
        f"    template_name,\n"
        f"    transfer_id,\n"
        f"    posting,\n"
        f"    expected_net,\n"
        f"    actual_net,\n"
        f"    (actual_net - expected_net) AS net_diff,\n"
        f"    leg_count,\n"
        f"    CASE\n"
        f"      WHEN ABS(actual_net - expected_net) >= 0.01 THEN 'Imbalanced'\n"
        f"      WHEN required_fired < required_total THEN 'Orphaned'\n"
        f"      WHEN xor_violations > 0 THEN 'Orphaned'\n"
        f"      ELSE 'Complete'\n"
        f"    END AS completion_status\n"
        f"  FROM firing_completion\n"
        f"  WHERE\n"
        f"{metadata_filter_clause(l2_instance, 'parent_metadata', cfg.dialect)}\n"
        f") tt_instances\n"
        # Y.2.e — template / completion pushdown via multi-valued dataset
        # params. Defaults span all declared values; emptying a dropdown
        # reverts to the default (QS does not emit `IN ()`).
        f"WHERE template_name IN (<<$pL2ftTtTemplate>>)\n"
        f"  AND completion_status IN (<<$pL2ftTtCompletion>>)\n"
        f"ORDER BY posting DESC, template_name, transfer_id"
    )
    return build_dataset(
        cfg, cfg.prefixed("l2ft-tt-instances-dataset"),
        "L2FT TT Instances", "l2ft-tt-instances",
        sql, TT_INSTANCES_CONTRACT,
        visual_identifier=DS_TT_INSTANCES,
        dataset_parameters=_tt_dataset_parameters(l2_instance),
    )


def _tt_dataset_parameters(
    l2_instance: L2Instance,
) -> list[DatasetParameter]:
    """Y.2.e — the shared dataset-parameter set for both TransferTemplates
    datasets (tt-instances + tt-legs): the metadata cascade pair (pKey /
    pValues) plus the template / completion pushdown pair. Same IDs in
    both datasets is fine — dataset-parameter IDs are unique per-dataset.
    """
    return [
        DatasetParameter(StringDatasetParameter=StringDatasetParameter(
            Id=_DSP_ID_PKEY,
            Name="pKey",
            ValueType="SINGLE_VALUED",
            DefaultValues=StringDatasetParameterDefaultValues(
                StaticValues=[META_KEY_ALL_SENTINEL],
            ),
        )),
        DatasetParameter(StringDatasetParameter=StringDatasetParameter(
            Id=_DSP_ID_PVALUES,
            Name="pValues",
            # Y.1.m: SINGLE_VALUED to match the analysis-level
            # parameter shape (text-field control). Was MULTI_VALUED
            # but the text-field control couldn't commit non-empty
            # values to multi-valued params — broke the cascade.
            ValueType="SINGLE_VALUED",
            DefaultValues=StringDatasetParameterDefaultValues(
                StaticValues=[META_VALUE_PLACEHOLDER_SENTINEL],
            ),
        )),
        DatasetParameter(StringDatasetParameter=StringDatasetParameter(
            Id=_DSP_ID_PTTTEMPLATE,
            Name="pL2ftTtTemplate",
            ValueType="MULTI_VALUED",
            DefaultValues=StringDatasetParameterDefaultValues(
                # An instance with no Templates declared → empty list →
                # `IN ()` (invalid). Sentinel keeps it valid / zero-row.
                StaticValues=(
                    declared_template_names(l2_instance)
                    or [PUSHDOWN_NO_MATCH_SENTINEL]
                ),
            ),
        )),
        DatasetParameter(StringDatasetParameter=StringDatasetParameter(
            Id=_DSP_ID_PTTCOMPLETION,
            Name="pL2ftTtCompletion",
            ValueType="MULTI_VALUED",
            DefaultValues=StringDatasetParameterDefaultValues(
                StaticValues=tt_completion_status_values(),
            ),
        )),
    ]


def build_tt_legs_dataset(
    cfg: Config, l2_instance: L2Instance,
) -> DataSet:
    """One row per Sankey edge segment for a TransferTemplate firing
    (M.3.10f, chain-edge UNION added in M.3.10i).

    The query UNIONs three row-sources:

    1. **Template legs** (``edge_kind='template_leg'``) — actual
       transactions where ``template_name`` matches a declared
       TransferTemplate. ``flow_source`` / ``flow_target`` derive from
       ``amount_direction`` so the Sankey reads as
       ``debit account → template_name → credit account``.
    2. **Matched chain children** (``edge_kind='chain_matched'``) —
       actual transactions whose ``transfer_parent_id`` points at a
       template firing's transfer_id AND whose rail_name matches a
       declared chain child. ``flow_source = template_name``,
       ``flow_target = child_rail_name``.
    3. **Orphan chain children** (``edge_kind='chain_orphan'``) —
       SYNTHETIC rows for declared (template, child) chain edges
       that the runtime didn't fire. One row per (parent firing,
       declared child) where no matching child transaction exists.
       ``flow_source = template_name``, ``flow_target = child_rail_name
       || ' (orphan)'`` so the analyst sees the missing edge
       visualized as a thin dashed-style ribbon to a distinct node.

    Together this gives a complete Sankey per parent firing — every
    declared chain edge is visible whether it fired or not, with the
    template legs forming the central trunk.

    Width = ABS(amount_money). Synthetic orphan rows carry the parent
    firing's amount as a representative ribbon thickness so they're
    visually present but recognizable.

    Joining against the declared-templates CTE filters out any
    rogue ``template_name`` value in current_transactions that isn't
    in the L2 declaration (mirrors tt-instances).

    Parameterized on pKey + pValues for the metadata cascade.
    """
    prefix = l2_instance.instance
    declared_tt = _declared_templates_cte(l2_instance, cfg.dialect)
    declared_ch = _declared_chains_cte(l2_instance, cfg.dialect)
    sql = (
        f"WITH templates AS (\n{declared_tt}\n),\n"
        f"declared AS (\n{declared_ch}\n),\n"
        # Per-firing completion calc — same shape as tt-instances'
        # firing_completion CTE so the column denormalized into every
        # leg + chain-edge row matches what the Table sees per firing.
        # Lets cross_dataset='ALL_DATASETS' on the Completion filter
        # narrow both Sankey + Table together (M.3.10k).
        f"firing_completion AS (\n"
        f"  SELECT\n"
        f"    t.template_name,\n"
        f"    t.expected_net,\n"
        f"    ct.transfer_id,\n"
        f"    SUM(ct.amount_money) AS actual_net,\n"
        f"    (\n"
        f"      SELECT COUNT(*)\n"
        f"      FROM declared d\n"
        f"      WHERE d.parent_name = t.template_name\n"
        f"        AND d.required = 'Required'\n"
        f"    ) AS required_total,\n"
        f"    (\n"
        f"      SELECT COUNT(DISTINCT d.child_name)\n"
        f"      FROM declared d\n"
        f"      WHERE d.parent_name = t.template_name\n"
        f"        AND d.required = 'Required'\n"
        f"        AND EXISTS (\n"
        f"          SELECT 1 FROM {prefix}_current_transactions c\n"
        f"          WHERE COALESCE(c.template_name, c.rail_name) "
        f"= d.child_name\n"
        f"            AND c.transfer_parent_id = ct.transfer_id\n"
        f"        )\n"
        f"    ) AS required_fired,\n"
        f"    (\n"
        f"      SELECT COUNT(*) FROM (\n"
        f"        SELECT d.xor_group,\n"
        f"          SUM(CASE WHEN EXISTS (\n"
        f"            SELECT 1 FROM {prefix}_current_transactions c\n"
        f"            WHERE COALESCE(c.template_name, c.rail_name) "
        f"= d.child_name\n"
        f"              AND c.transfer_parent_id = ct.transfer_id\n"
        f"          ) THEN 1 ELSE 0 END) AS fired_in_group\n"
        f"        FROM declared d\n"
        f"        WHERE d.parent_name = t.template_name\n"
        f"          AND d.xor_group IS NOT NULL\n"
        f"        GROUP BY d.xor_group\n"
        f"      ) g WHERE g.fired_in_group <> 1\n"
        f"    ) AS xor_violations\n"
        f"  FROM templates t\n"
        f"  JOIN {prefix}_current_transactions ct\n"
        f"    ON ct.template_name = t.template_name\n"
        f"  GROUP BY t.template_name, t.expected_net, ct.transfer_id\n"
        f"),\n"
        f"firing_status AS (\n"
        f"  SELECT\n"
        f"    transfer_id,\n"
        f"    CASE\n"
        f"      WHEN ABS(actual_net - expected_net) >= 0.01 THEN 'Imbalanced'\n"
        f"      WHEN required_fired < required_total THEN 'Orphaned'\n"
        f"      WHEN xor_violations > 0 THEN 'Orphaned'\n"
        f"      ELSE 'Complete'\n"
        f"    END AS completion_status\n"
        f"  FROM firing_completion\n"
        f"),\n"
        # Real template legs.
        f"template_legs AS (\n"
        f"  SELECT\n"
        f"    ct.template_name,\n"
        f"    ct.transfer_id,\n"
        f"    ct.posting,\n"
        f"    ct.metadata,\n"
        f"    ct.account_name,\n"
        f"    ct.account_role,\n"
        f"    ct.amount_money,\n"
        f"    ct.amount_direction,\n"
        f"    ABS(ct.amount_money) AS amount_abs,\n"
        f"    CASE\n"
        f"      WHEN ct.amount_direction = 'Debit' THEN ct.account_name\n"
        f"      ELSE ct.template_name\n"
        f"    END AS flow_source,\n"
        f"    CASE\n"
        f"      WHEN ct.amount_direction = 'Debit' THEN ct.template_name\n"
        f"      ELSE ct.account_name\n"
        f"    END AS flow_target,\n"
        f"    CAST('template_leg' AS VARCHAR(20)) AS edge_kind,\n"
        f"    fs.completion_status\n"
        f"  FROM {prefix}_current_transactions ct\n"
        f"  JOIN templates t ON t.template_name = ct.template_name\n"
        f"  JOIN firing_status fs ON fs.transfer_id = ct.transfer_id\n"
        f"),\n"
        # One row per (parent firing, declared chain child) — the
        # cartesian we need to detect both matched + orphan edges.
        # parent_firings dedupes the legs of one shared Transfer to
        # one (template_name, transfer_id, posting, metadata) row so
        # the cartesian doesn't multiply by leg count.
        f"parent_firings AS (\n"
        f"  SELECT DISTINCT\n"
        f"    template_name,\n"
        f"    transfer_id,\n"
        f"    MIN(posting) OVER (PARTITION BY transfer_id) AS posting,\n"
        f"    MAX(metadata) OVER (PARTITION BY transfer_id) AS metadata,\n"
        f"    MAX(ABS(amount_money)) OVER (PARTITION BY transfer_id) "
        f"AS firing_amount_abs,\n"
        f"    MAX(completion_status) OVER (PARTITION BY transfer_id) "
        f"AS completion_status\n"
        f"  FROM template_legs\n"
        f"),\n"
        f"chain_edges AS (\n"
        f"  SELECT\n"
        f"    pf.template_name,\n"
        f"    pf.transfer_id,\n"
        f"    pf.posting,\n"
        f"    pf.metadata,\n"
        f"    CAST(NULL AS VARCHAR(255)) AS account_name,\n"
        f"    CAST(NULL AS VARCHAR(100)) AS account_role,\n"
        f"    pf.firing_amount_abs AS amount_money,\n"
        f"    CAST('Credit' AS VARCHAR(20)) AS amount_direction,\n"
        f"    pf.firing_amount_abs AS amount_abs,\n"
        f"    pf.template_name AS flow_source,\n"
        f"    CASE WHEN EXISTS (\n"
        f"      SELECT 1 FROM {prefix}_current_transactions c\n"
        f"      WHERE c.rail_name = d.child_name\n"
        f"        AND c.transfer_parent_id = pf.transfer_id\n"
        f"    ) THEN d.child_name\n"
        f"    ELSE d.child_name || ' (orphan)'\n"
        f"    END AS flow_target,\n"
        f"    CASE WHEN EXISTS (\n"
        f"      SELECT 1 FROM {prefix}_current_transactions c\n"
        f"      WHERE c.rail_name = d.child_name\n"
        f"        AND c.transfer_parent_id = pf.transfer_id\n"
        f"    ) THEN CAST('chain_matched' AS VARCHAR(20))\n"
        f"    ELSE CAST('chain_orphan' AS VARCHAR(20))\n"
        f"    END AS edge_kind,\n"
        f"    pf.completion_status\n"
        f"  FROM parent_firings pf\n"
        f"  JOIN declared d ON d.parent_name = pf.template_name\n"
        f")\n"
        # Y.2.e — wrap the UNION so `template_name` / `completion_status`
        # (both real columns in each branch's projection) are visible to a
        # single outer WHERE. Metadata cascade on `metadata` stays inside
        # each branch (the column is in the CTEs but not the projection).
        f"SELECT * FROM (\n"
        f"  SELECT template_name, transfer_id, posting, account_name, "
        f"account_role, amount_money, amount_direction, amount_abs, "
        f"flow_source, flow_target, edge_kind, completion_status\n"
        f"  FROM template_legs\n"
        f"  WHERE\n"
        f"{metadata_filter_clause(l2_instance, 'metadata', cfg.dialect)}\n"
        f"  UNION ALL\n"
        f"  SELECT template_name, transfer_id, posting, account_name, "
        f"account_role, amount_money, amount_direction, amount_abs, "
        f"flow_source, flow_target, edge_kind, completion_status\n"
        f"  FROM chain_edges\n"
        f"  WHERE\n"
        f"{metadata_filter_clause(l2_instance, 'metadata', cfg.dialect)}\n"
        f") tt_legs\n"
        # Y.2.e — template / completion pushdown (mirrors tt-instances so
        # the Template / Completion dropdowns narrow the Sankey + Table
        # together — the M.3.10k denormalization made this pair available).
        f"WHERE template_name IN (<<$pL2ftTtTemplate>>)\n"
        f"  AND completion_status IN (<<$pL2ftTtCompletion>>)\n"
        f"ORDER BY posting DESC, template_name, transfer_id"
    )
    return build_dataset(
        cfg, cfg.prefixed("l2ft-tt-legs-dataset"),
        "L2FT TT Legs", "l2ft-tt-legs",
        sql, TT_LEGS_CONTRACT,
        visual_identifier=DS_TT_LEGS,
        dataset_parameters=_tt_dataset_parameters(l2_instance),
    )
