"""BV.7 Surface 4 — dynamic plant banner unit tests (BU.0 Lock 11.4).

The runtime banner reads ``<v>_config_kv['trainer_applied_plants']``
at request time, intersects with the L2-side ``PLANT_REGISTRY`` slice
(per BU.0 Lock 8: ``L2_TRIAGE`` / ``L2_COVERAGE`` / ``L2FT_HYGIENE``),
and renders chips for each active kind. These tests exercise the pure
renderer with synthetic ``applied_state`` maps — no DB, no Starlette
app — so the assertions are surgical to the HTML shape.

Two surfaces under test:

- ``render_plant_banner`` — the HTML snippet mounted on ``/etl/triage``.
- ``render_plant_banner_snippets`` — the CLI export reference table
  (``recon-gen docs export --surface=plant-banner-snippets``).
"""

from __future__ import annotations

from click.testing import CliRunner

from recon_gen.cli.docs import docs
from recon_gen.common.html._plant_banner import (
    _L2_CATEGORIES,
    _l2_entries,
    render_plant_banner,
    render_plant_banner_snippets,
)
from recon_gen.common.l2.plant_registry import (
    PLANT_REGISTRY,
    PlantCategory,
)


# -- Filter — Lock 8 L2-side category slice ---------------------------------


def test_l2_entries_includes_only_l2_side_categories() -> None:
    """The L2-side slice must not include L1_INVARIANT.

    Per BU.0 Lock 8 — L1 balance plants (drift, overdraft, etc.)
    live above the L2/ETL layer; they belong on ``/training/`` +
    the L1 dashboard, not ``/etl/triage``.
    """
    for entry in _l2_entries():
        assert entry.category != PlantCategory.L1_INVARIANT, (
            f"L2 banner slice leaked L1 kind {entry.kind!r} — the "
            f"category filter must exclude L1_INVARIANT per Lock 8"
        )
        assert entry.category in _L2_CATEGORIES, (
            f"kind {entry.kind!r} has category {entry.category!r} which "
            f"is neither L1_INVARIANT nor an L2-side category — "
            f"_L2_CATEGORIES needs an update"
        )


def test_l2_entries_includes_every_l2_side_kind() -> None:
    """No L2-side kind in PLANT_REGISTRY is silently dropped from the
    banner slice. Catches a future refactor that re-tags a kind into
    a new category without updating ``_L2_CATEGORIES``."""
    expected_kinds = {
        e.kind for e in PLANT_REGISTRY if e.category in _L2_CATEGORIES
    }
    actual_kinds = {e.kind for e in _l2_entries()}
    assert actual_kinds == expected_kinds


def test_l2_entries_preserves_registry_order() -> None:
    """``_l2_entries`` walks the registry in declaration order so the
    chip rendering is stable. Catches a future refactor that sorts /
    re-orders the slice."""
    registry_order = [
        e.kind for e in PLANT_REGISTRY if e.category in _L2_CATEGORIES
    ]
    slice_order = [e.kind for e in _l2_entries()]
    assert slice_order == registry_order


# -- Banner empty-state -----------------------------------------------------


def test_banner_empty_state_when_no_plants_applied() -> None:
    """Empty kv state → operator-friendly clean-baseline copy.

    The data-test attrs are the test hook surface — both branches
    carry ``data-test-plant-banner`` so a generic "did the banner
    render at all?" assertion works; the ``data-test-plant-state``
    discriminates empty vs active.
    """
    html = render_plant_banner({})
    assert "data-test-plant-banner" in html
    assert 'data-test-plant-state="empty"' in html
    assert "No active plants" in html
    assert "Trainer is at clean baseline" in html
    # Empty state must not render any chips.
    assert "data-test-plant-chip" not in html


def test_banner_empty_state_when_only_l1_kinds_applied() -> None:
    """L1_INVARIANT kinds in the kv don't render on /etl/triage.

    The /training/ banner shows them; /etl/triage filters them out.
    Synthesize an applied_state that contains ONLY an L1 kind and
    confirm the banner renders the empty state — the L1 kind is
    invisible at this surface.
    """
    l1_kind = next(
        e.kind for e in PLANT_REGISTRY
        if e.category == PlantCategory.L1_INVARIANT
    )
    applied_state = {l1_kind: {"count": "1"}}
    html = render_plant_banner(applied_state)
    assert 'data-test-plant-state="empty"' in html
    # The L1 kind name does not appear as a chip.
    assert f'data-test-plant-kind="{l1_kind}"' not in html


# -- Banner active state ----------------------------------------------------


def test_banner_active_state_renders_chip_per_l2_kind() -> None:
    """One chip per L2 kind in the kv. Each chip carries a deep-link
    href + the data-test-plant-kind attr.
    """
    # Pick the first two L2-category kinds.
    l2_kinds = [e.kind for e in _l2_entries()][:2]
    assert len(l2_kinds) == 2, (
        "registry must declare at least 2 L2-side kinds for this test"
    )
    applied_state = {kind: {"count": "1"} for kind in l2_kinds}
    html = render_plant_banner(applied_state)
    assert 'data-test-plant-state="active"' in html
    assert 'data-test-plant-count="2"' in html
    assert "Currently planted" in html
    for kind in l2_kinds:
        assert f'data-test-plant-kind="{kind}"' in html, (
            f"chip for L2 kind {kind!r} missing from active banner"
        )
        # And the deep-link slug (kebab-cased).
        slug = kind.lower().replace("_", "-")
        assert f"#{slug}" in html


