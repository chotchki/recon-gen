"""``quicksight-gen docs`` — mkdocs handbook site for an L2 instance.

Six operations:

  apply       — build the site to ``site/`` (wraps ``mkdocs build``).
  serve       — live-reload preview (wraps ``mkdocs serve``).
  clean       — ``rm -rf site/``.
  test        — pytest the docs gates (link sweep + persona neutrality).
  export      — extract mkdocs source for hand-build (legacy ``export docs``).
  screenshot  — capture deployed dashboards to PNG (legacy ``export screenshots``).

No ``--execute`` here — building a static site to a directory isn't
a destructive side effect. The "emit" and the "do it" are the same
operation.

The ``--l2`` flag is honored via the ``QS_DOCS_L2_INSTANCE`` env var
that ``main.py`` reads at mkdocs-macros define-env time.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import click

from quicksight_gen.cli._helpers import l2_instance_option, load_config


# ``_REPO_ROOT`` was the cwd for ``mkdocs build`` pre-restructure when
# ``mkdocs.yml`` lived at the repo root. Now the config ships inside
# the package and mkdocs is invoked with ``-f <bundled mkdocs.yml>``,
# which resolves all docs paths relative to the config file regardless
# of cwd. Kept around because ``docs test`` still cwds here to run
# pytest against ``tests/docs/`` — which only exists in dev mode.
_REPO_ROOT = Path(__file__).resolve().parents[3]

# Bundled mkdocs.yml — same package-relative path in dev
# (``<repo>/src/quicksight_gen/mkdocs.yml``) and installed mode
# (``<site-packages>/quicksight_gen/mkdocs.yml``). One level up from
# the actual ``docs/`` content so ``docs_dir: docs`` is a sibling
# (mkdocs rejects ``docs_dir: .``).
_BUNDLED_MKDOCS_YML = Path(__file__).resolve().parent.parent / "mkdocs.yml"


def build_docs_site(
    l2_instance_path: str | None,
    output_dir: str | Path,
    *,
    strict: bool = True,
    config: Path | None = None,
) -> int:
    """Run ``mkdocs build`` (the bundled config) into ``output_dir``.

    Returns the mkdocs exit code (0 = success). Threads the active L2
    instance via ``QS_DOCS_L2_INSTANCE`` and sets ``cwd`` to the bundled
    config's directory so mkdocs-macros's ``include_dir: docs/_macros``
    resolves (X.2.s.1 — mkdocs-macros joins ``include_dir`` against the
    process cwd, not the config-file dir). ``config`` overrides the
    config file (``docs apply --portable`` passes its synthesized
    ``mkdocs.portable.yml``); the default is the bundled ``mkdocs.yml``.
    ``output_dir`` is resolved to absolute so cwd doesn't affect where
    the site lands.

    Shared by ``docs apply`` and by ``serve app2 apply`` (which builds
    the site into a tempdir and embeds it at ``/docs`` — X.2.i).
    """
    env = os.environ.copy()
    if l2_instance_path is not None:
        env["QS_DOCS_L2_INSTANCE"] = str(Path(l2_instance_path).resolve())
    cmd = [
        sys.executable, "-m", "mkdocs", "build",
        "-f", str(config or _BUNDLED_MKDOCS_YML),
        "-d", str(Path(output_dir).resolve()),
    ]
    if strict:
        cmd.append("--strict")
    return subprocess.call(cmd, env=env, cwd=_BUNDLED_MKDOCS_YML.parent)


def _bake_portable_wasm(output_dir: Path) -> None:
    """Inline the wasm-graphviz module so diagrams render via file://.

    Modern browsers (Chrome / Firefox / Safari) refuse ES module
    imports from ``file://`` URLs for security. The default
    ``qs-graphviz-wasm.js`` does ``await import("./wasm-graphviz/index.js")``
    which works fine over HTTP but rejects with a CORS-shaped error
    when the page is opened by double-click — diagrams stay as empty
    ``<template>`` placeholders.

    For ``--portable`` builds we post-process the rendered ``site/``:
    read the vendored wasm bundle, strip its ES ``export``, wrap the
    bundle + renderer logic in one IIFE so ``Graphviz`` is available
    in lexical scope (no module fetch), and overwrite
    ``stylesheets/qs-graphviz-wasm.js``. The ``wasm-graphviz/`` dir
    is then unused and removed to keep the portable bundle lean.

    Source tree stays untouched — only the rendered ``output_dir/``
    gets the inline form. HTTP-served builds (default ``docs apply``,
    Pages workflow) keep the dynamic-import path.
    """
    import re

    style_dir = output_dir / "stylesheets"
    wasm_path = style_dir / "wasm-graphviz" / "index.js"
    qs_path = style_dir / "qs-graphviz-wasm.js"

    if not wasm_path.exists():
        raise click.ClickException(
            f"--portable post-process: expected {wasm_path} to exist after "
            f"mkdocs build. Did the docs build skip the stylesheets/ tree?"
        )
    if not qs_path.exists():
        raise click.ClickException(
            f"--portable post-process: expected {qs_path} to exist after "
            f"mkdocs build."
        )

    wasm_text = wasm_path.read_text(encoding="utf-8")
    # The vendored bundle ends with `...;export{Ft as Graphviz};` and a
    # sourceMappingURL pragma. Capture the local name (Ft today; the
    # variable is renamed across upstream releases) so we can rebind it
    # to ``Graphviz`` after the IIFE wrap.
    export_re = re.compile(r"export\s*\{\s*(\w+)\s+as\s+Graphviz\s*\}\s*;?")
    m = export_re.search(wasm_text)
    if m is None:
        raise click.ClickException(
            f"--portable post-process: couldn't find "
            f"`export {{<name> as Graphviz}}` at end of {wasm_path}. "
            f"The vendored upstream may have changed shape — re-vendor "
            f"or update the regex in cli/docs.py::_bake_portable_wasm."
        )
    local_name = m.group(1)
    wasm_body = wasm_text[: m.start()].rstrip()
    wasm_body = re.sub(
        r"//#\s*sourceMappingURL=.*$", "", wasm_body, flags=re.MULTILINE,
    ).rstrip()

    # The renderer's `getRenderer()` does
    #   const mod = await import("./wasm-graphviz/index.js");
    #   return await mod.Graphviz.load();
    # Swap to a direct reference now that Graphviz is lexically in scope.
    qs_text = qs_path.read_text(encoding="utf-8")
    import_re = re.compile(
        r'const\s+mod\s*=\s*await\s+import\s*\(\s*"\.\/wasm-graphviz\/index\.js"\s*\)\s*;\s*'
        r'return\s+await\s+mod\.Graphviz\.load\s*\(\s*\)\s*;',
        re.DOTALL,
    )
    if not import_re.search(qs_text):
        raise click.ClickException(
            f"--portable post-process: couldn't find the dynamic-import "
            f"call in {qs_path}. Was qs-graphviz-wasm.js refactored?"
        )
    qs_text = import_re.sub("return await Graphviz.load();", qs_text)

    portable_text = (
        "/* docs apply --portable: wasm-graphviz inlined so diagrams\n"
        " * render under file:// (modern browsers block ES module\n"
        " * imports from file:// for security). HTTP-served builds keep\n"
        " * the dynamic-import path; this transform only runs on the\n"
        " * rendered site/. */\n"
        "(() => {\n"
        + wasm_body
        + "\n"
        + f"const Graphviz = {local_name};\n"
        + qs_text
        + "\n})();\n"
    )
    qs_path.write_text(portable_text, encoding="utf-8")
    shutil.rmtree(style_dir / "wasm-graphviz")


# Per CLI app slug: (module path, builder function, output-subdir slug).
# The output-subdir slug is the short form (l1, l2ft, inv, exec) that
# matches the existing `docs/walkthroughs/screenshots/<short>/` convention
# already wired into handbook + walkthrough markdown refs. Lazy-imported
# in _build_app_for_screenshots so the CLI loads quickly when this command
# isn't invoked.
SCREENSHOT_APPS: dict[str, tuple[str, str, str]] = {
    "l1-dashboard": (
        "quicksight_gen.apps.l1_dashboard.app", "build_l1_dashboard_app",
        "l1",
    ),
    "l2-flow-tracing": (
        "quicksight_gen.apps.l2_flow_tracing.app", "build_l2_flow_tracing_app",
        "l2ft",
    ),
    "investigation": (
        "quicksight_gen.apps.investigation.app", "build_investigation_app",
        "inv",
    ),
    "executives": (
        "quicksight_gen.apps.executives.app", "build_executives_app",
        "exec",
    ),
}


# Per-app DateTimeParam names that the screenshots CLI sets when
# --date-from / --date-to are passed. The seed anchors at date(2030,1,1)
# but the dashboards default to "rolling 7 days back from today" — so
# without these overrides the captured PNGs render "no data" on every
# date-filtered sheet.
_APP_DATE_PARAMS: dict[str, dict[str, list[str]]] = {
    "l1-dashboard": {
        "from": ["pL1DateStart"],
        "to": ["pL1DateEnd", "pL1DsBalanceDate"],  # DS picker = single day
    },
    "l2-flow-tracing": {
        "from": ["pL2ftDateStart", "pL2ftChainsDateStart", "pL2ftTtDateStart"],
        "to": ["pL2ftDateEnd", "pL2ftChainsDateEnd", "pL2ftTtDateEnd"],
    },
    "investigation": {
        "from": [], "to": [],  # No date params
    },
    "executives": {
        "from": ["pExecDateStart"],
        "to": ["pExecDateEnd"],
    },
}


def parse_viewport(text: str) -> tuple[int, int]:
    """Parse a ``WxH`` string into ``(width, height)`` integers."""
    parts = text.lower().split("x")
    if len(parts) != 2:
        raise click.BadParameter(
            f"viewport must be WxH (e.g. 1280x900); got {text!r}"
        )
    try:
        width, height = int(parts[0]), int(parts[1])
    except ValueError:
        raise click.BadParameter(
            f"viewport WxH must be integers; got {text!r}"
        )
    if width <= 0 or height <= 0:
        raise click.BadParameter(
            f"viewport dimensions must be positive; got {text!r}"
        )
    return width, height


def _bundled_dir(name: str) -> Path:
    """Return a real Path to a bundled package data directory."""
    from importlib.resources import as_file, files

    traversable = files("quicksight_gen") / name
    # as_file() materializes to a real Path even when imported from a zip.
    # Closing the context manager when the wheel is on-disk is a no-op.
    with as_file(traversable) as path:
        if not path.is_dir():
            raise click.ClickException(
                f"Bundled '{name}/' directory not found in installed package."
            )
        return path


def _copy_tree(src: Path, dst: Path) -> int:
    shutil.copytree(src, dst, dirs_exist_ok=True)
    return sum(1 for p in dst.rglob("*") if p.is_file())


def _build_app_for_screenshots(app_slug: str, cfg, l2_instance):  # type: ignore[no-untyped-def]: cfg/l2_instance untyped pending CLI-wide sweep
    """Import + call the builder for ``app_slug``; resolve auto-IDs."""
    import importlib
    mod_path, fn_name, _subdir = SCREENSHOT_APPS[app_slug]
    mod = importlib.import_module(mod_path)
    builder = getattr(mod, fn_name)
    app = builder(cfg, l2_instance=l2_instance)
    # emit_analysis() resolves auto-IDs on the tree so sheet objects
    # match what was deployed.
    app.emit_analysis()
    return app


def _warm_db_for_screenshots(database_url: str) -> None:
    """Per the F12 cold-start footgun: SELECT 1 to warm the cluster
    before generating an embed URL. Without this, QuickSight shows
    'We can't open that dashboard' on the first walk."""
    scheme = (database_url.split("://", 1)[0] or "").lower()
    if scheme.startswith("oracle"):
        import oracledb  # type: ignore[import-untyped]: third-party library lacks PEP 561 stubs
        try:
            conn = oracledb.connect(database_url.split("://", 1)[1])
        except Exception as exc:  # pragma: no cover — env-specific
            raise click.ClickException(
                f"Oracle warmup failed ({exc}); pass --skip-warmup to bypass."
            )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM dual")
                cur.fetchall()
        finally:
            conn.close()
        return
    import psycopg
    conn = psycopg.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchall()
    finally:
        conn.close()


