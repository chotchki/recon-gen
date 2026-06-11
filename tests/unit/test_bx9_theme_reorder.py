"""BX.9 — theme editor reorder: essentials at top + Advanced collapsed.

Operator-locked design (Option C, 2026-06-11): the theme editor flat
17-color form was overwhelming. Most institutions only customize their
primary brand colour + a secondary surface + the logo — the other 16
colour fields stay at DEFAULT_PRESET. The editor now leads with an
``Essentials`` section (accent + secondary_bg + logo) plus a live-preview
sample card; everything else collapses into ``<details>Advanced</details>``
(default-closed unless the L2 already customized one of those fields, in
which case default-open so the operator sees what they had set).

Auto-save on field blur (no Save button per BX.9 operator lock) via
debounced ``hx-post="/l2_shape/theme/field"``. Live-preview re-renders
through ``hx-post="/l2_shape/theme/preview"`` on input events.

Tests cover:

- Essentials section renders the three essential fields at the top
  with a live-preview card next to them.
- Advanced section wraps the rest in ``<details>`` with
  ``data-section-name="advanced"``; default-closed when the theme
  matches DEFAULT_PRESET, default-open when any advanced field differs.
- ``data-section-name="essentials"`` + ``data-section-name="advanced"``
  anchors present so browser drivers can locate the operator-facing
  regions per [feedback_browser_drivers_user_facing_locators].
- Auto-save POST endpoint validates + persists single-field updates.
- Live-preview POST endpoint returns the preview card HTML.
- Schema unchanged: ThemePreset still carries all original fields;
  the editor only reorders rendering.
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
    _render_theme_form,
)
from recon_gen.common.html._studio_routes import make_studio_routes
from recon_gen.common.html._theme_editor_bx9 import (
    _THEME_ESSENTIAL_COLOR_FIELDS,
    _THEME_ESSENTIAL_FIELDS,
    _THEME_ESSENTIAL_URL_FIELDS,
    _render_theme_preview_card,
    _theme_advanced_has_customizations,
    _theme_default_dict,
)
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.l2.theme import ThemePreset
from recon_gen.common.theme import DEFAULT_PRESET
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


def _compose_advanced_field_sets() -> tuple[
    frozenset[str], frozenset[str], frozenset[str],
]:
    """Helper exposing the same advanced-field sets the bx9 helper
    expects, so individual tests can call
    ``_theme_advanced_has_customizations`` directly without re-deriving
    them."""
    from recon_gen.common.html._studio_editor_routes import (
        _THEME_COLOR_FIELDS,
        _THEME_OPTIONAL_URL_FIELDS,
        _THEME_TEXT_FIELDS,
    )
    all_color = {fname for fname, _, _, _ in _THEME_COLOR_FIELDS}
    advanced_color = frozenset(all_color - set(_THEME_ESSENTIAL_COLOR_FIELDS))
    text = frozenset(fname for fname, _, _, _, _ in _THEME_TEXT_FIELDS)
    all_url = {fname for fname, _, _ in _THEME_OPTIONAL_URL_FIELDS}
    advanced_url = frozenset(all_url - set(_THEME_ESSENTIAL_URL_FIELDS))
    return advanced_color, advanced_url, text


# ---------------------------------------------------------------------------
# Schema invariant — ThemePreset shape unchanged (Option C lock)
# ---------------------------------------------------------------------------


def test_theme_preset_schema_unchanged() -> None:
    """Per the BX.9 operator lock, ThemePreset keeps every original field.

    The editor reorder is rendering-only — adding / removing / renaming
    a ThemePreset field would cascade through the L2 loader + audit PDF
    + QS theme builder, which the lock explicitly prohibits.
    """
    expected = {
        "theme_name", "version_description", "analysis_name_prefix",
        "data_colors", "empty_fill_color", "gradient",
        "primary_bg", "secondary_bg", "primary_fg", "secondary_fg",
        "accent", "accent_fg", "link_tint",
        "danger", "danger_fg",
        "warning", "warning_fg",
        "success", "success_fg",
        "dimension", "dimension_fg",
        "measure", "measure_fg",
        "logo", "favicon",
    }
    actual = {f.name for f in ThemePreset.__dataclass_fields__.values()}
    assert actual == expected, (
        f"ThemePreset shape drifted: missing={expected - actual} "
        f"extra={actual - expected}"
    )


def test_essentials_are_three_fields() -> None:
    """The operator lock pinned three essentials: accent (primary brand
    color), secondary_bg (secondary surface), logo (logo URL). Anything
    else is Advanced."""
    assert len(_THEME_ESSENTIAL_FIELDS) == 3
    assert set(_THEME_ESSENTIAL_FIELDS) == {"accent", "secondary_bg", "logo"}
    assert set(_THEME_ESSENTIAL_COLOR_FIELDS) == {"accent", "secondary_bg"}
    assert set(_THEME_ESSENTIAL_URL_FIELDS) == {"logo"}


# ---------------------------------------------------------------------------
# Pure-helper tests — _theme_advanced_has_customizations
# ---------------------------------------------------------------------------


def test_advanced_has_customizations_is_false_when_default() -> None:
    """The DEFAULT_PRESET dict must read as ``no customizations``."""
    advanced_color, advanced_url, text = _compose_advanced_field_sets()
    assert _theme_advanced_has_customizations(
        _theme_default_dict(),
        advanced_color_fields=advanced_color,
        advanced_url_fields=advanced_url,
        text_field_names=text,
    ) is False


def test_advanced_has_customizations_false_for_essentials_changes() -> None:
    """Essentials customization alone doesn't open the Advanced section —
    only differences in the advanced fields do."""
    advanced_color, advanced_url, text = _compose_advanced_field_sets()
    d = _theme_default_dict()
    d["accent"] = "#ff0000"
    d["secondary_bg"] = "#abcdef"
    d["logo"] = "https://example.com/logo.png"
    assert _theme_advanced_has_customizations(
        d,
        advanced_color_fields=advanced_color,
        advanced_url_fields=advanced_url,
        text_field_names=text,
    ) is False


def test_advanced_has_customizations_true_when_advanced_color_changed() -> None:
    """Changing primary_bg (an Advanced colour) opens the Advanced section."""
    advanced_color, advanced_url, text = _compose_advanced_field_sets()
    d = _theme_default_dict()
    d["primary_bg"] = "#000000"
    assert _theme_advanced_has_customizations(
        d,
        advanced_color_fields=advanced_color,
        advanced_url_fields=advanced_url,
        text_field_names=text,
    ) is True


def test_advanced_has_customizations_true_when_data_colors_changed() -> None:
    """Data colour palette is advanced; a change opens the section."""
    advanced_color, advanced_url, text = _compose_advanced_field_sets()
    d = _theme_default_dict()
    d["data_colors"] = ["#111111"]
    assert _theme_advanced_has_customizations(
        d,
        advanced_color_fields=advanced_color,
        advanced_url_fields=advanced_url,
        text_field_names=text,
    ) is True


# ---------------------------------------------------------------------------
# _render_theme_form structure
# ---------------------------------------------------------------------------


def test_form_renders_essentials_section_first() -> None:
    """Essentials section anchor must appear BEFORE the Advanced anchor."""
    body = _render_theme_form(_theme_default_dict())
    ess_idx = body.find('data-section-name="essentials"')
    adv_idx = body.find('data-section-name="advanced"')
    assert ess_idx >= 0, "essentials section anchor missing"
    assert adv_idx >= 0, "advanced section anchor missing"
    assert ess_idx < adv_idx, "essentials must render before advanced"


def test_form_renders_essential_field_inputs() -> None:
    """Each of the three essentials must have its own input by name."""
    body = _render_theme_form(_theme_default_dict())
    for fname in _THEME_ESSENTIAL_FIELDS:
        assert f'name="{fname}"' in body, (
            f"essentials section missing input for {fname!r}"
        )


def test_form_renders_preview_card() -> None:
    """The live-preview card must be present in the essentials section,
    targeted by ``#theme-preview-card`` (the htmx swap target)."""
    body = _render_theme_form(_theme_default_dict())
    assert 'id="theme-preview-card"' in body
    assert 'data-role="theme-preview"' in body


