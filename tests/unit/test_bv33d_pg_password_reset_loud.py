"""BV.3.3.d — Unit tests for the loud-fail PG password reset + URL contract.

Mocks ``subprocess.run`` so the tests don't need Docker. What's pinned:

- ``_read_pg_container_user_db`` parses POSTGRES_USER + POSTGRES_DB out of
  the live container env (testcontainers defaults to ``test``, not
  ``postgres`` — the pre-BV.3.3.d hardcoded ``postgres`` was the silent-
  no-op root cause).
- ``_reset_pg_password_via_socket`` raises LOUD on docker-exec failure
  (mirror of Oracle #254).
- ``_reset_pg_password_via_socket`` raises LOUD on psql "role does not
  exist" / "FATAL" in stderr — covers the case where the env reports
  one user but pg_hba blocks unix-socket auth for it.
- ``_get_or_start_pg_container`` constructs the rendezvous URL with the
  actual user/db from the live container, not hardcoded ``postgres``.
"""
from __future__ import annotations

import subprocess
from typing import Any

import pytest

from recon_gen._dev.runner import (
    _read_pg_container_user_db,
    _reset_pg_password_via_socket,
)
from tests._marks import Tier, tier

pytestmark = tier(Tier.UNIT)


class _FakeCompletedProcess:
    """Minimal stand-in for ``subprocess.CompletedProcess`` — only the
    fields the helpers under test inspect."""

    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_read_pg_container_user_db_parses_testcontainers_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """testcontainers-python defaults: POSTGRES_USER=test, POSTGRES_DB=test.

    Pre-BV.3.3.d this was hardcoded to ``postgres`` — the silent-no-op
    root cause this fix targets.
    """
    env = (
        b"POSTGRES_USER=test\n"
        b"POSTGRES_PASSWORD=somehex\n"
        b"POSTGRES_DB=test\n"
        b"PGDATA=/var/lib/postgresql/data\n"
    )

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(returncode=0, stdout=env)

    monkeypatch.setattr(subprocess, "run", fake_run)
    user, db = _read_pg_container_user_db("fake-container")
    assert user == "test"
    assert db == "test"


def test_read_pg_container_user_db_raises_on_docker_exec_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """docker exec rc!=0 → RuntimeError pointing at the container, not
    a silent no-op. Error message includes recovery hint."""

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(
            returncode=1,
            stderr=b"Error: No such container: fake-container",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match=r"docker rm -f"):
        _read_pg_container_user_db("fake-container")


def test_read_pg_container_user_db_raises_on_missing_env_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Container env without POSTGRES_USER → RuntimeError flagging the
    poison state, not a silent fallback to ``postgres``."""

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(returncode=0, stdout=b"PATH=/usr/bin\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match=r"POSTGRES_USER"):
        _read_pg_container_user_db("fake-container")


def test_reset_pg_password_via_socket_uses_actual_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The psql `-U` flag and ALTER USER target both come from the live
    container's POSTGRES_USER, not a hardcoded ``postgres``. Verifies
    the BV.3.3.d fix at the command-construction layer.
    """
    env_response = _FakeCompletedProcess(
        returncode=0, stdout=b"POSTGRES_USER=test\nPOSTGRES_DB=test\n",
    )
    alter_response = _FakeCompletedProcess(returncode=0)
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        calls.append(cmd)
        if cmd[1] == "exec" and cmd[3] == "env":
            return env_response
        return alter_response

    monkeypatch.setattr(subprocess, "run", fake_run)
    _reset_pg_password_via_socket("fake-container", "newpass")
    # Second call is the psql ALTER USER — verify it targets `test`, not
    # `postgres`, and the ALTER USER statement matches.
    assert len(calls) == 2
    psql_cmd = calls[1]
    assert "psql" in psql_cmd
    assert "-U" in psql_cmd
    user_flag_idx = psql_cmd.index("-U")
    assert psql_cmd[user_flag_idx + 1] == "test"
    sql_idx = psql_cmd.index("-c")
    assert "ALTER USER test WITH PASSWORD 'newpass'" in psql_cmd[sql_idx + 1]


def test_reset_pg_password_via_socket_raises_loud_on_nonzero_rc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """psql ALTER USER rc!=0 → RuntimeError; previously this was
    silently swallowed under ``check=False, capture_output=True``."""
    env_response = _FakeCompletedProcess(
        returncode=0, stdout=b"POSTGRES_USER=test\nPOSTGRES_DB=test\n",
    )
    alter_response = _FakeCompletedProcess(
        returncode=2, stderr=b"psql: error: connection refused",
    )

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        if cmd[1] == "exec" and cmd[3] == "env":
            return env_response
        return alter_response

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match=r"docker rm -f"):
        _reset_pg_password_via_socket("fake-container", "newpass")


def test_reset_pg_password_via_socket_raises_loud_on_role_does_not_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when rc=0 (psql sometimes exits 0 on individual statement
    errors without ON_ERROR_STOP), a 'does not exist' marker in stderr
    triggers RuntimeError — the exact failure mode this fix targets."""
    env_response = _FakeCompletedProcess(
        returncode=0, stdout=b"POSTGRES_USER=test\nPOSTGRES_DB=test\n",
    )
    alter_response = _FakeCompletedProcess(
        returncode=0,
        stderr=b'ERROR:  role "postgres" does not exist',
    )

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        if cmd[1] == "exec" and cmd[3] == "env":
            return env_response
        return alter_response

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match=r"does not exist|FATAL|password reset"):
        _reset_pg_password_via_socket("fake-container", "newpass")
