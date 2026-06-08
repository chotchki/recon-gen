"""CN.6 — Sheet.handbook_path liveness gate.

Walk every app's tree, collect every Sheet, and verify each
``Sheet.handbook_path`` value resolves to an actual file under
``docs/handbook/<path>.md``. Companion to the
``test_field_spec_handbook.py`` liveness gate which covers
FieldSpec.handbook_path on the Studio editor side.

The two gates together guarantee the operator never clicks a ``?``
button that 404s — whether the button is on a dashboard sheet
header or an L2 editor form field.

Coverage expectation: ≥30 wired Sheets (one per app sheet across
L1/L2FT/Investigation/Executives — wired in CN.5). Below this,
sheets have been silently un-wired.
"""

from __future__ import annotations

from pathlib import Path

from recon_gen.common.handbook.diagrams import _build_app
from recon_gen.common.tree import App, Sheet


def _all_sheets(app: App) -> tuple[Sheet, ...]:
    """Collect every Sheet on an App's analysis. Dashboards re-publish
    the Analysis's sheet tree by ref (Dashboard.analysis == App.analysis),
    so walking analysis.sheets covers everything."""
    assert app.analysis is not None, (
        f"_build_app returned {app.name!r} with no analysis attached"
    )
    return tuple(app.analysis.sheets)


def test_all_sheet_handbook_paths_resolve_to_files() -> None:
    """Every Sheet.handbook_path across all four apps points at an
    actual file under ``docs/handbook/<path>.md``."""
    repo_root = Path(__file__).resolve().parents[2]
    handbook_root = repo_root / "docs" / "handbook"

    all_sheets: list[tuple[str, Sheet]] = []
    for app_name in ("l1_dashboard", "l2_flow_tracing",
                     "investigation", "executives"):
        app = _build_app(app_name)
        all_sheets.extend((app_name, sheet) for sheet in _all_sheets(app))

    wired = [(app, s) for app, s in all_sheets if s.handbook_path]
    # CN.5 wired all 30 sheets — anything substantially lower means
    # the registry has been un-wired.
    assert len(wired) >= 30, (
        f"expected ≥30 wired Sheet.handbook_path entries, got {len(wired)}"
    )

    missing: list[str] = []
    for app_name, sheet in wired:
        candidate = handbook_root / f"{sheet.handbook_path}.md"
        if not candidate.is_file():
            missing.append(
                f"{app_name}::{sheet.sheet_id} -> {candidate}"
            )
    assert not missing, "missing handbook pages:\n" + "\n".join(missing)
