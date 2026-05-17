"""L2FT Hygiene Exceptions parser — AA.C.4.

Reads ``src/quicksight_gen/docs/L2FT_Exceptions.md`` (the single
source of truth for the six L2FT runtime checks) and returns a typed
mapping of exception kind -> :class:`L2FTExceptionSection`.

Mirrors :mod:`common.handbook.invariants` (which serves the L1
invariants doc + L1 dashboard panels). Two parser deltas from the L1
flavor:

- L2FT exception headings are plain ``### N. <Title>`` — no
  ``{{ l2_instance_name }}_<kind>`` Jinja prefix, because the kinds
  are check_type labels (``"Chain Orphans"`` / ``"Unmatched Rail
  Name"`` / etc.) shipped in the L2FT dashboard's unified-exceptions
  ``check_type`` column. Kind keys are slug-cased titles
  (``"chain_orphans"`` / ``"unmatched_rail_name"`` / …).
- No SHOULD-blockquote convention. L2FT exceptions are runtime
  checks against the L2 declaration, not L1 SHOULD-constraints —
  the leading prose paragraph is the "what this surfaces"
  description, no blockquote indirection.

The doc carries ``**Columns:** ...`` + ``**What to do:** ...`` lines
in the same shape as L1_Invariants.md; the parser extracts them
identically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources


# Pin: which parsed exception kind maps to which L2FT dashboard sheet.
# All six L2FT hygiene checks roll up onto the unified ``L2 Hygiene
# Exceptions`` sheet (M.3.10l) — there's no per-kind sheet split (the
# row count per kind would be too sparse). The mapping is kept here
# for symmetry with :data:`common.handbook.invariants.INVARIANT_KIND_TO_SHEET`
# and to let AA.C.6 browser tests assert sheet-side wiring without
# hard-coding the sheet name in two places.
L2FT_EXCEPTION_KIND_TO_SHEET: dict[str, str] = {
    "chain_orphans": "L2 Hygiene Exceptions",
    "unmatched_rail_name": "L2 Hygiene Exceptions",
    "dead_rails": "L2 Hygiene Exceptions",
    "dead_bundles_activity": "L2 Hygiene Exceptions",
    # NB: slug-from-title yields ``dead_metadata_declarations`` even
    # though the build_exc_dead_metadata function name is shorter.
    # The title (and the unified dataset's ``check_type`` literal) is
    # ``"Dead Metadata Declarations"`` per the dashboard wording — the
    # kind slug tracks the title since that's what the parser derives.
    "dead_metadata_declarations": "L2 Hygiene Exceptions",
    "dead_limit_schedules": "L2 Hygiene Exceptions",
}


@dataclass(frozen=True)
class L2FTExceptionSection:
    """One parsed section from ``L2FT_Exceptions.md``.

    Same shape as :class:`common.handbook.invariants.InvariantSection`
    minus ``short_statement`` — L2FT exceptions don't use blockquote
    SHOULD-statements (they're runtime checks against L2, not L1
    invariants).
    """

    kind: str
    """Slug-cased section title — ``"chain_orphans"`` /
    ``"unmatched_rail_name"`` / etc. Joins to
    :data:`L2FT_EXCEPTION_KIND_TO_SHEET` and to the L2FT unified
    dataset's ``check_type`` literals (title-cased there: ``"Chain
    Orphans"``)."""

    title: str
    """Human heading — ``"Chain Orphans"`` /
    ``"Unmatched Rail Name"``. Matches the unified L2 Exceptions
    dataset's ``check_type`` literal exactly so a future renderer can
    cross-link a row to its panel description."""

    body: str
    """Prose paragraphs after the heading. The ``**What to do:** ...``
    line is *extracted* into :attr:`what_to_do` and dropped from
    ``body`` so the dashboard panel can render the remediation in its
    own styled block. The ``**Columns:** ...`` line stays inline."""

    columns: tuple[str, ...]
    """Parsed column names from the ``**Columns:** ...`` line. Empty
    tuple when the section doesn't declare columns."""

    what_to_do: str
    """Remediation paragraph parsed from the ``**What to do:** ...``
    line. One-paragraph guidance: what does a row in this check mean
    for the integrator, and what should they do about it. Empty
    string when the section omits the line."""


_HEADING = re.compile(r"^###\s+(?P<n>\d+)\.\s+(?P<title>.+?)\s*$")
"""Matches ``### 1. Chain Orphans``. No Jinja prefix (the L1 flavor
has ``\\`{{ l2_instance_name }}_<kind>\\``` — L2FT doesn't)."""

_HEADING_ANY = re.compile(r"^(#{2,3})\s+.+$")
"""Any ``##`` / ``###`` heading — used as a section terminator."""

