"""BTa.1 — `common/html/_side_panel.py` unit tests.

Pins:
- GLOSSARY shape (one dict, every term has a non-empty markdown body)
- Render helpers produce the expected drawer + trigger HTML shape
- Route handlers return the right HTML for full / per-term / unknown
- The top-nav `[?]` trigger lands in `emit_top_nav` output
- The drawer container lands in `emit_top_nav` output (single instance
  per page)
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
from recon_gen.common.html._side_panel import (
    GLOSSARY,
    SURFACES_AS,
    render_side_panel_drawer_container,
    render_side_panel_trigger,
)
from recon_gen.common.html.render import (
    build_top_nav_entries,
    emit_top_nav,
)
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
from tests._test_helpers import make_test_config


# -- GLOSSARY shape ---------------------------------------------------------


def test_glossary_keys_are_lowercase_slugs() -> None:
    """Each key uses lowercase letters + hyphens only — the per-term
    route's path param normalizes via `.lower()`, and the display name
    is generated via `key.replace('-', ' ').title()`."""
    for key in GLOSSARY:
        assert key == key.lower()
        assert " " not in key
        # `l2` carries a digit; allow alphanumeric + hyphen.
        assert key.replace("-", "").isalnum(), key


def test_glossary_bodies_are_non_empty_markdown() -> None:
    """Every term has a non-empty body; markdown bold (`**...**`) is
    present in at least most entries (the display convention for the
    term-name lead)."""
    bold_count = 0
    for body in GLOSSARY.values():
        assert body.strip(), "empty glossary body"
        assert len(body) > 50, f"glossary body too short: {body[:30]}..."
        if "**" in body:
            bold_count += 1
    # Most entries lead with the term name in bold.
    assert bold_count >= len(GLOSSARY) - 2


def test_glossary_includes_load_bearing_l2_terms() -> None:
    """The cold-read flagged these as the highest-friction vocabulary
    items. Pin to prevent silent removal."""
    for must_have in ("l2", "rail", "transfer-template", "chain", "limit-schedule"):
        assert must_have in GLOSSARY, f"missing must-have term {must_have!r}"


def test_glossary_includes_bx12_per_field_terms() -> None:
    """BX.12 — the per-field vocabulary the cold-read v1a flagged as
    needing inline ``[?]`` triggers on the L2 entity edit pages. Pin
    these so a silent rename / removal breaks loudly here, not at the
    operator's first click on the chip."""
    for must_have in (
        # Rail
        "posted-requirements", "bundles-activity", "cadence",
        "origin-overrides",
        # Chain
        "xor", "fan-in", "expected-parent-count",
        # LimitSchedule
        "direction",
    ):
        assert must_have in GLOSSARY, f"missing BX.12 term {must_have!r}"


# -- Render helpers --------------------------------------------------------


def test_drawer_container_has_aria_complementary_and_close_button() -> None:
    """Per BTa.0 Lock 1 — ARIA role=complementary + close affordance."""
    html = render_side_panel_drawer_container()
    assert 'role="complementary"' in html
    assert 'data-side-panel-close' in html
    assert 'aria-label="Close help panel"' in html
    assert 'id="side-panel"' in html
    assert 'id="side-panel-body"' in html
    # Slide-in transition (translate-x-full hidden by default).
    assert 'translate-x-full' in html
    # Click-outside overlay.
    assert 'data-side-panel-overlay' in html


def test_drawer_container_includes_escape_key_handler() -> None:
    """Escape closes the drawer (operator's expectation for any
    modal-ish surface)."""
    html = render_side_panel_drawer_container()
    assert "key === 'Escape'" in html


def test_drawer_container_exposes_window_open_hook() -> None:
    """CY.3 — `window.__sidePanelOpen()` is exposed by the IIFE so the
    CY.6 ctxmenu entry (and any future programmatic opener) can slide
    the drawer in without synthesizing a `[data-side-panel-trigger]`
    click."""
    html = render_side_panel_drawer_container()
    assert "window.__sidePanelOpen" in html


