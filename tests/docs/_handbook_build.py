"""AH.8 (#175) — isolated handbook builds under the run-artifact dir.

Every mkdocs build runs ``main.py``'s mkdocs-macros entry, which writes a
generated ``stylesheets/_l2_theme.css`` (and any L2 brand assets) into
``env.conf["docs_dir"]``; mkdocs then copies ``docs_dir`` into the
rendered site. When every docs test builds against the one bundled
``docs/`` source — and the shared ``REPO_ROOT/site`` output — parallel
xdist workers race on both:

* the output dir: ``if SITE.exists(): rmtree(SITE)`` is a cross-worker
  TOCTOU → ``FileNotFoundError`` when another worker deletes it first;
* the generated CSS: worker A writes ``docs_dir/stylesheets/_l2_theme.css``
  for its accent, worker B overwrites it, then A's mkdocs copies B's file
  into A's output → A's distinctive hex (``#FF00CC``) is gone.

This helper builds each invocation in an isolated sandbox UNDER the run
dir (``RECON_GEN_RUN_DIR`` when invoked through the layer-chain runner; a
stable tmp dir otherwise — same "all artifacts live in runs/<id>/"
convention as timings / coverage / browser diagnostics). It copies the
bundled ``docs/`` into the sandbox and points ``docs_dir`` at the copy via
an ``INHERIT`` config, so the generated CSS and the output are per-build.

``cwd`` and the synth config both stay at the bundled package dir on
purpose: mkdocs-macros resolves ``module_name: main`` against the config
file's directory, and ``main.py`` resolves ``_l2_fixtures`` relative to
its own ``__file__`` — copying ``main.py`` into the sandbox would break
that. Only the writable ``docs_dir`` is redirected.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from recon_gen.common.env_keys import EnvVarInvalid, RECON_GEN_RUN_DIR

_BUNDLED_MKDOCS_YML = (
    Path(__file__).resolve().parents[2] / "src" / "recon_gen" / "mkdocs.yml"
)
_BUNDLED_DOCS = _BUNDLED_MKDOCS_YML.parent / "docs"


def handbook_build_root() -> Path:
    """Per-run root for docs-build sandboxes.

    ``$RECON_GEN_RUN_DIR/docs-builds`` under the runner (so the sandboxes
    sit beside every other ``runs/<id>/`` artifact); a stable tmp subdir
    for a bare ``pytest`` invocation that has no run dir.
    """
    try:
        run_dir = RECON_GEN_RUN_DIR.get_or_none()
    except EnvVarInvalid:
        run_dir = None
    base = run_dir if run_dir else Path(tempfile.gettempdir()) / "recon-gen"
    root = base / "docs-builds"
    root.mkdir(parents=True, exist_ok=True)
    return root


def build_handbook(
    l2_instance_path: str | Path | None,
    *,
    strict: bool = True,
    portable: bool = False,
) -> Path:
    """Build the handbook into an isolated sandbox; return the rendered ``site/``.

    Skips (not fails) when the bundled config / mkdocs is unavailable — a
    minimal-extras checkout degrades the same way the callers' inline
    guards used to.
    """
    if not _BUNDLED_MKDOCS_YML.exists():
        pytest.skip(f"mkdocs.yml not found at {_BUNDLED_MKDOCS_YML}")
    try:
        import   # noqa: F401
    except ImportError:
        pytest.skip("mkdocs not installed")

    sandbox = Path(tempfile.mkdtemp(prefix="hb-", dir=handbook_build_root()))
    docs_copy = sandbox / "docs"
    shutil.copytree(_BUNDLED_DOCS, docs_copy)
    site_dir = sandbox / "site"

    # INHERIT the bundled config but redirect the (writable) docs_dir to our
    # copy. The synth config lives NEXT TO the bundled mkdocs.yml (unique
    # name) so ``INHERIT: mkdocs.yml`` + ``include_dir: docs/_macros`` +
    # ``module_name: main`` all resolve against the real package dir; only
    # docs_dir (absolute) points at the isolated copy.
    synth = _BUNDLED_MKDOCS_YML.parent / f"mkdocs.sandbox-{sandbox.name}.yml"
    body = ["INHERIT: mkdocs.yml", f"docs_dir: {docs_copy}"]
    if portable:
        body += ["use_directory_urls: false", "theme:", "  font: false"]
    synth.write_text("\n".join(body) + "\n", encoding="utf-8")

    env = os.environ.copy()
    if l2_instance_path is not None:
        env["QS_DOCS_L2_INSTANCE"] = str(Path(l2_instance_path).resolve())
    cmd = [
        sys.executable, "-m", "mkdocs", "build",
        "-f", str(synth),
        "-d", str(site_dir),
    ]
    if strict:
        cmd.append("--strict")
    # cwd = bundled package dir: mkdocs-macros joins ``include_dir`` against
    # cwd (X.2.s.1) and imports ``main`` off the config dir; both must be the
    # real package, not the sandbox.
    try:
        proc = subprocess.run(
            cmd, cwd=_BUNDLED_MKDOCS_YML.parent,
            capture_output=True, text=True, env=env,
        )
    finally:
        synth.unlink(missing_ok=True)
    if proc.returncode != 0:
        pytest.fail(
            f"mkdocs build (l2={l2_instance_path}, portable={portable}) "
            f"failed (exit {proc.returncode}):\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return site_dir
