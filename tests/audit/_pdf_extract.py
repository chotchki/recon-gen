# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# BF.4/F: pypdf interop is mostly Any — outline / pages / get_destination_page_number
# all return untyped. The PDF parser uses dynamic walks that don't surface in stubs.
"""Extract per-invariant table row counts from a rendered audit PDF (U.8.b.1).

The audit emits violation tables per L1 invariant under a
Title-Case section heading ("Drift Violations" / "Overdraft
Violations" / etc.). U.8.b's three-way agreement assert needs the
PDF-side row count to compare against the scenario-derived expected
count + the L1 dashboard's row count.

We use pypdf's outline (level-0 bookmarks placed by U.3.h) to find
each section's start page, then layout-mode text extraction over
those pages, then a "data-row signature" heuristic to count rows
across whatever sub-tables the section emits (drift renders one
table; overdraft / stuck_unbundled split into Parent + Child Grouped;
limit_breach + stuck_pending may render only a Child Grouped table;
supersession has Aggregate + Transactions + Daily balances).

A line is counted as a data row when:
  - it has at least 3 columns of content (split on runs of ≥ 3
    spaces — reportlab's layout-mode output uses wide gaps between
    cells); AND
  - it carries at least one numeric data signal (date `YYYY-MM-DD`,
    dollar amount, age suffix like `5.2d` / `2.0d`, or a trailing
    integer count for the supersession aggregate).

This excludes header rows (no numeric content) and continuation
lines from wrapped cells (single-fragment).

Returns 0 when:
  - The section emits a "Database not configured" placeholder
    (no DB at audit time — skeleton PDF).
  - The section emits a "No X detected" message (DB configured but
    the matview is empty).
  - No data rows match the signature for any other reason.

Raises ``ValueError`` when the named section heading isn't on the
PDF outline at level 0 — caller passed an unknown invariant or the
PDF predates U.7.5's Title-Case sweep.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal


Invariant = Literal[
    "drift",
    "overdraft",
    "limit_breach",
    "stuck_pending",
    "stuck_unbundled",
    "supersession",
]


_INVARIANT_TITLES: dict[Invariant, str] = {
    "drift": "Drift Violations",
    "overdraft": "Overdraft Violations",
    "limit_breach": "Limit Breach Violations",
    "stuck_pending": "Stuck Pending Transactions",
    "stuck_unbundled": "Stuck Unbundled Transactions",
    "supersession": "Supersession Audit",
}


# Phrases the audit emits when the section has zero rows.
_EMPTY_MARKERS: tuple[str, ...] = (
    "Database not configured",
    "No drift detected",
    "No overdrafts detected",
    "No limit breaches detected",
    "No stuck pending",
    "No stuck unbundled",
    "No supersessions recorded",
)


# Signals that a line carries cell data (vs. header / prose /
# section sub-heading). Any one match is sufficient.
_DATA_SIGNAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),          # ISO date
    re.compile(r"\$-?[\d,]+\.\d{2}"),               # dollar amount
    re.compile(r"\b\d+\.\d+d\b"),                   # age "5.2d"
    re.compile(r"\s\d[\d,]*\s*$"),                  # trailing integer
)


# Reportlab's layout extraction puts ≥ 3 spaces between adjacent
# cells in a row. Splitting on that boundary gives column count.
_COLUMN_GAP_RE = re.compile(r" {3,}")


def count_invariant_table_rows(
    pdf_path: Path,
    invariant: Invariant,
) -> int:
    """Count data rows in the named invariant's violation table(s).

    Excludes header rows + continuation lines + sub-headings.
    Returns 0 for empty sections (DB unconfigured OR DB returned
    no rows) — see module docstring for the empty-marker phrases
    recognized.
    """
    from pypdf import PdfReader

    title = _INVARIANT_TITLES[invariant]
    reader = PdfReader(str(pdf_path))
    start_page, end_page = _locate_section_pages(reader, title)
    section_lines = _section_layout_lines(
        reader, start_page=start_page, end_page=end_page,
    )
    return _count_data_rows(section_lines)


def _locate_section_pages(
    reader,  # type: ignore[no-untyped-def]: pypdf PdfReader, untyped to keep pyright off the optional dep
    title: str,
) -> tuple[int, int]:
    """Find ``(start, end_exclusive)`` page indices for a level-0 section.

    Walks the outline, finds the entry whose title equals ``title``,
    returns its destination page and the next level-0 entry's
    destination page (or last page + 1 if it's the final section).
    """
    level0_pages: list[tuple[str, int]] = []
    for item in reader.outline:
        if isinstance(item, list):
            continue
        page = reader.get_destination_page_number(item)
        level0_pages.append((item.title, page))
    for i, (this_title, this_page) in enumerate(level0_pages):
        if this_title == title:
            if i + 1 < len(level0_pages):
                return (this_page, level0_pages[i + 1][1])
            return (this_page, len(reader.pages))
    raise ValueError(
        f"Section {title!r} not found in PDF outline (level-0 entries: "
        f"{[t for t, _ in level0_pages]})"
    )


def _section_layout_lines(
    reader,  # type: ignore[no-untyped-def]: pypdf PdfReader, untyped to keep pyright off the optional dep
    *,
    start_page: int,
    end_page: int,
) -> list[str]:
    """Concatenate layout-extracted lines from a page range.

    Strips the per-page footer line (matched by the
    ``Generated YYYY-MM-DD HH:MM`` substring U.6 places there) so
    the row counter doesn't have to special-case it.
    """
    lines: list[str] = []
    for p_idx in range(start_page, end_page):
        text = reader.pages[p_idx].extract_text(extraction_mode="layout")
        for line in text.split("\n"):
            if "Generated " in line and "Page " in line:
                continue
            lines.append(line)
    return lines


def _count_data_rows(section_lines: list[str]) -> int:
    """Count data rows by signature across all sub-tables in the section.

    Skips header rows (multi-fragment but no numeric content),
    continuation lines from wrapped cells (single-fragment), and
    sub-section H3 headings ("Parent Accounts (Per-Row Detail)",
    "Child Accounts Grouped by …", "Aggregate (Entire Dataset)",
    "Transactions — …"). Counts every line that has ≥ 3 columns of
    content AND at least one numeric cell-data signal.
    """
    text = "\n".join(section_lines)
    for marker in _EMPTY_MARKERS:
        if marker in text:
            return 0

    rows = 0
    for line in section_lines:
        if _is_data_row(line):
            rows += 1
    return rows


def _is_data_row(line: str) -> bool:
    """A reportlab-table data row in layout-mode extracted text."""
    stripped = line.strip()
    if not stripped:
        return False
    fragments = [f for f in _COLUMN_GAP_RE.split(stripped) if f]
    if len(fragments) < 3:
        return False  # continuation line OR short sub-heading
    return any(p.search(line) for p in _DATA_SIGNAL_PATTERNS)
