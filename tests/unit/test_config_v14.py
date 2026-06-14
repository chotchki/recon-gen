"""DE.1 — pin the v14.0.0 cfg shape contract.

Covers:
- Concern-grouped shape (aws / db / auth / app2 / audit / test).
- ``extends:`` deep-merge (base + overlay).
- ``extends:`` cycle detection.
- Derive-on-load (``db.table_prefix`` from ``aws.deployment_name``).
- Legacy-key detection raises ``LegacyFieldError``.
- ``aws.datasource.mode`` literal + ``adopt`` requires arn.
- Required-field absence raises ``MissingFieldError``.

Pinned per DE.0 lock; DE.2 phase sweep migrates all callsites to this shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recon_gen.common.config import (
    CycleError,
    LegacyFieldError,
    MissingFieldError,
    _QS_USER_ARN_CACHE,
    load_config,
    resolve_qs_user_arn,
)
from recon_gen.common.sql import Dialect


_MIN_CFG = """\
aws:
  account_id: '123456789012'
  region: us-east-1
  deployment_name: test-deploy
db:
  dialect: postgres
  url: postgresql://u:p@h:5432/d
"""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


def test_minimal_cfg_loads(tmp_path: Path) -> None:
    """Minimum-field cfg loads + auto-derives table_prefix."""
    p = _write(tmp_path, "cfg.yaml", _MIN_CFG)
    cfg = load_config(p)
    assert cfg.aws.account_id == "123456789012"
    assert cfg.aws.region == "us-east-1"
    assert cfg.aws.deployment_name == "test-deploy"
    assert cfg.db.dialect is Dialect.POSTGRES
    assert cfg.db.url == "postgresql://u:p@h:5432/d"
    # Derived from deployment_name (`-` → `_`)
    assert cfg.db.table_prefix == "test_deploy"
    # All optional blocks default to empty.
    assert cfg.auth.aws.profile is None
    assert cfg.auth.oidc is None
    assert cfg.app2.tls is None
    assert cfg.audit.signing is None


def test_extends_deep_merges_child_over_parent(tmp_path: Path) -> None:
    """``extends: [./base.yaml]`` overlays the child on top of base.
    Scalars + lists: child wins. Dicts: deep-merge per key."""
    _write(tmp_path, "base.yaml", _MIN_CFG)
    overlay = """\
extends: [./base.yaml]
aws:
  deployment_name: prod-deploy
  qs_disable_pg_ssl: true
db:
  app2_pool_size: 30
"""
    p = _write(tmp_path, "overlay.yaml", overlay)
    cfg = load_config(p)
    # Inherited from base
    assert cfg.aws.account_id == "123456789012"
    assert cfg.aws.region == "us-east-1"
    # Overlaid by child
    assert cfg.aws.deployment_name == "prod-deploy"
    assert cfg.aws.qs_disable_pg_ssl is True
    # db.url inherited; app2_pool_size overlaid
    assert cfg.db.url == "postgresql://u:p@h:5432/d"
    assert cfg.db.app2_pool_size == 30
    # Derived from the *merged* deployment_name
    assert cfg.db.table_prefix == "prod_deploy"


def test_extends_cycle_detected(tmp_path: Path) -> None:
    """A → B → A raises ``CycleError`` with the path."""
    a = _write(tmp_path, "a.yaml", "extends: [./b.yaml]\n")
    _write(tmp_path, "b.yaml", "extends: [./a.yaml]\n")
    with pytest.raises(CycleError, match="cycle"):
        load_config(a)


def test_legacy_field_raises_with_migration_hint(tmp_path: Path) -> None:
    """v13 top-level key raises ``LegacyFieldError`` carrying the
    migration target."""
    legacy = """\
aws:
  account_id: '123456789012'
  region: us-east-1
  deployment_name: test
db:
  dialect: postgres
