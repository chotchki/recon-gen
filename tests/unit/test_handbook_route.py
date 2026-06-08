"""CN.5 — `?` help surface: route + Sheet.handbook_path wiring tests.

Three layers:

1. ``Sheet.handbook_path`` exists, defaults to ``None``, accepts a
   ``HandbookPath`` NewType value.
2. The Starlette ``GET /handbook/<path>`` route renders the .md file
   as HTML, returns 404 on missing files, and rejects path traversal.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from recon_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.ids import HandbookPath, SheetId
from recon_gen.common.tree import Sheet
from tests._test_helpers import make_test_config


def _client() -> TestClient:
    """A Starlette client wrapping a smoke-app deploy. The smoke
    dashboard satisfies make_app's "≥1 dashboard" requirement; only
    the ``/handbook/<path>`` route is exercised in this module."""
    cfg = make_test_config()
    tree_app, sheet = build_smoke_app(cfg)
    served = ServedDashboard(
        tree_app=tree_app, sheet=sheet, title="smoke",
        data_fetcher=stub_money_trail_fetcher,
        filter_specs=SMOKE_FILTER_SPECS,
    )
    app = make_app(dashboards={"smoke": served})
    return TestClient(app)  # pyright: ignore[reportArgumentType]: make_app returns Starlette, TestClient accepts ASGI apps


def test_sheet_accepts_handbook_path() -> None:
    sheet = Sheet(
        sheet_id=SheetId("test-sheet-x"),
        name="x", title="X",
        description="a test sheet",
        handbook_path=HandbookPath("l1/drift"),
    )
    assert sheet.handbook_path == "l1/drift"


def test_sheet_handbook_path_defaults_to_none() -> None:
    sheet = Sheet(
        sheet_id=SheetId("test-sheet-y"),
        name="y", title="Y",
        description="another test sheet",
    )
    assert sheet.handbook_path is None


def test_handbook_route_renders_existing_page() -> None:
    client = _client()
    res = client.get("/handbook/l1/drift")
    assert res.status_code == 200, res.text
    # H1 should land in the HTML — the page header is "Drift" but we
    # assert on a more page-specific phrase from the body so the test
    # doesn't drift with the L1 sheet's display name. The phrase below
    # comes from docs/handbook/l1/drift.md's first paragraph.
    assert "Sub-ledger drift" in res.text  # typing-smell: ignore[no-inline-production-constants]: the literal is a handbook prose fragment, not a code constant
    assert "<article" in res.text
    assert "handbook-page" in res.text


def test_handbook_route_renders_shared_page() -> None:
    """The shared App Info page lives under ``_shared/`` and is used by
    every dashboard's App Info sheet. Make sure the path resolves."""
    client = _client()
    res = client.get("/handbook/_shared/app-info")
    assert res.status_code == 200


def test_handbook_route_404s_on_missing() -> None:
    client = _client()
    res = client.get("/handbook/l1/nonexistent-sheet")
    assert res.status_code == 404


def test_handbook_route_rejects_path_traversal() -> None:
    """Defense against ``../etc/passwd`` style paths."""
    client = _client()
    res = client.get("/handbook/..%2Fetc%2Fpasswd")
    # 400 or 404 either means the operator can't reach files outside
    # docs/handbook/.
    assert res.status_code in (400, 404)
