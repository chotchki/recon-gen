"""AA.C.4 — L2FT Hygiene Exceptions parser unit tests.

Two layers (mirrors :mod:`tests.unit.test_handbook_invariants`):

- ``test_parse_l2ft_exceptions_*`` — synthetic markdown snippets
  exercise every parser branch (heading shape, columns extraction,
  what_to_do extraction, multi-section walk, title-to-kind slug).
- ``test_bundled_l2ft_exceptions_*`` — pin the parse against the
  real bundled ``L2FT_Exceptions.md`` so a future doc edit doesn't
  silently break the L2FT dashboard panel feed.
"""

from __future__ import annotations

from quicksight_gen.common.handbook.l2ft_exceptions import (
    L2FT_EXCEPTION_KIND_TO_SHEET,
    L2FTExceptionSection,
    load_bundled_l2ft_exceptions,
    panel_markdown,
    parse_l2ft_exceptions,
)


# -- Synthetic-markdown parser tests ----------------------------------------


def test_parse_l2ft_exceptions_single_section() -> None:
    md = """\
## The six L2FT hygiene checks

### 1. Chain Orphans

Each row is a declared Required chain edge where the parent fired but
no child followed.

**Columns:** `parent_name`, `child_name`, `orphan_count`.

**What to do:** Fix the ETL or retire the chain edge.
"""
    sections = parse_l2ft_exceptions(md)
    assert set(sections.keys()) == {"chain_orphans"}
    section = sections["chain_orphans"]
    assert isinstance(section, L2FTExceptionSection)
    assert section.kind == "chain_orphans"
    assert section.title == "Chain Orphans"
    assert "Required chain edge" in section.body
    assert section.columns == ("parent_name", "child_name", "orphan_count")
    assert section.what_to_do == "Fix the ETL or retire the chain edge."
    # what_to_do is lifted out of body so the panel can style it
    # separately (mirrors the L1 invariants parser contract).
    assert "**What to do:**" not in section.body


def test_parse_l2ft_exceptions_multi_section_walk() -> None:
    md = """\
### 1. Chain Orphans

First section body.

**What to do:** Fix it.

### 2. Unmatched Rail Name

Second section body.

**What to do:** Add to YAML or fix ETL.
"""
    sections = parse_l2ft_exceptions(md)
    assert list(sections.keys()) == ["chain_orphans", "unmatched_rail_name"]
    assert sections["chain_orphans"].what_to_do == "Fix it."
    assert sections["unmatched_rail_name"].what_to_do == "Add to YAML or fix ETL."


def test_parse_l2ft_exceptions_title_to_kind_slug() -> None:
    """Title -> kind slug: lower-case, spaces -> underscores, special
    chars dropped. Test the round-trip for every L2FT check title."""
    md_template = "### {n}. {title}\n\nBody.\n"
    titles = [
        "Chain Orphans",
        "Unmatched Rail Name",
        "Dead Rails",
        "Dead Bundles Activity",
        "Dead Metadata Declarations",
        "Dead Limit Schedules",
    ]
    md = "\n".join(
        md_template.format(n=i + 1, title=t) for i, t in enumerate(titles)
    )
    sections = parse_l2ft_exceptions(md)
    expected_kinds = [
        "chain_orphans",
        "unmatched_rail_name",
        "dead_rails",
        "dead_bundles_activity",
        "dead_metadata_declarations",
        "dead_limit_schedules",
    ]
    assert list(sections.keys()) == expected_kinds


def test_parse_l2ft_exceptions_no_columns_line() -> None:
    """Sections without a ``**Columns:**`` line get an empty tuple
    (matches L1 invariants parser contract)."""
    md = """\
### 1. Dead Rails

Body without columns.

**What to do:** Decide.
"""
    sections = parse_l2ft_exceptions(md)
    assert sections["dead_rails"].columns == ()


def test_parse_l2ft_exceptions_no_what_to_do_line() -> None:
    """Sections without a ``**What to do:**`` line get an empty
    string. Soft contract — the panel_markdown fallback handles it."""
    md = """\
### 1. Dead Rails

Body without remediation.

**Columns:** `rail_name`.
"""
    sections = parse_l2ft_exceptions(md)
    assert sections["dead_rails"].what_to_do == ""


def test_parse_l2ft_exceptions_multi_line_what_to_do_collapses() -> None:
    """The doc wraps the ``**What to do:**`` line across multiple
    physical lines for readability; the parser collapses internal
    newlines to single spaces so the dashboard panel reads as one
    continuous sentence."""
    md = """\
### 1. Chain Orphans

Body.

**What to do:** Fix the ETL or retire the chain edge. Drill to the
L2FT Chains sheet for the firing-count history per parent + child.
"""
    sections = parse_l2ft_exceptions(md)
    out = sections["chain_orphans"].what_to_do
    assert "\n" not in out
    assert "ETL or retire the chain edge. Drill to" in out


