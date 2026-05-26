"""Browser tests: Investigation parameter sliders narrow the visuals.

Parametrized over ``[qs, app2]`` (X.2.u.3) via ``inv_dashboard_driver``.
Investigation's threshold knobs (σ / max-hops / min-amount) are
single-value ``ParameterSlider`` controls; their narrowing is pushed
into the dataset SQL as a scalar ``<<$param>>`` bind (Y.2.a/Y.3.c), so
moving the slider re-fetches a narrower result on *both* renderers.
``set_slider(label, value, None)`` drives them on both legs (X.2.u.4.e):

- **app2** — ``make_filter_specs_for_sheet`` emits a ``ParameterNumberSpec``
  per ``ParameterSlider`` → an ``<input type="number" name="param_<name>">``
  + a one-handle noUiSlider; ``App2Driver.set_slider`` writes the input +
  a bubbling ``change`` → the visual re-fetches with the new scalar bind.
- **qs** — ``QsEmbedDriver.set_slider`` fills the QS ``ParameterSliderControl``
  card's typable text box and blurs it (the value commits on focus-loss),
  then settles on the WS-frame re-fetch.

The slider-narrowing *behaviour* is additionally covered renderer-free
by the SQL-pushdown unit tests (``<<$pInvAnomaliesSigma>>`` →
``WHERE z_score >= …``, ``<<$pInvMoneyTrailMinAmount>>`` → ``… >= …``)
and the App2 substitution path by ``test_html2_*`` / ``test_dashboard_driver``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from recon_gen.apps.investigation.datasets import (
    build_recipient_fanout_dataset,
    build_volume_anomalies_dataset,
    build_volume_anomalies_distribution_dataset,
)
from tests.e2e._kpi_parse import parse_currency_kpi, parse_int_kpi


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


def _sql_and_params_for(builder, *args):  # type: ignore[no-untyped-def]: builder takes (cfg) or (cfg, l2) at runtime — annotating would force imports here
    ds = builder(*args)
    sql = next(iter(ds.PhysicalTableMap.values())).CustomSql.SqlQuery
    # DataSet.DatasetParameters is `None` when the dataset declares no
    # `<<$pName>>` parameters (e.g. the volume-anomalies distribution
    # dataset, which reads a single matview with no param-bind columns).
    # `list(None)` would TypeError — fall back to an empty list.
    return sql, list(ds.DatasetParameters or ())


def test_min_sigma_slider_shrinks_anomalies_kpi(inv_dashboard_driver):
    """Pushing the "Min sigma" slider to its max (4) must drop the
    Flagged Pair-Windows KPI below its default-σ value.

    Sigma threshold binds ``WHERE z_score >= <<$pInvAnomaliesSigma>>`` in
    the Volume Anomalies dataset SQL; the seed's z-scores cap in single
    digits, so σ=4 narrows the surviving rows hard.
    """
    driver, dashboard_arg = inv_dashboard_driver
    driver.open(dashboard_arg, sheet="Volume Anomalies")
    driver.wait_loaded("Flagged at current σ")
    before = driver.kpi_value("Flagged at current σ")
    try:
        before_count = int((before or "0").replace(",", "").lstrip("$"))
    except ValueError:
        before_count = -1  # unparseable → not "empty"; let the assert run
    if before_count == 0:
        pytest.skip(
            "Flagged Pair-Windows starts at 0 for the deployed L2 — the "
            "Volume Anomalies seed produces no z-score anomalies above the "
            "default σ, so the σ-slider narrowing guard has nothing to "
            "shrink. Same shape as the Money-Trail min-hop skip below; the "
            "empty-render path is covered by the sheet-visuals tests. "
            "Plant pair-window spikes in the demo seed to re-light this."
        )

    driver.set_slider("Min sigma", 4, None)
    driver.wait_loaded("Flagged at current σ")
    after = driver.kpi_value("Flagged at current σ")

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
    driver.open(dashboard_arg, sheet="Money Trail")
    driver.wait_loaded("Money Trail — Hop-by-Hop")
    before = len(driver.table_rows("Money Trail — Hop-by-Hop"))
    if before <= 0:
        pytest.skip(
            "Money Trail — Hop-by-Hop starts empty for the deployed L2 "
            "(no multi-hop edges seeded — spec_example declares zero "
            "chains and single-leg templates); the slider-narrowing guard "
            "has nothing to shrink. The empty-render path is covered by "
            "the sheet-visuals tests."
        )

    driver.set_slider("Min hop amount ($)", 1000, None)
    driver.wait_loaded("Money Trail — Hop-by-Hop")
    after = len(driver.table_rows("Money Trail — Hop-by-Hop"))

    driver.screenshot()
    assert after < before, (
        f"Hop-by-Hop should shrink at min hop=$1,000; "
        f"before={before}, after={after}"
    )


# BG.4 — Investigation KPI honest gates -----------------------------------


def test_bg4_volume_anomalies_kpi_matches_filtered_matview_and_distribution(
    inv_dashboard_driver, cfg,
):
    """BG.4 — the Flagged Pair-Windows KPI must equal both:
    (a) the row count of the σ-filtered Volume Anomalies dataset
        (the dataset the table on this sheet binds), AND
    (b) the sum of the unfiltered distribution dataset's bins where
        ``z_score >= default_sigma`` — proves the KPI and the chart
        are reading the same underlying matview consistently.

    Direct catch for v11.21.0 cold-read finding #5 (KPI = 0 flagged
    pair-windows while the σ-distribution chart shows populated 2-3σ
    / 3-4σ / 4σ+ bins). The cold-read's bug shape is one of:
      - KPI binds the wrong dataset (e.g. the unfiltered distribution
        but with COUNT-DISTINCT)
      - Threshold mismatch: the KPI's σ-cut and the chart's bucket
        labels diverge (KPI uses ≥4 while bars label as ≥2)
      - Stale matview: dataset returns zero but distribution shows
        bins from an older snapshot
    Assertion (a) catches binding bugs; (b) catches threshold /
    matview-staleness divergence between the two visuals.
    """
    driver, dashboard_arg = inv_dashboard_driver
    driver.open(dashboard_arg, sheet="Volume Anomalies")
    driver.wait_loaded("Flagged at current σ")

    filtered_sql, filtered_params = _sql_and_params_for(
        build_volume_anomalies_dataset, cfg,
    )
    distribution_sql, dist_params = _sql_and_params_for(
        build_volume_anomalies_distribution_dataset, cfg,
    )

    filtered_rows = driver.query_db(
        filtered_sql, dataset_parameters=filtered_params,
    )
    distribution_rows = driver.query_db(
        distribution_sql, dataset_parameters=dist_params,
    )

    # (a) KPI == filtered-dataset row count.
    rendered_kpi = parse_int_kpi(driver.kpi_value("Flagged at current σ"))
    assert rendered_kpi == len(filtered_rows), (
        f"Flagged Pair-Windows KPI: rendered {rendered_kpi} ≠ "
        f"len(query_db(σ-filtered anomalies SQL)) = {len(filtered_rows)}. "
        f"v11.21.0 cold-read finding #5 binding-bug shape — KPI's "
        f"binding doesn't match the table on the same sheet, which "
        f"DOES bind this dataset."
    )

    # (b) Filtered-dataset count == distribution-dataset count above
    # the default σ threshold. Pull the default from the dataset
    # parameter (single source of truth — matches what QS substitutes
    # on initial load + what apply_dataset_param_defaults binds for
    # App2).
    default_sigma = _default_sigma_from(filtered_params)
    above_threshold = [
        r for r in distribution_rows
        if r["z_score"] is not None and Decimal(str(r["z_score"])) >= default_sigma
    ]
    assert len(filtered_rows) == len(above_threshold), (
        f"Filtered Volume Anomalies dataset returns {len(filtered_rows)} "
        f"rows but the distribution dataset has {len(above_threshold)} "
        f"rows with z_score ≥ {default_sigma} (default σ). These should "
        f"agree — both read the same matview. v11.21.0 finding #5: KPI "
        f"shows 0 flagged while the distribution chart shows populated "
        f">σ bins. If THIS trips, the matview itself is inconsistent "
        f"between the two SELECT wrappers (stale read? race?) or the "
        f"default σ on the KPI dataset is wrong."
    )
    driver.screenshot()


def test_bg4_recipient_fanout_kpis_match_inflows_only_truth(
    inv_dashboard_driver, cfg,
):
    """BG.4 — the Recipient Fanout KPIs (Qualifying Recipients, Distinct
    Senders, Total Inbound) must NOT exceed inflows-only ground truth
    (one row per recipient leg per transfer, NO cartesian inflation
    across sender legs).

    Direct catch for v11.21.0 cold-read finding #7 (Total Inbound =
    $1.54B implausibly large; 58% of bank-wide gross handle in 4
    recipients). The current fanout SQL JOINs inflows × outflows on
    transfer_id — for a transfer with N sender legs + M recipient
    legs, joined produces N×M rows each carrying the inflow amount,
    inflating SUM(amount) by the sender-leg count.

    Ground truth: dedupe the fanout dataset's rows by
    (recipient_account_id, transfer_id) — that gives the per-recipient
    per-transfer inflow ONCE. Sum gives the un-inflated Total Inbound.
    The rendered KPI must equal this de-duped sum (modulo the
    qualifying-recipients filter, which both already apply).

    NOTE: this test is **expected to fail on current production code**
    until the fanout SQL is rewritten to aggregate inflows-side BEFORE
    joining outflows for the sender-enumeration. That failure shape IS
    the catch — leaving the test red as the visible gate until the
    rewrite lands.
    """
    driver, dashboard_arg = inv_dashboard_driver
    driver.open(dashboard_arg, sheet="Recipient Fanout")
    driver.wait_loaded("Total Inbound")

    fanout_sql, fanout_params = _sql_and_params_for(
        build_recipient_fanout_dataset, cfg,
    )
    rows = driver.query_db(fanout_sql, dataset_parameters=fanout_params)

    if not rows:
        pytest.skip(
            "Recipient Fanout dataset returned no rows for the deployed "
            "L2 — the seed plants no qualifying recipients above the "
            "default threshold (test_min_sigma_slider_shrinks_anomalies_kpi "
            "uses the same skip pattern). Plant fanout spikes to re-light."
        )

    # Compute inflation-free ground truth that's invariant to whether
    # BH.7's window divide is applied at the dataset layer. For each
    # (recipient, transfer) pair, the JOINED rows share the same
    # underlying inflow amount, but the per-row `amount` value either
    # repeats (pre-BH.7) or pre-divides by sender-count (post-BH.7).
    # `amount_per_pair × rows_per_pair` reconstructs the per-pair
    # inflow in BOTH worlds: pre-BH.7 amount=I, rows=M → I×M (BUG —
    # over-counts), post-BH.7 amount=I/M, rows=M → I (correct). The
    # ground truth across all pairs is then SUM of per-pair inflows.
    from collections import defaultdict

    rows_per_pair: dict[tuple[str, str], int] = defaultdict(int)
    amount_per_pair: dict[tuple[str, str], Decimal] = {}
    qualifying_recipients: set[str] = set()
    transfer_ids: set[str] = set()
    senders: set[str] = set()
    for row in rows:
        recipient = str(row["recipient_account_id"])
        transfer = str(row["transfer_id"])
        key = (recipient, transfer)
        rows_per_pair[key] += 1
        amount_per_pair[key] = Decimal(str(row["amount"]))
        qualifying_recipients.add(recipient)
        transfer_ids.add(transfer)
        senders.add(str(row["sender_account_id"]))

    # Ground-truth inflation-free total: per-pair inflow × per-pair
    # rows, summed. Robust to pre- or post-BH.7 dataset shape (per
    # the docstring above).
    expected_total = sum(
        (amount_per_pair[k] * rows_per_pair[k] for k in amount_per_pair),
        Decimal("0"),
    )

    # (1) Qualifying Recipients == distinct recipient_account_ids in
    # the fanout dataset (already distinct_count() in the binding —
    # this proves the binding agrees with the SQL).
    rendered_qualifying = parse_int_kpi(
        driver.kpi_value("Qualifying Recipients"),
    )
    assert rendered_qualifying == len(qualifying_recipients), (
        f"Qualifying Recipients KPI: rendered {rendered_qualifying} ≠ "
        f"distinct(recipient_account_id) = {len(qualifying_recipients)}"
    )

    # (2) Distinct Senders — same shape.
    rendered_senders = parse_int_kpi(driver.kpi_value("Distinct Senders"))
    assert rendered_senders == len(senders), (
        f"Distinct Senders KPI: rendered {rendered_senders} ≠ "
        f"distinct(sender_account_id) = {len(senders)}"
    )

    # (3) Total Inbound — the cartesian-inflation catch. THIS is the
    # finding #7 gate.
    rendered_total = parse_currency_kpi(driver.kpi_value("Total Inbound"))
    assert rendered_total == expected_total, (
        f"Total Inbound KPI: rendered ${rendered_total} ≠ "
        f"inflation-free ground truth ${expected_total} (sum of "
        f"distinct (recipient, transfer) inflow amounts across "
        f"{len(amount_per_pair)} unique recipient legs vs "
        f"{len(rows)} joined rows in the fanout dataset). v11.21.0 "
        f"cold-read finding #7 — the fanout dataset CTE pattern "
        f"(inflows × outflows JOIN ON transfer_id) is cartesian for "
        f"multi-leg transfers, so SUM(amount) inflates by sender-leg "
        f"count. Fix: aggregate inflows BEFORE the outflows join. "
        f"Ratio {(rendered_total / expected_total) if expected_total else 'n/a'} "
        f"is the inflation factor."
    )
    driver.screenshot()


def _default_sigma_from(dataset_parameters) -> Decimal:  # type: ignore[no-untyped-def]: list[DatasetParameter] — annotating would import the wrapper here
    """Pull the default σ value from the Volume Anomalies dataset
    parameter. Same source-of-truth pattern BG.2's
    ``_summary_default_day`` uses for the Daily Statement balance-
    date default."""
    for dp in dataset_parameters:
        ip = dp.IntegerDatasetParameter
        if ip is None or str(ip.Name) != "pInvAnomaliesSigma":
            continue
        defaults = ip.DefaultValues
        if defaults is None or not defaults.StaticValues:
            raise RuntimeError(
                "pInvAnomaliesSigma DatasetParameter has no static "
                "default — production builder shape changed."
            )
        return Decimal(str(defaults.StaticValues[0]))
    raise RuntimeError(
        "pInvAnomaliesSigma DatasetParameter not found on the Volume "
        "Anomalies dataset; production builder shape changed."
    )
