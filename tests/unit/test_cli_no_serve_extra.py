"""Pin the X.4.a-regression: the CLI shell must import without ``[serve]``.

Failure mode caught (v10.0.0a2's first push): the GitHub Pages job and
release.yml's smoke-wheel test both install ``recon-gen[docs]`` —
no ``[serve]`` extra, so no ``uvicorn`` / ``starlette``. They then run
``recon-gen docs apply`` (Pages) or ``pytest tests/unit/`` (smoke).
Both go through ``from recon_gen.cli import main``. If anything
in the CLI shell's import chain pulls ``uvicorn`` / ``starlette`` at
module-load time, those jobs explode at startup.

The lazy-import discipline that the original ``cli/serve.py`` had:
``import uvicorn`` and the ``starlette`` imports lived inside the
function body. The X.4.a refactor lifted them to the top of
``cli/_html_serve.py``, breaking the rule. This test simulates the
no-``[serve]`` env (``sys.meta_path`` import block) and asserts:

1. ``from recon_gen.cli import main`` works.
2. ``CliRunner.invoke(main, ['--help'])`` exits 0 (full Click registration
   walked — which means ``cli.dashboards`` + ``cli.studio`` got loaded
   without crashing on the simulated-missing imports).

Add a new top-level CLI module → make sure its imports stay
lazy/optional for the heavy deps.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import Any

import pytest
from click.testing import CliRunner


_BLOCKED_PREFIXES = ("uvicorn", "starlette")


class _BlockedImport:
    """``sys.meta_path`` finder that ImportErrors on ``[serve]``-only deps."""

    def find_spec(
        self, name: str, path: Any = None, target: Any = None,
    ) -> None:
        for prefix in _BLOCKED_PREFIXES:
            if name == prefix or name.startswith(prefix + "."):
                raise ImportError(f"simulated missing: {name}")
        return None


@pytest.fixture
def no_serve_extra() -> Iterator[None]:
    """Pretend ``uvicorn`` + ``starlette`` aren't installed.

    Evicts already-loaded copies from ``sys.modules`` and the
    ``recon_gen.cli`` tree (so the import-time chain re-runs
    cleanly), installs the blocking finder, runs the test, then
    restores. Because pytest's other test modules eagerly load
    ``starlette`` (``test_html_server.py`` imports ``TestClient``),
    the eviction needs to be aggressive — but it's bounded to the
    fixture's scope.
    """
    blocker = _BlockedImport()
    saved = {
        k: sys.modules[k]
        for k in list(sys.modules)
        if (
            any(k == p or k.startswith(p + ".") for p in _BLOCKED_PREFIXES)
            or k == "recon_gen.cli"
            or k.startswith("recon_gen.cli.")
        )
    }
    for k in saved:
        del sys.modules[k]
    sys.meta_path.insert(0, blocker)
    try:
        yield
    finally:
        sys.meta_path.remove(blocker)
        for k in list(sys.modules):
            if any(k == p or k.startswith(p + ".") for p in _BLOCKED_PREFIXES):
                del sys.modules[k]
            elif k == "recon_gen.cli" or k.startswith("recon_gen.cli."):
                del sys.modules[k]
        # Re-load the modules that other tests' module-scope imports
        # hold references to, with the blocker removed.
        for k, mod in saved.items():
            sys.modules.setdefault(k, mod)


def test_cli_main_imports_without_serve_extra(no_serve_extra: None) -> None:
    """``from recon_gen.cli import main`` must work on a [docs]-only
    install — no transitive ``uvicorn`` / ``starlette`` import at module-
    load time.
    """
    del no_serve_extra
    from recon_gen.cli import main
    assert main is not None


def test_cli_help_exits_zero_without_serve_extra(
    no_serve_extra: None,
) -> None:
    """``recon-gen --help`` walks every Click command registration
    (so ``cli.dashboards`` + ``cli.studio`` get loaded) — this proves
    every command's module-top imports are still lazy on the heavy deps.
    """
    del no_serve_extra
    from recon_gen.cli import main
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0, result.output
    # Both new commands must be listed (proves they registered).
    assert "studio" in result.output
    assert "dashboards" in result.output
