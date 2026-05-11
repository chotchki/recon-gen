"""V.1.b — config.yaml ↔ L2 institution YAML boundary enforcement.

The loader is the single chokepoint that distinguishes "a config file
the operator typed by hand" from "any other YAML in the repo". The
strict-allowlist behavior here turns every silent typo (theme: in
config.yaml, l2_instance_prefix hardcoded) into a loud failure with a
pointer at where the field actually belongs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from quicksight_gen.common.config import load_config
from quicksight_gen.common.env_keys import (
    QS_GEN_AWS_ACCOUNT_ID,
    QS_GEN_AWS_REGION,
    QS_GEN_DATASOURCE_ARN,
    QS_GEN_DEMO_DATABASE_URL,
)


def _write_yaml(tmp_path: Path, body: dict) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(body), encoding="utf-8")
    return p


def test_minimal_valid_config_loads(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, {
        "aws_account_id": "111122223333",
        "aws_region": "us-east-1",
        "datasource_arn": "arn:aws:quicksight:us-east-1:111122223333:datasource/x",
    })
    cfg = load_config(p)
    assert cfg.aws_account_id == "111122223333"


def test_full_valid_config_loads(tmp_path: Path) -> None:
    """Every allowlisted key together — sanity check the allowlist
    isn't accidentally too narrow."""
    p = _write_yaml(tmp_path, {
        "aws_account_id": "111122223333",
        "aws_region": "us-east-2",
        "datasource_arn": "arn:aws:quicksight:us-east-2:111122223333:datasource/x",
        "resource_prefix": "qs-gen-test",
        "principal_arns": ["arn:aws:iam::111122223333:user/u"],
        "extra_tags": {"Owner": "team"},
        "demo_database_url": "postgresql://u:p@h:5432/d",
        "dialect": "postgres",
        "signing": {
            "key_path": "k.pem",
            "cert_path": "c.pem",
        },
        "tagging_enabled": False,
    })
    cfg = load_config(p)
    assert cfg.signing is not None
    assert cfg.dialect.value == "postgres"
    assert cfg.tagging_enabled is False


def test_tagging_enabled_defaults_to_true(tmp_path: Path) -> None:
    """The override is opt-in. Omitting it leaves cleanup's
    fail-CLOSED tag-based isolation intact."""
    p = _write_yaml(tmp_path, {
        "aws_account_id": "111122223333",
        "aws_region": "us-east-1",
        "datasource_arn": "arn:aws:quicksight:us-east-1:111122223333:datasource/x",
    })
    cfg = load_config(p)
    assert cfg.tagging_enabled is True


def test_tagging_enabled_false_omits_tags_kwarg(tmp_path: Path) -> None:
    """``cfg.tags()`` returns ``None`` when tagging is disabled —
    ``_strip_nones`` then drops the ``Tags`` field from the AWS JSON
    so the boto3 ``Create*`` call carries no ``Tags`` kwarg, keeping
    the IAM principal off ``quicksight:TagResource``."""
    p = _write_yaml(tmp_path, {
        "aws_account_id": "111122223333",
        "aws_region": "us-east-1",
        "datasource_arn": "arn:aws:quicksight:us-east-1:111122223333:datasource/x",
        "tagging_enabled": False,
    })
    cfg = load_config(p)
    assert cfg.tags() is None


def test_tagging_enabled_true_populates_tags_kwarg(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, {
        "aws_account_id": "111122223333",
        "aws_region": "us-east-1",
        "datasource_arn": "arn:aws:quicksight:us-east-1:111122223333:datasource/x",
        "resource_prefix": "qs-customprefix",
        "extra_tags": {"Owner": "team"},
    })
    cfg = load_config(p)
    tags = cfg.tags()
    assert tags is not None
    keys = {tag.Key for tag in tags}
    assert {"ManagedBy", "ResourcePrefix", "Owner"} <= keys


def test_tagging_enabled_non_bool_rejected(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, {
        "aws_account_id": "111122223333",
        "aws_region": "us-east-1",
        "datasource_arn": "arn:aws:quicksight:us-east-1:111122223333:datasource/x",
        "tagging_enabled": "false",  # YAML string, not bool
    })
    with pytest.raises(ValueError, match="tagging_enabled must be a bool"):
        load_config(p)


