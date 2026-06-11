"""BX.4 — Read-card visual upgrade matching edit-form sectioning.

Per the BX overnight plan + the BX.6 follow-up that wrapped the read
card in full-page chrome (commit d58bff59), this cell groups the
flat FieldSpec list into operator-meaningful sections (Identity /
Classification / Activity / cadence / Aging / Soft bounds / ...) and
renders the same section structure on BOTH the edit form (as
``<fieldset>`` + ``<legend>``) and the read card (as ``<section>`` +
``<h3>`` + per-section ``<dl>``).

Tests cover:

- ``_group_specs_into_sections`` pure helper — section order,
  empty-section drop, "Other" trailing bucket for unassigned fields.
- The shared ``_FIELD_SECTIONS_BY_KIND`` source-of-truth: every
  FieldSpec name resolved by ``_FIELD_SPECS_BY_KIND`` is named under
  exactly one section (no missing, no extras).
- Read-card body — sections render in the declared order with the
  declared header copy + ``data-section-name`` anchors per
  ``[feedback_browser_drivers_user_facing_locators]``.
- Edit form — same section order + same ``data-section-name``
  anchors on the rendered ``<fieldset>`` elements; the edit form
  + read card sections agree (this is the BX.4 invariant — drift here
  means the operator sees different shapes between read + edit).
- Empty section drops gracefully — when every field in a section
  gets filtered out by ``subtype_only`` (e.g. a single_leg rail
  hides Topology's two_leg fields), the fieldset / read-card-section
  doesn't render at all.
"""

from __future__ import annotations

import re
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
    _FIELD_SECTIONS_BY_KIND,
    _FIELD_SPECS_BY_KIND,
    FieldSpec,
    _group_specs_into_sections,
)
from recon_gen.common.html._studio_routes import make_studio_routes
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
from tests._test_helpers import make_test_config

_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    src = _FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


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


# ---------------------------------------------------------------------------
# Pure helper — _group_specs_into_sections + the section-map invariant
# ---------------------------------------------------------------------------


def test_group_specs_into_sections_preserves_declared_order() -> None:
    """Sections render in the order declared in
    ``_FIELD_SECTIONS_BY_KIND``, not in the FieldSpec list order. The
    rail FieldSpec list interleaves identity + topology + activity
    fields per CO.3 polish; the section map declares the explicit
    order operators see on the read card + edit form.
    """
    specs = _FIELD_SPECS_BY_KIND["rail"]
    sections = _group_specs_into_sections("rail", specs)
    labels = [label for label, _ in sections]
    # Identity must lead; aging trails; soft bounds last (per the
    # _FIELD_SECTIONS_BY_KIND declaration).
    assert labels[0] == "Identity"
    assert "Aging" in labels
    assert labels[-1] == "Soft bounds"


def test_group_specs_into_sections_drops_empty_sections() -> None:
    """When the filtered ``specs`` tuple omits every member of a
    declared section, that section drops — the bucket would be empty
    on render anyway, and an empty <fieldset> with just a <legend>
    confuses the operator. Filter a rail spec list down to just the
    Identity fields and confirm the other sections vanish.
    """
    rail_specs = _FIELD_SPECS_BY_KIND["rail"]
    identity_only = tuple(
        s for s in rail_specs if s.name in {"name", "description"}
    )
    sections = _group_specs_into_sections("rail", identity_only)
    assert len(sections) == 1
    assert sections[0][0] == "Identity"


def test_group_specs_into_sections_collects_unassigned_into_other() -> None:
    """A FieldSpec whose name isn't named under any section map entry
    lands in a trailing ``"Other"`` section so it still renders. This
    keeps the section map from silently swallowing fields the author
    forgot to assign — surfacing the gap rather than hiding it.
    """
    # Synthetic spec list with one in Identity + one not declared.
    fake_specs = (
        FieldSpec(name="name", label="Name", helper="", kind="text"),
        FieldSpec(name="__synthetic_unassigned", label="Synth", helper="", kind="text"),
    )
    sections = _group_specs_into_sections("rail", fake_specs)
    assert ("Identity", (fake_specs[0],)) in sections
    other = [s for s in sections if s[0] == "Other"]
    assert other
    other_specs = other[0][1]
    assert any(s.name == "__synthetic_unassigned" for s in other_specs)


@pytest.mark.parametrize("kind", list(_FIELD_SPECS_BY_KIND.keys()))
def test_field_section_map_covers_every_spec_no_extras(kind: str) -> None:
    """Every FieldSpec name in ``_FIELD_SPECS_BY_KIND[kind]`` must be
    named under exactly one section in ``_FIELD_SECTIONS_BY_KIND[kind]``,
    and no section may reference a field name that doesn't exist on
    the kind. This is the BX.4 source-of-truth contract — drift here
    means the read card and edit form disagree on the same field's
    bucket.
    """
    specs = _FIELD_SPECS_BY_KIND[kind]  # type: ignore[index]: kind iterates the closed EntityKind Literal at parametrize time
    spec_names = {s.name for s in specs}
    section_names: set[str] = set()
    for _label, names in _FIELD_SECTIONS_BY_KIND[kind]:  # type: ignore[index]: kind iterates the closed EntityKind Literal at parametrize time
        section_names.update(names)
    assert spec_names == section_names, (
        f"{kind}: missing={spec_names - section_names} "
        f"extra={section_names - spec_names}"
    )


