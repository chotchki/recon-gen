"""Unit tests for ``common/rich_text.py`` — XML composition helpers
for QuickSight ``SheetTextBox.Content``.

Pre-v8.4.0 the only break primitive was ``BR`` and the only authoring
helper was ``body()`` — multi-paragraph prose was a footgun (``\\n\\n``
in the input string survived as literal whitespace, since QS only
honors ``<br/>`` for breaks). v8.4.0 added ``markdown()`` which
handles paragraph + line breaks AND inline ``[text](url)`` links.
"""

from __future__ import annotations

from recon_gen.common import rich_text as rt


class TestBody:
    def test_xml_escapes(self) -> None:
        assert rt.body("a < b & c") == "a &lt; b &amp; c"

    def test_passthrough_for_safe_text(self) -> None:
        assert rt.body("hello world") == "hello world"


class TestInline:
    def test_no_attrs(self) -> None:
        assert rt.inline("hi") == "<inline>hi</inline>"

    def test_font_size(self) -> None:
        assert rt.inline("hi", font_size="24px") == '<inline font-size="24px">hi</inline>'

    def test_color(self) -> None:
        assert rt.inline("hi", color="#2E5090") == '<inline color="#2E5090">hi</inline>'

    def test_xml_escapes_body(self) -> None:
        assert "&lt;" in rt.inline("a < b")


class TestLink:
    def test_emits_href_and_text(self) -> None:
        assert rt.link("Click", "https://example.com") == (
            '<a href="https://example.com" target="_self">Click</a>'
        )

    def test_xml_escapes_both_text_and_href(self) -> None:
        out = rt.link("Q & A", "https://x.com/?a=1&b=2")
        assert "&amp;" in out
        # href escaping
        assert "?a=1&amp;b=2" in out
        # text escaping
        assert "Q &amp; A" in out


class TestMarkdown:
    """v8.4.0 — class fix for the rt.body() multi-paragraph footgun."""

    def test_single_line_no_breaks(self) -> None:
        assert rt.markdown("just one line") == "just one line"

    def test_paragraph_break_becomes_double_br(self) -> None:
        # Markdown convention: blank line between paragraphs.
        out = rt.markdown("first para\n\nsecond para")
        assert out == "first para<br/><br/>second para"

    def test_three_or_more_newlines_collapse_to_one_paragraph_break(self) -> None:
        # \n\n\n\n is still one paragraph break, not three line breaks.
        out = rt.markdown("a\n\n\n\nb")
        assert out == "a<br/><br/>b"

    def test_single_newline_becomes_a_space(self) -> None:
        # AH.2 — a lone \n is a CommonMark soft break = a single space,
        # NOT a <br/>. Source-readability line wrapping (YAML block
        # scalars, SPEC-doc prose) must reflow rather than hard-break
        # on the narrower self-hosted text box.
        out = rt.markdown("line one\nline two")
        assert out == "line one line two"

    def test_xml_escapes_body_text(self) -> None:
        assert rt.markdown("a < b & c") == "a &lt; b &amp; c"

    def test_inline_link_becomes_anchor(self) -> None:
        out = rt.markdown("see [the docs](https://example.com) for more")
        assert (
            out
            == 'see <a href="https://example.com" target="_self">the docs</a> for more'
        )

    def test_link_with_special_chars_in_url(self) -> None:
        # Query string ampersands in the URL must XML-escape inside the href.
        out = rt.markdown("[search](https://x.com/?a=1&b=2)")
        assert 'href="https://x.com/?a=1&amp;b=2"' in out

    def test_link_text_xml_escapes(self) -> None:
        out = rt.markdown("[Q & A](https://x.com)")
        assert ">Q &amp; A</a>" in out

    def test_multiple_links_in_one_line(self) -> None:
        out = rt.markdown("see [foo](https://foo.com) and [bar](https://bar.com)")
        assert out.count("<a ") == 2
        assert "foo</a>" in out
        assert "bar</a>" in out

    def test_link_inside_paragraph_break(self) -> None:
        out = rt.markdown("intro\n\nclick [here](https://x.com) please")
        assert out == (
            'intro<br/><br/>click <a href="https://x.com" '
            'target="_self">here</a> please'
        )

    def test_no_literal_double_newline_survives(self) -> None:
        # The footgun this helper closes: post-conversion text MUST
        # NOT contain literal ``\n\n`` anywhere.
        out = rt.markdown("a\n\nb\n\nc\n\nd")
        assert "\n\n" not in out

    def test_no_unconverted_markdown_link_survives(self) -> None:
        # Same: post-conversion text MUST NOT contain unconverted
        # markdown link syntax.
        out = rt.markdown("see [docs](https://x.com)")
        assert "[" not in out
        assert "](https" not in out

    def test_brackets_without_link_url_stay_as_text(self) -> None:
        # Plain text using [brackets] without a (url) should survive
        # unchanged (no false-positive conversion).
        out = rt.markdown("see [section 3] of the spec")
        assert "[section 3]" in out

    def test_empty_string(self) -> None:
        assert rt.markdown("") == ""


