"""Studio trainer pane (AA.C.5 / X.4.h.9).

Renders the L1 invariant catalogue into the right column of the Studio
``/data`` page (the ``<section class="data-training">`` slot
``_studio_routes._render_data_page`` reserves). One card per kind, each
showing:

- the kind label (badge),
- the human title + plain-English SHOULD statement (the operator-
  facing description),
- the ``**Action.**`` remediation,
- a deep-link to the App2 dashboard sheet that surfaces the matview
  for that kind.

Source is :func:`quicksight_gen.common.handbook.invariants.load_bundled_invariants`
— same parser AA.C.3 uses for the QuickSight sheet-bottom panels, so
the trainer's vocabulary stays in lock-step with the operator's
dashboard view. The destination dashboard is always App2 (NOT the
QuickSight embed): App2's URL-param control-sync defect (see
``project_qs_url_parameter_no_control_sync`` quirk) breaks deep-link
narrowing on the QS side, so trainer→trainee handoff only works
cleanly through the self-hosted renderer.

The dashboard slug + sheet IDs are constants — the L1 dashboard is
always mounted under ``"l1_dashboard"`` in App2 (see
``cli/_html_serve.REAL_APPS``), and the sheet IDs are pinned in
``apps/l1_dashboard/app.py`` (``SHEET_DRIFT`` etc). No tree
introspection needed here.
"""

from __future__ import annotations

from html import escape
from typing import Final

from quicksight_gen.common.handbook.invariants import (
    InvariantSection,
    load_bundled_invariants,
)


# App2 dashboard slug for the L1 dashboard. The trainer pane links
# point at ``/dashboards/{_L1_DASHBOARD_SLUG}/sheets/{_L1_KIND_TO_SHEET_ID[kind]}``.
# Sourced from ``cli/_html_serve.REAL_APPS`` — the slug is the dict key
# Studio's ``_html_serve._serve()`` registers each dashboard under.
_L1_DASHBOARD_SLUG: Final[str] = "l1_dashboard"


# Per-kind App2 sheet ID. Mirrors
# :data:`quicksight_gen.common.handbook.invariants.INVARIANT_KIND_TO_SHEET`'s
# kind→human-name mapping, but resolves to the URL-facing sheet_id the
# tree assigns (see ``apps/l1_dashboard/app.py`` SHEET_* constants).
# ``drift`` + ``ledger_drift`` both deep-link to the same Drift sheet;
# ``expected_eod_balance_breach`` deep-links to Today's Exceptions
# (the rollup sheet that surfaces it under M.2b).
_L1_KIND_TO_SHEET_ID: Final[dict[str, str]] = {
    "drift": "l1-sheet-drift",
    "ledger_drift": "l1-sheet-drift",
    "overdraft": "l1-sheet-overdraft",
    "expected_eod_balance_breach": "l1-sheet-todays-exceptions",
    "limit_breach": "l1-sheet-limit-breach",
    "stuck_pending": "l1-sheet-pending-aging",
    "stuck_unbundled": "l1-sheet-unbundled-aging",
    "supersession_audit": "l1-sheet-supersession-audit",
}


# Display order for the trainer pane cards. Picked to flow operator
# attention from the costliest invariants down to the diagnostic
# rollups: drift first (data integrity foundation), balance/policy
# breaches next (overdraft / limit / EOD), then aging exceptions
# (stuck pending / unbundled), then the supersession audit
# (diagnostic, not a SHOULD).
_DISPLAY_ORDER: Final[tuple[str, ...]] = (
    "drift",
    "ledger_drift",
    "overdraft",
    "limit_breach",
    "expected_eod_balance_breach",
    "stuck_pending",
    "stuck_unbundled",
    "supersession_audit",
)


def _ordered_sections(
    by_kind: dict[str, InvariantSection],
) -> list[InvariantSection]:
    """Return the parsed sections in :data:`_DISPLAY_ORDER` order.

    Unknown kinds (parser gained a new entry but the order tuple wasn't
    updated) tail the list in their natural ``by_kind`` order so the
    trainer never silently drops content. The unit tests pin every
    bundled kind appears in the order tuple, so this fallback is the
    safety net, not the normal path.
    """
    ordered: list[InvariantSection] = []
    seen: set[str] = set()
    for kind in _DISPLAY_ORDER:
        section = by_kind.get(kind)
        if section is not None:
            ordered.append(section)
            seen.add(kind)
    for kind, section in by_kind.items():
        if kind not in seen:
            ordered.append(section)
    return ordered


def _render_card(section: InvariantSection) -> str:
    """One trainer card. See module docstring for shape."""
    kind = escape(section.kind)
    title = escape(section.title)
    sheet_id = _L1_KIND_TO_SHEET_ID.get(section.kind)
    sheet_link_html = ""
    if sheet_id is not None:
        href = f"/dashboards/{escape(_L1_DASHBOARD_SLUG)}/sheets/{escape(sheet_id)}"
        sheet_link_html = (
            f'    <a class="data-training__link" href="{href}">'
            f'Open dashboard sheet →</a>\n'
        )
    parts = [
        f'  <li class="data-training__entry" data-kind="{kind}">',
        '    <header class="data-training__entry-head">',
        f'      <span class="data-training__kind">{kind}</span>',
        f'      <h3 class="data-training__title">{title}</h3>',
        '    </header>',
    ]
    if section.short_statement:
        parts.append(
            '    <p class="data-training__should">'
            f'{escape(section.short_statement)}</p>'
        )
    if section.what_to_do:
        parts.append(
            '    <p class="data-training__action">'
            f'<strong>Action.</strong> {escape(section.what_to_do)}</p>'
        )
    if sheet_link_html:
        parts.append(sheet_link_html.rstrip("\n"))
    parts.append('  </li>')
    return "\n".join(parts)


def render_training_pane() -> str:
    """Render the inner contents of ``<section class="data-training">``.

    Returns the HTML to splice INSIDE the section element — caller
    keeps the ``<section>`` wrapper + its automation attributes. The
    placeholder ``<p class="data-empty">training pane lands in X.4.h.9</p>``
    AA.C.5 supersedes is replaced wholesale by this output.

    Reads the bundled ``L1_Invariants.md`` via
    :func:`load_bundled_invariants`. Pure function — no DB, no I/O
    beyond the one-shot bundled-doc read; safe to call on every
    ``/data`` render.
    """
    by_kind = load_bundled_invariants()
    sections = _ordered_sections(by_kind)
    intro = (
        '  <h2 class="data-training__heading">Exception catalogue</h2>\n'
        '  <p class="data-training__intro">Each card describes one L1 '
        'invariant kind: what it means, what to do when it fires, and a '
        'link to the dashboard sheet that surfaces the underlying '
        'matview. The dashboards open in App2 (the self-hosted '
        'renderer) so the link narrows to the kind on initial load.</p>'
    )
    cards = "\n".join(_render_card(s) for s in sections)
    return f"{intro}\n  <ol class=\"data-training__list\">\n{cards}\n  </ol>"