def test_side_panel_trigger_renders_button_with_hx_get() -> None:
    """Triggers POST nothing; they hx-get a fragment into the drawer
    body. data-side-panel-trigger tells the panel JS to slide open."""
    html = render_side_panel_trigger(
        "/studio/side-panel/glossary/rail",
        label="?",
        aria_label="What is a rail?",
    )
    assert 'data-side-panel-trigger' in html
    assert 'hx-get="/studio/side-panel/glossary/rail"' in html
    assert 'hx-target="#side-panel-body"' in html
    assert 'aria-label="What is a rail?"' in html
    assert '>?<' in html


# -- Top-nav integration --------------------------------------------------


def test_top_nav_emits_glossary_trigger_button() -> None:
    """BTa.1 — every page rendering the top-nav gets the `[?]` button
    + the drawer container."""
    entries = build_top_nav_entries(
        dashboards=[("smoke", "Smoke")],
        docs_url=None,
        studio_enabled=True,
    )
    html = emit_top_nav(entries=entries, active_href="/")
    # Glossary trigger lands in the nav.
    assert 'hx-get="/studio/side-panel/glossary"' in html
    assert 'data-side-panel-trigger' in html
    assert 'aria-label="Open glossary side panel"' in html
    # Drawer chrome lands once (after the nav).
    assert 'id="side-panel"' in html
    assert html.count('id="side-panel-body"') == 1


def test_top_nav_drawer_container_only_renders_once_per_call() -> None:
    """Pin against accidental double-injection (sliding two drawers
    open at once would target the same `#side-panel-body` and
    break)."""
    entries = build_top_nav_entries(
        dashboards=[("smoke", "Smoke")], docs_url=None, studio_enabled=True,
    )
    html = emit_top_nav(entries=entries)
    assert html.count('id="side-panel"') == 1
    assert html.count('data-side-panel-overlay') == 1


def test_top_nav_empty_entries_skips_nav_and_drawer() -> None:
    """No nav entries = no drawer either (the single-surface deploy
    contract; caller filters)."""
    assert emit_top_nav(entries=[]) == ""


# -- Route handlers --------------------------------------------------------


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


def test_glossary_full_route_returns_dl_with_every_term(
    writable_l2_yaml: Path,
) -> None:
    """GET /studio/side-panel/glossary returns the full glossary as
    a definition list. Every GLOSSARY key surfaces as a <dt>."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/studio/side-panel/glossary")
        assert resp.status_code == 200
        body = resp.text
    assert body.startswith("<dl")
    # Every term renders (Title-Case display name in a <dt>).
    for key in GLOSSARY:
        display = key.replace("-", " ").title()
        assert f">{display}</dt>" in body, f"missing term {display!r}"


def test_glossary_term_route_returns_single_term(
    writable_l2_yaml: Path,
) -> None:
    """GET /studio/side-panel/glossary/<term> returns just that
    term's definition."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/studio/side-panel/glossary/rail")
        assert resp.status_code == 200
        body = resp.text
    assert "Rail" in body
    # Markdown bold renders to <strong>.
    assert "<strong>Rail</strong>" in body


def test_glossary_unknown_term_returns_404_with_helpful_text(
    writable_l2_yaml: Path,
) -> None:
    """Unknown term → 404 + a pointer to the full glossary."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/studio/side-panel/glossary/not-a-real-term")
        assert resp.status_code == 404
        body = resp.text
    assert "not-a-real-term" in body
    assert "Help" in body or "glossary" in body


def test_glossary_term_route_is_case_insensitive(
    writable_l2_yaml: Path,
) -> None:
    """The route normalizes the path param to lowercase so
    `/RAIL` and `/rail` resolve to the same term."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body_lower = c.get("/studio/side-panel/glossary/rail").text
        body_upper = c.get("/studio/side-panel/glossary/RAIL").text
    # Same content (display name normalization is uniform).
    assert body_lower == body_upper


# -- BX.12 — FieldSpec.glossary_anchor anti-drift -------------------------


