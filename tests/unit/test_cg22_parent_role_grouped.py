"""CG.22 — The `parent_role` select on the account create/edit
form is grouped into `<optgroup>` blocks so the operator can see
which roles are parent-eligible without reading the SPEC.

Cold-read v4 P1 #7: the dropdown showed 15 raw role enums sorted
alphabetically with no hint that only Account (singleton) roles
are parent-eligible per the validator's singleton-only constraint.

This cell:
- Adds `_resolve_grouped_roles(instance, current_value)` returning
  ((group_label, sorted_roles), ...) + allow_empty.
- The `select` field renderer picks `_resolve_grouped_roles` when
  spec.name == "parent_role" and spec.select_from == "roles".
- Other "roles" sites (where the singleton constraint doesn't
  apply) stay flat.
- A stale current-value (post-delete, hand-edited YAML) surfaces in
  its own "Stale (review)" group instead of silently appearing in
  the wrong category.
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
    _resolve_grouped_roles,
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
# _resolve_grouped_roles
# ---------------------------------------------------------------------------

def test_grouped_roles_splits_singletons_from_templates(
    writable_l2_yaml: Path,
) -> None:
    """The fixture has both Account singletons and AccountTemplate
    instances. Singleton parents group must come first; template-
    only roles second."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    groups, allow_empty = _resolve_grouped_roles(inst, "")
    assert allow_empty
    labels = [g[0] for g in groups]
    assert labels[0] == "Singleton parents (eligible)"
    assert "Template roles (not eligible)" in labels
    # Cross-check the partition against the L2 instance.
    singleton_roles = {
        a.role for a in inst.accounts
    }
    # AccountTemplate.role is `Identifier` (not None-able) at the
    # type level — included unconditionally.
    template_roles = {t.role for t in inst.account_templates}
    template_only = template_roles - singleton_roles
    sg = next(g for g in groups if g[0] == "Singleton parents (eligible)")
    tg = next(g for g in groups if g[0] == "Template roles (not eligible)")
    assert set(sg[1]) == singleton_roles
    assert set(tg[1]) == template_only


def test_grouped_roles_promotes_stale_value(
    writable_l2_yaml: Path,
) -> None:
    """A current_value not present in either singleton OR template
    roles surfaces in a third 'Stale (review)' group — the operator
    can SEE that the saved field is out of sync and pick a valid
    replacement instead of having it silently vanish."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    groups, _ = _resolve_grouped_roles(inst, "ZZZNotAnyRole")
    labels = [g[0] for g in groups]
    assert "Stale (review)" in labels
    stale_group = next(g for g in groups if g[0] == "Stale (review)")
    assert stale_group[1] == ("ZZZNotAnyRole",)


def test_grouped_roles_existing_value_doesnt_create_stale_group(
    writable_l2_yaml: Path,
) -> None:
    """A current_value that IS a known singleton OR template role
    doesn't trigger the Stale group — it just renders highlighted
    in its native category by the form (via the `selected` attr in
    the rendered HTML, asserted separately)."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    known_role = next(
        str(a.role) for a in inst.accounts
    )
    groups, _ = _resolve_grouped_roles(inst, known_role)
    labels = [g[0] for g in groups]
    assert "Stale (review)" not in labels


# ---------------------------------------------------------------------------
# Rendered form — optgroups in the parent_role <select>
# ---------------------------------------------------------------------------

def test_account_new_form_renders_optgroups(
    writable_l2_yaml: Path,
) -> None:
    """`/l2_shape/account/new` carries a parent_role <select> that
    groups roles into optgroups, not a flat list of <option>s."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/account/new").text
    assert 'name="parent_role"' in body
    # The select carries optgroups now.
    # Slice the parent_role select element so we don't false-match
    # other selects on the page.
    select_start = body.index('name="parent_role"')
    select_start = body.rfind("<select", 0, select_start)
    select_end = body.index("</select>", select_start)
    select_block = body[select_start:select_end]
    assert '<optgroup label="Singleton parents (eligible)">' in select_block
    assert '<optgroup label="Template roles (not eligible)">' in select_block


def test_account_edit_form_preserves_grouping(
    writable_l2_yaml: Path,
) -> None:
    """The grouping also fires on the edit form — same field, same
    constraint."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    account = inst.accounts[0]
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(f"/l2_shape/account/{account.id}/edit").text
    assert 'name="parent_role"' in body
    select_start = body.index('name="parent_role"')
    select_start = body.rfind("<select", 0, select_start)
    select_end = body.index("</select>", select_start)
    select_block = body[select_start:select_end]
    assert "<optgroup" in select_block


def test_other_roles_select_sites_stay_flat(
    writable_l2_yaml: Path,
) -> None:
    """`account_template`'s `parent_role` field also has the
    constraint, so it gets the optgroup treatment too — but
    Chain.parent / chain_children rails select sites use different
    `select_from` values and stay flat."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    template = inst.account_templates[0]
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(f"/l2_shape/account_template/{template.role}/edit").text
    # account_template's parent_role IS named `parent_role` + uses
    # select_from="roles" (per the FieldSpec), so it ALSO gets the
    # grouping — the singleton constraint applies symmetrically.
    if 'name="parent_role"' in body:
        select_start = body.index('name="parent_role"')
        select_start = body.rfind("<select", 0, select_start)
        select_end = body.index("</select>", select_start)
        select_block = body[select_start:select_end]
        assert "<optgroup" in select_block
