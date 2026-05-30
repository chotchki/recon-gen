"""Phase BS.2 — `emit_top_nav` shared chrome wrapper.

The wrapper is the single source of truth for App2's flat top-nav (BS.3
will migrate callsites). Tests pin the contract:

- Empty entries returns empty string (single-surface deploy = no nav).
- One link per entry; href + label HTML-escaped.
- Studio entries hide when caller omits them (cfg-gated upstream).
- Active link gets the active class.
- `<nav>` carries the expected accessibility label + Tailwind classes.
"""
from __future__ import annotations

from recon_gen.common.html.render import TopNavEntry, emit_top_nav


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
    entries = [
        TopNavEntry("L2 Editor", "/l2"),
        TopNavEntry("Docs", "/docs/"),
    ]
    nav = emit_top_nav(entries=entries, active_href="/docs/")
    # Pull each anchor's full tag (href through close-angle) and check
    # the active marker is on the docs anchor only.
    import re
    anchors = re.findall(r'<a [^>]*>', nav)
    docs_tag = next(a for a in anchors if '/docs/' in a)
    l2_tag = next(a for a in anchors if '/l2"' in a)
    assert "font-bold text-accent" in docs_tag
    assert "font-bold text-accent" not in l2_tag


def test_no_active_href_marks_nothing() -> None:
    nav = emit_top_nav(
        entries=[TopNavEntry("L2 Editor", "/l2"), TopNavEntry("Docs", "/docs/")],
        active_href=None,
    )
    assert "font-bold text-accent" not in nav


def test_html_escapes_labels_and_hrefs() -> None:
    """Per BS.2 contract: caller's label/href values flow through
    html.escape — no XSS via dashboard titles or odd dashboard IDs."""
    nav = emit_top_nav(entries=[
        TopNavEntry('Bad "Title" <x>', '/dashboards/bad&id'),
    ])
    assert '&quot;Title&quot;' in nav or '&#x27;Title&#x27;' in nav or "&quot;" in nav
    assert "&lt;x&gt;" in nav
    assert "&amp;id" in nav


def test_divider_via_divide_x_class() -> None:
    """Per BS.0 Lock 2: visual separator between every entry. Shipped
    as the `divide-x divide-surface-border` Tailwind utility on the
    parent <nav> — equivalent to `<hr>` between every child <a>
    without polluting the DOM."""
    nav = emit_top_nav(entries=[
        TopNavEntry("A", "/a"),
        TopNavEntry("B", "/b"),
    ])
    assert "divide-x" in nav
    assert "divide-surface-border" in nav
