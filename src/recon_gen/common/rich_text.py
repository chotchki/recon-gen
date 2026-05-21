"""Compose rich-text XML for QuickSight ``SheetTextBox.Content``.

QuickSight accepts a small XML dialect inside a single ``<text-box>`` root
(undocumented — full set confirmed by round-tripping a UI-authored text box
that exercised every formatting button via ``describe-analysis-definition``):

* ``<inline ...>text</inline>`` — styled run; attrs ``font-size="36px"``,
  ``color="#hex"``, ``background-color="#hex"`` (highlight), ``font-family="Name"``
* ``<b>`` / ``<i>`` / ``<s>`` / ``<u>`` — bold / italic / strikethrough /
  underline (bare HTML tags, NOT ``<inline>`` attrs)
* ``<block align="center">text</block>`` — paragraph alignment
  (``center`` / ``right``; left is the default, emitted with no block)
* ``<br/>`` — explicit line break
* ``<ul><li class="ql-indent-0">item</li></ul>`` — bulleted list
  (the ``ql-indent-0`` class is required for top-level bullets)
* ``<a href="..." target="_self">Link</a>`` — hyperlink
* ``<expression>${pName}</expression>`` — live parameter-value injection
* Body text between tags must be XML-escaped; ``&nbsp;`` survives

Theme tokens aren't supported by the parser, so colors are resolved to hex
at generate-time and interpolated here by the caller.

Authoring helpers:

* ``body(text)`` — single-line plain text, XML-escaped. Use for one-shot
  prose with no paragraph breaks or links.
* ``markdown(text)`` — multi-paragraph prose with optional inline
  ``[text](url)`` links. ``\\n\\n`` paragraph breaks become ``<br/><br/>``,
  a lone ``\\n`` is a CommonMark soft break that collapses to a single
  space, ``[text](url)`` becomes a clickable ``<a href="...">``. Use
  whenever the source string is L2-YAML-supplied description prose
  (which is markdown-shaped by convention).
* ``markdown_inline(text)`` — same XML-escape + ``[text](url)`` link
  handling, but ALL newlines collapse to a single space (no ``<br/>``).
  Use inside contexts where ``<br/>`` is not a valid child — most
  notably ``<li>``: QS's XML parser rejects ``<br/>`` as a child of
  ``<li>`` with ``Element 'li' cannot have 'br' elements as children``.

``bullets()`` is defensive: it routes items through ``markdown()``
then strips any ``<br/>`` from each item with a ``UserWarning``
showing the original input. Authors can choose ``markdown_inline``
explicitly when they need a guaranteed-no-``<br/>`` rendering of a
single string outside a bullet context.
"""

from __future__ import annotations

import re
import warnings
from typing import Iterable
from xml.sax.saxutils import escape as _xml_escape


BR = "<br/>"


# Match every ``<br>``-shape (self-closing, slash-self-closing, with or
# without whitespace inside the tag) so the bullets() defensive strip
# catches them regardless of which authoring path produced them.
_BR_TAG = re.compile(r"<br\s*/?\s*>", re.IGNORECASE)


# Inline markdown link: ``[text](url)``. Captures the link text + the
# href separately so each can be XML-escaped before re-interpolation.
# Non-greedy on text + href so adjacent links don't collapse into one.
_MARKDOWN_LINK = re.compile(r"\[([^\]]+?)\]\(([^)]+?)\)")

# AO.R.3 — inline emphasis markers parsed by ``markdown()`` /
# ``markdown_inline()`` into the QS rich-text vocab BOTH renderers honor
# (``render.py`` maps ``<b>`` → bold and ``<inline font-family>`` → a
# ``<span style="font-family:…">``). Before AO.R.3 ``**bold**`` / ```code```
# / ``- bullets`` passed through as literal text, so dashboard panels
# authored with them (``handbook/invariants.py`` + ``l2ft_exceptions.py``
# + the L1 Today's-Exceptions panel) rendered raw markdown in both QS and
# App2. Non-greedy bold so adjacent ``**a** **b**`` stay separate.
_BOLD_SPAN = re.compile(r"\*\*(.+?)\*\*")
_CODE_SPAN = re.compile(r"`([^`]+)`")
# A bullet line: ``- item`` or ``* item`` (leading whitespace already
# stripped by the caller). Group 1 is the item text.
_BULLET_LINE = re.compile(r"^[-*]\s+(.*)$")
# QS-safe monospace family for ```code``` spans (QS has no dedicated code
# tag; a monospace ``font-family`` is the closest portable rendering).
_CODE_FONT_FAMILY = "Courier New"


def body(text: str) -> str:
    """Plain body text — XML-escaped, no styling."""
    return _xml_escape(text)


