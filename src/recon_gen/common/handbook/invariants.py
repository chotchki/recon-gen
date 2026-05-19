"""L1 invariants parser — AA.C.2.

Reads ``src/recon_gen/docs/L1_Invariants.md`` (the single source of
truth for the seven L1 SHOULD-constraints + the Supersession Audit
diagnostic surface) and returns a typed mapping of invariant kind ->
:class:`InvariantSection`. Phase O.1's mkdocs vocabulary keeps the doc
on disk; this parser lets the dashboard generators pull the same prose
into sheet-bottom panels (AA.C.3) and the Studio trainer pane
(AA.C.5) without duplicating the text.

Bundled-doc input has two Jinja-style affordances the parser strips by
default so the output is dashboard-paneable as-is:

- ``{{ l2_instance_name }}`` substitution in view-name headings (e.g.,
  ``` `{{ l2_instance_name }}_drift` ```) -- collapsed to the bare view
  suffix so panel titles read ``drift`` not ``{{ l2_instance_name }}_drift``.
- ``{% if vocab.fixture_name == "X" %} ... {% endif %}`` worked-example
  blocks -- dropped entirely; they belong in the handbook for that
  specific demo, not in a dashboard panel that might render against
  any L2.

Pass ``strip_jinja=False`` to get the raw doc content (e.g., for
re-rendering through the mkdocs pipeline).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources

# Pin: which parsed invariant kind maps to which L1 dashboard sheet.
# Sheet names are the analyst-facing strings (Sheet.name) carried by
# the L1 dashboard tree; see ``apps/l1_dashboard/app.py``. Multiple
# kinds can map to the same sheet -- ``drift`` and ``ledger_drift``
# both surface on the Drift sheet, and AA.C.3 will compose both
# bodies into one panel there. ``expected_eod_balance_breach`` rolls
# up into Today's Exceptions alongside the other invariant kinds,
# but its own dedicated sheet would land here if M.2b ever splits it
# out.
INVARIANT_KIND_TO_SHEET: dict[str, str] = {
    "drift": "Drift",
    "ledger_drift": "Drift",
    "overdraft": "Overdraft",
    "expected_eod_balance_breach": "Today's Exceptions",
    "limit_breach": "Limit Breach",
    "stuck_pending": "Pending Aging",
    "stuck_unbundled": "Unbundled Aging",
    # AB.2.3 — chain_parent_disagreement surfaces only on Today's
    # Exceptions (no dedicated sheet, because the violation is keyed
    # on transfer_id not (account, day) and the analyst's drill goes
    # straight to the Transactions sheet via the conflicting parent ids).
    "chain_parent_disagreement": "Today's Exceptions",
    # AB.3.3 — xor_group_violation also surfaces only on Today's
    # Exceptions (same pattern: keyed on transfer_id + template_name,
    # analyst's drill goes to the Transactions sheet to see which
    # variant did or didn't fire). Mirrors chain_parent_disagreement's
    # cross-tool wiring; no dedicated dashboard sheet.
    "xor_group_violation": "Today's Exceptions",
    # AB.4.7 — fan_in_disagreement also surfaces on Today's Exceptions
    # (same UNION-only pattern as AB.2.3 + AB.3.3). Keyed on
    # child_transfer_id + child_template_name + disagreement_kind;
    # analyst's drill goes to Transactions to see which contributing
    # parent legs landed (or didn't).
    "fan_in_disagreement": "Today's Exceptions",
    # AB.6.5 — multi_xor_violation surfaces on Today's Exceptions
    # too (same UNION-only pattern). Keyed on parent_transfer_id +
    # parent_rail_or_template_name + disagreement_kind ('missed' /
    # 'overlap'); analyst's drill goes to Transactions to see which
    # XOR alternatives did or didn't fire under the parent firing.
    "multi_xor_violation": "Today's Exceptions",
    "supersession_audit": "Supersession Audit",
}


@dataclass(frozen=True)
class InvariantSection:
    """One parsed section from ``L1_Invariants.md``.

    Two heading shapes feed this:

    - The seven numbered ``### N. `{{ l2_instance_name }}_<kind>` -- <title>``
      sections under "## The seven L1 SHOULD-constraints".
    - The ``## Diagnostic surface -- Supersession Audit`` section,
      mapped to ``kind="supersession_audit"`` for symmetry.
    """

    kind: str
    """Bare view-name suffix -- ``"drift"`` / ``"limit_breach"`` /
    ``"supersession_audit"``. Joins to :data:`INVARIANT_KIND_TO_SHEET`."""

    title: str
    """Human heading after the em-dash -- ``"Sub-ledger drift"``."""

    short_statement: str
    """The blockquote SHOULD-constraint, one paragraph stripped of the
    leading ``> `` markers. Empty string for sections without a
    blockquote (the Supersession Audit section is descriptive, not
    a SHOULD)."""

    body: str
    """Prose paragraphs after the blockquote (or the heading, for
    sections without one). The ``**What to do:** ...`` line is
    *extracted* into :attr:`what_to_do` and dropped from ``body`` so
    the dashboard panel can render the remediation in its own styled
    block. The ``**Columns:** ...`` line stays inline."""

    columns: tuple[str, ...]
    """Parsed column names from the ``**Columns:** ...`` line.
    Empty tuple when the section doesn't declare columns (the
    Supersession Audit section, for instance)."""

    what_to_do: str
    """The operator-facing remediation paragraph parsed from the
    ``**What to do:** ...`` line. One-paragraph guidance: what does
    a row in this matview mean for the integrator, and what should
    they do about it. Empty string when the section omits the line
    (a soft contract -- AA.C.2 added the line to all 8 sections in
    ``L1_Invariants.md`` and AA.C.3.f tests pin every kind has one)."""


_HEADING_NUMBERED = re.compile(
    r"^###\s+(?P<n>\d+)\.\s+`\{\{\s*l2_instance_name\s*\}\}_(?P<kind>\w+)`"
    r"\s+—\s+(?P<title>.+?)(?:\s+\([A-Z]\.[\w.]+\))?\s*$"
)
"""Matches ``### 1. `{{ l2_instance_name }}_drift` -- Sub-ledger drift``
plus an optional ``(M.2b.8)`` trailing tag. Em-dash is ``—`` (the
file is UTF-8 and uses curly em-dashes, not ``--``)."""

_HEADING_SUPERSESSION = re.compile(
    r"^##\s+Diagnostic surface\s+—\s+Supersession Audit\s*$"
)
"""The one ``## ...`` section we treat as an invariant kind despite
its non-SHOULD framing."""

_HEADING_ANY_L1 = re.compile(r"^(#{2,3})\s+.+$")
"""Any ``##`` / ``###`` heading -- used as a section terminator."""

