"""CG.6 — dedicated `/l2_shape/<kind>/` pages wear the trainer-style
header strip (h1 + 1-sentence blurb on a white `border-b` strip).

The strip lands between the shared top-nav and the search input.
Embed mode (`?embed=1`) still emits the cards-only fragment — no
header strip — because the home page wraps each section in its own
chrome.
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
    src = FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


# Per-kind expectations: (URL kind segment, h1 text, a phrase from the blurb).
#
# BX.6/11 (2026-06-11) — Account + AccountTemplate h1s rebrand to
# the role-cardinality form. URLs unchanged.
_PAGES = [
    ("account", "Roles — 1:1", "ledger position"),
    ("account_template", "Roles — 1:N", "materialize"),
    ("rail", "Rails", "money-movement leg"),
    ("transfer_template", "Transfer templates", "two or more rails"),
    ("chain", "Chains", "Parent"),
    ("limit_schedule", "Limit schedules", "limit windows"),
]


@pytest.mark.parametrize(("kind", "h1_text", "blurb_phrase"), _PAGES)
def test_dedicated_page_has_trainer_style_header(
    writable_l2_yaml: Path, kind: str, h1_text: str, blurb_phrase: str,
) -> None:
    """`/l2_shape/<kind>/` carries a `<header>` strip with h1
    (operator-readable plural label) + blurb (one-sentence anchor)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(f"/l2_shape/{kind}/").text
    # Trainer-style header strip shape (`px-8 py-4 border-b ... bg-white`).
    assert "px-8 py-4 border-b" in body
    # h1 — operator-readable plural label, NOT the kind enum value
    # ("Accounts" not "account"; "Account templates" not
    # "account_template").
    assert f">{h1_text}<" in body
    assert blurb_phrase in body
    # The kind's underscore enum value doesn't leak into the title.
    if "_" in kind:
        assert f">{kind}<" not in body, (
            f"page leaked underscored kind enum {kind!r} into a tag"
        )


def test_embed_mode_skips_header_strip(writable_l2_yaml: Path) -> None:
    """`?embed=1` is the home-page section fragment — the home
    wraps each section in its OWN chrome, so the embed body must
    NOT include the trainer-style header (it'd render twice).
    (Per-card `<header>` elements stay — those are the title-row
    inside each card, scoped to the card, not the page.)"""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        embed = c.get("/l2_shape/rail/?embed=1").text
    # Trainer-style strip is the `px-8 py-4 border-b` shape — pin
    # by that class set so we don't collide with per-card headers.
    assert "px-8 py-4 border-b" not in embed
    assert "<h1" not in embed
    assert ">Rails<" not in embed


def test_dedicated_page_header_above_search(
    writable_l2_yaml: Path,
) -> None:
    """Header strip lands between the top-nav and the search form,
    not below the cards. Pins the visual ordering (operator scans
    top-down: nav → context → search → cards → pager)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/rail/").text
    h1_idx = body.index(">Rails<")
    search_idx = body.index('data-role="list-search"')
    grid_idx = body.index('id="entity-list"')
    pager_idx = body.index('data-role="list-pager"')
    assert h1_idx < search_idx < grid_idx < pager_idx, (
        "page order broken: expected header < search < cards < pager"
    )