def inline(
    text: str,
    *,
    font_size: str | None = None,
    color: str | None = None,
) -> str:
    """Styled inline run. ``font_size`` like ``"24px"``; ``color`` like ``"#2E5090"``."""
    attrs: list[str] = []
    if font_size:
        attrs.append(f'font-size="{font_size}"')
    if color:
        attrs.append(f'color="{color}"')
    attr_str = (" " + " ".join(attrs)) if attrs else ""
    return f"<inline{attr_str}>{_xml_escape(text)}</inline>"


def heading(text: str, color: str | None = None) -> str:
    """Top-level heading (32px)."""
    return inline(text, font_size="32px", color=color)


def subheading(text: str, color: str | None = None) -> str:
    """Section subheading (20px)."""
    return inline(text, font_size="20px", color=color)


def bullets(items: Iterable[str]) -> str:
    """Bulleted list at indent level 0.

    Each item is processed through :func:`markdown` (so inline
    ``[text](url)`` links render as clickable anchors) and then
    defensively stripped of any ``<br/>`` tags — QS's XML parser
    rejects ``<br/>`` as a child of ``<li>`` with
    ``Element 'li' cannot have 'br' elements as children``. A
    ``\\n\\n`` paragraph break still reflows to ``<br/><br/>`` via
    :func:`markdown` and would break ``CreateAnalysis`` inside an
    ``<li>`` (the v8.5.4 → v8.5.8 regression on the L1 Drift sheet),
    so it is stripped here. (A lone ``\\n`` is a soft break that
    :func:`markdown` collapses to a space upstream, so it never
    reaches this strip.)

    Any stripped ``<br/>`` raises a ``UserWarning`` showing the
    original item so authors can clean the source string when the
    line break was actually intended (e.g. break the item into two).
    """
    lis_parts: list[str] = []
    for item in items:
        rendered = markdown(item)
        if _BR_TAG.search(rendered):
            stripped = _BR_TAG.sub(" ", rendered)
            warnings.warn(
                f"bullets(): stripped <br/> from list item — QS "
                f"rejects <br/> as a child of <li>. Original item: "
                f"{item!r}. Consider splitting into two bullets or "
                f"removing the embedded line break in the source.",
                UserWarning,
                stacklevel=2,
            )
            rendered = stripped
        lis_parts.append(f'<li class="ql-indent-0">{rendered}</li>')
    return f"<ul>{''.join(lis_parts)}</ul>"


def bullets_raw(items: Iterable[str]) -> str:
    """Bulleted list whose items are pre-composed XML (so inline styling works inside bullets)."""
    lis = "".join(f'<li class="ql-indent-0">{item}</li>' for item in items)
    return f"<ul>{lis}</ul>"


def link(text: str, href: str) -> str:
    """Hyperlink opening in the same tab."""
    return f'<a href="{_xml_escape(href)}" target="_self">{_xml_escape(text)}</a>'


def bold(text: str) -> str:
    """Bold run — ``<b>`` (XML-escaped body)."""
    return f"<b>{_xml_escape(text)}</b>"


def code(text: str) -> str:
    """Inline monospace run. QS has no code tag, so this is an
    ``<inline font-family>`` (``render.py`` maps it to a
    ``<span style="font-family:…">`` in App2)."""
    return f'<inline font-family="{_CODE_FONT_FAMILY}">{_xml_escape(text)}</inline>'


def _emphasis(escaped: str) -> str:
    """Apply ``**bold**`` + ```code``` to an already-XML-escaped string.

    Bold is resolved first so a ```code``` span sitting inside ``**…**``
    still resolves on the second pass. The ``**`` / `` ` `` markers
    survive ``_xml_escape`` (it only touches ``< > &``), so this runs
    safely on escaped text without double-escaping the tags it inserts.
    """
    escaped = _BOLD_SPAN.sub(r"<b>\1</b>", escaped)
    escaped = _CODE_SPAN.sub(
        lambda m: f'<inline font-family="{_CODE_FONT_FAMILY}">{m.group(1)}</inline>',
        escaped,
    )
    return escaped


def _inline_md(text: str) -> str:
    """Inline markdown → rich-text XML: ``[text](url)`` links +
    ``**bold**`` + ```code```, XML-escaped. No block structure, no
    ``<br/>`` — safe inside an ``<li>`` or a single prose line."""
    parts: list[str] = []
    cursor = 0
    for match in _MARKDOWN_LINK.finditer(text):
        parts.append(_emphasis(_xml_escape(text[cursor:match.start()])))
        parts.append(link(match.group(1), match.group(2)))
        cursor = match.end()
    parts.append(_emphasis(_xml_escape(text[cursor:])))
    return "".join(parts)