def _all_field_spec_anchors() -> list[tuple[str, str, str]]:
    """Collect every (kind, field_name, anchor) triple from the editor's
    per-kind FieldSpec lists. Used by both directions of the anti-drift
    test (anchor → GLOSSARY + chip → field reachable from page)."""
    from recon_gen.common.html._studio_editor_routes import (
        _FIELD_SPECS_BY_KIND,
    )
    triples: list[tuple[str, str, str]] = []
    for kind, specs in _FIELD_SPECS_BY_KIND.items():
        for spec in specs:
            if spec.glossary_anchor is not None:
                triples.append((kind, spec.name, str(spec.glossary_anchor)))
    return triples


def test_every_field_spec_glossary_anchor_resolves_to_glossary() -> None:
    """BX.12 anti-drift: every FieldSpec.glossary_anchor MUST point to
    a real GLOSSARY key. A typo here breaks at sessionstart, not at
    the operator's first click on the chip."""
    triples = _all_field_spec_anchors()
    # Should have shipped at least one anchor — guard against the
    # entire wiring layer getting deleted in a refactor without anyone
    # noticing.
    assert triples, "no FieldSpec carries glossary_anchor — wiring removed?"
    for kind, field_name, anchor in triples:
        assert anchor in GLOSSARY, (
            f"{kind}.{field_name} carries glossary_anchor={anchor!r} but "
            f"GLOSSARY has no such key. Add the entry to "
            f"common/html/_side_panel.py::GLOSSARY or fix the anchor."
        )


def test_bx12_load_bearing_anchors_wired_into_field_specs() -> None:
    """BX.12 — pin the cold-read-flagged per-field anchors to their
    target FieldSpec so a future cleanup that drops them surfaces
    here, not in a cold-read regression. Maps (kind, field) → anchor.
    """
    expected_wirings = {
        ("rail", "posted_requirements"): "posted-requirements",
        ("rail", "bundles_activity"): "bundles-activity",
        ("rail", "cadence"): "cadence",
        ("rail", "source_origin"): "origin-overrides",
        ("rail", "destination_origin"): "origin-overrides",
        ("chain", "children"): "xor",
        ("limit_schedule", "direction"): "direction",
    }
    actual = {
        (kind, field): anchor
        for kind, field, anchor in _all_field_spec_anchors()
    }
    for key, expected_anchor in expected_wirings.items():
        kind, field = key
        assert key in actual, (
            f"{kind}.{field} lost its glossary_anchor wiring — "
            f"expected {expected_anchor!r}"
        )
        assert actual[key] == expected_anchor, (
            f"{kind}.{field} anchor drifted: expected "
            f"{expected_anchor!r}, got {actual[key]!r}"
        )


def test_glossary_chip_emits_side_panel_trigger() -> None:
    """The ``_glossary_chip_html`` helper emits the same
    ``data-side-panel-trigger`` shape the drawer JS recognizes. Verifies
    the chip is wired through ``render_side_panel_trigger`` rather than
    hand-rolling a button (which would silently miss future trigger-
    semantics changes)."""
    from recon_gen.common.html._studio_editor_routes import (
        FieldSpec,
        _glossary_chip_html,
    )
    from recon_gen.common.ids import GlossaryAnchor

    spec = FieldSpec(
        name="cadence",
        label="Cadence",
        helper="",
        kind="text",
        glossary_anchor=GlossaryAnchor("cadence"),
    )
    html = _glossary_chip_html(spec)
    assert html.strip(), "non-empty when anchor set"
    assert "data-side-panel-trigger" in html
    assert "/studio/side-panel/glossary/cadence" in html
    assert 'aria-label="Open glossary entry for Cadence"' in html
    # When anchor is None → empty string (caller concatenates).
    spec_empty = FieldSpec(name="x", label="X", helper="", kind="text")
    assert _glossary_chip_html(spec_empty) == ""


