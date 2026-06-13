"""DE.2 commit A — proxy view properties on legacy ``Config``.

Pins that ``cfg.aws.X`` / ``cfg.db.X`` / ``cfg.app2.X`` / ``cfg.audit.X``
/ ``cfg.test.X`` / ``cfg.auth.aws.X`` read the same values as their
flat-field counterparts. Sweep commits B-E migrate callsites from
flat to nested; this test guards the bridge.

Methods on ``_AwsView`` (``partition`` / ``prefixed`` / ``tags`` /
``dataset_arn`` / ``theme_arn``) are pinned here too — the legacy
``Config.<method>`` shape used to live on Config; sweep moves callers
to ``cfg.aws.<method>``.
"""

from __future__ import annotations

from datetime import date

import pytest

from recon_gen.common.cleanup import (
    DEPLOYMENT_TAG_KEY,
    MANAGED_TAG_KEY,
    MANAGED_TAG_VALUE,
)
from recon_gen.common.config import (
    AuthConfig,
    AwsConfig,
    Config,
    SigningConfig,
    TestGeneratorConfig,
)
from recon_gen.common.sql import Dialect


def _make_cfg(**overrides: object) -> Config:
    """Build a minimal-but-complete legacy ``Config`` for proxy tests."""
    # DE.5 steps 3-7 — aws_account_id / aws_region / deployment_name /
    # datasource_arn / principal_arns moved into nested aws=AwsConfig(...).
    from recon_gen.common.config import DatasourceConfig  # noqa: PLC0415
    datasource_arn_raw = overrides.pop("datasource_arn", None)
    datasource_arn: str | None = (
        str(datasource_arn_raw) if datasource_arn_raw else None
    )
    principal_arns_raw = overrides.pop(  # noqa: PLC0415
        "principal_arns",
        ["arn:aws:iam::123456789012:role/TestRole"],
    )
    # Narrow object → iterable[str] for AwsConfig.principal_arns
    if not isinstance(principal_arns_raw, (list, tuple)):
        raise TypeError(f"principal_arns must be list/tuple; got {type(principal_arns_raw).__name__}")
    extra_tags_raw = overrides.pop("extra_tags", {})
    if not isinstance(extra_tags_raw, dict):
        raise TypeError(f"extra_tags must be dict; got {type(extra_tags_raw).__name__}")
    tagging_enabled_raw = overrides.pop("tagging_enabled", True)
    if not isinstance(tagging_enabled_raw, bool):
        raise TypeError(f"tagging_enabled must be bool; got {type(tagging_enabled_raw).__name__}")
    aws_kwargs: dict[str, object] = {
        "account_id": overrides.pop("aws_account_id", "123456789012"),
        "region": overrides.pop("aws_region", "us-east-1"),
        "deployment_name": overrides.pop("deployment_name", "test-deploy"),
        "principal_arns": tuple(str(p) for p in principal_arns_raw),  # type: ignore[union-attr]: isinstance check above narrows; str() per-element handles whatever the test passed
        "extra_tags": tuple(sorted(
            (str(k), str(v)) for k, v in extra_tags_raw.items()  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]: isinstance check above narrows
        )),
        "tagging_enabled": tagging_enabled_raw,
        "datasource": DatasourceConfig(
            mode=("adopt" if datasource_arn else "create"),
            arn=datasource_arn,
        ),
    }
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
        "aws": AwsConfig(**aws_kwargs),  # pyright: ignore[reportArgumentType]: dict[str, object] kwarg surface
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


def test_aws_view_carries_flat_fields() -> None:
    """Proxy reads underlying flat fields. RHS uses the LEGACY field
    names so this test catches a bridge-break (e.g., if cfg.aws.region
    accidentally reads cfg.aws.deployment_name)."""
    cfg = _make_cfg()
    assert cfg.aws.account_id == cfg.aws.account_id  # type: ignore[attr-defined]: legacy flat field; surviving through DE.5 collapse
    assert cfg.aws.region == cfg.aws.region  # type: ignore[attr-defined]: legacy flat field surviving through DE.5 collapse
    assert cfg.aws.deployment_name == cfg.aws.deployment_name  # type: ignore[attr-defined]: legacy flat field surviving through DE.5 collapse
    assert cfg.aws.principal_arns == ("arn:aws:iam::123456789012:role/TestRole",)


def test_aws_view_partition_commercial_default() -> None:
    cfg = _make_cfg(principal_arns=[])
    assert cfg.aws.partition == "aws"


