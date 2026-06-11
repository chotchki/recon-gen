"""Phase BS.2 / BS.3 — `emit_top_nav` shared chrome wrapper +
`build_top_nav_entries` deployed-state assembly.

The wrapper is the single source of truth for App2's flat top-nav (BS.3
will migrate callsites). Tests pin the contract:

- Empty entries returns empty string (single-surface deploy = no nav).
- One link per entry; href + label HTML-escaped.
- Studio entries hide when caller omits them (cfg-gated upstream).
- Active link gets the active class.
- `<nav>` carries the expected accessibility label + Tailwind classes.
"""
from __future__ import annotations

from recon_gen.common.html.render import (
    TopNavEntry, build_top_nav_entries, emit_top_nav,
)


def test_empty_entries_returns_empty_string() -> None:
    """Per BS.0 Lock 1: when only one sub-app is deployed the nav
    isn't useful — caller filters entries down + the helper returns
    no markup."""
    assert emit_top_nav(entries=[]) == ""


def test_single_entry_renders_one_link() -> None:
    nav = emit_top_nav(entries=[TopNavEntry("Docs", "/docs/")])
    assert nav.count("<a ") == 1
    assert ">Docs<" in nav
    assert 'href="/docs/"' in nav
    assert '<nav class=' in nav
    assert 'aria-label="App nav"' in nav


def test_studio_enabled_includes_studio_entries() -> None:
    """Caller-driven: when studio_enabled=True the caller builds the
    list with the 3 Studio entries up front."""
    entries = [
        TopNavEntry("L2 Editor", "/l2", group="build"),
        TopNavEntry("ETL Support", "/etl", group="build"),
        TopNavEntry("Training", "/training", group="build"),
        TopNavEntry("L1 Dashboard", "/dashboards/l1", group="view"),
        TopNavEntry("Docs", "/docs/", group="ref"),
    ]
    nav = emit_top_nav(entries=entries)
    for label in ["L2 Editor", "ETL Support", "Training", "L1 Dashboard", "Docs"]:
        assert f">{label}<" in nav


def test_studio_disabled_excludes_studio_entries() -> None:
    """Caller-driven: when studio_enabled=False the caller omits the
    Studio entries — the helper renders only what it's given."""
    entries = [
        TopNavEntry("L1 Dashboard", "/dashboards/l1", group="view"),
        TopNavEntry("Docs", "/docs/", group="ref"),
    ]
    nav = emit_top_nav(entries=entries)
    assert "L2 Editor" not in nav
    assert "ETL Support" not in nav
    assert "Training" not in nav
    assert ">L1 Dashboard<" in nav
    assert ">Docs<" in nav


def test_active_href_marks_link() -> None:
    """BTa.7 — active page renders with a heavier underline + a tinted
    background so the operator's eye lands on it pre-attentively.

    BX.7 (2026-06-11): inactive entries now carry a 2-px group-tinted
    underline (`border-b-2`); active entries upgrade to 4-px
    (`border-b-4`). Hue is the group's theme token (accent / success /
    secondary-fg). Both anchors below default to the ``view`` group,
    so the active hue is ``success``."""
    entries = [
        TopNavEntry("L2 Editor", "/l2"),
        TopNavEntry("Docs", "/docs/"),
    ]
    nav = emit_top_nav(entries=entries, active_href="/docs/")
    import re
    anchors = re.findall(r'<a [^>]*>', nav)
    docs_tag = next(a for a in anchors if '/docs/' in a)
    l2_tag = next(a for a in anchors if '/l2"' in a)
    # Active anchor upgrades to 4-px underline; inactive stays at 2-px.
    assert "border-b-4" in docs_tag
    assert "border-b-4" not in l2_tag
    assert "border-b-2" in l2_tag
    # Both carry the view-group success-token hue (same group).
    assert "border-success" in docs_tag
    assert "border-success" in l2_tag
    # Active anchor's bg differs from inactive (light success tint).
    assert "bg-success/5" in docs_tag
    assert "bg-success/5" not in l2_tag


