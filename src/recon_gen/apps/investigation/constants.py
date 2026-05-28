"""Shared IDs for Investigation sheets, datasets, filter groups, and
visual IDs.

Phase K.4.2 shipped the 4 sheet IDs. K.4.3 adds the Recipient Fanout
sheet's dataset, filter group, parameter, and visual IDs. K.4.4 adds
the Volume Anomalies sheet's IDs. K.4.5 adds the Money Trail sheet's
IDs (chain root parameter, max-hops slider, min-hop-amount slider,
Sankey + hop-by-hop table visuals). K.4.8 adds the Account Network
sheet (5th sheet) — two Sankeys side-by-side over the K.4.5 matview,
viewed account-centrically: left Sankey shows inbound edges
(counterparties → anchor), right Sankey shows outbound edges (anchor
→ counterparties), with the anchor visually meeting in the middle. A
full-width touching-edges table sits below for the unambiguous
walk-the-flow drill.
"""

from recon_gen.common.ids import (
    FilterGroupId,
    ParameterName,
    SheetId,
)

# ---------------------------------------------------------------------------
# Sheets
# ---------------------------------------------------------------------------

SHEET_INV_GETTING_STARTED = SheetId("inv-sheet-getting-started")
SHEET_INV_FANOUT = SheetId("inv-sheet-fanout")                # K.4.3
SHEET_INV_ANOMALIES = SheetId("inv-sheet-anomalies")          # K.4.4
SHEET_INV_MONEY_TRAIL = SheetId("inv-sheet-money-trail")      # K.4.5
SHEET_INV_ACCOUNT_NETWORK = SheetId("inv-sheet-account-network")  # K.4.8
SHEET_INV_APP_INFO = SheetId("inv-sheet-app-info")                # M.4.4.5

# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------

DS_INV_RECIPIENT_FANOUT = "inv-recipient-fanout-ds"          # K.4.3
DS_INV_VOLUME_ANOMALIES = "inv-volume-anomalies-ds"          # K.4.4
# Y.1.b.companion — same matview as DS_INV_VOLUME_ANOMALIES but
# without the σ-pushdown parameter. Bound to the distribution chart
# only, which deliberately shows the FULL population shape regardless
# of where the analyst sets the σ slider. Pattern: when an analysis-
# level filter is SELECTED_VISUALS-scoped (KPI + Table only), the
# unfiltered visuals point at this companion dataset so SQL pushdown
# in the parameter-bearing dataset doesn't bleed across.
DS_INV_VOLUME_ANOMALIES_DISTRIBUTION = "inv-volume-anomalies-distribution-ds"  # Y.1
DS_INV_MONEY_TRAIL = "inv-money-trail-ds"                    # K.4.5
# Y.2.a.companion — same matview as DS_INV_MONEY_TRAIL but without the
# three pushdown parameters. Bound only to the chain-root dropdown's
# LinkedValues source so the dropdown's `SELECT DISTINCT root_transfer_id`
# query doesn't inherit the parameter-bearing dataset's WHERE clause
# (which would otherwise narrow the dropdown options to whatever the
# default-value sentinel selects). Same shape as
# DS_INV_VOLUME_ANOMALIES_DISTRIBUTION (Y.1.b.companion).
DS_INV_MONEY_TRAIL_ROOTS = "inv-money-trail-roots-ds"        # Y.2.a
DS_INV_ACCOUNT_NETWORK = "inv-account-network-ds"            # K.4.8
# BO.2 — directional siblings of DS_INV_ACCOUNT_NETWORK. Same matview,
# same anchor + min-amount pushdown bridge, but each pre-narrows to its
# Sankey's direction via a SQL WHERE (``target_display = anchor`` for
# inbound, ``source_display = anchor`` for outbound). Pre-BO.2 the two
# Sankeys shared the bidirectional dataset and were narrowed by visual-
# scoped ``FilterGroup``s — which QS applied but App2 silently dropped,
# so both Sankeys saw bidirectional rows and d3-sankey bailed out on
# the resulting cycles (blank canvas). The split makes "bidirectional
# rows reach a directional Sankey" unrepresentable.
DS_INV_ACCOUNT_NETWORK_INBOUND = "inv-account-network-inbound-ds"    # BO.2
DS_INV_ACCOUNT_NETWORK_OUTBOUND = "inv-account-network-outbound-ds"  # BO.2
# Narrow accounts dataset for the anchor dropdown only — K.4.8k. The
# main DS_INV_ACCOUNT_NETWORK wraps the matview with per-row concat
# (source_account_name||'('||source_account_id||')'); when QuickSight
# runs SELECT DISTINCT source_display against that wrapper the planner
# computes the concat over every matview row before dedupe — fine for a
# few hundred rows, slow enough to time out the dropdown as the matview
# grows. This dataset pushes the DISTINCT inside so PG dedupes the
# (id, name) pairs first and concats once per distinct account.
DS_INV_ANETWORK_ACCOUNTS = "inv-anetwork-accounts-ds"        # K.4.8k

# ---------------------------------------------------------------------------
# Filter groups
# ---------------------------------------------------------------------------

