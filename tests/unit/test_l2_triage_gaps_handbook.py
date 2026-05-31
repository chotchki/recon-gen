"""BU.2a — L2TriageGapSection typed catalogue tests.

Pins the parser + the doc-vs-code anti-drift gate. Mirrors
``tests/unit/test_l2ft_exceptions_handbook.py`` /
``tests/unit/test_invariants_handbook.py`` patterns.
"""

from __future__ import annotations

from typing import get_args

from recon_gen.common.handbook.l2_triage_gaps import (
    EDITOR_LABEL_BY_KIND,
    SECTION_TITLE_BY_KIND,
    L2TriageGapSection,
    load_bundled_l2_triage_gaps,
    parse_l2_triage_gaps,
)
from recon_gen.common.l2.triage import GapKind


# -- Parser shape ---------------------------------------------------------


def test_load_bundled_returns_section_for_every_kind_in_title_table() -> None:
    """Section catalogue covers two families per BU.2b: the four
    GapKind literals (L2 Triage) plus the L2 Coverage extension keys
    (uncovered_rail / uncovered_template). Set membership = the title
    table (which is the authoritative kind universe)."""
    sections = load_bundled_l2_triage_gaps()
    assert set(sections.keys()) == set(SECTION_TITLE_BY_KIND.keys())


def test_every_gap_kind_literal_has_a_section() -> None:
    """L2 Triage subset gate: every `GapKind` literal MUST still have
    a backing section — the section catalogue widening to include
    Coverage keys can't have silently dropped a Triage kind."""
    sections = load_bundled_l2_triage_gaps()
    for gap_kind in get_args(GapKind):
        assert gap_kind in sections, (
            f"GapKind {gap_kind!r} lost its section in the catalogue "
            f"widening — restore the ### heading in L2_Triage_Gaps.md."
        )


def test_every_section_has_what_to_do() -> None:
    """SoT contract: every triage gap section MUST carry remediation
    guidance — the operator's only way out of a gap is action."""
    for kind, section in load_bundled_l2_triage_gaps().items():
        assert section.what_to_do, f"section {kind!r} missing **What to do:**"
        assert len(section.what_to_do) > 50, (
            f"section {kind!r} what_to_do is one-liner stub, expand it"
        )


def test_every_section_has_columns() -> None:
    """Each gap kind documents the columns the dashboard surfaces, so
    the operator knows what to look for in the card body."""
    for kind, section in load_bundled_l2_triage_gaps().items():
        assert section.columns, f"section {kind!r} missing **Columns:**"


def test_section_titles_match_kind_title_table() -> None:
    """Title-to-kind mapping is the load-time gate that catches a doc
    heading rename divorced from a `SECTION_TITLE_BY_KIND` update."""
    sections = load_bundled_l2_triage_gaps()
    for kind, expected_title in SECTION_TITLE_BY_KIND.items():
        assert sections[kind].title == expected_title


def test_editor_labels_attached_to_sections() -> None:
    """The `_GAP_KIND_EDITOR_LABELS` migration must round-trip through
    the typed source. Empty / wrong labels would silently swap the CTA
    on the triage page."""
    sections = load_bundled_l2_triage_gaps()
    for kind, expected_label in EDITOR_LABEL_BY_KIND.items():
        assert sections[kind].editor_label == expected_label
        assert "Open" in sections[kind].editor_label


# -- Anti-drift: kind tables ↔ section coverage ---------------------------


def test_kind_title_table_covers_every_gap_kind() -> None:
    """Every `GapKind` literal MUST have an entry in
    `SECTION_TITLE_BY_KIND`. A new GapKind landing in `triage.py`
    without a doc section is a silent-blind-spot bug — the parser
    would still load but the new kind would render with a fallback /
    KeyError downstream."""
    declared_kinds = set(get_args(GapKind))
    table_kinds = set(SECTION_TITLE_BY_KIND.keys())
    missing = declared_kinds - table_kinds
    assert not missing, (
        f"GapKind literals {missing!r} have no entry in "
        f"common.handbook.l2_triage_gaps.SECTION_TITLE_BY_KIND — add "
        f"the title mapping and a corresponding ### section in "
        f"docs/L2_Triage_Gaps.md."
    )


def test_kind_title_table_includes_l2_coverage_extension() -> None:
    """BU.2b L2 Coverage extension: the catalogue MUST include the two
    coverage section kinds. The L2 Coverage `PlantKindEntry` rows in
    `PLANT_REGISTRY` look these up via `_resolve_l2_triage_section`."""
    table_kinds = set(SECTION_TITLE_BY_KIND.keys())
    for coverage_kind in ("uncovered_rail", "uncovered_template"):
        assert coverage_kind in table_kinds, (
            f"L2 Coverage section {coverage_kind!r} missing from "
            f"SECTION_TITLE_BY_KIND — add the table entry + the "
            f"matching ### section in L2_Triage_Gaps.md."
        )


def test_editor_label_table_matches_kind_table() -> None:
    """Editor labels + section titles cover the same kinds — symmetric
    surfaces, can't have one drift past the other."""
    assert set(SECTION_TITLE_BY_KIND.keys()) == set(EDITOR_LABEL_BY_KIND.keys())


# -- Direct parser sanity --------------------------------------------------


def test_parser_handles_doc_text_directly() -> None:
    """Parse a minimal handcrafted doc to exercise the parser without
    coupling to the bundled doc's exact wording."""
    md = """# Title

Intro paragraph.

## How the data flows

```
diagram
```

## The four triage gap kinds

### 1. Unmatched rail_name

Some prose about unmatched rails. More prose.

**Columns:** `rail_name`, `posting_count`.

**What to do:** Add the rail to the L2 yaml OR fix the ETL. The
remediation paragraph wraps across multiple lines and should be
collapsed by the parser.
"""
    sections = parse_l2_triage_gaps(md)
    assert set(sections.keys()) == {"unmatched_rail"}
    s = sections["unmatched_rail"]
    assert isinstance(s, L2TriageGapSection)
    assert s.title == "Unmatched rail_name"
    assert s.label == "Unmatched rail_name"
    assert s.editor_label == "Open Rails editor"
    assert s.columns == ("rail_name", "posting_count")
    assert "Add the rail" in s.what_to_do
    assert "\n" not in s.what_to_do  # multi-line paragraph collapsed
    assert "What to do" not in s.body  # extracted out of body
    assert "Some prose" in s.body


def test_parser_loud_fails_on_unknown_title() -> None:
    """Anti-drift: doc heading that doesn't match `KIND_TITLE_BY_GAP`
    raises KeyError — protects against a heading rename divorced from
    a table update."""
    import pytest

    md = """### 1. A heading the table doesn't know

Body.
"""
    with pytest.raises(KeyError, match="SECTION_TITLE_BY_KIND"):
        parse_l2_triage_gaps(md)
