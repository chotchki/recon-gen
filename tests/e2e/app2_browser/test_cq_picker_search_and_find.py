"""CQ.4.c + CQ.4.e — search-and-find browser e2e for every CQ-touched picker.

The Phase CQ bug class is **silent omission from a picker**: the picker
renders, options appear, but a legitimate value the operator expects
isn't in the list (truncation cap, ceiling guard, dataset-source
narrowing, cascade misfire, etc). Pyright + unit tests can't catch this
— it only surfaces when the operator types and gets no match. So the
load-bearing gate has to live in the browser layer, on the renderer
the operator actually uses.

Two shapes per renderer:

1. **CQ.4.c — Daily Statement Account picker.** Operator-locked
   2026-06-08: "The e2e browser test MUST search and find a 1:N
   account and a 1:1 account in qs and app2." Verifies both account
   classes (child + singleton-control) are pickable post-CQ.4 widening.

2. **CQ.4.e — every other CQ-touched picker.** User redirect 2026-06-08:
   "we really should do this same style of broswer test for every
   picker we touched." Covers the CQ.2 typeahead pickers
   (DS_L1_ACCOUNTS, DS_L1_TX_IDS) + the CQ.3 LinkedValues pickers
   (DS_RAILS, DS_ACCOUNT_ROLES, DS_METADATA_KEYS) + L2FT's Rail /
   Template / Chain pickers (CQ.3 LinkedValues sources).

Why specs read values from the deployed DB rather than hardcoding:
sasquatch_pr is the bundled L2 but it isn't the only L2 the suite
runs against; hardcoded values would break on spec_example /
fuzz_*. The helper SQL picks ONE row from each picker's source
matview/view; the assertion is "this DB-confirmed value is
findable" — which IS the silent-omission contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from recon_gen.apps.l1_dashboard.app import (
    _DAILY_STATEMENT_NAME,
    _DRIFT_NAME,
    _PENDING_AGING_NAME,
    _TRANSACTIONS_NAME,
)
from recon_gen.apps.l2_flow_tracing.app import (
    _CHAINS_NAME,
    _RAILS_NAME,
    _TRANSFER_TEMPLATES_NAME,
)
from recon_gen.common.config import Config
from recon_gen.common.db import connect_demo_db
from tests._marks import Need, Tier, needs, tier


if TYPE_CHECKING:
    from tests.e2e._drivers import DashboardDriver


pytestmark = [
    pytest.mark.e2e,
    tier(Tier.APP2),
    needs(Need.PLAYWRIGHT),
]


# -- Helpers -----------------------------------------------------------------


def _fetch_one(cfg: Config, sql: str) -> str:
    """Run ``sql`` against the deployed DB and return the first column
    of the first row as a string. Raises ``RuntimeError`` if no rows —
    that means the picker source matview/view is empty, which IS the
    failure shape (no values to surface in the picker)."""
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql)
            row = cur.fetchone()
        finally:
            cur.close()
    finally:
        conn.close()
    if row is None:
        raise RuntimeError(
            f"picker-source query returned zero rows — picker would "
            f"render empty: {sql!r}"
        )
    value = row[0]
    if value is None:
        raise RuntimeError(
            f"picker-source query returned NULL first column — picker "
            f"would render an empty/blank option: {sql!r}"
        )
    return str(value)


@dataclass(frozen=True)
class PickerSearchSpec:
    """One picker to type-and-find. ``value_sql`` returns a single
    known-good value the helper expects to appear in the picker's
    options + be pickable on both renderers."""

    sheet_name: str
    picker_label: str
    value_sql_template: str
    description: str

    def expected_value(self, cfg: Config) -> str:
        return _fetch_one(cfg, self.value_sql_template.format(
            prefix=cfg.db.table_prefix,
        ))


def _assert_pickable(
    driver: "DashboardDriver", picker_label: str, value: str, *,
    sheet_context: str,
) -> None:
    """The load-bearing assertion: ``value`` must appear in the
    picker's options AND be selectable. Both legs catch a silent
    omission — options gate catches "picker source is too narrow",
    pick gate catches "option appears but binding rejects it" (the
    AA.E.2 display-vs-id miss shape).

    DG.3 — uses ``typeahead_filter`` (not ``filter_options``) so the
    membership check works for both static + typeahead-marked
    pickers. For typeahead pickers like ``DS_L1_TX_IDS`` (8k+ row
    contract), ``filter_options`` returns the EMPTY SEED PAGE
    (typeahead pickers don't advertise options until something is
    typed). ``typeahead_filter`` types the expected value + reads
    the server-narrowed result on both legs; for non-typeahead
    pickers, the App2 + QS drivers fall through to the same shape
    as ``filter_options``. Caught by v13.15.1-gate CI failure
    ``test_cq_4_e_l1_picker_finds_known_value[qs-Transactions-Transfer]``
    showing ``Advertised (first 10 of 0): []`` after the prior
    timeout-retry fix unmasked it.
    """
    options = driver.typeahead_filter(picker_label, value)
    assert value in options, (
        f"{sheet_context}: picker {picker_label!r} does not advertise "
        f"value {value!r} (queried via typeahead). Picker source "
        f"narrowed too tightly, truncated, or the LinkedValues "
        f"column binding is wrong. "
        f"Advertised (first 10 of {len(options)}): "
        f"{sorted(options)[:10]}..."
    )
    # Drive the pick — App2's pick_filter calls Tom Select's
    # load(value) + setValue(value); QS clicks the option. Either
    # surface a binding/format mismatch as a downstream failure.
    driver.pick_filter(picker_label, [value])
    # Re-read options to confirm the value is still pickable post-pick
    # (a binding miss would silently fail to register the pick; the
    # value should still appear in the option set since the source
    # matview/view didn't change).
    options_after = driver.typeahead_filter(picker_label, value)
    assert value in options_after, (
        f"{sheet_context}: after picking {value!r} on picker "
        f"{picker_label!r}, the picker no longer advertises it. The "
        f"pick likely mutated the source dataset's narrowing in a "
        f"way that hid the just-picked value (the silent-omission "
        f"regression shape on a parametrized source)."
    )


# -- CQ.4.c — Daily Statement: 1:1 singleton + 1:N child --------------------


@dataclass(frozen=True)
class DsAccountClass:
    """One of the two account classes the operator's lock requires."""

    label: str
    value_sql_template: str
    description: str

    def expected_value(self, cfg: Config) -> str:
        return _fetch_one(cfg, self.value_sql_template.format(
            prefix=cfg.db.table_prefix,
        ))