def test_advanced_section_closed_when_at_default() -> None:
    """When every Advanced field matches DEFAULT_PRESET, the
    ``<details>`` element renders without the ``open`` attribute."""
    body = _render_theme_form(_theme_default_dict())
    match = re.search(
        r'<details[^>]*data-section-name="advanced"[^>]*>', body,
    )
    assert match is not None, "advanced <details> missing"
    assert " open" not in match.group(0), (
        f"advanced should default-closed at default preset; got: {match.group(0)}"
    )


def test_advanced_section_open_when_customized() -> None:
    """When any Advanced field is non-default, the ``<details>`` element
    renders with the ``open`` attribute so the operator sees what they
    had customized."""
    d = _theme_default_dict()
    d["primary_bg"] = "#012345"
    body = _render_theme_form(d)
    match = re.search(
        r'<details[^>]*data-section-name="advanced"[^>]*>', body,
    )
    assert match is not None, "advanced <details> missing"
    assert " open" in match.group(0), (
        f"advanced should default-open when customized; got: {match.group(0)}"
    )


def test_essentials_inputs_wire_auto_save() -> None:
    """Each essentials field must carry hx-post to the auto-save
    endpoint with a debounced blur trigger."""
    body = _render_theme_form(_theme_default_dict())
    assert body.count('hx-post="/l2_shape/theme/field"') >= len(
        _THEME_ESSENTIAL_FIELDS,
    ), "auto-save hx-post missing on essentials"
    assert 'hx-trigger="blur changed delay:300ms"' in body


