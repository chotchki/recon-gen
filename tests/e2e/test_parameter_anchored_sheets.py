"""Browser e2e: parameter-anchored sheets surface their anchor control
identically on QuickSight and App 2 (u.4.e.4).

Three sheets across two apps render *blank-until-you-pick* on default
load — each is driven by a value the analyst sets first:

- **Investigation Money Trail** — ``Chain root transfer`` (single-select
  dropdown, options from the chain-roots companion).
- **Investigation Account Network** — ``Anchor account`` (single-select
  dropdown, options from the narrow accounts dataset; the canonical
  "trust the chart, not the control" sheet).
- **L2 Flow Tracing Transfer Templates** — ``Template`` (multi-select
  dropdown over the L2's declared template names).

Neither renderer pre-picks a default — the bound analysis parameter
declares none — so the parity invariant this test guards is structural:
the anchor control is present in the filter bar with a non-empty option
universe, populated from its dataset, the same on both renderers
(parametrised ``[qs, app2]`` via the ``<app>_dashboard_driver`` fixtures).

**L1 Daily Statement** (``Account`` single-select, options from the
accounts companion) is the fourth such sheet but is *not* exercised here:
every visual on that sheet is account-AND-date scoped, so it renders
nothing on default load, and there's no always-rendering visual to
``wait_loaded`` on as a "the sheet is ready" signal (and getting a
non-blank render needs picking both an account and a known-good
``balance_date`` — out of scope for a structural parity test). Its
``Account`` control's shape — a ``ParameterDropdownSpec`` with
``options_dataset=DS_L1_ACCOUNTS[…]`` — is covered by ``test_tree_filter_specs``;
its render parity by ``test_l1_sheet_visuals::test_l1_dashboard_structure_matches_tree[app2]``.

What's *not* asserted here (verb / quirk gaps, covered elsewhere):

- "the widget shows no value on default load" — there's no
  ``DashboardDriver`` verb for a control's current selection; the
  blank-on-default behaviour is documented, not assayed.
- "``?param_<anchor>=<v>`` in the page URL narrows the destination" —
  the App 2 ``server.py`` route threads it (``_apply_url_param_overrides``,
  unit-tested in ``tests/unit/test_html_server.py``; the row-drill
  "walk" path exercises it end-to-end once ``test_inv_drilldown``
  re-lights), but QS's URL-param write doesn't sync its controls
  (``project_qs_url_parameter_no_control_sync``) and ``DashboardDriver.open``
  has no "open with a parameter pre-set" form — so there's nothing to
  parametrise over both renderers cleanly today.
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


def _assert_anchor_present_and_populated(
    driver, dashboard_arg, *, sheet_name: str, anchor_label: str,
    visual_title: str,
) -> None:
    """Open ``sheet_name``, wait for ``visual_title`` to render, then
    assert a filter control labelled like ``anchor_label`` exists and is
    populated with options."""
    driver.open(dashboard_arg, sheet=sheet_name)
    driver.wait_loaded(visual_title)

    labels = driver.filter_labels()
    matched = next((lbl for lbl in labels if anchor_label in lbl), None)
    assert matched is not None, (
        f"{sheet_name!r}: expected an anchor control labelled like "
        f"{anchor_label!r} in the filter bar, got {labels!r}"
    )
    opts = driver.filter_options(matched)
    assert opts, (
        f"{sheet_name!r}: the {matched!r} control should be populated "
        f"from its dataset, got an empty option list"
    )


@pytest.mark.parametrize(
    "sheet_name, anchor_label, visual_title",
    [
        ("Money Trail", "Chain root transfer", "Money Trail — Hop-by-Hop"),
        ("Account Network", "Anchor account",
         "Account Network — Touching Edges"),
    ],
)
def test_inv_anchor_control_present_and_populated(
    inv_dashboard_driver, sheet_name, anchor_label, visual_title,
) -> None:
    driver, dashboard_arg = inv_dashboard_driver
    _assert_anchor_present_and_populated(
        driver, dashboard_arg, sheet_name=sheet_name,
        anchor_label=anchor_label, visual_title=visual_title,
    )


def test_l2ft_transfer_templates_anchor_control_present_and_populated(
    l2ft_dashboard_driver,
) -> None:
    driver, dashboard_arg = l2ft_dashboard_driver
    _assert_anchor_present_and_populated(
        driver, dashboard_arg, sheet_name="Transfer Templates",
        anchor_label="Template", visual_title="Template Instances",
    )
