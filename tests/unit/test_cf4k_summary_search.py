"""CF.4 followup (2026-06-05) — search input in the section summary.

Operator lock: the search input lives in each `<details>` summary
(so it's visible without expanding), the body toolbar drops its own
input (one search per kind), and typing auto-opens the section.

Wire shape:
- Summary search: `<input type="search" name="<kind>_q" hx-get="…"
  hx-target="#home-section-body-<kind>" hx-trigger="input changed
  delay:300ms, search" hx-include="this"
  onclick="event.stopPropagation()"
  oninput="this.closest('details').open=true">`.
- Body toolbar still renders the sort dropdown + pager. The toolbar
  form carries a hidden `<input type="hidden" name="<kind>_q"
  value="…">` so sort changes and pager clicks preserve the active
  filter.
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


def _summary_for(body: str, kind: str) -> str:
    """Slice out the `<summary>` block for one kind's section.

    Each section is `<details data-kind="<kind>">…<summary>…</summary>…`.
    """
    marker = f'data-kind="{kind}"'
    idx = body.index(marker)
    summary_start = body.index("<summary", idx)
    summary_end = body.index("</summary>", summary_start) + len("</summary>")
    return body[summary_start:summary_end]


# ---------------------------------------------------------------------------
# Summary search input presence + initial value
# ---------------------------------------------------------------------------

def test_each_list_section_summary_has_search_input(
    writable_l2_yaml: Path,
) -> None:
    """Every list-kind section's `<summary>` carries an
    `<input type="search">` named `<kind>_q`. Singleton kinds (theme /
    instance / persona) DON'T have one — no list view to search."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    for kind in (
        "account", "account_template", "rail",
        "transfer_template", "chain", "limit_schedule",
    ):
        summary = _summary_for(body, kind)
        assert 'type="search"' in summary, (
            f"summary for {kind} has no search input"
        )
        assert f'name="{kind}_q"' in summary
    # Singletons have no search input — they edit a single form, no list.
    for kind in ("theme", "instance"):
        summary = _summary_for(body, kind)
        assert 'type="search"' not in summary


def test_summary_search_initial_value_reflects_url(
    writable_l2_yaml: Path,
) -> None:
    """`/?rail_q=external` → the rail section's summary search input
    has `value="external"` so a page refresh / shared URL surfaces the
    active filter immediately."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/?rail_q=external").text
    rail_summary = _summary_for(body, "rail")
    assert 'name="rail_q"' in rail_summary
    assert 'value="external"' in rail_summary
    # Other sections stay empty.
    account_summary = _summary_for(body, "account")
    # The input is there but with empty value.
    assert 'name="account_q"' in account_summary
    assert 'value=""' in account_summary


# ---------------------------------------------------------------------------
# Auto-open + stopPropagation wiring
# ---------------------------------------------------------------------------

def test_summary_search_input_auto_opens_details_on_input(
    writable_l2_yaml: Path,
) -> None:
    """`oninput="this.closest('details').open=true"` so the first
    keystroke surfaces the results — operator never sees the
    "collapsed-section-hiding-its-own-hits" footgun."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    rail_summary = _summary_for(body, "rail")
    assert "this.closest('details').open=true" in rail_summary


def test_summary_search_input_stops_propagation(
    writable_l2_yaml: Path,
) -> None:
    """Clicking the input must NOT toggle the parent `<details>` —
    operator clicks into the box to type, not to collapse the section."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    rail_summary = _summary_for(body, "rail")
    assert 'onclick="event.stopPropagation()"' in rail_summary


# ---------------------------------------------------------------------------
# htmx wiring (hx-get / hx-target / hx-trigger / hx-include)
# ---------------------------------------------------------------------------

def test_summary_search_input_htmx_targets_section_body(
    writable_l2_yaml: Path,
) -> None:
    """`hx-target="#home-section-body-<kind>"` so the refetch lands in
    the section body, not the summary."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    rail_summary = _summary_for(body, "rail")
    assert 'hx-target="#home-section-body-rail"' in rail_summary


def test_summary_search_input_debounces_and_fires_on_search(
    writable_l2_yaml: Path,
) -> None:
    """`hx-trigger="input changed delay:300ms, search"` — debounce on
    typing, fire on the X-clear button's `search` event too."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    rail_summary = _summary_for(body, "rail")
    assert (
        'hx-trigger="input changed delay:300ms, search"' in rail_summary
    )


def test_summary_search_input_includes_itself_in_request(
    writable_l2_yaml: Path,
) -> None:
    """`hx-include="this"` so the typed value reaches the section
    endpoint (which parses `<kind>_q` via parse_toolbar_state)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    rail_summary = _summary_for(body, "rail")
    assert 'hx-include="this"' in rail_summary


# ---------------------------------------------------------------------------
# Body toolbar: search input dropped, but hidden q preserves state
# ---------------------------------------------------------------------------

def test_embed_body_toolbar_drops_search_input(
    writable_l2_yaml: Path,
) -> None:
    """`?embed=1` (the home section's lazy fetch) returns a body
    toolbar with NO `<input type="search">` (summary owns it) and NO
    sort dropdown (removed 2026-06-05 — toolbar height tax for no
    daily value). The toolbar is just range + pager."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        embed = c.get("/l2_shape/rail/?embed=1").text
    assert 'data-role="list-toolbar"' in embed
    assert 'type="search"' not in embed
    assert 'name="sort_column"' not in embed
    assert "<select" not in embed
    # Range indicator IS present.
    assert 'data-role="toolbar-range"' in embed


def test_standalone_page_still_has_search_input(
    writable_l2_yaml: Path,
) -> None:
    """Non-embed (`/l2_shape/rail/`) is the dedicated per-kind page —
    no `<details>` summary, so the body toolbar keeps the search
    input (no regression for that surface)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/rail/").text
    assert 'data-role="list-toolbar"' in body
    assert 'type="search"' in body
    assert 'name="q"' in body


# ---------------------------------------------------------------------------
# Sort + page state preservation via hidden q on body toolbar
# ---------------------------------------------------------------------------

def test_body_toolbar_pager_url_carries_q(writable_l2_yaml: Path) -> None:
    """Pager links serialize `q=…` so clicking Next from a filtered
    page lands on the next filtered page, not on the unfiltered next."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # Force pagination — page_size=1 with a filter that matches
        # multiple rails.
        embed = c.get(
            "/l2_shape/rail/?embed=1&q=Rail&page_size=1",
        ).text
    # Pager URL on the Next button keeps the q.
    assert "q=Rail" in embed
    assert "page_offset=1" in embed


def test_search_in_summary_only_one_input_per_kind(
    writable_l2_yaml: Path,
) -> None:
    """Belt-and-braces — count the kind-prefixed `name="<kind>_q"`
    occurrences on the home page. Each list-kind section should have
    exactly one search input (the summary one)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    for kind in (
        "account", "account_template", "rail",
        "transfer_template", "chain", "limit_schedule",
    ):
        # The home page renders one summary search per list kind.
        assert body.count(f'name="{kind}_q"') == 1, (
            f"expected exactly 1 search input for {kind}, "
            f"got {body.count(f'name={kind}_q')}"
        )