demo_database_url: postgresql://u:p@h:5432/d
"""
    p = _write(tmp_path, "legacy.yaml", legacy)
    with pytest.raises(LegacyFieldError, match="demo_database_url.*db.url"):
        load_config(p)


def test_legacy_nested_auth_field_raises(tmp_path: Path) -> None:
    """v13 nested key like ``auth.aws_profile`` raises with the
    new ``auth.aws.profile`` target."""
    legacy = _MIN_CFG + """\
auth:
  aws_profile: my-profile
"""
    p = _write(tmp_path, "legacy.yaml", legacy)
    with pytest.raises(LegacyFieldError, match="auth.aws_profile.*auth.aws.profile"):
        load_config(p)


def test_missing_required_field_raises(tmp_path: Path) -> None:
    """Missing ``aws.account_id`` raises ``MissingFieldError``."""
    incomplete = """\
aws:
  region: us-east-1
  deployment_name: test
db:
  dialect: postgres
  url: postgresql://u:p@h:5432/d
"""
    p = _write(tmp_path, "incomplete.yaml", incomplete)
    with pytest.raises(MissingFieldError, match="aws.account_id"):
        load_config(p)


def test_datasource_mode_defaults_to_create(tmp_path: Path) -> None:
    """``aws.datasource`` block absent ⇒ mode=create. DE.5 — when
    mode=create + db.url is set, the loader auto-derives the arn
    (matches pre-DE Config.__post_init__ behavior so deploy emitters
    get a synthesized ARN without per-callsite logic)."""
    p = _write(tmp_path, "cfg.yaml", _MIN_CFG)
    cfg = load_config(p)
    assert cfg.aws.datasource.mode == "create"
    # Auto-derived: arn:aws:quicksight:<region>:<account>:datasource/<prefix>-demo-datasource
    assert cfg.aws.datasource.arn == (
        "arn:aws:quicksight:us-east-1:123456789012:datasource/test-deploy-demo-datasource"
    )


def test_datasource_mode_adopt_requires_arn(tmp_path: Path) -> None:
    """``aws.datasource.mode=adopt`` without arn raises
    ``MissingFieldError``."""
    cfg_text = _MIN_CFG + """\
  datasource:
    mode: adopt
"""
    # Inject under aws: block by replacing the block close
    cfg_text = _MIN_CFG.replace(
        "deployment_name: test-deploy\n",
        "deployment_name: test-deploy\n  datasource:\n    mode: adopt\n",
    )
    p = _write(tmp_path, "cfg.yaml", cfg_text)
    with pytest.raises(MissingFieldError, match="aws.datasource.arn"):
        load_config(p)


def test_datasource_mode_skip_is_accepted(tmp_path: Path) -> None:
    """``aws.datasource.mode=skip`` (the test-mode escape per DE.0
    operator comment) loads cleanly + carries the enum value."""
    cfg_text = _MIN_CFG.replace(
        "deployment_name: test-deploy\n",
        "deployment_name: test-deploy\n  datasource:\n    mode: skip\n",
    )
    p = _write(tmp_path, "cfg.yaml", cfg_text)
    cfg = load_config(p)
    assert cfg.aws.datasource.mode == "skip"
    assert cfg.aws.datasource.arn is None


def test_invalid_datasource_mode_raises(tmp_path: Path) -> None:
    """Unknown mode value raises ``CfgError``."""
    from recon_gen.common.config import CfgError
    cfg_text = _MIN_CFG.replace(
        "deployment_name: test-deploy\n",
        "deployment_name: test-deploy\n  datasource:\n    mode: nonsense\n",
    )
    p = _write(tmp_path, "cfg.yaml", cfg_text)
    with pytest.raises(CfgError, match="datasource.mode"):
        load_config(p)


def test_auth_oidc_block_loads_when_present(tmp_path: Path) -> None:
    """Phase DD's ``auth.oidc.*`` block loads when present."""
    cfg_text = _MIN_CFG + """\
auth:
  oidc:
    issuer_url: https://idp.example.com
    client_id: recon-gen-app2
    client_secret_env: RECON_GEN_OIDC_CLIENT_SECRET
    redirect_uri: https://localhost:8765/auth/callback
"""
    p = _write(tmp_path, "cfg.yaml", cfg_text)
    cfg = load_config(p)
    assert cfg.auth.oidc is not None
    assert cfg.auth.oidc.issuer_url == "https://idp.example.com"
    assert cfg.auth.oidc.client_id == "recon-gen-app2"
    # Default scopes
    assert cfg.auth.oidc.scopes == ("openid", "email", "profile")


