"""CF.4.e/f/g — Studio-Medium followups from the CF audit.

(e) Editor home gets an `<h1>L2 Editor</h1>` + purpose blurb under
    `<section id="home-intro">` so the first paint announces the
    surface. No top-level + Add CTA (per-section affordances stay
    the entry).

(f) Read-card Edit + Delete promoted from bare text links to typed
    button vocabulary: ghost-outline for Edit, danger-solid for
    Delete. Marks Delete as destructive at a glance instead of
    blending into the prose.

(g) `FieldSpec` gains `render_as: RenderAs` so list-of-id-string
    fields like `TransferTemplate.leg_rails` and
    `Account.bundles_activity` render as a flex-wrap of `<span>`
    chips with `break-keep`, instead of underscored ids splitting
    mid-token in the read card's value column.
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


# ---------------------------------------------------------------------------
# CF.4.e — editor home h1 + purpose blurb
# ---------------------------------------------------------------------------

def test_editor_home_has_h1_announcement(
    writable_l2_yaml: Path,
) -> None:
    """The home URL renders `<section id="home-intro">` carrying an
    `<h1>L2 Editor</h1>`. The blurb names every entity kind, which is
    the operator's anchor for what "L2" actually means."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    assert 'id="home-intro"' in body
    # `<h1 class="...">\n  L2 Editor\n  </h1>` — strip whitespace to
    # be resilient to indent.
    assert "<h1" in body
    assert "L2 Editor" in body
    # Blurb names the entity kinds the operator is about to see.
    for kind in ("accounts", "rails", "transfer templates", "chains"):
        assert kind in body.lower()


def test_editor_home_no_top_level_add_cta(
    writable_l2_yaml: Path,
) -> None:
    """No top-level + Add CTA — per-section + Add stays the entry
    point. The home-intro section's blurb references the per-section
    affordance descriptively but doesn't emit one."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    intro_start = body.index('id="home-intro"')
    intro_end = body.index("</section>", intro_start)
    intro = body[intro_start:intro_end]
    # The intro section itself has no anchor/button targeting `/new`.
    assert "/new" not in intro
    assert "+ Add" not in intro


# ---------------------------------------------------------------------------
# CF.4.f — Edit/Delete button vocabulary
# ---------------------------------------------------------------------------

def test_edit_button_is_ghost_outline(writable_l2_yaml: Path) -> None:
    """Edit promoted from bare text link to ghost-outline button:
    accent border + accent text + rounded + hover fill."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_read_card,
    )
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    rail = inst.rails[0]
    card = _render_read_card("rail", rail, inst, collapsed=False)
    # Ghost-outline carries an accent border + accent text + rounded.
    assert "border-accent" in card
    assert "text-accent" in card
    assert "rounded-sm" in card
    # Edit anchor is rendered as a button (inline-flex), not bare prose.
    assert "inline-flex" in card
    # Stays an <a> (left-clicks to edit URL) but no longer underlined.
    assert "no-underline" in card


def test_delete_button_is_danger_solid(writable_l2_yaml: Path) -> None:
    """Delete promoted to danger-solid: red border/text, hover fill.
    Operator sees destructive intent before clicking, not after."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_read_card,
    )
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    rail = inst.rails[0]
    card = _render_read_card("rail", rail, inst, collapsed=False)
    # Delete carries the danger token (red).
    assert "border-danger" in card
    assert "text-danger" in card
    # Still wires the htmx delete via hx-delete + hx-confirm guard.
    assert "hx-delete=" in card
    assert "hx-confirm=" in card


def test_delete_visually_distinct_from_edit(
    writable_l2_yaml: Path,
) -> None:
    """Edit + Delete don't share the same class set — Delete uses
    `danger`, Edit uses `accent`. Pin both so a future shared
    `Button` primitive (CI.3 followup) doesn't accidentally collapse
    them."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_read_card,
    )
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    rail = inst.rails[0]
    card = _render_read_card("rail", rail, inst, collapsed=False)
    # The two tokens coexist; neither bleeds into the other's anchor.
    edit_idx = card.index(">Edit<")
    delete_idx = card.index(">Delete<")
    edit_anchor_start = card.rfind("<a", 0, edit_idx)
    delete_anchor_start = card.rfind("<a", 0, delete_idx)
    edit_anchor = card[edit_anchor_start:edit_idx]
    delete_anchor = card[delete_anchor_start:delete_idx]
    assert "border-accent" in edit_anchor
    assert "border-danger" not in edit_anchor
    assert "border-danger" in delete_anchor
    assert "border-accent" not in delete_anchor


# ---------------------------------------------------------------------------
# CF.4.g — chip_list rendering for id-tuple fields
# ---------------------------------------------------------------------------

def test_leg_rails_renders_as_chip_list(
    writable_l2_yaml: Path,
) -> None:
    """`TransferTemplate.leg_rails` (`tuple[str, ...]` of rail ids) is
    tagged `render_as="chip_list"`. Each rail id renders as a chip
    `<span>` with `break-keep` so `cust_2_treasury_internal_book` no
    longer breaks at `_` boundaries in the read card's value column."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/transfer_template/").text
    # At least one chip wrapper present (templates have leg_rails).
    assert "flex flex-wrap" in body
    assert "break-keep" in body
    # And the chip spans are accent-tinted.
    assert "bg-link-tint" in body


def test_chip_list_for_empty_tuple_renders_em_dash(
    writable_l2_yaml: Path,
) -> None:
    """`_render_read_value` falls through to `—` for an empty tuple
    on a chip_list field — same convention as `_value_to_input_str`'s
    empty-value handling."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        FieldSpec,
        _render_read_value,
    )
    spec = FieldSpec(
        name="probe", label="Probe", helper="",
        kind="text", render_as="chip_list",
    )
    assert _render_read_value(spec, ()) == "—"


def test_chip_list_rendering_escapes_each_chip(
    writable_l2_yaml: Path,
) -> None:
    """Each chip's value is passed through `escape(str(v))` so a
    rogue `<script>` in an id can't break out of the span."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        FieldSpec,
        _render_read_value,
    )
    spec = FieldSpec(
        name="probe", label="Probe", helper="",
        kind="text", render_as="chip_list",
    )
    rendered = _render_read_value(spec, ("safe_id", "<script>alert(1)</script>"))
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_render_as_default_is_text(writable_l2_yaml: Path) -> None:
    """`FieldSpec.render_as` defaults to `"text"` so the >150
    existing FieldSpecs don't have to opt out of the new branch."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        FieldSpec,
    )
    spec = FieldSpec(
        name="probe", label="Probe", helper="", kind="text",
    )
    assert spec.render_as == "text"


def test_bundles_activity_field_tagged_chip_list(
    writable_l2_yaml: Path,
) -> None:
    """`Rail.bundles_activity` is one of the two id-tuple fields
    tagged in CF.4.g — pin the tag so a future field-spec edit
    doesn't silently regress operator-readable chips back to a comma
    string."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _FIELD_SPECS_BY_KIND,
    )
    spec = next(
        s for s in _FIELD_SPECS_BY_KIND["rail"]
        if s.name == "bundles_activity"
    )
    assert spec.render_as == "chip_list"


def test_leg_rails_field_tagged_chip_list(
    writable_l2_yaml: Path,
) -> None:
    """`TransferTemplate.leg_rails` — the other tagged field."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _FIELD_SPECS_BY_KIND,
    )
    spec = next(
        s for s in _FIELD_SPECS_BY_KIND["transfer_template"]
        if s.name == "leg_rails"
    )
    assert spec.render_as == "chip_list"