# account_display is computed at the dataset layer (CQ.1 NULL-safe
# COALESCE), NOT stored as a column on the matview. The helper SQL
# computes the same expression inline so the value the test reads
# matches the value the LinkedValues picker advertises.
_ACCOUNT_DISPLAY_EXPR = (
    "(COALESCE(account_name, account_id) || ' (' || account_id || ')')"
)

_DS_ACCOUNT_CLASSES: tuple[DsAccountClass, ...] = (
    DsAccountClass(
        label="1:1 singleton (control)",
        # account_parent_role IS NULL AND scope = 'internal' — same
        # filter as DS_L1_DS_CONTROL_ACCOUNTS (the bottom reference
        # Table on Daily Statement). account_display matches the
        # picker's LinkedValues binding.
        value_sql_template=(
            f"SELECT MIN({_ACCOUNT_DISPLAY_EXPR}) "
            "FROM {prefix}_current_daily_balances "
            "WHERE account_parent_role IS NULL AND account_scope = 'internal'"
        ),
        description="GL-control singleton (e.g. CashDueFRB)",
    ),
    DsAccountClass(
        label="1:N child",
        # account_parent_role NOT NULL AND scope = 'internal' — the
        # children rolled up under a control parent. account_display
        # picks one stable value via MIN for determinism.
        value_sql_template=(
            f"SELECT MIN({_ACCOUNT_DISPLAY_EXPR}) "
            "FROM {prefix}_current_daily_balances "
            "WHERE account_parent_role IS NOT NULL "
            "AND account_scope = 'internal'"
        ),
        description="rolled-up child account (e.g. cust-0001 ledger row)",
    ),
)


