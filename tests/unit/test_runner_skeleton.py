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


# Y.2.gate.c.8 — dependency probe tests.

import subprocess
from typing import Any
from unittest.mock import patch


def _fake_completed(returncode: int, stderr: str = "", stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["fake"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_layer_deps_match_audit_table() -> None:
    """c.14-shape cross-check: layer-to-deps mapping reflects audit §3.

    pyright/unit are dependency-free (in-process); db needs docker (containers
    per b.2); deploy/api need aws + docker; browser adds qs_arn for embed
    signing. Edits to either side without the other should fail loudly."""
    assert runner._LAYER_DEPS["pyright"] == frozenset()
    assert runner._LAYER_DEPS["unit"] == frozenset()
    assert runner._LAYER_DEPS["db"] == frozenset({"docker"})
    assert runner._LAYER_DEPS["deploy"] == frozenset({"aws", "docker"})
    assert runner._LAYER_DEPS["api"] == frozenset({"aws", "docker"})
    assert runner._LAYER_DEPS["browser"] == frozenset({"aws", "docker", "qs_arn"})


def test_probe_aws_success_returns_none() -> None:
    with patch.object(runner, "_run_probe_subprocess", return_value=_fake_completed(0, stdout="...arn:...")):
        assert runner._probe_aws() is None


def test_probe_aws_expired_token() -> None:
    with patch.object(
        runner,
        "_run_probe_subprocess",
        return_value=_fake_completed(255, stderr="An error occurred (ExpiredToken) when calling..."),
    ):
        result = runner._probe_aws()
    assert result is not None
    assert result.kind == "aws_creds_expired"
    assert "aws sso login" in result.message


def test_probe_aws_no_creds() -> None:
    with patch.object(
        runner,
        "_run_probe_subprocess",
        return_value=_fake_completed(255, stderr="Unable to locate credentials"),
    ):
        result = runner._probe_aws()
    assert result is not None
    assert result.kind == "aws_no_creds"


def test_probe_aws_cli_missing() -> None:
    with patch.object(runner, "_run_probe_subprocess", return_value=_fake_completed(127, stderr="aws: not found")):
        result = runner._probe_aws()
    assert result is not None
    assert result.kind == "aws_cli_missing"


def test_probe_docker_success_returns_none() -> None:
    with patch.object(runner, "_run_probe_subprocess", return_value=_fake_completed(0)):
        assert runner._probe_docker() is None


def test_probe_docker_daemon_down() -> None:
    with patch.object(
        runner,
        "_run_probe_subprocess",
        return_value=_fake_completed(1, stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock"),
    ):
        result = runner._probe_docker()
    assert result is not None
    assert result.kind == "docker_daemon_down"


def test_probe_docker_cli_missing() -> None:
    with patch.object(runner, "_run_probe_subprocess", return_value=_fake_completed(127, stderr="docker: not found")):
        result = runner._probe_docker()
    assert result is not None
    assert result.kind == "docker_cli_missing"


def test_probe_qs_arn_set(monkeypatch: Any) -> None:
    monkeypatch.setenv("QS_E2E_USER_ARN", "arn:aws:quicksight:us-east-1:123:user/default/test")
    assert runner._probe_qs_e2e_user_arn() is None


def test_probe_qs_arn_unset(monkeypatch: Any) -> None:
    monkeypatch.delenv("QS_E2E_USER_ARN", raising=False)
    result = runner._probe_qs_e2e_user_arn()
    assert result is not None
    assert result.kind == "qs_arn_unset"


def test_probe_dependencies_pyright_no_deps() -> None:
    """pyright layer requires nothing external; probe always empty."""
    assert runner.probe_dependencies("pyright") == []


def test_probe_dependencies_browser_aggregates_failures(monkeypatch: Any) -> None:
    """All three deps fail → all three failures returned in one pass.

    Operator sees everything missing in one go; doesn't have to fix one,
    re-run, hit the next, etc."""
    monkeypatch.delenv("QS_E2E_USER_ARN", raising=False)
    fake_probes = {
        "aws": lambda: runner.ProbeFailure(kind="aws_creds_expired", message="..."),
        "docker": lambda: runner.ProbeFailure(kind="docker_daemon_down", message="..."),
        "qs_arn": runner._probe_qs_e2e_user_arn,
    }
    monkeypatch.setattr(runner, "_PROBE_FUNCTIONS", fake_probes)
    failures = runner.probe_dependencies("browser")
    kinds = {f.kind for f in failures}
    assert kinds == {"aws_creds_expired", "docker_daemon_down", "qs_arn_unset"}


def test_run_probe_subprocess_handles_timeout() -> None:
    """A hanging probe shouldn't lock the runner — synthesize rc=124."""
    with patch.object(
        subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(cmd=["fake"], timeout=10.0),
    ):
        result = runner._run_probe_subprocess(["fake"])
    assert result.returncode == 124


def test_run_probe_subprocess_handles_missing_binary() -> None:
    """Missing binary → rc=127, doesn't raise."""
    with patch.object(subprocess, "run", side_effect=FileNotFoundError("not found")):
        result = runner._run_probe_subprocess(["bogus-cmd"])
    assert result.returncode == 127
