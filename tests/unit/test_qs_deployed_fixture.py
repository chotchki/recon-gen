"""Unit tests for the ``qs_deployed`` session-autouse fixture in
``tests/e2e/conftest.py`` (DI phase POLICY 1 lock).

Coverage:

- Gate cascade: RECON_GEN_E2E unset → RECON_GEN_SKIP_QS_DEPLOY set →
  _session_needs_aws(False) → early return (no subprocess fire).
- Idempotency: sentinel-present skips the subprocess fire.
- Subprocess shape: argv matches ``recon-gen json apply --execute -c
  <cfg> --l2 <l2> -o <out>``.
- Failure path: non-zero subprocess rc → pytest.fail, sentinel NOT
  written.
- Success path: sentinel touched after a clean fire.
- Source-level lock: fixture body uses FileLock + sentinel pattern
  (cross-xdist-worker rendezvous mirrors
  ``tests/conftest.py::_install_pgcrypto_under_filelock``).

These tests exercise the fixture's body directly with monkey-patched
``subprocess.run`` / env vars / ``_session_needs_aws`` so the
behavior is provable without spinning a real QS deploy.
"""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from recon_gen.common.env_keys import (
    RECON_GEN_CONFIG,
    RECON_GEN_E2E,
    RECON_GEN_QS_CONFIG,
    RECON_GEN_RUN_DIR,
    RECON_GEN_SKIP_QS_DEPLOY,
    RECON_GEN_TEST_L2_INSTANCE,
)
from tests.e2e import conftest as e2e_conftest


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _mk_completed(
    *, returncode: int = 0, stdout: str = "", stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr,
    )


def _mk_tmp_path_factory(
    base: Path, monkeypatch: pytest.MonkeyPatch,
) -> pytest.TempPathFactory:
    """Build a real TempPathFactory rooted at ``base``.

    ``getbasetemp().parent`` is where the sentinel lives. We point
    that parent at ``base`` directly by giving the factory's getbasetemp
    a subdir of base; the fixture's ``.parent`` walks to base itself.
    """
    fake = MagicMock(spec=pytest.TempPathFactory)
    basetemp = base / "session_basetemp"
    basetemp.mkdir(parents=True, exist_ok=True)
    fake.getbasetemp.return_value = basetemp

    def _mktemp(name: str, numbered: bool = True) -> Path:
        del numbered
        p = base / f"mktemp_{name}"
        p.mkdir(parents=True, exist_ok=True)
        return p
    fake.mktemp.side_effect = _mktemp
    return fake


def _mk_session(needs_aws: bool) -> Any:
    """Build a minimal pytest.Session-like object recognized by
    ``_session_needs_aws`` (caches the flag on the object)."""
    fake = MagicMock()
    fake._recon_aws_required = needs_aws  # _session_needs_aws short-circuits on this
    return fake


def _mk_request(needs_aws: bool = True) -> Any:
    fake = MagicMock()
    fake.session = _mk_session(needs_aws=needs_aws)
    return fake


def _run_fixture(
    *, request: Any, cfg: Any, tmp_path_factory: pytest.TempPathFactory,
    worker_id: str = "master",
) -> None:
    """Invoke the fixture function body directly (bypassing pytest's
    fixture machinery — we want unit-level control).
    """
    e2e_conftest.qs_deployed.__wrapped__(  # type: ignore[attr-defined]: pytest decorators preserve the wrapped function
        request=request, cfg=cfg,
        tmp_path_factory=tmp_path_factory, worker_id=worker_id,
    )


def _arm_required_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    """Set the env vars the fixture reads on the happy path."""
    monkeypatch.setenv(RECON_GEN_E2E.name, "1")
    monkeypatch.delenv(RECON_GEN_SKIP_QS_DEPLOY.name, raising=False)
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text("placeholder")
    l2_path = tmp_path / "l2.yaml"
    l2_path.write_text("placeholder")
    monkeypatch.setenv(RECON_GEN_CONFIG.name, str(cfg_path))
    monkeypatch.setenv(RECON_GEN_TEST_L2_INSTANCE.name, str(l2_path))
    monkeypatch.delenv(RECON_GEN_QS_CONFIG.name, raising=False)
    monkeypatch.delenv(RECON_GEN_RUN_DIR.name, raising=False)
    return cfg_path, l2_path


