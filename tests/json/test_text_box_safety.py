"""Class-level test: no text-box content carries unconverted markdown.

Closes the v8.4.0 footgun where ``rt.body(welcome_body)`` would XML-
escape multi-paragraph prose verbatim — ``\\n\\n`` paragraph breaks
survived as whitespace (QS only honors ``<br/>``), and inline
``[text](url)`` markdown links never became QuickSight ``<a>``
elements (they showed as literal bracket-paren syntax in the
rendered text box).

Walks the emitted analysis JSON for every shipped app, finds every
SheetTextBox.Content string, asserts:

1. No literal ``\\n\\n`` substring survives — paragraph breaks must
   be ``<br/><br/>``.
2. No ``[text](url)`` substring survives — markdown links must be
   converted to ``<a href="url">text</a>``.

Either failure means a ``rt.body(some_string)`` call site needs to
become ``rt.markdown(some_string)`` (or the input string needs to
not contain those constructs at all).
"""

from __future__ import annotations

import re
from typing import Any, Iterator

import pytest

from tests._test_helpers import make_test_config


_CFG = make_test_config()


# Same regex shape as ``common/rich_text.py::_MARKDOWN_LINK`` — must
# stay in sync. If the helper's regex changes, this one moves with it.
_UNCONVERTED_MARKDOWN_LINK = re.compile(r"\[([^\]]+?)\]\(([^)]+?)\)")


def _all_text_box_contents(emitted: Any) -> Iterator[tuple[str, str]]:
    """Walk the emitted analysis dict, yield ``(sheet_id, content)`` for
    every SheetTextBox.

    Uses the AWS API JSON shape (post-``to_aws_json`` /
    ``_strip_nones``): ``Definition.Sheets[].TextBoxes[].Content``.
    """
    definition = emitted.get("Definition", {})
    for sheet in definition.get("Sheets", []):
        sheet_id = sheet.get("SheetId", "<unknown>")
        for tb in sheet.get("TextBoxes") or []:
            yield sheet_id, tb.get("Content", "")


def _build_all_apps():
    """Build all 4 shipped apps + emit each analysis to the AWS shape.

    Yields ``(app_name, emitted_analysis_dict)`` tuples. Lazy import
    of each app's builder so a build failure in one app doesn't mask
    failures in others (each shows up as its own pytest collection
    error rather than a module-level ImportError).
    """
    from recon_gen.apps.l1_dashboard.app import build_l1_dashboard_app
    from recon_gen.apps.l2_flow_tracing.app import (
        build_l2_flow_tracing_app,
    )
    from recon_gen.apps.investigation.app import build_investigation_app
    from recon_gen.apps.executives.app import build_executives_app

    builders = [
        ("l1_dashboard", build_l1_dashboard_app),
        ("l2_flow_tracing", build_l2_flow_tracing_app),
        ("investigation", build_investigation_app),
        ("executives", build_executives_app),
    ]
    for name, build in builders:
        app = build(_CFG)
        emitted = app.emit_analysis().to_aws_json()
        yield name, emitted


@pytest.mark.parametrize("app_name,emitted", list(_build_all_apps()))
def test_no_unconverted_paragraph_break_in_text_box_content(
    app_name: str, emitted: Any,
) -> None:
    """Class regression: no ``\\n\\n`` (or longer run) survives in
    any rendered text box. Markdown convention paragraph breaks must
    become ``<br/><br/>`` via ``rt.markdown()``."""
    bad: list[str] = []
    for sheet_id, content in _all_text_box_contents(emitted):
        if "\n\n" in content:
            preview = content[: content.index("\n\n")][-40:]
            bad.append(
                f"  sheet={sheet_id!r}: content carries literal \\n\\n "
                f"after: ...{preview!r}"
            )
    assert not bad, (
        f"App {app_name!r} has text-box content with unconverted "
        f"paragraph breaks. Replace the ``rt.body(string)`` call site "
        f"with ``rt.markdown(string)`` (or strip the \\n\\n if not "
        f"actually a paragraph break):\n" + "\n".join(bad)
    )