@click.group()
def docs() -> None:
    """mkdocs handbook site (build, serve, test, export, screenshot)."""


@docs.command("apply")
@l2_instance_option()
@click.option(
    "-o", "--output", "output",
    type=click.Path(), default="site",
    help="Output directory for the built site (default: site/).",
)
@click.option(
    "--strict/--no-strict", default=True,
    help="Pass --strict to mkdocs (default on; treats warnings as errors).",
)
@click.option(
    "--portable", is_flag=True,
    help=(
        "Build with use_directory_urls: false so the rendered site "
        "opens via file:// without a web server (every page emits "
        "as <slug>/index.html with relative links). The default "
        "build uses pretty URLs (/scenario/) which require an HTTP "
        "server to map the slug to its index.html."
    ),
)
def docs_apply(
    l2_instance_path: str | None,
    output: str,
    strict: bool,
    portable: bool,
) -> None:
    """Build the docs site to ``site/`` (or ``-o DIR``).

    Wraps ``mkdocs build``. The L2 instance bound via ``--l2`` (or
    falling back to the bundled spec_example) drives every
    ``{{ vocab.* }}`` substitution in the rendered prose.

    With ``--portable``, emits a static-site that opens via ``file://``
    — handy for shipping the rendered handbook on a USB stick, in a
    zip attachment, or to a wiki that doesn't run a web server.

    No ``--execute``: building a static site IS the operation.
    """
    config_to_use = _BUNDLED_MKDOCS_YML
    portable_yml: Path | None = None
    if portable:
        # Synthesize a tiny INHERIT-based config that overrides keys
        # that don't make sense for an offline / file:// build.
        # Written next to the bundled mkdocs.yml so the relative
        # ``INHERIT: mkdocs.yml`` resolves correctly in both dev and
        # installed mode (was broken pre-restructure when this lived
        # at ``_REPO_ROOT`` — installed wheel doesn't have a writable
        # repo root, just ``<site-packages>/lib/pythonX/``).
        #
        # `use_directory_urls: false` — emit `<slug>/index.html`
        #     so links resolve via file:// without a web server.
        # `theme.font: false` — Material defaults to fetching Roboto
        #     from fonts.googleapis.com on every page load. Offline
        #     readers get an ugly Times New Roman fallback while the
        #     fetch hangs; disabling drops the link tag entirely so
        #     Material renders in the OS system font (San Francisco
        #     / Segoe UI / Cantarell).
        portable_yml = _BUNDLED_MKDOCS_YML.parent / "mkdocs.portable.yml"
        portable_yml.write_text(
            "INHERIT: mkdocs.yml\n"
            "use_directory_urls: false\n"
            "theme:\n"
            "  font: false\n",
            encoding="utf-8",
        )
        config_to_use = portable_yml

    click.echo(f"$ mkdocs build -f {config_to_use} -d {output}")
    try:
        exit_code = build_docs_site(
            l2_instance_path, output, strict=strict, config=config_to_use,
        )
    finally:
        if portable_yml is not None and portable_yml.exists():
            portable_yml.unlink()
    if exit_code != 0:
        raise click.ClickException(f"mkdocs build failed (exit {exit_code}).")
    if portable:
        out_path = Path(output).resolve()
        _bake_portable_wasm(out_path)
        index = out_path / "index.html"
        click.echo(
            f"Portable site at {out_path}/. "
            f"Open {index} directly in a browser (no web server needed)."
        )


