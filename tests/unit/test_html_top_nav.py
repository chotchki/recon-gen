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
        TopNavEntry("L2 Editor", "/l2", group="authoring"),
        TopNavEntry("ETL Support", "/etl", group="authoring"),
        TopNavEntry("Training", "/training", group="authoring"),
        TopNavEntry("L1 Dashboard", "/dashboards/l1", group="viewing"),
        TopNavEntry("Docs", "/docs/", group="reading"),
    ]
    nav = emit_top_nav(entries=entries)
    for label in ["L2 Editor", "ETL Support", "Training", "L1 Dashboard", "Docs"]:
        assert f">{label}<" in nav


def test_studio_disabled_excludes_studio_entries() -> None:
    """Caller-driven: when studio_enabled=False the caller omits the
    Studio entries — the helper renders only what it's given."""
    entries = [
        TopNavEntry("L1 Dashboard", "/dashboards/l1", group="viewing"),
        TopNavEntry("Docs", "/docs/", group="reading"),
    ]
    nav = emit_top_nav(entries=entries)
    assert "L2 Editor" not in nav
    assert "ETL Support" not in nav
    assert "Training" not in nav
    assert ">L1 Dashboard<" in nav
    assert ">Docs<" in nav


def test_active_href_marks_link() -> None:
    """BTa.7 — active page renders with a `border-b-2 border-accent`
    underline + a tinted accent background so the operator's eye
    lands on it pre-attentively. Pre-BTa.7 marker was the weaker
    `font-bold text-accent` (cold-read v3 flagged as too subtle)."""
    entries = [
        TopNavEntry("L2 Editor", "/l2"),
        TopNavEntry("Docs", "/docs/"),
    ]
    nav = emit_top_nav(entries=entries, active_href="/docs/")
    import re
    anchors = re.findall(r'<a [^>]*>', nav)
    docs_tag = next(a for a in anchors if '/docs/' in a)
    l2_tag = next(a for a in anchors if '/l2"' in a)
    # Active anchor carries the accent-underline marker.
    assert "border-accent" in docs_tag
    assert "border-accent" not in l2_tag
    # Active anchor's bg differs from inactive (light accent tint).
    assert "bg-accent/5" in docs_tag
    assert "bg-accent/5" not in l2_tag


def test_active_href_prefix_match_lights_parent_entry() -> None:
    """BTa.7 cold-read v3 finding: an ETL sub-page (`/etl/run`) should
    light up its parent nav entry (`ETL Support` at `/etl/`). Prefix
    match is the rule — `/etl/run` startswith `/etl/` ⇒ active."""
    entries = [
        TopNavEntry("ETL Support", "/etl/", group="authoring"),
        TopNavEntry("L2 Editor", "/", group="authoring"),
        TopNavEntry("L1 Dashboard", "/dashboards/l1", group="viewing"),
    ]
    nav = emit_top_nav(entries=entries, active_href="/etl/run")
    import re
    anchors = re.findall(r'<a [^>]*>', nav)
    etl_tag = next(a for a in anchors if 'href="/etl/"' in a)
    assert "border-accent" in etl_tag
    # Other entries stay inactive.
    other_tags = [a for a in anchors if 'href="/etl/"' not in a]
    for tag in other_tags:
        assert "border-accent" not in tag


def test_active_href_root_does_not_match_by_prefix() -> None:
    """The root nav entry `/` matches only exact `/` — otherwise it
    would shadow every sub-page (`/etl/run` startswith `/`)."""
    entries = [
        TopNavEntry("L2 Editor", "/", group="authoring"),
        TopNavEntry("ETL Support", "/etl/", group="authoring"),
    ]
    nav = emit_top_nav(entries=entries, active_href="/etl/run")
    import re
    anchors = re.findall(r'<a [^>]*>', nav)
    l2_tag = next(a for a in anchors if 'href="/"' in a)
    assert "border-accent" not in l2_tag


def test_active_href_prefix_match_requires_segment_boundary() -> None:
    """Defensive — `/etl-stuff` shouldn't match `/etl`. The prefix
    rule appends `/` before matching so segment boundaries are honored.
    """
    entries = [TopNavEntry("ETL", "/etl", group="authoring")]
    nav = emit_top_nav(entries=entries, active_href="/etl-stuff")
    import re
    anchors = re.findall(r'<a [^>]*>', nav)
    etl_tag = anchors[0]
    assert "border-accent" not in etl_tag


def test_no_active_href_marks_nothing() -> None:
    nav = emit_top_nav(
        entries=[TopNavEntry("L2 Editor", "/l2"), TopNavEntry("Docs", "/docs/")],
        active_href=None,
    )
    # No active marker means no accent-underline anchor + no tinted bg
    # (group-label chips use bg-accent/10 which is a different shade).
    import re
    anchors = re.findall(r'<a [^>]*>', nav)
    for a in anchors:
        assert "bg-accent/5" not in a
        assert "border-accent" not in a


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
    authoring entries first, then viewing, then reading."""
    entries = build_top_nav_entries(
        dashboards=[("l1", "L1 Dashboard"), ("inv", "Investigation")],
        studio_enabled=True,
        docs_url="/docs/",
    )
    labels = [e.label for e in entries]
    assert labels == [
        "L2 Editor", "ETL Support", "Training",
        "L1 Dashboard", "Investigation",
        "Docs",
    ]
    # Studio entries are authoring; dashboards viewing; Docs reading.
    by_label = {e.label: e for e in entries}
    assert by_label["L2 Editor"].group == "authoring"
    assert by_label["L1 Dashboard"].group == "viewing"
    assert by_label["Docs"].group == "reading"


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
    """Edge case: Studio-only deploy. Nav surfaces just the 3 Studio
    entries. (Caller would normally render no nav at all per BS.0
    Lock 1's `single-surface = no nav`, but the helper is honest
    about what's deployed.)"""
    entries = build_top_nav_entries(
        dashboards=[],
        studio_enabled=True,
        docs_url=None,
    )
    labels = [e.label for e in entries]
    assert labels == ["L2 Editor", "ETL Support", "Training"]


def test_dividers_between_entries() -> None:
    """BTa.7 — separators are now per-entry `<span>` spacers instead
    of the global `divide-x` utility, because cold-read v3 needed
    different weights between same-group and cross-group boundaries.
    Same-group ⇒ thin `w-px bg-surface-border` divider; different
    group ⇒ heavier `w-1 bg-accent/40` bar + a group-label chip."""
    nav = emit_top_nav(entries=[
        TopNavEntry("A", "/a", group="authoring"),
        TopNavEntry("B", "/b", group="authoring"),
        TopNavEntry("C", "/c", group="viewing"),
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