def test_rail_chain_limitschedule_edit_pages_contain_glossary_chips(
    writable_l2_yaml: Path,
) -> None:
    """Per-page wiring check: hit the Rail / Chain / LimitSchedule edit
    fragments + verify the BX.12 chips actually land in the rendered
    HTML. Catches "FieldSpec has anchor but renderer skips it"
    regressions (e.g., a future kind-specific renderer that forgets to
    call ``_glossary_chip_html``)."""
    from recon_gen.common.l2.cache import L2InstanceCache

    cache = L2InstanceCache.from_path(writable_l2_yaml)
    instance = cache.get()

    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        # Rail: pick the first rail in the L2 + hit its edit fragment.
        rail_name = str(instance.rails[0].name)
        rail_resp = c.get(f"/l2_shape/rail/{rail_name}/edit")
        assert rail_resp.status_code == 200, rail_resp.text
        rail_body = rail_resp.text
        # `cadence` is rail-universal (always renders); the chip MUST
        # appear. The same for `posted_requirements`.
        assert "/studio/side-panel/glossary/cadence" in rail_body
        assert "/studio/side-panel/glossary/posted-requirements" in rail_body

        # Chain: first chain in the L2. Composite key shape
        # ``<parent>::<sorted-children-csv>`` per editor lookup.
        if instance.chains:
            ch = instance.chains[0]
            children_csv = ",".join(sorted(str(c.name) for c in ch.children))
            chain_id = f"{ch.parent}::{children_csv}"
            chain_resp = c.get(f"/l2_shape/chain/{chain_id}/edit")
            assert chain_resp.status_code == 200, chain_resp.text
            assert "/studio/side-panel/glossary/xor" in chain_resp.text

        # LimitSchedule: first one in the L2. Composite key shape
        # ``<parent_role>::<rail>::<direction>`` per editor lookup.
        if instance.limit_schedules:
            ls = instance.limit_schedules[0]
            ls_id = f"{ls.parent_role}::{ls.rail}::{ls.direction}"
            ls_resp = c.get(f"/l2_shape/limit_schedule/{ls_id}/edit")
            assert ls_resp.status_code == 200, ls_resp.text
            assert "/studio/side-panel/glossary/direction" in ls_resp.text


# -- BX.13 — SurfaceAnchor + SURFACES_AS anti-drift ------------------------


def test_surfaces_as_keys_are_lowercase_slugs() -> None:
    """Mirror of the GLOSSARY shape contract: keys are lowercase + use
    hyphens (no spaces / digits-only). The per-anchor route normalizes
    via ``.lower()``, and the display name is generated via
    ``key.replace('-', ' ').title()``."""
    for key in SURFACES_AS:
        assert key == key.lower()
        assert " " not in key
        assert key.replace("-", "").isalnum(), key


def test_surfaces_as_bodies_carry_bullet_lists() -> None:
    """Every surfaces-as entry is a bullet list — that's the surface-
    location enumeration the chip's job is to deliver. Pin the markdown
    bullet form (``- `` prefix) so a future edit that loses the bullet
    convention surfaces here, not as a layout regression."""
    for key, body in SURFACES_AS.items():
        assert body.strip(), f"empty surfaces-as body: {key}"
        # 4+ bullet items per the style note — count the line-starts.
        bullets = [line for line in body.splitlines() if line.startswith("- ")]
        assert len(bullets) >= 2, (
            f"{key}: surfaces-as bodies should enumerate ≥2 surface "
            f"locations (the chip's whole job); got {len(bullets)} bullets."
        )


def test_surfaces_as_includes_bx13_load_bearing_anchors() -> None:
    """BX.13 — pin the post-BXa-scope anchors the cell explicitly ships.
    Silent removal here breaks the operator-facing chip on the editor
    instance + theme singleton pages."""
    must_haves = {
        # Instance singleton.
        "institution-name", "institution-description",
        # Theme identity.
        "theme-name", "theme-version-description",
        # Theme brand / state / chart colours.
        "theme-accent", "theme-accent-fg", "theme-link-tint",
        "theme-success", "theme-success-fg",
        "theme-danger", "theme-danger-fg",
        "theme-warning", "theme-warning-fg",
        "theme-dimension", "theme-dimension-fg",
        "theme-measure", "theme-measure-fg",
        # Data palette + gradient + empty.
        "theme-data-colors", "theme-empty-fill-color", "theme-gradient",
    }
    missing = must_haves - set(SURFACES_AS.keys())
    assert not missing, f"missing BX.13 SURFACES_AS keys: {sorted(missing)}"