@docs.command("serve")
@l2_instance_option()
@click.option(
    "--port", "-p", default=8000, show_default=True, type=int,
    help="Port to bind for live-reload preview.",
)
def docs_serve(l2_instance_path: str | None, port: int) -> None:
    """Live-reload preview at http://localhost:PORT (default 8000).

    Wraps ``mkdocs serve``. Edit any docs source file and the browser
    refreshes automatically. Useful for iterating on persona-block
    YAML edits or new walkthrough drafts.
    """
    env = os.environ.copy()
    if l2_instance_path is not None:
        env["QS_DOCS_L2_INSTANCE"] = str(Path(l2_instance_path).resolve())

    cmd = [
        sys.executable, "-m", "mkdocs", "serve",
        "-f", str(_BUNDLED_MKDOCS_YML),
        "-a", f"127.0.0.1:{port}",
    ]
    # cwd MUST be the bundled mkdocs.yml's directory — mkdocs-macros
    # resolves its ``include_dir: docs/_macros`` against cwd, not
    # against the config file's directory (X.2.s.1: from a non-editable
    # `pip install`, the default cwd has no ``docs/_macros`` → mkdocs
    # serve fails with "docs/_macros does not exist" before it can
    # serve a single page). Mirrors ``docs_apply``.
    click.echo(f"$ {' '.join(cmd)}")
    subprocess.call(cmd, env=env, cwd=_BUNDLED_MKDOCS_YML.parent)


