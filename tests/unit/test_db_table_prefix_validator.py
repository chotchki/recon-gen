"""CR.4 — ``cfg.db_table_prefix`` validation.

Pre-CR.4 the env-var description claimed "snake_case, ≤30 chars" and
primitives.py claimed config.py enforced it — neither did. A long
prefix only surfaced as ``ORA-00972: identifier is too long`` deep
in DDL. CR.4 wires ``validate_db_table_prefix`` at both the env-var
boundary and ``config.py``'s yaml loader so the rename surfaces at
config-load with the field name + budget calculation.
"""

from __future__ import annotations

import pytest

from recon_gen.common.env_keys import (
    DB_TABLE_PREFIX_MAX,
    RECON_GEN_DB_TABLE_PREFIX,
    EnvVarInvalid,
    validate_db_table_prefix,
)
from recon_gen.common.env_keys import _LONGEST_KNOWN_SUFFIX  # noqa: PLC2701 — anti-drift: a rename in production must fail the test loudly


# ---------------------------------------------------------------------------
# validate_db_table_prefix — per-shape behavior
# ---------------------------------------------------------------------------


def test_snake_case_within_cap_passes() -> None:
    # Existing operator-authored prefixes that already ship.
    validate_db_table_prefix("qsgen_postgres")
    validate_db_table_prefix("sasquatch_ar")
    validate_db_table_prefix("a")


def test_max_length_at_cap_passes() -> None:
    validate_db_table_prefix("a" * DB_TABLE_PREFIX_MAX)


def test_uppercase_rejected() -> None:
    with pytest.raises(ValueError, match="snake_case"):
        validate_db_table_prefix("Recon_Demo")


def test_leading_digit_rejected() -> None:
    with pytest.raises(ValueError, match="snake_case"):
        validate_db_table_prefix("1demo")


def test_leading_underscore_rejected() -> None:
    with pytest.raises(ValueError, match="snake_case"):
        validate_db_table_prefix("_demo")


def test_hyphen_rejected() -> None:
    with pytest.raises(ValueError, match="snake_case"):
        validate_db_table_prefix("recon-demo")


def test_over_cap_rejected_with_budget_breakdown() -> None:
    """The loud-fail message must call out the field length, the
    longest suffix the codebase emits, and the resulting identifier
    length — operator needs all three to choose a new prefix."""
    too_long = "a" * (DB_TABLE_PREFIX_MAX + 1)
    with pytest.raises(ValueError) as ei:
        validate_db_table_prefix(too_long)
    msg = str(ei.value)
    # Field length + cap surfaced.
    assert str(len(too_long)) in msg
    assert str(DB_TABLE_PREFIX_MAX) in msg
    # Codebase's longest suffix named so the operator knows what
    # drives the budget.
    assert _LONGEST_KNOWN_SUFFIX in msg
    # Dialect identifier limits surfaced.
    assert "63" in msg  # PostgreSQL NAMEDATALEN
    assert "128" in msg  # Oracle 19c+ default


# ---------------------------------------------------------------------------
# Env-var path — validator is wired
# ---------------------------------------------------------------------------


def test_env_override_rejects_bad_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting an invalid value via env must raise at access time
    (the runner / CLI surfaces this as EXIT_NEEDS_OPERATOR=2)."""
    monkeypatch.setenv("RECON_GEN_DB_TABLE_PREFIX", "WRONG_CASE")  # typing-smell: ignore[envvar-bypass]: testing the env-var validator's rejection path — needs raw set
    with pytest.raises(EnvVarInvalid):
        RECON_GEN_DB_TABLE_PREFIX.get_or_none()


def test_env_override_accepts_valid_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECON_GEN_DB_TABLE_PREFIX", "ci_run_42")  # typing-smell: ignore[envvar-bypass]: testing the env-var validator's accept path — needs raw set
    assert RECON_GEN_DB_TABLE_PREFIX.get_or_none() == "ci_run_42"


# ---------------------------------------------------------------------------
# config.py loader path — validator fires at cfg-load
# ---------------------------------------------------------------------------


def test_yaml_loader_rejects_overlong_prefix(tmp_path: object) -> None:
    """A bad ``db_table_prefix:`` in the cfg yaml must raise from
    ``load_config`` (pre-CR.4 it loaded silently and crashed at the
    first CREATE TABLE)."""
    from pathlib import Path

    from recon_gen.common.config import load_config

    assert isinstance(tmp_path, Path)
    cfg_yaml = tmp_path / "cfg.yaml"
    cfg_yaml.write_text(
        "aws_account_id: \"123\"\n"
        "aws_region: \"us-east-1\"\n"
        "deployment_name: \"recon-demo\"\n"
        f"db_table_prefix: \"{'a' * (DB_TABLE_PREFIX_MAX + 5)}\"\n"
        "dialect: postgres\n"
        # demo_database_url present so datasource_arn can derive
        # without the operator needing real AWS creds for the test.
        "demo_database_url: \"postgresql://u:p@h:5432/db\"\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="db_table_prefix"):
        load_config(str(cfg_yaml))