def test_parse_l2ft_exceptions_ignores_non_heading_lines() -> None:
    """Lines that don't match the ``### N. <Title>`` pattern are
    skipped (the parser is not confused by lead-in prose)."""
    md = """\
# L2FT Hygiene Exceptions

Intro paragraph. None of this matches the heading regex.

## A subsection that's not a section we care about

More prose.

### 1. Chain Orphans

Body.
"""
    sections = parse_l2ft_exceptions(md)
    assert set(sections.keys()) == {"chain_orphans"}


# -- Bundled-doc tests ------------------------------------------------------


def test_bundled_l2ft_exceptions_parses() -> None:
    """The bundled doc loads + parses to a non-empty dict."""
    sections = load_bundled_l2ft_exceptions()
    assert len(sections) >= 1


def test_bundled_l2ft_exceptions_covers_every_check_kind() -> None:
    """Every L2FT hygiene check that ships as a ``check_type``
    literal in the unified dataset has a corresponding parser
    section. Cross-checks the doc against the dashboard wiring so
    the panel doesn't silently miss a kind."""
    sections = load_bundled_l2ft_exceptions()
    expected_kinds = set(L2FT_EXCEPTION_KIND_TO_SHEET.keys())
    assert set(sections.keys()) == expected_kinds


def test_bundled_l2ft_exceptions_every_section_has_what_to_do() -> None:
    """Soft contract: every authored section MUST carry a
    ``**What to do:**`` line. AA.C.4 added it to all 6 sections; if
    a future doc edit drops one the panel would fall back to a
    placeholder string, which we'd rather catch in tests than ship."""
    sections = load_bundled_l2ft_exceptions()
    for kind, section in sections.items():
        assert section.what_to_do, (
            f"{kind} section missing **What to do:** remediation line"
        )


def test_bundled_l2ft_exceptions_every_section_has_columns() -> None:
    """Every authored section declares its column list (mirrors the
    L1 invariants doc contract). Catches a future edit that drops
    a Columns line."""
    sections = load_bundled_l2ft_exceptions()
    for kind, section in sections.items():
        assert section.columns, (
            f"{kind} section missing **Columns:** line"
        )


def test_bundled_l2ft_exceptions_title_matches_check_type_literal() -> None:
    """Each section's title MUST match the unified L2FT dataset's
    ``check_type`` literal exactly (case + spacing). Cross-links the
    parser-side kind to the runtime dataset's discriminator column —
    a future renderer rendering a panel for a clicked row needs the
    titles to match."""
    sections = load_bundled_l2ft_exceptions()
    # These are the CAST literals in build_unified_l2_exceptions_dataset
    # (apps/l2_flow_tracing/datasets.py). Post-Z.B (2026-05-15) they
    # match the rail_name vocabulary.
    expected_titles = {
        "Chain Orphans",
        "Unmatched Rail Name",
        "Dead Rails",
        "Dead Bundles Activity",
        "Dead Metadata Declarations",
        "Dead Limit Schedules",
    }
    actual_titles = {s.title for s in sections.values()}
    assert actual_titles == expected_titles


# -- panel_markdown tests ---------------------------------------------------


def test_panel_markdown_lists_every_kind() -> None:
    """The composed panel names every check kind so an operator
    skimming the sheet bottom sees the full catalog."""
    sections = load_bundled_l2ft_exceptions()
    panel = panel_markdown(sections)
    for section in sections.values():
        assert section.title in panel


def test_panel_markdown_includes_what_to_do_inline() -> None:
    """Each bullet's body is the section's ``what_to_do`` paragraph
    (the panel surfaces remediation directly, not just check
    descriptions)."""
    sections = load_bundled_l2ft_exceptions()
    panel = panel_markdown(sections)
    for section in sections.values():
        if section.what_to_do:
            # Match the first ~40 chars to avoid false-negatives on
            # markdown-formatting reshapes in the panel composer.
            head = section.what_to_do[:40]
            assert head in panel, (
                f"{section.kind} what_to_do not surfaced in panel"
            )


def test_panel_markdown_intro_calls_out_l2_to_runtime_correspondence() -> None:
    """Intro paragraph names the operational framing: these checks
    are L2-to-runtime drift, not L1 ledger violations. Helps the
    operator understand the sheet's purpose at a glance."""
    sections = load_bundled_l2ft_exceptions()
    panel = panel_markdown(sections)
    assert "L2-to-runtime correspondence" in panel


def test_panel_markdown_handles_empty_what_to_do_with_fallback() -> None:
    """The fallback contract: a section with no remediation paragraph
    gets the ``(remediation guidance not yet authored)`` placeholder.
    Prevents a partial-edit doc from blowing up the dashboard build."""
    sections = {
        "stub": L2FTExceptionSection(
            kind="stub", title="Stub Check", body="body", columns=(),
            what_to_do="",
        ),
    }
    panel = panel_markdown(sections)
    assert "(remediation guidance not yet authored)" in panel
