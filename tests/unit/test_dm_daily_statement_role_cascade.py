"""DM.1 + DM.2 — Daily Statement Role picker + Role→Account cascade.

These tests walk the REAL L1 Daily Statement tree (not a synthetic
fixture like ``test_app2_only_renderer_gate.py``) and lock the
DM-shape:

- **DM.1** — the Daily Statement sheet carries a ``Role`` dropdown,
  flagged ``app2_only=True``, positioned FIRST in the picker chain
  (Role → Account → Day). The QS emit drops it (flat Account + Day
  pair survives); the App2 spec walker keeps it.
- **DM.2** — the ``Account`` dropdown declares ``cascade_source`` =
  the Role dropdown + ``cascade_match_column`` = the accounts
  dataset's ``account_role`` column. The App2 spec carries the
  cascade-source param name (``pL1DsRole``); the QS emit of the
  Account control carries NO ``CascadingControlConfiguration`` because
  the cascade source is gated ``app2_only`` (see
  ``common/tree/controls.py::ParameterDropdown.emit``).

Design lock: ``docs/audits/dm_0_daily_statement_app2_cascade.md``.
"""

from __future__ import annotations

from recon_gen.apps.l1_dashboard.app import (
    P_L1_DS_ACCOUNT,
    P_L1_DS_BALANCE_DATE,
    P_L1_DS_ROLE,
    SHEET_DAILY_STATEMENT,
    build_l1_dashboard_app,
)
from recon_gen.apps.l1_dashboard.datasets import build_all_l1_dashboard_datasets
from recon_gen.common.html import make_filter_specs_for_sheet
from recon_gen.common.html.render import ParameterDateSpec, ParameterDropdownSpec
from recon_gen.common.l2 import default_l2_instance
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX
from recon_gen.common.tree.controls import (
    ParameterDateTimePicker,
    ParameterDropdown,
)
from recon_gen.common.tree.structure import App, Sheet
from tests._test_helpers import make_test_config


def _build_l1_app() -> App:
    cfg = make_test_config(db_table_prefix=DEFAULT_PREFIX)
    inst = default_l2_instance()
    build_all_l1_dashboard_datasets(cfg, inst)
    app = build_l1_dashboard_app(cfg, l2_instance=inst)
    app.resolve_auto_ids()
    return app


def _daily_statement_sheet(app: App) -> Sheet:
    assert app.analysis is not None
    for sheet in app.analysis.sheets:
        if sheet.sheet_id == SHEET_DAILY_STATEMENT:
            return sheet
    raise AssertionError("Daily Statement sheet not found in L1 tree")


def _dropdowns(sheet: Sheet) -> list[ParameterDropdown]:
    return [
        c for c in sheet.parameter_controls
        if isinstance(c, ParameterDropdown)
    ]


# --------------------------------------------------------------------------
# DM.1 — Role picker, App2-only, first in the chain.
# --------------------------------------------------------------------------

def test_dm1_role_dropdown_present_and_app2_only() -> None:
    """The Daily Statement sheet carries a ``Role`` dropdown that is
    flagged ``app2_only=True``."""
    sheet = _daily_statement_sheet(_build_l1_app())
    role = next(
        (d for d in _dropdowns(sheet) if d.title == "Role"), None,
    )
    assert role is not None, "DM.1 Role dropdown missing on Daily Statement"
    assert role.app2_only is True, (
        "DM.1 Role dropdown must be app2_only=True so the QS emit skips it"
    )
    assert role.parameter.name == str(P_L1_DS_ROLE)


def test_dm1_role_first_in_picker_chain() -> None:
    """Picker order is Role → Account → Day. The Role dropdown precedes
    the Account dropdown in the sheet's control list."""
    sheet = _daily_statement_sheet(_build_l1_app())
    titles = [d.title for d in _dropdowns(sheet)]
    assert "Role" in titles and "Account" in titles
    assert titles.index("Role") < titles.index("Account"), (
        f"Role must come before Account; got order {titles}"
    )


def test_dm1_role_param_not_pushed_into_datasets() -> None:
    """The Role param is a cascade SOURCE only — no
    ``mapped_dataset_params`` (the narrow happens in App2's BR.1 query,
    not via a QS dataset-param pushdown)."""
    sheet = _daily_statement_sheet(_build_l1_app())
    role = next(d for d in _dropdowns(sheet) if d.title == "Role")
    bridges = getattr(role.parameter, "mapped_dataset_params", None)
    assert not bridges, (
        "DM.1 Role param must not bridge into any dataset — it is a "
        f"cascade source only; got {bridges!r}"
    )