@docs.command("clean")
@click.option(
    "-o", "--output", "output",
    type=click.Path(), default="site",
    help="Directory to remove (default: site/).",
)
def docs_clean(output: str) -> None:
    """Remove the built site directory."""
    target = Path(output)
    if not target.exists():
        click.echo(f"{target}/ doesn't exist; nothing to clean.")
        return
    shutil.rmtree(target)
    click.echo(f"Removed {target}/")


@docs.command("test")
@click.option(
    "--pytest-args", default="",
    help="Extra args passed verbatim to pytest (e.g. '-k links -v').",
)
def docs_test(pytest_args: str) -> None:
    """Run the docs gates (link sweep + persona-neutral check)."""
    pytest_argv = (
        [sys.executable, "-m", "pytest", "tests/docs/", "-q"]
        + (pytest_args.split() if pytest_args else [])
    )
    pyright_argv = [
        sys.executable, "-m", "pyright",
        "src/quicksight_gen/common/handbook/",
        "main.py",
    ]
    failed = []
    click.echo(f"$ {' '.join(pytest_argv)}")
    if subprocess.call(pytest_argv, cwd=_REPO_ROOT) != 0:
        failed.append("pytest")
    click.echo(f"$ {' '.join(pyright_argv)}")
    if subprocess.call(pyright_argv, cwd=_REPO_ROOT) != 0:
        failed.append("pyright")
    if failed:
        raise click.ClickException(f"docs test failed: {', '.join(failed)}")
    click.echo("docs test: OK")


