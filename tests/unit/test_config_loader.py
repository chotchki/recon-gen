"""V.1.b — config.yaml ↔ L2 institution YAML boundary enforcement.

The loader is the single chokepoint that distinguishes "a config file
the operator typed by hand" from "any other YAML in the repo". The
strict-allowlist behavior here turns every silent typo (theme: in
config.yaml, legacy ``resource_prefix`` / ``l2_instance_prefix`` /
``instance``) into a loud failure with a pointer at where the field
actually belongs (Z.C: ``deployment_name`` + ``db_table_prefix``).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from recon_gen.common.cleanup import DEPLOYMENT_TAG_KEY, MANAGED_TAG_KEY
from recon_gen.common.config import load_config
from recon_gen.common.env_keys import (
    RECON_GEN_AWS_ACCOUNT_ID,
    RECON_GEN_AWS_REGION,
    RECON_GEN_DATASOURCE_ARN,
    RECON_GEN_DB_TABLE_PREFIX,
    RECON_GEN_DEMO_DATABASE_URL,
    RECON_GEN_DEPLOYMENT_NAME,
)


_REQUIRED = {
    "aws_account_id": "111122223333",
    "aws_region": "us-east-1",
    "datasource_arn": "arn:aws:quicksight:us-east-1:111122223333:datasource/x",
    # Z.C: required cfg fields. Defaults pinned for the assertion-light
    # tests below; tests that exercise the resource-ID shape override
    # ``deployment_name`` explicitly.
    "deployment_name": "recon-test",
    "db_table_prefix": "test",
}


def _required_yaml(extras: dict[str, object] | None = None) -> dict[str, object]:
    """Return a baseline dict carrying every required cfg field.

    Tests merge their own keys in via ``{**_required_yaml(), "key": ...}``
    or pass ``extras`` for one-line overrides.
    """
    out: dict[str, object] = dict(_REQUIRED)
    if extras:
        out.update(extras)
    return out


def _write_yaml(tmp_path: Path, body: dict) -> Path:  # pyright: ignore[reportUnknownParameterType, reportMissingTypeArgument]: body kwarg is the loose yaml dict shape
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(body), encoding="utf-8")
    return p


def test_minimal_valid_config_loads(tmp_path: Path) -> None:
    cfg = load_config(_write_yaml(tmp_path, _required_yaml()))
    assert cfg.aws_account_id == "111122223333"
    assert cfg.deployment_name == "recon-test"
    assert cfg.db_table_prefix == "test"


def test_full_valid_config_loads(tmp_path: Path) -> None:
    """Every allowlisted key together — sanity check the allowlist
    isn't accidentally too narrow."""
    p = _write_yaml(tmp_path, _required_yaml({
        "aws_region": "us-east-2",
        "datasource_arn": "arn:aws:quicksight:us-east-2:111122223333:datasource/x",
        "deployment_name": "recon-test-full",
        "db_table_prefix": "test_full",
        "principal_arns": ["arn:aws:iam::111122223333:user/u"],
        "extra_tags": {"Owner": "team"},
        "demo_database_url": "postgresql://u:p@h:5432/d",
        "dialect": "postgres",
        "signing": {
            "key_path": "k.pem",
            "cert_path": "c.pem",
        },
        "tagging_enabled": False,
    }))
    cfg = load_config(p)
    assert cfg.signing is not None
    assert cfg.dialect.value == "postgres"
    assert cfg.tagging_enabled is False
    assert cfg.deployment_name == "recon-test-full"
    assert cfg.db_table_prefix == "test_full"


def test_tagging_enabled_defaults_to_true(tmp_path: Path) -> None:
    """The override is opt-in. Omitting it leaves cleanup's
    fail-CLOSED tag-based isolation intact."""
    cfg = load_config(_write_yaml(tmp_path, _required_yaml()))
    assert cfg.tagging_enabled is True


def test_tagging_enabled_false_omits_tags_kwarg(tmp_path: Path) -> None:
    """``cfg.tags()`` returns ``None`` when tagging is disabled —
    ``_strip_nones`` then drops the ``Tags`` field from the AWS JSON
    so the boto3 ``Create*`` call carries no ``Tags`` kwarg, keeping
    the IAM principal off ``quicksight:TagResource``."""
    cfg = load_config(_write_yaml(tmp_path, _required_yaml({
        "tagging_enabled": False,
    })))
    assert cfg.tags() is None