@pytest.mark.parametrize("kind", list(_FIELD_SPECS_BY_KIND.keys()))
def test_field_section_map_has_no_duplicate_field_names(kind: str) -> None:
    """A field listed under two sections would render twice on the
    edit form — once per fieldset. Pin every kind's flat
    ``(field_name)`` sequence is unique.
    """
    seen: list[str] = []
    for _label, names in _FIELD_SECTIONS_BY_KIND[kind]:  # type: ignore[index]: kind iterates the closed EntityKind Literal at parametrize time
        seen.extend(names)
    assert len(seen) == len(set(seen)), (
        f"{kind} has duplicate field names across sections: {seen}"
    )


# ---------------------------------------------------------------------------
# Read-card body — sections render with data-section-name anchors.
# ---------------------------------------------------------------------------


def _section_names_in_order(body: str, marker: str) -> list[str]:
    """Pull ``data-section-name`` values out of rendered HTML in order.

    ``marker`` filters to one of the two surfaces (read-card-section
    on the read card, edit-form-section or create-form-section on
    the form). Lets the same regex serve both sides of the parity
    assertion.
    """
    # data-role anchors come right before data-section-name in the
    # rendered markup; match the pair so the regex doesn't pick up
    # any unrelated data-section-name attribute that lands on the
    # page through another surface.
    pattern = (
        rf'data-role="{re.escape(marker)}"\s+'
        r'data-section-name="([^"]+)"'
    )
    return re.findall(pattern, body)


def test_rail_read_card_renders_sections_in_declared_order(
    writable_l2_yaml: Path,
) -> None:
    """Fetch a rail's read-card body and assert the section anchors
    appear in the order ``_FIELD_SECTIONS_BY_KIND["rail"]`` declares.
    """
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        list_body = c.get("/l2_shape/rail/").text
        ids = re.findall(r'data-entity-id="([^"]+)"', list_body)
        assert ids, "fixture has no rails"
        body = c.get(f"/l2_shape/rail/{ids[0]}?body_only=1").text
    rendered = _section_names_in_order(body, "read-card-section")
    # The body-only fragment must contain at least Identity (every
    # rail has name + description); other sections depend on which
    # rail we hit but the relative order is invariant.
    assert "Identity" in rendered
    # Subsequence-of-declared-order check: rendered ⊆ declared in the
    # same relative order.
    declared = [
        label for label, _ in _FIELD_SECTIONS_BY_KIND["rail"]
    ]
    declared_index = {label: i for i, label in enumerate(declared)}
    indices = [declared_index[label] for label in rendered if label in declared_index]
    assert indices == sorted(indices), (
        f"rendered sections out of order: rendered={rendered}"
    )


def test_chain_read_card_renders_identity_and_composition_sections(
    writable_l2_yaml: Path,
) -> None:
    """Chain has just two sections (Identity + Composition); both must
    appear on every chain's read card since the fixture chains all
    declare ``parent`` + ``children``.
    """
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        list_body = c.get("/l2_shape/chain/").text
        ids = re.findall(r'data-entity-id="([^"]+)"', list_body)
        assert ids, "fixture has no chains"
        body = c.get(f"/l2_shape/chain/{ids[0]}?body_only=1").text
    rendered = _section_names_in_order(body, "read-card-section")
    assert rendered == ["Identity", "Composition"], (
        f"chain read card sections mismatch: {rendered}"
    )


def test_limit_schedule_read_card_renders_all_three_sections(
    writable_l2_yaml: Path,
) -> None:
    """LimitSchedule has Identity / Scope / Limit — three small
    sections that all populate from a non-degenerate fixture row.
    """
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        list_body = c.get("/l2_shape/limit_schedule/").text
        ids = re.findall(r'data-entity-id="([^"]+)"', list_body)
        assert ids, "fixture has no limit_schedules"
        body = c.get(
            f"/l2_shape/limit_schedule/{ids[0]}?body_only=1",
        ).text
    rendered = _section_names_in_order(body, "read-card-section")
    assert rendered == ["Identity", "Scope", "Limit"], (
        f"limit_schedule read card sections mismatch: {rendered}"
    )


# ---------------------------------------------------------------------------
# Edit form — same sections, same order, same anchors.
# ---------------------------------------------------------------------------


