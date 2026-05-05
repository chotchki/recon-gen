"""X.2.spike.1 — HTML renderer for tree ``Sheet`` objects.

Produces a complete HTML page from a ``Sheet`` node. Pure projection:
no data, no JS, no styling beyond plain semantic tags. The output is
inspectable in any browser; spike.2 layers HTMX + d3 hydration on top.

Visual rendering shape:

    <section data-visual-kind="<kind>" data-visual-id="<id>">
      <h2>{title}</h2>
      <p class="subtitle">{subtitle}</p>
      <div class="visual-data">
        <!-- data placeholder; spike.2 fills via hx-get / d3 hydrate -->
      </div>
    </section>

The ``data-visual-kind`` attribute is the d3-hydration hook for
spike.2: a single bootstrap script picks divs by ``[data-chart="..."]``
or by section's ``data-visual-kind`` and applies the right chart
renderer. Spike.1 just emits the structure; nothing reads it yet.
"""

from __future__ import annotations

import html
from typing import Any

from quicksight_gen.common.tree.structure import Sheet


_PAGE_SHELL = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
</head>
<body>
{body}
</body>
</html>
"""


def emit_html(sheet: Sheet) -> str:
    """Render a tree ``Sheet`` as a standalone HTML page.

    Spike.1 scope: sheet title + description + one ``<section>`` per
    visual with its title, subtitle, and a placeholder div for data.
    No interactivity, no charts, no filter controls (yet).

    Args:
        sheet: tree ``Sheet`` node. Caller passes the in-memory tree
            object directly — the renderer never touches disk. This
            is the X.4 future-proofing constraint (per-session in-
            memory tree must work).

    Returns:
        A complete, well-formed HTML document as a string. Title +
        body content are HTML-escaped at the leaf level.
    """
    body_parts: list[str] = [f"  <h1>{html.escape(sheet.title)}</h1>"]
    if sheet.description:
        body_parts.append(f"  <p>{html.escape(sheet.description)}</p>")
    for visual in sheet.visuals:
        body_parts.append(_render_visual(visual))
    return _PAGE_SHELL.format(
        title=html.escape(sheet.title),
        body="\n".join(body_parts),
    )


def _render_visual(visual: Any) -> str:
    """Render one visual as an HTML ``<section>``.

    Spike.1: title + optional subtitle + placeholder div tagged with
    the visual's class name (``data-visual-kind``) so spike.2 can
    target d3 hydration per kind without reflection.

    Visuals satisfy ``VisualLike`` (Protocol) — they all carry
    ``title`` and most carry ``subtitle``. Read attributes
    defensively via ``getattr`` so the renderer works against any
    future ``VisualLike`` subtype without per-kind branching here.
    """
    title = getattr(visual, "title", "(untitled)")
    subtitle = getattr(visual, "subtitle", None)
    kind = type(visual).__name__
    visual_id = str(getattr(visual, "visual_id", ""))

    parts: list[str] = []
    parts.append(
        f'  <section data-visual-kind="{html.escape(kind)}"'
        f' data-visual-id="{html.escape(visual_id)}">'
    )
    parts.append(f"    <h2>{html.escape(title)}</h2>")
    if subtitle:
        parts.append(
            f'    <p class="subtitle">{html.escape(subtitle)}</p>'
        )
    parts.append(
        '    <div class="visual-data">'
        '<!-- spike.2 fills this via hx-get + d3 hydrate -->'
        '</div>'
    )
    parts.append("  </section>")
    return "\n".join(parts)
