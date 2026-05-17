"""AA.C.5 — trainer-pane renderer unit tests.

These tests pin the contract :func:`render_training_pane` exposes to
the Studio ``/data`` page: one card per L1 invariant kind, kind label,
plain-English statement, ``Action.`` remediation, and a deep-link to
the App2 dashboard sheet that surfaces the matview. Bundled-doc
content drives the render — the parser is exercised separately in
``tests/unit/test_handbook_invariants.py``; here we cover the
trainer-pane wiring on top of it.
"""

from __future__ import annotations

from quicksight_gen.common.handbook.invariants import (
    INVARIANT_KIND_TO_SHEET,
    load_bundled_invariants,
)
from quicksight_gen.common.html._studio_training import (
    _L1_DASHBOARD_SLUG,
    _L1_KIND_TO_SHEET_ID,
    render_training_pane,
)


def test_render_training_pane_lists_every_invariant_kind() -> None:
    """Every kind ``load_bundled_invariants`` exposes lands as its own
    ``<li class="data-training__entry" data-kind="...">`` card. Pins
    that a parser-side addition (a new ``### N. ...`` section in
    L1_Invariants.md) propagates here without trainer-pane changes."""
    html = render_training_pane()
    expected_kinds = set(load_bundled_invariants().keys())
    assert expected_kinds, "fixture sanity: bundled doc parsed empty"
    for kind in expected_kinds:
        assert f'data-kind="{kind}"' in html, (
            f"trainer pane missing card for kind={kind!r}"
        )


def test_render_training_pane_links_each_kind_to_app2_dashboard() -> None:
    """Each card carries an ``<a class="data-training__link" href=...>``
    pointing at ``/dashboards/l1_dashboard/sheets/<sheet_id>``. The
    deep-link target is the App2 dashboard (not QS) because QS's
    URL-param-doesn't-sync-control quirk breaks initial-load narrowing
    on the QS leg — see ``project_qs_url_parameter_no_control_sync``
    memory entry + the AA.C.5 PLAN note."""
    html = render_training_pane()
    for kind, sheet_id in _L1_KIND_TO_SHEET_ID.items():
        href = f"/dashboards/{_L1_DASHBOARD_SLUG}/sheets/{sheet_id}"
        assert f'href="{href}"' in html, (
            f"trainer pane missing deep-link for kind={kind!r} → {href}"
        )


def test_render_training_pane_link_map_covers_every_invariant_kind() -> None:
    """Pin :data:`_L1_KIND_TO_SHEET_ID` to :data:`INVARIANT_KIND_TO_SHEET`'s
    key set so a parser-side kind addition fails *here* (not silently at
    render time) until the trainer adds the sheet_id mapping."""
    parser_kinds = set(INVARIANT_KIND_TO_SHEET.keys())
    trainer_kinds = set(_L1_KIND_TO_SHEET_ID.keys())
    missing = parser_kinds - trainer_kinds
    assert not missing, (
        f"trainer pane sheet-id map missing kinds: {sorted(missing)}. "
        f"Add to _L1_KIND_TO_SHEET_ID in _studio_training.py."
    )
    extra = trainer_kinds - parser_kinds
    assert not extra, (
        f"trainer pane sheet-id map has stale kinds (not in parser): "
        f"{sorted(extra)}"
    )


def test_render_training_pane_carries_action_line_for_every_kind() -> None:
    """Every kind has a ``**What to do:** ...`` line in L1_Invariants.md
    (AA.C.2 contract); the trainer card renders it as
    ``<strong>Action.</strong> <text>``. Pins both the parser's
    extraction AND the trainer's render path so a regression on either
    side surfaces here."""
    html = render_training_pane()
    by_kind = load_bundled_invariants()
    for kind, section in by_kind.items():
        assert section.what_to_do, (
            f"bundled-doc sanity: kind={kind!r} missing **What to do:** "
            f"line in L1_Invariants.md"
        )
    action_count = html.count("<strong>Action.</strong>")
    assert action_count == len(by_kind), (
        f"expected one Action line per kind ({len(by_kind)}); got "
        f"{action_count}"
    )


def test_render_training_pane_card_for_supersession_audit_has_no_blockquote() -> None:
    """The Supersession Audit section is descriptive (no SHOULD
    constraint blockquote in L1_Invariants.md) — its card omits the
    ``data-training__should`` paragraph rather than rendering an empty
    one. Pins the rendering branch for sections with empty
    :attr:`short_statement`."""
    html = render_training_pane()
    by_kind = load_bundled_invariants()
    section = by_kind["supersession_audit"]
    assert section.short_statement == "", (
        "bundled-doc sanity: supersession_audit should have no blockquote"
    )
    # The card itself is present.
    assert 'data-kind="supersession_audit"' in html
    # …but no should-statement <p> for it. Carve out the card's slice
    # by finding the next entry start and ensuring the should-class
    # doesn't appear within.
    start = html.index('data-kind="supersession_audit"')
    end = html.find('data-kind="', start + 1)
    if end == -1:
        end = len(html)
    card = html[start:end]
    assert 'data-training__should' not in card, (
        "supersession_audit card should omit empty should-paragraph"
    )


def test_render_training_pane_intro_explains_app2_deep_link() -> None:
    """The intro paragraph names App2 as the deep-link target so the
    trainer understands why the link doesn't open the QS embed
    (operator clarity, not just a doc-internal note)."""
    html = render_training_pane()
    assert 'data-training__heading' in html
    assert 'data-training__intro' in html
    assert "App2" in html, (
        "intro paragraph should call out App2 as the deep-link target"
    )


def test_render_training_pane_orders_drift_before_aging() -> None:
    """Display order: data-integrity foundations (drift) before
    aging/diagnostic kinds. Operators read top-to-bottom, so this
    matters. Specific pin: ``drift`` appears before ``stuck_pending``
    in the rendered HTML."""
    html = render_training_pane()
    drift_pos = html.find('data-kind="drift"')
    stuck_pos = html.find('data-kind="stuck_pending"')
    assert 0 <= drift_pos < stuck_pos, (
        f"drift card should appear before stuck_pending; "
        f"drift_pos={drift_pos}, stuck_pos={stuck_pos}"
    )
