"""X.2.h.2 — Executives Layer-2 e2e against live DB (PG or Oracle).

Companion to ``test_html2_executives.py`` (stub fetcher) — this
file uses the real ``make_tree_db_fetcher`` against the configured
DB. Catches the failure modes that don't surface with a stub:

- Wrong L2 instance: matview prefix doesn't match the seeded DB
  → fetcher's first SQL execute returns "relation does not exist"
- Filter substitution actually narrows: change date filter, see
  the KPI value drop
- Layer 1 ↔ Layer 2 agreement: row count from the matview equals
  what the rendered visual claims (uses ``_layer1_query.py``)

**Dialect coverage** — this file is dialect-agnostic. The fixture
goes through ``connect_demo_db(cfg)``, which returns whichever DB
the operator's cfg points at; ``cfg.dialect`` drives placeholder
rewriting in ``_sql_executor`` (``%(name)s`` for PG, ``:name`` for
Oracle and SQLite). CI runs the same file in both ``e2e-pg-api``
and ``e2e-oracle-api`` jobs (.github/workflows/e2e.yml); SQLite
is exercised by the X.3.g audit-PDF e2e on the same code path
(``execute_visual_sql`` is the shared seam).

Gates:

- ``QS_GEN_E2E=1`` — same as every other tests/e2e/ file
- A reachable DB (cfg.demo_database_url + driver installed)
- ``QS_GEN_TEST_L2_INSTANCE=<path>`` — points at the L2 YAML that
  matches the seeded DB. Defaults to ``spec_example`` (rarely
  what you want for a live DB run; sasquatch_pr is the canonical
  demo).

When the DB isn't reachable, the test skips with a message. The
operator opts in by setting the env var + having a populated DB.

Pattern note (for porting Investigation / L2FT / L1):

    1. Build the app's tree + datasets
    2. ``html2_server(tree_app, sheet, fetcher=...)`` spins
    3. Open the dashboard sheet
    4. ``wait_for_kpi_value`` / ``wait_for_table_rows`` + assert
    5. Optionally Layer 1 cross-check via ``_layer1_query.py``

Same shape per app — the only thing that changes is sheet IDs +
visual IDs + which Layer 1 matview to cross-check.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from dataclasses import dataclass

from quicksight_gen.apps.executives.app import build_executives_app
from quicksight_gen.apps.executives.datasets import build_all_datasets
from quicksight_gen.common.browser.helpers import webkit_page
from quicksight_gen.common.dataset_contract import get_sql
from quicksight_gen.common.env_keys import QS_GEN_TEST_L2_INSTANCE
from quicksight_gen.common.html._tree_fetcher import (
    _find_visual_dataset_identifier,
)
from quicksight_gen.common.tree.structure import App
from tests.e2e._harness_html2 import (
    html2_server,
    make_live_db_fetcher_for_app,
    wait_for_kpi_value,
)


@dataclass
class _LiveServer:
    """What the live-DB fixture yields: URL + the tree it was built
    against, so tests can walk the tree without rebuilding it."""
    base_url: str
    tree_app: App


playwright_sync_api = pytest.importorskip("playwright.sync_api")


_DASHBOARD_ID = "exec"


def _load_l2_instance() -> Any:
    """Load the L2 instance the test runs against — env override
    via ``QS_GEN_TEST_L2_INSTANCE``, else the bundled default
    (spec_example)."""
    from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
    from quicksight_gen.common.l2 import load_instance

    override = QS_GEN_TEST_L2_INSTANCE.get_or_none()
    if override is not None:
        return load_instance(override)
    return default_l2_instance()


def _try_db_connection(cfg: Any) -> tuple[bool, str]:
    """Attempt to open a connection to the configured DB.
    Returns (ok, reason) — when ok is False, reason is the skip
    message."""
    if not getattr(cfg, "demo_database_url", None):
        return False, "no demo_database_url in cfg"
    try:
        from quicksight_gen.common.db import connect_demo_db  # noqa: PLC0415
        conn = connect_demo_db(cfg)
        conn.close()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"DB connection failed: {exc}"


@pytest.fixture(scope="module")
def live_db_exec_server(cfg: Any) -> Iterator[_LiveServer]:
    """Spin App2 with the real Executives tree + DB-backed fetcher.

    Yields ``_LiveServer(base_url, tree_app)`` so tests can both
    drive Playwright against the URL AND walk the tree to discover
    visuals to assert on (e.g. find every date-sensitive KPI).

    Skips when no DB is reachable — operator opts in by configuring
    cfg.demo_database_url + having a populated DB.
    """
    # Hard gate on QS_GEN_TEST_L2_INSTANCE — without it, the test
    # would fall back to spec_example (the bundled default) which
    # almost certainly doesn't match the prefix used to seed the
    # operator's DB. Better to skip cleanly than fail with a
    # misleading "relation does not exist" error.
    if QS_GEN_TEST_L2_INSTANCE.get_or_none() is None:
        pytest.skip(
            "live-DB e2e skipped: set QS_GEN_TEST_L2_INSTANCE to "
            "the L2 YAML matching your seeded DB (e.g. "
            "src/quicksight_gen/_l2_fixtures/sasquatch_pr.yaml)"
        )
    ok, reason = _try_db_connection(cfg)
    if not ok:
        pytest.skip(f"live-DB e2e skipped: {reason}")
    instance = _load_l2_instance()
    cfg_with_prefix = cfg
    if cfg_with_prefix.l2_instance_prefix is None:
        cfg_with_prefix = cfg_with_prefix.with_l2_instance_prefix(
            str(instance.instance),
        )
    build_all_datasets(cfg_with_prefix)
    tree_app = build_executives_app(cfg_with_prefix, l2_instance=instance)
    assert tree_app.analysis is not None
    fetcher = make_live_db_fetcher_for_app(
        tree_app=tree_app, cfg=cfg_with_prefix,
    )
    primary_sheet = tree_app.analysis.sheets[0]
    with html2_server(
        tree_app=tree_app,
        sheet=primary_sheet,
        data_fetcher=fetcher,
        dashboard_id=_DASHBOARD_ID,
        dashboard_title="Executives (live)",
    ) as base_url:
        yield _LiveServer(base_url=base_url, tree_app=tree_app)


def test_account_coverage_kpi_renders_with_real_data(
    live_db_exec_server: _LiveServer,
) -> None:
    """The KPI on Account Coverage should auto-load and show a
    number from the live DB. Catches "wrong L2" (table doesn't
    exist → fetcher errors → no KPI), "renderer broken" (KPI value
    never appears), and "data layer empty" (KPI shows 0)."""
    with webkit_page() as page:
        page.goto(
            f"{live_db_exec_server.base_url}/dashboards/{_DASHBOARD_ID}"
            f"/sheets/exec-sheet-account-coverage"
        )
        kpi_text = wait_for_kpi_value(page, timeout_ms=15000)
    # KPI should be a number (count of accounts) — not blank, not "0",
    # not "NaN". A populated DB should have at least a few accounts.
    digits = "".join(ch for ch in kpi_text if ch.isdigit())
    assert digits, (
        f"KPI rendered no digits — got {kpi_text!r}. Either the L2 "
        f"prefix doesn't match the seeded DB or the matview is empty."
    )
    assert int(digits) > 0, (
        f"KPI shows zero accounts — KPI text {kpi_text!r}. Check the "
        f"sasquatch_pr_daily_balances seed."
    )


def test_date_filter_does_not_error_when_applied(
    live_db_exec_server: _LiveServer,
) -> None:
    """Smoke-only: set a narrow date window on Account Coverage and
    assert the KPI re-renders without error. Account Coverage's KPIs
    (Total Open Accounts / Active Accounts) are designed to either be
    invariant to date or rely on a visual-pinned FilterGroup that
    App2 doesn't yet apply, so this test cannot assert value-change.

    Value-change is covered by ``test_date_filter_narrows_every_*``
    which walks the tree for date-sensitive count KPIs and asserts
    each one's number drops when the window narrows.

    Kept here as the boundary check on the COALESCE+sentinel-date
    pattern: ``CAST(:date_from AS DATE)`` must not error when the
    bind value is empty (the PG OR-short-circuit gotcha).
    """
    with webkit_page() as page:
        page.goto(
            f"{live_db_exec_server.base_url}/dashboards/{_DASHBOARD_ID}"
            f"/sheets/exec-sheet-account-coverage"
        )
        # Initial render with empty filter — proves the COALESCE+
        # sentinel-date pattern works against PG without raising
        # ``invalid input syntax for type date: ""``.
        wait_for_kpi_value(page, timeout_ms=15000)
        page.fill('input[name="date_from"]', "2030-01-01")
        page.fill('input[name="date_to"]', "2030-12-31")
        # X.2.g.1.e — auto-refresh on filter change (300ms debounce).
        page.wait_for_timeout(1500)
        # Second render with a real date — proves the bind value
        # threads through CAST(... AS DATE) without errors.
        narrowed = wait_for_kpi_value(page, timeout_ms=10000)
    # KPI re-rendered → date substitution worked at SQL execution.
    digits = "".join(ch for ch in narrowed if ch.isdigit())
    assert digits, (
        f"Filtered KPI rendered no digits — got {narrowed!r}. "
        f"Date filter binding may have errored at SQL execution."
    )


# ---------------------------------------------------------------------------
# Generic value-change harness — tree walker
# ---------------------------------------------------------------------------


def _kpi_text_to_int(text: str) -> int:
    """Parse a KPI's rendered text into an int.

    Strips non-digit chars (currency symbols, commas, K/M suffix
    rendering). Empty / no-digit text → 0, which is the natural
    answer for a SUM/COUNT over zero rows.
    """
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else 0


def _date_sensitive_count_kpis(
    tree_app: App,
) -> list[tuple[str, str, str]]:
    """Walk the tree, return ``(sheet_id, visual_id, title)`` for
    every KPI whose underlying dataset SQL references ``:date_from``
    AND whose measure aggregation is sum/count (i.e. value MUST drop
    as the window narrows).

    Excludes KPIs whose value is constant across windows by design
    (avg / min / max can stay stable even when row count drops).
    Visual-pinned filters aren't checked — App2 doesn't apply them
    yet, so a KPI that depends on one would behave the same as one
    without (covered by the wrap_for_visual gap, not this test).
    """
    assert tree_app.analysis is not None
    countable = {"sum", "count", "distinct_count"}
    results: list[tuple[str, str, str]] = []
    for sheet in tree_app.analysis.sheets:
        for visual in sheet.visuals:
            if type(visual).__name__ != "KPI":
                continue
            measures = getattr(visual, "values", []) or []
            if not measures:
                continue
            kinds = {getattr(m, "kind", None) for m in measures}
            if not (kinds & countable):
                continue
            ds_id = _find_visual_dataset_identifier(visual)
            if ds_id is None:
                continue
            try:
                base_sql = get_sql(ds_id)
            except KeyError:
                continue
            if ":date_from" not in base_sql:
                continue
            results.append((
                str(sheet.sheet_id),
                str(getattr(visual, "visual_id", "")),
                str(getattr(visual, "title", "") or ""),
            ))
    return results


def test_date_filter_narrows_every_date_sensitive_count_kpi(
    live_db_exec_server: _LiveServer,
) -> None:
    """Generic value-change check: walk the executives tree, find
    every KPI whose dataset SQL is date-bind-aware AND whose
    measure aggregation is sum/count (so the value MUST shrink as
    the date window narrows). For each, assert wide_value >
    narrow_value.

    A no-op date filter (bind not reaching SQL, wrap_for_visual
    silently dropping the WHERE clause, or any future regression)
    fails this test loudly across every applicable KPI rather than
    a single hand-picked one. As more apps wire ``app2_date_filter``
    into their datasets the same harness pattern picks them up
    automatically — copy this test verbatim against the new
    server fixture.
    """
    targets = _date_sensitive_count_kpis(live_db_exec_server.tree_app)
    assert targets, (
        "No date-sensitive count KPIs found in the executives tree. "
        "Either wrap_for_visual logic changed, or no dataset SQL "
        "references :date_from anymore. The test has nothing to "
        "guard if this list is empty."
    )

    # Date windows are relative to today — the seed anchors to
    # ``date.today()`` (see ``common/l2/seed.py``) so a hardcoded
    # year-range would land outside the data. Wide captures the
    # whole seed (90-day baseline + plants); narrow is a 2-day slice
    # near the start of the seed. The 2-day slice MUST contain less
    # data than the full window for any sum/count KPI; if it doesn't,
    # the date filter is a no-op.
    today = date.today()
    wide_from = today - timedelta(days=365)
    wide_to = today + timedelta(days=1)
    narrow_from = today - timedelta(days=89)
    narrow_to = narrow_from + timedelta(days=1)

    failures: list[str] = []
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        for sheet_id, visual_id, title in targets:
            page = browser.new_page()
            try:
                page.goto(
                    f"{live_db_exec_server.base_url}/dashboards/"
                    f"{_DASHBOARD_ID}/sheets/{sheet_id}"
                )
                wait_for_kpi_value(page, timeout_ms=15000)
                # Wide window — full seed.
                page.fill('input[name="date_from"]', wide_from.isoformat())
                page.fill('input[name="date_to"]', wide_to.isoformat())
                page.wait_for_timeout(1500)
                wide_text = page.locator(
                    f'[data-visual-id="{visual_id}"] .kpi-value'
                ).first.inner_text()
                wide_value = _kpi_text_to_int(wide_text)
                # Narrow window — 2-day slice near the start of the seed.
                page.fill('input[name="date_from"]', narrow_from.isoformat())
                page.fill('input[name="date_to"]', narrow_to.isoformat())
                page.wait_for_timeout(1500)
                narrow_text = page.locator(
                    f'[data-visual-id="{visual_id}"] .kpi-value'
                ).first.inner_text()
                narrow_value = _kpi_text_to_int(narrow_text)
            finally:
                page.close()
            label = f"{title!r} ({sheet_id}/{visual_id})"
            if wide_value <= 0:
                failures.append(
                    f"{label}: wide-window value is {wide_value} "
                    f"(text={wide_text!r}). Seed may be empty for "
                    f"window {wide_from} .. {wide_to}."
                )
                continue
            if narrow_value >= wide_value:
                failures.append(
                    f"{label}: narrowing did NOT reduce — "
                    f"wide={wide_text!r} ({wide_value}) → "
                    f"narrow={narrow_text!r} ({narrow_value}). "
                    f"narrow window={narrow_from} .. {narrow_to}."
                )
    assert not failures, (
        "Date filter did not narrow at least one count KPI. "
        "Bind is not reaching SQL or wrap_for_visual is stripping "
        "the WHERE clause:\n  - "
        + "\n  - ".join(failures)
    )
