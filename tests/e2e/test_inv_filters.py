"""Browser tests: Investigation parameter sliders narrow the visuals.

Ported onto the ``DashboardDriver`` protocol (X.2.q.3 — no Playwright in
the test body; ``qs_driver`` from conftest). Both tests stay
``@pytest.mark.skip``: Investigation's threshold knobs are single-value
``ParameterSliderControl`` widgets, and ``QsEmbedDriver.set_slider`` is
the one verb still ``NotImplementedError`` (there's no DOM helper for
that widget yet — see ``tests/e2e/_drivers/qs.py``). When ``set_slider``
lands, drop the skip and these light up.

(The slider-narrowing path is otherwise covered: App2 drives the same
``<<$param>>`` substitution via ``test_dashboard_driver`` /
``test_html2_*``, and the SQL-pushdown unit tests cover the substitution
itself. This file is the QS-DOM leg of the same behaviour.)
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.browser]

_SKIP_NO_QS_SLIDER = (
    "QsEmbedDriver.set_slider (ParameterSliderControl DOM-drive) is not "
    "implemented yet — X.2.q follow-on. Drop this skip when it lands."
)


@pytest.mark.skip(reason=_SKIP_NO_QS_SLIDER)
def test_min_sigma_slider_shrinks_anomalies_kpi(qs_driver, inv_dashboard_id):
    """Pushing the "Min sigma" slider to its max (4) must drop the
    Flagged Pair-Windows KPI below its default-σ value.

    Sigma threshold binds ``WHERE z_score >= <<$pInvAnomaliesSigma>>`` in
    the Volume Anomalies dataset SQL; the seed's z-scores cap in single
    digits, so σ=4 narrows the surviving rows hard.
    """
    qs_driver.open(inv_dashboard_id, sheet="Volume Anomalies")
    qs_driver.wait_loaded("Flagged Pair-Windows")
    before = qs_driver.kpi_value("Flagged Pair-Windows")

    qs_driver.set_slider("Min sigma", 4, None)
    qs_driver.wait_loaded("Flagged Pair-Windows")
    after = qs_driver.kpi_value("Flagged Pair-Windows")

    qs_driver.screenshot()
    assert after != before, (
        f"Flagged Pair-Windows should change at σ=4; "
        f"before={before!r} (default σ), after={after!r} (σ=4)"
    )


@pytest.mark.skip(reason=_SKIP_NO_QS_SLIDER)
def test_min_hop_amount_slider_shrinks_money_trail_table(
    qs_driver, inv_dashboard_id,
):
    """Pushing the "Min hop amount ($)" slider to its max ($1,000) must
    shrink the Money Trail Hop-by-Hop table vs its default ($0).

    Min-amount binds ``WHERE ... >= <<$pInvMoneyTrailMinAmount>>`` in the
    Money Trail dataset SQL.
    """
    qs_driver.open(inv_dashboard_id, sheet="Money Trail")
    qs_driver.wait_loaded("Money Trail — Hop-by-Hop")
    before = len(qs_driver.table_rows("Money Trail — Hop-by-Hop"))
    assert before > 0, f"Hop-by-Hop pre-filter should have rows, got {before}"

    qs_driver.set_slider("Min hop amount ($)", 1000, None)
    qs_driver.wait_loaded("Money Trail — Hop-by-Hop")
    after = len(qs_driver.table_rows("Money Trail — Hop-by-Hop"))

    qs_driver.screenshot()
    assert after < before, (
        f"Hop-by-Hop should shrink at min hop=$1,000; "
        f"before={before}, after={after}"
    )