@pytest.mark.parametrize("leaked_key", [
    "theme", "persona", "rails", "accounts", "chains",
    "transfer_templates", "account_templates", "limit_schedules",
    "instance", "description",
])
def test_l2_only_key_in_config_yaml_rejects(
    tmp_path: Path, leaked_key: str,
) -> None:
    """Dropping any L2 institution field into config.yaml is the most
    common misedit. Each one must error with a pointer at the L2 YAML."""
    p = _write_yaml(tmp_path, {
        "aws_account_id": "111122223333",
        "aws_region": "us-east-1",
        "datasource_arn": "arn:aws:quicksight:us-east-1:111122223333:datasource/x",
        leaked_key: "anything",
    })
    with pytest.raises(ValueError, match="L2 institution YAML"):
        load_config(p)


def test_l2_instance_prefix_in_config_yaml_rejects(tmp_path: Path) -> None:
    """The prefix is computed from the L2 instance.instance field at
    CLI time. Hand-setting it in config.yaml is a sign the user has
    bypassed `--l2`."""
    p = _write_yaml(tmp_path, {
        "aws_account_id": "111122223333",
        "aws_region": "us-east-1",
        "datasource_arn": "arn:aws:quicksight:us-east-1:111122223333:datasource/x",
        "l2_instance_prefix": "spec_example",
    })
    with pytest.raises(ValueError, match="derived from the L2"):
        load_config(p)


def test_unknown_key_rejects(tmp_path: Path) -> None:
    """Random typos / stale keys don't sneak through silently."""
    p = _write_yaml(tmp_path, {
        "aws_account_id": "111122223333",
        "aws_region": "us-east-1",
        "datasource_arn": "arn:aws:quicksight:us-east-1:111122223333:datasource/x",
        "theme_preset": "sasquatch-bank",  # removed in N.4
    })
    with pytest.raises(ValueError, match="unknown config keys"):
        load_config(p)


def test_legacy_principal_arn_singular_still_works(tmp_path: Path) -> None:
    """Backwards compat — singular `principal_arn` accepted alongside
    the canonical plural form."""
    p = _write_yaml(tmp_path, {
        "aws_account_id": "111122223333",
        "aws_region": "us-east-1",
        "datasource_arn": "arn:aws:quicksight:us-east-1:111122223333:datasource/x",
        "principal_arn": "arn:aws:iam::111122223333:user/legacy",
    })
    cfg = load_config(p)
    assert cfg.principal_arns == ["arn:aws:iam::111122223333:user/legacy"]


def test_run_postgres_config_still_loads() -> None:
    """Sanity: the operator's actual postgres config in run/ still
    parses cleanly under the new strict rules."""
    p = Path(__file__).parent.parent.parent / "run" / "config.postgres.yaml"
    if not p.exists():
        pytest.skip(f"{p} not present")
    cfg = load_config(p)
    assert cfg.dialect.value == "postgres"


def test_run_oracle_config_still_loads() -> None:
    p = Path(__file__).parent.parent.parent / "run" / "config.oracle.yaml"
    if not p.exists():
        pytest.skip(f"{p} not present")
    cfg = load_config(p)
    assert cfg.dialect.value == "oracle"


# --- Y.2.gate.h.5 — loud failure on missing required config ---


def test_missing_aws_account_id_fails_loud_with_env_var_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gate.h.5 — when a required field is missing from cfg yaml AND
    the env-var fallback isn't set, the loader fails loud with a
    message naming both the missing key and its env-var fallback so
    the operator knows exactly what to fix.

    Clear the env vars so the loader's env-fallback path can't quietly
    fill them — we're testing the missing-everything case.
    """
    monkeypatch.delenv(QS_GEN_AWS_ACCOUNT_ID.name, raising=False)
    monkeypatch.delenv(QS_GEN_AWS_REGION.name, raising=False)
    monkeypatch.delenv(QS_GEN_DATASOURCE_ARN.name, raising=False)

    p = _write_yaml(tmp_path, {
        # aws_account_id deliberately absent — also missing from env.
        "aws_region": "us-east-1",
        "datasource_arn": "arn:aws:quicksight:us-east-1:111122223333:datasource/x",
    })
    with pytest.raises(ValueError) as exc_info:
        load_config(p)
    msg = str(exc_info.value)
    assert "Missing required configuration" in msg, (
        f"loud-fail message must lead with 'Missing required configuration'; "
        f"got: {msg}"
    )
    assert "aws_account_id" in msg, (
        f"loud-fail message must name the missing key; got: {msg}"
    )
    assert "QS_GEN_AWS_ACCOUNT_ID" in msg, (
        f"loud-fail message must surface the env-var fallback so the "
        f"operator knows the alternative; got: {msg}"
    )


def test_missing_datasource_arn_without_demo_url_fails_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gate.h.5 — datasource_arn is required UNLESS demo_database_url
    is set (the latter auto-derives the former). Without either, fail
    loud with the missing key + env-var fallback."""
    monkeypatch.delenv(QS_GEN_DATASOURCE_ARN.name, raising=False)
    monkeypatch.delenv(QS_GEN_DEMO_DATABASE_URL.name, raising=False)

    p = _write_yaml(tmp_path, {
        "aws_account_id": "111122223333",
        "aws_region": "us-east-1",
        # neither datasource_arn nor demo_database_url
    })
    with pytest.raises(ValueError) as exc_info:
        load_config(p)
    msg = str(exc_info.value)
    assert "datasource_arn" in msg
    assert "QS_GEN_DATASOURCE_ARN" in msg


