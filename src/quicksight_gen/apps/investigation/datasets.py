"""Custom-SQL datasets for the Investigation app.

K.4.3 ships the recipient-fanout dataset. K.4.4 adds the rolling-window
anomaly dataset (read from the ``inv_pair_rolling_anomalies`` matview).
K.4.5 adds the money-trail dataset (read from the
``inv_money_trail_edges`` matview, which precomputes the
``WITH RECURSIVE`` walk over ``parent_transfer_id``). K.4.8 wraps the
same matview as a second dataset so the account-centric filters
(anchor account, min amount) don't cross-contaminate K.4.5's
chain-rooted filters.

All datasets read the shared `transactions` + `daily_balances` base
tables — Investigation has no app-specific schema. The K.4.4 + K.4.5
matviews are computed at refresh time, not dataset time, because the
rolling-window z-score and the recursive chain walk were both too heavy
for QuickSight Direct Query at realistic transaction volumes.
"""

from __future__ import annotations

from quicksight_gen.apps.investigation.constants import (
    DS_INV_ACCOUNT_NETWORK,
    DS_INV_ANETWORK_ACCOUNTS,
    DS_INV_MONEY_TRAIL,
    DS_INV_MONEY_TRAIL_ROOTS,
    DS_INV_RECIPIENT_FANOUT,
    DS_INV_VOLUME_ANOMALIES,
    DS_INV_VOLUME_ANOMALIES_DISTRIBUTION,
    P_INV_ANETWORK_ANCHOR,
    P_INV_ANETWORK_MIN_AMOUNT,
    P_INV_ANOMALIES_SIGMA,
    P_INV_MONEY_TRAIL_MAX_HOPS,
    P_INV_MONEY_TRAIL_MIN_AMOUNT,
    P_INV_MONEY_TRAIL_ROOT,
)
from quicksight_gen.common.config import Config
from quicksight_gen.common.dataset_contract import (
    ColumnShape,
    ColumnSpec,
    DatasetContract,
    build_dataset,
    register_contract,
)
from quicksight_gen.common.models import (
    DataSet,
    DatasetParameter,
    IntegerDatasetParameter,
    IntegerDatasetParameterDefaultValues,
    StringDatasetParameter,
    StringDatasetParameterDefaultValues,
)
from quicksight_gen.common.sheets.app_info import (
    build_liveness_dataset,
    build_matview_status_dataset,
)


# M.4.4.5 — matviews the Investigation app reads, surfaced on the
# App Info ("i") sheet's matview-status table. V.3 — paired with the
# date column the App Info ``latest_date`` KPI takes its MAX from.
# `inv_pair_rolling_anomalies` carries `window_end` in its outer
# projection (the most recent day the rolling 2-day window covers —
# semantically the "freshest day this matview is current through").
# Note: `posted_day` lives in an inner CTE but is not projected.
# `inv_money_trail_edges` is a recursive-CTE walk over edge metadata
# with no natural date dimension (each row is a hop, not a posting),
# so it gets None.
_INV_MATVIEW_BARE_SPECS: list[tuple[str, str | None]] = [
    ("inv_pair_rolling_anomalies", "window_end"),
    ("inv_money_trail_edges", None),
]


def inv_matview_specs(
    l2_instance: L2Instance,
) -> list[tuple[str, str | None]]:
    """The L2-prefixed Inv matviews + base tables the dashboard reads,
    paired with the date column for App Info's ``latest_date`` KPI.

    Includes the base tables (transactions / daily_balances) so the
    operator can spot stale matviews against fresh ETL loads at a
    glance. Mirrors ``l1_matview_specs`` / ``l2ft_matview_specs``.
    """
    p = str(l2_instance.instance)
    return [
        (f"{p}_transactions", "posting"),
        (f"{p}_daily_balances", "business_day_start"),
        *((f"{p}_{name}", date_col) for name, date_col in _INV_MATVIEW_BARE_SPECS),
    ]


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------

