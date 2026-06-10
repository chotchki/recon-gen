"""BV.7 — coverage-matrix anti-drift gate (BU.0 Lock 9).

The §0.5 violation coverage matrix is now a generated artifact emitted
by ``recon-gen docs export --surface=matrix``. This test asserts the
live generator output is byte-identical to the committed reference
under ``tests/data/_handbook_artifacts/coverage_matrix.md``.

Future ``PLANT_REGISTRY`` edits that don't refresh the reference
artifact fail loudly here — that's the whole point of Lock 9
("docs freshness byte-identity").

Refresh recipe when this test fails AND the registry change is
intentional:

    .venv/bin/recon-gen docs export --surface=matrix \\
        --output tests/data/_handbook_artifacts/coverage_matrix.md

then re-stage + re-commit the artifact alongside the registry change.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from recon_gen.cli.docs import docs
from recon_gen.common.handbook.coverage_matrix import render_coverage_matrix
from recon_gen.common.l2.plant_registry import PLANT_REGISTRY


_REFERENCE = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "_handbook_artifacts"
    / "coverage_matrix.md"
)


def test_coverage_matrix_byte_identity() -> None:
    """Lock 9 #5 — committed artifact == fresh generator output.

    Any drift here means a PLANT_REGISTRY edit landed without a
    refresh of the reference artifact. See module docstring for the
    refresh recipe.
    """
    expected = _REFERENCE.read_text(encoding="utf-8")
    actual = render_coverage_matrix()
    assert actual == expected, (
        "coverage_matrix.md drift — registry changed but the reference "
        "artifact wasn't refreshed. Run:\n"
        "  .venv/bin/recon-gen docs export --surface=matrix "
        "--output tests/data/_handbook_artifacts/coverage_matrix.md\n"
        "then re-commit the artifact."
    )


def test_coverage_matrix_row_per_registry_entry() -> None:
    """Sanity belt — one table row per PLANT_REGISTRY entry.

    Catches a bug where the generator filters / dedupes / re-orders
    silently. Counts the body rows (skip the H1 / preamble / blank
    lines / table header + separator).
    """
    output = render_coverage_matrix()
    body_rows = [
        line for line in output.splitlines()
        if line.startswith("| ") and not line.startswith("| ---")
        and not line.startswith("| Family")  # header row
    ]
    assert len(body_rows) == len(PLANT_REGISTRY), (
        f"expected {len(PLANT_REGISTRY)} body rows (one per registry "
        f"entry); got {len(body_rows)}"
    )


def test_coverage_matrix_every_row_has_typed_source() -> None:
    """Lock 11 — every kind must name a typed source class.

    Belt-and-suspenders on top of the byte-identity check: if a
    category-without-an-SoT-class slipped in, the **Typed-source class**
    column would be empty here. Caught early.
    """
    output = render_coverage_matrix()
    for line in output.splitlines():
        if not line.startswith("|"):
            continue
        if line.startswith("| Family") or line.startswith("| ---"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        assert cells, f"row parsed to empty cells: {line!r}"
        assert cells[-1], (
            f"row missing typed-source-class cell: {line!r} "
            f"(BU.0 Lock 11 — every kind needs a typed SoT)"
        )
        assert "Section" in cells[-1], (
            f"typed-source cell {cells[-1]!r} doesn't look like a "
            f"`ClassName[\"key\"]` reference (row: {line!r})"
        )


def test_cli_surface_matrix_to_stdout() -> None:
    """``recon-gen docs export --surface=matrix`` (no --output) emits
    the matrix to stdout byte-identical to render_coverage_matrix().

    Guards the CLI wiring — a Click option rename or stdout-vs-file
    branch bug surfaces here, not in a downstream docs-freshness gate.
    """
    runner = CliRunner()
    result = runner.invoke(docs, ["export", "--surface=matrix"])
    assert result.exit_code == 0, (
        f"CLI exit {result.exit_code} (output: {result.output!r})"
    )
    # ``click.echo(..., nl=False)`` emits the markdown verbatim; the
    # render already ends with a trailing newline.
    assert result.output == render_coverage_matrix(), (
        "CLI stdout output diverges from render_coverage_matrix() — "
        "the docs.py wiring corrupted the bytes."
    )


def test_cli_surface_matrix_to_file(tmp_path: Path) -> None:
    """``--surface=matrix --output PATH`` writes the matrix to PATH
    byte-identical to render_coverage_matrix(). Parallel to the
    stdout case so a divergence between the two write paths surfaces
    immediately."""
    target = tmp_path / "out.md"
    runner = CliRunner()
    result = runner.invoke(
        docs,
        ["export", "--surface=matrix", "--output", str(target)],
    )
    assert result.exit_code == 0, (
        f"CLI exit {result.exit_code} (output: {result.output!r})"
    )
    assert target.read_text(encoding="utf-8") == render_coverage_matrix()