def test_banner_active_state_skips_unknown_kinds() -> None:
    """A kv entry for a kind not in the registry is silently dropped.

    This is the safe degraded mode for a registry rename / removal
    that landed without a kv cleanup — surfacing "??? : unknown_kind"
    in the operator-facing banner would be worse than dropping it.
    """
    applied_state = {"this_kind_does_not_exist": {"count": "1"}}
    html = render_plant_banner(applied_state)
    assert 'data-test-plant-state="empty"' in html


def test_banner_active_state_singular_plural_grammar() -> None:
    """One kind → "1 L2 kind"; two kinds → "2 L2 kinds"."""
    l2_kinds = [e.kind for e in _l2_entries()][:2]

    html_one = render_plant_banner({l2_kinds[0]: {}})
    assert "1 L2 kind active" in html_one

    html_two = render_plant_banner({k: {} for k in l2_kinds})
    assert "2 L2 kinds active" in html_two


def test_banner_active_state_filters_to_l2_only_when_mixed() -> None:
    """KV containing BOTH L1 and L2 kinds → only the L2 kinds chip up
    on /etl/triage. Smoke for the cross-surface separation Lock 8
    promises.
    """
    l1_kind = next(
        e.kind for e in PLANT_REGISTRY
        if e.category == PlantCategory.L1_INVARIANT
    )
    l2_kind = next(
        e.kind for e in PLANT_REGISTRY
        if e.category in _L2_CATEGORIES
    )
    applied_state = {l1_kind: {"count": "1"}, l2_kind: {"count": "1"}}
    html = render_plant_banner(applied_state)
    assert 'data-test-plant-state="active"' in html
    assert 'data-test-plant-count="1"' in html
    assert f'data-test-plant-kind="{l2_kind}"' in html
    assert f'data-test-plant-kind="{l1_kind}"' not in html


def test_banner_active_state_chip_carries_section_title() -> None:
    """Each chip's ``title`` attr (hover tooltip) carries the typed
    section's display title — so the operator can hover an opaque
    machine kind like ``unmatched_rail`` and see "Unmatched rail_name
    in transaction rows" without leaving the page.
    """
    l2_kind = next(e.kind for e in _l2_entries())
    html = render_plant_banner({l2_kind: {}})
    # Title is non-empty + present as the ``title=`` attr value.
    # We don't pin the exact prose (it lives in the handbook parser);
    # just that SOMETHING beyond the bare kind appears.
    assert 'title="' in html
    assert "data-test-plant-chip" in html


def test_banner_deep_link_base_override() -> None:
    """Test-driven override of the deep-link base path. Keeps the
    runtime banner pluggable for docs-host changes without rewiring
    every call site.
    """
    l2_kind = next(e.kind for e in _l2_entries())
    html = render_plant_banner(
        {l2_kind: {}},
        deep_link_base="/docs/handbook.html",
    )
    slug = l2_kind.lower().replace("_", "-")
    assert f'href="/docs/handbook.html#{slug}"' in html


# -- CLI export — plant-banner-snippets -------------------------------------


def test_snippets_export_has_one_row_per_l2_kind() -> None:
    """The reference table has exactly one Markdown table row per
    L2-side registry kind (excluding the header + separator rows).
    """
    out = render_plant_banner_snippets()
    table_rows = [
        line for line in out.splitlines()
        if line.startswith("| `") and not line.startswith("| `Kind`")
    ]
    expected_count = sum(
        1 for e in PLANT_REGISTRY if e.category in _L2_CATEGORIES
    )
    assert len(table_rows) == expected_count


def test_snippets_export_includes_kind_and_slug_columns() -> None:
    """Every L2 kind must appear in the table with its kebab slug."""
    out = render_plant_banner_snippets()
    for entry in _l2_entries():
        slug = entry.kind.lower().replace("_", "-")
        assert f"`{entry.kind}`" in out
        assert f"`{slug}`" in out


def test_snippets_export_excludes_l1_invariant_kinds() -> None:
    """L1_INVARIANT kinds must NOT appear in the snippets table.

    The CLI export mirrors the runtime filter so docs readers see
    exactly the kinds the runtime banner could render.
    """
    out = render_plant_banner_snippets()
    l1_kinds = [
        e.kind for e in PLANT_REGISTRY
        if e.category == PlantCategory.L1_INVARIANT
    ]
    for kind in l1_kinds:
        assert f"| `{kind}` |" not in out, (
            f"L1 kind {kind!r} leaked into the L2 plant-banner snippets"
        )


def test_snippets_cli_stdout() -> None:
    """``recon-gen docs export --surface=plant-banner-snippets`` (no
    --output) prints the markdown to stdout."""
    runner = CliRunner()
    result = runner.invoke(
        docs, ["export", "--surface=plant-banner-snippets"],
    )
    assert result.exit_code == 0, result.output
    assert "# Plant banner — per-kind reference" in result.output
    assert "| Kind | Title | Slug | Category |" in result.output


def test_snippets_cli_rejects_l2_flag() -> None:
    """The export is L2-independent — passing --l2 is a usage error
    (matches the matrix / trainer-cards / violations surfaces).
    """
    runner = CliRunner()
    result = runner.invoke(
        docs,
        ["export", "--surface=plant-banner-snippets",
         "--l2", "tests/l2/spec_example.yaml"],
    )
    assert result.exit_code != 0
    assert "--l2 has no effect" in result.output
