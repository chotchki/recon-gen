"""CG.11 — Account / AccountTemplate cards surface the analyst-
readable `Account.name` next to the kebab id.

Cold-read v3 P1: an accountant operator reads accounts by
Fed-statement name ("Cash & Due From Federal Reserve"), not by GL
kebab ("gl-1010-cash-due-frb"). The at-a-glance scan was broken
because the kebab id sat alone in the title and the human-readable
name only rendered after expanding the card.

Fix: surface `Account.name` (or `AccountTemplate.name`) as a
smaller, secondary-fg span next to the id, like the rail subtype
badge. Other kinds keep the existing title shape (they don't have
the same id-vs-name disconnect).
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


def test_account_card_title_renders_display_name(
    writable_l2_yaml: Path,
) -> None:
    """`_render_read_card` for kind=account emits the
    `data-role="card-display-name"` span carrying the analyst-
    readable name next to the kebab id."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_read_card,
    )
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    # cust-001 has a `name` field in spec_example.
    account = next(a for a in inst.accounts if a.id == "cust-001" and a.name)
    card = _render_read_card(
        "account", account, inst, demo_mode=False, collapsed=True,
    )
    assert 'data-role="card-display-name"' in card
    assert escape_str(str(account.name)) in card


def test_account_template_card_title_omits_display_name_span(
    writable_l2_yaml: Path,
) -> None:
    """`AccountTemplate` carries `role` + `instance_name_template`,
    NOT a `name`. Cold-read v3's accountant-readability finding is
    purely about Account singletons (which have a Fed-statement
    `name` distinct from the GL kebab); per-template materialized
    instances surface their generated name elsewhere. Keep the title
    shape simple here so the renderer doesn't pretend a field exists
    when it doesn't."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_read_card,
    )
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    template = inst.account_templates[0]
    card = _render_read_card(
        "account_template", template, inst,
        demo_mode=False, collapsed=True,
    )
    assert 'data-role="card-display-name"' not in card


def test_rail_card_title_omits_display_name_span(
    writable_l2_yaml: Path,
) -> None:
    """Only account / account_template carry the display-name span.
    Rails already have the subtype badge; other kinds don't share the
    accountant id-vs-name disconnect."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_read_card,
    )
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    rail = inst.rails[0]
    card = _render_read_card(
        "rail", rail, inst, demo_mode=False, collapsed=True,
    )
    assert 'data-role="card-display-name"' not in card


def test_account_with_no_name_omits_the_span(
    writable_l2_yaml: Path,
) -> None:
    """`Account.name` is optional. When None or empty, no span — the
    kebab id stands alone, same as before CG.11."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_read_card,
    )
    # Build an account with no name.
    import dataclasses
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    base = next(a for a in inst.accounts if a.name is not None)
    nameless = dataclasses.replace(base, name=None)
    card = _render_read_card(
        "account", nameless, inst, demo_mode=False, collapsed=True,
    )
    assert 'data-role="card-display-name"' not in card


def test_display_name_is_html_escaped(writable_l2_yaml: Path) -> None:
    """An account name with `<script>` doesn't break out of the span."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_read_card,
    )
    import dataclasses
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    base = next(a for a in inst.accounts if a.name is not None)
    xss = dataclasses.replace(base, name="<script>alert(1)</script>")  # type: ignore[arg-type]: Name is a NewType[str]
    card = _render_read_card(
        "account", xss, inst, demo_mode=False, collapsed=True,
    )
    assert "<script>alert(1)</script>" not in card
    assert "&lt;script&gt;" in card


def test_account_list_page_renders_display_name(
    writable_l2_yaml: Path,
) -> None:
    """Integration: hitting `/l2_shape/account/?body_only=1` for a
    named account returns the display-name span in the body."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        list_body = c.get("/l2_shape/account/").text
    # At least one account card should carry the display-name span
    # on a fixture with named accounts.
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    named = next(a for a in inst.accounts if a.name is not None)
    # The list page renders collapsed cards; the title (including
    # display name) lives in the always-visible summary.
    assert 'data-role="card-display-name"' in list_body
    assert escape_str(str(named.name)) in list_body


def escape_str(s: str) -> str:
    """Mirror the renderer's html.escape() so assertions match."""
    from html import escape  # noqa: PLC0415
    return escape(s)
