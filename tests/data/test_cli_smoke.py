"""CLI smoke for ``quicksight-gen data`` — help + emit-only paths.

U.9 acceptance net for the data artifact group. Mirrors the shape
of ``tests/audit/test_cli_smoke.py``: minimal config + ``CliRunner``,
asserts ``--help`` lists every subcommand, asserts each verb's
``--help`` exits 0, and exercises the emit-only path of ``apply`` /
``refresh`` / ``clean`` / ``etl-example`` against the bundled
``spec_example.yaml`` to confirm the SQL stream renders without DB
or AWS infrastructure.

The ``hash`` verb has its own dedicated suite in
``test_cli_seed_l2.py`` (--lock / --check flows). The ``test`` verb
shells out to pytest + pyright; we only smoke ``--help`` for it.
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
    don't need a live DB. ``data apply`` / ``refresh`` / ``clean``
    only read the dialect setting from cfg when emitting SQL.

    Z.C — adds ``deployment_name`` + ``db_table_prefix`` (Config
    loud-fails when either is missing). ``db_table_prefix`` matches
    the bundled spec_example.yaml so the per-prefix table assertions
    (``spec_example_transactions``, etc.) below stay valid.
    """
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "aws_account_id: '111122223333'\n"
        "aws_region: us-west-2\n"
        "datasource_arn: arn:aws:quicksight:us-west-2:111122223333"
        ":datasource/ds\n"
        "deployment_name: qsgen-test\n"
        "db_table_prefix: spec_example\n"
    )
    return cfg


def test_data_help_lists_subcommands():
    runner = CliRunner()
    result = runner.invoke(main, ["data", "--help"])
    assert result.exit_code == 0, result.output
    assert "apply" in result.output
    assert "clean" in result.output
    assert "refresh" in result.output
    assert "lock" in result.output
    assert "etl-example" in result.output
    assert "test" in result.output


@pytest.mark.parametrize(
    "verb", ["apply", "clean", "refresh", "lock", "etl-example", "test"],
)
def test_data_verb_help_exits_zero(verb: str):
    runner = CliRunner()
    result = runner.invoke(main, ["data", verb, "--help"])
    assert result.exit_code == 0, result.output


def test_data_apply_emits_seed_sql_to_stdout(min_config: Path):
    """``data apply`` (no --execute) emits 90-day baseline + plant
    overlay INSERTs to stdout. Default emit path — no DB connection.

    Slow-ish (composes the full seed pipeline) but cheap enough as
    a smoke; the SQL is generated in memory."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "data", "apply",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "INSERT INTO spec_example_transactions" in result.output
    assert "INSERT INTO spec_example_daily_balances" in result.output


def test_data_apply_emits_to_file(min_config: Path, tmp_path: Path):
    """``-o FILE`` redirects the seed SQL to a file."""
    out = tmp_path / "seed.sql"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "data", "apply",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
            "-o", str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.is_file()
    sql = out.read_text()
    assert "INSERT INTO spec_example_transactions" in sql


def test_data_refresh_emits_refresh_sql_to_stdout(min_config: Path):
    """``data refresh`` (no --execute) emits REFRESH MATERIALIZED
    VIEW for every per-prefix matview to stdout. No DB connection."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "data", "refresh",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "REFRESH MATERIALIZED VIEW" in result.output
    assert "spec_example_" in result.output


def test_data_clean_emits_truncate_sql_to_stdout(min_config: Path):
    """``data clean`` (no --execute) emits TRUNCATE statements for
    the per-prefix base tables to stdout. No DB connection."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "data", "clean",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "TRUNCATE" in result.output
    assert "spec_example_transactions" in result.output
    assert "spec_example_daily_balances" in result.output


def test_data_etl_example_writes_sql_file(tmp_path: Path):
    """``data etl-example`` writes canonical INSERT examples to a file.

    Always file-based (no stdout option). Default path is
    ``demo/etl-examples.sql``; we redirect to tmp_path so the test
    doesn't pollute the repo cwd. Today the generator emits a
    K.4.2-skeleton stub for Investigation; assert the file lands +
    contains content (not the verb's exact payload)."""
    out = tmp_path / "etl-examples.sql"
    runner = CliRunner()
    result = runner.invoke(
        main, ["data", "etl-example", "-o", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert out.is_file()
    sql = out.read_text()
    assert sql.strip(), "etl-example output should not be empty"
    assert f"Wrote ETL examples to {out}" in result.output
