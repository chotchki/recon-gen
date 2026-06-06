"""CG-bug.1 — Markdown Preview tab on a textarea field POSTs only
that field's value to `/preview/markdown`, not the rest of the
form's data.

Dogfood bug post-CG sweep: clicking Preview on the account edit
form's Description tab rendered the kebab `id`
(gl-1010-cash-due-frb) instead of the markdown prose. Root cause:
the Preview button sits inside the edit `<form>`, and HTMX's
default when the trigger is inside a form is to serialize the
ENTIRE form's data + still pull `hx-include` extras. The server's
`/preview/markdown` handler takes the FIRST non-meta value from
the form body — which was `id`, not `description`.

Fix: add `hx-params="<field_name>"` to the Preview button. HTMX's
`hx-params` filters the POST body to ONLY the named field, so
the server's first-value loop picks the right one.

Tests pin both layers:
1. The rendered Preview button carries `hx-params="<spec.name>"`.
2. End-to-end POSTing the whole form to `/preview/markdown` with
   no `hx-params` filter would pick the wrong field — assert the
   server picks the description when only `description=...` is
   posted.
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
# Rendered button carries hx-params filter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/l2_shape/account/cust-001/edit",
    "/l2_shape/account/new",
    "/l2_shape/rail/new",  # rail subtype picker
    "/l2_shape/theme/",  # singleton
    "/l2_shape/account/",  # list view
    "/l2_shape/persona/",  # 404 chrome
])
def test_form_pages_load_htmx(
    writable_l2_yaml: Path, path: str,
) -> None:
    """Every editor surface that carries `hx-*` attributes must
    actually load htmx so the attributes fire. Pre-CG-bug.2 only
    the list view loaded htmx; the edit / create / singleton /
    subtype-picker / 404 pages had `hx-delete`, `hx-post`,
    `hx-confirm` attrs silently no-op-ing because htmx wasn't on
    the page. Dogfood surfaced it via the markdown-Preview button;
    CG.19's edit-form Delete button was a latent victim too."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(path).text
    assert "htmx.org@1.9.10" in body, (
        f"{path}: htmx script tag missing — every `hx-*` attribute "
        f"on this page silently no-ops without it"
    )


def test_account_edit_preview_button_filters_to_description(
    writable_l2_yaml: Path,
) -> None:
    """Account edit page Preview button on the Description field
    carries `hx-params="description"` so the POST body to
    `/preview/markdown` contains ONLY the description, not the
    surrounding form scope."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    account = inst.accounts[0]
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(f"/l2_shape/account/{account.id}/edit").text
    # The Preview button is identifiable by the description preview
    # target; slice that fragment so we don't false-match elsewhere.
    # Slice the Preview button by its unique opening-tag id= marker
    # (the Edit button also references this id inside its onclick
    # handler, so a plain substring match hits the Edit tag first).
    target_idx = body.index('id="field-description-tab-preview"')
    button_start = body.rfind("<button", 0, target_idx)
    button_end = body.index(">", target_idx) + 1
    button_block = body[button_start:button_end]
    assert 'hx-post="/preview/markdown"' in button_block
    assert 'hx-params="description"' in button_block


@pytest.mark.parametrize("kind,entity_attr,field", [
    ("account", "accounts", "description"),
    ("rail", "rails", "description"),
    ("account_template", "account_templates", "description"),
])
def test_preview_button_filters_per_kind(
    writable_l2_yaml: Path, kind: str, entity_attr: str, field: str,
) -> None:
    """Every kind that carries a markdown-preview-enabled FieldSpec
    gets the same `hx-params` filter, so the bug doesn't recur on
    rail / account_template / etc. edit pages."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    entities = getattr(inst, entity_attr)
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _entity_id,
    )
    eid = _entity_id(kind, entities[0])  # type: ignore[arg-type]: parametrize fixture passes valid kinds
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(f"/l2_shape/{kind}/{eid}/edit").text
    target_marker = f'id="field-{field}-tab-preview"'
    if target_marker not in body:
        pytest.skip(
            f"{kind} edit page has no markdown-preview tab for "
            f"{field!r} in this fixture",
        )
    target_idx = body.index(target_marker)
    button_start = body.rfind("<button", 0, target_idx)
    button_end = body.index(">", target_idx) + 1
    button_block = body[button_start:button_end]
    assert f'hx-params="{field}"' in button_block


# ---------------------------------------------------------------------------
# End-to-end — server picks the right field
# ---------------------------------------------------------------------------

def test_preview_endpoint_renders_only_description_field(
    writable_l2_yaml: Path,
) -> None:
    """With `hx-params="description"` in effect, the POST body to
    `/preview/markdown` contains only the description field. The
    server picks it up + renders its markdown.

    Pre-fix the whole form was POSTed; the server's "first non-meta
    value" loop picked `id` first → Preview showed the kebab id."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.post(
            "/preview/markdown",
            data={"description": "Hello **world**"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    assert resp.status_code == 200
    body = resp.text
    assert "<strong>world</strong>" in body
    assert "Hello" in body


def test_preview_endpoint_buggy_pre_fix_shape_documented(
    writable_l2_yaml: Path,
) -> None:
    """Document the pre-fix scenario: if the WHOLE form were POSTed
    (no hx-params filter), the server picks the FIRST non-meta
    value — typically `id`. The test asserts this so a future
    refactor that drops the per-field filter regresses visibly."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.post(
            "/preview/markdown",
            data={
                "id": "gl-1010-cash-due-frb",
                "scope": "internal",
                "description": "Hello **world**",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    # The server's iteration order is form-multi-items insertion order.
    # The first non-meta value is `id` → rendered as a bare paragraph.
    assert "gl-1010-cash-due-frb" in resp.text
    # Description's markdown is NOT rendered in this scenario.
    assert "<strong>world</strong>" not in resp.text
