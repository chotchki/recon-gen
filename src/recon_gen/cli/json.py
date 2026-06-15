"""``recon-gen json`` — QuickSight dashboard JSON for all four apps.

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

from recon_gen.cli._helpers import (
    APPS,
    config_option,
    execute_option,
    l2_instance_option,
    output_option,
    resolve_l2_for_demo,
)
from recon_gen.common.config import Config


def _maybe_export_data_anchor(cfg: Config) -> None:
    """DK.4 — export ``RECON_GEN_AS_OF_ANCHOR`` from the data_anchor matview.

    Runs before app generation. When neither ``cfg.test.generator.end_date``
    nor the existing ``RECON_GEN_AS_OF_ANCHOR`` env-pin is set, query the
    ``<prefix>_data_anchor`` matview (DK.1) and export its value as
    ``RECON_GEN_AS_OF_ANCHOR``. Downstream dataset builders' calls to
    ``cfg.test.generator.as_of_frame()`` then fall through to
    ``AsOfFrame.live()`` → ``_as_of_today()`` → env-read, so every
    dataset's date-parameter default pins on the feed's actual latest
    moment instead of wall-clock today.

    No-op when either the cfg pin or the env pin is already present —
    preserves operator-pin + chain-determinism semantics.

    Cold-DB / empty-matview case: log a warning, leave the env unset.
    Downstream AsOfFrame.live() falls through to date.today() — the
    pre-DK behavior, which the operator probably wants to fix by
    running ``data apply --execute`` first. DK.7.e2e exercises this
    path to verify the warning is loud enough.

    Connection failure (no DB / wrong host / matview missing on legacy
    deploy): same as cold-DB — warn, fall through. Treats the absence
    of the DK.1 matview as the legacy case rather than an error so
    pre-DK deploys don't break on the v14.4.0 upgrade.
    """
    from recon_gen.common.as_of_frame import _query_data_anchor  # noqa: PLC0415
    from recon_gen.common.db import connect_demo_db  # noqa: PLC0415
    from recon_gen.common.env_keys import RECON_GEN_AS_OF_ANCHOR  # noqa: PLC0415

    # Operator pin via cfg yaml wins — DK.3's path 2.
    if cfg.test.generator.end_date is not None:
        return
    # Existing env-pin wins — chain-determinism / RECON_GEN_AS_OF_ANCHOR
    # override path. Caller already set it; we don't second-guess.
    if RECON_GEN_AS_OF_ANCHOR.get_or_none() is not None:
        return
    # Data-derived path.
    try:
        conn = connect_demo_db(cfg)
    except Exception as exc:  # noqa: BLE001 — DB-connect failure → fall through to live(wall-clock); not the right time to crash json apply
        click.echo(
            f"warning: could not connect to demo DB for data-anchor "
            f"resolution ({exc!r}); falling back to live(wall-clock). "
            f"Dashboards may render blank for the default date window "
            f"if the feed is stale.",
            err=True,
        )
        return
    try:
        anchor = _query_data_anchor(conn, cfg.db.table_prefix)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — close on a half-broken conn must not mask the original error
            pass
    if anchor is None:
        click.echo(
            f"warning: <prefix>_data_anchor matview is empty or absent "
            f"({cfg.db.table_prefix}_data_anchor); falling back to "
            f"live(wall-clock). Run `recon-gen data apply --execute` + "
            f"`recon-gen data refresh --execute` to populate the feed.",
            err=True,
        )
        return
    # Export so every downstream subprocess inherits the same anchor.
    # (For in-process callers, ``RECON_GEN_AS_OF_ANCHOR.get_or_none()``
    # re-reads ``os.environ`` on each call — no module caching to bust.)
    import os  # noqa: PLC0415
    from recon_gen.common.env_keys import RECON_GEN_AS_OF_ANCHOR_SOURCE  # noqa: PLC0415
    os.environ[RECON_GEN_AS_OF_ANCHOR.name] = anchor.isoformat()
    # DK.5.bullets — marker for Info-sheet deploy-stamp source attribution.
    # Distinguishes "operator pinned env" from "DK.4 auto-derived from matview".
    os.environ[RECON_GEN_AS_OF_ANCHOR_SOURCE.name] = "data_anchor"
    click.echo(
        f"as_of pinned at {anchor.isoformat()} from "
        f"{cfg.db.table_prefix}_data_anchor (DK.4)."
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
    from recon_gen.cli._app_builders import (
        _generate_executives,
        _generate_investigation,
        _generate_l1_dashboard,
        _generate_l2_flow_tracing,
    )

    out_path = Path(output)
    out_path.mkdir(parents=True, exist_ok=True)

    cfg, _instance = resolve_l2_for_demo(config, l2_instance_path)

    # DK.4 — data-derived as_of resolution. When the operator hasn't
    # pinned an explicit ``cfg.test.generator.end_date`` AND hasn't set
    # ``RECON_GEN_AS_OF_ANCHOR`` for chain determinism, query the
    # ``<prefix>_data_anchor`` matview (DK.1) to derive the build-time
    # anchor from the actual feed state. Export as
    # ``RECON_GEN_AS_OF_ANCHOR`` so every downstream dataset builder's
    # ``cfg.test.generator.as_of_frame()`` call inherits the value via
    # ``AsOfFrame.live()`` → ``_as_of_today()`` → env-read shape (no
    # callsite churn — DK.3's deprecation comment marks the now-
    # virtually-dead live(wall-clock) fallback). Cold-DB / empty-matview
    # case: warn but continue (the bare live() fallback fires; operator
    # gets the pre-DK behavior they had before). DK.7.e2e will exercise
    # this path with an empty-feed e2e to verify the warning is loud
    # enough that a real prod-stale-feed slip wouldn't go silent.
    _maybe_export_data_anchor(cfg)

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
    # DE.5 step 6 — `datasource_arn_was_derived` sentinel removed.
    # mode=create is the "we own it" case (post-DE.0 lock 3).
    if cfg.aws.datasource.mode == "create":
        import json
        from recon_gen.common.datasource import build_datasource
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

    from recon_gen.common.deploy import deploy

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
    """Sweep AWS QuickSight resources tagged ManagedBy:recon-gen.

    Default: dry-run. Lists every resource tagged ``ManagedBy:
    recon-gen`` (for the active L2 instance) that is NOT in the
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
    from recon_gen.cli._helpers import load_config
    from recon_gen.common.cleanup import run_cleanup

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
        "src/recon_gen/apps/",
    ]
    failed: list[str] = []
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
    from recon_gen.cli._app_builders import _dashboard_id_for_app
    from recon_gen.cli._helpers import load_config
    from recon_gen.common.probe import probe_dashboard, format_report

    cfg = load_config(config)
    for app_name in APPS:
        did = _dashboard_id_for_app(app_name, output_dir)
        click.echo(
            f"Probing {did} ({app_name})... "
            f"opens headless browser, ~30-90s/dashboard"
        )
        results = probe_dashboard(
            aws_account_id=cfg.aws.account_id,
            aws_region=cfg.aws.region,
            dashboard_id=did,
        )
        click.echo(format_report(did, results))
        click.echo("")
