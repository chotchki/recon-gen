"""CF.4.c — collapse-by-default per card + HTMX expand-on-demand.

`_render_read_card` was split into a lightweight summary + a heavy
`<dl>` body. When the page renders > `COLLAPSE_THRESHOLD` total
entities, cards wrap in `<details>` with the body deferred via
`hx-trigger="toggle once"`; below the threshold (sasquatch_pr's 7
rails), eager render keeps things cheap.

`/l2_shape/<kind>/<id>?body_only=1` returns just the body
fragment — the lazy-fetch endpoint.
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
# Eager vs collapsed render
# ---------------------------------------------------------------------------

def test_small_kind_still_renders_collapsed(
    writable_l2_yaml: Path,
) -> None:
    """CG.5 (2026-06-05): chevron + lazy-load applies UNIFORMLY
    regardless of count. spec_example's 7 accounts now render with
    the same `<details>` + chevron + body lazy-fetch pattern that
    rails always used. Operator lock: the asymmetry between small
    and large kinds read as "two different products" in cold-read
    v3; uniform UX trumps the extra-click cost on tiny L2s."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/account/").text
    assert "<details" in body
    assert 'data-role="card-body"' in body
    assert 'hx-trigger="toggle once"' in body


def test_large_kind_renders_collapsed(
    writable_l2_yaml: Path,
) -> None:
    """spec_example has 21 rails. Rail cards wrap in `<details>` and
    lazy-fetch their body (post-CG.5 same as small kinds)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/rail/").text
    assert "<details" in body
    assert 'data-role="card-body"' in body
    assert 'hx-trigger="toggle once"' in body
    assert "?body_only=1" in body


def test_large_l2_renders_collapsed_via_unit_call(
    writable_l2_yaml: Path,
) -> None:
    """Direct `_render_read_card(collapsed=True)` produces the
    collapsed shape regardless of fixture size — pin the contract."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_read_card,
    )
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    rail = inst.rails[0]
    card_html = _render_read_card(
        "rail", rail, inst, collapsed=True,
    )
    # Collapsed cards wrap in <details>.
    assert "<details" in card_html
    # The lazy-fetch placeholder is present, NOT the full `<dl>`.
    assert 'data-role="card-body"' in card_html
    # The HTMX trigger fires once on toggle.
    assert 'hx-trigger="toggle once"' in card_html
    # The body endpoint URL is /l2_shape/<kind>/<id>?body_only=1.
    assert 'hx-get="/l2_shape/rail/' in card_html
    assert "?body_only=1" in card_html
    # The heavy `<dl>` body is NOT inlined.
    assert "<dl class=" not in card_html


def test_eager_card_keeps_dl_body_inlined_via_unit_call(
    writable_l2_yaml: Path,
) -> None:
    """Counter-test: eager path still works."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_read_card,
    )
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    rail = inst.rails[0]
    card_html = _render_read_card(
        "rail", rail, inst, collapsed=False,
    )
    # No <details> wrapper on eager render.
    assert "<details" not in card_html
    # `<dl>` rendered inline.
    assert "<dl class=" in card_html
    # No body-fetch placeholder.
    assert 'data-role="card-body"' not in card_html


# ---------------------------------------------------------------------------
# body_only=1 endpoint
# ---------------------------------------------------------------------------

def test_body_only_returns_dl_only(writable_l2_yaml: Path) -> None:
    """`GET /l2_shape/rail/<name>?body_only=1` returns just the
    `<dl>` rows — no `<article>` wrapper, no header, no actions."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # Pick a real rail id out of the fixture.
        list_body = c.get("/l2_shape/rail/").text
        # First article id is the rail name (read-card root).
        import re
        ids = re.findall(r'data-entity-id="([^"]+)"', list_body)
        assert ids, "fixture has no rails"
        rail_id = ids[0]
        body_only = c.get(f"/l2_shape/rail/{rail_id}?body_only=1").text
    assert body_only.startswith("<dl ")
    assert "<article" not in body_only
    assert "<header" not in body_only
    # Edit / Delete actions don't leak into the body fragment.
    assert ">Edit<" not in body_only
    assert ">Delete<" not in body_only


def test_body_only_404s_for_unknown_entity(writable_l2_yaml: Path) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get(
            "/l2_shape/rail/definitely_no_such_rail?body_only=1",
        )
    assert resp.status_code == 404


def test_card_route_without_body_only_returns_full_card(
    writable_l2_yaml: Path,
) -> None:
    """The existing read_card endpoint without `body_only` still
    returns the full eager card (no regression for post-save flows)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        list_body = c.get("/l2_shape/rail/").text
        import re
        ids = re.findall(r'<article[^>]*id="([^"]+)"', list_body)
        rail_id = ids[0]
        full = c.get(f"/l2_shape/rail/{rail_id}").text
    assert "<article" in full
    assert "<header" in full
    assert "<dl class=" in full


# ---------------------------------------------------------------------------
# Edit/Delete event-propagation guard inside <summary>
# ---------------------------------------------------------------------------

def test_action_links_stop_propagation_in_collapsed_card(
    writable_l2_yaml: Path,
) -> None:
    """Edit + Delete inside `<summary>` carry an onclick guard so
    clicking them doesn't toggle the parent `<details>` (which would
    expand the card just to immediately navigate away).

    BX.1 followup (2026-06-11): Delete's guard upgraded from
    ``event.stopPropagation()`` alone to
    ``event.preventDefault(); event.stopPropagation()``.
    ``stopPropagation()`` cancels propagation but NOT the default
    action of the click event; the ``<summary>`` activation behavior
    (toggle the parent ``<details>``) IS that default action. Adding
    ``preventDefault()`` cancels it. Edit keeps the bare
    ``stopPropagation()`` because Edit navigates the operator away
    (the toggle is invisible to them) AND ``preventDefault()`` on
    Edit would block middle-click / cmd-click "open in new tab".

    The card title itself is plain ``<h3>`` — title-as-diagram-focus
    was dropped 2026-06-05 (a holdover from before Diagram became its
    own top-level surface), so title clicks NOW intentionally bubble
    to toggle the details.
    """
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_read_card,
    )
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    account = next(a for a in inst.accounts if a.id == "cust-001")
    card = _render_read_card("account", account, inst, collapsed=True)
    # Edit guards via `stopPropagation()` only (its click navigates
    # the operator away — bubbling-induced toggle is invisible).
    import re
    edit_anchor = re.search(
        r'<a\b[^>]*href="/l2_shape/[^"]+/edit"[^>]*>',
        card,
    )
    assert edit_anchor is not None, card
    assert "event.stopPropagation()" in edit_anchor.group(0)
    # Delete guards via both — its click does NOT navigate; the
    # banner appears in place and the operator stays on the card.
    delete_anchor = re.search(
        r'<a\b[^>]*\bdata-role="card-delete"[^>]*>',
        card,
    )
    assert delete_anchor is not None, card
    assert "event.preventDefault()" in delete_anchor.group(0), (
        "BX.1 followup invariant: Delete anchor MUST carry "
        "`event.preventDefault()` so the parent `<details>` "
        "doesn't toggle open when the operator clicks Delete on a "
        "collapsed card. Missing this guard caused the limit_schedule "
        "Delete-toggles-card bug (2026-06-11 operator report)."
    )
    assert "event.stopPropagation()" in delete_anchor.group(0)
    # Title carries no anchor → no `/diagram?focus=…` link.
    assert "/diagram?focus=" not in card
