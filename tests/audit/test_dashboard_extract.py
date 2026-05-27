"""Tests for the L1 dashboard row-count extractor (U.8.b.2).

The function itself talks to a live QuickSight dashboard via
Playwright, so the full integration test (open dashboard, apply
period filter, count rows) lives in U.8.b.3 alongside the
end-to-end fixture that deploys the L1 app + seeds the DB.

This file holds the cheap structural checks — no browser, no AWS,
no DB — that catch the most common ways the extractor can drift:
a typoed sheet name or visual title in the layout map. We walk
the live L1 dashboard tree and confirm every (sheet_name,
table_title) the extractor would target actually exists. If the
L1 app renames a sheet or visual, this test fails immediately at
test-collect time rather than waiting for a slow browser run to
hit a "no visual with title 'X'" assertion deep in CI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator

import pytest

from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX
from tests.audit._dashboard_extract import (
    _DASHBOARD_LAYOUT,
    L1Invariant,
    count_l1_invariant_rows,
)

if TYPE_CHECKING:
    # BE.7.C.2 — type-only import. App lives in recon_gen.common.tree
    # which transitively pulls a heavy subtree; defer the import to
    # type-check time so test collection stays cheap.
    from recon_gen.common.tree import App


@pytest.fixture(scope="module", autouse=True)
def _cfg_env(monkeypatch_module: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]: autouse pytest fixture
    """Stamp the cfg-shaped env vars `load_config(None)` needs in the
    `l1_app` fixture below. Module-scoped so the env doesn't leak into
    other test modules (pre-Z.C.7 this was module-level
    `os.environ.setdefault`, which polluted `tests/unit/test_config_loader.py`).
    """
    monkeypatch_module.setenv("RECON_GEN_AWS_ACCOUNT_ID", "111122223333")
    monkeypatch_module.setenv("RECON_GEN_AWS_REGION", "us-west-2")
    monkeypatch_module.setenv(
        "RECON_GEN_DATASOURCE_ARN",
        "arn:aws:quicksight:us-west-2:111122223333:datasource/ds",
    )
    monkeypatch_module.setenv("RECON_GEN_DEPLOYMENT_NAME", DEFAULT_PREFIX)
    monkeypatch_module.setenv("RECON_GEN_DB_TABLE_PREFIX", DEFAULT_PREFIX)


@pytest.fixture(scope="module")
def monkeypatch_module() -> Iterator[pytest.MonkeyPatch]:
    """Module-scoped monkeypatch — pytest's built-in is function-scoped."""
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def l1_app() -> "App":
    """Build + emit the default L1 dashboard tree.

    Pure-Python — no AWS calls. The tree is the source of truth for
    sheet names and visual titles; we walk it to validate the
    extractor's hand-maintained layout map.
    """
    from recon_gen.common.config import load_config
    from recon_gen.apps.l1_dashboard.app import build_l1_dashboard_app

    cfg = load_config(None)
    app = build_l1_dashboard_app(cfg)
    app.emit_analysis()
    return app


@pytest.fixture(scope="module")
def sheet_visual_titles(l1_app: "App") -> dict[str, set[str]]:
    """Map sheet name → set of visual titles on that sheet."""
    out: dict[str, set[str]] = {}
    # `l1_app` fixture above calls `emit_analysis()`, which sets the
    # analysis attribute — never None here. Asserting narrows for
    # pyright (App.analysis is declared Optional).
    assert l1_app.analysis is not None
    for sheet in l1_app.analysis.sheets:
        out[sheet.name] = {
            # VisualLike Protocol doesn't declare `title`; concrete
            # subtypes (KPI/Table/BarChart/...) all do. The
            # `getattr(..., None)` guard handles any Protocol-impl
            # that genuinely lacks one.
            getattr(v, "title")
            for v in sheet.visuals if getattr(v, "title", None)
        }
    return out


def test_layout_covers_every_invariant() -> None:
    """Every L1Invariant Literal value has a layout entry."""
    expected: set[L1Invariant] = {
        "drift", "overdraft", "limit_breach",
        "stuck_pending", "stuck_unbundled", "supersession",
    }
    assert set(_DASHBOARD_LAYOUT.keys()) == expected


@pytest.mark.parametrize("invariant", list(_DASHBOARD_LAYOUT.keys()))
def test_layout_sheet_exists_on_l1_dashboard(
    sheet_visual_titles: dict[str, set[str]], invariant: L1Invariant,
) -> None:
    """Each layout entry's sheet name matches a real L1 sheet."""
    sheet_name, _, _ = _DASHBOARD_LAYOUT[invariant]
    assert sheet_name in sheet_visual_titles, (
        f"L1 dashboard has no sheet named {sheet_name!r}; layout "
        f"map for {invariant!r} is stale. Live sheets: "
        f"{sorted(sheet_visual_titles)}"
    )


@pytest.mark.parametrize("invariant", list(_DASHBOARD_LAYOUT.keys()))
def test_layout_visual_title_exists_on_sheet(
    sheet_visual_titles: dict[str, set[str]], invariant: L1Invariant,
) -> None:
    """Each layout entry's table title matches a real visual on
    the named sheet."""
    sheet_name, table_title, _ = _DASHBOARD_LAYOUT[invariant]
    titles_on_sheet = sheet_visual_titles.get(sheet_name, set())
    assert table_title in titles_on_sheet, (
        f"Sheet {sheet_name!r} has no visual titled {table_title!r}; "
        f"layout map for {invariant!r} is stale. Sheet's visuals: "
        f"{sorted(titles_on_sheet)}"
    )


def test_count_l1_invariant_rows_is_callable() -> None:
    """Smoke-import the extractor entry point — catches signature
    drift without spinning up a browser."""
    assert callable(count_l1_invariant_rows)
