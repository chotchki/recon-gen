"""CN.5a — FieldSpec.handbook_path wiring tests.

Three layers:

1. ``FieldSpec`` accepts a ``handbook_path`` value and defaults to None.
2. The ``_handbook_chip_html`` helper emits the dashboard-class anchor
   when populated and "" when not — same hook the dashboard ``?``
   button uses, so a single ``bootstrap.js`` handler services both.
3. Liveness gate: every wired FieldSpec.handbook_path across the
   editor routes resolves to an actual file under ``src/recon_gen/docs/_handbook_per_sheet/``.
"""

from __future__ import annotations

from pathlib import Path

from recon_gen.common.html._studio_editor_routes import (
    _ACCOUNT_FIELDS,
    _ACCOUNT_TEMPLATE_FIELDS,
    _CHAIN_FIELDS,
    _RAIL_FIELDS,
    _TRANSFER_TEMPLATE_FIELDS,
    FieldSpec,
    _handbook_chip_html,
)
from recon_gen.common.ids import HandbookPath


def test_field_spec_handbook_path_defaults_to_none() -> None:
    spec = FieldSpec(name="x", label="X", helper="", kind="text")
    assert spec.handbook_path is None


def test_field_spec_handbook_path_accepts_value() -> None:
    spec = FieldSpec(
        name="x", label="X", helper="", kind="text",
        handbook_path=HandbookPath("l2-editor/transfer-key"),
    )
    assert spec.handbook_path == "l2-editor/transfer-key"


def test_handbook_chip_html_empty_when_unset() -> None:
    spec = FieldSpec(name="x", label="X", helper="", kind="text")
    assert _handbook_chip_html(spec) == ""


def test_handbook_chip_html_emits_anchor_when_set() -> None:
    spec = FieldSpec(
        name="x", label="X", helper="", kind="text",
        handbook_path=HandbookPath("l2-editor/transfer-key"),
    )
    html_out = _handbook_chip_html(spec)
    # Same hook the dashboard ? button uses — must match the JS
    # delegation selector (`.handbook-help-button` +
    # `data-handbook-path=...`).
    assert 'class="handbook-help-button' in html_out
    assert 'data-handbook-path="l2-editor/transfer-key"' in html_out
    assert 'href="/handbook/l2-editor/transfer-key"' in html_out
    assert ">?</a>" in html_out


def test_all_wired_handbook_paths_resolve_to_files() -> None:
    """Liveness gate — every FieldSpec wired with a handbook_path
    points at an actual file under ``src/recon_gen/docs/_handbook_per_sheet/<path>.md``.
    Catches typos + dangling references at unit-test time, before
    the operator clicks the ``?`` button and gets a 404."""
    repo_root = Path(__file__).resolve().parents[2]
    handbook_root = repo_root / "src" / "recon_gen" / "docs" / "_handbook_per_sheet"
    all_specs: tuple[FieldSpec, ...] = (
        _ACCOUNT_FIELDS
        + _ACCOUNT_TEMPLATE_FIELDS
        + _RAIL_FIELDS
        + _CHAIN_FIELDS
        + _TRANSFER_TEMPLATE_FIELDS
    )
    wired = [s for s in all_specs if s.handbook_path]
    # Defense — CN.5a wired at least 7 paths. Below this, the
    # registry has been silently un-wired.
    assert len(wired) >= 7, (
        f"expected ≥7 wired FieldSpec.handbook_path entries, got {len(wired)}"
    )
    missing: list[str] = []
    for spec in wired:
        candidate = handbook_root / f"{spec.handbook_path}.md"
        if not candidate.is_file():
            missing.append(f"{spec.name} -> {candidate}")
    assert not missing, "missing handbook pages:\n" + "\n".join(missing)
