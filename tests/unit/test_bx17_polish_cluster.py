"""BX.17 (2026-06-11) — Polish cluster.

Three independent polish items bundled per the PLAN.md BX.17 entry:

  a. Duration picker (operator-locked quick-pick chips + free-text
     fallback) on FieldKind="duration" — Rail.max_pending_age +
     Rail.max_unbundled_age opt in.
  b. Reference panels (the ⓘ Reference details block) default-open
     only on empty list / edit pages. ZERO persistence.
  c. Completion-expression DSL autocomplete — static v1 vocabulary
     literals + dynamic metadata.<key> entries derived from the L2.

Browser drivers locate via ``data-role`` — never Tailwind class — per
[feedback_browser_drivers_user_facing_locators].
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).parent.parent / "l2"


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    """Copy spec_example.yaml so the cache load doesn't mutate the
    bundled fixture (mirrors the BX.8 / BX.16 test fixture pattern)."""
    src = FIXTURES_DIR / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


# ---------------------------------------------------------------------------
# (a) Duration picker
# ---------------------------------------------------------------------------


def test_duration_picker_renders_four_quick_picks() -> None:
    """The operator-locked chip set is Instant / 1h / EOD / Next-day —
    four picks, no more, no fewer. Each chip surfaces both the visible
    label and an ISO 8601 ``data-duration-pick`` value the JS hook
    reads on click."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        FieldSpec,
        _DURATION_QUICK_PICKS,
        _render_duration_picker_field,
    )

    # Closed tuple — exact length asserts no chip drift.
    assert len(_DURATION_QUICK_PICKS) == 4
    labels = [label for label, _ in _DURATION_QUICK_PICKS]
    assert labels == ["Instant", "1h", "EOD", "Next-day"]

    spec = FieldSpec(
        name="max_pending_age",
        label="Max pending age",
        helper="",
        kind="duration",
        placeholder="PT24H",
    )
    html = _render_duration_picker_field(spec, "")
    for label, iso_value in _DURATION_QUICK_PICKS:
        assert f">{label}</button>" in html
        assert f'data-duration-pick="{iso_value}"' in html


def test_duration_picker_uses_data_role_anchors() -> None:
    """Per [feedback_browser_drivers_user_facing_locators], the picker
    must expose ``data-role`` anchors — not Tailwind classes — so
    browser drivers can locate the fieldset, the free-text input, and
    the chip strip without coupling to styling."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        FieldSpec,
        _render_duration_picker_field,
    )

    spec = FieldSpec(
        name="max_pending_age", label="Max pending age",
        helper="", kind="duration",
    )
    html = _render_duration_picker_field(spec, "")
    assert 'data-role="duration-picker"' in html
    assert 'data-role="duration-free-text"' in html
    assert 'data-role="duration-quick-picks"' in html


def test_duration_picker_flags_active_chip_when_value_matches() -> None:
    """When the field's current value matches a quick-pick value, the
    corresponding chip carries ``data-active="true"`` on render — the
    sighted operator sees the active state without a JS round-trip + the
    test layer asserts the server-side decision."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        FieldSpec,
        _render_duration_picker_field,
    )

    spec = FieldSpec(
        name="max_pending_age", label="Max pending age",
        helper="", kind="duration",
    )
    html_eod = _render_duration_picker_field(spec, "PT24H")
    # The "EOD" chip's data-active flag matches the current value.
    assert (
        'data-duration-pick="PT24H" data-target="field-max_pending_age" '
        'data-active="true"' in html_eod
    )
    # Other chips do NOT carry data-active.
    assert (
        'data-duration-pick="PT1H" data-target="field-max_pending_age" '
        'aria-label' in html_eod
    )


