"""BX.15 — Coverage + Trainer diagram-sidebar chip tooltips.

The diagram sidebar's Overlays section carries two checkboxes
(``id="toggle-coverage"`` + ``id="toggle-trainer"``) that flip the
node-tint overlays. Pre-BX.15 the operator had no inline explanation
of what either tint *means* (which colour = what?) or *where* the
data comes from (live ETL? planted scenario rows?). This file pins:

- Each checkbox is followed by a ``[?]`` side-panel trigger pointing
  at the matching GLOSSARY anchor (``coverage`` / ``trainer-tint``).
- Both anchors resolve to non-empty GLOSSARY entries (anti-drift —
  a typo would break here, not at the operator's first click).
- The Coverage chip is suppressed in lockstep with its toggle when
  ``coverage_available=False`` (no DB pool wired); the Trainer chip
  always renders (pure scenario walk, no DB dependency).
- The chip lives OUTSIDE the ``<label>`` so its click doesn't toggle
  the checkbox (label-internal clicks dispatch to the checkbox).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recon_gen.common.html._side_panel import GLOSSARY


FIXTURES_DIR = Path(__file__).parent.parent / "l2"


# -- GLOSSARY anti-drift (sessionstart parity) -------------------------------


def test_bx15_glossary_carries_coverage_entry() -> None:
    """The ``coverage`` anchor used by the diagram sidebar [?] chip
    MUST resolve in GLOSSARY. A rename / typo / removal breaks here
    at sessionstart, not at the operator's first click."""
    assert "coverage" in GLOSSARY, (
        "BX.15 — missing GLOSSARY['coverage']; the diagram sidebar's "
        "Coverage [?] chip would 404. Add the entry to "
        "common/html/_side_panel.py::GLOSSARY."
    )
    body = GLOSSARY["coverage"]
    assert body.strip(), "coverage body is empty"
    assert len(body) > 100, "coverage body too short to be useful"
    # Lead with the term name in bold — display convention.
    assert "**Coverage**" in body


def test_bx15_glossary_carries_trainer_tint_entry() -> None:
    """Same anti-drift for the Trainer tint anchor."""
    assert "trainer-tint" in GLOSSARY, (
        "BX.15 — missing GLOSSARY['trainer-tint']; the diagram "
        "sidebar's Trainer [?] chip would 404. Add the entry to "
        "common/html/_side_panel.py::GLOSSARY."
    )
    body = GLOSSARY["trainer-tint"]
    assert body.strip(), "trainer-tint body is empty"
    assert len(body) > 100, "trainer-tint body too short to be useful"
    assert "**Trainer tint**" in body


# -- Diagram sidebar markup --------------------------------------------------


def _render_with_coverage(*, coverage_available: bool) -> str:
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_diagram_page,
    )
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415

    cache = L2InstanceCache.from_path(FIXTURES_DIR / "spec_example.yaml")
    return _render_diagram_page(
        cache,
        dev_log=False,
        focus_node_id=None,
        layer=1,
        coverage_available=coverage_available,
        embed=False,
    )


def test_bx15_coverage_chip_targets_coverage_glossary_entry() -> None:
    """The Coverage [?] chip wires ``hx-get`` at the coverage glossary
    route, opens the shared side-panel drawer, and carries an
    ARIA label the screen reader can announce."""
    html = _render_with_coverage(coverage_available=True)

    # Coverage checkbox still renders (BX.15 doesn't change the toggle).
    assert 'id="toggle-coverage"' in html
    # The [?] trigger right next to it points at the coverage glossary
    # fragment route. This is the load-bearing wire — if the URL drifts,
    # the side-panel route's 404 fallback would surface (visible to the
    # operator but useless).
    assert 'hx-get="/studio/side-panel/glossary/coverage"' in html, (
        "BX.15 — Coverage [?] chip missing its hx-get target"
    )
    # ARIA label is operator-readable, not "open glossary entry for ?".
    assert 'aria-label="What does the Coverage overlay show?"' in html


def test_bx15_trainer_chip_targets_trainer_tint_glossary_entry() -> None:
    """Same wire-shape assertion for the Trainer [?] chip."""
    html = _render_with_coverage(coverage_available=True)

    assert 'id="toggle-trainer"' in html
    assert 'hx-get="/studio/side-panel/glossary/trainer-tint"' in html, (
        "BX.15 — Trainer [?] chip missing its hx-get target"
    )
    assert 'aria-label="What does the Trainer tint show?"' in html


def test_bx15_coverage_chip_suppressed_when_toggle_suppressed() -> None:
    """Coverage toggle is conditional on a wired demo-DB pool. The
    [?] chip should follow the toggle — a chip explaining a UI control
    that isn't rendered would be confusing. Trainer's always-on, so
    its chip remains in both states."""
    html_without_pool = _render_with_coverage(coverage_available=False)

    # Coverage toggle gone.
    assert 'id="toggle-coverage"' not in html_without_pool
    # Coverage chip gone in lockstep.
    assert "glossary/coverage" not in html_without_pool
    # Trainer chip still present (always-on toggle).
    assert 'id="toggle-trainer"' in html_without_pool
    assert 'hx-get="/studio/side-panel/glossary/trainer-tint"' in html_without_pool


def test_bx15_chip_renders_outside_checkbox_label() -> None:
    """Click on a ``<label>`` dispatches to the wrapped checkbox — if
    the [?] button lived INSIDE the label, clicking the chip would
    toggle the overlay AND open the drawer. Render the chip as a
    sibling ``<button>`` next to the label, wrapped in a flex row.

    Pinned via the BX.15 marker data-attrs the renderer emits on each
    row's wrapper, so the structural intent is visible in the markup
    (not just inferable from order)."""
    html = _render_with_coverage(coverage_available=True)

    # Both rows carry their marker attribute on the wrapper <div>.
    assert "data-bx15-coverage-row" in html
    assert "data-bx15-trainer-row" in html

    # The chip's <button data-side-panel-trigger ...> appears AFTER the
    # label's </label> within the wrapper — i.e., outside the label.
    # Find the Coverage wrapper substring and verify the order.
    cov_start = html.index("data-bx15-coverage-row")
    cov_end = html.index("</div>", cov_start)
    cov_block = html[cov_start:cov_end]
    label_close_idx = cov_block.index("</label>")
    chip_idx = cov_block.index("glossary/coverage")
    assert chip_idx > label_close_idx, (
        "BX.15 — Coverage [?] chip rendered INSIDE the <label>; "
        "clicking it would toggle the checkbox. Move outside the label."
    )

    trn_start = html.index("data-bx15-trainer-row")
    trn_end = html.index("</div>", trn_start)
    trn_block = html[trn_start:trn_end]
    label_close_idx = trn_block.index("</label>")
    chip_idx = trn_block.index("glossary/trainer-tint")
    assert chip_idx > label_close_idx, (
        "BX.15 — Trainer [?] chip rendered INSIDE the <label>; "
        "clicking it would toggle the checkbox. Move outside the label."
    )