# One row per (recipient leg, sender leg) pair sharing a transfer_id.
# Visuals aggregate to one row per recipient via COUNT_DISTINCT(sender_id)
# + SUM(amount), so the dataset stays at the legs grain to support both
# the fanout count and the per-row drill into AR Transactions in K.4.7.
RECIPIENT_FANOUT_CONTRACT = DatasetContract(columns=[
    ColumnSpec("recipient_account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
    ColumnSpec("recipient_account_name", "STRING"),
    ColumnSpec("recipient_account_type", "STRING"),
    ColumnSpec("sender_account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
    ColumnSpec("sender_account_name", "STRING"),
    ColumnSpec("sender_account_type", "STRING"),
    ColumnSpec("transfer_id", "STRING", shape=ColumnShape.TRANSFER_ID),
    ColumnSpec("posted_at", "DATETIME"),
    ColumnSpec("amount", "DECIMAL"),
])


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

# One row per (sender, recipient, posted_day) with that day's rolling
# 2-day SUM, transfer count, and z-score against the population of all
# pair-windows. Computed by the ``inv_pair_rolling_anomalies`` matview;
# see ``schema.sql`` for the windowing CTE.
VOLUME_ANOMALIES_CONTRACT = DatasetContract(columns=[
    ColumnSpec("recipient_account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
    ColumnSpec("recipient_account_name", "STRING"),
    ColumnSpec("recipient_account_type", "STRING"),
    ColumnSpec("sender_account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
    ColumnSpec("sender_account_name", "STRING"),
    ColumnSpec("sender_account_type", "STRING"),
    ColumnSpec("window_start", "DATETIME"),
    ColumnSpec("window_end", "DATETIME"),
    ColumnSpec("window_sum", "DECIMAL"),
    ColumnSpec("transfer_count", "INTEGER"),
    ColumnSpec("pop_mean", "DECIMAL"),
    ColumnSpec("pop_stddev", "DECIMAL"),
    ColumnSpec("z_score", "DECIMAL"),
    ColumnSpec("z_bucket", "STRING"),
])


# One row per (chain root, transfer, source-leg × target-leg) edge in the
# precomputed money-trail matview. ``root_transfer_id`` is the chain's
# top-most transfer (no parent); ``transfer_id`` is the transfer this
# edge belongs to; ``depth`` is the hop's distance from the root (0 =
# root). Edges include only multi-leg transfers — single-leg sales /
# external arrivals appear as chain members in the recursive walk but
# don't surface as visible edges. See ``schema.sql`` for the recursive
# CTE shape and the multi-leg-only rationale.
MONEY_TRAIL_CONTRACT = DatasetContract(columns=[
    ColumnSpec("root_transfer_id", "STRING", shape=ColumnShape.TRANSFER_ID),
    ColumnSpec("transfer_id", "STRING", shape=ColumnShape.TRANSFER_ID),
    ColumnSpec("depth", "INTEGER"),
    ColumnSpec("source_account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
    ColumnSpec("source_account_name", "STRING"),
    ColumnSpec("source_account_type", "STRING"),
    ColumnSpec("target_account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
    ColumnSpec("target_account_name", "STRING"),
    ColumnSpec("target_account_type", "STRING"),
    ColumnSpec("hop_amount", "DECIMAL"),
    ColumnSpec("posted_at", "DATETIME"),
    ColumnSpec("transfer_type", "STRING"),
    # Concatenated display labels, computed in the dataset SQL (see
    # ``_money_trail_base_sql``). Used by the Account Network sheet as the
    # walk-the-flow anchor — they're both human-readable AND uniquely
    # keyed (embedded account_id disambiguates name collisions). Money
    # Trail doesn't read these but they project cleanly through its
    # own dataset wrapper and stay zero-cost at query time.
    ColumnSpec("source_display", "STRING", shape=ColumnShape.ACCOUNT_DISPLAY),
    ColumnSpec("target_display", "STRING", shape=ColumnShape.ACCOUNT_DISPLAY),
])


# Y.2.a — companion contract for the chain-root dropdown's options
# source. Single column ``root_transfer_id`` distinct'd over the
# matview so the dropdown's option fetch is O(distinct chains) instead
# of O(matview rows). Same shape as ANETWORK_ACCOUNTS_CONTRACT.
MONEY_TRAIL_ROOTS_CONTRACT = DatasetContract(columns=[
    ColumnSpec("root_transfer_id", "STRING", shape=ColumnShape.TRANSFER_ID),
])


# Both money-trail-shaped datasets project the same matview; the
# wrapper computes the display columns inline so the matview stays a
# pure shape over base tables. N.3.d: matview name is per-instance
# prefixed (was global ``inv_money_trail_edges`` pre-N.3).
def _money_trail_base_sql(prefix: str) -> str:
    # Oracle disallows ``SELECT *, expr FROM ...`` — the star must be
    # qualified when other columns appear in the same SELECT list. The
    # ``e.*`` qualified form parses on both Postgres and Oracle.
    return (
        f"SELECT\n"
        f"    e.*,\n"
        f"    source_account_name || ' (' || source_account_id || ')' "
        f"AS source_display,\n"
        f"    target_account_name || ' (' || target_account_id || ')' "
        f"AS target_display\n"
        f"FROM {prefix}_inv_money_trail_edges e\n"
    )


# K.4.8k — narrow dataset feeding only the anchor-account dropdown.
# Single column ``source_display`` (the same concatenated label the
# Account Network dataset uses) so the anchor parameter, the calc
# fields, and the dropdown population all speak the same string. The
# DISTINCT happens INSIDE the SELECT so PG dedupes the (id, name) pairs
# before computing the per-row concat — O(distinct accounts) instead
# of O(matview rows). At dataset-load time the planner gets one column
# of ~tens of values; the dropdown loads instantly.
ANETWORK_ACCOUNTS_CONTRACT = DatasetContract(columns=[
    ColumnSpec(
        "source_display", "STRING", shape=ColumnShape.ACCOUNT_DISPLAY,
    ),
])

def _anetwork_accounts_sql(prefix: str) -> str:
    return (
        f"SELECT DISTINCT\n"
        f"    source_account_name || ' (' || source_account_id || ')' "
        f"AS source_display\n"
        f"FROM {prefix}_inv_money_trail_edges\n"
    )


def _require_prefix(cfg: Config) -> str:
    """Return ``cfg.l2_instance_prefix`` or raise.

    Investigation under L2-fed (N.3) requires ``build_investigation_app``
    to have set ``cfg.l2_instance_prefix`` before any dataset SQL is
    rendered. Build entry points either pre-stamp the field on the cfg
    or auto-derive it from ``l2_instance.instance``.
    """
    if cfg.l2_instance_prefix is None:
        raise ValueError(
            "Investigation datasets require cfg.l2_instance_prefix to be "
            "set; build_investigation_app derives it from the L2 instance."
        )
    return cfg.l2_instance_prefix


def build_recipient_fanout_dataset(cfg: Config) -> DataSet:
    """Recipient × sender × transfer rows, one per (recipient leg, sender leg).

    Filters to leaf-internal recipients (``account_scope='internal'``
    AND ``account_parent_role IS NOT NULL`` — the v6 equivalent of the
    v5 ``account_type IN ('dda', 'merchant_dda')`` filter, mirroring
    the Inv matview convention) so administrative sweeps into control
    accounts don't dominate the fanout ranking. v5 column names kept
    in the output projection (``_account_type``) so downstream consumers
    aren't sensitive to the source-side rename.
    """
    p = _require_prefix(cfg)
    sql = f"""\
WITH inflows AS (
    SELECT
        t.transfer_id,
        t.account_id            AS recipient_account_id,
        t.account_name          AS recipient_account_name,
        t.account_role          AS recipient_account_type,
        t.amount_money          AS amount,
        t.posting               AS posted_at
    FROM {p}_transactions t
    WHERE t.amount_money > 0
      AND t.status = 'Posted'
      AND t.account_scope = 'internal'
      AND t.account_parent_role IS NOT NULL
),
outflows AS (
    SELECT
        t.transfer_id,
        t.account_id            AS sender_account_id,
        t.account_name          AS sender_account_name,
        t.account_role          AS sender_account_type
    FROM {p}_transactions t
    WHERE t.amount_money < 0
      AND t.status = 'Posted'
)
SELECT
    i.recipient_account_id,
    i.recipient_account_name,
    i.recipient_account_type,
    o.sender_account_id,
    o.sender_account_name,
    o.sender_account_type,
    i.transfer_id,
    i.posted_at,
    i.amount
FROM inflows i
JOIN outflows o ON o.transfer_id = i.transfer_id"""
    return build_dataset(
        cfg,
        cfg.prefixed("inv-recipient-fanout-dataset"),
        "Investigation Recipient Fanout",
        "inv-recipient-fanout",
        sql,
        RECIPIENT_FANOUT_CONTRACT,
        visual_identifier=DS_INV_RECIPIENT_FANOUT,
    )


_DEFAULT_VOLUME_ANOMALIES_SIGMA = 2

# Y.1.b — dataset parameter id for the σ-threshold pushdown. Distinct
# from the parameter NAME (``pInvAnomaliesSigma``) — the name is what
# QuickSight substitutes via ``<<$pInvAnomaliesSigma>>`` and what App2
# binds via ``:param_pInvAnomaliesSigma`` after the preprocessor; the
# id is the dataset-resource-internal handle that QS uses to find the
# parameter when the analysis param's MappedDataSetParameters resolves
# to it.
_DSP_ID_INV_ANOMALIES_SIGMA = "dsp-inv-anomalies-sigma"


def build_volume_anomalies_dataset(cfg: Config) -> DataSet:
    """Pair-grain rolling-window anomalies — σ-filtered at the DB.

    Y.1.b — the σ-threshold parameter is pushed into the dataset SQL
    via ``<<$pInvAnomaliesSigma>>`` (QS substitutes the literal at
    query time) so QS Direct Query hits the database with the WHERE
    clause already applied — the matview's full row count never
    crosses the wire. The companion dataset
    ``build_volume_anomalies_distribution_dataset`` reads the same
    matview WITHOUT the parameter so the distribution chart stays
    unfiltered (its UX role is to show the full population shape
    against which the analyst reads the threshold).

    App2 reads the same SQL after a one-line preprocessor in
    ``_sql_executor`` translates ``<<$pInvAnomaliesSigma>>`` →
    ``:param_pInvAnomaliesSigma`` (bind variable from the URL).
    Both dialects converge on one SQL truth.

    Bridges to the analysis-level ``pInvAnomaliesSigma`` parameter
    via ``MappedDataSetParameters`` declared on the parameter
    declaration in ``apps/investigation/app.py``.
    """
    p = _require_prefix(cfg)
    sql = (
        f"SELECT * FROM {p}_inv_pair_rolling_anomalies "
        f"WHERE 1=1 AND z_score >= <<${P_INV_ANOMALIES_SIGMA}>>"
    )
    return build_dataset(
        cfg,
        cfg.prefixed("inv-volume-anomalies-dataset"),
        "Investigation Volume Anomalies",
        "inv-volume-anomalies",
        sql,
        VOLUME_ANOMALIES_CONTRACT,
        visual_identifier=DS_INV_VOLUME_ANOMALIES,
        dataset_parameters=[
            DatasetParameter(IntegerDatasetParameter=IntegerDatasetParameter(
                Id=_DSP_ID_INV_ANOMALIES_SIGMA,
                Name=str(P_INV_ANOMALIES_SIGMA),
                ValueType="SINGLE_VALUED",
                DefaultValues=IntegerDatasetParameterDefaultValues(
                    StaticValues=[_DEFAULT_VOLUME_ANOMALIES_SIGMA],
                ),
            )),
        ],
    )


def build_volume_anomalies_distribution_dataset(cfg: Config) -> DataSet:
    """Same matview as ``build_volume_anomalies_dataset`` — no σ filter.

    Y.1.b.companion — the SELECTED_VISUALS-scope workaround for SQL
    pushdown. The σ filter under Y.1 lives in the dataset SQL of
    ``build_volume_anomalies_dataset``; that filter applies to every
    visual reading that dataset. The Volume Anomalies sheet's
    distribution bar chart deliberately stays UNFILTERED (its UX
    role is to show the full population shape against which the
    analyst reads "where my σ threshold sits"). Filtering the
    distribution by σ would defeat its purpose.

    Solution: a second dataset over the same matview without the
    parameter. The distribution chart binds to this dataset; KPI
    + Table bind to the parameter-bearing one. Same matview, two
    dataset SELECT wrappers — DB cost is one matview scan per
    visual, identical to pre-Y. The duplication is the cost of
    preserving SELECTED_VISUALS scope under SQL-level pushdown.

    Y.2 will reuse this pattern wherever a FilterGroup with
    SELECTED_VISUALS scope gets pushed to dataset SQL.
    """
    p = _require_prefix(cfg)
    sql = f"SELECT * FROM {p}_inv_pair_rolling_anomalies"
    return build_dataset(
        cfg,
        cfg.prefixed("inv-volume-anomalies-distribution-dataset"),
        "Investigation Volume Anomalies — Distribution",
        "inv-volume-anomalies-distribution",
        sql,
        VOLUME_ANOMALIES_CONTRACT,
        visual_identifier=DS_INV_VOLUME_ANOMALIES_DISTRIBUTION,
    )


# Defaults matched to the analysis-level parameter declarations in
# ``apps/investigation/app.py``. The dataset-parameter default is what
# QS substitutes when the bridge has no value (initial load before any
# widget interaction); the analysis-level default is what the slider /
# dropdown widget shows. Both must match or the user sees one number
# in the control and a different filter applied at the DB.
_DEFAULT_MONEY_TRAIL_MAX_HOPS = 5
_DEFAULT_MONEY_TRAIL_MIN_AMOUNT = 0


_DSP_ID_INV_MONEY_TRAIL_ROOT = "dsp-inv-money-trail-root"
_DSP_ID_INV_MONEY_TRAIL_MAX_HOPS = "dsp-inv-money-trail-max-hops"
_DSP_ID_INV_MONEY_TRAIL_MIN_AMOUNT = "dsp-inv-money-trail-min-amount"

# Sentinel default for the chain-root dataset parameter. The analysis-
# level ``pInvMoneyTrailRoot`` carries an empty default by design (the
# dropdown auto-populates from the companion roots dataset on first
# paint); QS dataset parameters need a literal default to substitute
# when the bridge has no value, so we pick a sentinel that matches
# nothing in the matview. Initial paint of the Sankey + table is empty
# until the analyst commits a chain root via the dropdown — which fires
# the bridge per Y.1 finding.
_MONEY_TRAIL_ROOT_SENTINEL = "__no_chain_selected__"


def build_money_trail_dataset(cfg: Config) -> DataSet:
    """Per-edge money trail rows — Y.2.a SQL pushdown.

    Three analysis-level parameters bridge into dataset-level
    parameters substituted by QS into the dataset SQL at query time:

    - ``pInvMoneyTrailRoot`` → ``WHERE root_transfer_id = <<$...>>``
      narrows to a single chain. Initial paint substitutes a sentinel
      that matches nothing; the dropdown's first commit fires the
      bridge and the visuals populate.
    - ``pInvMoneyTrailMaxHops`` → ``AND depth <= <<$...>>`` caps chain
      depth. Default 5 substitutes literally on first paint.
    - ``pInvMoneyTrailMinAmount`` → ``AND hop_amount >= <<$...>>``
      drops noise edges. Default 0 = keep all on first paint.

    The chain-root dropdown reads from the
    ``build_money_trail_roots_dataset`` companion (no parameters) so
    the dropdown's DISTINCT-roots query doesn't inherit the WHERE
    clause we just baked in here. Pattern: Y.1.b.companion.

    App2 reads the same SQL after the
    ``translate_qs_dataset_params`` preprocessor in ``_sql_executor``
    rewrites ``<<$pName>>`` → ``:param_pName`` bind variables.
    """
    p = _require_prefix(cfg)
    base = _money_trail_base_sql(p)
    sql = (
        f"{base}WHERE 1=1\n"
        f"  AND e.root_transfer_id = <<${P_INV_MONEY_TRAIL_ROOT}>>\n"
        f"  AND e.depth <= <<${P_INV_MONEY_TRAIL_MAX_HOPS}>>\n"
        f"  AND e.hop_amount >= <<${P_INV_MONEY_TRAIL_MIN_AMOUNT}>>"
    )
    return build_dataset(
        cfg,
        cfg.prefixed("inv-money-trail-dataset"),
        "Investigation Money Trail",
        "inv-money-trail",
        sql,
        MONEY_TRAIL_CONTRACT,
        visual_identifier=DS_INV_MONEY_TRAIL,
        dataset_parameters=[
            DatasetParameter(StringDatasetParameter=StringDatasetParameter(
                Id=_DSP_ID_INV_MONEY_TRAIL_ROOT,
                Name=str(P_INV_MONEY_TRAIL_ROOT),
                ValueType="SINGLE_VALUED",
                DefaultValues=StringDatasetParameterDefaultValues(
                    StaticValues=[_MONEY_TRAIL_ROOT_SENTINEL],
                ),
            )),
            DatasetParameter(IntegerDatasetParameter=IntegerDatasetParameter(
                Id=_DSP_ID_INV_MONEY_TRAIL_MAX_HOPS,
                Name=str(P_INV_MONEY_TRAIL_MAX_HOPS),
                ValueType="SINGLE_VALUED",
                DefaultValues=IntegerDatasetParameterDefaultValues(
                    StaticValues=[_DEFAULT_MONEY_TRAIL_MAX_HOPS],
                ),
            )),
            DatasetParameter(IntegerDatasetParameter=IntegerDatasetParameter(
                Id=_DSP_ID_INV_MONEY_TRAIL_MIN_AMOUNT,
                Name=str(P_INV_MONEY_TRAIL_MIN_AMOUNT),
                ValueType="SINGLE_VALUED",
                DefaultValues=IntegerDatasetParameterDefaultValues(
                    StaticValues=[_DEFAULT_MONEY_TRAIL_MIN_AMOUNT],
                ),
            )),
        ],
    )


def build_money_trail_roots_dataset(cfg: Config) -> DataSet:
    """Companion to ``build_money_trail_dataset`` — distinct chain roots.

    Y.2.a — the parameter-bearing money-trail dataset filters rows by
    ``root_transfer_id = <<$pInvMoneyTrailRoot>>``. The chain-root
    dropdown can't read its options from that dataset (its
    ``SELECT DISTINCT root_transfer_id`` would inherit the WHERE
    clause). This companion wraps the same matview without the
    parameter so the dropdown's option fetch sees every chain root.
    Same pattern as ``build_volume_anomalies_distribution_dataset``
    (Y.1.b.companion) and ``build_account_network_accounts_dataset``
    (K.4.8k).
    """
    p = _require_prefix(cfg)
    sql = (
        f"SELECT DISTINCT root_transfer_id\n"
        f"FROM {p}_inv_money_trail_edges"
    )
    return build_dataset(
        cfg,
        cfg.prefixed("inv-money-trail-roots-dataset"),
        "Investigation Money Trail — Roots",
        "inv-money-trail-roots",
        sql,
        MONEY_TRAIL_ROOTS_CONTRACT,
        visual_identifier=DS_INV_MONEY_TRAIL_ROOTS,
    )


_DSP_ID_INV_ANETWORK_ANCHOR = "dsp-inv-anetwork-anchor"
_DSP_ID_INV_ANETWORK_MIN_AMOUNT = "dsp-inv-anetwork-min-amount"

# Sentinel default for the anchor dataset parameter. The analysis-level
# ``pInvANetworkAnchor`` carries an empty default by design (the dropdown
# auto-populates from the K.4.8k narrow accounts dataset on first paint);
# QS dataset parameters need a literal default for initial substitution
# when the bridge has no value, so we pick a sentinel that matches no
# source_display / target_display in the matview. Initial paint of the
# Sankeys + table is empty until the dropdown commits a real anchor and
# the bridge fires.
_ANETWORK_ANCHOR_SENTINEL = "__no_anchor_selected__"


def build_account_network_dataset(cfg: Config) -> DataSet:
    """Per-edge account-network rows — Y.2.b SQL pushdown.

    Same matview as money trail (``inv_money_trail_edges``) but with
    two analysis-level parameters bridging into dataset-level
    parameters substituted by QS into the dataset SQL at query time:

    - ``pInvANetworkAnchor`` → broad anchor narrow:
      ``WHERE (source_display = <<$pInvANetworkAnchor>> OR
      target_display = <<$pInvANetworkAnchor>>)``. Pre-narrows the
      wire to only edges that touch the anchor account in either
      direction. Initial paint substitutes a sentinel that matches
      no row; the dropdown's first commit fires the bridge.
    - ``pInvANetworkMinAmount`` → ``AND hop_amount >= <<$...>>``.
      Default 0 = keep all on first paint.

    Per-Sankey direction partitioning continues to happen via the
    ``is_inbound_edge`` / ``is_outbound_edge`` calc fields + their
    SELECTED_VISUALS-scoped FilterGroups (those operate on the
    pre-narrowed anchor-touching set; no DB pushdown needed because
    the work is already small post-anchor-narrow). Y.3.b will push
    those calc fields into the dataset SQL as real columns.

    The K.4.5 chain-root filters that pre-Y.2 lived on a separate
    dataset registration (Money Trail's chain-root context) are now
    irrelevant here — Account Network's narrow is anchor-driven,
    Money Trail's narrow is chain-root-driven; the two datasets
    keep their own pushdowns.

    The anchor dropdown reads from ``DS_INV_ANETWORK_ACCOUNTS``
    (K.4.8k) — already an unfiltered companion shape. No new
    companion dataset needed for Y.2.b.
    """
    p = _require_prefix(cfg)
    # CTE wrap: ``source_display`` / ``target_display`` are SELECT-list
    # aliases over concat expressions, not real matview columns. PG /
    # Oracle / SQLite all evaluate WHERE before SELECT, so the aliases
    # aren't visible to a same-query WHERE — `WHERE source_display = ...`
    # raises ``UndefinedColumn``. Wrapping the projection in a CTE moves
    # the WHERE one scope outward, where the alias IS in scope. Caught
    # by ``tests/integration/verify_dataset_sql.py`` in seconds.
    base = _money_trail_base_sql(p)
    sql = (
        f"WITH base AS (\n"
        f"{base}"
        f")\n"
        f"SELECT * FROM base\n"
        f"WHERE 1=1\n"
        f"  AND (\n"
        f"    source_display = <<${P_INV_ANETWORK_ANCHOR}>>\n"
        f"    OR target_display = <<${P_INV_ANETWORK_ANCHOR}>>\n"
        f"  )\n"
        f"  AND hop_amount >= <<${P_INV_ANETWORK_MIN_AMOUNT}>>"
    )
    return build_dataset(
        cfg,
        cfg.prefixed("inv-account-network-dataset"),
        "Investigation Account Network",
        "inv-account-network",
        sql,
        MONEY_TRAIL_CONTRACT,
        visual_identifier=DS_INV_ACCOUNT_NETWORK,
        dataset_parameters=[
            DatasetParameter(StringDatasetParameter=StringDatasetParameter(
                Id=_DSP_ID_INV_ANETWORK_ANCHOR,
                Name=str(P_INV_ANETWORK_ANCHOR),
                ValueType="SINGLE_VALUED",
                DefaultValues=StringDatasetParameterDefaultValues(
                    StaticValues=[_ANETWORK_ANCHOR_SENTINEL],
                ),
            )),
            DatasetParameter(IntegerDatasetParameter=IntegerDatasetParameter(
                Id=_DSP_ID_INV_ANETWORK_MIN_AMOUNT,
                Name=str(P_INV_ANETWORK_MIN_AMOUNT),
                ValueType="SINGLE_VALUED",
                DefaultValues=IntegerDatasetParameterDefaultValues(
                    StaticValues=[_DEFAULT_MONEY_TRAIL_MIN_AMOUNT],
                ),
            )),
        ],
    )


def build_account_network_accounts_dataset(cfg: Config) -> DataSet:
    """Narrow accounts dataset feeding the K.4.8 anchor dropdown only.

    Single column ``source_display`` distinct'd over the matview so
    QuickSight's dropdown can ``SELECT DISTINCT source_display FROM ...``
    in O(distinct accounts) work instead of O(matview rows). Originally
    the dropdown pointed at the full Account Network dataset; that
    dataset wraps the matview with a per-row concat that the dropdown's
    DISTINCT couldn't push past, so the dropdown started timing out as
    the matview grew. This dataset puts the DISTINCT inside the SELECT
    so PG dedupes the (id, name) pairs before concatenating.

    Reuses ``inv_money_trail_edges`` — no new matview needed.
    """
    return build_dataset(
        cfg,
        cfg.prefixed("inv-anetwork-accounts-dataset"),
        "Investigation Account Network — Accounts",
        "inv-anetwork-accounts",
        _anetwork_accounts_sql(_require_prefix(cfg)),
        ANETWORK_ACCOUNTS_CONTRACT,
        visual_identifier=DS_INV_ANETWORK_ACCOUNTS,
    )


def build_all_datasets(
    cfg: Config, l2_instance: L2Instance,
) -> list[DataSet]:
    """Return every dataset Investigation's sheets reference.

    ``l2_instance`` is required for App Info matview names (which need
    the L2 prefix). Mirrors the L1 / L2FT / Exec ``build_all_datasets``
    signatures — every L2-fed app threads the instance explicitly.
    """
    return [
        build_recipient_fanout_dataset(cfg),
        build_volume_anomalies_dataset(cfg),
        build_volume_anomalies_distribution_dataset(cfg),
        build_money_trail_dataset(cfg),
        build_money_trail_roots_dataset(cfg),
        build_account_network_dataset(cfg),
        build_account_network_accounts_dataset(cfg),
        # M.4.4.5 — App Info ("i") sheet datasets, ALWAYS LAST.
        # M.4.4.7 — per-app segment so deploy <single-app> doesn't
        # delete-then-create another app's App Info dataset.
        build_liveness_dataset(cfg, app_segment="inv"),
        build_matview_status_dataset(
            cfg, app_segment="inv",
            view_specs=inv_matview_specs(l2_instance),
        ),
    ]


# Register contracts at module import so visuals built later resolve
# drill source-field shapes without depending on dataset construction
# order.
_CONTRACT_REGISTRATIONS: tuple[tuple[str, DatasetContract], ...] = (
    (DS_INV_RECIPIENT_FANOUT, RECIPIENT_FANOUT_CONTRACT),
    (DS_INV_VOLUME_ANOMALIES, VOLUME_ANOMALIES_CONTRACT),
    (DS_INV_VOLUME_ANOMALIES_DISTRIBUTION, VOLUME_ANOMALIES_CONTRACT),
    (DS_INV_MONEY_TRAIL, MONEY_TRAIL_CONTRACT),
    (DS_INV_MONEY_TRAIL_ROOTS, MONEY_TRAIL_ROOTS_CONTRACT),
    (DS_INV_ACCOUNT_NETWORK, MONEY_TRAIL_CONTRACT),
    (DS_INV_ANETWORK_ACCOUNTS, ANETWORK_ACCOUNTS_CONTRACT),
)
for _vid, _contract in _CONTRACT_REGISTRATIONS:
    register_contract(_vid, _contract)