def test_app2_tls_block_loads_when_present(tmp_path: Path) -> None:
    """Phase DC's ``app2.tls.*`` block loads when present."""
    cfg_text = _MIN_CFG + """\
app2:
  tls:
    cert_path: /etc/ssl/cert.pem
    key_path: /etc/ssl/key.pem
"""
    p = _write(tmp_path, "cfg.yaml", cfg_text)
    cfg = load_config(p)
    assert cfg.app2.tls is not None
    assert cfg.app2.tls.cert_path == "/etc/ssl/cert.pem"


def test_audit_signing_block_loads_when_present(tmp_path: Path) -> None:
    """Pre-DE ``signing.*`` → post-DE ``audit.signing.*``."""
    cfg_text = _MIN_CFG + """\
audit:
  signing:
    key_path: /etc/audit/key.pem
    cert_path: /etc/audit/cert.pem
    signer_name: Custom Signer
"""
    p = _write(tmp_path, "cfg.yaml", cfg_text)
    cfg = load_config(p)
    assert cfg.audit.signing is not None
    assert cfg.audit.signing.signer_name == "Custom Signer"


def test_explicit_table_prefix_overrides_derived(tmp_path: Path) -> None:
    """When ``db.table_prefix`` is set explicitly, no derivation."""
    cfg_text = _MIN_CFG + """\
  table_prefix: custom_prefix
"""
    # Append to db: block by replacing block close
    cfg_text = _MIN_CFG.replace(
        "url: postgresql://u:p@h:5432/d\n",
        "url: postgresql://u:p@h:5432/d\n  table_prefix: custom_prefix\n",
    )
    p = _write(tmp_path, "cfg.yaml", cfg_text)
    cfg = load_config(p)
    assert cfg.db.table_prefix == "custom_prefix"


# ---------------------------------------------------------------------------
# DE.1 sub-B — QS user ARN lazy resolver (no-boto paths)
# ---------------------------------------------------------------------------


def test_resolve_qs_user_arn_explicit_override(tmp_path: Path) -> None:
    """``cfg.auth.aws.quicksight_user_arn`` wins; boto NOT fired."""
    cfg_text = _MIN_CFG + """\
auth:
  aws:
    profile: some-profile
    quicksight_user_arn: arn:aws:quicksight:us-east-1:123:user/default/explicit
"""
    p = _write(tmp_path, "cfg.yaml", cfg_text)
    cfg = load_config(p)
    arn = resolve_qs_user_arn(cfg)
    assert arn == "arn:aws:quicksight:us-east-1:123:user/default/explicit"


def test_resolve_qs_user_arn_no_profile_returns_none(tmp_path: Path) -> None:
    """``cfg.auth.aws.profile`` absent ⇒ resolver returns None without
    firing boto. Runner uses None signal to skip qs_browser layer."""
    p = _write(tmp_path, "cfg.yaml", _MIN_CFG)
    cfg = load_config(p)
    assert resolve_qs_user_arn(cfg) is None


def test_partition_defaults_to_commercial_aws(tmp_path: Path) -> None:
    """No principal_arns + no datasource.arn ⇒ ``aws`` (commercial).
    Preserves pre-DE behavior for fuzz fixtures that don't carry
    AWS-side identity material."""
    p = _write(tmp_path, "cfg.yaml", _MIN_CFG)
    cfg = load_config(p)
    assert cfg.aws.partition == "aws"


