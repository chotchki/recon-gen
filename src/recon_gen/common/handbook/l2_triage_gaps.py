"""L2 Triage Gaps parser — BU.2a (Lock 10).

Reads ``src/recon_gen/docs/L2_Triage_Gaps.md`` (the single source of
truth for the four L2-to-runtime triage gap kinds) and returns a
typed mapping of gap kind -> :class:`L2TriageGapSection`.

Mirrors :mod:`common.handbook.l2ft_exceptions` (the L2FT hygiene
catalogue) and :mod:`common.handbook.invariants` (the L1 invariants
catalogue). Parser shape is identical to the L2FT flavor: ``### N.
<Title>`` headings, ``**Columns:** ...`` line, ``**What to do:** ...``
line — no SHOULD blockquote prefix (these are runtime checks against
the L2, not L1 SHOULD-constraints).

The single difference from L2FT is the kind slug derivation. The
triage gap kinds are the four ``GapKind`` literals
(``unmatched_rail`` / ``unmatched_template`` / ``missing_limit_schedule``
/ ``missing_metadata_key``) — they don't map to the doc's natural
title slugs one-to-one (e.g. doc title ``"Unmatched rail_name"``
slugs to ``"unmatched_rail_name"`` but the ``GapKind`` literal is
``"unmatched_rail"``). The doc orders sections to match the GapKind
tuple order so the parser can zip them positionally; the
:data:`KIND_TITLE_BY_GAP` table pins the title-to-kind mapping
explicitly so a doc reordering would loud-fail rather than silently
mis-attach.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources


# Pin: which Lock-10 GapKind maps to which doc-section title. The doc
# is authored with operator-readable titles (``"Unmatched rail_name"``)
# while the typed kind keys are the ``GapKind`` literals defined in
# :mod:`common.l2.triage` (``"unmatched_rail"``). Keep these two
# vocabularies aligned here — a doc edit that renames a heading OR a
# new GapKind landing without a section will both surface as KeyError
# at parser load + at the anti-drift test in
# ``tests/unit/test_l2_triage_gaps_handbook.py``.
KIND_TITLE_BY_GAP: Mapping[str, str] = {
    "unmatched_rail": "Unmatched rail_name",
    "unmatched_template": "Unmatched template_name",
    "missing_limit_schedule": "Missing LimitSchedule",
    "missing_metadata_key": "Missing required metadata key",
}


# Pin: per-kind editor CTA label rendered alongside the gap card.
# Moved off ``common.html._studio_routes._GAP_KIND_EDITOR_LABELS``
# into the typed catalogue so the editor copy + the section prose
# stay in one place. Triage card render reads this table.
EDITOR_LABEL_BY_GAP: Mapping[str, str] = {
    "unmatched_rail": "Open Rails editor",
    "unmatched_template": "Open Templates editor",
    "missing_limit_schedule": "Open Limits editor",
    "missing_metadata_key": "Open template editor",
}


@dataclass(frozen=True)
class L2TriageGapSection:
    """One parsed section from ``L2_Triage_Gaps.md``.

    Same shape as :class:`common.handbook.l2ft_exceptions.L2FTExceptionSection`
    plus a ``label`` field that's the canonical short operator-facing
    label (replaces the inline ``_GAP_KIND_LABELS`` dict in
    ``_studio_routes``).
    """

    kind: str
    """One of the four :class:`common.l2.triage.GapKind` literals —
    ``"unmatched_rail"`` / ``"unmatched_template"`` /
    ``"missing_limit_schedule"`` / ``"missing_metadata_key"``."""

    title: str
    """Human heading — ``"Unmatched rail_name"`` /
    ``"Missing LimitSchedule"`` / etc. Matches the doc heading
    verbatim so a renderer can cross-link a gap card to its section."""

    label: str
    """Short operator-facing label rendered in the triage accordion
    header. Replaces ``common.html._studio_routes._GAP_KIND_LABELS``.
    Same as ``title`` by default — split for future divergence."""

    editor_label: str
    """CTA copy on the gap card's editor link (``"Open Rails
    editor"`` / etc.). Replaces ``_GAP_KIND_EDITOR_LABELS``."""

    body: str
    """Prose paragraphs after the heading. The ``**What to do:** ...``
    line is *extracted* into :attr:`what_to_do` and dropped from
    ``body`` (mirrors the L1 + L2FT parsers). The ``**Columns:** ...``
    line stays inline."""

    columns: tuple[str, ...]
    """Parsed column names from the ``**Columns:** ...`` line."""

    what_to_do: str
    """Remediation paragraph parsed from the ``**What to do:** ...``
    line. One-paragraph guidance — what does this gap mean for the
    integrator and what should they do about it."""


_HEADING = re.compile(r"^###\s+(?P<n>\d+)\.\s+(?P<title>.+?)\s*$")
_HEADING_ANY = re.compile(r"^(#{2,3})\s+.+$")
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
    """Reverse-lookup the ``GapKind`` for a doc heading title. Loud-fails
    when the doc title isn't in :data:`KIND_TITLE_BY_GAP` — a doc edit
    that introduces a heading without a backing kind (or vice versa) is
    a bug the parser refuses to hide."""
    for kind, kind_title in KIND_TITLE_BY_GAP.items():
        if kind_title == title:
            return kind
    raise KeyError(
        f"L2_Triage_Gaps.md heading {title!r} doesn't map to any "
        f"GapKind in KIND_TITLE_BY_GAP — either the doc renamed a "
        f"heading or a new GapKind was added without updating "
        f"common.handbook.l2_triage_gaps.KIND_TITLE_BY_GAP."
    )


def _parse_columns(body: str) -> tuple[str, ...]:
    match = _COLUMNS_LINE.search(body)
    if not match:
        return ()
    return tuple(_COLUMN_TOKEN.findall(match.group("rest")))


def _extract_what_to_do(body: str) -> tuple[str, str]:
    """Pull the ``**What to do:** ...`` paragraph out of ``body``.

    Returns ``(stripped_body, what_to_do)`` — same contract as the L1
    + L2FT parsers. The doc wraps the paragraph across multiple
    physical lines; renderers want one continuous sentence so internal
    newlines collapse to single spaces."""
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


def parse_l2_triage_gaps(md_text: str) -> dict[str, L2TriageGapSection]:
    """Walk the markdown source and yield one section per
    ``### N. <Title>`` heading. Returns ``{kind: L2TriageGapSection}``
    keyed on the :class:`common.l2.triage.GapKind` literal."""
    sections: dict[str, L2TriageGapSection] = {}
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

        sections[kind] = L2TriageGapSection(
            kind=kind,
            title=title,
            label=title,
            editor_label=EDITOR_LABEL_BY_GAP[kind],
            body=body,
            columns=columns,
            what_to_do=what_to_do,
        )
        i = j

    return sections


def load_bundled_l2_triage_gaps() -> dict[str, L2TriageGapSection]:
    """Read the bundled ``L2_Triage_Gaps.md`` from ``recon_gen.docs``
    and return parsed sections. Single call site for the triage page
    + the BU.2b registry adapter — neither needs to know where the
    doc lives."""
    md_text = (
        resources.files("recon_gen.docs")
        .joinpath("L2_Triage_Gaps.md")
        .read_text(encoding="utf-8")
    )
    return parse_l2_triage_gaps(md_text)
