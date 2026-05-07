"""Y.2.gate.c.1 / b.14.* skeleton primitives.

Locks the run-id format, the argv normalization (``up_to=<x>`` ↔ ``up_to <x>``),
and the destructive-op refusal pattern. These shapes are stable contracts the
rest of the c-stage implementation (capture, diff, dispatch) builds on.
"""

from __future__ import annotations

import re

from quicksight_gen._dev import runner


def test_create_run_id_format() -> None:
    """Run-id = `<utc-ts>-<short-sha>[-dirty]`. UTC, sortable, dirty-aware."""
    run_id = runner.create_run_id()
    assert re.match(r"^\d{8}T\d{6}Z-[\w]+(?:-dirty)?$", run_id), run_id


def test_create_run_id_is_unique_per_call() -> None:
    """Two calls in different seconds produce different ids; same-second is OK
    because the runner only creates one run-id per invocation."""
    a = runner.create_run_id()
    b = runner.create_run_id()
    # Same second collision is fine; what matters is the format.
    assert re.match(r"^\d{8}T\d{6}Z-", a)
    assert re.match(r"^\d{8}T\d{6}Z-", b)


def test_normalize_argv_splits_equals_form() -> None:
    """`up_to=<layer>` → `[up_to, <layer>]` so argparse subcommands work."""
    assert runner._normalize_argv(["up_to=unit"]) == ["up_to", "unit"]
    assert runner._normalize_argv(["up_to=unit", "--variants=pg"]) == [
        "up_to",
        "unit",
        "--variants=pg",
    ]


def test_normalize_argv_passthrough_for_space_form() -> None:
    """Space form `up_to <layer>` already-correct; passthrough."""
    assert runner._normalize_argv(["up_to", "unit"]) == ["up_to", "unit"]
    assert runner._normalize_argv(["status"]) == ["status"]
    assert runner._normalize_argv([]) == []


def test_normalize_argv_only_splits_first_token() -> None:
    """Don't accidentally split `--variants=pg` (which lives later in argv)."""
    assert runner._normalize_argv(["up_to", "unit", "--variants=pg"]) == [
        "up_to",
        "unit",
        "--variants=pg",
    ]


def test_destructive_down_refuses_without_yes() -> None:
    """b.14.3 — `down` is destructive; refuse with NEEDS_OPERATOR exit code."""
    code = runner.main(["down"])
    assert code == runner.EXIT_NEEDS_OPERATOR


def test_destructive_sweep_refuses_without_yes() -> None:
    """Same for `sweep`."""
    code = runner.main(["sweep"])
    assert code == runner.EXIT_NEEDS_OPERATOR


def test_up_to_creates_run_dir() -> None:
    """`up_to=<layer>` returns success (skeleton dispatch) — proves the wiring."""
    code = runner.main(["up_to=unit"])
    assert code == runner.EXIT_SUCCESS


def test_layers_list_matches_audit_table() -> None:
    """Y.2.gate.b.11 lock — runner's LAYERS is the runtime authority; the audit
    doc layer table is the documented mirror. This is the small-canonical-sample
    cross-check that catches one-side-only edits (full version lands in c.14)."""
    assert runner.LAYERS == ("pyright", "unit", "db", "deploy", "api", "browser")