def test_essentials_inputs_wire_live_preview() -> None:
    """Each essentials field must carry hx-post to the preview endpoint
    with an input-event trigger so the preview card updates as the
    operator types/picks a colour."""
    body = _render_theme_form(_theme_default_dict())
    assert 'hx-post="/l2_shape/theme/preview"' in body
    assert 'hx-target="#theme-preview-card"' in body
    assert 'hx-trigger="input changed delay:200ms"' in body


def test_essentials_copy_lock() -> None:
    """The operator-locked copy must surface verbatim in the essentials
    section so the BX.9 promise to operators stays intact."""
    body = _render_theme_form(_theme_default_dict())
    assert (
        "Most institutions only need the essentials below" in body
    ), "operator-locked copy missing from essentials section"


def test_advanced_section_omits_essentials() -> None:
    """The essentials fields are rendered ONCE — they live in the
    essentials section, NOT also under Advanced. Otherwise blur on
    the Advanced copy races with the Essentials copy."""
    body = _render_theme_form(_theme_default_dict())
    accent_count = body.count('name="accent"')
    assert accent_count == 1, (
        f"accent should render exactly once (essentials only); got {accent_count}"
    )
    secondary_bg_count = body.count('name="secondary_bg"')
    assert secondary_bg_count == 1, (
        f"secondary_bg should render exactly once (essentials only); "
        f"got {secondary_bg_count}"
    )
    logo_count = body.count('name="logo"')
    assert logo_count == 1, (
        f"logo should render exactly once (essentials only); got {logo_count}"
    )


# ---------------------------------------------------------------------------
# Live-preview card content
# ---------------------------------------------------------------------------


def test_preview_card_uses_current_values() -> None:
    """The preview card must render with the supplied accent + secondary_bg
    inline styles so the operator sees their chosen colours land."""
    d = _theme_default_dict()
    d["accent"] = "#ff0000"
    d["secondary_bg"] = "#00ff00"
    card = _render_theme_preview_card(d)
    assert "#ff0000" in card
    assert "#00ff00" in card