_COLUMNS_LINE = re.compile(
    r"^\*\*Columns:\*\*\s+(?P<rest>.+?)(?=^\s*$|\Z)",
    re.MULTILINE | re.DOTALL,
)
_WHAT_TO_DO_LINE = re.compile(
    r"^\*\*What to do:\*\*\s+(?P<rest>.+?)(?=^\s*$|\Z)",
    re.MULTILINE | re.DOTALL,
)
_COLUMN_TOKEN = re.compile(r"`([^`]+)`")


def _title_to_kind(title: str) -> str:
    """``"Chain Orphans"`` -> ``"chain_orphans"``. The kind is the
    snake-cased title, stable across L2 instances (the dashboard's
    ``check_type`` literal IS the title, so the slug derivation is
    one-step and round-trippable)."""
    out: list[str] = []
    for ch in title:
        if ch.isalnum():
            out.append(ch.lower())
        elif out and out[-1] != "_":
            out.append("_")
    return "".join(out).strip("_")


def _parse_columns(body: str) -> tuple[str, ...]:
    """Extract every backticked token from a ``**Columns:** ...`` line."""
    match = _COLUMNS_LINE.search(body)
    if not match:
        return ()
    return tuple(_COLUMN_TOKEN.findall(match.group("rest")))


def _extract_what_to_do(body: str) -> tuple[str, str]:
    """Pull the ``**What to do:** ...`` paragraph out of ``body``.

    Returns ``(stripped_body, what_to_do)`` — same contract as
    :func:`common.handbook.invariants._extract_what_to_do`. The doc
    wraps the paragraph across multiple physical lines; the dashboard
    panel wants one continuous sentence, so internal newlines collapse
    to single spaces.
    """
    match = _WHAT_TO_DO_LINE.search(body)
    if not match:
        return body, ""
    raw = match.group("rest").strip()
    paragraph = " ".join(line.strip() for line in raw.splitlines() if line.strip())
    span_start, span_end = match.span()
    stripped = (body[:span_start] + body[span_end:]).strip()
    while "\n\n\n" in stripped:
        stripped = stripped.replace("\n\n\n", "\n\n")
    return stripped, paragraph


def parse_l2ft_exceptions(md_text: str) -> dict[str, L2FTExceptionSection]:
    """Walk the markdown source and yield one section per ``### N.
    <Title>`` heading. Returns ``{kind: L2FTExceptionSection}``."""
    sections: dict[str, L2FTExceptionSection] = {}
    lines = md_text.splitlines()

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        match = _HEADING.match(line)
        if not match:
            i += 1
            continue

        title = match.group("title").strip()
        kind = _title_to_kind(title)

        # Consume body until the next heading (## or ###).
        body_lines: list[str] = []
        j = i + 1
        while j < n:
            if _HEADING_ANY.match(lines[j]):
                break
            body_lines.append(lines[j])
            j += 1

        body = "\n".join(body_lines).strip("\n")
        columns = _parse_columns(body)
        body, what_to_do = _extract_what_to_do(body)

        sections[kind] = L2FTExceptionSection(
            kind=kind, title=title, body=body, columns=columns,
            what_to_do=what_to_do,
        )
        i = j

    return sections


def load_bundled_l2ft_exceptions() -> dict[str, L2FTExceptionSection]:
    """Read the bundled ``L2FT_Exceptions.md`` from
    ``quicksight_gen.docs`` and return parsed sections.

    Single call site for the dashboard-side consumer (AA.C.4 panel
    wiring) — it doesn't need to know where the doc lives.
    """
    md_text = (
        resources.files("quicksight_gen.docs")
        .joinpath("L2FT_Exceptions.md")
        .read_text(encoding="utf-8")
    )
    return parse_l2ft_exceptions(md_text)


def panel_markdown(sections: dict[str, L2FTExceptionSection]) -> str:
    """Compose the L2 Hygiene Exceptions sheet's bottom panel.

    L2FT's six checks all roll up onto one sheet (the unified Hygiene
    Exceptions sheet), so unlike L1 — where each invariant kind has
    its own sheet + panel — L2FT gets one panel that lists all six
    kinds with their remediation guidance inline. Shape mirrors L1's
    Today's Exceptions intro panel (AA.C.3.e).

    Returns a markdown string suitable for ``rich_text.markdown(...)``.
    """
    parts = [
        "**L2 Hygiene Exceptions — what each check surfaces**",
        (
            "Each row in the table above is a piece of L2 declaration "
            "that doesn't match the live runtime. None of these break "
            "the ledger; they break the L2-to-runtime correspondence "
            "the integrator's ETL is supposed to maintain. The six "
            "check kinds:"
        ),
    ]
    for section in sections.values():
        line = f"- **{section.title}.** "
        if section.what_to_do:
            line += section.what_to_do
        else:
            # Soft contract: every L2FT exception section SHOULD carry
            # a what_to_do line. Tests pin this — the fallback exists
            # so a partial-edit doc doesn't blow up the dashboard.
            line += "(remediation guidance not yet authored)"
        parts.append(line)
    return "\n\n".join(parts)