@docs.command("export")
@click.option(
    "-o", "--output", "output",
    type=click.Path(), required=True,
    help="Target directory; created if missing, merged into if existing.",
)
@click.option(
    "--l2", "l2_instance_path",
    type=click.Path(exists=True),
    help=(
        "Optional path to an L2 institution YAML to bind the rendered "
        "docs against. Validated here; binding happens at mkdocs build "
        "time via QS_DOCS_L2_INSTANCE env var."
    ),
)
def docs_export(output: str, l2_instance_path: str | None) -> None:
    """Copy the bundled mkdocs source to a folder for hand-build.

    Different from ``docs apply``: ``apply`` builds the site INTO a
    directory; ``export`` copies the SOURCE FILES so an integrator
    can use their own mkdocs config / theme / build pipeline.
    """
    src = _bundled_dir("docs")
    dst = Path(output)
    count = _copy_tree(src, dst)
    click.echo(f"Wrote {count} documentation files to {dst}")

    if l2_instance_path is not None:
        l2_path = Path(l2_instance_path).resolve()
        click.echo("")
        click.echo(
            "L2 instance bound: " + str(l2_path) + "\n"
            "To render against this instance, run:\n"
            f"    QS_DOCS_L2_INSTANCE={l2_path} mkdocs build -f {dst}/mkdocs.yml"
        )


