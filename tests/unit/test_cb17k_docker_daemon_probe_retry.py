"""CB.17.k — Unit tests for the Docker daemon readiness probe + retry.

Pins the bounded-retry behavior added to ``_probe_docker``: post-macOS-
reboot, Docker Desktop's daemon takes ~30-60s to be fully responsive;
before this fix the single-shot probe declared the daemon down on the
first attempt, the runner swallowed the warning, and 32s of cascading
db-tier failures followed. The retry budget (3 attempts at 5s/10s/20s
= ~35s ceiling) covers the lag window; terminal failure surfaces as
the same ``docker_daemon_down`` ProbeFailure with an updated message.

What's pinned:

- Fast-path: ``docker ps`` rc=0 → ``None`` (no retries, no sleep).
- CLI missing (rc=127) → ``docker_cli_missing`` on first attempt.
- Non-daemon-down stderr → ``docker_check_failed`` on first attempt.
- Daemon-down then daemon-up → retries, returns ``None`` on success.
- Daemon-down on every attempt → ``docker_daemon_down`` after full
  budget; message references the 35s ceiling.
"""
from __future__ import annotations

import subprocess
from typing import Any

import pytest

from recon_gen._dev import runner
from recon_gen._dev.runner import _probe_docker
from tests._marks import Tier, tier

pytestmark = tier(Tier.UNIT)


def _stub_probe_subprocess(
    monkeypatch: pytest.MonkeyPatch, results: list[subprocess.CompletedProcess[str]],
) -> list[int]:
    """Replace ``_run_probe_subprocess`` with a script that yields
    ``results[i]`` on the i'th call. Returns a one-element call counter
    the test can assert against (so we can pin "fast-path = 1 call").
    """
    calls = [0]

    def fake_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        idx = calls[0]
        calls[0] = idx + 1
        if idx < len(results):
            return results[idx]
        # Past the scripted list → repeat the last (covers all 4 attempts
        # without re-listing 4×).
        return results[-1]

    monkeypatch.setattr(runner, "_run_probe_subprocess", fake_run)
    return calls


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace ``time.sleep`` with a recorder so retry tests run instantly
    but still pin the backoff schedule was honored."""
    slept: list[float] = []

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(runner.time, "sleep", fake_sleep)
    return slept


def test_probe_docker_fast_path_no_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """``docker ps`` rc=0 → returns None on the first call, no sleeps."""
    calls = _stub_probe_subprocess(monkeypatch, [
        subprocess.CompletedProcess(args=["docker", "ps"], returncode=0, stdout="", stderr=""),
    ])
    slept = _no_sleep(monkeypatch)

    assert _probe_docker() is None
    assert calls[0] == 1
    assert slept == []


def test_probe_docker_cli_missing_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """rc=127 (CLI not found) → ``docker_cli_missing`` on first attempt,
    no retries — this is not a transient failure."""
    calls = _stub_probe_subprocess(monkeypatch, [
        subprocess.CompletedProcess(args=["docker", "ps"], returncode=127, stdout="", stderr="docker: not found"),
    ])
    slept = _no_sleep(monkeypatch)

    failure = _probe_docker()
    assert failure is not None
    assert failure.kind == "docker_cli_missing"
    assert calls[0] == 1
    assert slept == []


def test_probe_docker_non_daemon_down_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-daemon-down stderr (e.g. permission denied) → ``docker_check_failed``
    on first attempt — not transient, no retries."""
    calls = _stub_probe_subprocess(monkeypatch, [
        subprocess.CompletedProcess(
            args=["docker", "ps"], returncode=1, stdout="",
            stderr="permission denied while trying to connect",
        ),
    ])
    slept = _no_sleep(monkeypatch)

    failure = _probe_docker()
    assert failure is not None
    assert failure.kind == "docker_check_failed"
    assert calls[0] == 1
    assert slept == []


def test_probe_docker_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Daemon-down on attempt 1, up on attempt 2 → returns None;
    one backoff (5s) was honored. Pins the lag-window recovery path."""
    daemon_down = subprocess.CompletedProcess(
        args=["docker", "ps"], returncode=1, stdout="",
        stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock.",
    )
    daemon_up = subprocess.CompletedProcess(
        args=["docker", "ps"], returncode=0, stdout="", stderr="",
    )
    calls = _stub_probe_subprocess(monkeypatch, [daemon_down, daemon_up])
    slept = _no_sleep(monkeypatch)

    assert _probe_docker() is None
    assert calls[0] == 2
    assert slept == [5.0]


def test_probe_docker_full_budget_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Daemon-down on every attempt → ``docker_daemon_down`` after the
    full 5s/10s/20s budget. Pins the actionable message + the ~35s ceiling
    so an operator post-reboot sees "wait longer" guidance."""
    daemon_down = subprocess.CompletedProcess(
        args=["docker", "ps"], returncode=1, stdout="",
        stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock.",
    )
    calls = _stub_probe_subprocess(monkeypatch, [daemon_down])
    slept = _no_sleep(monkeypatch)

    failure = _probe_docker()
    assert failure is not None
    assert failure.kind == "docker_daemon_down"
    assert "35s" in failure.message
    assert "open -a Docker" in failure.message
    # 1 initial + 3 retries = 4 total `docker ps` calls.
    assert calls[0] == 4
    assert slept == [5.0, 10.0, 20.0]


def test_probe_docker_mid_retry_shape_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the failure shape changes mid-retry (daemon-down → permission
    denied), surface as ``docker_check_failed`` immediately — don't
    keep burning the backoff budget on a non-transient failure."""
    daemon_down = subprocess.CompletedProcess(
        args=["docker", "ps"], returncode=1, stdout="",
        stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock.",
    )
    other_failure = subprocess.CompletedProcess(
        args=["docker", "ps"], returncode=1, stdout="",
        stderr="permission denied",
    )
    calls = _stub_probe_subprocess(monkeypatch, [daemon_down, other_failure])
    slept = _no_sleep(monkeypatch)

    failure = _probe_docker()
    assert failure is not None
    assert failure.kind == "docker_check_failed"
    assert calls[0] == 2
    # Only the first backoff fired before the shape changed.
    assert slept == [5.0]
