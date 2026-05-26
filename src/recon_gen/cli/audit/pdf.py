"""PDF renderers for the audit report.

reportlab story builders that take pre-queried dataclass instances
(``cli/audit/__init__.py`` populates them) + the resolved
``ThemePreset`` and assemble the page sequence: cover (with optional
logo + provenance block) → TOC → exec summary → per-invariant
sections → Daily Statement walks → sign-off.

Generic reportlab plumbing (``BookmarkedDocTemplate``,
``bookmarked_h1``/``h3``, ``make_footer_drawer``) lives in
``common/pdf/audit_chrome.py`` so this module stays focused on
audit-specific story content.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from recon_gen.common.as_of_frame import AsOfFrame
from recon_gen.common.intervals import DateInterval  # noqa: F401 — kept for type/test imports
from recon_gen.common.pdf.audit_chrome import (
    BookmarkedDocTemplate,
    bookmarked_h1,
    bookmarked_h3,
    make_footer_drawer,
)
from recon_gen.common.provenance import (
    ProvenanceFingerprint,
    l2_fingerprint_placeholder,
)


from recon_gen.cli.audit import (
    DailyStatementWalk,
    DriftViolation,
    ExecSummary,
    LimitBreachViolation,
    OverdraftViolation,
    StuckPendingViolation,
    StuckUnbundledViolation,
    SupersessionAuditData,
    _EXCEPTION_INVARIANTS,
    _format_age,
    _split_limit_breach_by_account_class,
    _split_overdraft_by_account_class,
    _split_stuck_pending_by_account_class,
    _split_stuck_unbundled_by_account_class,
)

if TYPE_CHECKING:
    from recon_gen.cli.audit import MatviewEvidence
    from recon_gen.common.l2.theme import ThemePreset


__all__ = [
    "_write_audit_pdf",
]


def _write_audit_pdf(
    path: Path,
    *,
    institution: str,
    frame: AsOfFrame,
    generated_at: datetime,
    exec_summary: ExecSummary | None,
    drift_rows: list[DriftViolation] | None,
    overdraft_rows: list[OverdraftViolation] | None,
    limit_breach_rows: list[LimitBreachViolation] | None,
    stuck_pending_rows: list[StuckPendingViolation] | None,
    stuck_unbundled_rows: list[StuckUnbundledViolation] | None,
    supersession_data: SupersessionAuditData | None,
    daily_statement_walks: list[DailyStatementWalk] | None,
    singleton_ids: set[str],
    theme: ThemePreset,
    version: str,
    l2_label: str,
    provenance: ProvenanceFingerprint | None,
    matview_evidence: list[MatviewEvidence] | None,
    l2_instance_path: str | None,
) -> None:
    """Render the audit report as a PDF.

    Page sequence: cover → table of contents → executive summary →
    per-invariant tables (Drift, Overdraft, Limit breach, Stuck
    pending, Stuck unbundled, Supersession audit) → per-account Daily
    Statement walks. Each per-invariant page paginates via LongTable;
    every page carries a footer with the provenance fingerprint
    placeholder (real hash lands in U.7).

    Uses ``BookmarkedDocTemplate.multiBuild`` (two-pass) so the
    ``TableOfContents`` flowable can pick up correct page numbers,
    and section headings emit both PDF outline entries (left-sidebar
    nav) and TOC entries via the ``afterFlowable`` hook.
    """
    # Imported lazily so the audit CLI loads even when the [audit]
    # extra isn't installed — only --execute paths need reportlab.
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        Frame,
        PageBreak,
        PageTemplate,
        Paragraph,
        Spacer,
    )
    from reportlab.platypus.tableofcontents import TableOfContents

    path.parent.mkdir(parents=True, exist_ok=True)
    # Mutable holder bridges multiBuild's two-pass rendering: pass 1's
    # _allSatisfied stamps the just-stabilized page count here; pass 2's
    # footer drawer reads it back as "Page X of N".
    total_pages_holder: list[int] = [0]
    # Provenance fingerprint embedded in PDF metadata (Subject) as a
    # JSON blob so ``audit verify`` can extract it from the PDF
    # without re-running the audit. When provenance is None
    # (skeleton mode, no DB), Subject stays empty.
    import json as _json
    subject_meta = (
        _json.dumps(provenance.to_dict(), separators=(",", ":"))
        if provenance is not None
        else ""
    )
    doc = BookmarkedDocTemplate(
        str(path),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.85 * inch,  # extra room for the page footer
        title=f"Recon Generator Audit Report — {institution}",
        subject=subject_meta,
        author=f"recon-gen v{version}",
        total_pages_holder=total_pages_holder,
    )
    footer_drawer = make_footer_drawer(
        theme,
        version=version,
        generated_at=generated_at,
        total_pages_holder=total_pages_holder,
        provenance=provenance,
    )
    main_frame = Frame(
        doc.leftMargin, doc.bottomMargin,
        doc.width, doc.height, id="normal",
    )
    doc.addPageTemplates([
        PageTemplate(id="main", frames=[main_frame], onPage=footer_drawer),
    ])
    styles = getSampleStyleSheet()
    institution_style = ParagraphStyle(
        "InstitutionName",
        parent=styles["Heading1"],
        fontSize=20,
        leading=24,
        spaceBefore=0,
        spaceAfter=12,
        textColor=HexColor(theme.primary_fg),
    )
    period_band_style = ParagraphStyle(
        "PeriodBand",
        parent=styles["BodyText"],
        fontSize=13,
        leading=18,
        spaceBefore=6,
        spaceAfter=6,
        textColor=HexColor(theme.primary_fg),
        backColor=HexColor(theme.link_tint),
        borderColor=HexColor(theme.accent),
        borderWidth=0.5,
        borderPadding=10,
    )
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle(
            "TOCHeading1", parent=styles["BodyText"],
            fontSize=12, leading=16, fontName="Helvetica-Bold",
            leftIndent=0, spaceAfter=4,
            textColor=HexColor(theme.primary_fg),
        ),
        ParagraphStyle(
            "TOCHeading2", parent=styles["BodyText"],
            fontSize=10, leading=14,
            leftIndent=18, spaceAfter=2,
            textColor=HexColor(theme.secondary_fg),
        ),
    ]
    start, end = frame.window.start, frame.window.end
    # Cover-page Title: bookmark at level 0 so the auditor can jump
    # back to the cover from anywhere via the sidebar nav, and so it
    # appears at the top of the rendered TOC. We attach
    # _bookmark_level directly rather than wrapping in
    # bookmarked_h1 because we want to preserve the Title style.
    cover_title = Paragraph(
        "Recon Generator Audit Report",
        styles["Title"],
    )
    cover_title._bookmark_level = 0  # type: ignore[attr-defined]: reportlab Paragraph monkey-patch for bookmark generation
    toc_heading = Paragraph("Table of Contents", styles["Heading1"])
    toc_heading._bookmark_level = 0  # type: ignore[attr-defined]: reportlab Paragraph monkey-patch for bookmark generation
    # Optional: institutional logo above the title when theme.logo
    # is a loadable absolute file path.
    logo_flowable = _cover_logo_flowable(theme)
    # reportlab.platypus has no PEP 561 stubs — every Flowable returns
    # Unknown. Using ``list[object]`` keeps the type checker quiet while
    # preserving the heterogeneous Flowable element semantics.
    story: list[object] = []
    if logo_flowable is not None:
        story.extend([logo_flowable, Spacer(1, 0.25 * inch)])
    story.extend([
        cover_title,
        Spacer(1, 0.2 * inch),
        Paragraph(institution, institution_style),
        Spacer(1, 0.1 * inch),
        Paragraph(
            f"<b>Reporting period:</b> {start.isoformat()} &ndash; "
            f"{end.isoformat()} (inclusive)",
            period_band_style,
        ),
        Spacer(1, 0.25 * inch),
        Paragraph(
            f"<b>Generated:</b> {generated_at.isoformat(timespec='seconds')}",
            styles["BodyText"],
        ),
        Spacer(1, 0.4 * inch),
        Paragraph(
            "This report covers the L1 reconciliation invariants &mdash; "
            "drift, overdraft, limit breach, stuck pending, stuck "
            "unbundled, supersession audit &mdash; for the period above. "
            "Sourced directly from the operator's database matviews; the "
            "per-source breakdown below + the page-footer fingerprint "
            "bind this report's contents to its inputs for "
            "reproducibility.",
            styles["BodyText"],
        ),
    ])
    story.extend(_provenance_block_story(
        styles, theme,
        version=version, l2_label=l2_label,
        provenance=provenance,
    ))
    story.extend([
        # Table of contents (own page, bookmarked at level 0 above so
        # the auditor can jump to it from the sidebar nav and so it
        # shows up in its own rendered list).
        PageBreak(),
        toc_heading,
        Spacer(1, 0.15 * inch),
        toc,
    ])
    story.extend(_executive_summary_story(
        exec_summary, styles, frame, theme,
    ))
    story.extend(_drift_story(drift_rows, styles, frame, theme))
    story.extend(_overdraft_story(
        overdraft_rows, styles, frame, singleton_ids, theme,
    ))
    story.extend(_limit_breach_story(
        limit_breach_rows, styles, frame, singleton_ids, theme,
    ))
    story.extend(_stuck_pending_story(
        stuck_pending_rows, styles, singleton_ids, theme,
    ))
    story.extend(_stuck_unbundled_story(
        stuck_unbundled_rows, styles, singleton_ids, theme,
    ))
    story.extend(_supersession_story(
        supersession_data, styles, frame, theme,
    ))
    story.extend(_daily_statement_walks_story(
        daily_statement_walks, styles, theme,
    ))
    # Mutable registry: each _SigFieldPlaceholder appends its
    # (name, page_idx, rect) here at draw time; pyHanko reads the
    # list post-multiBuild to drop empty signature widgets at the
    # exact spots the layout reserved.
    # Tuples are appended by _SigFieldPlaceholder.drawOn — (name, page_idx, box).
    signature_field_registry: list[tuple[str, int, tuple[float, float, float, float]]] = []
    story.extend(_signoff_story(
        styles, theme,
        institution=institution,
        frame=frame,
        generated_at=generated_at,
        version=version,
        l2_label=l2_label,
        provenance=provenance,
        signature_field_registry=signature_field_registry,
    ))
    story.extend(_appendix_story(
        styles, theme,
        version=version,
        l2_label=l2_label,
        l2_instance_path=l2_instance_path,
        provenance=provenance,
        matview_evidence=matview_evidence,
    ))
    # multiBuild = two-pass render so TableOfContents picks up the
    # final page numbers (pass 1 collects via the afterFlowable hook,
    # pass 2 renders the resolved TOC). The doc template's
    # _allSatisfied override stamps the final page count into
    # total_pages_holder between passes so pass 2's footer drawer
    # can render "Page X of N" (U.6).
    doc.multiBuild(story)  # type: ignore[arg-type]: story is list[object] for typing-quiet but elements are reportlab Flowable subclasses

    # U.7.c — embed the L2 YAML + verify-provenance.py recipe as PDF
    # file attachments (post-multiBuild, pre-signing). Verifiers
    # download byte-exact from the PDF reader's attachments panel:
    # the YAML to confirm l2_yaml_sha against the embedded
    # provenance, the script to recompute the composite hash
    # without retyping it from the rendered appendix page.
    if l2_instance_path is None:
        l2_attachment_name = "l2-default.yaml"
    else:
        l2_attachment_name = Path(l2_instance_path).name
    if provenance is not None:
        recipe_text = _build_verify_recipe_script(
            tx_hwm=str(provenance.transactions_hwm),
            bal_hwm=str(provenance.balances_hwm),
            code_id=provenance.code_identity,
        )
    else:
        recipe_text = _build_verify_recipe_script(
            tx_hwm="<pending>",
            bal_hwm="<pending>",
            code_id=f"v{version}",
        )
    _attach_files_to_pdf(
        path,
        attachments={
            l2_attachment_name: _read_l2_yaml_bytes(l2_instance_path),
            "verify-provenance.py": recipe_text.encode("utf-8"),
        },
    )
    # Two empty reviewer signature widgets at the layout-reserved
    # coordinates below the notes box. Runs before the system
    # signing step (which happens in audit_apply); the system sig's
    # byte range covers these definitions, so subsequent reviewers
    # sign INTO existing fields instead of appending new ones.
    _add_empty_signature_fields(
        path, fields=signature_field_registry,
    )


def _executive_summary_story(
    summary: ExecSummary | None,
    styles: Any,  # WHY: reportlab StyleSheet1 lacks PEP 561 stubs; runtime-imported in render fn
    frame: AsOfFrame,
    theme: ThemePreset,
) -> list[object]:  # WHY: reportlab Flowable list — runtime-imported to avoid hard reportlab dep
    """Platypus elements for the U.2 executive summary page.

    Caller appends to the doc story after the cover page. Renders a
    page break, heading, period context, and two tables (Volume +
    Exception counts). When summary is None, renders "—" cells and a
    notice — keeps the layout reviewable without a live DB.
    """
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    start, end = frame.window.start, frame.window.end
    elements: list[object] = [
        PageBreak(),
        bookmarked_h1("Executive Summary", styles),
        Paragraph(
            f"Reporting period: {start.isoformat()} &ndash; "
            f"{end.isoformat()} (inclusive)",
            styles["BodyText"],
        ),
        Spacer(1, 0.15 * inch),
    ]

    if summary is None:
        elements.append(
            Paragraph(
                "<i>Database not configured &mdash; totals shown as "
                "placeholders. Set <b>demo_database_url</b> in your "
                "config to populate.</i>",
                styles["BodyText"],
            ),
        )
        volume_data = [
            ["Metric", "Value"],
            ["Transactions (legs)", "—"],
            ["Transfers (logical events)", "—"],
            ["Dollar volume — gross", "—"],
            ["Dollar volume — net", "—"],
        ]
        exc_rows = [
            [f"{label}*" if date_col is None else label, "—"]
            for label, _, date_col in _EXCEPTION_INVARIANTS
        ]
        exc_rows.append(["Supersession", "—"])
        exception_data = [["Invariant", "Count"]] + exc_rows
    else:
        volume_data = [
            ["Metric", "Value"],
            ["Transactions (legs)", f"{summary.transactions_count:,}"],
            ["Transfers (logical events)", f"{summary.transfers_count:,}"],
            ["Dollar volume — gross", f"${summary.dollar_volume_gross:,.2f}"],
            ["Dollar volume — net", f"${summary.dollar_volume_net:,.2f}"],
        ]
        exception_data = [["Invariant", "Count"]] + [
            [label, f"{count:,}"]
            for label, count in summary.exception_counts
        ]

    table_style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(theme.primary_fg)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(theme.accent_fg)),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(theme.link_tint)),
        ("ALIGN", (1, 1), (1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ])
    col_widths = [3.5 * inch, 2.5 * inch]

    elements.extend([
        Spacer(1, 0.15 * inch),
        bookmarked_h3("Volume", styles),
        Spacer(1, 0.05 * inch),
        Table(volume_data, colWidths=col_widths, style=table_style),
        Spacer(1, 0.3 * inch),
        bookmarked_h3("Exception Counts", styles),
        Spacer(1, 0.05 * inch),
        Table(exception_data, colWidths=col_widths, style=table_style),
        Spacer(1, 0.1 * inch),
        Paragraph(
            "<i>* Current state &mdash; open as of report generation, "
            "regardless of when posted (matches the L1 dashboard "
            "convention for stuck-aging matviews).</i>",
            styles["BodyText"],
        ),
    ])
    return elements


def _drift_story(
    rows: list[DriftViolation] | None,
    styles: Any,  # WHY: reportlab StyleSheet1 lacks PEP 561 stubs; runtime-imported in render fn
    frame: AsOfFrame,
    theme: ThemePreset,
) -> list[object]:  # WHY: reportlab Flowable list — runtime-imported to avoid hard reportlab dep
    """Platypus elements for the U.3.a Drift violations page.

    LongTable auto-paginates with the header row repeated. None = no
    DB → placeholder notice. Empty list = DB healthy with zero
    drifts in period → good-news render. Non-empty = full table.
    """
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        LongTable,
        PageBreak,
        Paragraph,
        Spacer,
        TableStyle,
    )

    start, end = frame.window.start, frame.window.end
    elements: list[object] = [
        PageBreak(),
        bookmarked_h1("Drift Violations", styles),
        Paragraph(
            f"Reporting period: {start.isoformat()} &ndash; "
            f"{end.isoformat()} (inclusive).",
            styles["BodyText"],
        ),
        Paragraph(
            "<i>Per-account-day discrepancies between stored "
            "end-of-day balance and the balance computed from "
            "posted transactions.</i>",
            styles["BodyText"],
        ),
        Spacer(1, 0.15 * inch),
    ]

    if rows is None:
        elements.append(
            Paragraph(
                "<i>Database not configured &mdash; table not "
                "populated. Set <b>demo_database_url</b> in your "
                "config to query.</i>",
                styles["BodyText"],
            ),
        )
        return elements
    if not rows:
        elements.append(
            Paragraph(
                "<i>No drift detected for the period &mdash; "
                "books reconcile.</i>",
                styles["BodyText"],
            ),
        )
        return elements

    cell_style = ParagraphStyle(
        "DriftCell",
        parent=styles["BodyText"],
        fontSize=8,
        leading=10,
        spaceBefore=0,
        spaceAfter=0,
    )
    header: list[object] = [
        "Account ID",
        "Account name",
        "Role",
        "Day",
        "Stored",
        "Computed",
        "Drift",
    ]
    data: list[list[object]] = [header]
    for r in rows:
        data.append([
            Paragraph(r.account_id, cell_style),
            Paragraph(r.account_name, cell_style),
            Paragraph(r.account_role, cell_style),
            r.business_day.isoformat(),
            f"${r.stored_balance:,.2f}",
            f"${r.computed_balance:,.2f}",
            f"${r.drift:,.2f}",
        ])

    table_style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(theme.primary_fg)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(theme.accent_fg)),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(theme.link_tint)),
        # Right-align numeric columns (Day, Stored, Computed, Drift).
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
            colors.white, colors.HexColor(theme.secondary_bg),
        ]),
    ])
    col_widths = [
        1.15 * inch,  # Account ID
        1.15 * inch,  # Account name
        1.05 * inch,  # Role  (fits "CustomerDDA" / "ConcentrationMaster")
        0.8 * inch,   # Day
        0.95 * inch,  # Stored
        0.95 * inch,  # Computed
        0.9 * inch,   # Drift
    ]
    elements.append(
        LongTable(
            data, colWidths=col_widths, style=table_style, repeatRows=1,
        ),
    )
    return elements


def _overdraft_story(
    rows: list[OverdraftViolation] | None,
    styles: Any,  # WHY: reportlab StyleSheet1 lacks PEP 561 stubs; runtime-imported in render fn
    frame: AsOfFrame,
    singleton_ids: set[str],
    theme: ThemePreset,
) -> list[object]:  # WHY: reportlab Flowable list — runtime-imported to avoid hard reportlab dep
    """Platypus elements for the U.3.b Overdraft violations page.

    Renders up to TWO sub-tables:
      - Parent accounts (L2 ``Account`` singletons): per-row detail
        — each occurrence of a parent itself going negative is a
        systemic event worth surfacing individually.
      - Child accounts (template-materialized): grouped by parent
        role; one row per parent role with distinct-children-negative
        + summed-peak-negative.
    Empty sub-tables are omitted; the section header still renders.
    """
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        LongTable,
        PageBreak,
        Paragraph,
        Spacer,
        TableStyle,
    )

    start, end = frame.window.start, frame.window.end
    elements: list[object] = [
        PageBreak(),
        bookmarked_h1("Overdraft Violations", styles),
        Paragraph(
            f"Reporting period: {start.isoformat()} &ndash; "
            f"{end.isoformat()} (inclusive).",
            styles["BodyText"],
        ),
        Paragraph(
            "<i>Account-days where the stored end-of-day balance "
            "went negative. Parent accounts (L2 singletons &mdash; "
            "GL clearing, concentration, ZBA master) shown per-row "
            "because a parent itself going negative is systemic. "
            "Child accounts (templated, e.g. customer DDAs, ZBA "
            "sub-accounts) roll up by parent role.</i>",
            styles["BodyText"],
        ),
        Spacer(1, 0.15 * inch),
    ]

    if rows is None:
        elements.append(
            Paragraph(
                "<i>Database not configured &mdash; table not "
                "populated. Set <b>demo_database_url</b> in your "
                "config to query.</i>",
                styles["BodyText"],
            ),
        )
        return elements
    if not rows:
        elements.append(
            Paragraph(
                "<i>No overdrafts detected for the period.</i>",
                styles["BodyText"],
            ),
        )
        return elements

    parent_rows, child_groups = _split_overdraft_by_account_class(
        rows, singleton_ids,
    )
    cell_style = ParagraphStyle(
        "OverdraftCell",
        parent=styles["BodyText"],
        fontSize=8,
        leading=10,
        spaceBefore=0,
        spaceAfter=0,
    )
    base_table_style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(theme.primary_fg)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(theme.accent_fg)),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(theme.link_tint)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
            colors.white, colors.HexColor(theme.secondary_bg),
        ]),
    ])

    if parent_rows:
        elements.extend([
            bookmarked_h3("Parent Accounts (Per-Row Detail)", styles),
            Spacer(1, 0.05 * inch),
        ])
        detail_data: list[list[object]] = [
            ["Account ID", "Account name", "Role", "Day", "Stored balance"],
        ]
        for r in parent_rows:
            detail_data.append([
                Paragraph(r.account_id, cell_style),
                Paragraph(r.account_name, cell_style),
                Paragraph(r.account_role, cell_style),
                r.business_day.isoformat(),
                f"${r.stored_balance:,.2f}",
            ])
        detail_style = TableStyle(
            base_table_style.getCommands() + [
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
            ],
        )
        elements.append(LongTable(
            detail_data,
            colWidths=[1.6 * inch, 1.5 * inch, 1.5 * inch,
                       0.8 * inch, 1.5 * inch],
            style=detail_style, repeatRows=1,
        ))
        elements.append(Spacer(1, 0.25 * inch))

    if child_groups:
        elements.extend([
            bookmarked_h3(
                "Child Accounts Grouped by Parent Role", styles,
            ),
            Spacer(1, 0.05 * inch),
        ])
        group_data: list[list[object]] = [
            ["Parent role", "Children negative", "Total peak negative"],
        ]
        for s in child_groups:
            group_data.append([
                Paragraph(s.parent_role, cell_style),
                f"{s.distinct_children_negative}",
                f"${s.total_peak_negative:,.2f}",
            ])
        group_style = TableStyle(
            base_table_style.getCommands() + [
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ],
        )
        elements.append(LongTable(
            group_data,
            colWidths=[3.0 * inch, 1.6 * inch, 2.3 * inch],
            style=group_style, repeatRows=1,
        ))
    return elements


def _limit_breach_story(
    rows: list[LimitBreachViolation] | None,
    styles: Any,  # WHY: reportlab StyleSheet1 lacks PEP 561 stubs; runtime-imported in render fn
    frame: AsOfFrame,
    singleton_ids: set[str],
    theme: ThemePreset,
) -> list[object]:  # WHY: reportlab Flowable list — runtime-imported to avoid hard reportlab dep
    """Platypus elements for the U.3.c Limit breach violations page.

    Same parent-vs-child split as Overdraft. Children grouped by
    (parent_role, rail_name) since the LimitSchedule cap is
    keyed on that pair. Parent table carries 8 columns (account,
    role, day, rail_name, outbound, cap, overshoot); child
    summary 4 columns (parent_role, rail_name, count, total
    overshoot).
    """
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        LongTable,
        PageBreak,
        Paragraph,
        Spacer,
        TableStyle,
    )

    start, end = frame.window.start, frame.window.end
    elements: list[object] = [
        PageBreak(),
        bookmarked_h1("Limit Breach Violations", styles),
        Paragraph(
            f"Reporting period: {start.isoformat()} &ndash; "
            f"{end.isoformat()} (inclusive).",
            styles["BodyText"],
        ),
        Paragraph(
            "<i>Account-day-rail_name cells where cumulative "
            "outbound exceeded the L2-configured cap. Parent accounts "
            "shown per-row; child accounts grouped by (parent role, "
            "transfer type) &mdash; the LimitSchedule key shape.</i>",
            styles["BodyText"],
        ),
        Spacer(1, 0.15 * inch),
    ]

    if rows is None:
        elements.append(
            Paragraph(
                "<i>Database not configured &mdash; table not "
                "populated. Set <b>demo_database_url</b> in your "
                "config to query.</i>",
                styles["BodyText"],
            ),
        )
        return elements
    if not rows:
        elements.append(
            Paragraph(
                "<i>No limit breaches detected for the period.</i>",
                styles["BodyText"],
            ),
        )
        return elements

    parent_rows, child_groups = _split_limit_breach_by_account_class(
        rows, singleton_ids,
    )
    cell_style = ParagraphStyle(
        "LimitBreachCell",
        parent=styles["BodyText"],
        fontSize=8,
        leading=10,
        spaceBefore=0,
        spaceAfter=0,
    )
    base_table_style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(theme.primary_fg)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(theme.accent_fg)),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(theme.link_tint)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
            colors.white, colors.HexColor(theme.secondary_bg),
        ]),
    ])

    if parent_rows:
        elements.extend([
            bookmarked_h3("Parent Accounts (Per-Row Detail)", styles),
            Spacer(1, 0.05 * inch),
        ])
        detail_data: list[list[object]] = [
            ["Account ID", "Account name", "Role", "Day",
             "Transfer type", "Direction", "Flow", "Cap", "Overshoot"],
        ]
        for r in parent_rows:
            detail_data.append([
                Paragraph(r.account_id, cell_style),
                Paragraph(r.account_name, cell_style),
                Paragraph(r.account_role, cell_style),
                r.business_day.isoformat(),
                Paragraph(r.rail_name, cell_style),
                Paragraph(r.direction, cell_style),
                f"${r.outbound_total:,.2f}",
                f"${r.cap:,.2f}",
                f"${r.overshoot:,.2f}",
            ])
        detail_style = TableStyle(
            base_table_style.getCommands() + [
                # Right-align Day + 4 right-hand columns (Transfer type,
                # Direction, Flow, Cap, Overshoot).
                ("ALIGN", (5, 1), (-1, -1), "RIGHT"),
            ],
        )
        elements.append(LongTable(
            detail_data,
            colWidths=[1.0 * inch, 1.0 * inch, 0.8 * inch,
                       0.7 * inch, 0.9 * inch, 0.65 * inch, 0.8 * inch,
                       0.65 * inch, 0.75 * inch],
            style=detail_style, repeatRows=1,
        ))
        elements.append(Spacer(1, 0.25 * inch))

    if child_groups:
        elements.extend([
            bookmarked_h3(
                "Child Accounts Grouped by Parent Role + Transfer Type",
                styles,
            ),
            Spacer(1, 0.05 * inch),
        ])
        group_data: list[list[object]] = [
            ["Parent role", "Transfer type",
             "Children breaching", "Total overshoot"],
        ]
        for s in child_groups:
            group_data.append([
                Paragraph(s.parent_role, cell_style),
                Paragraph(s.rail_name, cell_style),
                f"{s.distinct_children_breaching}",
                f"${s.total_overshoot:,.2f}",
            ])
        group_style = TableStyle(
            base_table_style.getCommands() + [
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
            ],
        )
        elements.append(LongTable(
            group_data,
            colWidths=[2.0 * inch, 2.0 * inch,
                       1.4 * inch, 1.5 * inch],
            style=group_style, repeatRows=1,
        ))
    return elements


def _stuck_pending_story(
    rows: list[StuckPendingViolation] | None,
    styles: Any,  # WHY: reportlab StyleSheet1 lacks PEP 561 stubs; runtime-imported in render fn
    singleton_ids: set[str],
    theme: ThemePreset,
) -> list[object]:  # WHY: reportlab Flowable list — runtime-imported to avoid hard reportlab dep
    """Platypus elements for the U.3.d Stuck pending transactions page.

    Current-state matview: NO date filter (mirrors L1 dashboard).
    Same parent/child split as Overdraft + Limit breach. Child
    summary 5 cols (parent role, transfer type, distinct children,
    stuck transaction count, total amount) — transaction count drives
    operational workload, child count drives spread.
    """
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        LongTable,
        PageBreak,
        Paragraph,
        Spacer,
        TableStyle,
    )

    elements: list[object] = [
        PageBreak(),
        bookmarked_h1("Stuck Pending Transactions", styles),
        Paragraph(
            "<i>Transactions currently in Pending status whose age "
            "exceeds the L2-configured aging cap. <b>Current-state</b> "
            "&mdash; shown regardless of posting date; the report "
            "period band on the cover does not scope this section.</i>",
            styles["BodyText"],
        ),
        Spacer(1, 0.15 * inch),
    ]

    if rows is None:
        elements.append(
            Paragraph(
                "<i>Database not configured &mdash; table not "
                "populated. Set <b>demo_database_url</b> in your "
                "config to query.</i>",
                styles["BodyText"],
            ),
        )
        return elements
    if not rows:
        elements.append(
            Paragraph(
                "<i>No stuck pending transactions &mdash; backlog "
                "clear.</i>",
                styles["BodyText"],
            ),
        )
        return elements

    parent_rows, child_groups = _split_stuck_pending_by_account_class(
        rows, singleton_ids,
    )
    cell_style = ParagraphStyle(
        "StuckPendingCell",
        parent=styles["BodyText"],
        fontSize=8,
        leading=10,
        spaceBefore=0,
        spaceAfter=0,
    )
    base_table_style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(theme.primary_fg)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(theme.accent_fg)),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(theme.link_tint)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
            colors.white, colors.HexColor(theme.secondary_bg),
        ]),
    ])

    if parent_rows:
        elements.extend([
            bookmarked_h3("Parent Accounts (Per-Row Detail)", styles),
            Spacer(1, 0.05 * inch),
        ])
        detail_data: list[list[object]] = [
            ["Account ID", "Account name", "Transfer type",
             "Posted", "Amount", "Age", "Cap"],
        ]
        for r in parent_rows:
            detail_data.append([
                Paragraph(r.account_id, cell_style),
                Paragraph(r.account_name, cell_style),
                Paragraph(r.rail_name, cell_style),
                r.posting.strftime("%Y-%m-%d %H:%M"),
                f"${r.amount_money:,.2f}",
                _format_age(r.age_seconds),
                _format_age(r.max_pending_age_seconds),
            ])
        detail_style = TableStyle(
            base_table_style.getCommands() + [
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
            ],
        )
        elements.append(LongTable(
            detail_data,
            colWidths=[1.15 * inch, 1.15 * inch, 1.1 * inch,
                       1.05 * inch, 0.95 * inch,
                       0.7 * inch, 0.7 * inch],
            style=detail_style, repeatRows=1,
        ))
        elements.append(Spacer(1, 0.25 * inch))

    if child_groups:
        elements.extend([
            bookmarked_h3(
                "Child Accounts Grouped by Parent Role + Transfer Type",
                styles,
            ),
            Spacer(1, 0.05 * inch),
        ])
        group_data: list[list[object]] = [
            ["Parent role", "Transfer type", "Children affected",
             "Stuck transactions", "Total amount"],
        ]
        for s in child_groups:
            group_data.append([
                Paragraph(s.parent_role, cell_style),
                Paragraph(s.rail_name, cell_style),
                f"{s.distinct_children_affected}",
                f"{s.stuck_transaction_count}",
                f"${s.total_stuck_amount:,.2f}",
            ])
        group_style = TableStyle(
            base_table_style.getCommands() + [
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
            ],
        )
        elements.append(LongTable(
            group_data,
            colWidths=[1.6 * inch, 1.6 * inch, 1.2 * inch,
                       1.2 * inch, 1.3 * inch],
            style=group_style, repeatRows=1,
        ))
    return elements


def _stuck_unbundled_story(
    rows: list[StuckUnbundledViolation] | None,
    styles: Any,  # WHY: reportlab StyleSheet1 lacks PEP 561 stubs; runtime-imported in render fn
    singleton_ids: set[str],
    theme: ThemePreset,
) -> list[object]:  # WHY: reportlab Flowable list — runtime-imported to avoid hard reportlab dep
    """Platypus elements for the U.3.e Stuck unbundled transactions page.

    Same shape as Stuck pending; cap is ``max_unbundled_age_seconds``.
    Current-state, no date filter.
    """
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        LongTable,
        PageBreak,
        Paragraph,
        Spacer,
        TableStyle,
    )

    elements: list[object] = [
        PageBreak(),
        bookmarked_h1("Stuck Unbundled Transactions", styles),
        Paragraph(
            "<i>Posted transactions awaiting bundle assignment whose "
            "age exceeds the L2-configured bundling cap. "
            "<b>Current-state</b> &mdash; shown regardless of posting "
            "date; the report period band on the cover does not scope "
            "this section.</i>",
            styles["BodyText"],
        ),
        Spacer(1, 0.15 * inch),
    ]

    if rows is None:
        elements.append(
            Paragraph(
                "<i>Database not configured &mdash; table not "
                "populated. Set <b>demo_database_url</b> in your "
                "config to query.</i>",
                styles["BodyText"],
            ),
        )
        return elements
    if not rows:
        elements.append(
            Paragraph(
                "<i>No stuck unbundled transactions &mdash; bundling "
                "caught up.</i>",
                styles["BodyText"],
            ),
        )
        return elements

    parent_rows, child_groups = _split_stuck_unbundled_by_account_class(
        rows, singleton_ids,
    )
    cell_style = ParagraphStyle(
        "StuckUnbundledCell",
        parent=styles["BodyText"],
        fontSize=8,
        leading=10,
        spaceBefore=0,
        spaceAfter=0,
    )
    base_table_style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(theme.primary_fg)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(theme.accent_fg)),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(theme.link_tint)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
            colors.white, colors.HexColor(theme.secondary_bg),
        ]),
    ])

    if parent_rows:
        elements.extend([
            bookmarked_h3("Parent Accounts (Per-Row Detail)", styles),
            Spacer(1, 0.05 * inch),
        ])
        detail_data: list[list[object]] = [
            ["Account ID", "Account name", "Transfer type",
             "Posted", "Amount", "Age", "Cap"],
        ]
        for r in parent_rows:
            detail_data.append([
                Paragraph(r.account_id, cell_style),
                Paragraph(r.account_name, cell_style),
                Paragraph(r.rail_name, cell_style),
                r.posting.strftime("%Y-%m-%d %H:%M"),
                f"${r.amount_money:,.2f}",
                _format_age(r.age_seconds),
                _format_age(r.max_unbundled_age_seconds),
            ])
        detail_style = TableStyle(
            base_table_style.getCommands() + [
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
            ],
        )
        elements.append(LongTable(
            detail_data,
            colWidths=[1.15 * inch, 1.15 * inch, 1.1 * inch,
                       1.05 * inch, 0.95 * inch,
                       0.7 * inch, 0.7 * inch],
            style=detail_style, repeatRows=1,
        ))
        elements.append(Spacer(1, 0.25 * inch))

    if child_groups:
        elements.extend([
            bookmarked_h3(
                "Child Accounts Grouped by Parent Role + Transfer Type",
                styles,
            ),
            Spacer(1, 0.05 * inch),
        ])
        group_data: list[list[object]] = [
            ["Parent role", "Transfer type", "Children affected",
             "Stuck transactions", "Total amount"],
        ]
        for s in child_groups:
            group_data.append([
                Paragraph(s.parent_role, cell_style),
                Paragraph(s.rail_name, cell_style),
                f"{s.distinct_children_affected}",
                f"{s.stuck_transaction_count}",
                f"${s.total_stuck_amount:,.2f}",
            ])
        group_style = TableStyle(
            base_table_style.getCommands() + [
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
            ],
        )
        elements.append(LongTable(
            group_data,
            colWidths=[1.6 * inch, 1.6 * inch, 1.2 * inch,
                       1.2 * inch, 1.3 * inch],
            style=group_style, repeatRows=1,
        ))
    return elements


def _supersession_story(
    data: SupersessionAuditData | None,
    styles: Any,  # WHY: reportlab StyleSheet1 lacks PEP 561 stubs; runtime-imported in render fn
    frame: AsOfFrame,
    theme: ThemePreset,
) -> list[object]:  # WHY: reportlab Flowable list — runtime-imported to avoid hard reportlab dep
    """Platypus elements for the U.3.f Supersession audit page.

    Aggregate table covers entire dataset; detail tables limited to
    the report window (one per base table, omitted if empty).
    """
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        LongTable,
        PageBreak,
        Paragraph,
        Spacer,
        TableStyle,
    )

    start, end = frame.window.start, frame.window.end
    elements: list[object] = [
        PageBreak(),
        bookmarked_h1("Supersession Audit", styles),
        Paragraph(
            "<i>Aggregate counts cover the <b>entire dataset</b> "
            "(current-state); detail tables are limited to "
            f"{start.isoformat()} &ndash; {end.isoformat()} "
            "(inclusive) so the page stays bounded as supersession "
            "history accumulates over time.</i>",
            styles["BodyText"],
        ),
        Spacer(1, 0.15 * inch),
    ]

    if data is None:
        elements.append(
            Paragraph(
                "<i>Database not configured &mdash; table not "
                "populated. Set <b>demo_database_url</b> in your "
                "config to query.</i>",
                styles["BodyText"],
            ),
        )
        return elements
    if not data.aggregates:
        elements.append(
            Paragraph(
                "<i>No supersessions recorded &mdash; entries have "
                "not been corrected.</i>",
                styles["BodyText"],
            ),
        )
        return elements

    cell_style = ParagraphStyle(
        "SupersessionCell",
        parent=styles["BodyText"],
        fontSize=8,
        leading=10,
        spaceBefore=0,
        spaceAfter=0,
    )
    base_table_style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(theme.primary_fg)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(theme.accent_fg)),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(theme.link_tint)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
            colors.white, colors.HexColor(theme.secondary_bg),
        ]),
    ])

    elements.extend([
        bookmarked_h3("Aggregate (Entire Dataset)", styles),
        Spacer(1, 0.05 * inch),
    ])
    aggregate_data: list[list[object]] = [
        ["Base table", "Reason category", "Total", "New in period"],
    ]
    for r in data.aggregates:
        aggregate_data.append([
            Paragraph(r.base_table, cell_style),
            Paragraph(r.supersedes_category, cell_style),
            f"{r.total_count:,}",
            f"{r.new_in_period_count:,}",
        ])
    aggregate_style = TableStyle(
        base_table_style.getCommands() + [
            ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ],
    )
    elements.append(LongTable(
        aggregate_data,
        colWidths=[1.6 * inch, 2.4 * inch, 1.4 * inch, 1.5 * inch],
        style=aggregate_style, repeatRows=1,
    ))

    if data.transaction_details:
        elements.extend([
            Spacer(1, 0.25 * inch),
            bookmarked_h3(
                "Transactions — Correcting Entries in Period", styles,
            ),
            Spacer(1, 0.05 * inch),
        ])
        txn_data: list[list[object]] = [
            ["Transaction ID", "Reason", "Account ID", "Account name",
             "Posted", "Amount"],
        ]
        for d in data.transaction_details:
            txn_data.append([
                Paragraph(d.transaction_id, cell_style),
                Paragraph(d.supersedes_category, cell_style),
                Paragraph(d.account_id, cell_style),
                Paragraph(d.account_name, cell_style),
                d.posting.strftime("%Y-%m-%d %H:%M"),
                f"${d.amount_money:,.2f}",
            ])
        txn_style = TableStyle(
            base_table_style.getCommands() + [
                ("ALIGN", (4, 1), (-1, -1), "RIGHT"),
            ],
        )
        elements.append(LongTable(
            txn_data,
            colWidths=[1.3 * inch, 1.1 * inch, 1.0 * inch,
                       1.2 * inch, 1.2 * inch, 1.0 * inch],
            style=txn_style, repeatRows=1,
        ))

    if data.daily_balance_details:
        elements.extend([
            Spacer(1, 0.25 * inch),
            bookmarked_h3(
                "Daily Balances — Correcting Entries in Period", styles,
            ),
            Spacer(1, 0.05 * inch),
        ])
        bal_data: list[list[object]] = [
            ["Account ID", "Account name", "Day", "Reason", "Balance"],
        ]
        for d in data.daily_balance_details:
            bal_data.append([
                Paragraph(d.account_id, cell_style),
                Paragraph(d.account_name, cell_style),
                d.business_day.isoformat(),
                Paragraph(d.supersedes_category, cell_style),
                f"${d.money:,.2f}",
            ])
        bal_style = TableStyle(
            base_table_style.getCommands() + [
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
            ],
        )
        elements.append(LongTable(
            bal_data,
            colWidths=[1.5 * inch, 1.5 * inch, 1.0 * inch,
                       1.5 * inch, 1.3 * inch],
            style=bal_style, repeatRows=1,
        ))

    if not data.transaction_details and not data.daily_balance_details:
        elements.extend([
            Spacer(1, 0.2 * inch),
            Paragraph(
                "<i>No new correcting entries posted in the report "
                "window &mdash; aggregate counts above are all from "
                "prior periods.</i>",
                styles["BodyText"],
            ),
        ])
    return elements


def _daily_statement_walks_story(
    walks: list[DailyStatementWalk] | None,
    styles: Any,  # WHY: reportlab StyleSheet1 lacks PEP 561 stubs; runtime-imported in render fn
    theme: ThemePreset,
) -> list[object]:  # WHY: reportlab Flowable list — runtime-imported to avoid hard reportlab dep
    """Platypus elements for the U.4 per-account Daily Statement walk pages.

    Section header at level-0 outline; one sub-section per walk at
    level-1 outline (so the auditor can jump to a specific account-day
    from the sidebar / TOC). Each walk renders a 5-KPI summary table
    + a transactions detail table mirroring the dashboard's Daily
    Statement sheet.
    """
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        LongTable,
        PageBreak,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    elements: list[object] = [
        PageBreak(),
        bookmarked_h1("Per-Account Daily Statement Walk", styles),
        Paragraph(
            "One walk per (account, day) pair from U.3.a's drift table, "
            "plus every internal parent-account day in the report window. "
            "Internal parents (L2 singletons &mdash; GL clearing, "
            "concentration, ZBA master) render even when drift is zero "
            "because their day-by-day walk is itself auditor-relevant; a "
            "clean walk is evidence of correctness. External counterparty "
            "singletons are out of scope for reconciliation and do not "
            "get walks.",
            styles["BodyText"],
        ),
        Paragraph(
            "<i>The <b>Drift</b> KPI here is the per-day drift "
            "(closing stored &minus; closing recomputed-from-day's-flow). "
            "U.3.a's table shows cumulative drift "
            "(stored &minus; sum of all transactions ever); the two can "
            "diverge when daily_balances are sparse.</i>",
            styles["BodyText"],
        ),
        Spacer(1, 0.15 * inch),
    ]

    if walks is None:
        elements.append(
            Paragraph(
                "<i>Database not configured &mdash; walks not "
                "populated. Set <b>demo_database_url</b> in your "
                "config to query.</i>",
                styles["BodyText"],
            ),
        )
        return elements
    if not walks:
        elements.append(
            Paragraph(
                "<i>No drift in the report window &mdash; no walks "
                "needed.</i>",
                styles["BodyText"],
            ),
        )
        return elements

    cell_style = ParagraphStyle(
        "DailyStatementCell",
        parent=styles["BodyText"],
        fontSize=8,
        leading=10,
        spaceBefore=0,
        spaceAfter=0,
    )
    base_table_style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(theme.primary_fg)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(theme.accent_fg)),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(theme.link_tint)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
            colors.white, colors.HexColor(theme.secondary_bg),
        ]),
    ])

    for w in walks:
        # Sub-section heading + bookmark (one per walk so auditor can
        # jump to a specific account-day from the sidebar / TOC).
        elements.append(PageBreak())
        elements.append(bookmarked_h3(
            f"{w.account_id} — {w.business_day_end.isoformat()}", styles,
        ))
        elements.append(Paragraph(
            f"<b>{w.account_name}</b> ({w.account_role})",
            styles["BodyText"],
        ))
        elements.append(Spacer(1, 0.1 * inch))

        # 5-KPI summary table (one row, currency-formatted).
        kpi_data: list[list[object]] = [
            ["Opening", "Debits", "Credits", "Closing stored", "Drift"],
            [
                f"${w.opening_balance:,.2f}",
                f"${w.total_debits:,.2f}",
                f"${w.total_credits:,.2f}",
                f"${w.closing_balance_stored:,.2f}",
                f"${w.drift:,.2f}",
            ],
        ]
        kpi_style = TableStyle(
            base_table_style.getCommands() + [
                ("ALIGN", (0, 1), (-1, -1), "RIGHT"),
                ("ALIGN", (0, 0), (-1, 0), "RIGHT"),
            ],
        )
        elements.append(Table(
            kpi_data,
            colWidths=[1.4 * inch] * 5,
            style=kpi_style,
        ))
        elements.append(Spacer(1, 0.2 * inch))

        # Day's transactions detail. Heading NOT bookmarked — adding
        # a "Posted Money records" entry per walk would clutter the
        # sidebar with 50 identical-titled entries; the per-walk
        # account-day bookmark already covers nav.
        if w.transactions:
            elements.append(Paragraph(
                "Posted Money records", styles["Heading3"],
            ))
            txn_data: list[list[object]] = [
                ["Posted", "Transaction ID", "Transfer type",
                 "Direction", "Amount", "Status"],
            ]
            for t in w.transactions:
                txn_data.append([
                    t.posting.strftime("%H:%M"),
                    Paragraph(t.transaction_id, cell_style),
                    Paragraph(t.rail_name, cell_style),
                    t.amount_direction,
                    f"${t.amount_money:,.2f}",
                    t.status,
                ])
            txn_style = TableStyle(
                base_table_style.getCommands() + [
                    ("ALIGN", (4, 1), (4, -1), "RIGHT"),
                ],
            )
            elements.append(LongTable(
                txn_data,
                colWidths=[0.7 * inch, 1.5 * inch, 1.4 * inch,
                           0.85 * inch, 1.0 * inch, 0.8 * inch],
                style=txn_style, repeatRows=1,
            ))
        else:
            elements.append(Paragraph(
                "<i>No Posted Money records on this day.</i>",
                styles["BodyText"],
            ))
    return elements


def _signoff_story(
    styles: Any,  # WHY: reportlab StyleSheet1 lacks PEP 561 stubs; runtime-imported in render fn
    theme: ThemePreset,
    *,
    institution: str,
    frame: AsOfFrame,
    generated_at: datetime,
    version: str,
    l2_label: str,
    provenance: ProvenanceFingerprint | None,
    signature_field_registry: list[tuple[str, int, tuple[float, float, float, float]]],  # mutable; populated at draw time
) -> list[object]:
    """Final-page sign-off block with system + auditor attestation (U.5).

    System block carries machine-attestable provenance (code version,
    L2 instance, period, generation timestamp, fingerprint
    placeholder) — what U.7's cryptographic seal will cover. Auditor
    block is a printable form (signature line + notes box) for human
    sign-off; intentionally separate so an unattended pipeline can
    publish the system block without forging a human signature.
    """
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    start, end = frame.window.start, frame.window.end
    cell_style = ParagraphStyle(
        "SignoffCell",
        parent=styles["BodyText"],
        fontSize=10,
        leading=13,
    )
    label_style = ParagraphStyle(
        "SignoffLabel",
        parent=cell_style,
        fontName="Helvetica-Bold",
    )
    panel_bg = HexColor(theme.link_tint)

    system_data = [
        [Paragraph("Institution", label_style),
         Paragraph(institution, cell_style)],
        [Paragraph("Reporting period", label_style),
         Paragraph(
             f"{start.isoformat()} &ndash; {end.isoformat()} (inclusive)",
             cell_style,
         )],
        [Paragraph("Generated by", label_style),
         Paragraph(f"recon-gen v{version}", cell_style)],
        [Paragraph("Generated at", label_style),
         Paragraph(
             generated_at.isoformat(timespec="seconds"), cell_style,
         )],
        [Paragraph("L2 instance", label_style),
         Paragraph(l2_label, cell_style)],
        [Paragraph("Provenance fingerprint", label_style),
         Paragraph(
             "<font face='Courier'>"
             + (
                 provenance.composite_sha
                 if provenance is not None
                 else l2_fingerprint_placeholder()
                     .replace("<", "&lt;").replace(">", "&gt;")
             )
             + "</font>",
             cell_style,
         )],
    ]
    system_table = Table(
        system_data,
        colWidths=[1.9 * inch, 4.6 * inch],
        style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, -1), panel_bg),
            ("BOX", (0, 0), (-1, -1), 0.5,
             HexColor(theme.secondary_fg)),
            ("INNERGRID", (0, 0), (-1, -1), 0.25,
             HexColor(theme.secondary_fg)),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]),
    )

    # Auditor block: no labelled name/title/date rows — when the PDF
    # carries digital signatures (system + any reviewers), each
    # signature dictionary already binds the signer name + signing
    # time. Duplicating those as printable form fields would invite
    # contradictions ("the digital sig says Jane on Tuesday, the
    # form line says John on Wednesday"). The notes / exceptions
    # box stays as the human surface for free-form review comments
    # before the next signer countersigns.

    # Notes / exceptions box — fillable AcroForm text field so a
    # reviewer can type comments in any PDF reader (Adobe, Preview,
    # pyHanko's signing UI) before adding their digital signature.
    # When the system signature is applied first, pyHanko leaves
    # form fields unlocked — subsequent signers can still fill the
    # notes before sealing with their own signature.
    notes_field = _make_fillable_notes_field(
        name="QSGNotesField",
        width=6.5 * inch,
        height=1.4 * inch,
        border_color=HexColor(theme.secondary_fg),
        tooltip=(
            "Notes / Exceptions — fill any review comments here "
            "before adding your digital signature."
        ),
    )
    # Two empty reviewer signature fields stacked below the notes
    # box. The placeholders just record their absolute page coords
    # into the registry; pyHanko's append_signature_field drops the
    # actual sig-field widgets at those coords post-multiBuild
    # (before signing), so a reviewer opening the PDF in any
    # signing-capable reader sees clickable Sign-Here boxes.
    sig_field_1 = _make_signature_field_placeholder(
        name="QSGReviewerSignature1",
        width=6.5 * inch,
        height=0.55 * inch,
        registry=signature_field_registry,
    )
    sig_field_2 = _make_signature_field_placeholder(
        name="QSGReviewerSignature2",
        width=6.5 * inch,
        height=0.55 * inch,
        registry=signature_field_registry,
    )

    return [
        PageBreak(),
        bookmarked_h1("Sign-Off", styles),
        Paragraph(
            "<b>System Attestation</b>",
            styles["Heading3"],
        ),
        Paragraph(
            "<i>Machine-generated. Binds this report to the code "
            "version, L2 spec, source data, and generation time. The "
            "external cryptographic seal over this block lands in "
            "Phase U.7.</i>",
            styles["BodyText"],
        ),
        Spacer(1, 0.1 * inch),
        system_table,
        Spacer(1, 0.3 * inch),
        Paragraph(
            "<b>Reviewer Attestation</b>",
            styles["Heading3"],
        ),
        Paragraph(
            "<i>I have reviewed the contents of this report and "
            "attest to the findings above as of the report period. "
            "Click a signature box below to sign in any PDF reader "
            "that supports digital signatures (Adobe Acrobat, "
            "pyHanko, etc.). The system attestation above stands on "
            "its own when no human reviewer countersigns; subsequent "
            "reviewers may stack additional signatures without "
            "invalidating the system seal.</i>",
            styles["BodyText"],
        ),
        Spacer(1, 0.2 * inch),
        Paragraph(
            "<b>Notes / Exceptions</b>",
            styles["BodyText"],
        ),
        Spacer(1, 0.05 * inch),
        notes_field,
        Spacer(1, 0.2 * inch),
        sig_field_1,
        Spacer(1, 0.1 * inch),
        sig_field_2,
    ]




def _make_fillable_notes_field(
    *,
    name: str,
    width: float,
    height: float,
    border_color: Any,  # WHY: reportlab colors.Color; runtime-imported
    tooltip: str,
) -> Any:  # WHY: reportlab Flowable; runtime-imported
    """Build a platypus Flowable that emits an AcroForm text field (U.7.c).

    Stays fillable in the generated PDF so a reviewer can type
    review comments into the notes box in any PDF reader (Adobe,
    Preview, Foxit) before applying their digital signature.
    pyHanko's incremental sign leaves form fields unlocked by
    default, so the system signature applied first does NOT prevent
    the next signer from filling this field — the next signer
    decides whether to lock fields when they sign.

    Subclasses ``Flowable`` so reportlab's frame engine handles
    layout (``wrap`` returns the field size; ``drawOn`` is the hook
    the frame calls with absolute ``(x, y)`` page coordinates).
    Overriding ``drawOn`` (NOT ``draw``) is required because
    ``canvas.acroForm.textfield`` ignores the canvas's translate
    transform — it records absolute page coordinates directly.
    The default Flowable.drawOn would translate the canvas to
    ``(x, y)`` and then call ``draw`` with origin at ``(0, 0)``,
    which lands the form widget at page-origin (bottom-left
    corner) instead of where the frame placed us. Bypassing the
    translate and feeding ``acroForm.textfield`` the real absolute
    ``(x, y)`` puts the widget where the layout reserved space.
    """
    from reportlab.platypus import Flowable

    class _FillableNotesField(Flowable):  # type: ignore[misc]: reportlab.Flowable has no PEP 561 stubs
        def __init__(self) -> None:
            super().__init__()
            self.width = width
            self.height = height

        def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:  # type: ignore[override]: reportlab.Flowable.wrap signature is Untyped
            return (self.width, self.height)

        def drawOn(self, canvas: Any, x: float, y: float, _sW: float = 0) -> None:  # type: ignore[override]: reportlab Flowable.drawOn signature differs
            # No canvas.saveState/translate dance — acroForm uses
            # absolute page coords, so feed (x, y) directly.
            canvas.acroForm.textfield(
                name=name,
                x=x,
                y=y,
                width=self.width,
                height=self.height,
                value="",
                fieldFlags="multiline",
                tooltip=tooltip,
                borderColor=border_color,
                forceBorder=True,
            )

    return _FillableNotesField()


def _make_signature_field_placeholder(
    *,
    name: str,
    width: float,
    height: float,
    registry: list[tuple[str, int, tuple[float, float, float, float]]],
) -> Any:  # WHY: reportlab Flowable; runtime-imported
    """Reserve layout space for an empty reviewer signature field.

    Reportlab can lay out the *space* for a signature widget but
    can't emit the widget itself — sig fields aren't part of
    ``canvas.acroForm``'s repertoire. So we use a two-step pattern:
    (1) this Flowable reserves the box and records its absolute
    page coordinates into a registry list; (2) post-multiBuild
    pyHanko's ``append_signature_field`` drops the actual
    ``empty_field_appearance=True`` widget at the recorded coords.

    Same ``drawOn``-not-``draw`` pattern as the notes field: the
    coords pyHanko needs are absolute page coords, and the frame
    engine passes those directly to ``drawOn`` before the default
    canvas-translate dance happens.
    """
    from reportlab.platypus import Flowable

    class _SigFieldPlaceholder(Flowable):  # type: ignore[misc]: reportlab.Flowable has no PEP 561 stubs
        def __init__(self) -> None:
            super().__init__()
            self.width = width
            self.height = height

        def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:  # type: ignore[override]: reportlab.Flowable.wrap signature is Untyped
            return (self.width, self.height)

        def drawOn(self, canvas: Any, x: float, y: float, _sW: float = 0) -> None:  # type: ignore[override]: reportlab Flowable.drawOn signature differs
            # canvas.getPageNumber() is 1-indexed; pyHanko's
            # SigFieldSpec.on_page is 0-indexed — translate here.
            page_idx = canvas.getPageNumber() - 1
            registry.append(
                (name, page_idx, (x, y, x + self.width, y + self.height))
            )

    return _SigFieldPlaceholder()


def _read_l2_yaml_bytes(l2_instance_path: str | None) -> bytes:
    """Same lookup ``l2_yaml_sha256`` uses, returning the bytes.

    For the appendix we attach the verbatim YAML to the PDF so a
    verifier can download it bit-exact from the PDF reader's
    attachments panel and SHA256 it themselves. Reading via the
    same code path that feeds the fingerprint guarantees
    byte-equality with what got hashed.
    """
    if l2_instance_path is None:
        from recon_gen.common.l2 import default_l2_bytes
        return default_l2_bytes()
    return Path(l2_instance_path).read_bytes()


def _add_empty_signature_fields(
    pdf_path: Path,
    *,
    fields: list[tuple[str, int, tuple[float, float, float, float]]],
) -> None:
    """Append empty reviewer signature widgets via pyHanko (U.7.c).

    Each ``fields`` entry is ``(field_name, page_idx, (x1, y1, x2,
    y2))`` — page_idx is 0-indexed; rect is in PDF user-space coords
    (y=0 at page bottom). Widgets are added with
    ``empty_field_appearance=True`` so PDF readers show a visible
    Sign-Here placeholder before any reviewer fills them.

    Run AFTER ``_attach_files_to_pdf`` and BEFORE the system signing
    step in audit_apply, so the system signature's byte range covers
    the empty reviewer field definitions; subsequent reviewers
    sign-into the existing fields rather than appending new ones.
    """
    if not fields:
        return
    import io

    from pyhanko.pdf_utils.incremental_writer import (
        IncrementalPdfFileWriter,
    )
    from pyhanko.sign.fields import (
        SigFieldSpec,
        append_signature_field,
    )

    # multiBuild runs the layout pass twice (TOC settling), so each
    # _SigFieldPlaceholder.drawOn fires twice and registers the
    # same name twice. Dedupe by name keeping the LAST entry, which
    # is the final pass's resolved coordinates.
    deduped: dict[str, tuple[str, int, tuple[float, float, float, float]]] = {}
    for entry in fields:
        deduped[entry[0]] = entry

    with pdf_path.open("rb") as inf:
        w = IncrementalPdfFileWriter(inf)
        for name, page_idx, box in deduped.values():
            x1, y1, x2, y2 = box
            spec = SigFieldSpec(
                sig_field_name=name,
                on_page=page_idx,
                box=(int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))),
                empty_field_appearance=True,
            )
            append_signature_field(w, spec)
        out = io.BytesIO()
        w.write(out)
    pdf_path.write_bytes(out.getvalue())


def _attach_files_to_pdf(
    pdf_path: Path,
    *,
    attachments: dict[str, bytes],
) -> None:
    """Embed file attachments into the PDF via pypdf (U.7.c).

    Run AFTER ``doc.multiBuild`` writes the PDF and BEFORE pyHanko
    signing (so attachments land inside the byte range the signature
    covers). Verifiers see attachments in their PDF reader's
    attachments panel and can download them byte-exact to recompute
    ``l2_yaml_sha256(file)`` (for the L2 yaml) or run the recipe
    locally (for ``verify-provenance.py``).
    """
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(pdf_path))
    writer = PdfWriter(clone_from=reader)
    for name, data in attachments.items():
        writer.add_attachment(name, data)
    with pdf_path.open("wb") as f:
        writer.write(f)


def _build_verify_recipe_script(
    *,
    tx_hwm: str,
    bal_hwm: str,
    code_id: str,
) -> str:
    """Manual-recompute Python recipe with this report's per-source values.

    Single source of truth for both the appendix's Preformatted
    code block AND the ``verify-provenance.py`` PDF attachment, so
    the script a reader sees on the page is byte-identical to the
    one they can download. ``tx_hwm`` / ``bal_hwm`` / ``code_id``
    come from the embedded ``ProvenanceFingerprint``; the table
    prefix is left as ``<prefix>`` because it lives in the L2 yaml
    (also attached) — the verifier substitutes per the spec they
    audited against.
    """
    return (
        "import hashlib\n"
        "\n"
        "def canonical(v):\n"
        "    if v is None: return b''\n"
        "    if isinstance(v, bool): return b'1' if v else b'0'\n"
        "    if hasattr(v, 'isoformat'): "
        "return v.isoformat().encode()\n"
        "    return str(v).encode()\n"
        "\n"
        "def hash_table(cur, table, hwm):\n"
        "    cur.execute(f'SELECT * FROM {table} '\n"
        "                f'WHERE entry <= {hwm} ORDER BY entry')\n"
        "    cols = sorted(\n"
        "        enumerate(cur.description),\n"
        "        key=lambda i_d: i_d[1][0].lower())\n"
        "    h = hashlib.sha256()\n"
        "    for row in cur:\n"
        "        h.update(b'\\x1f'.join(\n"
        "            canonical(row[i]) for i, _ in cols))\n"
        "        h.update(b'\\x1e')\n"
        "    return h.hexdigest()\n"
        "\n"
        f"tx_sha  = hash_table(cur, '<prefix>_transactions', {tx_hwm})\n"
        f"bal_sha = hash_table(cur, '<prefix>_daily_balances', {bal_hwm})\n"
        "l2_sha  = hashlib.sha256(\n"
        "    open(L2_YAML_PATH, 'rb').read()).hexdigest()\n"
        "\n"
        "h = hashlib.sha256()\n"
        f"h.update(b'tx_hwm={tx_hwm}\\n')\n"
        "h.update(f'tx_sha={tx_sha}\\n'.encode())\n"
        f"h.update(b'bal_hwm={bal_hwm}\\n')\n"
        "h.update(f'bal_sha={bal_sha}\\n'.encode())\n"
        "h.update(f'l2_sha={l2_sha}\\n'.encode())\n"
        f"h.update(b'code={code_id}\\n')\n"
        "print(h.hexdigest())  # composite_sha\n"
    )


def _appendix_story(
    styles: Any,  # WHY: reportlab StyleSheet1 lacks PEP 561 stubs; runtime-imported in render fn
    theme: ThemePreset,
    *,
    version: str,
    l2_label: str,
    l2_instance_path: str | None,
    provenance: ProvenanceFingerprint | None,
    matview_evidence: list[MatviewEvidence] | None,
) -> list[object]:
    """Provenance Appendix page (U.7.c).

    Targets the regulator who wants to audit the auditor: enough
    info to independently re-derive the report's fingerprint
    without recon-gen installed. Three sections:

    1. **Matview Evidence** — per-matview SHA256 + row count.
       Distinct from the authoritative composite fingerprint
       (which covers base tables); a divergence here is a
       *technical* signal (matview needs refresh) rather than a
       data-binding problem.
    2. **Reproduce With recon-gen** — the one-shot
       ``audit verify`` command.
    3. **Reproduce Manually** — the per-source recompute formulas
       + a ~50-line Python recipe a third-party verifier can paste
       into a script. SHA256 each input, concatenate the labeled
       lines, hash again — that's the composite.

    Bookmarked at outline level 0 alongside the per-invariant
    sections so it shows up in the sidebar nav + TOC. When the
    audit ran without a DB (skeleton mode), shows the matview
    table with placeholder dashes; the recipe still renders since
    it's static text.
    """
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    cell_style = ParagraphStyle(
        "AppendixCell",
        parent=styles["BodyText"],
        fontSize=9,
        leading=12,
    )
    code_style = ParagraphStyle(
        "AppendixCode",
        parent=cell_style,
        fontName="Courier",
        fontSize=8,
    )
    header_style = ParagraphStyle(
        "AppendixHeader",
        parent=cell_style,
        fontName="Helvetica-Bold",
        textColor=HexColor(theme.primary_fg),
    )

    # --- (1) Matview evidence table ---
    if matview_evidence:
        rows = [
            [
                Paragraph("Matview", header_style),
                Paragraph("Rows", header_style),
                Paragraph("SHA256", header_style),
            ],
        ]
        for ev in matview_evidence:
            rows.append([
                Paragraph(ev.matview, cell_style),
                Paragraph(f"{ev.row_count:,}", code_style),
                Paragraph(ev.sha256, code_style),
            ])
    else:
        rows = [
            [
                Paragraph("Matview", header_style),
                Paragraph("Rows", header_style),
                Paragraph("SHA256", header_style),
            ],
            [
                Paragraph(
                    "<i>Database not configured at audit time — "
                    "matview evidence not available.</i>",
                    cell_style,
                ),
                Paragraph("—", cell_style),
                Paragraph("—", cell_style),
            ],
        ]
    matview_table = Table(
        rows,
        colWidths=[2.0 * inch, 0.8 * inch, 3.7 * inch],
        style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, 0),
             HexColor(theme.link_tint)),
            ("BOX", (0, 0), (-1, -1), 0.5,
             HexColor(theme.secondary_fg)),
            ("INNERGRID", (0, 0), (-1, -1), 0.25,
             HexColor(theme.secondary_fg)),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]),
    )

    # --- (3) Per-source breakdown for the manual-verify recipe ---
    placeholder = "<pending>"
    if provenance is not None:
        tx_hwm = str(provenance.transactions_hwm)
        tx_sha = provenance.transactions_sha
        bal_hwm = str(provenance.balances_hwm)
        bal_sha = provenance.balances_sha
        l2_sha = provenance.l2_yaml_sha
        code_id = provenance.code_identity
        composite = provenance.composite_sha
    else:
        tx_hwm = bal_hwm = tx_sha = bal_sha = l2_sha = placeholder
        code_id = f"v{version}"
        composite = placeholder

    sources_rows = [
        [
            Paragraph("Source", header_style),
            Paragraph("Identifier", header_style),
            Paragraph("SHA256", header_style),
        ],
        [
            Paragraph("Transactions table", cell_style),
            Paragraph(f"entry &le; {tx_hwm}", code_style),
            Paragraph(tx_sha, code_style),
        ],
        [
            Paragraph("Daily balances table", cell_style),
            Paragraph(f"entry &le; {bal_hwm}", code_style),
            Paragraph(bal_sha, code_style),
        ],
        [
            Paragraph("L2 instance YAML", cell_style),
            Paragraph(l2_label, code_style),
            Paragraph(l2_sha, code_style),
        ],
        [
            Paragraph("recon-gen code", cell_style),
            Paragraph(code_id, code_style),
            Paragraph("(identity, no SHA)", cell_style),
        ],
        [
            Paragraph(
                "<b>Composite fingerprint</b>", cell_style,
            ),
            Paragraph("SHA256 of labeled lines", code_style),
            Paragraph(f"<b>{composite}</b>", code_style),
        ],
    ]
    sources_table = Table(
        sources_rows,
        colWidths=[1.7 * inch, 2.2 * inch, 2.6 * inch],
        style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, 0),
             HexColor(theme.link_tint)),
            ("BOX", (0, 0), (-1, -1), 0.5,
             HexColor(theme.secondary_fg)),
            ("INNERGRID", (0, 0), (-1, -1), 0.25,
             HexColor(theme.secondary_fg)),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]),
    )

    return [
        PageBreak(),
        bookmarked_h1("Provenance Appendix", styles),
        Paragraph(
            "<i>Everything an independent verifier needs to "
            "reproduce this report's bindings without "
            "recon-gen installed. The composite fingerprint "
            "in the cover, sign-off, and footer is the "
            "authoritative artifact; this appendix shows how it "
            "was computed.</i>",
            styles["BodyText"],
        ),
        Spacer(1, 0.2 * inch),

        Paragraph("<b>Matview Evidence</b>", styles["Heading3"]),
        Paragraph(
            "<i>Per-matview SHA256 + row count, computed via the "
            "same alphabetical-column-discovery + canonical-bytes "
            "recipe as the base-table fingerprint. NOT part of the "
            "authoritative composite — matviews are derived data. "
            "A divergence between these and a recompute is a "
            "technical signal (the matview needs refresh, schema "
            "drift) rather than a data-binding problem.</i>",
            styles["BodyText"],
        ),
        Spacer(1, 0.1 * inch),
        matview_table,
        Spacer(1, 0.3 * inch),

        Paragraph(
            "<b>Reproduce With recon-gen</b>",
            styles["Heading3"],
        ),
        Paragraph(
            "<font face='Courier'>"
            "recon-gen audit verify report.pdf "
            "-c config.yaml --l2 &lt;path-to-L2.yaml&gt;"
            "</font>",
            cell_style,
        ),
        Paragraph(
            "<i>Extracts the embedded provenance JSON from the "
            "PDF's /Subject metadata, recomputes each input at "
            "the embedded high-water-marks, and compares. Exit 0 "
            "on match, 1 on per-source diff.</i>",
            styles["BodyText"],
        ),
        Spacer(1, 0.3 * inch),

        Paragraph(
            "<b>Reproduce Manually</b>",
            styles["Heading3"],
        ),
        Paragraph(
            "<i>For verifiers who don't want to install "
            "recon-gen. Per-source values embedded in this "
            "report are below; the recompute recipe is attached "
            "to this PDF as "
            "<font face='Courier'>verify-provenance.py</font> "
            "(see Attachments) — open it from the PDF reader's "
            "attachments panel.</i>",
            styles["BodyText"],
        ),
        Spacer(1, 0.1 * inch),
        sources_table,
        Spacer(1, 0.25 * inch),

        Paragraph("<b>Attachments</b>", styles["Heading3"]),
        Paragraph(
            "<i>This PDF carries two file attachments. Open it in "
            "any reader (Adobe Acrobat, Preview, Foxit, etc.), "
            "find the attachments panel, and download each file "
            "byte-for-byte.</i>",
            styles["BodyText"],
        ),
        Spacer(1, 0.05 * inch),
        Paragraph(
            "<b><font face='Courier'>"
            f"{l2_label}"
            "</font></b> &mdash; the L2 instance YAML this report "
            "was generated against. Run "
            "<font face='Courier'>"
            "sha256sum &lt;attachment&gt;"
            "</font> to confirm it matches the "
            "<font face='Courier'>l2_yaml_sha</font> in the "
            "sources table; that proves the L2 spec the auditor "
            "saw is the L2 spec the report was generated against.",
            styles["BodyText"],
        ),
        Spacer(1, 0.05 * inch),
        Paragraph(
            "<b><font face='Courier'>verify-provenance.py</font>"
            "</b> &mdash; the manual-recompute Python recipe shown "
            "above, with this report's per-source high-water marks "
            "and code identity already substituted in. Drop it into "
            "a script that opens a database cursor against the "
            "operator's tables, run it, and the printed hash should "
            "match the composite fingerprint above.",
            styles["BodyText"],
        ),
    ]


def _cover_logo_flowable(theme: ThemePreset) -> Any:  # WHY: reportlab Image | None; runtime-imported
    """Cover-page logo Image flowable (or None if no logo / can't load).

    Reads ``theme.logo`` (string accepting either a URL or absolute
    file path). For audit-PDF generation we deliberately do NOT
    fetch URLs — making the audit network-dependent at gen time is
    a fragility we don't want for a regulator-facing deliverable
    (and a URL fetch failure mid-generation would either break the
    audit outright or silently swap in a stale cached version
    depending on caching). URLs and unloadable paths log a warning
    and skip the logo; the cover renders without it rather than
    failing the audit.

    Sized to fit within a 4"x1" bounding box, scaled proportionally
    so the natural aspect ratio is preserved. Centered horizontally
    by reportlab's default ``hAlign``.
    """
    from reportlab.lib.units import inch
    from reportlab.platypus import Image

    logo = getattr(theme, "logo", None)
    if not logo:
        return None
    if logo.startswith(("http://", "https://", "//")):
        click.echo(
            f"audit: skipping URL logo {logo!r} on cover page — URL "
            f"fetching disabled for audit reproducibility. Use an "
            f"absolute file path in theme.logo to render.",
            err=True,
        )
        return None
    path = Path(logo)
    if not path.is_absolute() or not path.is_file():
        click.echo(
            f"audit: theme.logo {logo!r} not found at absolute file "
            f"path — cover page will render without it.",
            err=True,
        )
        return None
    try:
        return Image(
            str(path),
            width=4.0 * inch,
            height=1.0 * inch,
            kind="proportional",
        )
    except Exception as e:  # reportlab raises various
        click.echo(
            f"audit: failed to load theme.logo {logo!r}: {e}; "
            f"cover page will render without it.",
            err=True,
        )
        return None


def _provenance_block_story(
    styles: Any,  # WHY: reportlab StyleSheet1 lacks PEP 561 stubs; runtime-imported in render fn
    theme: ThemePreset,
    *,
    version: str,
    l2_label: str,
    provenance: ProvenanceFingerprint | None,
) -> list[object]:
    """Cover-page long-form source-data provenance block (U.6).

    Lists the source artifacts that, together, fully determine this
    report's content: the operator's two base tables (transactions +
    daily_balances), the L2 instance YAML, and the recon-gen
    code version. U.7 fills in the per-source SHA256 + high-water
    entry-id columns; until then they show the long-form
    ``<pending>`` placeholder so a grep for it catches a "we shipped
    without wiring U.7" regression before the auditor does.

    Distinct from the per-page footer (which carries a SHORT hash);
    this is the per-source breakdown that the footer's hash
    summarizes.
    """
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    cell_style = ParagraphStyle(
        "ProvenanceCell",
        parent=styles["BodyText"],
        fontSize=9,
        leading=12,
    )
    header_style = ParagraphStyle(
        "ProvenanceHeader",
        parent=cell_style,
        fontName="Helvetica-Bold",
        textColor=HexColor(theme.primary_fg),
    )
    code_style = ParagraphStyle(
        "ProvenanceCode",
        parent=cell_style,
        fontName="Courier",
    )

    placeholder = l2_fingerprint_placeholder().replace(
        "<", "&lt;",
    ).replace(">", "&gt;")

    def _row(source: str, hwm: str, hash_text: str) -> list[object]:
        return [
            Paragraph(source, cell_style),
            Paragraph(hwm, code_style),
            Paragraph(hash_text, code_style),
        ]

    if provenance is not None:
        tx_hwm = str(provenance.transactions_hwm)
        tx_sha = provenance.transactions_sha
        bal_hwm = str(provenance.balances_hwm)
        bal_sha = provenance.balances_sha
        l2_sha = provenance.l2_yaml_sha
        code_id = provenance.code_identity
        code_sha = provenance.composite_sha
    else:
        tx_hwm = tx_sha = bal_hwm = bal_sha = l2_sha = placeholder
        code_id = f"v{version}"
        code_sha = placeholder

    rows = [
        [
            Paragraph("Source", header_style),
            Paragraph("Last entry / version", header_style),
            Paragraph("SHA256", header_style),
        ],
        _row("Transactions table", tx_hwm, tx_sha),
        _row("Daily balances table", bal_hwm, bal_sha),
        _row("L2 instance YAML", l2_label, l2_sha),
        _row("recon-gen code", code_id, code_sha),
    ]
    table = Table(
        rows,
        colWidths=[2.0 * inch, 2.2 * inch, 2.8 * inch],
        style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, 0),
             HexColor(theme.link_tint)),
            ("BOX", (0, 0), (-1, -1), 0.5,
             HexColor(theme.secondary_fg)),
            ("INNERGRID", (0, 0), (-1, -1), 0.25,
             HexColor(theme.secondary_fg)),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]),
    )
    return [
        Spacer(1, 0.3 * inch),
        Paragraph(
            "<b>Source-Data Provenance</b>", styles["Heading3"],
        ),
        Paragraph(
            "<i>Reproducibility binding. The contents of this report "
            "derive entirely from the four sources below. The full "
            "fingerprint (the SHA256 of these inputs concatenated) "
            "is summarized in every page footer; the cryptographic "
            "seal over the system attestation block on the sign-off "
            "page covers the same inputs.</i>",
            styles["BodyText"],
        ),
        Spacer(1, 0.1 * inch),
        table,
    ]