def test_demo_database_url_satisfies_datasource_arn_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gate.h.5 sister: when demo_database_url IS set, datasource_arn
    is auto-derived from it — no loud-fail. Locks the contract that
    the missing-cfg check is necessity-aware, not just a blanket key
    list."""
    monkeypatch.delenv(QS_GEN_DATASOURCE_ARN.name, raising=False)
    p = _write_yaml(tmp_path, {
        "aws_account_id": "111122223333",
        "aws_region": "us-east-1",
        "demo_database_url": "postgresql://u:p@h:5432/d",
        "dialect": "postgres",
    })
    cfg = load_config(p)
    # __post_init__ derives the datasource_arn from the URL.
    assert cfg.datasource_arn is not None
    assert "datasource/" in cfg.datasource_arn
    # ...and records that we own the datasource resource → cli/json.py
    # emits out/datasource.json.
    assert cfg.datasource_arn_was_derived is True


def test_datasource_arn_was_derived_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v9.0.0 — `datasource_arn_was_derived` distinguishes "we own the
    QS datasource" (derived from `demo_database_url`) from "operator
    supplied a pre-existing ARN" (leave it alone, don't emit a
    competing datasource resource) — even when both fields are in the
    cfg (a prod cfg that lists both a real ARN and a DB URL for the
    seed/demo CLI). Bug before this: the explicit-ARN-plus-DB-URL case
    still regenerated the QS datasource."""
    monkeypatch.delenv(QS_GEN_DATASOURCE_ARN.name, raising=False)
    monkeypatch.delenv(QS_GEN_DEMO_DATABASE_URL.name, raising=False)
    explicit_arn = "arn:aws:quicksight:us-east-1:111122223333:datasource/customer-managed-ds"
    dir_a = tmp_path / "a"; dir_a.mkdir()
    dir_b = tmp_path / "b"; dir_b.mkdir()
    dir_c = tmp_path / "c"; dir_c.mkdir()

    # Explicit ARN only → not derived.
    cfg1 = load_config(_write_yaml(dir_a, {
        "aws_account_id": "111122223333", "aws_region": "us-east-1",
        "datasource_arn": explicit_arn, "dialect": "postgres",
    }))
    assert cfg1.datasource_arn == explicit_arn
    assert cfg1.datasource_arn_was_derived is False

    # Explicit ARN AND demo_database_url → still NOT derived (the fix);
    # the explicit ARN wins, and survives with_l2_instance_prefix.
    cfg2 = load_config(_write_yaml(dir_b, {
        "aws_account_id": "111122223333", "aws_region": "us-east-1",
        "datasource_arn": explicit_arn,
        "demo_database_url": "postgresql://u:p@h:5432/d", "dialect": "postgres",
    }))
    assert cfg2.datasource_arn == explicit_arn
    assert cfg2.datasource_arn_was_derived is False
    cfg2p = cfg2.with_l2_instance_prefix("sasquatch_pr")
    assert cfg2p.datasource_arn == explicit_arn
    assert cfg2p.datasource_arn_was_derived is False

    # demo_database_url only → derived; survives the prefix re-derive.
    cfg3 = load_config(_write_yaml(dir_c, {
        "aws_account_id": "111122223333", "aws_region": "us-east-1",
        "demo_database_url": "postgresql://u:p@h:5432/d", "dialect": "postgres",
    }))
    assert cfg3.datasource_arn_was_derived is True
    cfg3p = cfg3.with_l2_instance_prefix("sasquatch_pr")
    assert cfg3p.datasource_arn_was_derived is True
    assert "sasquatch_pr" in (cfg3p.datasource_arn or "")