@pytest.mark.parametrize(
    "account_class", _DS_ACCOUNT_CLASSES, ids=lambda c: c.label,
)
def test_cq_4_c_daily_statement_finds_account_class(
    l1_dashboard_driver: tuple["DashboardDriver", str],
    cfg: Config, account_class: DsAccountClass,
) -> None:
    """CQ.4.c — Daily Statement Account picker MUST find both a 1:1
    singleton account AND a 1:N child account on both renderers.
    Operator-locked 2026-06-08 — the load-bearing gate for the CQ.4
    Role-cascade-drop + scope-widen redesign.

    Failure shapes:

    - **1:1 singleton missing** → the picker source's WHERE narrowed
      out the GL-control accounts (the original v13.6.1 silent
      omission). Pre-CQ.4 these were absent because the Role
      cascade defaulted to a CustomerSubledger-narrowed view that
      excluded GL-control roles.

    - **1:N child missing** → the picker source narrowed to parents
      only (the opposite over-correction — making the picker only
      show singletons would mirror the audit's complaint in reverse).
      Operator-locked: "ALL internal accounts should be searchable" —
      both classes are pickable.

    - **Pick succeeds but value disappears after** → typeahead binding
      mismatch (AA.E.2 family — display-vs-id, format-vs-bare).
    """
    driver, dashboard_arg = l1_dashboard_driver
    value = account_class.expected_value(cfg)

    driver.open(dashboard_arg, sheet=_DAILY_STATEMENT_NAME)
    driver.wait_loaded("Opening Balance")

    _assert_pickable(
        driver, "Account", value,
        sheet_context=(
            f"Daily Statement — {account_class.label} "
            f"({account_class.description})"
        ),
    )
    driver.screenshot()


# -- CQ.4.e — generalize the search-and-find to every CQ-touched picker ----


# One spec per CQ-touched picker source × representative sheet. Each
# spec is hand-pinned to a sheet that demonstrably uses the source;
# the helper SQL fetches one known-good value to type+find.
_L1_PICKER_SEARCH_SPECS: tuple[PickerSearchSpec, ...] = (
    # CQ.2 — DS_L1_ACCOUNTS typeahead picker (was the 2000-cap source).
    PickerSearchSpec(
        sheet_name=_DRIFT_NAME,
        picker_label="Account",
        value_sql_template=(
            f"SELECT MIN({_ACCOUNT_DISPLAY_EXPR}) "
            "FROM {prefix}_current_daily_balances"
        ),
        description=(
            "DS_L1_ACCOUNTS — Drift sheet Account picker (CQ.2 "
            "typeahead-enabled; was the silent-2000-cap source)"
        ),
    ),
    # CQ.3 — DS_ACCOUNT_ROLES LinkedValues picker. Drift's Account
    # Role pulls from the v_config_account_roles → DS_ACCOUNT_ROLES
    # source.
    PickerSearchSpec(
        sheet_name=_DRIFT_NAME,
        picker_label="Account Role",
        value_sql_template=(
            "SELECT MIN(account_role) FROM {prefix}_current_daily_balances "
            "WHERE account_role IS NOT NULL"
        ),
        description="DS_ACCOUNT_ROLES — Drift sheet Account Role picker (CQ.3 LinkedValues)",
    ),
    # CQ.3 — DS_RAILS LinkedValues picker. Pending Aging's Rail
    # pulls from v_config_rails → DS_RAILS.
    PickerSearchSpec(
        sheet_name=_PENDING_AGING_NAME,
        picker_label="Rail",
        value_sql_template=(
            "SELECT MIN(rail_name) FROM {prefix}_current_transactions "
            "WHERE rail_name IS NOT NULL"
        ),
        description="DS_RAILS — Pending Aging Rail picker (CQ.3 LinkedValues)",
    ),
    # CQ.2 — DS_L1_TX_IDS typeahead picker (Transactions Transfer).
    # The "8k+ rows" case that drove the cap drop.
    PickerSearchSpec(
        sheet_name=_TRANSACTIONS_NAME,
        picker_label="Transfer",
        value_sql_template=(
            "SELECT MIN(transfer_id) FROM {prefix}_current_transactions "
            "WHERE transfer_id IS NOT NULL"
        ),
        description="DS_L1_TX_IDS — Transactions Transfer picker (CQ.2 typeahead, was 8k+ rows)",
    ),
    # DR.3 — DS_L1_TX_TRANSACTION_IDS typeahead picker (Transactions
    # Transaction ID). The value universe is `id` (the v6 PK), UNIQUE-
    # indexed on the matview so the matview-direct search is sub-ms.
    PickerSearchSpec(
        sheet_name=_TRANSACTIONS_NAME,
        picker_label="Transaction ID",
        value_sql_template=(
            "SELECT MIN(id) FROM {prefix}_current_transactions "
            "WHERE id IS NOT NULL"
        ),
        description="DS_L1_TX_TRANSACTION_IDS — Transactions Transaction ID picker (DR.3 typeahead, indexed via UNIQUE idx_*_curr_tx_id)",
    ),
)


