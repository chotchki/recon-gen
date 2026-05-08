"""Reportlab document template + page chrome helpers.

Generic reportlab plumbing extracted from ``cli/audit.py``: a
``BaseDocTemplate`` subclass that handles bookmarks + TOC entries +
``Page X of N`` page-count threading via a multiBuild-aware closure,
plus level-tagged ``Paragraph`` helpers and a themed footer factory.

Deliberately stays domain-agnostic: no audit-specific story builders
or content live here. ``cli/audit.py`` (and any future PDF-generating
artifact) imports these primitives and assembles its own story.
"""

from __future__ import annotations

from datetime import datetime

from quicksight_gen.common.provenance import (
    ProvenanceFingerprint,
    short_fingerprint_placeholder,
)


def bookmarked_h1(text: str, styles):  # type: ignore[no-untyped-def]: styles is reportlab StyleSheet1; returns Paragraph
    """Heading1 paragraph tagged for PDF outline + TOC at level 0.

    Used by every per-section heading the reader should be able to
    jump to from the bookmark sidebar or the TOC page.
    """
    from reportlab.platypus import Paragraph
    p = Paragraph(text, styles["Heading1"])
    p._bookmark_level = 0  # type: ignore[attr-defined]: reportlab Paragraph monkey-patch for bookmark generation
    return p


def bookmarked_h3(text: str, styles):  # type: ignore[no-untyped-def]: styles is reportlab StyleSheet1; returns Paragraph
    """Heading3 paragraph tagged for PDF outline + TOC at level 1."""
    from reportlab.platypus import Paragraph
    p = Paragraph(text, styles["Heading3"])
    p._bookmark_level = 1  # type: ignore[attr-defined]: reportlab Paragraph monkey-patch for bookmark generation
    return p


class BookmarkedDocTemplate:
    """``BaseDocTemplate`` proxy with bookmark + TOC + page-count support.

    Defined as a thin proxy via ``__new__``-style indirection so
    reportlab is only imported when the caller actually needs it (so
    the parent CLI loads cleanly without the PDF extra installed).

    Builds the PDF outline (left-sidebar nav) + feeds the
    ``TableOfContents`` flowable's notification stream from any
    flowable tagged with a ``_bookmark_level`` attribute (use
    ``bookmarked_h1`` / ``bookmarked_h3``). Also records the final
    page count after each ``multiBuild`` pass into the
    caller-provided ``total_pages_holder`` so the footer drawer can
    render "Page X of Y" without resorting to a NumberedCanvas
    (which breaks bookmark→page refs).
    """

    def __new__(  # type: ignore[no-untyped-def]: returns reportlab BaseDocTemplate, runtime-imported
        cls, *args, total_pages_holder: list | None = None, **kwargs,
    ):
        from reportlab.platypus import BaseDocTemplate

        class _Inner(BaseDocTemplate):
            def afterFlowable(self, flowable) -> None:  # type: ignore[no-untyped-def]: reportlab Flowable callback override
                level = getattr(flowable, "_bookmark_level", None)
                if level is None:
                    return
                text = flowable.getPlainText()
                key = f"qsg-bm-{id(flowable)}"
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(text, key, level=level)
                self.notify("TOCEntry", (level, text, self.page, key))

            def _allSatisfied(self):  # type: ignore[no-untyped-def]: reportlab BaseDocTemplate hook override
                # multiBuild calls this after each pass to decide
                # whether to run another. We piggyback to publish the
                # just-stabilized page count into the holder so the
                # footer drawer's "Page X of Y" picks it up on the
                # next pass.
                if total_pages_holder is not None:
                    total_pages_holder[0] = self.page
                return super()._allSatisfied()

        return _Inner(*args, **kwargs)


def make_footer_drawer(
    theme,  # type: ignore[no-untyped-def]: ThemePreset, untyped to avoid runtime import in render fn
    *,
    version: str,
    generated_at: datetime,
    total_pages_holder: list,
    provenance: ProvenanceFingerprint | None,
):  # type: ignore[no-untyped-def]: returns inner closure (reportlab page-template callable)
    """Build a per-page footer drawer with U.6 chrome.

    "Page X of Y" needs the FINAL page count, which only stabilizes
    at the end of a ``multiBuild`` pass. We piggyback on the fact
    that ``multiBuild`` runs the build at least twice (once for
    ``TableOfContents`` to collect entries, once to render the
    resolved TOC): pass 1's footer renders "Page X of ?" while
    ``total_pages_holder[0] == 0``; ``BookmarkedDocTemplate``
    overrides ``_allSatisfied`` to record ``self.page`` (now
    stable) into the holder; pass 2's footer reads it back as
    "Page X of N".

    Tried the standard NumberedCanvas pattern (defer ``showPage``,
    replay buffered state in ``save``) — it broke every PDF
    bookmark, because ``dict(self.__dict__)`` snapshots include
    ``_destinations`` / page-ref state, and restoring an earlier
    snapshot at save time overwrote the accumulated bookmark→page
    refs with the LAST state's, collapsing every outline entry to
    page 1. The two-pass closure here keeps reportlab's normal
    page-template chrome flow untouched, so bookmarks resolve
    correctly through the standard machinery.

    Per U.7: when provenance is computed, the footer renders the
    real short fingerprint (first 8 hex of composite SHA256); when
    not, the ``pending`` placeholder.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch

    secondary_fg = colors.HexColor(theme.secondary_fg)
    timestamp = generated_at.strftime("%Y-%m-%d %H:%M")
    short_fp = (
        provenance.short
        if provenance is not None
        else short_fingerprint_placeholder()
    )

    def _draw_footer(canvas, doc) -> None:  # type: ignore[no-untyped-def]: reportlab Canvas + Document signature
        canvas.saveState()
        width, _ = letter
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(secondary_fg)
        left = 0.75 * inch
        right = width - 0.75 * inch
        baseline = 0.5 * inch
        canvas.drawString(
            left, baseline,
            f"quicksight-gen v{version}  ·  Generated {timestamp}",
        )
        total = total_pages_holder[0]
        of_total = f" of {total}" if total else ""
        canvas.drawRightString(
            right, baseline,
            f"Page {doc.page}{of_total}  ·  "
            f"Provenance: {short_fp}",
        )
        canvas.restoreState()

    return _draw_footer