def test_tagging_enabled_true_populates_tags_kwarg(tmp_path: Path) -> None:
    """Z.C: cfg.tags() emits a single ``Deployment=<name>`` tag instead of
    the v8.x two-tag (ResourcePrefix + L2Instance) pair."""
    cfg = load_config(_write_yaml(tmp_path, _required_yaml({
        "deployment_name": "recon-customprefix",
        "extra_tags": {"Owner": "team"},
    })))
    tags = cfg.tags()
    assert tags is not None
    keys = {tag.Key for tag in tags}
    assert {MANAGED_TAG_KEY, DEPLOYMENT_TAG_KEY, "Owner"} <= keys
    # The legacy two-tag pair must not regress.
    assert "ResourcePrefix" not in keys
    assert "L2Instance" not in keys
    by_key = {tag.Key: tag.Value for tag in tags}
    assert by_key[DEPLOYMENT_TAG_KEY] == "recon-customprefix"


def test_tagging_enabled_non_bool_rejected(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, _required_yaml({
        "tagging_enabled": "false",  # YAML string, not bool
    }))
    with pytest.raises(ValueError, match="tagging_enabled must be a bool"):
        load_config(p)


# Phase BS.2 — Studio toggle (D1 nav contract).

def test_studio_enabled_defaults_to_true(tmp_path: Path) -> None:
    """Default-on for dev. Production cfgs that ship dashboards-only
    set ``studio_enabled: false`` per BS.0 Lock 1."""
    cfg = load_config(_write_yaml(tmp_path, _required_yaml()))
    assert cfg.studio_enabled is True


def test_studio_enabled_false_loads(tmp_path: Path) -> None:
    """Explicit opt-out keeps the cfg valid (no Studio surface mounted
    on the binary, no Studio top-nav entries)."""
    cfg = load_config(_write_yaml(tmp_path, _required_yaml({
        "studio_enabled": False,
    })))
    assert cfg.studio_enabled is False


def test_studio_enabled_non_bool_rejected(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, _required_yaml({
        "studio_enabled": "false",  # YAML string, not bool
    }))
    with pytest.raises(ValueError, match="studio_enabled must be a bool"):
        load_config(p)


@pytest.mark.parametrize("leaked_key", [
    "theme", "persona", "rails", "accounts", "chains",
    "transfer_templates", "account_templates", "limit_schedules",
    "description",
])
def test_l2_only_key_in_config_yaml_rejects(
    tmp_path: Path, leaked_key: str,
) -> None:
    """Dropping any L2 institution field into config.yaml is the most
    common misedit. Each one must error with a pointer at the L2 YAML.

    Z.C: ``instance`` removed from this list — the L2 yaml's
    ``instance:`` field is gone entirely (replaced by cfg.deployment_name
    + cfg.db_table_prefix). It now lands in the legacy-key migration
    path, not the L2-only-leak path. See
    ``test_legacy_keys_in_config_yaml_reject_with_migration_pointer``.
    """
    p = _write_yaml(tmp_path, _required_yaml({leaked_key: "anything"}))
    with pytest.raises(ValueError, match="L2 institution YAML"):
        load_config(p)


@pytest.mark.parametrize("legacy_key,expected_pointer", [
    ("resource_prefix", "deployment_name"),
    ("l2_instance_prefix", "deployment_name"),
    ("instance", "db_table_prefix"),
])
def test_legacy_keys_in_config_yaml_reject_with_migration_pointer(
    tmp_path: Path, legacy_key: str, expected_pointer: str,
) -> None:
    """Z.C: each removed cfg key gets a specific actionable error
    naming the new field, so an operator copy-pasting an old config
    sees the precise migration step instead of a generic 'unknown key'."""
    p = _write_yaml(tmp_path, _required_yaml({legacy_key: "anything"}))
    with pytest.raises(ValueError) as exc_info:
        load_config(p)
    msg = str(exc_info.value)
    assert "legacy config keys removed in Z.C" in msg, (
        f"loud-fail must lead with the Z.C migration banner; got: {msg}"
    )
    assert legacy_key in msg, f"must name the key being rejected; got: {msg}"
    assert expected_pointer in msg, (
        f"migration pointer must name the replacement field "
        f"({expected_pointer!r}); got: {msg}"
    )


def test_unknown_key_rejects(tmp_path: Path) -> None:
    """Random typos / stale keys don't sneak through silently."""
    p = _write_yaml(tmp_path, _required_yaml({
        "theme_preset": "sasquatch-bank",  # removed in N.4
    }))
    with pytest.raises(ValueError, match="unknown config keys"):
        load_config(p)