def test_dm1_role_dropped_from_qs_emit_kept_on_app2() -> None:
    """QS emit drops the app2_only Role control; the App2 spec walker
    keeps it (renders all controls)."""
    sheet = _daily_statement_sheet(_build_l1_app())

    # QS side: the emitted SheetDefinition's parameter-control titles
    # do NOT include "Role"; Account + Business Day survive.
    emitted = sheet.emit()
    qs_titles = {
        c.Dropdown.Title for c in (emitted.ParameterControls or [])
        if c.Dropdown is not None
    }
    assert "Role" not in qs_titles, (
        "QS emit must NOT carry the app2_only Role control"
    )
    assert "Account" in qs_titles

    # App2 side: the spec walker carries a dropdown spec for Role.
    specs = make_filter_specs_for_sheet(sheet)
    app2_labels = {
        s.label for s in specs if isinstance(s, ParameterDropdownSpec)
    }
    assert "Role" in app2_labels, (
        "App2 spec walker must render the Role dropdown"
    )


# --------------------------------------------------------------------------
# DM.2 — Role → Account cascade (tree wiring + renderer split).
# --------------------------------------------------------------------------

def test_dm2_account_dropdown_cascade_source_is_role() -> None:
    """The Account dropdown's ``cascade_source`` is the Role dropdown and
    its ``cascade_match_column`` is the accounts dataset's
    ``account_role`` column."""
    sheet = _daily_statement_sheet(_build_l1_app())
    account = next(d for d in _dropdowns(sheet) if d.title == "Account")
    assert account.cascade_source is not None, (
        "DM.2 Account dropdown must declare a cascade_source"
    )
    assert account.cascade_source.title == "Role"
    assert account.cascade_match_column is not None
    assert account.cascade_match_column.name == "account_role"


def test_dm2_app2_account_spec_carries_cascade_source_param() -> None:
    """App2's Account dropdown spec threads the cascade source param
    name (``pL1DsRole``) so render.py wires the BR.1 HTMX refresh."""
    sheet = _daily_statement_sheet(_build_l1_app())
    specs = make_filter_specs_for_sheet(sheet)
    account_spec = next(
        s for s in specs
        if isinstance(s, ParameterDropdownSpec) and s.label == "Account"
    )
    assert account_spec.cascade_source_param == str(P_L1_DS_ROLE), (
        "DM.2 App2 Account spec must carry the Role cascade source param"
    )


def test_dm2_qs_account_emit_has_no_cascade_config() -> None:
    """The QS emit of the Account control carries NO
    ``CascadingControlConfiguration`` — the cascade source (Role) is
    app2_only and never emitted to QS, so referencing it from a QS
    cascade block would dangle. controls.py gates the emit on the
    source's ``app2_only`` flag."""
    sheet = _daily_statement_sheet(_build_l1_app())
    emitted = sheet.emit()
    account_ctrl = next(
        c for c in (emitted.ParameterControls or [])
        if c.Dropdown is not None and c.Dropdown.Title == "Account"
    )
    assert account_ctrl.Dropdown is not None
    assert account_ctrl.Dropdown.CascadingControlConfiguration is None, (
        "QS Account control must not carry a CascadingControlConfiguration "
        "pointing at the app2_only Role control"
    )


# --------------------------------------------------------------------------
# DM.3 — Business Day picker day-availability decoration wiring.
# --------------------------------------------------------------------------

def test_dm3_business_day_picker_declares_account_source() -> None:
    """The Business Day picker declares its day-availability source
    account param (``pL1DsAccount``) so App2 wires the decoration."""
    sheet = _daily_statement_sheet(_build_l1_app())
    pickers = [
        c for c in sheet.parameter_controls
        if isinstance(c, ParameterDateTimePicker)
    ]
    business_day = next(
        (p for p in pickers if p.title == "Business Day"), None,
    )
    assert business_day is not None
    assert business_day.day_availability_account_param == str(P_L1_DS_ACCOUNT), (
        "DM.3 Business Day picker must name the Account picker as its "
        "day-availability source"
    )


def test_dm3_app2_date_spec_carries_account_param() -> None:
    """App2's date spec for the Business Day picker threads the source
    account param so the server can stamp the endpoint URL."""
    sheet = _daily_statement_sheet(_build_l1_app())
    specs = make_filter_specs_for_sheet(sheet)
    date_specs = [s for s in specs if isinstance(s, ParameterDateSpec)]
    business_day = next(
        (s for s in date_specs if s.name == str(P_L1_DS_BALANCE_DATE)), None,
    )
    assert business_day is not None
    assert business_day.day_availability_account_param == str(P_L1_DS_ACCOUNT)


def test_dm3_business_day_picker_not_gated_off_qs() -> None:
    """DM.3 decoration is App2-only via the tree flag (not app2_only) —
    the Business Day picker STILL emits to QS as a plain picker. (QS
    keeps Account + Day; only the Role widget is dropped.)"""
    sheet = _daily_statement_sheet(_build_l1_app())
    emitted = sheet.emit()
    dt_titles = {
        c.DateTimePicker.Title
        for c in (emitted.ParameterControls or [])
        if c.DateTimePicker is not None
    }
    assert "Business Day" in dt_titles, (
        "QS must still render the plain Business Day picker"
    )
