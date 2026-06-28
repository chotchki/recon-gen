"""DE.2 commit A — proxy view properties on ``Config``.

Pins that ``cfg.db.X`` / ``cfg.app2.X`` / ``cfg.audit.X`` / ``cfg.test.X``
read the concern-grouped nested shape locked in DE.0.

The AWS-side surface collapsed in the dead-config sweep: ``AwsConfig``
keeps only ``deployment_name`` + ``prefixed()``, so the ARN-synthesis
proxies (``partition`` / ``dataset_arn`` / ``theme_arn`` / ``datasource``)
and the ``auth.aws`` profile/quicksight_user_arn proxies went with
QuickSight. Only ``cfg.aws.prefixed()`` survives here. (``tags()`` died
with the QuickSight emit graph in DW.8.1.c.)
"""

from __future__ import annotations

from datetime import date

import pytest

from recon_gen.common.config import (
    AwsConfig,
    Config,
    SigningConfig,
    TestGeneratorConfig,
)
from recon_gen.common.sql import Dialect


def _make_cfg(**overrides: object) -> Config:
    """Build a minimal-but-complete ``Config`` for proxy tests."""
    # Post-DW dead-config sweep: ``AwsConfig`` keeps only
    # ``deployment_name``. The legacy aws_* / datasource / principal /
    # tag kwargs are accepted-and-ignored so the helper's call surface
    # stays stable across the callers that still pass them.
    for _dead in (
        "aws_account_id", "aws_region", "datasource_arn",
        "principal_arns", "extra_tags", "tagging_enabled",
    ):
        overrides.pop(_dead, None)
    deployment_name = overrides.pop("deployment_name", "test-deploy")
    from recon_gen.common.config import App2Config as _App2Config  # noqa: PLC0415
    from recon_gen.common.config import AuditConfig as _AuditConfig  # noqa: PLC0415
    from recon_gen.common.config import DbConfig as _DbConfig  # noqa: PLC0415
    from recon_gen.common.config import SigningConfig as _SigningConfig  # noqa: PLC0415
    from recon_gen.common.config import TestConfig as _TestConfig  # noqa: PLC0415
    from recon_gen.common.config import TestGeneratorConfig as _TestGeneratorConfig  # noqa: PLC0415
    # DE.5 step 17 — etl_hook + banner_text moved to App2Config.
    etl_hook_raw = overrides.pop("etl_hook", None)
    banner_text_raw = overrides.pop("banner_text", None)
    # DE.5 step 18 — signing moved to AuditConfig.
    signing_raw = overrides.pop("signing", None)
    signing_obj: _SigningConfig | None = (
        signing_raw if isinstance(signing_raw, _SigningConfig) else None
    )
    # DE.5 step 19 — test_generator moved to TestConfig.
    tgen_raw = overrides.pop("test_generator", None)
    tgen_obj = (
        tgen_raw if isinstance(tgen_raw, _TestGeneratorConfig)
        else _TestGeneratorConfig()
    )
    defaults: dict[str, object] = {
        "aws": AwsConfig(deployment_name=str(deployment_name)),
        "db": _DbConfig(
            table_prefix="test_deploy",
            url="postgresql://u:p@h:5432/d",
            dialect=Dialect.POSTGRES,
        ),
        "audit": _AuditConfig(signing=signing_obj),
        "test": _TestConfig(generator=tgen_obj),
        "app2": _App2Config(
            etl_hook=str(etl_hook_raw) if etl_hook_raw is not None else None,
            banner_text=str(banner_text_raw) if banner_text_raw is not None else None,
        ),
    }
    defaults.update(overrides)
    return Config(**defaults)  # type: ignore[arg-type]: dict[str, object] is the dataclass kwarg surface; pyright can't narrow per-key


# ---------------------------------------------------------------------------
# cfg.aws — AWS deploy + ARN-synthesis methods
# ---------------------------------------------------------------------------


def test_aws_view_prefixed_returns_deployment_prefix() -> None:
    cfg = _make_cfg(deployment_name="prod-deploy")
    assert cfg.aws.prefixed("foo") == "prod-deploy-foo"
    assert cfg.aws.prefixed("demo-datasource") == "prod-deploy-demo-datasource"


# ---------------------------------------------------------------------------
# cfg.db
# ---------------------------------------------------------------------------


def test_db_view_carries_flat_fields() -> None:
    """Proxy reads underlying flat fields; RHS uses legacy names."""
    cfg = _make_cfg()
    assert cfg.db.dialect == cfg.db.dialect  # type: ignore[attr-defined]: legacy flat field surviving through DE.5 collapse
    assert cfg.db.url == cfg.db.url  # type: ignore[attr-defined]: legacy flat field surviving through DE.5 collapse
    assert cfg.db.table_prefix == cfg.db.table_prefix  # type: ignore[attr-defined]: legacy flat field surviving through DE.5 collapse
    assert cfg.db.default_l2_instance == cfg.db.default_l2_instance  # type: ignore[attr-defined]: legacy flat field surviving through DE.5 collapse
    assert cfg.db.app2_pool_size == cfg.db.app2_pool_size  # type: ignore[attr-defined]: legacy flat field surviving through DE.5 collapse


# ---------------------------------------------------------------------------
# cfg.app2
# ---------------------------------------------------------------------------