def test_aws_view_partition_govcloud_from_principal() -> None:
    cfg = _make_cfg(
        principal_arns=["arn:aws-us-gov:iam::123456789012:role/RegOpsAdmin"],
    )
    assert cfg.aws.partition == "aws-us-gov"


def test_aws_view_partition_china_from_datasource_arn() -> None:
    cfg = _make_cfg(
        datasource_arn="arn:aws-cn:quicksight:cn-north-1:123456789012:datasource/preexisting",
    )
    assert cfg.aws.partition == "aws-cn"


def test_aws_view_prefixed_returns_deployment_prefix() -> None:
    cfg = _make_cfg(deployment_name="prod-deploy")
    assert cfg.aws.prefixed("foo") == "prod-deploy-foo"
    assert cfg.aws.prefixed("demo-datasource") == "prod-deploy-demo-datasource"


def test_aws_view_tags_emits_managed_by_and_deployment() -> None:
    cfg = _make_cfg(extra_tags={"Environment": "staging"})
    tags = cfg.aws.tags()
    assert tags is not None
    tag_dict = {t.Key: t.Value for t in tags}
    assert tag_dict[MANAGED_TAG_KEY] == MANAGED_TAG_VALUE
    assert tag_dict[DEPLOYMENT_TAG_KEY] == "test-deploy"
    assert tag_dict["Environment"] == "staging"


def test_aws_view_tags_returns_none_when_disabled() -> None:
    """``tagging_enabled=False`` ⇒ tags() returns None so the Create*
    boto call carries no Tags kwarg + IAM doesn't need TagResource."""
    cfg = _make_cfg(tagging_enabled=False)
    assert cfg.aws.tags() is None


def test_aws_view_dataset_arn_synthesizes_with_partition() -> None:
    cfg = _make_cfg(
        principal_arns=["arn:aws-us-gov:iam::123456789012:role/Op"],
        aws_region="us-gov-east-1",
    )
    arn = cfg.aws.dataset_arn("my-dataset")
    assert arn == "arn:aws-us-gov:quicksight:us-gov-east-1:123456789012:dataset/my-dataset"


def test_aws_view_theme_arn_synthesizes_with_partition() -> None:
    cfg = _make_cfg()
    arn = cfg.aws.theme_arn("my-theme")
    assert arn == "arn:aws:quicksight:us-east-1:123456789012:theme/my-theme"


def test_aws_view_datasource_mode_adopt_when_arn_set() -> None:
    """Legacy presence-of-arn dispatch maps to ``mode=adopt``."""
    cfg = _make_cfg(
        datasource_arn="arn:aws:quicksight:us-east-1:123456789012:datasource/preexisting",
    )
    assert cfg.aws.datasource.mode == "adopt"
    assert cfg.aws.datasource.arn.endswith("preexisting")  # pyright: ignore[reportOptionalMemberAccess]: asserted not-None by datasource_arn present at construction


def test_aws_view_datasource_mode_create_when_arn_derived() -> None:
    """No explicit datasource_arn ⇒ Config.__post_init__ derives one;
    proxy sees mode=adopt because the field is now set. (Mode=create
    only when no arn was ever produced — pre-derive state; in practice
    every loaded cfg has a derived arn.) Pins the legacy semantic."""
    cfg = _make_cfg(datasource_arn=None)
    # After __post_init__ derive, datasource_arn IS populated. Proxy
    # reflects the live state — adopt-after-derive matches today's
    # legacy code which uses `datasource_arn_was_derived` as the
    # discriminator; DE.4 wires the v14 mode=create path explicitly.
    assert cfg.aws.datasource.arn is not None
    assert cfg.aws.datasource.mode == "create"


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


# ---------------------------------------------------------------------------
# cfg.auth.aws
# ---------------------------------------------------------------------------


def test_auth_view_default_when_block_absent() -> None:
    """No auth: block in cfg ⇒ cfg.auth is the empty AuthConfig
    (DE.2 commit A made auth default-factory). cfg.auth.aws.profile
    is None without None-checking cfg.auth."""
    cfg = _make_cfg()
    assert cfg.auth.aws.profile is None
    assert cfg.auth.aws.quicksight_user_arn is None


def test_auth_view_carries_profile_when_set() -> None:
    cfg = _make_cfg(
        auth=AuthConfig(
            aws_profile="recon-gen-local",
            quicksight_user_arn="arn:aws:quicksight:us-east-1:123:user/default/test",
        ),
    )
    assert cfg.auth.aws.profile == "recon-gen-local"
    assert cfg.auth.aws.quicksight_user_arn == (
        "arn:aws:quicksight:us-east-1:123:user/default/test"
    )