FG_INV_FANOUT_THRESHOLD = FilterGroupId("fg-inv-fanout-threshold")  # K.4.3
FG_INV_FANOUT_WINDOW = FilterGroupId("fg-inv-fanout-window")        # K.4.3
# Y.1.d — FG_INV_ANOMALIES_SIGMA removed; the σ threshold is now a
# dataset-level parameter (``<<$pInvAnomaliesSigma>>``) substituted
# into the dataset SQL by QS at query time. Bridge:
# ``apps/investigation/app.py``::``sigma_param.mapped_dataset_params``.
FG_INV_ANOMALIES_WINDOW = FilterGroupId("fg-inv-anomalies-window")  # K.4.4
# Y.2.a — FG_INV_MONEY_TRAIL_{ROOT,HOPS,AMOUNT} removed; the three
# parameter-bound filters are now dataset-level pushdowns substituted
# into the dataset SQL via ``<<$pInvMoneyTrailRoot>>`` /
# ``<<$pInvMoneyTrailMaxHops>>`` / ``<<$pInvMoneyTrailMinAmount>>``.
# Bridges: ``apps/investigation/app.py``::``mapped_dataset_params`` on
# each parameter declaration.
FG_INV_MONEY_TRAIL_WINDOW = FilterGroupId("fg-inv-money-trail-window")  # Q.1.b
# Y.2.b — FG_INV_ANETWORK_ANCHOR + FG_INV_ANETWORK_AMOUNT removed; the
# broad anchor narrow (source = anchor OR target = anchor) and the
# min-amount cutoff are now dataset-level pushdowns substituted into
# ``build_account_network_dataset``'s SQL via
# ``<<$pInvANetworkAnchor>>`` / ``<<$pInvANetworkMinAmount>>``.
# Bridges: ``apps/investigation/app.py``::``mapped_dataset_params``.
# BO.2 (2026-05-28) — the directional FilterGroups (FG_INV_ANETWORK_INBOUND
# / _OUTBOUND) were removed too. They scoped each Sankey to its
# direction via ``CategoryFilter(is_inbound_edge='yes')`` at the QS
# analysis layer; App2 doesn't apply visual-scoped FilterGroups, so both
# Sankeys received the bidirectional row set and d3-sankey crashed
# silently on the resulting cycles. The direction predicate now lives
# in the dataset SQL (``DS_INV_ACCOUNT_NETWORK_INBOUND`` /
# ``..._OUTBOUND``), keeping QS + App2 byte-symmetric for the wire shape.

# ---------------------------------------------------------------------------
# Calculated fields
# ---------------------------------------------------------------------------

# Analysis-level calc field on the recipient-fanout dataset. Counts
# distinct senders per recipient over the current row scope (post date
# filter); the threshold NumericRangeFilter narrows visuals to recipients
# whose count crosses pInvFanoutThreshold.
CF_INV_FANOUT_DISTINCT_SENDERS = "recipient_distinct_sender_count"

# Y.2.b — CF_INV_ANETWORK_IS_ANCHOR_EDGE removed. Pre-Y.2 it was the
# only consumer of FG_INV_ANETWORK_ANCHOR (CategoryFilter narrowing the
# table to anchor-touching edges); Y.2.b pushed the broad anchor narrow
# (source = anchor OR target = anchor) into the dataset SQL itself, so
# every row in ds_anet IS an anchor-touching edge by construction.
# The calc field had no remaining consumers and was orphaned.
# Y.3.b will push the directional calc fields (is_inbound_edge /
# is_outbound_edge) and counterparty_display into SQL too.
#
# Direction-specific edge-touching calc fields. Each scoped to its own
# Sankey so the inbound/outbound layout does the direction-encoding
# visually rather than asking the analyst to read it off a node's
# position inside one big Sankey.
#   is_inbound_edge  — 'yes' when the edge's TARGET equals the anchor
#                      (the anchor received the money)
#   is_outbound_edge — 'yes' when the edge's SOURCE equals the anchor
#                      (the anchor sent the money)
CF_INV_ANETWORK_IS_INBOUND_EDGE = "is_inbound_edge"
CF_INV_ANETWORK_IS_OUTBOUND_EDGE = "is_outbound_edge"

# Analysis-level calc field on the account-network dataset. For any edge
# touching the anchor, one side IS the anchor by definition; this picks
# the OTHER side. Lets the table wire a single, unambiguous "Walk to
# other account" drill. Walking no longer happens from the Sankeys —
# each Sankey now shows only one direction, so the walk-from-node
# ambiguity is gone, and QuickSight's Sankey right-click drill isn't
# functional in practice anyway.
CF_INV_ANETWORK_COUNTERPARTY_DISPLAY = "counterparty_display"

# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

P_INV_FANOUT_THRESHOLD = ParameterName("pInvFanoutThreshold")  # K.4.3
P_INV_ANOMALIES_SIGMA = ParameterName("pInvAnomaliesSigma")    # K.4.4
P_INV_MONEY_TRAIL_ROOT = ParameterName("pInvMoneyTrailRoot")   # K.4.5
P_INV_MONEY_TRAIL_MAX_HOPS = ParameterName("pInvMoneyTrailMaxHops")  # K.4.5
P_INV_MONEY_TRAIL_MIN_AMOUNT = ParameterName("pInvMoneyTrailMinAmount")  # K.4.5
P_INV_ANETWORK_ANCHOR = ParameterName("pInvANetworkAnchor")              # K.4.8
P_INV_ANETWORK_MIN_AMOUNT = ParameterName("pInvANetworkMinAmount")       # K.4.8

# Visual IDs are auto-derived per L.1.16; no constants needed here.
