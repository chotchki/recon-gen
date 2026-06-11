"""BX.new.list-cascade-reload — standalone list pages subscribe to
``HX-Trigger: l2-cascade-reload``.

Bug shape: deleting an entity on a list page (``/l2_shape/<kind>/``)
returned the trigger header but no listener on the page caught it, so
the deleted article's parent ``<article>`` stayed visible until the
operator hit reload. The home page already solved this via per-section
``hx-trigger="load, l2-cascade-reload from:body"`` on each section
body (see ``_render_home_page``); standalone list pages lacked the
mirror wiring until this cell.

Fix shape: wrap the standalone list page's
``search + <main entity-list> + pager`` in
``<div id="list-page-body" hx-get="<current URL>"
hx-trigger="l2-cascade-reload from:body"
hx-select="#list-page-body" hx-swap="outerHTML">``.
On cascade-reload htmx re-fetches the current URL (preserving search /
sort / page query state), extracts the new ``#list-page-body`` out of
the full HTML response, and swaps the whole wrapper — the deleted
article disappears + the listener re-attaches in the new wrapper.

Embed mode (the home page's section body fragment) does NOT add this
wrapper — the home page's per-section ``<div>`` already owns
cascade-reload there. Adding it on the embed surface would double-fire
(home section refetch + inner list-page-body refetch on the same
event), so the embed path stays clean.
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


@pytest.mark.parametrize("kind", ["rail", "account", "transfer_template", "chain", "limit_schedule"])
def test_standalone_list_page_wraps_body_in_cascade_reload_wrapper(
    writable_l2_yaml: Path, kind: str,
) -> None:
    """Every standalone list page renders the
    ``<div id="list-page-body">`` wrapper with cascade-reload wiring.

    Tied to (1) the wrapper id is the hx-select target on refetch so
    it must be unique + stable; (2) the trigger MUST be
    ``l2-cascade-reload from:body`` to match the DELETE handler's
    HX-Trigger header (see ``_studio_editor_routes.py``'s delete
    handler); (3) ``hx-swap="outerHTML"`` so the wrapper rebuilds
    itself on each cascade — preserves the re-attaching pattern (the
    new wrapper carries its own trigger).
    """
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but make_app return type is inferred Any
        body = c.get(f"/l2_shape/{kind}/").text

    assert 'id="list-page-body"' in body, (
        f"kind={kind!r} standalone list page missing the "
        f"`#list-page-body` wrapper. The DELETE handler emits "
        f"`HX-Trigger: l2-cascade-reload`; without this wrapper the "
        f"event fires into the void and the deleted article stays "
        f"visible until reload."
    )
    assert 'hx-trigger="l2-cascade-reload from:body"' in body, (
        f"kind={kind!r} list page missing the cascade-reload "
        f"hx-trigger declaration. The trigger name must match the "
        f"DELETE handler's HX-Trigger header verbatim."
    )
    assert 'hx-select="#list-page-body"' in body, (
        f"kind={kind!r} list page missing the hx-select. Without it "
        f"htmx would swap the WHOLE response document (chrome + "
        f"top-nav + header + body) into the wrapper, nesting a full "
        f"<html> doc inside itself."
    )
    assert 'hx-swap="outerHTML"' in body, (
        f"kind={kind!r} list page missing outerHTML swap. innerHTML "
        f"would leave the OLD wrapper element in the DOM (sans its "
        f"children) and the new content would be injected without "
        f"its trigger re-attaching cleanly."
    )


def test_cascade_reload_url_preserves_search_sort_page_state(
    writable_l2_yaml: Path,
) -> None:
    """The wrapper's ``hx-get`` URL echoes the operator's current URL
    (path + query string) so the cascade refetch preserves their
    search / sort / page view.

    Without this, a delete-while-paginated to page 3 would refetch
    page 1 (the default) and the operator's view would silently snap
    to the top.
    """
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]
        body = c.get(
            "/l2_shape/rail/?q=ach&sort_column=name&page_offset=25",
        ).text

    # The hx-get URL on the cascade wrapper carries every state key
    # the operator's URL did. Bare paths only (no ?embed=1 — that
    # form is the home-page surface) — the standalone refetch parses
    # the same query keys it was loaded with.
    assert (
        'hx-get="/l2_shape/rail/?q=ach&amp;sort_column=name'
        '&amp;page_offset=25"'
    ) in body, (
        "cascade-reload URL dropped the search/sort/page state. "
        "Operator's paginated view would snap to page 1 + lose the "
        "search term on every delete. Re-thread `request.url.query` "
        "through to `_render_list_page(cascade_url=…)`."
    )


def test_embed_form_does_not_emit_cascade_wrapper(
    writable_l2_yaml: Path,
) -> None:
    """``?embed=1`` (the home-page section fragment) does NOT emit
    the wrapper.

    The home page's section body ``<div hx-trigger="load,
    l2-cascade-reload from:body">`` already owns cascade-reload for
    embedded fragments. Doubling up would fire two refetches on the
    same trigger.
    """
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]
        body = c.get("/l2_shape/rail/?embed=1").text

    assert 'id="list-page-body"' not in body, (
        "embed fragment leaked the cascade wrapper. The home page's "
        "section body already wires cascade-reload; the embed payload "
        "must stay just the cards + pager fragment."
    )


def test_cascade_wrapper_contains_search_main_and_pager(
    writable_l2_yaml: Path,
) -> None:
    """The wrapper must enclose search + ``<main id="entity-list">`` +
    pager so the refetch swap re-renders all three together.

    Caught a structural drift: if the cascade wrapper closed before
    the pager, a delete that crossed a page boundary would refresh
    the cards but leave the pager showing the pre-delete total.
    """
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]
        body = c.get("/l2_shape/rail/").text

    open_idx = body.index('id="list-page-body"')
    # Find the closing </div> of the wrapper. Naive but works because
    # the wrapper is the outermost div inside <body> and contains a
    # bounded number of inner elements.
    wrap_close_idx = body.rindex("</div>")
    inner = body[open_idx:wrap_close_idx]

    assert 'data-role="list-search"' in inner, (
        "cascade wrapper missing search form. Search input must "
        "re-render on cascade to reflect the (potentially) new "
        "filter result count."
    )
    assert 'id="entity-list"' in inner, (
        "cascade wrapper missing the <main id=\"entity-list\"> "
        "cards grid. This is the swap target the toolbar Search "
        "form posts into."
    )
    assert 'data-role="list-pager"' in inner, (
        "cascade wrapper missing the pager strip. A delete that "
        "drops the total below the current page's start would "
        "leave the pager showing stale Showing X–Y of Z numbers."
    )