@pytest.mark.parametrize("app_name,emitted", list(_build_all_apps()))
def test_no_unconverted_markdown_link_in_text_box_content(
    app_name: str, emitted: Any,
) -> None:
    """Class regression: no ``[text](url)`` survives in any rendered
    text box. Markdown links must become QuickSight ``<a>`` elements
    via ``rt.markdown()`` (or ``rt.link()`` for one-off explicit
    construction)."""
    bad: list[str] = []
    for sheet_id, content in _all_text_box_contents(emitted):
        match = _UNCONVERTED_MARKDOWN_LINK.search(content)
        if match:
            bad.append(
                f"  sheet={sheet_id!r}: content carries literal "
                f"{match.group(0)!r} (markdown link not converted)"
            )
    assert not bad, (
        f"App {app_name!r} has text-box content with unconverted "
        f"markdown links. Replace the ``rt.body(string)`` call site "
        f"with ``rt.markdown(string)`` so QS renders the link as "
        f"clickable:\n" + "\n".join(bad)
    )


# Unconverted ``**bold**`` markdown (AO.R.3). Opener must be followed by
# a non-space (real markdown bold), so a lone ``**`` or a ``**/*.py``
# glob doesn't false-positive. After ``rt.markdown()`` every bold span
# becomes ``<b>…</b>`` — a surviving ``**word**`` means a panel author
# fed markdown to ``rt.body()`` (or some path that skips the parser).
_UNCONVERTED_BOLD = re.compile(r"\*\*\S.*?\*\*")


@pytest.mark.parametrize("app_name,emitted", list(_build_all_apps()))
def test_no_unconverted_bold_in_text_box_content(
    app_name: str, emitted: Any,
) -> None:
    """Class regression (AO.R.3): no ``**bold**`` survives in any
    rendered text box. ``rt.markdown()`` converts it to ``<b>``; a
    surviving marker means raw markdown is reaching the panel (the
    operator-flagged L2FT/L1 panels rendered literal ``**`` before the
    parser learned bold/code/bullets)."""
    bad: list[str] = []
    for sheet_id, content in _all_text_box_contents(emitted):
        match = _UNCONVERTED_BOLD.search(content)
        if match:
            bad.append(
                f"  sheet={sheet_id!r}: content carries literal "
                f"{match.group(0)!r} (markdown bold not converted)"
            )
    assert not bad, (
        f"App {app_name!r} has text-box content with unconverted "
        f"``**bold**`` markers. Route the string through ``rt.markdown()`` "
        f"(which now parses bold/code/bullets) instead of ``rt.body()``:\n"
        + "\n".join(bad)
    )


# ``<li ...>...</li>`` block, captured non-greedily so adjacent items
# don't merge into one match. ``re.DOTALL`` so any line breaks inside
# the item (the failure mode we're hunting) are visible to the
# ``<br/>`` substring check.
_LI_BLOCK = re.compile(r"<li\b[^>]*>(.*?)</li>", re.DOTALL)


@pytest.mark.parametrize("app_name,emitted", list(_build_all_apps()))
def test_no_br_inside_li_in_text_box_content(
    app_name: str, emitted: Any,
) -> None:
    """Class regression: no ``<br/>`` may appear inside an ``<li>``.

    QuickSight's text-box XML parser rejects this with
    ``Element 'li' cannot have 'br' elements as children`` at
    ``CreateAnalysis`` time — silent up to that point (the JSON
    validates locally, the dataset validates locally). The bug
    surfaced in v8.5.4 once ``rt.bullets()`` started routing items
    through ``rt.markdown()``: L2 YAML ``description: |`` block
    scalars carry embedded ``\\n`` from human-readable wrapping,
    those reflowed to ``<br/>``, and ``CreateAnalysis`` died on the
    L1 Drift sheet's ``l1-drift-accounts`` text box.

    Fix: ``rt.bullets()`` calls ``rt.markdown_inline()`` per item
    (collapses newlines to spaces, no ``<br/>`` emitted). This test
    is the regression guard.
    """
    bad: list[str] = []
    for sheet_id, content in _all_text_box_contents(emitted):
        for li_match in _LI_BLOCK.finditer(content):
            inner = li_match.group(1)
            if "<br/>" in inner or "<br />" in inner or "<br>" in inner:
                # Snip surrounding context for the error message so
                # the failure message points at the offending item.
                bad.append(
                    f"  sheet={sheet_id!r}: <li> contains <br/>: "
                    f"{li_match.group(0)[:200]!r}"
                )
    assert not bad, (
        f"App {app_name!r} has text-box content with ``<br/>`` inside "
        f"``<li>`` — QuickSight's CreateAnalysis will reject this with "
        f"``Element 'li' cannot have 'br' elements as children``. "
        f"``rt.bullets()`` must use ``rt.markdown_inline()`` per item "
        f"(not ``rt.markdown()``). See ``common/rich_text.py`` and "
        f"``docs/reference/quicksight-quirks.md``:\n" + "\n".join(bad)
    )
