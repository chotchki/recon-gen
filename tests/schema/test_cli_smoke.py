"""CLI smoke for ``quicksight-gen schema`` — help + emit-only paths.

U.9 acceptance net for the schema artifact group. Mirrors the shape
of ``tests/audit/test_cli_smoke.py``: minimal config + ``CliRunner``,
asserts ``--help`` lists every subcommand, asserts each verb's
``--help`` exits 0, and exercises the emit-only path of ``apply`` /
``clean`` against the bundled ``spec_example.yaml`` to confirm the
DDL / DROP SQL stream renders without DB or AWS infrastructure.

The ``test`` verb shells out to pytest + pyright on the project; we
only smoke ``--help`` for it (running the subprocess from inside a
pytest run would recurse).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from quicksight_gen.cli import main


_FIXTURES = Path(__file__).parent.parent / "l2"
_SPEC_EXAMPLE = _FIXTURES / "spec_example.yaml"


@pytest.fixture
def min_config(tmp_path: Path) -> Path:
    """Minimal config.yaml — no demo_database_url; emit-only paths
    don't need a live DB. ``schema apply`` / ``clean`` only read the
    dialect setting from cfg when emitting SQL."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "aws_account_id: '111122223333'\n"
        "aws_region: us-west-2\n"
        "datasource_arn: arn:aws:quicksight:us-west-2:111122223333"
        ":datasource/ds\n"
        "deployment_name: qsgen-test\n"
        "db_table_prefix: test\n"
    )
    return cfg


def test_schema_help_lists_subcommands():
    runner = CliRunner()
    result = runner.invoke(main, ["schema", "--help"])
    assert result.exit_code == 0, result.output
    assert "apply" in result.output
    assert "clean" in result.output
    assert "test" in result.output


@pytest.mark.parametrize("verb", ["apply", "clean", "test"])
def test_schema_verb_help_exits_zero(verb: str):
    runner = CliRunner()
    result = runner.invoke(main, ["schema", verb, "--help"])
    assert result.exit_code == 0, result.output


def test_schema_apply_emits_ddl_to_stdout(min_config: Path):
    """``schema apply`` (no --execute) emits CREATE TABLE / matview
    DDL for the L2 instance to stdout. Default emit path — no DB
    connection."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "schema", "apply",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "CREATE TABLE" in result.output
    assert "test_transactions" in result.output
    assert "test_daily_balances" in result.output


def test_schema_apply_emits_to_file(min_config: Path, tmp_path: Path):
    """``-o FILE`` redirects the DDL to a file."""
    out = tmp_path / "schema.sql"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "schema", "apply",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
            "-o", str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.is_file()
    sql = out.read_text()
    assert "CREATE TABLE" in sql
    assert "test_transactions" in sql


def test_schema_clean_emits_drops_to_stdout(min_config: Path):
    """``schema clean`` (no --execute) emits DROP statements for the
    L2 instance to stdout. Default emit path — no DB connection."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "schema", "clean",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "DROP" in result.output
    assert "test_transactions" in result.output