def test_rail_edit_form_renders_sections_in_declared_order(
    writable_l2_yaml: Path,
) -> None:
    """Fetch a rail's full edit page and confirm the fieldset
    ``data-section-name`` anchors appear in the same order as the
    section map.
    """
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        list_body = c.get("/l2_shape/rail/").text
        ids = re.findall(r'data-entity-id="([^"]+)"', list_body)
        assert ids, "fixture has no rails"
        edit_body = c.get(f"/l2_shape/rail/{ids[0]}/edit").text
    rendered = _section_names_in_order(edit_body, "edit-form-section")
    declared = [label for label, _ in _FIELD_SECTIONS_BY_KIND["rail"]]
    declared_index = {label: i for i, label in enumerate(declared)}
    indices = [
        declared_index[label] for label in rendered if label in declared_index
    ]
    assert indices == sorted(indices), (
        f"edit form sections out of order: rendered={rendered}"
    )
    # Every rendered section must be a declared one (no stale
    # "Other" bucket leaking on the rail edit form).
    assert all(label in declared_index for label in rendered), rendered


def test_read_card_and_edit_form_render_same_sections(
    writable_l2_yaml: Path,
) -> None:
    """The BX.4 invariant: pick a rail, fetch BOTH the read card body
    and the edit form, and assert the (filtered) section anchor
    sequences match. The operator should not see one set of buckets
    on the read card and a different set on the edit form.
    """
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        list_body = c.get("/l2_shape/rail/").text
        ids = re.findall(r'data-entity-id="([^"]+)"', list_body)
        assert ids, "fixture has no rails"
        rid = ids[0]
        read_body = c.get(f"/l2_shape/rail/{rid}?body_only=1").text
        edit_body = c.get(f"/l2_shape/rail/{rid}/edit").text
    read_sections = _section_names_in_order(read_body, "read-card-section")
    edit_sections = _section_names_in_order(edit_body, "edit-form-section")
    assert read_sections == edit_sections, (
        f"section parity drift: read={read_sections} edit={edit_sections}"
    )


def test_chain_edit_form_renders_identity_and_composition_sections(
    writable_l2_yaml: Path,
) -> None:
    """Pin the chain edit form's section anchors so a regression where
    one of the two chain sections drops surfaces immediately.
    """
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        list_body = c.get("/l2_shape/chain/").text
        ids = re.findall(r'data-entity-id="([^"]+)"', list_body)
        assert ids, "fixture has no chains"
        edit_body = c.get(f"/l2_shape/chain/{ids[0]}/edit").text
    rendered = _section_names_in_order(edit_body, "edit-form-section")
    assert rendered == ["Identity", "Composition"], (
        f"chain edit form sections mismatch: {rendered}"
    )


# ---------------------------------------------------------------------------
# Empty-section drop — single_leg rails hide two_leg-only sections.
# ---------------------------------------------------------------------------


def test_single_leg_rail_drops_two_leg_only_sections_when_empty(
    writable_l2_yaml: Path,
) -> None:
    """When a rail is single_leg, the FieldSpec ``subtype_only`` filter
    hides the two_leg-specific fields (source_role / destination_role
    / source_origin / destination_origin / expected_net). The Origin
    section still has the rail-level ``origin`` field, so it survives;
    the Conservation section drops entirely when expected_net is the
    only member and gets filtered out.

    The point of this test: filtering happens BEFORE sectioning, so a
    section whose only declared members vanish on this rail subtype
    must drop from the rendered output. Operator sees no empty
    fieldset with just a legend.
    """
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        list_body = c.get("/l2_shape/rail/").text
        # The spec_example fixture has single_leg rails (e.g.
        # ExternalRailOutbound is single_leg in many flavors).
        # Walk each rail until we find one whose edit page renders the
        # single-leg topology fields (leg_role / leg_direction) but
        # NOT the two-leg ones (source_role / destination_role).
        ids = re.findall(r'data-entity-id="([^"]+)"', list_body)
        single_leg_rid: str | None = None
        for rid in ids:
            edit_body = c.get(f"/l2_shape/rail/{rid}/edit").text
            if (
                'name="leg_role"' in edit_body
                and 'name="source_role"' not in edit_body
            ):
                single_leg_rid = rid
                break
        if single_leg_rid is None:
            pytest.skip("fixture has no single_leg rails to exercise")
        edit_body = c.get(f"/l2_shape/rail/{single_leg_rid}/edit").text
        read_body = c.get(
            f"/l2_shape/rail/{single_leg_rid}?body_only=1",
        ).text
    # Conservation section's only declared member is expected_net,
    # which is subtype_only=two_leg, so single_leg rails MUST not
    # render a Conservation fieldset (this is the empty-section drop).
    edit_sections = _section_names_in_order(edit_body, "edit-form-section")
    read_sections = _section_names_in_order(read_body, "read-card-section")
    assert "Conservation" not in edit_sections, (
        f"Conservation should drop on single_leg: edit={edit_sections}"
    )
    assert "Conservation" not in read_sections, (
        f"Conservation should drop on single_leg: read={read_sections}"
    )
    # The read card / edit form sections still agree.
    assert edit_sections == read_sections, (
        f"single_leg parity drift: edit={edit_sections} read={read_sections}"
    )
