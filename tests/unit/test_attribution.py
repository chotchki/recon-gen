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
    Attribution,
    attribution_copyright_html,
    attribution_link_html,
    resolve_attribution,
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


# -- DZ.11 resolver: overlay an L2 Attribution onto the baked defaults ------


def test_resolve_none_is_the_baked_default_credit() -> None:
    r = resolve_attribution(None)
    assert (r.name, r.url, r.prefix, r.enabled) == (
        ATTRIBUTION_NAME, ATTRIBUTION_URL, ATTRIBUTION_PREFIX, True,
    )


def test_resolve_full_override_replaces_every_field() -> None:
    r = resolve_attribution(
        Attribution(name="Acme Recon", url="https://acme.example", prefix="Built by"),
    )
    assert (r.name, r.url, r.prefix, r.enabled) == (
        "Acme Recon", "https://acme.example", "Built by", True,
    )


def test_resolve_partial_override_keeps_defaults_for_omitted_fields() -> None:
    # Override only the URL — name + prefix fall back to the defaults.
    r = resolve_attribution(Attribution(url="mailto:ops@acme.example"))
    assert r.url == "mailto:ops@acme.example"
    assert r.name == ATTRIBUTION_NAME
    assert r.prefix == ATTRIBUTION_PREFIX


def test_resolve_blank_field_falls_back_to_default() -> None:
    # A whitespace/empty field (the Studio editor can submit one) resolves
    # to the default rather than rendering a blank link.
    r = resolve_attribution(Attribution(name="   ", url=""))
    assert r.name == ATTRIBUTION_NAME
    assert r.url == ATTRIBUTION_URL


def test_resolve_enabled_false_carries_through() -> None:
    r = resolve_attribution(Attribution(enabled=False))
    assert r.enabled is False


def test_copyright_html_is_empty_when_suppressed() -> None:
    # enabled=False → no copyright line in the Material footer.
    assert attribution_copyright_html(resolve_attribution(Attribution(enabled=False))) == ""


def test_link_and_copyright_html_escape_operator_supplied_values() -> None:
    # name/url come from the L2 yaml now — they must be escaped so a
    # stray ``&`` / ``<`` / ``"`` can't break the markup or inject.
    r = resolve_attribution(
        Attribution(name='A&B <x>', url='https://e.example/?a=1&b="2"'),
    )
    link = attribution_link_html(r)
    assert "A&amp;B &lt;x&gt;" in link
    assert "&amp;b=&quot;2&quot;" in link
    assert "<x>" not in link  # the raw angle brackets never survive
    cr = attribution_copyright_html(r)
    assert "A&amp;B &lt;x&gt;" in cr
