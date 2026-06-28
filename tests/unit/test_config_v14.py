"""DE.1 — pin the v14.0.0 cfg shape contract.

Covers:
- Concern-grouped shape (aws / db / auth / app2 / audit / test).
- ``extends:`` deep-merge (base + overlay).
- ``extends:`` cycle detection.
- Derive-on-load (``db.table_prefix`` from ``aws.deployment_name``).
- Legacy-key detection raises ``LegacyFieldError``.
- Required-field absence raises ``MissingFieldError``.

Pinned per DE.0 lock; DE.2 phase sweep migrates all callsites to this shape.

Note (dead-config sweep): the AWS account/region/datasource/partition
surface was removed from ``AwsConfig`` with the QuickSight deploy path —
a cfg carrying those keys still loads (they're ignored), so the loader
tests that pinned their presence/derivation/validation retired here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recon_gen.common.config import (
    CycleError,
    LegacyFieldError,
    MissingFieldError,
    load_config,
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
    assert cfg.aws.deployment_name == "test-deploy"
    assert cfg.db.dialect is Dialect.POSTGRES
    assert cfg.db.url == "postgresql://u:p@h:5432/d"
    # Derived from deployment_name (`-` → `_`)
    assert cfg.db.table_prefix == "test_deploy"
    # All optional blocks default to empty.
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
db:
  app2_pool_size: 30
"""
    p = _write(tmp_path, "overlay.yaml", overlay)
    cfg = load_config(p)
    # Overlaid by child
    assert cfg.aws.deployment_name == "prod-deploy"
    # db.url inherited from base; app2_pool_size overlaid by child
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
    """Phase DC's ``app2.tls.*`` block loads when present.

    Defaults: ``env="dev"`` when not specified; ``account_email`` is
    required (raises MissingFieldError if absent — see test below).
    """
    cfg_text = _MIN_CFG + """\
app2:
  tls:
    cert_path: /etc/ssl/cert.pem
    key_path: /etc/ssl/key.pem
    account_email: ops@example.com
"""
    p = _write(tmp_path, "cfg.yaml", cfg_text)
    cfg = load_config(p)
    assert cfg.app2.tls is not None
    assert cfg.app2.tls.cert_path == "/etc/ssl/cert.pem"
    assert cfg.app2.tls.key_path == "/etc/ssl/key.pem"
    assert cfg.app2.tls.account_email == "ops@example.com"
    assert cfg.app2.tls.env == "dev"  # default


def test_app2_tls_env_ci_loads(tmp_path: Path) -> None:
    """``app2.tls.env: ci`` overrides the default."""
    cfg_text = _MIN_CFG + """\
app2:
  tls:
    cert_path: /etc/ssl/cert.pem
    key_path: /etc/ssl/key.pem
    account_email: ops@example.com
    env: ci
"""
    p = _write(tmp_path, "cfg.yaml", cfg_text)
    cfg = load_config(p)
    assert cfg.app2.tls is not None
    assert cfg.app2.tls.env == "ci"


def test_app2_tls_invalid_env_raises(tmp_path: Path) -> None:
    """Unknown ``env`` value raises ``CfgError`` via ``__post_init__``."""
    from recon_gen.common.config import CfgError
    cfg_text = _MIN_CFG + """\
app2:
  tls:
    cert_path: /etc/ssl/cert.pem
    key_path: /etc/ssl/key.pem
    account_email: ops@example.com
    env: prod
"""
    p = _write(tmp_path, "cfg.yaml", cfg_text)
    with pytest.raises(CfgError, match="app2.tls.env"):
        load_config(p)


def test_app2_tls_missing_account_email_raises(tmp_path: Path) -> None:
    """``account_email`` is required when ``app2.tls:`` block is set."""
    cfg_text = _MIN_CFG + """\
app2:
  tls:
    cert_path: /etc/ssl/cert.pem
    key_path: /etc/ssl/key.pem
"""
    p = _write(tmp_path, "cfg.yaml", cfg_text)
    with pytest.raises(MissingFieldError, match="app2.tls.account_email"):
        load_config(p)


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
# DE.5.config_v14_consolidation.C — env-var overrides + run/*.yaml smoke
# (absorbed from the retired tests/unit/test_config_loader.py)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# write_yaml — mutate-via-`dataclasses.replace`-then-save round-trip
# (restored 2026-06-15; original Config.write_yaml landed pre-DE.5 then
# was dropped in DE.5.config_v14_consolidation.C alongside the flat-yaml
# loader retirement. Operator use case: edit a cfg in-process then
# persist back to disk via `cfg.write_yaml(path)`. Round-trip semantics
# matter; byte-identity does not.)
# ---------------------------------------------------------------------------


def test_write_yaml_round_trips_minimal_cfg(tmp_path: Path) -> None:
    """The minimal cfg loads, writes back out, and re-loads to the same
    semantic Config — Dialect coerced through .value, no None bloat."""
    p = _write(tmp_path, "cfg.yaml", _MIN_CFG)
    cfg = load_config(p)
    out_path = tmp_path / "out.yaml"
    cfg.write_yaml(out_path)
    cfg2 = load_config(out_path)
    assert cfg2.aws.deployment_name == cfg.aws.deployment_name
    assert cfg2.db.dialect is cfg.db.dialect  # Enum survived through .value
    assert cfg2.db.url == cfg.db.url


def test_write_yaml_after_dataclasses_replace_mutate(tmp_path: Path) -> None:
    """Canonical use case: load, mutate via dataclasses.replace, save
    back. The frozen-dataclass constraint forces functional mutation;
    write_yaml is the persistence half."""
    from dataclasses import replace
    p = _write(tmp_path, "cfg.yaml", _MIN_CFG)
    cfg = load_config(p)
    mutated = replace(
        cfg,
        aws=replace(cfg.aws, deployment_name="prod-deploy"),
    )
    mutated.write_yaml(p)
    re_loaded = load_config(p)
    assert re_loaded.aws.deployment_name == "prod-deploy"
    # Other fields preserved.
    assert re_loaded.db.url == cfg.db.url
    assert re_loaded.db.dialect is Dialect.POSTGRES


def test_write_yaml_drops_none_optional_blocks(tmp_path: Path) -> None:
    """``cfg.auth.oidc`` / ``cfg.app2.tls`` / ``cfg.audit.signing`` are
    all None on the minimal cfg. The emitted YAML must NOT carry
    ``oidc: null`` etc. — load_config treats absent and null
    equivalently, but the operator-facing file stays compact."""
    import yaml as _yaml
    p = _write(tmp_path, "cfg.yaml", _MIN_CFG)
    cfg = load_config(p)
    out_path = tmp_path / "out.yaml"
    cfg.write_yaml(out_path)
    raw = _yaml.safe_load(out_path.read_text())
    # None-valued nested fields dropped.
    auth = raw.get("auth", {})
    assert "oidc" not in auth
    assert "session" not in auth
    app2 = raw.get("app2", {})
    assert "tls" not in app2
    audit = raw.get("audit", {})
    assert "signing" not in audit


def test_write_yaml_to_stream(tmp_path: Path) -> None:
    """write_yaml accepts an already-open text stream (use case: write
    to stdout, write to a BytesIO buffer for tests, etc.)."""
    import io
    p = _write(tmp_path, "cfg.yaml", _MIN_CFG)
    cfg = load_config(p)
    buf = io.StringIO()
    cfg.write_yaml(buf)
    rendered = buf.getvalue()
    assert "deployment_name: test-deploy" in rendered
    assert "dialect: postgres" in rendered
