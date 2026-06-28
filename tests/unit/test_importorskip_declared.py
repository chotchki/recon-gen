"""DY.1 — lint: every ``pytest.importorskip(X)`` names a DECLARED dependency.

A ``pytest.importorskip("foo")`` that names a package the project never
declares in ``pyproject.toml`` is a silent black hole: ``foo`` is in no
extra, so it is never installed in any env (local OR CI), so the guarded
module skips at collection EVERYWHERE — the tests look "present" but run
nowhere, forever. That is exactly how ``test_studio_deploy_browser.py`` (a
Deploy→pipeline→dashboards Postgres integration test) sat dead behind
``importorskip("aiosqlite")`` after SQLite was dropped in CB.8.

This is the env-INDEPENDENT companion to the reconciliation gate in
``test_layer_coverage_reconciliation.py`` (which can only see tests that
collect): it reads source + pyproject, no env state, so a dead ``importorskip``
fails the same way on every box. Pairs a real-tree assertion with a planted
smoke test so it can't silently become a zero-hit no-op
([[feedback_cheapest_validation_must_fire]]).
"""

from __future__ import annotations

import ast
import re
import tomllib
from collections import defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Modules that are always importable (stdlib / pip itself) and so are a
# legitimate — if unusual — importorskip target despite not being a declared
# dependency. Empty today; an explicit, reasoned escape hatch if one appears.
_ALWAYS_PRESENT: frozenset[str] = frozenset()

# The project's OWN top-level packages. ``importorskip("tests.e2e._helper")``
# / ``importorskip("recon_gen.x")`` guards a LOCAL module's importability (a
# different pattern from gating on an external optional dep), so it is out of
# scope for the declared-dep check — local packages aren't pyproject deps.
_LOCAL_TOP_LEVEL: frozenset[str] = frozenset({"tests", "recon-gen"})


def _norm(name: str) -> str:
    """Normalize a package/import name for comparison: lowercase, ``_`` → ``-``."""
    return name.strip().lower().replace("_", "-")


def _declared_dep_packages() -> set[str]:
    """Top-level package names declared in pyproject (core ``dependencies`` +
    every ``optional-dependencies`` extra), normalized."""
    data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    specs: list[str] = list(project.get("dependencies", []))
    for extra_specs in project.get("optional-dependencies", {}).values():
        specs.extend(extra_specs)
    pkgs: set[str] = set()
    for spec in specs:
        # Strip version / extras / markers: "testcontainers[postgres]>=4" -> "testcontainers".
        head = re.split(r"[<>=!~\[;( ]", spec.strip(), maxsplit=1)[0]
        if head:
            pkgs.add(_norm(head))
    return pkgs


def _importorskip_targets_in_tree(tree: ast.AST) -> set[str]:
    """Top-level package names passed to ``pytest.importorskip("...")`` /
    bare ``importorskip("...")`` with a string-literal first arg."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_importorskip = (
            (isinstance(func, ast.Attribute) and func.attr == "importorskip")
            or (isinstance(func, ast.Name) and func.id == "importorskip")
        )
        if not is_importorskip or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            out.add(_norm(first.value.split(".", 1)[0]))
    return out


def _importorskip_sites() -> dict[str, list[str]]:
    """Map normalized top-level package → ``file:lineno`` sites across tests/."""
    sites: dict[str, list[str]] = defaultdict(list)
    for path in sorted((_REPO_ROOT / "tests").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_importorskip = (
                (isinstance(func, ast.Attribute) and func.attr == "importorskip")
                or (isinstance(func, ast.Name) and func.id == "importorskip")
            )
            if not is_importorskip or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                pkg = _norm(first.value.split(".", 1)[0])
                sites[pkg].append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno}")
    return sites


def test_importorskip_targets_are_declared_deps() -> None:
    """Every importorskip target is a declared dependency (or always-present).

    Catches the dead-``importorskip`` class — a guard naming a package in no
    extra, which skips the guarded tests at collection everywhere. The fix is
    to declare the dep (if the tests should run) or delete the dead guard.
    """
    declared = _declared_dep_packages() | _ALWAYS_PRESENT | _LOCAL_TOP_LEVEL
    sites = _importorskip_sites()
    assert sites, "no pytest.importorskip targets found — did the AST walk break?"
    undeclared = {pkg: locs for pkg, locs in sites.items() if pkg not in declared}
    assert not undeclared, (
        "pytest.importorskip() names package(s) the project does NOT declare in "
        "pyproject.toml — they are installed in no env, so the guarded tests "
        "skip at collection EVERYWHERE (incl. CI), silently forever. Declare the "
        "dep (if the tests should run) or delete the dead guard:\n"
        + "\n".join(
            f"  {pkg!r}: {', '.join(locs)}" for pkg, locs in sorted(undeclared.items())
        )
    )


def test_lint_detects_a_planted_dead_importorskip() -> None:
    """Smoke: the detector flags an undeclared importorskip (not a no-op)."""
    src = 'import pytest\npytest.importorskip("nonexistent_dead_dep")\n'
    targets = _importorskip_targets_in_tree(ast.parse(src))
    assert "nonexistent-dead-dep" in targets
    assert "nonexistent-dead-dep" not in _declared_dep_packages()
