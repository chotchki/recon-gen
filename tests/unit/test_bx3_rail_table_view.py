"""BX.3 — Rail list table view (session-only toggle, grouped by source_role).

Operator-locked: card grid stays the default; ``?view=table`` flips to
a source-role-grouped ``<table>``. Session-only (no .studio-state.yaml
persist) — a full reload without the query param resets to grid.

Anchors per memory ``feedback_browser_drivers_user_facing_locators``:
``data-view-toggle="grid"``/``"table"`` for the toggle chip,
``data-role="rail-list-table"`` / ``"rail-role-header"`` /
``"rail-role-section"`` / ``"rail-row"`` on the table markup.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from recon_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from recon_gen.common.html._studio_routes import make_studio_routes
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
from tests._test_helpers import make_test_config


REPO_ROOT = Path(__file__).parent.parent.parent
FIXTURES = REPO_ROOT / "tests" / "l2"


def _build_app(yaml_path: Path) -> object:
    """Studio app — mirrors test_cf4b_paginated_list_view._build_app."""
    cache = L2InstanceCache.from_path(yaml_path)
    cfg = make_test_config()
    tree_app, sheet = build_smoke_app(cfg)
    served = ServedDashboard(
        tree_app=tree_app, sheet=sheet, title="smoke",
        data_fetcher=stub_money_trail_fetcher,
        filter_specs=SMOKE_FILTER_SPECS,
    )
    return make_app(
        dashboards={"smoke": served},
        studio_routes=make_studio_routes(cache),
    )


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    """Copy spec_example.yaml to a tempfile so writes don't mutate the
    bundled fixture."""
    src = FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


# ---------------------------------------------------------------------------
# Default — card grid
# ---------------------------------------------------------------------------

def test_default_request_renders_card_grid(writable_l2_yaml: Path) -> None:
    """No ``?view=`` query param → card grid (the historical / locked
    default). The cards container carries ``data-view-mode="grid"`` and
    NO ``<table data-role="rail-list-table">`` markup."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/rail/").text

    assert 'data-view-mode="grid"' in body
    assert 'data-role="rail-list-table"' not in body
    # The card grid renders <article> read cards; presence proves the
    # card path didn't get accidentally dropped.
    assert '<article' in body
    assert 'data-kind="rail"' in body


def test_explicit_view_grid_renders_card_grid(writable_l2_yaml: Path) -> None:
    """``?view=grid`` (the explicit default form) renders the card grid
    just like the bare URL — proves the parser treats ``"grid"`` as
    grid, not as a typo that falls through to a default branch."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/rail/?view=grid").text

    assert 'data-view-mode="grid"' in body
    assert 'data-role="rail-list-table"' not in body


def test_garbage_view_param_falls_back_to_grid(writable_l2_yaml: Path) -> None:
    """``?view=garbage`` is not a 500 — falls back to grid silently
    (parser narrows the typed return to one of the two valid modes)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/rail/?view=garbage")

    assert resp.status_code == 200
    assert 'data-view-mode="grid"' in resp.text


# ---------------------------------------------------------------------------
# ?view=table — table markup
# ---------------------------------------------------------------------------

def test_view_table_renders_table_markup(writable_l2_yaml: Path) -> None:
    """``?view=table`` flips to the table render. The page carries the
    ``data-role="rail-list-table"`` anchor and the five column headers
    (Name, Source role, Destination role, Subtype, Badges)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/rail/?view=table").text

    assert 'data-view-mode="table"' in body
    assert 'data-role="rail-list-table"' in body
    # No card articles in table mode — the table is the entire body.
    assert 'data-kind="rail"' in body  # container still tagged
    # Column headers (operator-readable, not coerced from kebab).
    assert ">Name<" in body
    assert ">Source role<" in body
    assert ">Destination role<" in body
    assert ">Subtype<" in body
    assert ">Badges<" in body


def test_view_table_groups_by_source_role(writable_l2_yaml: Path) -> None:
    """The table emits one ``<tbody data-role="rail-role-section">``
    per distinct source_role group, headed by a
    ``<tr data-role="rail-role-header">`` separator carrying the
    group label. spec_example carries TwoLegRails with at least two
    distinct source roles (ExternalCounterparty, CustomerSubledger,
    NorthPool); single-leg rails collapse into the ``(single-leg)``
    bucket."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/rail/?view=table").text

    # At least one role header + section per group; spec_example
    # exercises both two-leg + single-leg, so we expect ≥2 sections.
    role_header_count = body.count('data-role="rail-role-header"')
    role_section_count = body.count('data-role="rail-role-section"')
    assert role_header_count >= 2
    assert role_section_count >= 2
    # Header count matches section count — one separator per group.
    assert role_header_count == role_section_count
    # Specific known source roles from spec_example surface as
    # ``data-source-role`` attributes on the headers.
    assert 'data-source-role="ExternalCounterparty"' in body
    assert 'data-source-role="(single-leg)"' in body