def test_legacy_principal_arn_singular_still_works(tmp_path: Path) -> None:
    """Backwards compat — singular `principal_arn` accepted alongside
    the canonical plural form."""
    p = _write_yaml(tmp_path, _required_yaml({
        "principal_arn": "arn:aws:iam::111122223333:user/legacy",
    }))
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
    monkeypatch.delenv(RECON_GEN_AWS_ACCOUNT_ID.name, raising=False)
    monkeypatch.delenv(RECON_GEN_AWS_REGION.name, raising=False)
    monkeypatch.delenv(RECON_GEN_DATASOURCE_ARN.name, raising=False)

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
    assert "RECON_GEN_AWS_ACCOUNT_ID" in msg, (
        f"loud-fail message must surface the env-var fallback so the "
        f"operator knows the alternative; got: {msg}"
    )


def test_missing_datasource_arn_without_demo_url_fails_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gate.h.5 — datasource_arn is required UNLESS demo_database_url
    is set (the latter auto-derives the former). Without either, fail
    loud with the missing key + env-var fallback."""
    monkeypatch.delenv(RECON_GEN_DATASOURCE_ARN.name, raising=False)
    monkeypatch.delenv(RECON_GEN_DEMO_DATABASE_URL.name, raising=False)

    p = _write_yaml(tmp_path, {
        "aws_account_id": "111122223333",
        "aws_region": "us-east-1",
        # neither datasource_arn nor demo_database_url
    })
    with pytest.raises(ValueError) as exc_info:
        load_config(p)
    msg = str(exc_info.value)
    assert "datasource_arn" in msg
    assert "RECON_GEN_DATASOURCE_ARN" in msg


def test_demo_database_url_satisfies_datasource_arn_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gate.h.5 sister: when demo_database_url IS set, datasource_arn
    is auto-derived from it — no loud-fail. Locks the contract that
    the missing-cfg check is necessity-aware, not just a blanket key
    list."""
    monkeypatch.delenv(RECON_GEN_DATASOURCE_ARN.name, raising=False)
    body = {
        k: v for k, v in _REQUIRED.items() if k != "datasource_arn"
    }
    body["demo_database_url"] = "postgresql://u:p@h:5432/d"
    body["dialect"] = "postgres"
    cfg = load_config(_write_yaml(tmp_path, body))
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
    still regenerated the QS datasource.

    Z.C: ``with_l2_instance_prefix`` is gone — the deployment_name is
    fixed at cfg-load time, so the "survives a re-stamp" sub-assertion
    is no longer applicable. The ARN-was-derived flag survives normal
    construction; that's all we need to verify here.
    """
    monkeypatch.delenv(RECON_GEN_DATASOURCE_ARN.name, raising=False)
    monkeypatch.delenv(RECON_GEN_DEMO_DATABASE_URL.name, raising=False)
    explicit_arn = "arn:aws:quicksight:us-east-1:111122223333:datasource/customer-managed-ds"
    dir_a = tmp_path / "a"; dir_a.mkdir()
    dir_b = tmp_path / "b"; dir_b.mkdir()
    dir_c = tmp_path / "c"; dir_c.mkdir()

    # Explicit ARN only → not derived.
    cfg1 = load_config(_write_yaml(dir_a, _required_yaml({
        "datasource_arn": explicit_arn, "dialect": "postgres",
    })))
    assert cfg1.datasource_arn == explicit_arn
    assert cfg1.datasource_arn_was_derived is False

    # Explicit ARN AND demo_database_url → still NOT derived (the fix);
    # the explicit ARN wins.
    cfg2 = load_config(_write_yaml(dir_b, _required_yaml({
        "datasource_arn": explicit_arn,
        "demo_database_url": "postgresql://u:p@h:5432/d", "dialect": "postgres",
    })))
    assert cfg2.datasource_arn == explicit_arn
    assert cfg2.datasource_arn_was_derived is False

    # demo_database_url only → derived; ARN carries the deployment_name
    # in the path (per Config.prefixed and __post_init__).
    cfg3 = load_config(_write_yaml(dir_c, _required_yaml({
        "deployment_name": "recon-sasquatch-pr",
        "demo_database_url": "postgresql://u:p@h:5432/d", "dialect": "postgres",
    })))
    # Drop the explicit datasource_arn from _required_yaml so __post_init__
    # actually does the derive (otherwise the explicit ARN wins).
    cfg3 = load_config(_write_yaml(dir_c, {
        k: v for k, v in _required_yaml({
            "deployment_name": "recon-sasquatch-pr",
            "demo_database_url": "postgresql://u:p@h:5432/d",
            "dialect": "postgres",
        }).items() if k != "datasource_arn"
    }))
    assert cfg3.datasource_arn_was_derived is True
    assert "recon-sasquatch-pr" in (cfg3.datasource_arn or "")


# X.4.g.1+2+3 — Deploy-pipeline config schema. Three new fields on Config:
# `etl_hook` (top-level optional str), `etl_datasource` (nested block,
# Optional), `test_generator` (nested block, default-factory so the
# pipeline never None-checks). All three are V.1.b-allowlisted.

def _base_cfg(extras: dict[str, object]) -> dict[str, object]:
    """Z.C: alias for ``_required_yaml`` — the deploy-pipeline test block
    below predates the rename. Same shape, both call sites supported."""
    return _required_yaml(extras)


# --- Z.C — deployment_name + db_table_prefix loud-fail ---

@pytest.mark.parametrize("missing_key,env_var", [
    ("deployment_name", "RECON_GEN_DEPLOYMENT_NAME"),
    ("db_table_prefix", "RECON_GEN_DB_TABLE_PREFIX"),
])
def test_missing_zc_field_fails_loud_with_env_var_hint(
    tmp_path: Path, missing_key: str, env_var: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Z.C: both new required cfg fields loud-fail when missing from
    cfg.yaml AND env, naming the env-var fallback alongside the missing
    cfg key — same shape as ``aws_account_id`` (gate.h.5)."""
    monkeypatch.delenv(env_var, raising=False)
    body = dict(_REQUIRED)
    del body[missing_key]
    p = _write_yaml(tmp_path, body)
    with pytest.raises(ValueError) as exc_info:
        load_config(p)
    msg = str(exc_info.value)
    assert "Missing required configuration" in msg, (
        f"loud-fail must lead with 'Missing required configuration'; got: {msg}"
    )
    assert missing_key in msg
    assert env_var in msg


