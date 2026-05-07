"""Test layer chain runner — orchestrates the layered test chain with per-run
output isolation + timing-diff drift detection.

Invoked via the ``./run_tests.sh`` bash shim at repo root; the shim
``exec``s into ``python -m quicksight_gen._dev.runner``.

Verbs:
    up_to <layer>     Run the chain up to and including <layer>.
                      Layers: pyright | unit | db | deploy | api | browser.
                      Equivalent forms: ``up_to=<layer>`` and ``up_to <layer>``.
    up [scope]        Boot dependencies. scope = local | aws | all (default).
    down [scope]      Tear down dependencies. scope as above.
    status [--cost]   Show what's currently running.
    sweep             Clean orphan resources (tagged ManagedBy:quicksight-gen).

Exit codes:
    0  success
    1  test failure (one or more layers / variants failed)
    2  needs-operator (expired creds, dirty deploy refused, missing cfg, etc.)
    3  config / argument error

Substrate: pytest-as-orchestrator + this thin Python wrapper. See
``docs/audits/y_2_gate_b_0_runner_lang_spike.md`` for the design lock.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

EXIT_SUCCESS: Final = 0
EXIT_FAILURE: Final = 1
EXIT_NEEDS_OPERATOR: Final = 2
EXIT_CONFIG_ERROR: Final = 3

LAYERS: Final[tuple[str, ...]] = (
    "pyright",
    "unit",
    "db",
    "deploy",
    "api",
    "browser",
)

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
RUNS_DIR: Final = REPO_ROOT / "runs"


def _short_sha() -> str:
    """Return short git SHA, or 'nogit' if not in a repo."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "nogit"


def _is_dirty() -> bool:
    """True if the working tree has tracked modifications (b.10 lock — tracked-only).

    Untracked files are not treated as dirty (they're usually scratch / mid-edit
    new files, not deploy-blockers).
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--"],
            cwd=REPO_ROOT,
            check=False,
        )
    except FileNotFoundError:
        return False
    return result.returncode != 0


def create_run_id() -> str:
    """Y.2.gate.c.1 — `<utc-ts>-<short-sha>[-dirty]`.

    Stable, sortable, includes the dirty suffix so cross-run timing diffs
    don't compare a clean run against a dirty one and claim spurious drift.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sha = _short_sha()
    suffix = "-dirty" if _is_dirty() else ""
    return f"{ts}-{sha}{suffix}"


def _normalize_argv(argv: Sequence[str]) -> list[str]:
    """Pre-process argv so ``up_to=<layer>`` and ``up_to <layer>`` both work.

    The audit + PLAN spec uses ``up_to=<layer>``; argparse subcommands want
    ``up_to <layer>``. Split the leading token if it contains ``=``.
    """
    args = list(argv)
    if args and "=" in args[0]:
        verb, value = args[0].split("=", 1)
        return [verb, value, *args[1:]]
    return args


def cmd_up_to(args: argparse.Namespace) -> int:
    """Run the test chain up to and including the named layer."""
    run_id = create_run_id()
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"runner: run_id={run_id}")
    print(f"runner: run_dir={run_dir.relative_to(REPO_ROOT)}")
    print(f"runner: up_to={args.layer}")
    print("runner: skeleton — dispatch not implemented yet (Y.2.gate.c.5+)")
    return EXIT_SUCCESS


def cmd_up(args: argparse.Namespace) -> int:
    """Boot dependencies. scope = local | aws | all (default)."""
    print(f"runner: up scope={args.scope} — not implemented yet (Y.2.gate.l.2)")
    return EXIT_NEEDS_OPERATOR


def cmd_down(args: argparse.Namespace) -> int:
    """Tear down dependencies. scope = local | aws | all (default).

    Destructive — requires --yes (Y.2.gate.b.14.3 destructive-op opt-in)."""
    if not args.yes and not os.environ.get("QS_GEN_RUNNER_YES"):
        print("runner: 'down' is destructive — pass --yes (or set QS_GEN_RUNNER_YES=1)", file=sys.stderr)
        return EXIT_NEEDS_OPERATOR
    print(f"runner: down scope={args.scope} --yes — not implemented yet (Y.2.gate.l.2)")
    return EXIT_NEEDS_OPERATOR


def cmd_status(args: argparse.Namespace) -> int:
    """Show what's currently running. --cost for hourly cost estimate."""
    print(f"runner: status (cost={args.cost}) — not implemented yet (Y.2.gate.l.2)")
    return EXIT_NEEDS_OPERATOR


def cmd_sweep(args: argparse.Namespace) -> int:
    """Clean orphan resources tagged ManagedBy:quicksight-gen.

    Destructive — requires --yes."""
    if not args.yes and not os.environ.get("QS_GEN_RUNNER_YES"):
        print("runner: 'sweep' is destructive — pass --yes (or set QS_GEN_RUNNER_YES=1)", file=sys.stderr)
        return EXIT_NEEDS_OPERATOR
    print("runner: sweep --yes — not implemented yet (Y.2.gate.c.9)")
    return EXIT_NEEDS_OPERATOR


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_tests.sh",
        description="Test layer chain runner. See module docstring for full usage.",
    )
    subs = parser.add_subparsers(dest="verb", required=True)

    p_up_to = subs.add_parser("up_to", help="Run the chain up to and including <layer>")
    p_up_to.add_argument("layer", choices=LAYERS)
    p_up_to.set_defaults(func=cmd_up_to)

    p_up = subs.add_parser("up", help="Boot dependencies (default scope = all)")
    p_up.add_argument("scope", nargs="?", default="all", choices=["local", "aws", "all"])
    p_up.set_defaults(func=cmd_up)

    p_down = subs.add_parser("down", help="Tear down dependencies (default scope = all)")
    p_down.add_argument("scope", nargs="?", default="all", choices=["local", "aws", "all"])
    p_down.add_argument("--yes", action="store_true", help="confirm destructive op")
    p_down.set_defaults(func=cmd_down)

    p_status = subs.add_parser("status", help="Show what's currently running")
    p_status.add_argument("--cost", action="store_true", help="include hourly cost estimate")
    p_status.set_defaults(func=cmd_status)

    p_sweep = subs.add_parser("sweep", help="Clean orphan resources tagged ManagedBy:quicksight-gen")
    p_sweep.add_argument("--yes", action="store_true", help="confirm destructive op")
    p_sweep.set_defaults(func=cmd_sweep)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw = argv if argv is not None else sys.argv[1:]
    args = _build_parser().parse_args(_normalize_argv(raw))
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