def test_active_href_prefix_match_lights_parent_entry() -> None:
    """BTa.7 cold-read v3 finding: an ETL sub-page (`/etl/run`) should
    light up its parent nav entry (`ETL Support` at `/etl/`). Prefix
    match is the rule — `/etl/run` startswith `/etl/` ⇒ active.

    BX.7 — active page upgrades from `border-b-2` to `border-b-4` in
    the group hue; the heavier border IS the active marker (the 2-px
    underline is now a group-membership signal on every entry)."""
    entries = [
        TopNavEntry("ETL Support", "/etl/", group="build"),
        TopNavEntry("L2 Editor", "/", group="build"),
        TopNavEntry("L1 Dashboard", "/dashboards/l1", group="view"),
    ]
    nav = emit_top_nav(entries=entries, active_href="/etl/run")
    import re
    anchors = re.findall(r'<a [^>]*>', nav)
    etl_tag = next(a for a in anchors if 'href="/etl/"' in a)
    assert "border-b-4" in etl_tag
    # Other entries stay inactive (2-px group underline only).
    other_tags = [a for a in anchors if 'href="/etl/"' not in a]
    for tag in other_tags:
        assert "border-b-4" not in tag


def test_active_href_root_does_not_match_by_prefix() -> None:
    """The root nav entry `/` matches only exact `/` — otherwise it
    would shadow every sub-page (`/etl/run` startswith `/`)."""
    entries = [
        TopNavEntry("L2 Editor", "/", group="build"),
        TopNavEntry("ETL Support", "/etl/", group="build"),
    ]
    nav = emit_top_nav(entries=entries, active_href="/etl/run")
    import re
    anchors = re.findall(r'<a [^>]*>', nav)
    l2_tag = next(a for a in anchors if 'href="/"' in a)
    # BX.7 — active marker is the heavier 4-px underline (border-b-4),
    # not the presence of `border-accent` (every build entry has that).
    assert "border-b-4" not in l2_tag


def test_active_href_prefix_match_requires_segment_boundary() -> None:
    """Defensive — `/etl-stuff` shouldn't match `/etl`. The prefix
    rule appends `/` before matching so segment boundaries are honored.
    """
    entries = [TopNavEntry("ETL", "/etl", group="build")]
    nav = emit_top_nav(entries=entries, active_href="/etl-stuff")
    import re
    anchors = re.findall(r'<a [^>]*>', nav)
    etl_tag = anchors[0]
    # Same as the root-prefix test: active = `border-b-4`; inactive
    # entries still carry the 2-px group underline.
    assert "border-b-4" not in etl_tag


def test_no_active_href_marks_nothing() -> None:
    """BX.7 — no active means no anchor carries the heavier 4-px
    underline (`border-b-4`) or the 5% group-hue tint background.
    Every anchor still has its 2-px group underline (group-membership
    signal), and the chips still tint per group; only the
    active-page-promotion markers are absent."""
    nav = emit_top_nav(
        entries=[TopNavEntry("L2 Editor", "/l2"), TopNavEntry("Docs", "/docs/")],
        active_href=None,
    )
    import re
    anchors = re.findall(r'<a [^>]*>', nav)
    for a in anchors:
        assert "border-b-4" not in a
        # Default group is "view" (success). No 5% tint bg on any link.
        assert "bg-success/5" not in a
        assert "bg-accent/5" not in a
        assert "bg-secondary-fg/5" not in a


def test_html_escapes_labels_and_hrefs() -> None:
    """Per BS.2 contract: caller's label/href values flow through
    html.escape — no XSS via dashboard titles or odd dashboard IDs."""
    nav = emit_top_nav(entries=[
        TopNavEntry('Bad "Title" <x>', '/dashboards/bad&id'),
    ])
    assert '&quot;Title&quot;' in nav or '&#x27;Title&#x27;' in nav or "&quot;" in nav
    assert "&lt;x&gt;" in nav
    assert "&amp;id" in nav