def test_view_table_rows_carry_entity_id(writable_l2_yaml: Path) -> None:
    """Each rail surfaces as a ``<tr data-role="rail-row">`` with
    ``data-entity-id`` set to the rail name (the addressing key the
    L2-shape lookup contract uses). The row's name cell links to the
    rail's read card."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/rail/?view=table").text

    # ≥ 1 rail row in the fixture.
    assert body.count('data-role="rail-row"') >= 1
    # Read-card link for at least one known rail.
    # spec_example carries an ExternalRailInbound rail; its name cell
    # links to /l2_shape/rail/ExternalRailInbound.
    assert 'href="/l2_shape/rail/ExternalRailInbound"' in body


# ---------------------------------------------------------------------------
# Toggle chip — data-view-toggle anchors
# ---------------------------------------------------------------------------

def test_toggle_chip_present_in_grid_view(writable_l2_yaml: Path) -> None:
    """In grid mode (default), the toggle chip renders BOTH anchors:
    Cards (active, aria-pressed=true) + Table (inactive)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/rail/").text

    assert 'data-role="rail-view-toggle"' in body
    assert 'data-view-toggle="grid"' in body
    assert 'data-view-toggle="table"' in body
    # Anchor ordering: the chip emits Cards then Table. The aria-pressed
    # attribute reflects the active mode.
    grid_idx = body.index('data-view-toggle="grid"')
    table_idx = body.index('data-view-toggle="table"')
    assert grid_idx < table_idx
    # Active = grid mode. Check the aria-pressed="true" appears AFTER
    # the grid anchor's data-view-toggle attribute AND before the next
    # one (table). The grid anchor's tag carries it.
    grid_anchor = body[grid_idx:table_idx]
    assert 'aria-pressed="true"' in grid_anchor


def test_toggle_chip_present_in_table_view(writable_l2_yaml: Path) -> None:
    """In table mode, both anchors still render; Table is active
    (aria-pressed=true), Cards is inactive."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/rail/?view=table").text

    assert 'data-view-toggle="grid"' in body
    assert 'data-view-toggle="table"' in body
    # Find the table anchor; its tag should carry aria-pressed=true.
    table_idx = body.index('data-view-toggle="table"')
    # Slice forward to the end of that anchor tag.
    end_of_anchor = body.index('>', table_idx)
    table_anchor = body[table_idx:end_of_anchor + 1]
    assert 'aria-pressed="true"' in table_anchor


def test_toggle_chip_grid_anchor_strips_view_param(
    writable_l2_yaml: Path,
) -> None:
    """The "Cards" anchor href is the bare list URL (no ``?view=``);
    the "Table" anchor href adds ``?view=table``. Session-only contract:
    the toggle controls the URL, nothing else; a fresh load with NO
    query string snaps back to grid."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/rail/?view=table").text

    # Both anchors render with explicit hrefs.
    assert 'href="/l2_shape/rail/"' in body  # the Cards anchor
    assert 'href="/l2_shape/rail/?view=table"' in body  # the Table anchor


# ---------------------------------------------------------------------------
# Non-rail kinds — toggle does NOT render
# ---------------------------------------------------------------------------

def test_toggle_chip_absent_on_account_list(writable_l2_yaml: Path) -> None:
    """BX.3 is a rail-only feature. Account list page (and every other
    non-rail kind) shows no toggle and ignores ``?view=table``."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/account/").text
        table_body = c.get("/l2_shape/account/?view=table").text

    assert 'data-role="rail-view-toggle"' not in body
    assert 'data-view-toggle="table"' not in body
    # Even with ?view=table, account list renders the card grid (no
    # rail table primitive applies to accounts).
    assert 'data-role="rail-list-table"' not in table_body


# ---------------------------------------------------------------------------
# Session-only — no .studio-state.yaml persist
# ---------------------------------------------------------------------------

def test_view_toggle_does_not_persist_to_studio_state(
    writable_l2_yaml: Path,
) -> None:
    """Operator lock: SESSION-only — the toggle controls the URL, NOT
    .studio-state.yaml. Hitting ``?view=table`` and then GET'ing the
    bare list URL returns the grid (no persistence). And no
    .studio-state.yaml file is written as a side-effect."""
    app = _build_app(writable_l2_yaml)
    state_file = writable_l2_yaml.parent / ".studio-state.yaml"
    pre_existed = state_file.exists()
    pre_mtime = state_file.stat().st_mtime if pre_existed else None
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        table_body = c.get("/l2_shape/rail/?view=table").text
        bare_body = c.get("/l2_shape/rail/").text

    assert 'data-view-mode="table"' in table_body
    # Bare URL after the table hit still renders grid — no persistence.
    assert 'data-view-mode="grid"' in bare_body
    # .studio-state.yaml either still doesn't exist OR (if pre-existed)
    # its mtime is unchanged — the toggle wrote nothing.
    if not pre_existed:
        assert not state_file.exists()
    else:
        assert state_file.stat().st_mtime == pre_mtime


# ---------------------------------------------------------------------------
# Embed mode (home-page section) — toggle still wires
# ---------------------------------------------------------------------------

def test_view_table_in_embed_mode(writable_l2_yaml: Path) -> None:
    """``?embed=1&view=table`` returns the table fragment (no <html>);
    the home-page section embed inherits the table view when the
    operator drills in. Both the table markup and the toggle anchors
    render."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/rail/?embed=1&view=table").text

    assert "<html" not in body
    assert 'data-role="rail-list-table"' in body
    assert 'data-view-toggle="table"' in body