def test_preview_card_falls_back_to_default_when_blank() -> None:
    """A blank field falls back to DEFAULT_PRESET so the preview never
    renders with #000000 when the operator clears the hex input."""
    d: dict[str, object] = {"accent": ""}
    card = _render_theme_preview_card(d)
    assert DEFAULT_PRESET.accent.lower() in card.lower()


def test_preview_card_renders_logo_url_as_img() -> None:
    """A URL logo renders as an ``<img>`` tag so the operator sees their
    brand mark land in context."""
    d = _theme_default_dict()
    d["logo"] = "https://example.com/logo.svg"
    card = _render_theme_preview_card(d)
    assert "<img" in card
    assert 'src="https://example.com/logo.svg"' in card


# ---------------------------------------------------------------------------
# Route integration — GET /l2_shape/theme/ renders the essentials form
# ---------------------------------------------------------------------------


def test_theme_singleton_page_renders_essentials(
    writable_l2_yaml: Path,
) -> None:
    """GET /l2_shape/theme/ must include both section anchors."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/l2_shape/theme/").text
    assert 'data-section-name="essentials"' in body
    assert 'data-section-name="advanced"' in body


# ---------------------------------------------------------------------------
# Auto-save endpoint — POST /l2_shape/theme/field
# ---------------------------------------------------------------------------


def test_theme_field_save_persists_single_field(
    writable_l2_yaml: Path,
) -> None:
    """POST a single accent change ⇒ 204; the cache's theme.accent
    must reflect the new value so the next page render shows it."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.post(
            "/l2_shape/theme/field",
            data={"_field": "accent", "accent": "#abcdef"},
        )
        assert resp.status_code == 204, resp.text
        body = c.get("/l2_shape/theme/").text
    assert 'value="#abcdef"' in body, (
        "accent value didn't persist through the field-save endpoint"
    )


def test_theme_field_save_rejects_missing_field_name(
    writable_l2_yaml: Path,
) -> None:
    """POST without _field ⇒ 400."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.post(
            "/l2_shape/theme/field",
            data={"accent": "#abcdef"},
        )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Live-preview endpoint — POST /l2_shape/theme/preview
# ---------------------------------------------------------------------------


def test_theme_preview_endpoint_returns_card_html(
    writable_l2_yaml: Path,
) -> None:
    """POST the essentials values ⇒ 200 with the preview card fragment
    that includes the new accent colour."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.post(
            "/l2_shape/theme/preview",
            data={"accent": "#ff00ff"},
        )
    assert resp.status_code == 200, resp.text
    assert 'id="theme-preview-card"' in resp.text
    assert "#ff00ff" in resp.text


def test_theme_preview_does_not_persist(
    writable_l2_yaml: Path,
) -> None:
    """The preview endpoint MUST NOT save the operator's typed value to
    the cache — that's the field-save endpoint's job."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        before = c.get("/l2_shape/theme/").text
        original_accent_match = re.search(
            r'<input type="text" id="field-accent-hex" name="accent" '
            r'value="([^"]+)"',
            before,
        )
        assert original_accent_match is not None
        original_accent = original_accent_match.group(1)

        c.post(
            "/l2_shape/theme/preview",
            data={"accent": "#deadbe"},
        )

        after = c.get("/l2_shape/theme/").text
        assert f'value="{original_accent}"' in after, (
            "preview endpoint leaked into persistence; field-save is the "
            "only allowed write path"
        )


def test_preview_card_composes_with_cached_full_palette(
    writable_l2_yaml: Path,
) -> None:
    """Preview must use the cached primary_bg + accent_fg even when the
    operator's POST only carries the three essentials — otherwise the
    card looks wrong because those colours fall back to blank."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.post(
            "/l2_shape/theme/preview",
            data={"accent": "#777777"},
        )
    body = resp.text
    # The fixture's accent_fg comes from DEFAULT_PRESET; assert it
    # surfaces (case-insensitively because the loader normalizes but
    # render preserves operator-typed case).
    expected = DEFAULT_PRESET.accent_fg
    assert expected in body or expected.lower() in body or expected.upper() in body
