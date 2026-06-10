"""CY.1 — git short SHA bakes into the wheel at build time.

``_git_short_sha()`` resolves the SHA via three steps:

1. Import ``recon_gen._build_info.__build_info__`` — written by the
   ``build_hook.BuildPyWithBuildInfo`` cmdclass at ``python -m build`` /
   ``uv build`` / ``pip install -e .`` time.
2. Runtime ``git rev-parse --short HEAD`` fallback for dev venvs that
   imported the package before the build hook ran.
3. ``"unknown"`` if both fail.

These tests pin all three steps. The build-hook pure functions
(``compute_build_info`` / ``render_build_info_module``) get their own
coverage via the repo-root ``build_hook.py`` import.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

from recon_gen.common.sheets import app_info as _app_info_mod


# --- _git_short_sha() resolution order --------------------------------


def _make_fake_build_info(git_sha: str) -> Any:
    """Build a fake ``recon_gen._build_info`` module object with the
    given git_sha. Used by the import-module patches below."""
    fake_module: Any = type(sys)("recon_gen._build_info")
    fake_module.__build_info__ = {
        "git_sha": git_sha,
        "built_at": "2026-06-09T12:00:00+00:00",
        "build_kind": "release" if git_sha != "unknown" else "dev",
    }
    return fake_module


def _patched_importlib(fake_lookup: dict[str, Any]) -> Any:
    """Patch ``app_info.importlib.import_module`` to look up the given
    name dict first, fall through to the real importer otherwise. The
    real import path goes via the ORIGINAL ``importlib.import_module``
    captured before the patch installs — avoids the mock-resolves-via-
    importlib recursion that bites when you redirect through
    ``import importlib`` inside the side_effect.
    """
    real_import_module = importlib.import_module

    def side_effect(name: str) -> Any:
        if name in fake_lookup:
            value = fake_lookup[name]
            if isinstance(value, Exception):
                raise value
            return value
        return real_import_module(name)

    return patch.object(
        _app_info_mod.importlib, "import_module", side_effect=side_effect,
    )


def test_git_short_sha_prefers_baked_build_info() -> None:
    """When ``recon_gen._build_info`` is importable AND carries a
    non-"unknown" git_sha, ``_git_short_sha()`` returns that SHA without
    shelling out to git. This is the wheel-installed path."""
    fake_module = _make_fake_build_info("abc1234")
    with _patched_importlib({"recon_gen._build_info": fake_module}):
        assert _app_info_mod._git_short_sha() == "abc1234"


def test_git_short_sha_skips_baked_unknown_value() -> None:
    """A baked SHA of "unknown" (build hook fired in a non-git env)
    must NOT short-circuit — the runtime fallback gets a chance."""
    fake_module = _make_fake_build_info("unknown")

    def fake_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["git"], returncode=0, stdout="runtime1\n", stderr="",
        )

    def noop_persist(_sha: str) -> None:
        return None

    with (
        _patched_importlib({"recon_gen._build_info": fake_module}),
        patch.object(_app_info_mod.subprocess, "run", fake_run),
        patch.object(
            _app_info_mod, "_persist_build_info_best_effort", noop_persist,
        ),
    ):
        assert _app_info_mod._git_short_sha() == "runtime1"


def test_git_short_sha_falls_back_to_subprocess_when_build_info_absent() -> None:
    """When ``recon_gen._build_info`` is missing (dev venv that never ran
    the build hook), ``_git_short_sha`` shells out to ``git rev-parse``."""

    def fake_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["git"], returncode=0, stdout="dead1234\n", stderr="",
        )

    def noop_persist(_sha: str) -> None:
        return None

    sys.modules.pop("recon_gen._build_info", None)

    with (
        _patched_importlib({
            "recon_gen._build_info": ImportError("simulated missing"),
        }),
        patch.object(_app_info_mod.subprocess, "run", fake_run),
        patch.object(
            _app_info_mod, "_persist_build_info_best_effort", noop_persist,
        ),
    ):
        assert _app_info_mod._git_short_sha() == "dead1234"


def test_git_short_sha_returns_unknown_when_all_fail() -> None:
    """No baked module + git missing/failing → ``"unknown"``."""

    def fake_run_fail(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("git not on PATH")

    sys.modules.pop("recon_gen._build_info", None)

    with (
        _patched_importlib({
            "recon_gen._build_info": ImportError("simulated missing"),
        }),
        patch.object(_app_info_mod.subprocess, "run", fake_run_fail),
    ):
        assert _app_info_mod._git_short_sha() == "unknown"


# --- build_hook.py pure functions --------------------------------------


def test_compute_build_info_returns_required_keys(tmp_path: Path) -> None:
    """``compute_build_info(repo_root)`` returns the three documented
    keys with string values, even when the dir isn't a git checkout."""
    # Need the build_hook module on the path; add the repo root.
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root))
    try:
        from build_hook import compute_build_info
        info = compute_build_info(tmp_path)
    finally:
        sys.path.remove(str(repo_root))

    assert set(info.keys()) >= {"git_sha", "built_at", "build_kind"}
    # tmp_path isn't a git checkout → SHA must be "unknown".
    assert info["git_sha"] == "unknown"
    # No git checkout → no tag → build_kind defaults to "dev".
    assert info["build_kind"] == "dev"
    # ISO 8601 timestamp: starts with year-month-day.
    assert info["built_at"][:4].isdigit()


def test_compute_build_info_reads_real_sha_in_repo() -> None:
    """In the actual repo checkout, ``compute_build_info`` returns a
    non-"unknown" SHA (matches ``git rev-parse --short HEAD``)."""
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root))
    try:
        from build_hook import compute_build_info
        info = compute_build_info(repo_root)
    finally:
        sys.path.remove(str(repo_root))

    # Sanity-check against the live git command.
    expected = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=str(repo_root), capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert info["git_sha"] == expected
    assert info["git_sha"] != "unknown"


def test_render_build_info_module_is_valid_python() -> None:
    """The rendered module must be importable Python that re-yields the
    same dict. Round-trip via exec into a fresh namespace."""
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root))
    try:
        from build_hook import render_build_info_module
        info = {
            "git_sha": "feedbeef",
            "built_at": "2026-06-09T12:34:56+00:00",
            "build_kind": "release",
        }
        text = render_build_info_module(info)
    finally:
        sys.path.remove(str(repo_root))

    namespace: dict[str, Any] = {}
    exec(text, namespace, namespace)
    assert namespace["__build_info__"] == info


def test_write_build_info_creates_file(tmp_path: Path) -> None:
    """``write_build_info(repo_root)`` materializes the module file at
    ``src/recon_gen/_build_info.py`` under ``repo_root``."""
    (tmp_path / "src" / "recon_gen").mkdir(parents=True)
    # Need a fake __init__.py so _read_version returns something useful;
    # absence is also fine — compute_build_info tolerates None version.
    (tmp_path / "src" / "recon_gen" / "__init__.py").write_text(
        '__version__ = "0.0.0"\n', encoding="utf-8",
    )

    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root))
    try:
        from build_hook import write_build_info
        written = write_build_info(tmp_path)
    finally:
        sys.path.remove(str(repo_root))

    expected = tmp_path / "src" / "recon_gen" / "_build_info.py"
    assert written == expected
    assert expected.exists()
    text = expected.read_text(encoding="utf-8")
    assert "__build_info__" in text
    assert "git_sha" in text
