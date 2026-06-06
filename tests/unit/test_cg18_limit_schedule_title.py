"""CG.18 — `limit_schedule` card title drops the 3-segment
"Role::Rail::Direction" composite for a scannable shape.

Cold-read v4 P1 #1: limit_schedule still rendered the unscannable
composite ("DDAControl::CustomerOutboundACH::Outbound") as its h3
even though CG.12 had solved the analogous problem for chain. The
cold-read suggested title = role only, but multiple
limit_schedules can share the same parent_role (one role, many
rails, ± direction) so role-only would render N indistinguishable
cards.

Final shape: title = `{role} → {rail}` so each (role, rail) pair
is unique-on-scan; direction renders as a small `text-secondary-fg`
badge after the title (binary Inbound/Outbound, carries weight,
belongs in the title row but smaller). Composite still lives on
`data-entity-id` for URL/hx-target plumbing — same pattern CG.12
locked for chain.
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


def _h3_text_only(card_html: str) -> str:
    """Slice the <h3>...</h3> contents with all nested <span>s
    removed (so the test compares the title prose, not the badge
    chrome around it)."""
    h3_start = card_html.index("<h3")
    h3_open_end = card_html.index(">", h3_start) + 1
    h3_close = card_html.index("</h3>", h3_open_end)
    inner = card_html[h3_open_end:h3_close]
    while "<span" in inner:
        span_open = inner.index("<span")
        span_open_end = inner.index(">", span_open) + 1
        span_close = inner.index("</span>", span_open_end)
        inner = inner[:span_open] + inner[span_close + len("</span>"):]
    return inner.strip()


def test_limit_schedule_title_renders_role_then_rail(
    writable_l2_yaml: Path,
) -> None:
    """Title prose (with badges stripped) is `{role} → {rail}` —
    not the 3-segment "Role::Rail::Direction" composite, not just
    the role."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    ls = inst.limit_schedules[0]
    card = _render_read_card(
        "limit_schedule", ls, inst, demo_mode=False, collapsed=True,
    )
    title = _h3_text_only(card)
    assert title == f"{ls.parent_role} → {ls.rail}"
    assert "::" not in title


def test_limit_schedule_title_carries_direction_badge(
    writable_l2_yaml: Path,
) -> None:
    """Direction renders as a `data-role="card-direction-badge"`
    span inside the h3 (similar shape to the rail subtype badge +
    the account display-name badge from CG.11)."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    ls = inst.limit_schedules[0]
    card = _render_read_card(
        "limit_schedule", ls, inst, demo_mode=False, collapsed=True,
    )
    assert 'data-role="card-direction-badge"' in card
    assert str(ls.direction) in card


def test_limit_schedule_data_entity_id_keeps_composite(
    writable_l2_yaml: Path,
) -> None:
    """Same contract CG.12 locked for chain: title shrinks for the
    operator's eye but `data-entity-id` keeps the full composite key
    for URL/hx-target plumbing."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    ls = inst.limit_schedules[0]
    card = _render_read_card(
        "limit_schedule", ls, inst, demo_mode=False, collapsed=True,
    )
    composite = _entity_id("limit_schedule", ls)
    assert "::" in composite, "fixture entry should carry composite shape"
    assert f'data-entity-id="{composite}"' in card


def test_other_kinds_titles_not_affected(
    writable_l2_yaml: Path,
) -> None:
    """The direction badge + role-arrow-rail shape only fires for
    `limit_schedule`. Rails / accounts / chains keep their existing
    title shape (chain still parent-only, account still
    `<id> · <name>`)."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    rail = inst.rails[0]
    card = _render_read_card(
        "rail", rail, inst, demo_mode=False, collapsed=True,
    )
    assert 'data-role="card-direction-badge"' not in card


def test_list_page_no_composite_in_any_title(
    writable_l2_yaml: Path,
) -> None:
    """Integration: hit `/l2_shape/limit_schedule/` and inspect
    every limit_schedule card's h3. None should leak `::`."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/limit_schedule/").text
    cursor = 0
    title_count = 0
    while True:
        idx = body.find("<h3", cursor)
        if idx == -1:
            break
        h3_close = body.index("</h3>", idx)
        h3_block = body[idx:h3_close]
        # Only inspect h3s inside limit_schedule cards.
        details_start = body.rfind('data-kind="', 0, idx)
        if details_start == -1:
            cursor = h3_close
            continue
        kind_end = body.index('"', details_start + len('data-kind="'))
        kind = body[details_start + len('data-kind="'):kind_end]
        if kind != "limit_schedule":
            cursor = h3_close
            continue
        title_count += 1
        assert "::" not in h3_block, (
            f"limit_schedule card title leaks composite key: {h3_block!r}"
        )
        # Title text contains the arrow shape we expect.
        assert "→" in h3_block, (
            f"limit_schedule card h3 missing role → rail arrow: {h3_block!r}"
        )
        cursor = h3_close
    assert title_count > 0, (
        "expected at least one limit_schedule card on the page"
    )