@pytest.mark.parametrize(
    "spec", _L1_PICKER_SEARCH_SPECS,
    ids=lambda s: f"{s.sheet_name}-{s.picker_label}",
)
def test_cq_4_e_l1_picker_finds_known_value(
    l1_dashboard_driver: tuple["DashboardDriver", str],
    cfg: Config, spec: PickerSearchSpec,
) -> None:
    """CQ.4.e — for each L1 picker we touched in Phase CQ, a known-good
    value from the picker's source matview/view MUST be both
    advertised by the picker and pickable on both QS + App2."""
    driver, dashboard_arg = l1_dashboard_driver
    value = spec.expected_value(cfg)

    driver.open(dashboard_arg, sheet=spec.sheet_name)

    _assert_pickable(
        driver, spec.picker_label, value, sheet_context=spec.description,
    )
    driver.screenshot()


# L2FT specs — three CQ.3 LinkedValues sources (DS_RAILS / DS_TEMPLATES /
# DS_CHAIN_PARENTS) + one DS_METADATA_KEYS site (the Rails sheet
# Metadata Key picker is the most stable; the Chain / Template sheets
# also have Metadata Key but cover the same source).
_L2FT_PICKER_SEARCH_SPECS: tuple[PickerSearchSpec, ...] = (
    PickerSearchSpec(
        sheet_name=_RAILS_NAME,
        picker_label="Rail",
        value_sql_template=(
            "SELECT MIN(rail_name) FROM {prefix}_current_transactions "
            "WHERE rail_name IS NOT NULL"
        ),
        description="DS_RAILS — L2FT Rails sheet Rail picker (CQ.3 LinkedValues)",
    ),
    PickerSearchSpec(
        sheet_name=_RAILS_NAME,
        picker_label="Metadata Key",
        value_sql_template=(
            "SELECT MIN(metadata_key) FROM {prefix}_v_config_rail_metadata_keys "
            "WHERE metadata_key IS NOT NULL"
        ),
        description="DS_METADATA_KEYS — L2FT Rails sheet Metadata Key picker (CQ.3 LinkedValues)",
    ),
    PickerSearchSpec(
        sheet_name=_TRANSFER_TEMPLATES_NAME,
        picker_label="Template",
        # DS_TEMPLATES SQL: SELECT name FROM v_config_transfer_templates
        # (the column is `name`, not `transfer_template_name`).
        value_sql_template=(
            "SELECT MIN(name) FROM {prefix}_v_config_transfer_templates "
            "WHERE name IS NOT NULL"
        ),
        description="DS_TEMPLATES — L2FT Transfer Templates sheet Template picker (CQ.3 LinkedValues)",
    ),
    PickerSearchSpec(
        sheet_name=_CHAINS_NAME,
        picker_label="Chain",
        # DS_CHAIN_PARENTS SQL: SELECT DISTINCT parent_name FROM
        # v_config_chain_children (BS.5's existing view).
        value_sql_template=(
            "SELECT MIN(parent_name) FROM {prefix}_v_config_chain_children "
            "WHERE parent_name IS NOT NULL"
        ),
        description="DS_CHAIN_PARENTS — L2FT Chains sheet Chain picker (CQ.3 LinkedValues)",
    ),
)


@pytest.mark.parametrize(
    "spec", _L2FT_PICKER_SEARCH_SPECS,
    ids=lambda s: f"{s.sheet_name}-{s.picker_label}",
)
def test_cq_4_e_l2ft_picker_finds_known_value(
    l2ft_dashboard_driver: tuple["DashboardDriver", str],
    cfg: Config, spec: PickerSearchSpec,
) -> None:
    """CQ.4.e — same as L1 sibling but for L2FT pickers."""
    driver, dashboard_arg = l2ft_dashboard_driver
    value = spec.expected_value(cfg)

    driver.open(dashboard_arg, sheet=spec.sheet_name)

    _assert_pickable(
        driver, spec.picker_label, value, sheet_context=spec.description,
    )
    driver.screenshot()
