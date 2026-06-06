"""CH.1 + CH.2 — App2's `renderTable` ships the canonical table
component classes:

- CH.1: tabular-nums + right-align on numeric / currency cells.
- CH.2: zebra striping (alternating `bg-white` / `bg-surface-bg`
  on `<tr>`), sticky header (`sticky top-0 z-10` on `<thead>`),
  header-row fill from a theme token (`bg-surface-alt` — defined
  in input.css and AA-safe per the existing palette).

The v13.1.1 audit's Dashboards Med #2 ("dense ledger tables
undifferentiated dumps") closes when the App2 renderer carries
this contract. The Playwright table test
(`tests/js/test_render_table.py`) already exercises the dynamic
behavior; this static-source test pins the class-string contract
so a future bootstrap.js edit can't quietly drop one of them
without the unit-tier sweep failing.

Note: this test doesn't replace the Playwright test. It catches
the case where the JS module ships without one of the canonical
classes — the Playwright test would also catch it, but only at
the browser tier (gated on `QS_GEN_E2E`). The static-source
check runs everywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent.parent
BOOTSTRAP_JS = (
    REPO_ROOT
    / "src" / "recon_gen" / "common" / "html" / "assets"
    / "js" / "bootstrap.js"
)


@pytest.fixture(scope="module")
def bootstrap_source() -> str:
    return BOOTSTRAP_JS.read_text()


def test_render_table_tabular_nums_right_align_on_numeric_cells(
    bootstrap_source: str,
) -> None:
    """CH.1 — money / number cells carry `tabular-nums text-right`.
    The renderer keys off `isNumericFormat(cell.col.format)` so the
    column metadata's `format` field
    (`currency` / `number` / `date`) drives alignment."""
    assert "tabular-nums text-right" in bootstrap_source
    assert "isNumericFormat(cell.col.format)" in bootstrap_source


def test_render_table_zebra_stripes_alternate_rows(
    bootstrap_source: str,
) -> None:
    """Zebra: even rows `bg-white`, odd rows `bg-surface-bg`. The
    audit asked for alternating row backgrounds via theme tokens
    (no hardcoded palette per the existing `no-hardcoded-palette`
    lint)."""
    assert "bg-white" in bootstrap_source
    assert "bg-surface-bg" in bootstrap_source
    # Pin the conditional shape — `(i % 2 === 0 ? "bg-white" :
    # "bg-surface-bg")`. The exact string is brittle to whitespace;
    # check both halves.
    assert 'i % 2 === 0 ? "bg-white" : "bg-surface-bg"' in bootstrap_source


def test_render_table_sticky_header(bootstrap_source: str) -> None:
    """CH.2 — `<thead>` is `sticky top-0 z-10` so it stays in view
    as the operator scrolls a dense ledger table."""
    assert "sticky top-0 z-10" in bootstrap_source
    # Sticky + bg-surface-alt are co-located on the thead's class
    # string. The class is set via `.attr("class", "...")` —
    # multi-line in the source, so pin both tokens appearing
    # together in the same class declaration.
    assert "sticky top-0 z-10 bg-surface-alt" in bootstrap_source


def test_render_table_header_uses_theme_token_fill(
    bootstrap_source: str,
) -> None:
    """CH.2 — header row carries `bg-surface-alt` (theme token,
    defined in input.css). Catches a future refactor that swaps
    the fill for a hardcoded palette color."""
    # The header carries `bg-surface-alt` (both on the thead AND
    # repeated on each <th> so the cell fills extend past any gaps
    # introduced by padding).
    assert "bg-surface-alt" in bootstrap_source
    # Pin presence on the `<th>` class attribute too.
    th_attr_marker = (
        "px-3 py-2 text-left border-b border-surface-border "
        "bg-surface-alt"
    )
    assert th_attr_marker in bootstrap_source


def test_input_css_defines_surface_alt_token() -> None:
    """The `--color-surface-alt` token must exist in input.css so
    Tailwind compiles `bg-surface-alt`. The custom-color → utility
    pipeline (theme L5) reads this CSS var at theme-build time."""
    input_css = (
        REPO_ROOT
        / "src" / "recon_gen" / "common" / "html" / "assets"
        / "input.css"
    ).read_text()
    assert "--color-surface-alt:" in input_css


def test_output_css_compiles_table_primitive_classes() -> None:
    """The Tailwind-compiled output.css carries the canonical table
    primitive classes that bootstrap.js applies at runtime. Tailwind
    @source scans the JS file, so adding a class to bootstrap.js
    that input.css doesn't reference would otherwise silently drop
    out of the compile."""
    output_css = (
        REPO_ROOT
        / "src" / "recon_gen" / "common" / "html" / "assets"
        / "output.css"
    ).read_text()
    # tabular-nums + text-right + zebra fills + sticky-header utilities
    for needed in (
        "tabular-nums",
        "text-right",
        "bg-surface-alt",
        # sticky positioning composes into one class
        ".sticky",
    ):
        assert needed in output_css, (
            f"output.css missing compiled utility {needed!r} — "
            f"either Tailwind didn't @source-scan the file that "
            f"references it, or the class wasn't actually emitted"
        )
