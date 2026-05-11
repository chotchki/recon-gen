"""``quicksight-gen json`` — QuickSight dashboard JSON for all four apps.

Four operations:

  apply  — emit JSON for all four apps to ``out/`` (default), or
           ``--execute`` to also deploy to AWS QuickSight.
  clean  — list resources that would be deleted (default), or
           ``--execute`` to actually delete them.
  test   — pytest the per-app contract suites + pyright the builders.
  probe  — Playwright sanity walk against deployed dashboards.

The four bundled apps (investigation / executives / l1-dashboard /
l2-flow-tracing) are always operated on as a set — there's no
``--app`` filter. Per-app development was useful during M / N / O
when each iterated independently; today they ship as a bundle.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

from quicksight_gen.cli._helpers import (
    APPS,
    config_option,
    execute_option,
    l2_instance_option,
    output_option,
    resolve_l2_for_demo,
)


@click.group()
def json_() -> None:
    """QuickSight dashboard JSON for all four apps."""


# Click confuses `json` (the module name) with the subcommand. Register
# under the public name in __init__.py via add_command(name="json").
json_.name = "json"


@json_.command("apply")
@l2_instance_option()
@config_option()
@output_option(default="out")
@execute_option()
def json_apply(
    l2_instance_path: str | None, config: str,
    output: str, execute: bool,
) -> None:
    """Emit JSON for all four apps (and optionally deploy to AWS).

    Always emits to ``out/`` (or ``-o DIR``). Always operates on every
    app: investigation / executives / l1-dashboard / l2-flow-tracing.

    Default: write the four apps' JSON files (datasets, analyses,
    dashboards, theme, datasource) to the output directory. Inspect
    them; check them into git if you want; deploy them with whatever
    tool you use.

    Pass ``--execute`` to also deploy to AWS QuickSight (delete-then-
    create on every resource ID — idempotent re-runs).
    """
    from quicksight_gen.cli._app_builders import (
        _generate_executives,
        _generate_investigation,
        _generate_l1_dashboard,
        _generate_l2_flow_tracing,
    )

    out_path = Path(output)
    out_path.mkdir(parents=True, exist_ok=True)

    cfg, _instance = resolve_l2_for_demo(config, l2_instance_path)

    click.echo(f"Generating JSON for all four apps into {out_path}/...")
    _generate_investigation(config, output, l2_instance_path=l2_instance_path)
    _generate_executives(config, output, l2_instance_path=l2_instance_path)
    _generate_l1_dashboard(config, output, l2_instance_path=l2_instance_path)
    _generate_l2_flow_tracing(
        config, output, l2_instance_path=l2_instance_path,
    )

    # V.1.a — Auto-emit out/datasource.json when we're provisioning the
    # QuickSight datasource ourselves. "We own it" = `datasource_arn` was
    # *derived* from `demo_database_url` (`Config.datasource_arn_was_derived`),
    # NOT when the operator supplied an explicit `datasource_arn` — even if
    # `demo_database_url` is also set in the cfg (e.g. a prod cfg that lists
    # both a pre-existing datasource ARN and a DB URL for the demo/seed CLI):
    # an explicit ARN means a customer-managed datasource, leave it alone,
    # don't deploy a competing resource. Closes the U.8.b.3 manual-bridge
    # gap that hit during spec_example deploys: the apps' datasets reference
    # a datasource ARN the deploy step then can't find because nobody emitted
    # the matching out/datasource.json. common/deploy.py reads this file when
    # it exists and skips when it doesn't — so the absence IS the "use the
    # operator's ARN as-is" signal.
    if cfg.datasource_arn_was_derived:
        import json
        from quicksight_gen.common.datasource import build_datasource
        ds = build_datasource(cfg)
        ds_path = out_path / "datasource.json"
        ds_path.write_text(
            json.dumps(ds.to_aws_json(), indent=2), encoding="utf-8",
        )
        click.echo(f"  wrote {ds_path}")

    if not execute:
        click.echo(
            f"\nDone — JSON written to {out_path}/. "
            f"Re-run with --execute to deploy to AWS."
        )
        return

    from quicksight_gen.common.deploy import deploy

    click.echo(f"\nDeploying to AWS QuickSight...")
    exit_code = deploy(cfg, out_path, list(APPS))
    if exit_code != 0:
        raise click.ClickException(f"Deploy failed (exit code {exit_code}).")


@json_.command("clean")
@config_option()
@click.option(
    "--output-dir", "-o", "output_dir",
    type=click.Path(), default="out",
    help=(
        "Directory holding current emit output. Resources NOT in this "
        "directory get deleted (so re-running emit + clean is safe). "
        "Default: out/. Ignored when ``--all`` is passed."
    ),
)
@click.option(
    "--all", "purge_all", is_flag=True, default=False,
    help=(
        "Purge mode: ignore ``out/`` entirely and sweep EVERY resource "
        "matching the cfg's prefix scope, including the live deploy. "
        "Use to fully decommission a deploy. Pair with ``--execute``."
    ),
)
@execute_option()
def json_clean(
    config: str, output_dir: str, purge_all: bool, execute: bool,
) -> None:
    """Sweep AWS QuickSight resources tagged ManagedBy:quicksight-gen.

    Default: dry-run. Lists every resource tagged ``ManagedBy:
    quicksight-gen`` (for the active L2 instance) that is NOT in the
    current ``out/`` directory. Nothing is deleted.

    Pass ``--execute`` to actually delete. The ``out/`` directory
    drives "what's safe" — anything currently emitted there is kept;
    everything else carrying the tag goes.

    Pass ``--all`` to skip the ``out/`` carve-out entirely — every
    resource matching the cfg's prefix scope (including the live
    deploy) becomes eligible for deletion. Use to fully tear down
    a deploy. The flag is independent of ``--execute``: pair them
    to actually nuke; just ``--all`` previews what would go.
    """
    from quicksight_gen.cli._helpers import load_config
    from quicksight_gen.common.cleanup import run_cleanup

    cfg = load_config(config)
    # ``--execute`` semantics: opt in to actually delete (skip
    # confirmation prompt; the flag itself is the confirmation).
    exit_code = run_cleanup(
        cfg, Path(output_dir),
        dry_run=not execute, skip_confirm=True, purge_all=purge_all,
    )
    if exit_code != 0:
        raise click.ClickException(f"Cleanup failed (exit code {exit_code}).")


@json_.command("test")
@click.option(
    "--pytest-args", default="",
    help="Extra args passed verbatim to pytest (e.g. '-k l1_drift').",
)
@click.option(
    "--browser", is_flag=True,
    help="Also run the Playwright e2e tests under tests/e2e/.",
)
def json_test(pytest_args: str, browser: bool) -> None:
    """Run the JSON contract test suites (all four apps) + pyright."""
    targets = ["tests/json/"]
    if browser:
        targets.append("tests/e2e/")
    pytest_argv = (
        [sys.executable, "-m", "pytest", *targets, "-q"]
        + (pytest_args.split() if pytest_args else [])
    )
    pyright_argv = [
        sys.executable, "-m", "pyright",
        "src/quicksight_gen/apps/",
    ]
    failed = []
    click.echo(f"$ {' '.join(pytest_argv)}")
    if subprocess.call(pytest_argv) != 0:
        failed.append("pytest")
    click.echo(f"$ {' '.join(pyright_argv)}")
    if subprocess.call(pyright_argv) != 0:
        failed.append("pyright")
    if failed:
        raise click.ClickException(f"json test failed: {', '.join(failed)}")
    click.echo("json test: OK")


@json_.command("probe")
@config_option()
@click.option(
    "--output-dir", "-o", "output_dir",
    type=click.Path(), default="out",
    help="Directory holding the deployed-set JSON (used to find dashboard IDs).",
)
def json_probe(config: str, output_dir: str) -> None:
    """Playwright sanity walk against every deployed dashboard.

    Opens each of the four deployed dashboards via an embed URL, walks
    the sheets, and surfaces any visible 'failed to load' / spinner-
    forever / dataset-error states. Catches the silent-fail mode
    where datasets describe-cleanly but visuals stay frozen.

    No ``--execute`` here — probe is read-only by definition.
    """
    from quicksight_gen.cli._app_builders import _dashboard_id_for_app
    from quicksight_gen.cli._helpers import load_config
    from quicksight_gen.common.probe import probe_dashboard, format_report

    cfg = load_config(config)
    for app_name in APPS:
        did = _dashboard_id_for_app(app_name, output_dir)
        click.echo(
            f"Probing {did} ({app_name})... "
            f"opens headless browser, ~30-90s/dashboard"
        )
        results = probe_dashboard(
            aws_account_id=cfg.aws_account_id,
            aws_region=cfg.aws_region,
            dashboard_id=did,
        )
        click.echo(format_report(did, results))
        click.echo("")
