"""BV.7 — coverage-matrix + trainer-cards anti-drift gates (BU.0 Lock 9).

Two generated artifacts on the same ``recon-gen docs export`` surface:

- ``--surface=matrix`` — the §0.5 violation coverage matrix, pinned
  against ``tests/data/_handbook_artifacts/coverage_matrix.md``.
- ``--surface=trainer-cards`` — one ``##`` block per registry kind
  carrying the canonical ``short_statement`` + ``what_to_do`` strings
  the Trainer's ``/training/`` v3 cards render, pinned against
  ``tests/data/_handbook_artifacts/trainer_cards.md``.

Future ``PLANT_REGISTRY`` edits (or typed-section catalogue edits)
that don't refresh the reference artifacts fail loudly here — that's
the whole point of Lock 9 ("docs freshness byte-identity").

Refresh recipe when one of these tests fails AND the registry/section
change is intentional:

    .venv/bin/recon-gen docs export --surface=matrix \\
        --output tests/data/_handbook_artifacts/coverage_matrix.md
    .venv/bin/recon-gen docs export --surface=trainer-cards \\
        --output tests/data/_handbook_artifacts/trainer_cards.md

then re-stage + re-commit the artifacts alongside the registry change.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from recon_gen.cli.docs import docs
from recon_gen.common.handbook.coverage_matrix import render_coverage_matrix
from recon_gen.common.handbook.trainer_cards import render_trainer_cards
from recon_gen.common.handbook.violations import (
    _slug_for,
    render_violations_handbook,
)
from recon_gen.common.html._studio_training_v2 import resolve_section
from recon_gen.common.l2.plant_registry import PLANT_REGISTRY


_REFERENCE = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "_handbook_artifacts"
    / "coverage_matrix.md"
)
_TRAINER_REFERENCE = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "_handbook_artifacts"
    / "trainer_cards.md"
)
_VIOLATIONS_REFERENCE = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "_handbook_artifacts"
    / "violations.md"
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


# -- Trainer cards surface (BV.7 Surface 3) -------------------------------


def test_trainer_cards_byte_identity() -> None:
    """Lock 9 #5 — committed artifact == fresh generator output.

    Drift here = a PLANT_REGISTRY edit OR a typed-section catalogue
    edit (short_statement / what_to_do prose touched) landed without
    a refresh of the reference artifact. Refresh recipe in the module
    docstring.
    """
    expected = _TRAINER_REFERENCE.read_text(encoding="utf-8")
    actual = render_trainer_cards()
    assert actual == expected, (
        "trainer_cards.md drift — registry or section prose changed "
        "but the reference artifact wasn't refreshed. Run:\n"
        "  .venv/bin/recon-gen docs export --surface=trainer-cards "
        "--output tests/data/_handbook_artifacts/trainer_cards.md\n"
        "then re-commit the artifact."
    )


def test_trainer_cards_one_section_per_registry_entry() -> None:
    """Sanity belt — one ``## {kind}`` block per PLANT_REGISTRY entry.

    Catches a bug where the generator silently filters / dedupes /
    re-orders. Counts ``## `` headers (not ``# ``, which is the
    preamble H1).
    """
    output = render_trainer_cards()
    section_headers = [
        line for line in output.splitlines()
        if line.startswith("## ")
    ]
    assert len(section_headers) == len(PLANT_REGISTRY), (
        f"expected {len(PLANT_REGISTRY)} ``## {{kind}}`` blocks (one "
        f"per registry entry); got {len(section_headers)}"
    )


def test_trainer_cards_every_kind_has_short_statement() -> None:
    """Every kind must resolve to a non-empty ``short_statement``.

    Reads through ``resolve_section`` directly (not the rendered
    markdown) so a parser regression that emits the field-name
    literally + an empty value still trips here. The Trainer's v3
    cards have nothing to show when short_statement is blank.
    """
    for entry in PLANT_REGISTRY:
        section = resolve_section(entry)
        assert section.short_statement.strip(), (
            f"kind {entry.kind!r} resolves to blank short_statement — "
            f"the typed-section catalogue is missing the slug "
            f"{entry.section_kind or entry.kind!r} or the parser "
            f"dropped its leading paragraph"
        )


def test_trainer_cards_every_kind_has_what_to_do() -> None:
    """Every kind must resolve to a non-empty ``what_to_do``.

    The Trainer's v3 cards lean on this string for the "now what?"
    operator guidance; blank = the card is documentation-shaped but
    useless. Reads ``resolve_section`` directly for the same reason
    as the short_statement test.
    """
    for entry in PLANT_REGISTRY:
        section = resolve_section(entry)
        assert section.what_to_do.strip(), (
            f"kind {entry.kind!r} resolves to blank what_to_do — the "
            f"typed-section catalogue's prose for section "
            f"{entry.section_kind or entry.kind!r} is missing the "
            f"``Action.`` / remediation line the parser extracts"
        )


# -- Violations handbook surface (BV.7 Surface 2) -------------------------


def test_violations_handbook_byte_identity() -> None:
    """Lock 9 #5 — committed artifact == fresh generator output.

    Drift here = a PLANT_REGISTRY edit OR a typed-section catalogue
    edit (title / short_statement / what_to_do prose touched) landed
    without a refresh of the reference artifact. Run:

        .venv/bin/recon-gen docs export --surface=violations \\
            --output tests/data/_handbook_artifacts/violations.md

    then re-commit the artifact.
    """
    expected = _VIOLATIONS_REFERENCE.read_text(encoding="utf-8")
    actual = render_violations_handbook()
    assert actual == expected, (
        "violations.md drift — registry or section prose changed but "
        "the reference artifact wasn't refreshed. Run:\n"
        "  .venv/bin/recon-gen docs export --surface=violations "
        "--output tests/data/_handbook_artifacts/violations.md\n"
        "then re-commit the artifact."
    )


def test_violations_handbook_one_section_per_registry_entry() -> None:
    """Sanity belt — one ``## {title} {#slug}`` block per
    PLANT_REGISTRY entry.

    Counts ``## `` headers that carry an ``{#...}`` anchor attr — the
    Contents H2 doesn't, so it doesn't inflate the count. Catches a
    bug where the generator silently filters / dedupes / re-orders.
    """
    output = render_violations_handbook()
    section_headers = [
        line for line in output.splitlines()
        if line.startswith("## ") and "{#" in line
    ]
    assert len(section_headers) == len(PLANT_REGISTRY), (
        f"expected {len(PLANT_REGISTRY)} ``## {{title}} {{#slug}}`` "
        f"blocks (one per registry entry); got {len(section_headers)}"
    )


def test_violations_handbook_every_anchor_reachable() -> None:
    """Every TOC bullet must resolve to a section anchor declared in
    the same document.

    Reads the TOC bullets' ``(#slug)`` fragments and the section
    headers' ``{#slug}`` attrs, then asserts the two sets agree. A
    drift here = a slug derivation bug (TOC and section headers fell
    out of sync) — same shape the BTa.1 side-panel would hit at
    runtime as a dead deep-link.
    """
    import re

    output = render_violations_handbook()
    toc_slugs = set(re.findall(r"\]\(#([\w-]+)\)", output))
    anchor_slugs = set(re.findall(r"\{#([\w-]+)\}", output))
    assert toc_slugs == anchor_slugs, (
        f"TOC slugs vs section anchors diverge — "
        f"TOC only: {sorted(toc_slugs - anchor_slugs)!r}; "
        f"anchors only: {sorted(anchor_slugs - toc_slugs)!r}"
    )
    expected_slugs = {_slug_for(entry) for entry in PLANT_REGISTRY}
    assert anchor_slugs == expected_slugs, (
        f"section anchors don't match the slug-derivation rule — "
        f"missing: {sorted(expected_slugs - anchor_slugs)!r}; "
        f"extra: {sorted(anchor_slugs - expected_slugs)!r}"
    )


def test_cli_surface_violations_to_stdout() -> None:
    """``recon-gen docs export --surface=violations`` (no --output)
    emits the handbook to stdout byte-identical to
    render_violations_handbook(). Guards the CLI wiring — a Click
    option rename or stdout-vs-file branch bug surfaces here, not in
    a downstream docs-freshness gate.
    """
    runner = CliRunner()
    result = runner.invoke(docs, ["export", "--surface=violations"])
    assert result.exit_code == 0, (
        f"CLI exit {result.exit_code} (output: {result.output!r})"
    )
    assert result.output == render_violations_handbook(), (
        "CLI stdout output diverges from render_violations_handbook() "
        "— the docs.py wiring corrupted the bytes."
    )
