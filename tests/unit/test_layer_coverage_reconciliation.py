"""DY.1 — coverage-reconciliation gate: no e2e test runs NOWHERE.

The runner's five layers (``unit → db → app2 → app2_browser → agreement``)
mostly select tests by DIRECTORY/tier: ``db`` = ``tests/e2e/db/``, ``app2`` =
``tests/e2e/app2/``, ``agreement`` = the three-dir set filtered by
``--tier=agreement``. The lone holdout is ``app2_browser``, which still
selects the ROOT ``tests/e2e/`` dir by the LEGACY ``-m browser`` mark (the
unfinished CB.6 ``-m mark`` → ``--tier`` migration). The tier source-of-truth
(``@tier(Tier.APP2)``, on every root file) and that selector have drifted: a
root test carrying the tier but NO hand-applied ``browser`` mark is collected
by no dir layer and deselected by ``-m browser`` — so it runs in NO layer,
with no error. That is exactly how the agreement validators went un-run since
the CB.5 tier-dir migration, and how ``test_dashboard_driver.py`` (9) +
``test_studio_deploy_browser.py`` (2) are dark today.

This gate drives the runner's REAL per-layer selectors (``_layer_command``)
through ``pytest --collect-only`` and asserts every test physically under
``tests/e2e/`` is claimed by at least one layer. It is the cheapest
validation that must fire ([[feedback_cheapest_validation_must_fire]]) and
the invariant-in-process that makes "a test that runs nowhere" structurally
unrepresentable ([[feedback_invariants_in_process]]) — not fixed once. It is
RED until DY.1 finishes the migration and retires ``-m browser``.

Collection-only: no DB, no browser, no Docker — belongs in the unit prelude,
gates every push.

SCOPE CAVEAT: this gate sees only tests that COLLECT in its env. A module
that ``importorskip``s out (a missing OPTIONAL dep, fine — or a DEAD dep,
a bug) collects zero tests and is invisible here. DY.1 hit one instance —
``test_studio_deploy_browser.py`` still ``importorskip``ed ``aiosqlite``
(SQLite was dropped in CB.8) and so was skipped at collection everywhere
incl. CI, despite driving a postgres testcontainer; that dead import was
removed so the module collects + this gate now claims it. A zero-collect-
file check (every ``tests/e2e/**/test_*.py`` collects >=1 test in the full
[dev] env) would catch that class structurally — a candidate follow-up.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Unconditional _dev import: same pattern as tests/unit/test_runner_*.py. The
# _dev package is wheel-excluded, but the release wheel-smoke runs a curated
# unit-file list (release.yml) that this file is not on — so import-at-collect
# is safe in the dev/CI env where _dev is present.
from recon_gen._dev.runner import RunOptions, _layer_command

_REPO_ROOT = Path(__file__).resolve().parents[2]

# The e2e layers whose union must cover every test under tests/e2e/.
_E2E_LAYERS = ("db", "app2", "agreement", "app2_browser")

# Args that affect WHICH tests collect (kept); everything else (-n / -q /
# --reruns / --reruns-delay / --dist / --cov) is execution tuning we strip so
# --collect-only runs clean + fast.
_SELECTOR_KEEP_PREFIXES = ("tests/", "--tier", "--ignore")


def _selector_args(layer: str) -> list[str]:
    """The runner's REAL pytest selector for ``layer`` — directories + ``-m``
    + ``--tier`` + ``--ignore`` — with the execution-tuning flags stripped."""
    result = _layer_command(layer, _REPO_ROOT / "runs" / "_recon_gate", RunOptions())
    assert result is not None, f"_layer_command returned None for {layer!r}"
    cmd, _env = result
    args = cmd[1:]  # drop the pytest binary path (cmd[0])
    kept: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith(_SELECTOR_KEEP_PREFIXES):
            kept.append(a)
        elif a == "-m":  # marker expression — two tokens (-m, "browser")
            kept.append(a)
            kept.append(args[i + 1])
            i += 1
        i += 1
    return kept


def _collect_nodeids(pytest_args: list[str]) -> set[str]:
    """Run ``pytest --collect-only -q <args>`` in a subprocess and return the
    set of POST-deselection nodeids under tests/e2e/."""
    proc = subprocess.run(
        [
            sys.executable, "-m", "pytest", *pytest_args,
            "--collect-only", "-q", "-p", "no:cacheprovider",
        ],
        cwd=_REPO_ROOT,
        env={**os.environ, "RECON_GEN_SKIP_PYRIGHT": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    nodeids = {
        line.strip()
        for line in proc.stdout.splitlines()
        if "::" in line and line.strip().startswith("tests/e2e/")
    }
    # rc 0 = collected; rc 5 = no tests collected (a layer may legitimately
    # deselect everything). Any other rc means collection itself broke.
    if not nodeids and proc.returncode not in (0, 5):
        raise AssertionError(
            f"collection failed for {pytest_args}: rc={proc.returncode}\n"
            f"--- stdout (tail) ---\n{proc.stdout[-2000:]}\n"
            f"--- stderr (tail) ---\n{proc.stderr[-2000:]}"
        )
    return nodeids


def test_no_e2e_test_runs_nowhere() -> None:
    """Every test physically under ``tests/e2e/`` is claimed by at least one
    runner layer's real selector.

    FAILS while ``app2_browser`` selects by ``-m browser``: the difference
    set is the tier-marked-but-not-mark-marked root tests that run nowhere.
    DY.1's migration (dir-driven ``app2_browser/`` + retired ``-m browser``)
    turns it green.
    """
    all_e2e = _collect_nodeids(["tests/e2e/"])
    assert all_e2e, (
        "collected zero tests under tests/e2e/ — collection itself is broken "
        "(check playwright/testcontainers are installed in this env)"
    )

    claimed: set[str] = set()
    for layer in _E2E_LAYERS:
        claimed |= _collect_nodeids(_selector_args(layer))

    orphans = all_e2e - claimed
    assert not orphans, (
        f"{len(orphans)} test(s) under tests/e2e/ run in NO runner layer — "
        f"the CB.6 tier-mark-vs-selector drift (a tier-marked root test with "
        f"no `-m browser` mark is collected by no dir layer AND deselected by "
        f"the app2_browser `-m browser` selector). Finish the --tier "
        f"migration (DY.1) so the tier-dir IS the run-set:\n  "
        + "\n  ".join(sorted(orphans))
    )
