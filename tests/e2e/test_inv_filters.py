"""Browser tests: Investigation parameter sliders narrow the visuals.

Parametrized over ``[qs, app2]`` (X.2.u.3) via ``inv_dashboard_driver``.
Both tests currently ``pytest.skip`` on *both* legs — Investigation's
threshold knobs are single-value ``ParameterSlider`` controls and
neither renderer can drive that today:

- **qs** — ``QsEmbedDriver.set_slider`` (the ``ParameterSliderControl``
  DOM-drive) is the one verb still ``NotImplementedError`` (X.2.q
  follow-on; no DOM helper for that widget yet).
- **app2** — App2's filter-spec auto-derivation (``make_filter_specs_for_sheet``)
  emits no widget for a ``ParameterSlider``-bound named parameter, so
  there's nothing for ``App2Driver.set_slider`` to grab. (``App2Driver.set_slider``
  itself works — it drives the noUiSlider over a column ``NumericRangeSpec``
  — just not these.) The X.2.l.4.d follow-on adds an App2 ``ParameterSlider``
  widget; then drop that skip.

The slider-narrowing *behaviour* is covered meanwhile: the SQL-pushdown
unit tests assert ``<<$pInvAnomaliesSigma>>`` / ``<<$pInvMoneyTrailMinAmount>>``
expand into ``WHERE z_score >= …`` / ``… >= …``, and ``test_html2_*`` /
``test_dashboard_driver`` exercise the App2 substitution path. This file
is the DOM-drive leg, lit up once the verbs above land.
"""

from __future__ import annotations

import pytest

from tests.e2e._drivers import DashboardDriver


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


def _skip_until_slider_drive(driver: DashboardDriver) -> None:
    """Both legs skip until the slider-drive verbs land — see the module
    docstring. Dialect-specific reason so the skip log is actionable."""
    if driver.dialect == "qs":
        pytest.skip(
            "QsEmbedDriver.set_slider (ParameterSliderControl DOM-drive) "
            "not implemented yet — X.2.q follow-on"
        )
    pytest.skip(
        "App2 renders no filter widget for ParameterSlider-bound named "
        "params yet — X.2.l.4.d follow-on. The <<$param>> SQL pushdown "
        "still narrows (covered by the pushdown unit tests + test_html2_*)."
    )


def test_min_sigma_slider_shrinks_anomalies_kpi(inv_dashboard_driver):
    """Pushing the "Min sigma" slider to its max (4) must drop the
    Flagged Pair-Windows KPI below its default-σ value.

    Sigma threshold binds ``WHERE z_score >= <<$pInvAnomaliesSigma>>`` in
    the Volume Anomalies dataset SQL; the seed's z-scores cap in single
    digits, so σ=4 narrows the surviving rows hard.
    """
    driver, dashboard_arg = inv_dashboard_driver
    _skip_until_slider_drive(driver)
    driver.open(dashboard_arg, sheet="Volume Anomalies")
    driver.wait_loaded("Flagged Pair-Windows")
    before = driver.kpi_value("Flagged Pair-Windows")

    driver.set_slider("Min sigma", 4, None)
    driver.wait_loaded("Flagged Pair-Windows")
    after = driver.kpi_value("Flagged Pair-Windows")

    driver.screenshot()
    assert after != before, (
        f"Flagged Pair-Windows should change at σ=4; "
        f"before={before!r} (default σ), after={after!r} (σ=4)"
    )


def test_min_hop_amount_slider_shrinks_money_trail_table(inv_dashboard_driver):
    """Pushing the "Min hop amount ($)" slider to its max ($1,000) must
    shrink the Money Trail Hop-by-Hop table vs its default ($0).

    Min-amount binds ``WHERE ... >= <<$pInvMoneyTrailMinAmount>>`` in the
    Money Trail dataset SQL.
    """
    driver, dashboard_arg = inv_dashboard_driver
    _skip_until_slider_drive(driver)
    driver.open(dashboard_arg, sheet="Money Trail")
    driver.wait_loaded("Money Trail — Hop-by-Hop")
    before = len(driver.table_rows("Money Trail — Hop-by-Hop"))
    assert before > 0, f"Hop-by-Hop pre-filter should have rows, got {before}"

    driver.set_slider("Min hop amount ($)", 1000, None)
    driver.wait_loaded("Money Trail — Hop-by-Hop")
    after = len(driver.table_rows("Money Trail — Hop-by-Hop"))

    driver.screenshot()
    assert after < before, (
        f"Hop-by-Hop should shrink at min hop=$1,000; "
        f"before={before}, after={after}"
    )
