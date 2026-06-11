"""CG.16 — Chain card HTML `id` attribute and matching HTMX
`hx-target` selectors are CSS-safe.

Cold-read v4 P0 #3: a chain whose composite key is
"Parent::ChildA,ChildB,ChildC" emitted
`id="entity-chain-Parent__ChildA,ChildB,ChildC"` with matching
`hx-target="#entity-chain-Parent__ChildA,ChildB,ChildC"`. CSS treats
the comma as the selector-list separator, so that hx-target reads
as "id #entity-chain-Parent__ChildA OR descendant ChildB OR
descendant ChildC" — `querySelector` either lands on a different
DOM node or returns null. Delete on a multi-child chain is the
data-loss path.

Fix: `_html_id_slug` extends the existing `::` → `__` substitution
to also map `,` → `_C_`. `data-entity-id` keeps the raw composite
(URL-side addressing). Pin both the slug function and a behavioral
assertion that the rendered Delete button's `hx-target` matches the
card's `id` attribute character-for-character.
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
from recon_gen.common.html._studio_editor_routes import (
    _entity_id,
    _html_id_slug,
    _render_read_card,
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


# ---------------------------------------------------------------------------
# _html_id_slug round-trips
# ---------------------------------------------------------------------------

def test_slug_strips_double_colon() -> None:
    """`::` was already handled (CF.4-era). Pin the existing behavior
    so the comma extension doesn't regress it."""
    assert _html_id_slug("Foo::Bar") == "Foo__Bar"


def test_slug_strips_comma() -> None:
    """`,` → `_C_`. The new contract."""
    assert _html_id_slug("Foo,Bar,Baz") == "Foo_C_Bar_C_Baz"


def test_slug_handles_chain_composite_shape() -> None:
    """Real-world chain composite — both `::` and `,` present."""
    composite = "MerchantSettlementCycle::PayoutACH,PayoutCheck,PayoutWire"
    assert _html_id_slug(composite) == (
        "MerchantSettlementCycle__PayoutACH_C_PayoutCheck_C_PayoutWire"
    )


def test_slug_output_has_no_css_unsafe_chars() -> None:
    """The slug never contains `:` or `,` — the two characters that
    break CSS id selectors."""
    for sample in [
        "Foo::Bar",
        "Foo,Bar",
        "Foo::Bar,Baz",
        "DDAControl::CustomerOutboundACH::Outbound",  # limit_schedule shape
        "Solo",
    ]:
        slug = _html_id_slug(sample)
        assert ":" not in slug
        assert "," not in slug


# ---------------------------------------------------------------------------
# Rendered chain card — id and hx-target match
# ---------------------------------------------------------------------------

def test_chain_card_id_matches_delete_hx_target(
    writable_l2_yaml: Path,
) -> None:
    """For every chain in the fixture (including multi-child chains
    whose composite key carries commas), the rendered card's
    ``id="entity-chain-..."`` attribute must remain CSS-safe.

    BX.1 (2026-06-11): the Delete button no longer targets the card
    via ``hx-target="#<card-id>"`` — it targets the page-level
    ``#delete-confirm-banner-slot``. The CG.16 contract (no commas
    in the CSS-safe slug) still matters because OTHER hx-targets
    (the card body lazy-load, the editor's cascade-reload re-render)
    still address the card by id. Pin both invariants."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    multi_child_seen = False
    for chain in inst.chains:
        composite = _entity_id("chain", chain)
        # Render the card (not collapsed — we want the action buttons
        # in the summary visible).
        card = _render_read_card(
            "chain", chain, inst, collapsed=True,
        )
        # The card's id is the CSS-safe slug (no commas, no `::`).
        expected_slug = _html_id_slug(composite)
        assert f'id="entity-chain-{expected_slug}"' in card
        # BX.1 — Delete targets the banner slot, not the card.
        assert 'hx-target="#delete-confirm-banner-slot"' in card
        # Belt + suspenders: prove the slug itself is CSS-safe.
        assert "," not in expected_slug
        if "," in composite:
            multi_child_seen = True
    assert multi_child_seen, (
        "spec_example fixture should include at least one multi-child "
        "chain (composite key with commas) so this test exercises the "
        "real CG.16 contract"
    )


def test_chain_card_data_entity_id_keeps_raw_composite(
    writable_l2_yaml: Path,
) -> None:
    """The sanitization only touches the HTML `id` attribute. The
    `data-entity-id` attribute (consumed by URL builders + addressing
    code) keeps the raw composite — that's what server-side routes
    accept on the URL path."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    for chain in inst.chains:
        composite = _entity_id("chain", chain)
        card = _render_read_card(
            "chain", chain, inst, collapsed=True,
        )
        assert f'data-entity-id="{composite}"' in card


# ---------------------------------------------------------------------------
# Integration — the rendered home page survives commas
# ---------------------------------------------------------------------------

def test_home_chain_section_renders_safe_selectors(
    writable_l2_yaml: Path,
) -> None:
    """End-to-end: hit `/l2_shape/chain/` and assert no chain id /
    hx-target carries a literal comma. The cold-read v4 ran exactly
    this kind of HTML inspection."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/chain/").text
    cursor = 0
    while True:
        idx = body.find("entity-chain-", cursor)
        if idx == -1:
            break
        # Slug runs to the next " or whitespace or `;` etc.
        end = idx
        while end < len(body) and body[end] not in '"\' \n\t':
            end += 1
        slug_chunk = body[idx:end]
        assert "," not in slug_chunk, (
            f"chain id/hx-target leaks a comma: {slug_chunk!r}"
        )
        cursor = end
