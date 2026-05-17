"""Project-wide pytest fixtures and session hooks.

``pytest_sessionstart`` runs two static-analysis gates before any test
executes, so a lint / type error fails the session immediately rather
than letting tests run against broken code. Both are the same gates CI
runs, shifted into the local loop so you don't have to remember to run
them by hand — and both flow through the test-layer-chain runner's
``unit`` prelude automatically (the runner's unit layer is just
``pytest`` on the unit dirs).

- **pyright strict** (L.1.20) — type-checks the strict-scope include
  list (``pyproject.toml::tool.pyright.include``). Opt out:
  ``RECON_GEN_SKIP_PYRIGHT=1``.
- **biome** (the X.2.l.4 follow-on) — lints the App 2 JS + the JS test
  fixtures (``biome.jsonc::files.includes``). ``biome check`` exits
  non-zero on lint *errors* (e.g. ``noInnerDeclarations``) and zero on
  warnings — same "errors fail, warnings don't" policy as the project
  config. Opt out: ``RECON_GEN_SKIP_BIOME=1``. ``biome`` is a standalone
  Rust binary (brew locally / ``biomejs/setup-biome`` in CI), not a pip
  dep — when it's not on ``PATH`` the gate skips rather than failing.
  (No official Biome PyPI package yet — tracking biomejs/biome#8818; once
  that lands it becomes a ``[dev]`` dep like ``pyright`` and this gate's
  ``shutil.which`` lookup gets a venv-sibling check.)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _find_pyright() -> str | None:
    """Locate the pyright binary.

    Prefer the venv next to the running interpreter (``sys.executable``'s
    sibling) so the gate works whether pytest was launched via the venv's
    pytest script or via a system pytest with the venv bin not on PATH.
    Fall back to ``shutil.which`` for a system-installed pyright.
    """
    venv_pyright = Path(sys.executable).parent / "pyright"
    if venv_pyright.exists():
        return str(venv_pyright)
    return shutil.which("pyright")


def _run_static_gate(
    *, name: str, argv: list[str], cwd: str, skip_env: str, fail_msg: str,
) -> None:
    """Run a static-analysis subprocess; ``pytest.exit`` on non-zero.

    The output goes to ``sys.__stderr__`` directly (pytest captures the
    sessionstart hook's stdout/stderr, so the operator otherwise wouldn't
    see the failure context).
    """
    if os.environ.get(skip_env):
        return
    result = subprocess.run(argv, capture_output=True, text=True, cwd=cwd)
    if result.returncode == 0:
        return
    output = (result.stdout or "") + (result.stderr or "")
    sys.__stderr__.write(
        f"\n{fail_msg}\nSet {skip_env}=1 to bypass.\n\n{output}\n"
    )
    sys.__stderr__.flush()
    pytest.exit(f"{name} failed; see stderr for details.", returncode=2)


def pytest_sessionstart(session: pytest.Session) -> None:
    """Run pyright strict + biome before any test executes. Fail-fast."""
    rootpath = str(session.config.rootpath)

    pyright = _find_pyright()
    if pyright is not None:  # dev install without pyright → skip the gate
        _run_static_gate(
            name="pyright strict",
            argv=[pyright],
            cwd=rootpath,
            skip_env="RECON_GEN_SKIP_PYRIGHT",
            fail_msg="pyright strict failed — fix type errors before tests run.",
        )

    biome = shutil.which("biome")
    if biome is not None:  # biome not installed → skip the gate
        _run_static_gate(
            name="biome",
            # --max-diagnostics high so a real error isn't truncated past
            # the default cap by the pre-existing style warnings.
            argv=[biome, "check", "--max-diagnostics=400"],
            cwd=rootpath,
            skip_env="RECON_GEN_SKIP_BIOME",
            fail_msg="biome found lint errors — fix the App 2 JS before tests run.",
        )