_JINJA_IF_BLOCK = re.compile(
    r"\{%\s*if\b.*?%\}.*?\{%\s*endif\s*%\}", re.DOTALL,
)
"""Strip ``{% if ... %} ... {% endif %}`` worked-example blocks."""

_JINJA_INSTANCE_TOKEN = re.compile(r"\{\{\s*l2_instance_name\s*\}\}")
"""Strip the ``{{ l2_instance_name }}`` placeholder from prose."""

_COLUMNS_LINE = re.compile(
    r"^\*\*Columns:\*\*\s+(?P<rest>.+?)(?=^\s*$|\Z)",
    re.MULTILINE | re.DOTALL,
)
"""Match the ``**Columns:** ...`` block until the next blank line. The
columns line is wrapped across multiple physical lines in the doc."""

_WHAT_TO_DO_LINE = re.compile(
    r"^\*\*What to do:\*\*\s+(?P<rest>.+?)(?=^\s*$|\Z)",
    re.MULTILINE | re.DOTALL,
)
"""Match the ``**What to do:** ...`` paragraph until the next blank
line. AA.C.2 added one per section to drive the dashboard-panel
remediation block."""

_COLUMN_TOKEN = re.compile(r"`([^`]+)`")
"""Each column is fenced in backticks inside the Columns block."""


def _strip_jinja(text: str) -> str:
    """Remove fixture-conditional blocks + the ``l2_instance_name``
    token. Run between section-split and short_statement/body
    extraction so the dashboard panel never shows a literal
    ``{{ ... }}`` or a fixture-specific worked example."""
    text = _JINJA_IF_BLOCK.sub("", text)
    text = _JINJA_INSTANCE_TOKEN.sub("", text)
    # The token strip can leave a stray leading underscore in the
    # numbered headings' inline code (``_drift`` after the empty
    # substitution). The headings themselves are consumed by the
    # parser before this runs, but a body paragraph that happens
    # to reference ``{{ l2_instance_name }}_view`` would now read
    # ``_view`` -- acceptable for panel display.
    return text


def _parse_columns(body: str) -> tuple[str, ...]:
    """Extract every backticked token from a ``**Columns:** ...`` line.
    Returns empty tuple when the body has no Columns line."""
    match = _COLUMNS_LINE.search(body)
    if not match:
        return ()
    return tuple(_COLUMN_TOKEN.findall(match.group("rest")))


def _extract_what_to_do(body: str) -> tuple[str, str]:
    """Pull the ``**What to do:** ...`` paragraph out of ``body``.

    Returns ``(stripped_body, what_to_do)`` -- ``stripped_body`` is
    the input minus the matched block + any blank line left behind,
    ``what_to_do`` is the paragraph text with internal newlines
    collapsed to single spaces (the doc wraps long lines for readability;
    the dashboard panel wants one continuous sentence). Returns
    ``(body, "")`` when no match."""
    match = _WHAT_TO_DO_LINE.search(body)
    if not match:
        return body, ""
    raw = match.group("rest").strip()
    paragraph = " ".join(line.strip() for line in raw.splitlines() if line.strip())
    span_start, span_end = match.span()
    stripped = (body[:span_start] + body[span_end:]).strip()
    # Drop the blank-line gap the strip can leave behind.
    while "\n\n\n" in stripped:
        stripped = stripped.replace("\n\n\n", "\n\n")
    return stripped, paragraph


