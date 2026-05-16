"""Persona-cleanliness test for the audit PDF text payload (U.3.g).

Mirrors ``tests/data/test_seed_persona_clean.py`` but for the audit
report. Generates a PDF against the persona-neutral SPEC fixture
(``tests/l2/spec_example.yaml``), extracts text via pypdf, and asserts
that ZERO persona literals (Sasquatch / SNB / FRB / Bigfoot / etc.)
appear anywhere in the rendered text.

If anything leaks, the audit renderer smuggled an L3 persona literal
into L1-or-L2 prose somewhere — typically a hardcoded label or
docstring that should have come from ``HandbookVocabulary`` or the
L2 instance's ``persona:`` block.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from quicksight_gen.cli import main


_FIXTURES = Path(__file__).parent.parent / "l2"
_SPEC_EXAMPLE = _FIXTURES / "spec_example.yaml"


# Same persona blocklist shape as tests/data/test_seed_persona_clean.py
# (case-insensitive match). When a new persona/fixture lands, add its
# distinctive tokens here so the audit text payload stays clean.
_PERSONA_BLOCKLIST = (
    "sasquatch",
    "bigfoot",
    "yeti",
    "cascadia",
    "juniper",
    "snb",
    "frb",
    "farmers exchange",
)


@pytest.fixture
def min_config(tmp_path: Path) -> Path:
    """Same minimal config used by test_cli_smoke (no demo_database_url —
    audit renders in placeholder mode, no DB queries needed for this test).
    """
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "aws_account_id: '111122223333'\n"
        "aws_region: us-west-2\n"
        "deployment_name: qsgen-test\n"
        "db_table_prefix: spec_example\n"
        "datasource_arn: arn:aws:quicksight:us-west-2:111122223333"
        ":datasource/ds\n"
    )
    return cfg


def test_audit_pdf_carries_no_persona_literals(
    min_config: Path, tmp_path: Path,
) -> None:
    """Audit PDF generated against persona-neutral SPEC must not leak
    any Sasquatch / SNB / FRB / etc. token in its text payload.

    Runs with --execute so we exercise the actual reportlab render
    path (the markdown emit covers different code paths and is also
    persona-clean by construction).
    """
    out = tmp_path / "report.pdf"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit", "apply",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
            "-o", str(out),
            "--execute",
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.is_file()

    from pypdf import PdfReader
    reader = PdfReader(str(out))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    haystack = text.lower()
    leaks = [needle for needle in _PERSONA_BLOCKLIST if needle in haystack]
    assert not leaks, (
        f"Audit PDF (against spec_example) carries persona literal(s) "
        f"{leaks}. Either the audit renderer hardcodes a persona token, "
        f"or _PERSONA_BLOCKLIST needs an exception-allowlist entry. "
        f"Search the renderer for the leaked string and either remove "
        f"it or thread it through HandbookVocabulary / persona block."
    )


def test_audit_markdown_carries_no_persona_literals(
    min_config: Path,
) -> None:
    """Same check on the markdown emit path.

    Markdown shouldn't go through reportlab so any leak there is
    purely a hardcoded prose problem.
    """
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit", "apply",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
        ],
    )
    assert result.exit_code == 0, result.output
    haystack = result.output.lower()
    leaks = [needle for needle in _PERSONA_BLOCKLIST if needle in haystack]
    assert not leaks, (
        f"Audit markdown (against spec_example) carries persona "
        f"literal(s) {leaks}. Either the audit renderer hardcodes a "
        f"persona token, or _PERSONA_BLOCKLIST needs an "
        f"exception-allowlist entry."
    )