class TestTextBox:
    def test_wraps_parts_in_root_with_interior_padding(self) -> None:
        # v8.6.3 — auto-pads with leading + trailing ``<br/><br/>`` so
        # rendered text doesn't sit flush against the box edges.
        # SheetTextBox itself has no padding fields in the AWS API.
        assert rt.text_box("a", "b") == (
            "<text-box><br/><br/>ab<br/><br/></text-box>"
        )

    def test_empty_still_pads(self) -> None:
        # Empty content still pads — the box renders as visible
        # whitespace rather than collapsing to zero-height (which is
        # less useful as a layout placeholder).
        assert rt.text_box() == (
            "<text-box><br/><br/><br/><br/></text-box>"
        )


class TestBullets:
    def test_bullets_emit_ql_indent_class(self) -> None:
        out = rt.bullets(["one", "two"])
        assert out.count('class="ql-indent-0"') == 2

    def test_bullets_xml_escape(self) -> None:
        out = rt.bullets(["a < b"])
        assert "a &lt; b" in out

    def test_bullets_raw_does_not_escape(self) -> None:
        # bullets_raw is for pre-composed XML (e.g. inline-styled
        # bullets) — must NOT escape the items.
        out = rt.bullets_raw(['<inline color="#fff">styled</inline>'])
        assert '<inline color="#fff">styled</inline>' in out

    def test_bullets_render_inline_markdown_links(self) -> None:
        # v8.5.4 — inline ``[text](url)`` inside a bullet item must
        # become a clickable anchor. L1 dashboard's Drift sheet feeds
        # ``rt.bullets`` markdown-shaped L2 description strings; the
        # pre-fix path leaked literal ``[...](...)`` into the rendered
        # text box.
        out = rt.bullets(["see [the docs](https://x.com) for details"])
        assert (
            'see <a href="https://x.com" target="_self">the docs</a>'
            in out
        )
        # Sanity: the literal markdown syntax must NOT survive.
        assert "[the docs]" not in out
        assert "](https://x.com)" not in out

    def test_bullets_lone_newline_reflows_to_space(self) -> None:
        # AH.2 — a lone \n is a CommonMark soft break: markdown()
        # collapses it to a space upstream, so no <br/> ever reaches
        # the <li> and no warning fires. (A \n\n paragraph break still
        # produces <br/><br/> → stripped + warned; see the next test.)
        import warnings as _w

        with _w.catch_warnings(record=True) as caught:
            _w.simplefilter("always")
            out = rt.bullets(["line one\nline two"])
        assert "<br/>" not in out
        assert "line one line two" in out
        assert caught == []

    def test_bullets_strip_br_from_paragraph_break_and_warn(self) -> None:
        # ``\n\n`` produces ``<br/><br/>`` via markdown(); both get
        # stripped, one warning per item.
        import warnings as _w

        with _w.catch_warnings(record=True) as caught:
            _w.simplefilter("always")
            out = rt.bullets(["para one\n\npara two"])
        assert "<br/>" not in out
        assert "para one" in out and "para two" in out
        assert len(caught) == 1

    def test_bullets_no_warning_when_no_br_emitted(self) -> None:
        # Plain bullets — no newlines, no warning, exact pre-v8.5.8
        # output shape preserved.
        import warnings as _w

        with _w.catch_warnings(record=True) as caught:
            _w.simplefilter("always")
            out = rt.bullets(["alpha", "beta"])
        assert caught == []
        assert (
            out
            == '<ul><li class="ql-indent-0">alpha</li>'
            '<li class="ql-indent-0">beta</li></ul>'
        )

    def test_bullets_link_inside_item_with_newlines(self) -> None:
        # Combining the two v8.5.x fixes: a bullet item containing
        # both an inline ``[text](url)`` link AND embedded newlines
        # renders the link AND strips the resulting ``<br/>``.
        import warnings as _w

        with _w.catch_warnings():
            _w.simplefilter("ignore")
            out = rt.bullets(["see [docs](https://x.com)\nfor details"])
        assert (
            '<a href="https://x.com" target="_self">docs</a>'
            in out
        )
        assert "<br/>" not in out

    def test_bullets_one_warning_per_offending_item(self) -> None:
        # If multiple items contain a \n\n paragraph break (which
        # markdown() turns into <br/><br/>), each one gets its own
        # warning so the author can fix every offender in one pass.
        # (A lone \n is a soft break → space → no warning; see above.)
        import warnings as _w

        with _w.catch_warnings(record=True) as caught:
            _w.simplefilter("always")
            rt.bullets(["a\n\nb", "clean", "c\n\nd"])
        assert len(caught) == 2