# ---------------------------------------------------------------------------
# Gate cascade — early returns must NOT fire the subprocess.
# ---------------------------------------------------------------------------


def test_qs_deployed_skips_when_e2e_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """RECON_GEN_E2E unset → no subprocess fire (unit / non-e2e sessions)."""
    monkeypatch.delenv(RECON_GEN_E2E.name, raising=False)
    request = _mk_request(needs_aws=True)
    tpf = _mk_tmp_path_factory(tmp_path, monkeypatch)
    with patch.object(subprocess, "run") as run_mock:
        _run_fixture(request=request, cfg=MagicMock(), tmp_path_factory=tpf)
    run_mock.assert_not_called()


def test_qs_deployed_skips_when_skip_env_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """RECON_GEN_SKIP_QS_DEPLOY set → operator escape hatch fires; no subprocess."""
    monkeypatch.setenv(RECON_GEN_E2E.name, "1")
    monkeypatch.setenv(RECON_GEN_SKIP_QS_DEPLOY.name, "1")
    request = _mk_request(needs_aws=True)
    tpf = _mk_tmp_path_factory(tmp_path, monkeypatch)
    with patch.object(subprocess, "run") as run_mock:
        _run_fixture(request=request, cfg=MagicMock(), tmp_path_factory=tpf)
    run_mock.assert_not_called()


