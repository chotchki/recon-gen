"""CB.17.d — Smoke tests for the EnvVar access log.

Every `EnvVar.get_or_none()` / `.require()` / `.serialize()` call is
recorded in a process-wide log. Strangler-pattern verification +
env-var death-list audit both consume this log:

- read_hit  → the env var was read AND had a value
- read_miss → the env var was read but unset/empty
- write     → the env var was serialized into a subprocess env

Combined: a var with only writes (no reads) is producer-only and the
producer can be deleted; only reads (no writes) is reader-only and
the absence-handling path is the only behavior; neither = dead.
"""
from __future__ import annotations

import pytest

from recon_gen.common.env_keys import (
    RECON_GEN_FUZZ_SEED,
    dump_env_access,
    reset_env_access,
)
from tests._marks import Tier, tier


pytestmark = tier(Tier.UNIT)


def test_read_miss_logs_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `get_or_none()` call on an unset var logs `read_miss`."""
    monkeypatch.delenv(RECON_GEN_FUZZ_SEED.name, raising=False)
    reset_env_access()
    _ = RECON_GEN_FUZZ_SEED.get_or_none()
    log = dump_env_access()
    assert (RECON_GEN_FUZZ_SEED.name, "read_miss") in log


def test_read_hit_logs_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `get_or_none()` call on a set var logs `read_hit`."""
    monkeypatch.setenv(RECON_GEN_FUZZ_SEED.name, "12345")
    reset_env_access()
    val = RECON_GEN_FUZZ_SEED.get_or_none()
    assert val == 12345
    log = dump_env_access()
    assert (RECON_GEN_FUZZ_SEED.name, "read_hit") in log


def test_write_logs_on_serialize() -> None:
    """A `.serialize(value)` call logs `write`."""
    reset_env_access()
    RECON_GEN_FUZZ_SEED.serialize(99)
    log = dump_env_access()
    assert (RECON_GEN_FUZZ_SEED.name, "write") in log


def test_reset_clears_log() -> None:
    """`reset_env_access()` empties the log."""
    RECON_GEN_FUZZ_SEED.serialize(1)
    assert len(dump_env_access()) > 0
    reset_env_access()
    assert dump_env_access() == []


def test_log_preserves_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reads and writes appear in call order."""
    monkeypatch.setenv(RECON_GEN_FUZZ_SEED.name, "7")
    reset_env_access()
    RECON_GEN_FUZZ_SEED.get_or_none()      # read_hit
    RECON_GEN_FUZZ_SEED.serialize(11)      # write
    monkeypatch.delenv(RECON_GEN_FUZZ_SEED.name, raising=False)
    RECON_GEN_FUZZ_SEED.get_or_none()      # read_miss
    log = [op for name, op in dump_env_access() if name == RECON_GEN_FUZZ_SEED.name]
    assert log == ["read_hit", "write", "read_miss"]