def markdown(text: str) -> str:
    """Block + inline markdown → QuickSight rich-text XML.

    Block structure (paragraphs split on a blank line, ``\\n\\n+``):

    - A block whose first non-blank line is a bullet (``- `` / ``* ``)
      becomes a ``<ul><li class="ql-indent-0">…</li></ul>`` list.
      Non-bullet lines inside the block are continuations of the
      preceding item (soft-wrapped source reflows into one item).
      Consecutive bullet blocks (authors who separate items with blank
      lines) merge into one ``<ul>``.
    - Any other block is a prose paragraph: a lone ``\\n`` is a
      CommonMark soft break (→ a single space), and blocks join with
      ``<br/><br/>``.

    Inline (within every block): ``[text](url)`` → clickable ``<a>``,
    ``**bold**`` → ``<b>``, ```code``` → monospace ``<inline>``; the
    remaining text is XML-escaped.

    Use whenever the input is L2-YAML-supplied prose or any
    markdown-shaped string. ``body()`` is for plain single-line text
    only — feeding multi-paragraph / link- / emphasis-bearing strings to
    ``body()`` produces unrendered markdown in QuickSight (the v8.4.0
    footgun this helper closes; AO.R.3 added bold/code/bullet parsing so
    dashboard panels stop rendering literal ``**`` / ``- `` markers).
    """
    if not text:
        return ""
    rendered: list[str] = []
    for block in re.split(r"\n{2,}", text):
        nonblank = [ln for ln in block.split("\n") if ln.strip()]
        if not nonblank:
            continue
        if _BULLET_LINE.match(nonblank[0].strip()):
            items: list[str] = []
            for ln in nonblank:
                m = _BULLET_LINE.match(ln.strip())
                if m:
                    items.append(m.group(1).strip())
                elif items:  # continuation of the current item
                    items[-1] = f"{items[-1]} {ln.strip()}".strip()
            lis = "".join(
                f'<li class="ql-indent-0">{_inline_md(it)}</li>' for it in items
            )
            rendered.append(f"<ul>{lis}</ul>")
        elif nonblank[0].lstrip().startswith(">"):
            # Blockquote — QS has no <blockquote> tag, so render the
            # quoted lines as one italic run (the closest portable
            # rendering; used by the L1 invariant panels' SHOULD line).
            quoted = " ".join(
                re.sub(r"^>\s?", "", ln.strip()) for ln in nonblank
            )
            rendered.append(f"<i>{_inline_md(quoted)}</i>")
        else:
            # Prose — lone \n is a CommonMark soft break (→ space).
            rendered.append(_inline_md(" ".join(ln.strip() for ln in nonblank)))
    out = (BR + BR).join(rendered)
    # Merge consecutive bullet blocks into a single list.
    return out.replace(f"</ul>{BR}{BR}<ul>", "")


def markdown_inline(text: str) -> str:
    """Single-line markdown with link handling but no ``<br/>``.

    Same transformations as :func:`markdown` for inline links and
    XML-escaping, but ANY whitespace run that contains a newline
    collapses to a single space. ``<br/>`` is never emitted. Use
    inside ``<li>`` (the QS XML parser rejects ``<br/>`` as an
    ``<li>`` child) or any other context where line breaks must
    not appear.

    Trailing / leading whitespace is stripped — useful when the
    input came from a YAML ``|`` block scalar (which always ends in
    ``\\n``). Strip happens after link substitution so a link sitting
    at the end of the input stays attached to the next part rather
    than getting whitespace-eaten.
    """
    parts: list[str] = []
    cursor = 0
    for match in _MARKDOWN_LINK.finditer(text):
        before = text[cursor:match.start()]
        parts.append(_emphasis(_escape_collapse_newlines(before)))
        parts.append(link(match.group(1), match.group(2)))
        cursor = match.end()
    parts.append(_emphasis(_escape_collapse_newlines(text[cursor:])))
    return "".join(parts).strip()


def _escape_collapse_newlines(text: str) -> str:
    """XML-escape ``text`` and collapse newline-bearing whitespace runs.

    Any whitespace run that contains at least one ``\\n`` collapses
    to a single space — that handles the YAML block-scalar case where
    line wrapping creates ``\\n`` between words AND the case of
    ``\\n\\n`` paragraph breaks (both reflow to a single space here,
    since ``<li>`` doesn't take ``<br/>``). Pure-space runs that
    don't contain a newline are left alone, so authored intra-line
    spacing survives.
    """
    escaped = _xml_escape(text)
    # \s* on either side captures leading/trailing space adjacent to
    # the newline run; the whole match collapses to a single space.
    return re.sub(r"[ \t]*\n+[ \t]*", " ", escaped)


def text_box(*parts: str) -> str:
    """Wrap parts in a ``<text-box>`` root.

    Auto-pads the interior with leading + trailing ``<br/>`` so the
    rendered text doesn't sit flush against the box's top / bottom
    edges. ``SheetTextBox`` itself has no padding/margin fields in
    the AWS API — interior breathing room only comes via the
    rich-text grammar inside ``Content``. Two ``<br/>`` per side
    matches what hand-authored QS UI text boxes emit when an editor
    hits Enter twice for spacing (a common pattern).

    Pass already-``<br/>``-padded ``parts`` if you want even more
    space; the auto-pad is additive.
    """
    return f"<text-box>{BR}{BR}{''.join(parts)}{BR}{BR}</text-box>"