def test_qs_deployed_skips_when_session_doesnt_need_aws(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """_session_needs_aws(session) == False → no subprocess fire (db /
    app2 tier sessions inherit this conftest but their fixtures don't
    touch AWS)."""
    monkeypatch.setenv(RECON_GEN_E2E.name, "1")
    monkeypatch.delenv(RECON_GEN_SKIP_QS_DEPLOY.name, raising=False)
    request = _mk_request(needs_aws=False)
    tpf = _mk_tmp_path_factory(tmp_path, monkeypatch)
    with patch.object(subprocess, "run") as run_mock:
        _run_fixture(request=request, cfg=MagicMock(), tmp_path_factory=tpf)
    run_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Idempotency — sentinel present → skip the subprocess fire.
# ---------------------------------------------------------------------------


def test_qs_deployed_skips_when_sentinel_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Sentinel file present → second worker / second session call sees
    it under the FileLock and bails before invoking the subprocess.

    This is THE idempotency guarantee: only the first worker in a
    pytest-xdist session pays the deploy cost. Mirrors
    _install_pgcrypto_under_filelock's contract.
    """
    _arm_required_env(monkeypatch, tmp_path)
    tpf = _mk_tmp_path_factory(tmp_path, monkeypatch)
    # Sentinel lives at getbasetemp().parent / "qs-deployed.sentinel" —
    # the fixture builds that path via tmp_path_factory.getbasetemp().parent.
    sentinel = tpf.getbasetemp().parent / "qs-deployed.sentinel"
    sentinel.touch()

    request = _mk_request(needs_aws=True)
    with patch.object(subprocess, "run") as run_mock:
        _run_fixture(request=request, cfg=MagicMock(), tmp_path_factory=tpf)
    run_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Happy path — subprocess fired with the expected argv shape;
# sentinel touched on success.
# ---------------------------------------------------------------------------


def test_qs_deployed_subprocess_argv_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Successful path fires ``recon-gen json apply --execute -c <cfg>
    --l2 <l2> -o <out_dir>`` and touches the sentinel.
    """
    cfg_path, l2_path = _arm_required_env(monkeypatch, tmp_path)
    tpf = _mk_tmp_path_factory(tmp_path, monkeypatch)
    request = _mk_request(needs_aws=True)
    with patch.object(
        subprocess, "run",
        return_value=_mk_completed(returncode=0, stdout="deployed ok"),
    ) as run_mock:
        _run_fixture(request=request, cfg=MagicMock(), tmp_path_factory=tpf)
    assert run_mock.call_count == 1
    (argv,), _ = run_mock.call_args
    # argv shape — last 8 tokens.
    assert "json" in argv
    assert "apply" in argv
    assert "--execute" in argv
    cfg_idx = argv.index("-c")
    assert argv[cfg_idx + 1] == str(cfg_path)
    l2_idx = argv.index("--l2")
    assert argv[l2_idx + 1] == str(l2_path)
    assert "-o" in argv
    # Sentinel touched on success.
    sentinel = tpf.getbasetemp().parent / "qs-deployed.sentinel"
    assert sentinel.is_file()


def test_qs_deployed_prefers_qs_config_over_local_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """When both RECON_GEN_QS_CONFIG and RECON_GEN_CONFIG are set, the
    QS cfg (hotchkiss.io URL) wins — matches the runner's qs_layers
    routing rule.
    """
    _arm_required_env(monkeypatch, tmp_path)
    qs_cfg = tmp_path / "qs.yaml"
    qs_cfg.write_text("placeholder")
    monkeypatch.setenv(RECON_GEN_QS_CONFIG.name, str(qs_cfg))
    tpf = _mk_tmp_path_factory(tmp_path, monkeypatch)
    request = _mk_request(needs_aws=True)
    with patch.object(
        subprocess, "run", return_value=_mk_completed(returncode=0),
    ) as run_mock:
        _run_fixture(request=request, cfg=MagicMock(), tmp_path_factory=tpf)
    (argv,), _ = run_mock.call_args
    cfg_idx = argv.index("-c")
    assert argv[cfg_idx + 1] == str(qs_cfg)


# ---------------------------------------------------------------------------
# Failure path — non-zero subprocess rc → pytest.fail, sentinel NOT touched.
# ---------------------------------------------------------------------------


def test_qs_deployed_failure_doesnt_touch_sentinel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Subprocess rc != 0 → pytest.fail. Sentinel NOT written so the
    next session re-fires the deploy (delete-then-create handles
    partial state)."""
    _arm_required_env(monkeypatch, tmp_path)
    tpf = _mk_tmp_path_factory(tmp_path, monkeypatch)
    request = _mk_request(needs_aws=True)
    with patch.object(
        subprocess, "run",
        return_value=_mk_completed(
            returncode=1, stdout="...", stderr="deploy boom",
        ),
    ):
        # pytest.fail raises ``_pytest.outcomes.Failed`` which is a
        # ``BaseException`` subclass but NOT an ``Exception`` subclass
        # (per pytest's outcomes contract). Catch via BaseException.
        with pytest.raises(BaseException) as exc_info:  # noqa: B017, PT011 — see comment above
            _run_fixture(
                request=request, cfg=MagicMock(), tmp_path_factory=tpf,
            )
    assert "deploy subprocess failed" in str(exc_info.value)
    sentinel = tpf.getbasetemp().parent / "qs-deployed.sentinel"
    assert not sentinel.exists()


# ---------------------------------------------------------------------------
# Source-level locks — body uses FileLock + sentinel pattern.
# ---------------------------------------------------------------------------


def test_qs_deployed_uses_filelock_for_xdist_rendezvous() -> None:
    """Source-level lock: fixture body uses ``FileLock`` to serialize
    deploy across xdist workers. The lock acquire wraps the sentinel
    check + subprocess fire so first-to-the-lock wins; followers see
    the sentinel.
    """
    body = inspect.getsource(e2e_conftest.qs_deployed)
    assert "from filelock import FileLock" in body
    assert "FileLock(lock_path, timeout=" in body
    assert "qs-deployed.sentinel" in body


def test_qs_deployed_invokes_json_apply_execute() -> None:
    """Source-level lock: subprocess argv shape stays ``recon-gen
    json apply --execute``. Locks the design lock against drift to a
    custom CLI verb."""
    body = inspect.getsource(e2e_conftest.qs_deployed)
    assert '"json"' in body
    assert '"apply"' in body
    assert '"--execute"' in body
    # Subprocess form, not in-process.
    assert "subprocess.run" in body


def test_qs_deployed_pre_warm_depends_on_deploy() -> None:
    """``_qs_pre_warm_dashboards`` has ``qs_deployed`` in its signature
    so pytest orders the autouse fixtures: deploy first, then warm.
    Without this dep, both session-autouse fixtures order arbitrarily.
    """
    sig = inspect.signature(e2e_conftest._qs_pre_warm_dashboards)
    assert "qs_deployed" in sig.parameters
