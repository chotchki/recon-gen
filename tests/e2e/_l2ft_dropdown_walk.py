"""Shared driver-based walk for the L2FT ``*_dropdowns`` browser tests.

Each L2FT sheet (Rails / Chains / Transfer Templates) carries 1-2
``ParameterDropDownControl``s that narrow a results table. Pre-X.1.g
those were ``CategoryFilter(values=[], FILTER_ALL_VALUES)`` which forced
QS to lazy-fetch the column's distinct values from the
``tenK-sample-values-V2`` endpoint at first render — that endpoint 404s
on cold per-CI-run dashboards. X.1.g swapped each to a StaticValues
``ParameterDropdown`` (option lists known at deploy time, no runtime
fetch); Y.2.c then moved the narrowing from an analysis-level
``CategoryFilter`` into the dataset SQL (multi-valued ``<<$param>>``
substitution). Either way the observable behaviour this guards: picking
any advertised value narrows the table without emptying it.

Ported onto the ``DashboardDriver`` protocol (X.2.q.3) — no Playwright
in the test bodies or here; the driver handles QS's quirks.

Data-agnostic per the no-hardcoded-data rule: we read the dropdown's
*advertised* options (data-derived) and assert every one of them, when
picked alone, leaves the table non-empty. That catches three failure
modes that all surface the same operator symptom (analyst picks an
advertised value → table goes blank):

1. **Stale enum** — a hardcoded value (``transaction_status_values()``
   etc.) that no seed row carries → dead-end pick.
2. **New YAML value** — a Rail / Chain / Template added to the L2 YAML
   without a matching seed plant → empty narrowing.
3. **Data seeding bug** — a declared value the seed *should* produce
   rows for but doesn't (baseline-generator skip, plant misconfig).
"""

from __future__ import annotations

import pytest

from tests.e2e._drivers import DashboardDriver


def walk_dropdown(
    driver: DashboardDriver,
    *,
    sheet_label: str,
    dropdown_title: str,
    table_title: str,
) -> None:
    """For each advertised option of ``dropdown_title`` on the current
    sheet, pick only that value and assert ``table_title`` keeps > 0
    rows. ``pytest.skip`` if the dropdown is empty, or if the table
    starts empty (the deployed L2 has nothing in that sheet's matview to
    exercise — the empty-sheet render is covered by the render tests)."""
    options = driver.filter_options(dropdown_title)
    if not options:
        pytest.skip(
            f"{dropdown_title!r} dropdown empty on the deployed L2 — the "
            f"pick-and-narrow guard has nothing to exercise."
        )
    driver.wait_loaded(table_title)
    before = len(driver.table_rows(table_title))
    if before <= 0:
        pytest.skip(
            f"{table_title!r} table starts empty — the {sheet_label} sheet "
            f"has no matview rows for the deployed L2 to exercise the "
            f"dropdown narrowing against (the empty-sheet render is covered "
            f"by the render tests)."
        )

    failures: list[str] = []
    for option in options:
        # ``pick_filter`` blocks until the dataset re-query lands (the QS
        # driver waits for the table to stop mutating), so the read below
        # sees the post-filter state, not the spinner gap.
        driver.pick_filter(dropdown_title, [option])
        if len(driver.table_rows(table_title)) <= 0:
            failures.append(option)

    assert not failures, (
        f"{table_title!r} went empty after picking these "
        f"{dropdown_title!r} values: {failures}. Either the dropdown "
        f"advertises an option with no matching seed data (stale enum / "
        f"new YAML value missing plants / data seeding bug) or the X.1.g "
        f"param-bound narrowing regressed."
    )