# ---------------------------------------------------------------------------
# build_top_nav_entries — assembly from deployed-state
# ---------------------------------------------------------------------------


def test_build_entries_studio_enabled_with_dashboards_and_docs() -> None:
    """Full deploy: Studio + dashboards + docs. Order per BS.0 Lock 2:
    authoring entries first, then viewing, then reading. CF.3.l added
    Diagram as the first authoring entry."""
    entries = build_top_nav_entries(
        dashboards=[("l1", "L1 Dashboard"), ("inv", "Investigation")],
        studio_enabled=True,
        docs_url="/docs/",
    )
    labels = [e.label for e in entries]
    assert labels == [
        "Diagram", "L2 Editor", "ETL Support", "Training",
        "L1 Dashboard", "Investigation",
        "Docs",
    ]
    # Studio entries are build; dashboards view; Docs ref. BX.7 (2026-06-11)
    # renamed tokens from authoring/viewing/reading to build/view/ref so
    # internal vocabulary matches the operator-facing BUILD/VIEW/REFERENCE chip.
    by_label = {e.label: e for e in entries}
    assert by_label["Diagram"].group == "build"
    assert by_label["L2 Editor"].group == "build"
    assert by_label["L1 Dashboard"].group == "view"
    assert by_label["Docs"].group == "ref"


def test_build_entries_studio_disabled() -> None:
    entries = build_top_nav_entries(
        dashboards=[("l1", "L1 Dashboard")],
        studio_enabled=False,
        docs_url="/docs/",
    )
    labels = [e.label for e in entries]
    assert labels == ["L1 Dashboard", "Docs"]


def test_build_entries_no_docs_no_studio() -> None:
    """Dashboards-only deploy: nav has just the dashboard entries."""
    entries = build_top_nav_entries(
        dashboards=[("l1", "L1"), ("l2", "L2")],
        studio_enabled=False,
        docs_url=None,
    )
    labels = [e.label for e in entries]
    assert labels == ["L1", "L2"]


def test_build_entries_studio_only_no_dashboards_no_docs() -> None:
    """Edge case: Studio-only deploy. Nav surfaces the 4 Studio
    entries (CF.3.l added Diagram to the 3-entry baseline). Caller
    would normally render no nav at all per BS.0 Lock 1's
    `single-surface = no nav`, but the helper is honest about what's
    deployed."""
    entries = build_top_nav_entries(
        dashboards=[],
        studio_enabled=True,
        docs_url=None,
    )
    labels = [e.label for e in entries]
    assert labels == ["Diagram", "L2 Editor", "ETL Support", "Training"]


def test_dividers_between_entries() -> None:
    """BTa.7 — separators are now per-entry `<span>` spacers instead
    of the global `divide-x` utility, because cold-read v3 needed
    different weights between same-group and cross-group boundaries.
    Same-group ⇒ thin `w-px bg-surface-border` divider; different
    group ⇒ heavier `w-1 bg-accent/40` bar + a group-label chip."""
    nav = emit_top_nav(entries=[
        TopNavEntry("A", "/a", group="build"),
        TopNavEntry("B", "/b", group="build"),
        TopNavEntry("C", "/c", group="view"),
    ])
    # Thin separator inside the same group (A → B).
    assert 'w-px bg-surface-border' in nav
    # Heavy separator + label between groups (B → C).
    assert 'bg-accent/40' in nav


def test_nav_renders_recon_gen_brand_title_first() -> None:
    """BS.3 follow-up (2026-05-30): "Recon-Gen" brand title sits left
    of the first nav entry, separated from it by the same divide-x
    border that separates entries. Reads as "Brand | nav links"."""
    nav = emit_top_nav(entries=[
        TopNavEntry("A", "/a"),
        TopNavEntry("B", "/b"),
    ])
    # Brand title appears once + before any <a>.
    assert nav.count(">Recon-Gen<") == 1
    brand_idx = nav.index(">Recon-Gen<")
    first_link_idx = nav.index('<a href="/a"')
    assert brand_idx < first_link_idx
    # Not a link — the brand is a <span>, not an <a> (no destination).
    assert '<a href' not in nav[:brand_idx + len(">Recon-Gen<")]


