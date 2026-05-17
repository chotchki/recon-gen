"""CLI smoke for ``recon-gen json`` — help + emit-only paths.

U.9 acceptance net for the json artifact group. Mirrors the shape
of ``tests/audit/test_cli_smoke.py``: minimal config + ``CliRunner``,
asserts ``--help`` lists every subcommand, asserts each verb's
``--help`` exits 0, and exercises the emit-only path of ``apply``
against the bundled ``spec_example.yaml`` to confirm the four-app
JSON bundle renders without DB or AWS infrastructure.

Two verbs only get ``--help`` smoke and not an emit-only run:

- ``clean`` — even the dry-run path opens a boto3 QuickSight client
  to enumerate ManagedBy-tagged resources; we'd need real AWS
  credentials to make it past ``run_cleanup``'s first AWS call.
- ``probe`` — Playwright walk against deployed dashboards; needs
  AWS + a deployed dashboard to do anything.

The ``test`` verb shells out to pytest + pyright; only ``--help``
smoke for it (running the subprocess from inside a pytest run
would recurse).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from recon_gen.cli import main


_FIXTURES = Path(__file__).parent.parent / "l2"
_SPEC_EXAMPLE = _FIXTURES / "spec_example.yaml"


@pytest.fixture
def min_config(tmp_path: Path) -> Path:
    """Minimal config.yaml — no demo_database_url; the ``apply``
    emit-only path doesn't need a DB. The four-app builders read
    aws_account_id / aws_region / deployment_name / db_table_prefix /
    datasource_arn / dialect off cfg."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "aws_account_id: '111122223333'\n"
        "aws_region: us-west-2\n"
        # Z.C — required cfg fields.
        "deployment_name: recon-cli-smoke\n"
        "db_table_prefix: spec_example\n"
        "datasource_arn: arn:aws:quicksight:us-west-2:111122223333"
        ":datasource/ds\n"
    )
    return cfg


def test_json_help_lists_subcommands():
    runner = CliRunner()
    result = runner.invoke(main, ["json", "--help"])
    assert result.exit_code == 0, result.output
    assert "apply" in result.output
    assert "clean" in result.output
    assert "test" in result.output
    assert "probe" in result.output


@pytest.mark.parametrize("verb", ["apply", "clean", "test", "probe"])
def test_json_verb_help_exits_zero(verb: str):
    runner = CliRunner()
    result = runner.invoke(main, ["json", verb, "--help"])
    assert result.exit_code == 0, result.output


def test_json_apply_emits_all_four_apps(
    min_config: Path, tmp_path: Path,
):
    """``json apply -o DIR`` (no --execute) writes the four-app JSON
    bundle to DIR. Default emit path — no AWS deploy.

    Asserts every app's analysis + dashboard JSON lands plus the
    shared theme + a representative dataset under datasets/."""
    out = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "json", "apply",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
            "-o", str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.is_dir()
    # Shared theme.json — emitted whenever the L2 has a theme block.
    assert (out / "theme.json").is_file()
    # Per-app analysis + dashboard JSON files.
    for app_slug in (
        "investigation", "executives",
        "l1-dashboard", "l2-flow-tracing",
    ):
        assert (out / f"{app_slug}-analysis.json").is_file(), (
            f"missing {app_slug}-analysis.json under {out}"
        )
        assert (out / f"{app_slug}-dashboard.json").is_file(), (
            f"missing {app_slug}-dashboard.json under {out}"
        )
    # Datasets directory populated.
    datasets_dir = out / "datasets"
    assert datasets_dir.is_dir()
    dataset_files = list(datasets_dir.glob("*.json"))
    assert dataset_files, (
        f"expected at least one dataset JSON under {datasets_dir}"
    )
