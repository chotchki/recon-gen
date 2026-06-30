"""Author attribution — the single source for the "Made by" credit.

The HTMX renderer (Studio + Dashboards page shell) and the mkdocs
handbook both carry a footer crediting the author. The name + URL live
here ONCE, as the baked DEFAULT, so a plain ``pip install recon-gen``
shows the credit out of the box. An L2 instance white-labels the credit
by declaring an inline ``attribution:`` block (next to ``theme:``) — see
``Attribution`` below; ``resolve_attribution`` overlays that block onto
these defaults, and ``enabled: false`` drops the footer entirely (the
neutral-chrome case).

This module is dependency-free on purpose. Both the HTMX shell
(``common/html/render.py``) and the mkdocs macro module (``main.py``)
import it, and it must stay importable without pulling in Starlette /
mkdocs. The ``Attribution`` override dataclass lives HERE rather than in
``common/l2/`` for the same reason — it keeps attribution a single home,
and ``common/l2/primitives.py`` importing ``Attribution`` from here is
one-directional (no l2 → attribution → l2 cycle, and no l2 import on the
dependency-free path).

Tailwind note: the footer's utility classes are NOT authored here.
``input.css``'s ``@source`` only scans ``common/html/**/*.py``, so the
class strings live in ``render.py`` (which IS scanned) and this module
exposes only the link markup + a ``link_class`` slot the caller fills
with already-scanned utilities.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

#: Author display name shown in the credit when an L2 omits its own.
ATTRIBUTION_NAME: str = "Christopher Hotchkiss"

#: Author site the credit links to when an L2 omits its own. Used as an
#: ``href`` verbatim (escaped at render) — a ``mailto:`` is equally valid.
ATTRIBUTION_URL: str = "https://hotchkiss.io"

#: Lead-in text before the linked name. "Made by <name>".
ATTRIBUTION_PREFIX: str = "Made by"


@dataclass(frozen=True, slots=True)
class Attribution:
    """An L2 instance's optional override of the author credit.

    Every field is optional. An omitted (or blank) ``name`` / ``url`` /
    ``prefix`` falls back to the module default, so an L2 can override
    just the URL and keep the rest. ``enabled=False`` suppresses the
    footer entirely — the white-label "no chrome" case. The shape mirrors
    a ``theme:`` block: declared inline in the L2 yaml, parsed by
    ``common/l2/loader.py``, round-tripped by ``common/l2/serializer.py``.
    """

    name: str | None = None
    url: str | None = None
    prefix: str | None = None
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class ResolvedAttribution:
    """A fully-resolved credit — defaults applied, ready to render.

    ``enabled=False`` means the caller drops the footer; ``name`` /
    ``url`` / ``prefix`` are always concrete (never blank) so the markup
    helpers don't have to re-check.
    """

    name: str
    url: str
    prefix: str
    enabled: bool


def resolve_attribution(override: Attribution | None) -> ResolvedAttribution:
    """Overlay an L2 ``Attribution`` onto the baked defaults.

    ``None`` (no L2 block) yields the default credit. A blank / omitted
    field falls back to its default; ``enabled=False`` carries through so
    the caller drops the footer. Pure — no I/O — so the live renderer and
    the mkdocs macro share one resolution path.
    """
    if override is None:
        return ResolvedAttribution(
            name=ATTRIBUTION_NAME,
            url=ATTRIBUTION_URL,
            prefix=ATTRIBUTION_PREFIX,
            enabled=True,
        )
    return ResolvedAttribution(
        name=(override.name or "").strip() or ATTRIBUTION_NAME,
        url=(override.url or "").strip() or ATTRIBUTION_URL,
        prefix=(override.prefix or "").strip() or ATTRIBUTION_PREFIX,
        enabled=override.enabled,
    )


def attribution_link_html(
    resolved: ResolvedAttribution | None = None, *, link_class: str = "",
) -> str:
    """The credited name as an external anchor — the single-source markup.

    Pass a ``ResolvedAttribution`` to honor an L2 override; omit it for
    the baked default. ``link_class`` is dropped in verbatim as the
    anchor's ``class`` (callers pass Tailwind utilities, which must be
    literals in a ``@source``-scanned file). The name + URL are
    HTML-escaped — they are operator-supplied via the L2 yaml now, not a
    trusted constant.
    """
    r = resolved if resolved is not None else resolve_attribution(None)
    cls = f' class="{link_class}"' if link_class else ""
    href = html.escape(r.url, quote=True)
    name = html.escape(r.name)
    return (
        f'<a href="{href}" target="_blank" rel="noopener"{cls}>'
        f"{name}</a>"
    )


def attribution_copyright_html(
    resolved: ResolvedAttribution | None = None,
) -> str:
    """"Made by <name>" for mkdocs-material's ``copyright`` config key.

    Returns ``""`` when the credit is suppressed (``enabled=False``) so
    the Material footer carries no copyright line. Otherwise the prefix
    is escaped and the anchor passes through as raw HTML (mkdocs-material
    renders ``copyright`` unescaped; the Material theme owns footer link
    styling, so no Tailwind classes here).
    """
    r = resolved if resolved is not None else resolve_attribution(None)
    if not r.enabled:
        return ""
    prefix = html.escape(r.prefix)
    return f"{prefix} {attribution_link_html(r)}"