# ---------------------------------------------------------------------------
# BX.7 (2026-06-11) — group color-coding + token rename + chip labels.
# ---------------------------------------------------------------------------


def test_bx7_group_label_chip_text_is_build_view_reference() -> None:
    """BX.7 — group-label chip text matches the operator-locked
    BUILD / VIEW / REFERENCE labels (was Studio / Dashboards /
    Reference pre-BX.7). The chip text is the redundant non-color cue
    that lets the grouping survive deuteranopia (≈5% of male users)
    per WCAG AA on color-only signals."""
    nav = emit_top_nav(entries=[
        TopNavEntry("L2 Editor", "/", group="build"),
        TopNavEntry("L1 Dashboard", "/dashboards/l1", group="view"),
        TopNavEntry("Docs", "/docs/", group="ref"),
    ])
    assert ">BUILD<" in nav
    assert ">VIEW<" in nav
    assert ">REFERENCE<" in nav
    # Pre-BX.7 chip text gone — the rename is total, not additive.
    assert ">Studio<" not in nav
    assert ">Dashboards<" not in nav
    # "Reference" alone (mixed case) wouldn't appear; the all-caps
    # `>REFERENCE<` is the chip, not a partial-match false positive.


def test_bx7_data_nav_group_attribute_per_anchor() -> None:
    """BX.7 — every anchor carries a `data-nav-group="..."` attribute
    so browser drivers + cold-read screenshots can anchor on stable
    semantic markup, not Tailwind utility classes
    (`feedback_browser_drivers_user_facing_locators`)."""
    nav = emit_top_nav(entries=[
        TopNavEntry("L2 Editor", "/", group="build"),
        TopNavEntry("L1 Dashboard", "/dashboards/l1", group="view"),
        TopNavEntry("Docs", "/docs/", group="ref"),
    ])
    import re
    anchors = re.findall(r'<a [^>]*>', nav)
    by_href = {
        re.search(r'href="([^"]+)"', a).group(1): a  # type: ignore[union-attr]: every anchor in this test fixture carries an href attribute by construction so the re.search Match is non-None
        for a in anchors
    }
    assert 'data-nav-group="build"' in by_href["/"]
    assert 'data-nav-group="view"' in by_href["/dashboards/l1"]
    assert 'data-nav-group="ref"' in by_href["/docs/"]


def test_bx7_data_nav_group_label_attribute_on_chips() -> None:
    """BX.7 — group-label chip spans carry their own
    `data-nav-group-label="..."` attribute (parallels the per-anchor
    `data-nav-group="..."`). Lets a driver query the chip without
    string-matching its display text."""
    nav = emit_top_nav(entries=[
        TopNavEntry("L2 Editor", "/", group="build"),
        TopNavEntry("L1 Dashboard", "/dashboards/l1", group="view"),
        TopNavEntry("Docs", "/docs/", group="ref"),
    ])
    assert 'data-nav-group-label="build"' in nav
    assert 'data-nav-group-label="view"' in nav
    assert 'data-nav-group-label="ref"' in nav


def test_bx7_underline_hue_per_group() -> None:
    """BX.7 — each entry's 2-px underline is in its group's theme hue:
    build → accent (default blue), view → success (green), ref →
    secondary-fg (neutral grey). Hue derives from the active theme's
    tokens so L2 overrides re-tint without per-brand engineering."""
    nav = emit_top_nav(entries=[
        TopNavEntry("L2 Editor", "/", group="build"),
        TopNavEntry("L1 Dashboard", "/dashboards/l1", group="view"),
        TopNavEntry("Docs", "/docs/", group="ref"),
    ])
    import re
    anchors = re.findall(r'<a [^>]*>', nav)
    by_href = {
        re.search(r'href="([^"]+)"', a).group(1): a  # type: ignore[union-attr]: every anchor in this test fixture carries an href attribute by construction so the re.search Match is non-None
        for a in anchors
    }
    # build → border-accent (inactive: 2-px).
    assert "border-b-2" in by_href["/"]
    assert "border-accent" in by_href["/"]
    # view → border-success.
    assert "border-b-2" in by_href["/dashboards/l1"]
    assert "border-success" in by_href["/dashboards/l1"]
    # ref → border-secondary-fg.
    assert "border-b-2" in by_href["/docs/"]
    assert "border-secondary-fg" in by_href["/docs/"]