def test_zc_field_env_overrides_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Z.C: env var override path covers both fields (the runner relies
    on this to inject per-cell deployment_name + db_table_prefix without
    rewriting the operator's cfg yaml)."""
    monkeypatch.setenv(RECON_GEN_DEPLOYMENT_NAME.name, "recon-from-env")
    monkeypatch.setenv(RECON_GEN_DB_TABLE_PREFIX.name, "from_env")
    body = dict(_REQUIRED)
    # Leave the yaml fields in place to confirm env wins.
    body["deployment_name"] = "recon-from-yaml"
    body["db_table_prefix"] = "from_yaml"
    cfg = load_config(_write_yaml(tmp_path, body))
    assert cfg.deployment_name == "recon-from-env"
    assert cfg.db_table_prefix == "from_env"
    # And cfg.prefixed picks the env value up.
    assert cfg.prefixed("foo") == "recon-from-env-foo"


def test_etl_hook_defaults_none(tmp_path: Path) -> None:
    cfg = load_config(_write_yaml(tmp_path, _base_cfg({})))
    assert cfg.etl_hook is None


def test_etl_hook_passthrough(tmp_path: Path) -> None:
    cfg = load_config(_write_yaml(tmp_path, _base_cfg({
        "etl_hook": "/usr/local/bin/refresh-demo --etl-only",
    })))
    assert cfg.etl_hook == "/usr/local/bin/refresh-demo --etl-only"


def test_etl_datasource_defaults_none(tmp_path: Path) -> None:
    cfg = load_config(_write_yaml(tmp_path, _base_cfg({})))
    assert cfg.etl_datasource is None


def test_etl_datasource_full_block_loads(tmp_path: Path) -> None:
    cfg = load_config(_write_yaml(tmp_path, _base_cfg({
        "etl_datasource": {
            "url": "postgresql://prod-replica:5432/ledger",
            "transactions_table": "ledger.txns",
            "daily_balances_table": "ledger.balances_eod",
        },
    })))
    assert cfg.etl_datasource is not None
    assert cfg.etl_datasource.url == "postgresql://prod-replica:5432/ledger"
    assert cfg.etl_datasource.transactions_table == "ledger.txns"
    assert cfg.etl_datasource.daily_balances_table == "ledger.balances_eod"


def test_etl_datasource_missing_required_field_rejects(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, _base_cfg({
        "etl_datasource": {
            "url": "postgresql://x:5432/y",
            "transactions_table": "txns",
            # daily_balances_table missing
        },
    }))
    with pytest.raises(ValueError, match="missing required field"):
        load_config(p)


def test_etl_datasource_unknown_subkey_rejects(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, _base_cfg({
        "etl_datasource": {
            "url": "postgresql://x:5432/y",
            "transactions_table": "txns",
            "daily_balances_table": "balances",
            "schema": "ledger",  # not in the allowlist
        },
    }))
    with pytest.raises(ValueError, match="unknown keys"):
        load_config(p)


def test_etl_datasource_non_mapping_rejects(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, _base_cfg({
        "etl_datasource": "postgresql://x:5432/y",
    }))
    with pytest.raises(ValueError, match="must be a mapping"):
        load_config(p)


def test_test_generator_defaults_to_empty_block(tmp_path: Path) -> None:
    """Absent block resolves to TestGeneratorConfig() — byte-identical
    to today's locked-seed output. The pipeline never None-checks."""
    cfg = load_config(_write_yaml(tmp_path, _base_cfg({})))
    assert cfg.test_generator.enabled is True
    assert cfg.test_generator.scope == "full"
    assert cfg.test_generator.end_date is None
    assert cfg.test_generator.seed is None
    assert cfg.test_generator.plants == ()
    assert cfg.test_generator.only_template is None
    assert cfg.test_generator.derive_balances is False


def test_test_generator_full_block_loads(tmp_path: Path) -> None:
    cfg = load_config(_write_yaml(tmp_path, _base_cfg({
        "test_generator": {
            "enabled": True,
            "scope": "exceptions_only",
            "end_date": "2030-06-15",
            "seed": 42,
            "plants": ["drift", "overdraft"],
            "only_template": "PayoutCheck",
            "derive_balances": True,
        },
    })))
    tg = cfg.test_generator
    assert tg.enabled is True
    assert tg.scope == "exceptions_only"
    assert tg.end_date == date(2030, 6, 15)
    assert tg.seed == 42
    assert tg.plants == ("drift", "overdraft")
    assert tg.only_template == "PayoutCheck"
    assert tg.derive_balances is True


def test_test_generator_native_yaml_date(tmp_path: Path) -> None:
    """YAML's native date scalar (`2030-01-01` unquoted) parses to
    datetime.date — accept it as well as ISO strings."""
    body = _base_cfg({})
    body["test_generator"] = {"end_date": date(2030, 1, 1)}
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(body), encoding="utf-8")
    cfg = load_config(p)
    assert cfg.test_generator.end_date == date(2030, 1, 1)


