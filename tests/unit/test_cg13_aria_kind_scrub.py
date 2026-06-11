"""CG.13 — `aria-label`, `<title>`, h1, and tooltip strings no
longer leak underscored `EntityKind` enum values.

Cold-read v3 P1: `account_template`, `transfer_template`, and
`limit_schedule` rendered straight into screen-reader announcements
("Search account_templates"), `<title>` bars ("Edit
account_template: cust-001 — Studio"), and tooltip hovers ("Create
a new transfer_template"). The fix is a single source of truth in
`_components.py::{kind_label_singular, kind_label_plural}` (Mappings
+ helpers) that every aria / title / h1 / tooltip site consumes.

This file pins:

- the helpers themselves return the operator-readable forms,
- the home page's per-section search aria-label uses
  "Search <plural lowercase>" (no underscore, no enum value),
- the home page's "+ Add" tooltip uses "Create a new <singular>",
- the dedicated `/l2_shape/<kind>/` list page's `<title>` uses
  the Title-Case plural,
- the `Create new <kind>` and `Edit <kind>: <id>` form pages use
  the singular form in both `<title>` and h1.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from recon_gen.common.html._components import (
    kind_label_plural,
    kind_label_singular,
)
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
# Helpers themselves
# ---------------------------------------------------------------------------

def test_helpers_strip_underscores_for_user_facing_kinds() -> None:
    """Every multi-word `EntityKind` value is space-separated in both
    singular + plural form. Single-word kinds pass through."""
    cases = (
        ("account_template", "account template", "Account templates"),
        ("transfer_template", "transfer template", "Transfer templates"),
        ("limit_schedule", "limit schedule", "Limit schedules"),
        ("account", "account", "Accounts"),
        ("rail", "rail", "Rails"),
        ("chain", "chain", "Chains"),
    )
    for kind, want_singular, want_plural in cases:
        assert kind_label_singular(kind) == want_singular  # type: ignore[arg-type]: literal tuple from local case list narrows to EntityKind at runtime
        assert kind_label_plural(kind) == want_plural  # type: ignore[arg-type]: literal tuple from local case list narrows to EntityKind at runtime


def test_kind_label_plural_lowercase_flag() -> None:
    """`lowercase=True` returns the plural in mid-sentence form (for
    aria-labels and "No <plural> match" empty-states)."""
    assert kind_label_plural("account_template", lowercase=True) == "account templates"
    assert kind_label_plural("rail", lowercase=True) == "rails"


# ---------------------------------------------------------------------------
# Home page surfaces
# ---------------------------------------------------------------------------

def test_home_page_summary_search_aria_no_underscores(
    writable_l2_yaml: Path,
) -> None:
    """Every section's search input renders `aria-label="Search
    <plural>"` — no underscore, no kebab id, no raw enum value."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    assert 'aria-label="Search account_templates"' not in body
    assert 'aria-label="Search transfer_templates"' not in body
    assert 'aria-label="Search limit_schedules"' not in body
    assert 'aria-label="Search account templates"' in body
    assert 'aria-label="Search transfer templates"' in body
    assert 'aria-label="Search limit schedules"' in body
    # Plain single-word kinds work too.
    assert 'aria-label="Search rails"' in body


def test_home_page_add_button_tooltip_no_underscores(
    writable_l2_yaml: Path,
) -> None:
    """The `+ Add` button's `title=` tooltip uses the singular
    operator-readable form.

    BX.6/11 (2026-06-11): account + account_template no longer have
    their own +Add buttons on the home page — they're funnelled
    through the single ``+ Add Role`` modal in the Roles wrapper.
    The remaining sections (rail, transfer_template, chain,
    limit_schedule) keep the per-section +Add tooltips.
    """
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    assert 'title="Create a new account_template"' not in body
    assert 'title="Create a new transfer_template"' not in body
    # Account + account_template Add buttons moved to the modal.
    assert 'title="Create a new account template"' not in body
    assert 'title="Create a new account"' not in body
    # The remaining kinds keep their per-section +Add buttons.
    assert 'title="Create a new transfer template"' in body
    assert 'title="Create a new limit schedule"' in body


# ---------------------------------------------------------------------------
# Dedicated list pages
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind,want_plural", [
    ("account_template", "Account templates"),
    ("transfer_template", "Transfer templates"),
    ("limit_schedule", "Limit schedules"),
    ("account", "Accounts"),
    ("rail", "Rails"),  # typing-smell: ignore[no-inline-production-constants]: same string as l2_flow_tracing/app.py::_RAILS_NAME by coincidence (sheet name) but semantically the kind_label_plural value, not a shared contract
    ("chain", "Chains"),  # typing-smell: ignore[no-inline-production-constants]: same string as l2_flow_tracing/app.py::_CHAINS_NAME by coincidence (sheet name) but semantically the kind_label_plural value, not a shared contract
])
def test_list_page_title_uses_plural_label(
    writable_l2_yaml: Path, kind: str, want_plural: str,
) -> None:
    """The `<title>` bar on `/l2_shape/<kind>/` reads the Title-Case
    plural — never a raw enum value with underscores. Title shape
    is locked by CG.21 (`Recon-Gen · Studio · Editor · <plural>`)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(f"/l2_shape/{kind}/").text
    assert f"<title>Recon-Gen · Studio · Editor · {want_plural}</title>" in body
    # The underscored enum should never appear in the title.
    if "_" in kind:
        assert kind not in body.split("</title>")[0]


# ---------------------------------------------------------------------------
# Form pages — Create + Edit
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind,want_singular", [
    ("account_template", "account template"),
    ("transfer_template", "transfer template"),
    ("limit_schedule", "limit schedule"),
    ("rail", "rail"),
    ("account", "account"),
])
def test_create_form_uses_singular_label(
    writable_l2_yaml: Path, kind: str, want_singular: str,
) -> None:
    """Both the `<title>` and the form-page h1 use the singular
    operator-readable form."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(f"/l2_shape/{kind}/new").text
    # <title>
    assert f"Create new {want_singular}" in body
    # h1 (lives inside the form-page header strip)
    assert f">Create new {want_singular}" in body
    # Raw enum value (with underscore) should not leak in either.
    if "_" in kind:
        assert f"Create new {kind}" not in body


def test_edit_form_uses_singular_label(
    writable_l2_yaml: Path,
) -> None:
    """Edit page surfaces the singular operator-readable kind label
    ("account", "account template") — never the underscored enum.
    Title shape is locked by CG.21 (`Edit account · <id>`); h1 lives
    inside the form-page header strip and reads `Edit account ...:
    <id>` (CG.19 may prepend a display name on accounts). The
    intent of this test is to pin the absence of the underscore-
    leak, not the exact punctuation."""
    app = _build_app(writable_l2_yaml)
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    account = inst.accounts[0]
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(f"/l2_shape/account/{account.id}/edit").text
    # The singular label appears in the page; the kind enum
    # (without underscore here, both happen to be "account") does
    # not lead the title or h1 in some other shape.
    assert "Edit account" in body
    # The interesting case — multi-word kind.
    template = inst.account_templates[0]
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(f"/l2_shape/account_template/{template.role}/edit").text
    assert "Edit account template" in body
    assert "Edit account_template" not in body
    assert "account_template" not in body.split("</title>")[0]
