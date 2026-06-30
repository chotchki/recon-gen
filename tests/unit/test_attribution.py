"""DZ.1 — the author-attribution single source.

These pin the shape both renderers consume: the linked-name markup and
the mkdocs ``copyright`` string. They key off the module constants
(``ATTRIBUTION_NAME`` / ``ATTRIBUTION_URL``) rather than literal
"Christopher Hotchkiss", so a white-label override flips the credit AND
these assertions together — the test follows the seam, it doesn't fight
it.
"""

from __future__ import annotations

from recon_gen.common.attribution import (
    ATTRIBUTION_NAME,
    ATTRIBUTION_PREFIX,
    ATTRIBUTION_URL,
    attribution_copyright_html,
    attribution_link_html,
)


def test_link_html_is_an_external_anchor_to_the_author_site() -> None:
    link = attribution_link_html()
    assert f'href="{ATTRIBUTION_URL}"' in link
    assert f">{ATTRIBUTION_NAME}</a>" in link
    # External link hygiene — new tab + no referrer/opener leak.
    assert 'target="_blank"' in link
    assert 'rel="noopener"' in link


def test_link_html_drops_caller_classes_in_verbatim() -> None:
    """The HTMX footer passes Tailwind utilities through ``link_class``;
    they must land as the anchor's ``class`` unescaped so the compiled
    stylesheet matches."""
    link = attribution_link_html(link_class="text-accent hover:underline")
    assert 'class="text-accent hover:underline"' in link


def test_link_html_omits_class_attr_when_empty() -> None:
    # mkdocs-material styles footer links itself — no stray empty class.
    assert "class=" not in attribution_link_html()


def test_copyright_html_is_prefix_plus_link() -> None:
    cr = attribution_copyright_html()
    assert cr.startswith(ATTRIBUTION_PREFIX)
    assert ATTRIBUTION_NAME in cr
    assert ATTRIBUTION_URL in cr