class TestMarkdownInline:
    """v8.5.8 — strict-no-``<br/>`` variant for use inside ``<li>``
    (and other contexts where the QS XML parser rejects line
    breaks)."""

    def test_collapses_single_newline_to_space(self) -> None:
        assert rt.markdown_inline("a\nb") == "a b"

    def test_collapses_multiple_newlines_to_single_space(self) -> None:
        assert rt.markdown_inline("a\n\n\nb") == "a b"

    def test_strips_leading_trailing_whitespace(self) -> None:
        assert rt.markdown_inline("\n\nbody\n\n") == "body"

    def test_xml_escapes(self) -> None:
        assert rt.markdown_inline("a < b & c") == "a &lt; b &amp; c"

    def test_inline_link_becomes_anchor(self) -> None:
        out = rt.markdown_inline("see [docs](https://x.com) here")
        assert (
            out
            == 'see <a href="https://x.com" target="_self">docs</a> here'
        )

    def test_link_with_newlines_around_it(self) -> None:
        out = rt.markdown_inline("intro\n[click](https://x.com)\noutro")
        assert (
            out
            == 'intro <a href="https://x.com" target="_self">click</a> outro'
        )

    def test_never_emits_br(self) -> None:
        # The whole raison d'etre — the ``<br/>`` ban must be total.
        for inp in [
            "a\nb",
            "a\n\nb",
            "a\n\n\n\nb",
            "[link](https://x.com)\nthen text",
            "\n\nleading\n\nbody\n\ntrailing\n\n",
        ]:
            assert "<br/>" not in rt.markdown_inline(inp), inp

    def test_pure_spaces_preserved(self) -> None:
        # Authored intra-line spacing survives — only newline-bearing
        # whitespace runs collapse.
        assert rt.markdown_inline("a   b") == "a   b"

    def test_plain_bullets_unchanged(self) -> None:
        # Regression guard: items with no markdown shape behave
        # identically to the pre-v8.5.4 path.
        out = rt.bullets(["alpha", "beta", "gamma"])
        assert (
            out
            == '<ul><li class="ql-indent-0">alpha</li>'
            '<li class="ql-indent-0">beta</li>'
            '<li class="ql-indent-0">gamma</li></ul>'
        )


class TestBoldCodeHelpers:
    """AO.R.3 — bold / code primitives."""

    def test_bold(self) -> None:
        assert rt.bold("Drift") == "<b>Drift</b>"

    def test_bold_xml_escapes(self) -> None:
        assert rt.bold("a < b") == "<b>a &lt; b</b>"

    def test_code(self) -> None:
        assert rt.code("rail_name") == (
            '<inline font-family="Courier New">rail_name</inline>'
        )