def test_partition_derives_govcloud_from_principal_arn(tmp_path: Path) -> None:
    """First ``arn:aws-us-gov:``-prefixed principal ARN ⇒ govcloud.
    Covers the customer-supplied role/user case where region is
    operator-set + matches partition."""
    cfg_text = _MIN_CFG.replace(
        "deployment_name: test-deploy\n",
        "deployment_name: test-deploy\n"
        "  principal_arns:\n"
        "    - arn:aws-us-gov:iam::123456789012:role/RegOpsAdmin\n",
    )
    p = _write(tmp_path, "cfg.yaml", cfg_text)
    cfg = load_config(p)
    assert cfg.aws.partition == "aws-us-gov"


def test_partition_derives_china_from_adopted_datasource_arn(tmp_path: Path) -> None:
    """Explicit ``aws.datasource.arn`` (mode=adopt) wins over
    principal_arns — covers the pre-provisioned-datasource case where
    the operator's pinned ARN is authoritative."""
    cfg_text = _MIN_CFG.replace(
        "deployment_name: test-deploy\n",
        "deployment_name: test-deploy\n"
        "  principal_arns:\n"
        "    - arn:aws:iam::123456789012:role/SomeAdmin\n"
        "  datasource:\n"
        "    mode: adopt\n"
        "    arn: arn:aws-cn:quicksight:cn-north-1:123456789012:datasource/preexisting\n",
    )
    p = _write(tmp_path, "cfg.yaml", cfg_text)
    cfg = load_config(p)
    # datasource.arn wins (preserves pre-DE precedence: explicit
    # account-bound ARN beats principal_arns).
    assert cfg.aws.partition == "aws-cn"


def test_resolve_qs_user_arn_cache_hit_returns_cached(tmp_path: Path) -> None:
    """Pre-populated cache entry is returned without firing boto.
    Pins the cache-key shape ``(profile, account_id, region)`` so the
    runner's per-cell subprocesses share lookups."""
    cfg_text = _MIN_CFG + """\
auth:
  aws:
    profile: cached-profile
"""
    p = _write(tmp_path, "cfg.yaml", cfg_text)
    cfg = load_config(p)
    cache_key = ("cached-profile", "123456789012", "us-east-1")
    _QS_USER_ARN_CACHE[cache_key] = "arn:aws:quicksight:cached:user/test"
    try:
        assert resolve_qs_user_arn(cfg) == "arn:aws:quicksight:cached:user/test"
    finally:
        _QS_USER_ARN_CACHE.pop(cache_key, None)


# ---------------------------------------------------------------------------
# DE.5.config_v14_consolidation.C — env-var overrides + run/*.yaml smoke
# (absorbed from the retired tests/unit/test_config_loader.py)
# ---------------------------------------------------------------------------


def test_env_var_overrides_aws_account_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``RECON_GEN_AWS_ACCOUNT_ID`` overrides the yaml's ``aws.account_id``.

    The runner injects per-cell env vars to point a single cfg.yaml at
    multiple test cells without rewriting the file.
    """
    monkeypatch.setenv("RECON_GEN_AWS_ACCOUNT_ID", "999999999999")  # typing-smell: ignore[envvar-bypass]: cfg-loader env-var contract
    p = _write(tmp_path, "cfg.yaml", _MIN_CFG)
    cfg = load_config(p)
    assert cfg.aws.account_id == "999999999999"


def test_env_var_overrides_demo_database_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``RECON_GEN_DEMO_DATABASE_URL`` overrides the yaml's ``db.url``."""
    monkeypatch.setenv(  # typing-smell: ignore[envvar-bypass]: cfg-loader env-var contract
        "RECON_GEN_DEMO_DATABASE_URL",
        "postgresql://override:pw@host:5432/overridedb",
    )
    p = _write(tmp_path, "cfg.yaml", _MIN_CFG)
    cfg = load_config(p)
    assert cfg.db.url == "postgresql://override:pw@host:5432/overridedb"


