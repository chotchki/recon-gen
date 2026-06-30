"""Author attribution — the single source for the "Made by" credit.

The HTMX renderer (Studio + Dashboards page shell) and the mkdocs
handbook both carry a footer crediting the author. Name + URL live
here ONCE so the credit is white-label-able from a single seam: a
real-tenant deploy that needs neutral chrome overrides
``ATTRIBUTION_NAME`` / ``ATTRIBUTION_URL`` (or drops the footer at the
call site) in one place rather than hunting two renderers.

This module is dependency-free on purpose — both the HTMX shell
(``common/html/render.py``) and the mkdocs macro module
(``main.py``) import it, and it must stay importable without pulling
in Starlette / mkdocs.

Tailwind note: the footer's utility classes are NOT authored here.
``input.css``'s ``@source`` only scans ``common/html/**/*.py``, so the
class strings live in ``render.py`` (which IS scanned) and this module
exposes only the link markup + an ``link_class`` slot the caller fills
with already-scanned utilities.
"""

from __future__ import annotations

#: Author display name shown in the credit. Override to white-label.
ATTRIBUTION_NAME: str = "Christopher Hotchkiss"

#: Author site the credit links to. Override to white-label.
ATTRIBUTION_URL: str = "https://hotchkiss.io"

#: Lead-in text before the linked name. "Made by <name>".
ATTRIBUTION_PREFIX: str = "Made by"


def attribution_link_html(*, link_class: str = "") -> str:
    """The credited name as an external anchor — the single-source markup.

    ``link_class`` is dropped in verbatim as the anchor's ``class``;
    callers pass Tailwind utilities (which must be literals in a
    ``@source``-scanned file). Empty class is fine — mkdocs-material
    styles footer links itself, so the copyright path passes nothing.
    """
    cls = f' class="{link_class}"' if link_class else ""
    return (
        f'<a href="{ATTRIBUTION_URL}" target="_blank" rel="noopener"{cls}>'
        f"{ATTRIBUTION_NAME}</a>"
    )


def attribution_copyright_html() -> str:
    """"Made by <name>" for mkdocs-material's ``copyright`` config key.

    mkdocs-material renders ``copyright`` as raw HTML in the site
    footer, so the anchor passes through. No Tailwind classes — the
    Material theme owns footer link styling.
    """
    return f"{ATTRIBUTION_PREFIX} {attribution_link_html()}"