def test_duration_picker_free_text_preserves_name_contract() -> None:
    """The free-text input keeps ``name=<spec.name>`` so the existing
    ``_load_duration`` coerce path keeps working without the form-data
    shape changing — chips don't submit a second field, they're a UI
    affordance on top of the one shared form key."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        FieldSpec,
        _render_duration_picker_field,
    )

    spec = FieldSpec(
        name="max_unbundled_age", label="Max unbundled age",
        helper="", kind="duration", placeholder="P3D",
    )
    html = _render_duration_picker_field(spec, "P1D")
    # name= contract preserved.
    assert 'name="max_unbundled_age"' in html
    # The current value sits in the input.
    assert 'value="P1D"' in html
    # The placeholder rides through.
    assert 'placeholder="P3D"' in html


def test_rail_duration_field_specs_use_kind_duration() -> None:
    """Rail.max_pending_age + Rail.max_unbundled_age opted into the new
    kind so the picker shows up on the rail edit / create pages."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _FIELD_SPECS_BY_KIND,
    )

    rail_specs_by_name = {
        s.name: s for s in _FIELD_SPECS_BY_KIND["rail"]
    }
    assert rail_specs_by_name["max_pending_age"].kind == "duration"
    assert rail_specs_by_name["max_unbundled_age"].kind == "duration"


# ---------------------------------------------------------------------------
# (b) Reference panels default-open
# ---------------------------------------------------------------------------


def test_reference_panel_default_closed_when_open_by_default_false() -> None:
    """Pre-BX.17.b behavior preserved when the caller omits the kwarg.
    The closed details block carries data-role="reference-panel" for
    browser-driver anchoring."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_intro_details,
    )

    html = _render_intro_details("<p>intro prose</p>")
    assert 'data-role="reference-panel"' in html
    # The `open` attribute is absent (default-closed shape).
    assert "<details" in html
    assert "<details " in html and " open>" not in html.replace(
        '"reference-panel"', "",
    )


def test_reference_panel_default_open_when_flag_true() -> None:
    """``open_by_default=True`` renders the details block with the
    ``open`` attribute so the prose is visible on first paint."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_intro_details,
    )

    html = _render_intro_details(
        "<p>intro prose</p>", open_by_default=True,
    )
    assert 'data-role="reference-panel"' in html
    assert "<details" in html
    # The `open` attribute is present.
    assert "open>" in html


def test_kind_is_empty_true_for_empty_collection() -> None:
    """``_kind_is_empty`` returns True when the L2 has zero entities of
    the kind — the trigger for the create page's default-open
    reference panel."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _kind_is_empty,
    )

    class _StubInstance:
        rails: tuple[object, ...] = ()
        accounts: tuple[object, ...] = ()
        transfer_templates: tuple[object, ...] = ()
        chains: tuple[object, ...] = ()
        account_templates: tuple[object, ...] = ()
        limit_schedules: tuple[object, ...] = ()

    stub = _StubInstance()
    assert _kind_is_empty(stub, "rail") is True
    assert _kind_is_empty(stub, "account") is True
    assert _kind_is_empty(stub, "limit_schedule") is True


def test_kind_is_empty_false_for_populated_collection() -> None:
    """``_kind_is_empty`` returns False once the collection has at
    least one entity — the create page's reference panel collapses
    back to its default-closed shape."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _kind_is_empty,
    )

    class _StubInstance:
        rails: tuple[str, ...] = ("placeholder",)
        accounts: tuple[object, ...] = ()

    stub = _StubInstance()
    assert _kind_is_empty(stub, "rail") is False
    assert _kind_is_empty(stub, "account") is True


def test_singleton_has_no_value_theme() -> None:
    """Theme singleton — ``True`` when ``instance.theme is None``."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _singleton_has_no_value,
    )

    class _NoTheme:
        theme = None

    class _WithTheme:
        theme = object()

    assert _singleton_has_no_value(_NoTheme(), "theme") is True
    assert _singleton_has_no_value(_WithTheme(), "theme") is False


def test_singleton_has_no_value_instance() -> None:
    """Instance singleton — True when none of description /
    institution_name / institution_acronym are populated. Mirrors the
    home page's is_set rule in ``_studio_routes.py``."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _singleton_has_no_value,
    )

    class _Empty:
        description = None
        institution_name = None
        institution_acronym = None

    class _Populated:
        description = "stuff"
        institution_name = None
        institution_acronym = None

    assert _singleton_has_no_value(_Empty(), "instance") is True
    assert _singleton_has_no_value(_Populated(), "instance") is False


# ---------------------------------------------------------------------------
# (c) Completion DSL autocomplete
# ---------------------------------------------------------------------------