class TestMarkdownEmphasisAndBullets:
    """AO.R.3 — markdown() parses ``**bold**`` / ```code``` / ``- bullets``
    into the QS rich-text vocab both renderers honor. Before this, panel
    content authored with these markers rendered the raw ``**`` / ``- ``
    in QS AND App2 (the operator-flagged L2FT bottom panel)."""

    def test_inline_bold(self) -> None:
        assert rt.markdown("a **bold** word") == "a <b>bold</b> word"

    def test_inline_code(self) -> None:
        assert rt.markdown("the `rail_name` column") == (
            'the <inline font-family="Courier New">rail_name</inline> column'
        )

    def test_no_raw_markers_survive(self) -> None:
        out = rt.markdown("**B.** the `c` and a [l](https://x.com)")
        assert "**" not in out
        assert "`" not in out
        assert "<b>B.</b>" in out
        assert '<a href="https://x.com"' in out

    def test_bullet_list(self) -> None:
        out = rt.markdown("- one\n- two")
        assert out == (
            '<ul><li class="ql-indent-0">one</li>'
            '<li class="ql-indent-0">two</li></ul>'
        )

    def test_bullet_star_marker(self) -> None:
        out = rt.markdown("* a\n* b")
        assert out.count('<li class="ql-indent-0">') == 2

    def test_bullet_continuation_line_joins_into_item(self) -> None:
        # A soft-wrapped bullet (item text spans two source lines) is one
        # <li>, not a dropped line — matches _TODAYS_EXCEPTIONS_PANEL's
        # wrapped bullets.
        out = rt.markdown("- first part\n  wrapped tail\n- second")
        assert out == (
            '<ul><li class="ql-indent-0">first part wrapped tail</li>'
            '<li class="ql-indent-0">second</li></ul>'
        )

    def test_blank_line_separated_bullets_merge_into_one_list(self) -> None:
        # panel_markdown joins each bullet with \n\n; they must still
        # render as ONE list, not N single-item lists with gaps.
        out = rt.markdown("- a\n\n- b\n\n- c")
        assert out.startswith("<ul>")
        assert out.endswith("</ul>")
        assert out.count("<ul>") == 1
        assert out.count('<li class="ql-indent-0">') == 3
        assert "<br/>" not in out

    def test_bold_inside_bullet(self) -> None:
        out = rt.markdown("- **Drift.** the sub-ledger drifted")
        assert out == (
            '<ul><li class="ql-indent-0"><b>Drift.</b> '
            "the sub-ledger drifted</li></ul>"
        )

    def test_prose_block_then_bullets(self) -> None:
        out = rt.markdown("Intro line.\n\n- a\n- b")
        assert out == (
            "Intro line.<br/><br/>"
            '<ul><li class="ql-indent-0">a</li>'
            '<li class="ql-indent-0">b</li></ul>'
        )

    def test_markdown_inline_parses_bold_and_code(self) -> None:
        assert rt.markdown_inline("**x** and `y`") == (
            '<b>x</b> and <inline font-family="Courier New">y</inline>'
        )

    def test_markdown_inline_never_bullets_or_br(self) -> None:
        # markdown_inline is for <li> / no-break contexts: it must NOT
        # introduce <ul>/<br/> even when the source looks list-shaped.
        out = rt.markdown_inline("- a\n- b")
        assert "<ul>" not in out
        assert "<br/>" not in out

    def test_blockquote_renders_italic(self) -> None:
        # QS has no <blockquote>; the L1 invariant panels' "> SHOULD"
        # line renders as italic prose (marker stripped).
        assert rt.markdown("> X SHOULD hold.") == "<i>X SHOULD hold.</i>"

    def test_blockquote_then_prose_block(self) -> None:
        out = rt.markdown("> quoted line\n\nbody para")
        assert out == "<i>quoted line</i><br/><br/>body para"