def test_env_var_overrides_dialect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``RECON_GEN_DIALECT`` overrides the yaml's ``db.dialect``."""
    monkeypatch.setenv("RECON_GEN_DIALECT", "duckdb")  # typing-smell: ignore[envvar-bypass]: cfg-loader env-var contract
    p = _write(tmp_path, "cfg.yaml", _MIN_CFG)
    cfg = load_config(p)
    assert cfg.db.dialect is Dialect.DUCKDB


def test_env_var_overrides_datasource_arn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``RECON_GEN_DATASOURCE_ARN`` flips ``aws.datasource`` to mode=adopt
    + sets the arn. Lets the runner point at a pre-existing QS datasource
    without rewriting the cfg yaml."""
    monkeypatch.setenv(  # typing-smell: ignore[envvar-bypass]: cfg-loader env-var contract
        "RECON_GEN_DATASOURCE_ARN",
        "arn:aws:quicksight:us-east-1:123456789012:datasource/preexisting",
    )
    p = _write(tmp_path, "cfg.yaml", _MIN_CFG)
    cfg = load_config(p)
    assert cfg.aws.datasource.mode == "adopt"
    assert (
        cfg.aws.datasource.arn
        == "arn:aws:quicksight:us-east-1:123456789012:datasource/preexisting"
    )


def test_env_var_overrides_principal_arns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``RECON_GEN_PRINCIPAL_ARNS`` (comma-separated) overrides the yaml's
    ``aws.principal_arns`` list."""
    monkeypatch.setenv(  # typing-smell: ignore[envvar-bypass]: cfg-loader env-var contract
        "RECON_GEN_PRINCIPAL_ARNS",
        "arn:aws:iam::1:user/a, arn:aws:iam::1:user/b",
    )
    p = _write(tmp_path, "cfg.yaml", _MIN_CFG)
    cfg = load_config(p)
    assert cfg.aws.principal_arns == (
        "arn:aws:iam::1:user/a", "arn:aws:iam::1:user/b",
    )


def test_env_var_app2_pool_size_int_coercion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``RECON_GEN_APP2_DB_POOL_SIZE`` coerces to int; non-int raises CfgError."""
    monkeypatch.setenv("RECON_GEN_APP2_DB_POOL_SIZE", "42")  # typing-smell: ignore[envvar-bypass]: cfg-loader env-var contract
    p = _write(tmp_path, "cfg.yaml", _MIN_CFG)
    cfg = load_config(p)
    assert cfg.db.app2_pool_size == 42


def test_run_postgres_config_smoke() -> None:
    """The committed run/config.postgres.yaml round-trips through the
    nested loader. Operator's actual ops cfg stays load-clean."""
    p = Path(__file__).parent.parent.parent / "run" / "config.postgres.yaml"
    if not p.exists():
        pytest.skip(f"{p} not present (run/ is operator-local)")
    cfg = load_config(p)
    assert cfg.db.dialect is Dialect.POSTGRES
    assert cfg.aws.deployment_name == "qsgen-postgres"


def test_run_oracle_config_smoke() -> None:
    p = Path(__file__).parent.parent.parent / "run" / "config.oracle.yaml"
    if not p.exists():
        pytest.skip(f"{p} not present (run/ is operator-local)")
    cfg = load_config(p)
    assert cfg.db.dialect is Dialect.ORACLE


def test_run_duckdb_config_smoke() -> None:
    p = Path(__file__).parent.parent.parent / "run" / "config.duckdb.yaml"
    if not p.exists():
        pytest.skip(f"{p} not present (run/ is operator-local)")
    cfg = load_config(p)
    assert cfg.db.dialect is Dialect.DUCKDB


def test_load_config_raises_when_path_missing() -> None:
    """Explicit non-existent path raises CfgError (vs falling back to
    candidate discovery, which would mask the operator's typo)."""
    from recon_gen.common.config import CfgError
    with pytest.raises(CfgError, match="cfg path does not exist"):
        load_config("/nonexistent/path/cfg.yaml")