def test_completion_datalist_carries_vocab_literals() -> None:
    """The static v1 vocabulary literals always appear in the datalist
    so the operator sees the canonical shapes (``business_day_end`` +
    the +Nd variants + ``month_end``) regardless of the L2's current
    transfer_key state."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _COMPLETION_VOCAB_LITERALS,
        _render_completion_datalist,
    )

    class _EmptyInstance:
        transfer_templates: tuple[object, ...] = ()
        rails: tuple[object, ...] = ()

    html = _render_completion_datalist(_EmptyInstance(), "field-completion-autocomplete")
    for lit in _COMPLETION_VOCAB_LITERALS:
        assert f'<option value="{lit}"></option>' in html
    # The datalist carries the data-role anchor for browser drivers.
    assert 'data-role="dsl-autocomplete-list"' in html


def test_completion_datalist_surfaces_metadata_keys_from_l2(
    writable_l2_yaml: Path,
) -> None:
    """Dynamic suggestion source: every transfer_key entry on every TT
    + every metadata_keys entry on every Rail becomes a
    ``metadata.<key>`` autocomplete option so the operator authoring a
    new TT.completion sees keys that already exist in the L2."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_completion_datalist,
    )
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415

    inst = L2InstanceCache.from_path(writable_l2_yaml).get()
    html = _render_completion_datalist(inst, "field-completion-autocomplete")
    # spec_example carries an `external_reference` metadata_keys entry +
    # several other rail-side keys. At least one expected key shows up
    # as a `metadata.<key>` suggestion.
    assert 'value="metadata.external_reference"' in html


def test_completion_autocomplete_metadata_keys_derive_from_both_surfaces() -> None:
    """Suggestion source = TT.transfer_key UNION Rail.metadata_keys.
    A key declared only on a transfer_template (not yet wired to a
    rail) still shows up — and vice versa."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _completion_metadata_suggestions,
    )

    class _Rail:
        metadata_keys: tuple[str, ...] = ("rail_only_key",)

    class _Template:
        transfer_key: tuple[str, ...] = ("template_only_key",)

    class _Inst:
        rails: tuple[_Rail, ...] = (_Rail(),)
        transfer_templates: tuple[_Template, ...] = (_Template(),)

    keys = _completion_metadata_suggestions(_Inst())
    assert "rail_only_key" in keys
    assert "template_only_key" in keys


def test_completion_field_renders_datalist_and_htmx_refresh_attrs() -> None:
    """The TransferTemplate.completion field's <input> carries:
      - ``list="field-completion-autocomplete"`` so the browser uses the
        datalist for typeahead.
      - ``hx-get`` / ``hx-trigger="input changed delay:200ms"`` so
        the suggestion set refreshes as the operator types.
      - ``data-role="dsl-autocomplete-input"`` so browser drivers can
        anchor on the autocomplete-enabled field.
    """
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        FieldSpec,
        _render_field,
    )

    class _StubInstance:
        transfer_templates: tuple[object, ...] = ()
        rails: tuple[object, ...] = ()

    spec = FieldSpec(
        name="completion",
        label="Completion expression",
        helper="",
        kind="text",
        required=True,
    )
    html = _render_field(spec, "business_day_end", _StubInstance())
    assert 'list="field-completion-autocomplete"' in html
    assert 'data-role="dsl-autocomplete-input"' in html
    assert "hx-get=" in html
    assert "/completion-autocomplete" in html
    assert 'hx-trigger="input changed delay:200ms"' in html
    # The static literal options appear inside the rendered datalist.
    assert 'value="business_day_end"' in html


def test_completion_vocabulary_literals_match_validator_regex() -> None:
    """Every literal in ``_COMPLETION_VOCAB_LITERALS`` MUST be accepted
    by the L2 validator's ``_completion_is_valid`` — drift between the
    autocomplete suggestions and the validator surface would be a
    footgun (operator picks a suggestion, save fails). Pin both ends."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _COMPLETION_VOCAB_LITERALS,
    )
    from recon_gen.common.l2.validate import (  # noqa: PLC0415
        _completion_is_valid,
    )

    for lit in _COMPLETION_VOCAB_LITERALS:
        assert _completion_is_valid(lit), (
            f"Suggestion {lit!r} would fail validator at save"
        )