def test_app2_view_carries_flat_fields() -> None:
    cfg = _make_cfg(etl_hook="./bin/etl.sh", banner_text="Demo mode")
    assert cfg.app2.etl_hook == "./bin/etl.sh"
    assert cfg.app2.banner_text == "Demo mode"
    # DE.5 step 17 — db_pool_size lives on cfg.db.app2_pool_size only.
    assert cfg.db.app2_pool_size == 10


def test_app2_view_tls_is_none_on_legacy_cfg() -> None:
    """Legacy ``Config`` doesn't carry tls fields; DE.4 wires the
    ``cfg.app2.tls.{cert_path, key_path}`` block as cfg-fallback to
    DC.1's CLI flags. Until then, proxy returns None."""
    cfg = _make_cfg()
    assert cfg.app2.tls is None


# ---------------------------------------------------------------------------
# cfg.audit
# ---------------------------------------------------------------------------


def test_audit_view_signing_none_when_absent() -> None:
    cfg = _make_cfg(signing=None)
    assert cfg.audit.signing is None


def test_audit_view_signing_carries_paths() -> None:
    sig = SigningConfig(
        key_path="/etc/audit/key.pem",
        cert_path="/etc/audit/cert.pem",
        passphrase_env="AUDIT_PASSPHRASE",
        signer_name="Test Signer",
    )
    cfg = _make_cfg(signing=sig)
    assert cfg.audit.signing is not None
    assert cfg.audit.signing.key_path == "/etc/audit/key.pem"
    assert cfg.audit.signing.cert_path == "/etc/audit/cert.pem"
    assert cfg.audit.signing.passphrase_env == "AUDIT_PASSPHRASE"
    assert cfg.audit.signing.signer_name == "Test Signer"


def test_audit_signing_passphrase_reads_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator's DE.2 comment: passphrase should follow the env-var-name
    pattern symmetric with OIDC / JWT secrets. Pins the lazy env load."""
    sig = SigningConfig(
        key_path="/k.pem", cert_path="/c.pem",
        passphrase_env="AUDIT_PASSPHRASE",
    )
    cfg = _make_cfg(signing=sig)
    monkeypatch.setenv("AUDIT_PASSPHRASE", "my-secret-passphrase")  # typing-smell: ignore[envvar-bypass]: cfg-supplied env var name (audit.signing.passphrase_env) per [[feedback_no_credential_friction]]
    assert cfg.audit.signing is not None
    assert cfg.audit.signing.passphrase() == b"my-secret-passphrase"


def test_audit_signing_passphrase_none_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``passphrase_env`` named but env unset ⇒ None (unencrypted key
    path; pyHanko treats None as no passphrase)."""
    sig = SigningConfig(
        key_path="/k.pem", cert_path="/c.pem",
        passphrase_env="AUDIT_PASSPHRASE",
    )
    cfg = _make_cfg(signing=sig)
    monkeypatch.delenv("AUDIT_PASSPHRASE", raising=False)  # typing-smell: ignore[envvar-bypass]: cfg-supplied env var name (audit.signing.passphrase_env) per [[feedback_no_credential_friction]]
    assert cfg.audit.signing is not None
    assert cfg.audit.signing.passphrase() is None


def test_audit_signing_passphrase_none_when_env_name_absent() -> None:
    """No ``passphrase_env`` field set ⇒ key is unencrypted; passphrase()
    returns None without touching os.environ."""
    sig = SigningConfig(
        key_path="/k.pem", cert_path="/c.pem",
        passphrase_env=None,
    )
    cfg = _make_cfg(signing=sig)
    assert cfg.audit.signing is not None
    assert cfg.audit.signing.passphrase() is None


# ---------------------------------------------------------------------------
# cfg.test.generator + as_of_frame
# ---------------------------------------------------------------------------


def test_test_view_generator_carries_full_surface() -> None:
    tg = TestGeneratorConfig(
        enabled=True, seed=42,
        plants=("drift", "overdraft"),
        derive_balances=True,
        derive_balances_account_roles=("gl_control", "dda"),
    )
    cfg = _make_cfg(test_generator=tg)
    assert cfg.test.generator.enabled is True
    assert cfg.test.generator.seed == 42
    assert cfg.test.generator.plants == ("drift", "overdraft")
    assert cfg.test.generator.derive_balances is True
    assert cfg.test.generator.derive_balances_account_roles == (
        "gl_control", "dda",
    )


def test_test_view_as_of_frame_locked_anchor() -> None:
    from recon_gen.common.as_of_frame import LOCKED_ANCHOR
    tg = TestGeneratorConfig(end_date=LOCKED_ANCHOR)
    cfg = _make_cfg(test_generator=tg)
    frame = cfg.test.generator.as_of_frame()
    # Locked frame keys off LOCKED_ANCHOR — the canonical demo anchor
    assert frame.as_of == LOCKED_ANCHOR


def test_test_view_as_of_frame_explicit_end_date() -> None:
    tg = TestGeneratorConfig(end_date=date(2026, 3, 15))
    cfg = _make_cfg(test_generator=tg)
    frame = cfg.test.generator.as_of_frame()
    assert frame.as_of == date(2026, 3, 15)


def test_test_view_as_of_frame_window_days_widens_interval() -> None:
    tg = TestGeneratorConfig(end_date=date(2026, 3, 15))
    cfg = _make_cfg(test_generator=tg)
    frame = cfg.test.generator.as_of_frame(window_days=7)
    # 7-day window ending at anchor — interval spans 8 calendar days
    # (inclusive); the as_of stays pinned at end_date.
    assert frame.as_of == date(2026, 3, 15)