def test_surfaces_as_entry_route_returns_single_anchor(
    writable_l2_yaml: Path,
) -> None:
    """GET /studio/side-panel/surfaces-as/<anchor> returns the matching
    entry's content + a "Where does this surface?" header that visually
    distinguishes it from the glossary surface."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/studio/side-panel/surfaces-as/theme-accent")
        assert resp.status_code == 200
        body = resp.text
    # Distinct visual header so the operator knows which surface they
    # opened.
    assert "Where does this surface?" in body
    # Display-name capitalization derived from the anchor.
    assert "Theme Accent" in body
    # Bullet content rendered as a <ul>.
    assert "<ul>" in body and "<li>" in body
    # Markdown bold survives the renderer.
    assert "<strong>L1 KPI bars</strong>" in body


def test_surfaces_as_unknown_anchor_returns_404(
    writable_l2_yaml: Path,
) -> None:
    """Unknown anchor → 404 with a pointer to the source file so the
    operator (or follow-up cell) knows where to add the entry."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/studio/side-panel/surfaces-as/not-a-real-anchor")
        assert resp.status_code == 404
        body = resp.text
    assert "not-a-real-anchor" in body
    assert "_side_panel.py" in body or "SURFACES_AS" in body


def test_surfaces_as_route_is_case_insensitive(
    writable_l2_yaml: Path,
) -> None:
    """Same case-insensitivity contract as the glossary route — path
    param normalizes via ``.lower()`` so ``THEME-ACCENT`` and
    ``theme-accent`` resolve identically."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body_lower = c.get("/studio/side-panel/surfaces-as/theme-accent").text
        body_upper = c.get("/studio/side-panel/surfaces-as/THEME-ACCENT").text
    assert body_lower == body_upper


def _all_singleton_surface_anchors() -> list[tuple[str, str, str]]:
    """Collect every (form_kind, field_name, anchor) triple wired in the
    instance + theme singleton form fields. Used by the anti-drift test.
    """
    from recon_gen.common.html._studio_editor_routes import (
        _INSTANCE_STRUCTURED_FIELDS,
        _THEME_COLOR_FIELDS,
        _THEME_TEXT_FIELDS,
    )
    triples: list[tuple[str, str, str]] = []
    for fname, _, _, _, anchor in _INSTANCE_STRUCTURED_FIELDS:
        if anchor is not None:
            triples.append(("instance", fname, str(anchor)))
    for fname, _, _, _, anchor in _THEME_TEXT_FIELDS:
        if anchor is not None:
            triples.append(("theme", fname, str(anchor)))
    for fname, _, _, anchor in _THEME_COLOR_FIELDS:
        if anchor is not None:
            triples.append(("theme", fname, str(anchor)))
    return triples


def test_every_wired_surface_anchor_resolves_to_surfaces_as() -> None:
    """BX.13 anti-drift: every ``SurfaceAnchor`` wired into the instance
    + theme singleton form fields MUST point to a real ``SURFACES_AS``
    key. A typo here breaks at sessionstart, not at the operator's
    first click on the chip."""
    triples = _all_singleton_surface_anchors()
    # Guard against the entire wiring layer being deleted in a refactor.
    assert triples, "no singleton field carries a SurfaceAnchor — wiring removed?"
    for form_kind, fname, anchor in triples:
        assert anchor in SURFACES_AS, (
            f"{form_kind}.{fname} carries surfaces_anchor={anchor!r} but "
            f"SURFACES_AS has no such key. Add the entry to "
            f"common/html/_side_panel.py::SURFACES_AS or fix the anchor."
        )


def test_bx13_load_bearing_anchors_wired_into_singleton_fields() -> None:
    """Pin the BX.13 (form_kind, field) → anchor wirings so a future
    cleanup that drops them surfaces here, not in a cold-read regression.
    """
    expected = {
        # Instance singleton.
        ("instance", "institution_name"): "institution-name",
        # Theme identity.
        ("theme", "theme_name"): "theme-name",
        ("theme", "version_description"): "theme-version-description",
        # Theme brand colours.
        ("theme", "accent"): "theme-accent",
        ("theme", "accent_fg"): "theme-accent-fg",
        ("theme", "link_tint"): "theme-link-tint",
        # State colours.
        ("theme", "success"): "theme-success",
        ("theme", "danger"): "theme-danger",
        ("theme", "warning"): "theme-warning",
        # Chart-axis chips.
        ("theme", "dimension"): "theme-dimension",
        ("theme", "measure"): "theme-measure",
    }
    actual = {
        (form_kind, fname): anchor
        for form_kind, fname, anchor in _all_singleton_surface_anchors()
    }
    for key, expected_anchor in expected.items():
        form_kind, fname = key
        assert key in actual, (
            f"{form_kind}.{fname} lost its SurfaceAnchor wiring — "
            f"expected {expected_anchor!r}"
        )
        assert actual[key] == expected_anchor, (
            f"{form_kind}.{fname} anchor drifted: expected "
            f"{expected_anchor!r}, got {actual[key]!r}"
        )


def test_surfaces_as_chip_emits_where_label_and_side_panel_trigger() -> None:
    """``_surfaces_as_chip_html`` emits the same
    ``data-side-panel-trigger`` shape the drawer JS recognizes but with
    a distinct ``where?`` label so the operator can tell it apart from
    the BX.12 ``?`` glossary chip at a glance."""
    from recon_gen.common.html._studio_editor_routes import (
        _surfaces_as_chip_html,
    )
    from recon_gen.common.ids import SurfaceAnchor

    html = _surfaces_as_chip_html(
        SurfaceAnchor("theme-accent"), field_label="Accent",
    )
    assert html.strip(), "non-empty when anchor set"
    assert "data-side-panel-trigger" in html
    assert "/studio/side-panel/surfaces-as/theme-accent" in html
    # Distinct label vs. the glossary chip's bare "?".
    assert ">where?<" in html
    # ARIA label names the field + the surface concept.
    assert "Accent" in html and "surfaces" in html
    # None anchor → empty string (caller concatenates).
    assert _surfaces_as_chip_html(None, field_label="X") == ""


def test_instance_singleton_edit_page_contains_surfaces_as_chips(
    writable_l2_yaml: Path,
) -> None:
    """Per-page wiring check for the instance singleton — hit
    ``GET /l2_shape/instance/`` and verify the BX.13 chips for
    institution_name + description actually land in the rendered HTML.
    Catches "form has anchor but renderer skips it" regressions."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/l2_shape/instance/")
        assert resp.status_code == 200, resp.text
        body = resp.text
    assert "/studio/side-panel/surfaces-as/institution-name" in body
    assert "/studio/side-panel/surfaces-as/institution-description" in body


def test_theme_singleton_edit_page_contains_surfaces_as_chips(
    writable_l2_yaml: Path,
) -> None:
    """Per-page wiring check for the theme singleton — hit
    ``GET /l2_shape/theme/`` and verify the BX.13 chips for the load-
    bearing theme fields actually land in the rendered HTML."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/l2_shape/theme/")
        assert resp.status_code == 200, resp.text
        body = resp.text
    # Identity scalars.
    assert "/studio/side-panel/surfaces-as/theme-name" in body
    assert "/studio/side-panel/surfaces-as/theme-version-description" in body
    # Brand + state colours.
    assert "/studio/side-panel/surfaces-as/theme-accent" in body
    assert "/studio/side-panel/surfaces-as/theme-danger" in body
    assert "/studio/side-panel/surfaces-as/theme-success" in body
    assert "/studio/side-panel/surfaces-as/theme-warning" in body
    # Data palette + gradient + empty.
    assert "/studio/side-panel/surfaces-as/theme-data-colors" in body
    assert "/studio/side-panel/surfaces-as/theme-empty-fill-color" in body
    assert "/studio/side-panel/surfaces-as/theme-gradient" in body