def _split_short_statement_and_body(raw_section: str) -> tuple[str, str]:
    """Split a section's content into (blockquote, rest).

    The blockquote is one or more contiguous ``> `` lines at the top of
    the section (with intervening blank-line-prefixed continuations
    counted as part of the same quote). The rest is everything after
    the first non-quote, non-blank line.
    """
    lines = raw_section.splitlines()
    quote_lines: list[str] = []
    body_start = 0
    in_quote = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(">"):
            in_quote = True
            # ``> For every ...`` -> ``For every ...``; ``>`` alone -> ``""``.
            quote_lines.append(stripped.lstrip(">").lstrip())
        elif in_quote and stripped == "":
            # Blank between quote and body -- consume, look ahead.
            continue
        else:
            body_start = i
            break
    else:
        # All lines were quote / blank.
        body_start = len(lines)
    short = " ".join(s for s in quote_lines if s).strip()
    body = "\n".join(lines[body_start:]).strip()
    return short, body


def parse_l1_invariants(
    md_text: str, *, strip_jinja: bool = True,
) -> dict[str, InvariantSection]:
    """Walk the markdown source and yield one section per recognized
    heading. Returns ``{kind: InvariantSection}``.

    See module docstring for the Jinja stripping contract -- pass
    ``strip_jinja=False`` only when you want raw doc content (e.g.,
    re-rendering through the mkdocs pipeline).
    """
    # Heading matching runs on the *raw* text — the ``{{ l2_instance_name }}``
    # token is part of the heading pattern itself. Jinja stripping runs
    # on body content (post-extraction) where the conditional blocks +
    # the placeholder are display noise.
    sections: dict[str, InvariantSection] = {}
    lines = md_text.splitlines()

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        kind: str | None = None
        title: str | None = None

        numbered = _HEADING_NUMBERED.match(line)
        if numbered:
            kind = numbered.group("kind")
            title = numbered.group("title").strip()
        elif _HEADING_SUPERSESSION.match(line):
            kind = "supersession_audit"
            title = "Supersession Audit"

        if kind is None or title is None:
            i += 1
            continue

        # Consume body until the next heading (## or ###).
        body_lines: list[str] = []
        j = i + 1
        while j < n:
            if _HEADING_ANY_L1.match(lines[j]):
                break
            body_lines.append(lines[j])
            j += 1

        raw_section = "\n".join(body_lines).strip("\n")
        if strip_jinja:
            raw_section = _strip_jinja(raw_section).strip("\n")
        short, body = _split_short_statement_and_body(raw_section)
        # Order matters: parse columns (which leaves the line in body
        # for panel rendering), then extract what_to_do (which lifts
        # the line OUT of body into its own field so the panel can
        # style the remediation separately).
        columns = _parse_columns(body)
        body, what_to_do = _extract_what_to_do(body)

        sections[kind] = InvariantSection(
            kind=kind, title=title,
            short_statement=short, body=body, columns=columns,
            what_to_do=what_to_do,
        )
        i = j

    return sections


def load_bundled_invariants(
    *, strip_jinja: bool = True,
) -> dict[str, InvariantSection]:
    """Read the bundled ``L1_Invariants.md`` from
    ``recon_gen.docs`` and return parsed sections.

    Single call site for the dashboard-side consumers (AA.C.3 +
    AA.C.5) -- they don't need to know where the doc lives.
    """
    md_text = (
        resources.files("recon_gen.docs")
        .joinpath("L1_Invariants.md")
        .read_text(encoding="utf-8")
    )
    return parse_l1_invariants(md_text, strip_jinja=strip_jinja)


def panel_markdown(section: InvariantSection) -> str:
    """Compose a sheet-bottom panel for an :class:`InvariantSection`.

    Returns a markdown string suitable for passing through
    ``rich_text.markdown(...)`` and into a ``SheetTextBox`` content
    block. The shape, top-to-bottom:

    1. Bold title (the section's human heading).
    2. The SHOULD-constraint as a blockquote (omitted for the
       descriptive Supersession Audit section).
    3. The body prose (with the ``**Columns:** ...`` line
       inline, since the column list is useful context for
       operators reading the panel).
    4. A bold ``Action.`` line carrying :attr:`what_to_do`.

    AA.C.3 wires one of these per L1 invariant sheet via
    ``apps/l1_dashboard/app.py``. The Today's Exceptions sheet
    composes its own intro panel (AA.C.3.e) rather than stacking
    seven of these.
    """
    parts = [f"**{section.title}**"]
    if section.short_statement:
        parts.append(f"> {section.short_statement}")
    if section.body:
        parts.append(section.body)
    if section.what_to_do:
        parts.append(f"**Action.** {section.what_to_do}")
    return "\n\n".join(parts)