def test_bx7_chip_class_carries_group_hue() -> None:
    """BX.7 — the group-label chip's text+bg classes are in the
    group's hue too (text-accent + bg-accent/10 for BUILD, etc.).
    Without this the chip would be a constant accent color across all
    three groups, which was the BTa.7 gap that BX.7 closes."""
    nav = emit_top_nav(entries=[
        TopNavEntry("L2 Editor", "/", group="build"),
        TopNavEntry("L1 Dashboard", "/dashboards/l1", group="view"),
        TopNavEntry("Docs", "/docs/", group="ref"),
    ])
    # BUILD chip — accent.
    assert (
        'data-nav-group-label="build"' in nav
        and "text-accent" in nav
        and "bg-accent/10" in nav
    )
    # VIEW chip — success.
    import re
    view_chip = re.search(
        r'<span class="([^"]*)" data-nav-group-label="view">',
        nav,
    )
    assert view_chip is not None
    assert "text-success" in view_chip.group(1)
    assert "bg-success/10" in view_chip.group(1)
    # REFERENCE chip — secondary-fg.
    ref_chip = re.search(
        r'<span class="([^"]*)" data-nav-group-label="ref">',
        nav,
    )
    assert ref_chip is not None
    assert "text-secondary-fg" in ref_chip.group(1)
    assert "bg-secondary-fg/10" in ref_chip.group(1)


def test_bx7_active_page_upgrades_to_heavier_underline_in_group_hue() -> None:
    """BX.7 — active page wins a heavier 4-px underline in its group's
    hue (build → accent, view → success, ref → secondary-fg). Active
    also gets a 5% tinted bg in the same hue."""
    entries = [
        TopNavEntry("L2 Editor", "/", group="build"),
        TopNavEntry("L1 Dashboard", "/dashboards/l1", group="view"),
        TopNavEntry("Docs", "/docs/", group="ref"),
    ]

    def _active_anchor(active_href: str) -> str:
        nav = emit_top_nav(entries=entries, active_href=active_href)
        import re
        return next(
            a for a in re.findall(r'<a [^>]*>', nav)
            if f'href="{active_href}"' in a
        )

    build_active = _active_anchor("/")
    assert "border-b-4" in build_active
    assert "border-accent" in build_active
    assert "bg-accent/5" in build_active

    view_active = _active_anchor("/dashboards/l1")
    assert "border-b-4" in view_active
    assert "border-success" in view_active
    assert "bg-success/5" in view_active

    ref_active = _active_anchor("/docs/")
    assert "border-b-4" in ref_active
    assert "border-secondary-fg" in ref_active
    assert "bg-secondary-fg/5" in ref_active


def test_bx7_build_top_nav_entries_uses_new_token_values() -> None:
    """BX.7 — the assembler emits `build`/`view`/`ref` tokens, NOT
    the legacy `authoring`/`viewing`/`reading`. Burned in here so a
    future rename can't quietly land without flipping this test."""
    entries = build_top_nav_entries(
        dashboards=[("l1", "L1")],
        studio_enabled=True,
        docs_url="/docs/",
    )
    groups = {e.group for e in entries}
    assert groups == {"build", "view", "ref"}
    # Legacy tokens must not leak through.
    assert "authoring" not in groups
    assert "viewing" not in groups
    assert "reading" not in groups
