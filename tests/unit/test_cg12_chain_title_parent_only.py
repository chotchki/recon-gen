"""CG.12 — Chain card titles render the parent only, not the
composite "Parent::children-csv" addressing key.

Cold-read v3 P1: the composite id is unscannable in a card title
("MerchantSettlementCycle::MerchantPayoutACH,MerchantPayoutCheck,..."
runs off the visible width). The body already carries a
`<dt>Children</dt>` row (FieldSpec.kind="chain_children",
label="Children"), so dropping the children from the title is pure
signal-to-noise.

The composite key still lives on `data-entity-id` for hx-target
plumbing + the URL pathway (`/l2_shape/chain/<composite>`); only the
visible title shrinks.
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


def _h3_text(card_html: str) -> str:
    """Slice the visible <h3>...</h3> contents (strip tags) from a
    rendered card."""
    h3_start = card_html.index("<h3")
    h3_open_end = card_html.index(">", h3_start) + 1
    h3_close = card_html.index("</h3>", h3_open_end)
    inner = card_html[h3_open_end:h3_close]
    # Strip any nested spans (display name / subtype badge).
    while "<span" in inner:
        span_open = inner.index("<span")
        span_open_end = inner.index(">", span_open) + 1
        span_close = inner.index("</span>", span_open_end)
        inner = inner[:span_open] + inner[span_close + len("</span>"):]
    return inner.strip()


# ---------------------------------------------------------------------------
# Chain title renders the parent only
# ---------------------------------------------------------------------------

def test_chain_card_title_renders_parent_only(
    writable_l2_yaml: Path,
) -> None:
    """The <h3> for a chain card carries the parent identifier — NOT
    the "parent::child,child,..." composite. Composite stays on
    `data-entity-id` for URL/hx-target addressing."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _entity_id, _render_read_card,
    )
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    chain = inst.chains[0]
    card = _render_read_card(
        "chain", chain, inst, collapsed=True,
    )
    composite_id = _entity_id("chain", chain)
    assert "::" in composite_id, "fixture chain should have composite id"
    title = _h3_text(card)
    assert title == str(chain.parent)
    # The composite key (the part with `::`) doesn't appear in the
    # visible title — that was the cold-read complaint.
    assert "::" not in title


def test_chain_card_data_entity_id_keeps_composite(
    writable_l2_yaml: Path,
) -> None:
    """The addressing key (URL pathway + hx-target) stays the full
    composite. CG.12 only affects what the operator SEES, not how the
    server addresses rows."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _entity_id, _render_read_card,
    )
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    chain = inst.chains[0]
    card = _render_read_card(
        "chain", chain, inst, collapsed=True,
    )
    composite = _entity_id("chain", chain)
    assert f'data-entity-id="{composite}"' in card


def test_non_chain_cards_still_render_full_entity_id(
    writable_l2_yaml: Path,
) -> None:
    """Other kinds' titles are unaffected — only chain shrinks."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _entity_id, _render_read_card,
    )
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    rail = inst.rails[0]
    card = _render_read_card(
        "rail", rail, inst, collapsed=True,
    )
    title = _h3_text(card)
    assert title == _entity_id("rail", rail)


def test_chain_list_page_titles_drop_composite(
    writable_l2_yaml: Path,
) -> None:
    """Integration: the rendered `/l2_shape/chain/` page has no
    `::child` text inside any visible <h3>."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/chain/").text
    # Pull every <h3>...</h3> and assert no composite leaks in.
    cursor = 0
    chain_h3_count = 0
    while True:
        idx = body.find("<h3", cursor)
        if idx == -1:
            break
        h3_close = body.index("</h3>", idx)
        h3_block = body[idx:h3_close]
        # Only inspect h3s inside chain cards — other surfaces (top-
        # nav, page header) carry their own h3s. Skip via the parent
        # data-kind marker: walk back to find the enclosing
        # `data-kind="..."`.
        details_start = body.rfind('data-kind="', 0, idx)
        if details_start == -1:
            cursor = h3_close
            continue
        kind_end = body.index('"', details_start + len('data-kind="'))
        kind = body[details_start + len('data-kind="'):kind_end]
        if kind != "chain":
            cursor = h3_close
            continue
        chain_h3_count += 1
        # The composite key uses `::` — must NOT appear in chain
        # card titles.
        assert "::" not in h3_block, (
            f"chain card title leaks composite key: {h3_block!r}"
        )
        cursor = h3_close
    assert chain_h3_count > 0, "expected at least one chain card on the page"