@docs.command("screenshot")
@click.option(
    "--app",
    type=click.Choice(sorted(SCREENSHOT_APPS.keys())),
    default=None,
    help="Single app to capture. Mutually exclusive with --all.",
)
@click.option(
    "--all", "all_apps", is_flag=True,
    help="Capture all 4 apps. Output goes to <DIR>/<app-slug>/.",
)
@click.option(
    "-o", "--output", type=click.Path(), required=True,
    help="Target directory; per-app subdirs created under it.",
)
@click.option(
    "-c", "--config", "config_path",
    type=click.Path(exists=True), default=None,
    help="Config YAML (default: env vars).",
)
@click.option(
    "--l2", "l2_instance_path",
    type=click.Path(exists=True), default=None,
    help=(
        "L2 institution YAML override. Defaults to each app's built-in "
        "default (spec_example for most). Pass when capturing a deploy "
        "against a non-default L2 (e.g. tests/l2/sasquatch_pr.yaml)."
    ),
)
@click.option(
    "--viewport", "viewport_text", default="1280x900",
    show_default=True,
    help="Browser viewport WxH (default 1280x900).",
)
@click.option(
    "--skip-warmup", is_flag=True,
    help="Skip the F12 SELECT 1 cluster warmup (use when DB is hot).",
)
@click.option(
    "--headless/--no-headless", default=True, show_default=True,
    help="Run browser headless (default) or visible (debug).",
)
@click.option(
    "--initial-settle-ms", type=int, default=10_000, show_default=True,
    help="Settle delay after dashboard chrome appears, before first capture.",
)
@click.option(
    "--per-sheet-settle-ms", type=int, default=8_000, show_default=True,
    help="Settle delay after each sheet-tab click, before capture.",
)
@click.option(
    "--date-from", "date_from", default=None,
    help=(
        "YYYY-MM-DD override for each app's `*DateStart` parameter(s). "
        "Use to span the seed's anchor date when the dashboard's default "
        "rolling-window control doesn't reach it."
    ),
)
@click.option(
    "--date-to", "date_to", default=None,
    help=(
        "YYYY-MM-DD override for each app's `*DateEnd` parameter(s) "
        "(L1 also applies it to the Daily Statement single-day picker)."
    ),
)
def docs_screenshot(
    app: str | None,
    all_apps: bool,
    output: str,
    config_path: str | None,
    l2_instance_path: str | None,
    viewport_text: str,
    skip_warmup: bool,
    headless: bool,
    initial_settle_ms: int,
    per_sheet_settle_ms: int,
    date_from: str | None,
    date_to: str | None,
) -> None:
    """Capture per-sheet PNG screenshots from deployed dashboards.

    Walks the requested app's tree via WebKit and writes one full-page
    PNG per sheet to ``<output>/<app-slug>/<sheet_id>.png``. Single CLI
    surface for every app (replaces the per-app capture scripts that
    used to live under ``scripts/``).

    Requires the dashboards already deployed (``json apply --execute``).
    The handbook + walkthrough pages embed these screenshots by
    relative path under ``docs/walkthroughs/screenshots/<app>/``.
    """
    if app is None and not all_apps:
        raise click.UsageError("Specify --app <name> or --all.")
    if app is not None and all_apps:
        raise click.UsageError("Pass either --app or --all, not both.")

    apps_to_capture = (
        sorted(SCREENSHOT_APPS.keys()) if all_apps else [app]
    )

    cfg = load_config(config_path)
    if not cfg.aws_account_id or not cfg.aws_region:
        raise click.ClickException(
            "Config missing aws_account_id or aws_region — "
            "screenshots need them to generate an embed URL."
        )
    if not skip_warmup and not cfg.demo_database_url:
        raise click.ClickException(
            "demo_database_url not set; pass --skip-warmup to bypass "
            "the cluster warmup step."
        )

    viewport = parse_viewport(viewport_text)

    # Validate date overrides up-front so a typo doesn't surface mid-walk.
    from datetime import date as _date
    if date_from is not None:
        try:
            _date.fromisoformat(date_from)
        except ValueError as exc:
            raise click.BadParameter(
                f"--date-from must be YYYY-MM-DD; got {date_from!r} ({exc})"
            )
    if date_to is not None:
        try:
            _date.fromisoformat(date_to)
        except ValueError as exc:
            raise click.BadParameter(
                f"--date-to must be YYYY-MM-DD; got {date_to!r} ({exc})"
            )

    l2_instance = None
    if l2_instance_path is not None:
        from quicksight_gen.common.l2 import load_instance
        l2_instance = load_instance(Path(l2_instance_path))

    if not skip_warmup:
        click.echo(
            f"-> Warming DB ({cfg.demo_database_url.split('@')[-1]}, "
            f"SELECT 1)...", nl=False,
        )
        _warm_db_for_screenshots(cfg.demo_database_url)
        click.echo(" OK")

    from quicksight_gen.common.browser.helpers import (
        generate_dashboard_embed_url,
    )
    from quicksight_gen.common.browser.screenshot import capture_deployed_app

    output_root = Path(output)
    output_root.mkdir(parents=True, exist_ok=True)

    grand_total = 0
    for slug in apps_to_capture:
        click.echo(f"== {slug} ==")
        app_obj = _build_app_for_screenshots(slug, cfg, l2_instance)
        # Dashboard ID convention: cfg.prefixed(<dashboard_id_suffix>).
        # MUST use app_obj.cfg, not the outer cfg — the builders auto-
        # derive cfg.l2_instance_prefix from l2_instance.instance and
        # store the updated cfg on the app.
        dashboard_suffix = app_obj.dashboard.dashboard_id_suffix
        dashboard_id = app_obj.cfg.prefixed(dashboard_suffix)
        click.echo(
            f"-> embed URL for {dashboard_id}...", nl=False,
        )
        url = generate_dashboard_embed_url(
            aws_account_id=cfg.aws_account_id,
            aws_region=cfg.aws_region,
            dashboard_id=dashboard_id,
        )
        click.echo(" OK")

        # Write to the short-slug subdir (l1/, l2ft/, inv/, exec/).
        _, _, output_subdir = SCREENSHOT_APPS[slug]
        out_dir = output_root / output_subdir

        url_params: dict[str, str] = {}
        if date_from is not None:
            for pname in _APP_DATE_PARAMS[slug]["from"]:
                url_params[pname] = date_from
        if date_to is not None:
            for pname in _APP_DATE_PARAMS[slug]["to"]:
                url_params[pname] = date_to
        if url_params:
            click.echo(
                f"-> URL date params: "
                + ", ".join(f"{k}={v}" for k, v in url_params.items())
            )

        click.echo(f"-> capturing {len(app_obj.analysis.sheets)} sheets at "
                   f"{viewport[0]}x{viewport[1]} into {out_dir}/")
        results = capture_deployed_app(
            app_obj,
            embed_url=url,
            output_dir=out_dir,
            viewport=viewport,
            initial_settle_ms=initial_settle_ms,
            per_sheet_settle_ms=per_sheet_settle_ms,
            headless=headless,
            url_params=url_params or None,
        )
        for sheet, path in results.items():
            click.echo(f"   {sheet.name:30s} -> {path.name}")
        grand_total += len(results)

    click.echo("")
    click.echo(
        f"Captured {grand_total} screenshots across "
        f"{len(apps_to_capture)} app(s) at {output_root}/"
    )