def test_test_generator_unknown_subkey_rejects(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, _base_cfg({
        "test_generator": {"density": 5.0},  # X.4.h proposed a knob; not landed
    }))
    with pytest.raises(ValueError, match="unknown keys"):
        load_config(p)


def test_test_generator_invalid_scope_rejects(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, _base_cfg({
        "test_generator": {"scope": "everything"},
    }))
    with pytest.raises(ValueError, match="scope must be one of"):
        load_config(p)


def test_test_generator_invalid_plant_rejects(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, _base_cfg({
        "test_generator": {"plants": ["fraud"]},  # not a known plant kind
    }))
    with pytest.raises(ValueError, match="contains unknown values"):
        load_config(p)


def test_test_generator_bad_date_rejects(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, _base_cfg({
        "test_generator": {"end_date": "not-a-date"},
    }))
    with pytest.raises(ValueError, match="must be ISO 8601"):
        load_config(p)


def test_test_generator_bad_seed_type_rejects(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, _base_cfg({
        "test_generator": {"seed": "abc"},
    }))
    with pytest.raises(ValueError, match="seed must be an integer"):
        load_config(p)


def test_test_generator_non_mapping_rejects(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, _base_cfg({
        "test_generator": "full",
    }))
    with pytest.raises(ValueError, match="must be a mapping"):
        load_config(p)


def test_v1b_allowlist_includes_three_pipeline_keys(tmp_path: Path) -> None:
    """Smoke: the three new keys + a base config load cleanly together —
    proves the V.1.b allowlist actually carries them."""
    cfg = load_config(_write_yaml(tmp_path, _base_cfg({
        "etl_hook": "./etl.sh",
        "etl_datasource": {
            "url": "postgresql://x:5432/y",
            "transactions_table": "txns",
            "daily_balances_table": "balances",
        },
        "test_generator": {"scope": "full"},
    })))
    assert cfg.etl_hook == "./etl.sh"
    assert cfg.etl_datasource is not None
    assert cfg.test_generator.scope == "full"
