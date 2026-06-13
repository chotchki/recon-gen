# pyright: reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false
# DE.1 — pyright pragmas relax strict on this file during the v14 migration
# only. yaml.safe_load returns ``Any | None`` recursively; tightening the
# narrowing here would add 200+ lines of cast / isinstance scaffolding for
# a file scheduled to be deleted (legacy config.py) or renamed when DE.2
# sweeps callsites. DE.2 phase exit tightens; for now the runtime
# validation (_build_*) catches malformed cfg shapes.
"""DE.1 — v14.0.0 cfg.yaml shape (concern-grouped, ``extends:`` inheritance, derive-on-load).

Designed against `docs/audits/de_0_cfg_redesign.md` locks. The legacy
shape lives at ``src/recon_gen/common/config.py`` for the duration of
DE.1's migration; once every callsite swaps to ``cfg.aws.X`` /
``cfg.db.X`` / etc., the legacy module is deleted + this file is
renamed to ``config.py`` (per ``[[feedback_no_compat_shims]]``: hard
break to v14.0.0, no compat shim).

Highlights:

- **Concern grouping.** ``aws: / db: / auth: / app2: / audit: / test:``.
- **``extends:`` inheritance.** ``extends: [./base.yaml]`` deep-merges
  the parent under the child; chains supported via recursive resolution.
  Cycle-detected via ``_seen`` set; raises ``CycleError`` on detection.
- **Derive-on-load.** ``db.table_prefix`` defaults to
  ``aws.deployment_name.replace('-', '_')`` when absent;
  ``auth.aws.quicksight_user_arn`` derives via
  ``boto3.list_users(Namespace="default")`` ADMIN-role lookup.
- **Legacy-key detection.** Loader raises ``LegacyFieldError`` with a
  migration hint when v13 keys appear at top level.
- **Datasource lifecycle as explicit enum.** ``aws.datasource.mode``:
  ``create | adopt | skip``. ``skip`` is the no-AWS-cost test path
  (per operator's inline comment in DE.0 spike).

Used by DC.1 + DD.1 + DE.2 sweeps once approved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from recon_gen.common.sql import Dialect


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CfgError(Exception):
    """Base for v14 cfg errors. Operator-facing message format:
    short cause + actionable next step."""


class CycleError(CfgError):
    """``extends:`` chain references a cfg already in the resolution
    set. Carry the cycle path for operator triage."""


class LegacyFieldError(CfgError):
    """A v13-shape key appeared at the top level. Carries the
    migration hint with the new path."""


class MissingFieldError(CfgError):
    """Required field absent after merge + derivation. Carries the
    field path + the cfg files that contributed to the merged
    result."""


# ---------------------------------------------------------------------------
# Datasource lifecycle
# ---------------------------------------------------------------------------


class DatasourceMode(str, Enum):
    """``aws.datasource.mode`` — explicit replacement for the pre-DE
    implicit dispatch on ``datasource_arn`` presence.

    Per operator-flagged DE.0 comment: the implicit-presence-of-key
    dispatch was opaque + provided no escape for tests that exercise
    the cfg shape but don't want to pay AWS API costs.
    """
    CREATE = "create"      # generator creates the QS datasource (default for prod-deploy)
    ADOPT = "adopt"        # use an explicit ``aws.datasource.arn``; don't try to create
    SKIP = "skip"          # don't touch the QS datasource API at all (test-mode)


@dataclass(frozen=True)
class DatasourceConfig:
    mode: DatasourceMode = DatasourceMode.CREATE
    arn: str | None = None  # required iff mode=ADOPT; ignored otherwise


# ---------------------------------------------------------------------------
# AWS block
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AwsConfig:
    """Everything QS-deploy + AWS-API-side. Required when any
    AWS-touching surface is used (``json apply``, ``audit apply``,
    e2e qs_api/qs_browser layers)."""
    account_id: str
    region: str
    deployment_name: str
    principal_arns: tuple[str, ...] = ()
    extra_tags: tuple[tuple[str, str], ...] = ()
    tagging_enabled: bool = True
    qs_disable_pg_ssl: bool = False
    pg_cluster_id: str | None = None
    oracle_instance_id: str | None = None
    datasource: DatasourceConfig = field(default_factory=DatasourceConfig)


# ---------------------------------------------------------------------------
# DB block
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DbConfig:
    """Database connection + dialect + L2 selection. The ``url`` is
    the rename of pre-DE ``demo_database_url`` (the v1-demo prefix
    fossilized through years; the field now serves production
    deployments too)."""
    dialect: Dialect
    url: str
    table_prefix: str  # derived from aws.deployment_name when absent
    default_l2_instance: str | None = None
    app2_pool_size: int = 10


# ---------------------------------------------------------------------------
# Auth block (the three concerns nested)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthAwsConfig:
    """AWS-side auth. Pre-DE: top-level ``auth.aws_profile`` +
    ``auth.quicksight_user_arn``. Post-DE: nested under
    ``auth.aws.{profile, quicksight_user_arn}``."""
    profile: str | None = None
    quicksight_user_arn: str | None = None  # derived via list_users when absent


@dataclass(frozen=True)
class AuthOidcConfig:
    """OIDC client config (Phase DD)."""
    issuer_url: str
    client_id: str
    client_secret_env: str
    redirect_uri: str
    scopes: tuple[str, ...] = ("openid", "email", "profile")


@dataclass(frozen=True)
class AuthSessionConfig:
    """Session config (Phase DD). JWT-only — no server-side store."""
    jwt_secret_env: str


@dataclass(frozen=True)
class AuthConfig:
    aws: AuthAwsConfig = field(default_factory=AuthAwsConfig)
    oidc: AuthOidcConfig | None = None    # populated by DD
    session: AuthSessionConfig | None = None  # populated by DD


# ---------------------------------------------------------------------------
# App2 block
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class App2TlsConfig:
    """TLS termination paths (Phase DC). Absent ⇒ HTTP."""
    cert_path: str
    key_path: str


@dataclass(frozen=True)
class App2Config:
    etl_hook: str | None = None
    banner_text: str | None = None
    tls: App2TlsConfig | None = None  # populated by DC


# ---------------------------------------------------------------------------
# Audit block
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditSigningConfig:
    """Signing material for audit PDF. Pre-DE ``signing.*`` →
    post-DE ``audit.signing.*``."""
    key_path: str
    cert_path: str
    passphrase_env: str | None = None
    signer_name: str | None = None


@dataclass(frozen=True)
class AuditConfig:
    signing: AuditSigningConfig | None = None


# ---------------------------------------------------------------------------
# Test block (optional + collapsed per DE.0 lock)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TestGeneratorConfig:
    """Fuzz-L2 generation knobs. Operators on prod-deploy postures
    never set this — defaults are sane for the bundled fuzz seeds."""
    enabled: bool = False
    seed: int | None = None


@dataclass(frozen=True)
class TestConfig:
    generator: TestGeneratorConfig = field(default_factory=TestGeneratorConfig)


# ---------------------------------------------------------------------------
# Top-level Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    """v14 cfg shape. Hard break from v13 — loader raises
    ``LegacyFieldError`` on legacy top-level keys."""
    aws: AwsConfig
    db: DbConfig
    auth: AuthConfig = field(default_factory=AuthConfig)
    app2: App2Config = field(default_factory=App2Config)
    audit: AuditConfig = field(default_factory=AuditConfig)
    test: TestConfig = field(default_factory=TestConfig)


# ---------------------------------------------------------------------------
# Legacy → new field-path migration map (used by the LegacyFieldError raiser)
# ---------------------------------------------------------------------------


_LEGACY_TO_NEW: dict[str, str] = {
    # Top-level fields (pre-DE)
    "aws_account_id": "aws.account_id",
    "aws_region": "aws.region",
    "deployment_name": "aws.deployment_name",
    "db_table_prefix": "db.table_prefix (auto-derived from aws.deployment_name when absent)",
    "principal_arns": "aws.principal_arns",
    "datasource_arn": "aws.datasource.arn (with aws.datasource.mode: adopt)",
    "extra_tags": "aws.extra_tags",
    "tagging_enabled": "aws.tagging_enabled",
    "qs_disable_pg_ssl": "aws.qs_disable_pg_ssl",
    "aws_pg_cluster_id": "aws.pg_cluster_id",
    "aws_oracle_instance_id": "aws.oracle_instance_id",
    "dialect": "db.dialect",
    "demo_database_url": "db.url",
    "app2_db_pool_size": "db.app2_pool_size",
    "default_l2_instance": "db.default_l2_instance",
    "studio_enabled": "(removed — absence of app2: block ⇒ studio off)",
    "etl_hook": "app2.etl_hook",
    "banner_text": "app2.banner_text",
    "signing": "audit.signing",
    "test_generator": "test.generator",
    # Legacy auth: block (was AWS-only, now nested under auth.aws.*)
    "auth.aws_profile": "auth.aws.profile",
    "auth.quicksight_user_arn": "auth.aws.quicksight_user_arn",
}


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _deep_merge(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Child-wins deep merge. Dicts merge recursively; lists +
    scalars: child replaces parent (no append). Lists wanting append
    semantics use explicit ``[{{ inherited }}, new]`` in child —
    per DE.0 lock."""
    merged: dict[str, Any] = dict(parent)
    for key, child_value in child.items():
        parent_value = merged.get(key)
        if isinstance(parent_value, dict) and isinstance(child_value, dict):
            merged[key] = _deep_merge(parent_value, child_value)
        else:
            merged[key] = child_value
    return merged


def _load_raw(
    path: Path, _seen: set[Path] | None = None,
) -> dict[str, Any]:
    """Recursively load YAML + apply ``extends:``. Returns the
    merged raw dict (pre-typed)."""
    _seen = _seen if _seen is not None else set()
    abs_path = path.resolve()
    if abs_path in _seen:
        cycle = " → ".join(str(p) for p in _seen) + f" → {abs_path}"
        raise CycleError(f"extends: cycle detected: {cycle}")
    _seen.add(abs_path)
    raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    extends = raw.pop("extends", None)
    if extends is None:
        return raw
    # extends: must be a list per DE.0 lock (composition-friendly).
    if isinstance(extends, str):
        extends = [extends]
    if not isinstance(extends, list):
        raise CfgError(
            f"extends: must be a list of paths (or a single string), "
            f"got {type(extends).__name__} in {abs_path}"
        )
    merged: dict[str, Any] = {}
    for ext_path_str in extends:
        if not isinstance(ext_path_str, str):
            raise CfgError(
                f"extends: entries must be strings, got "
                f"{type(ext_path_str).__name__} in {abs_path}"
            )
        ext_path = (path.parent / ext_path_str).resolve()
        ext_raw = _load_raw(ext_path, _seen=set(_seen))  # isolated branch
        merged = _deep_merge(merged, ext_raw)
    return _deep_merge(merged, raw)


def _check_legacy_keys(raw: dict[str, Any], path: Path) -> None:
    """Raise LegacyFieldError if any v13-shape key appears."""
    for legacy, new in _LEGACY_TO_NEW.items():
        if "." in legacy:
            # Nested check: auth.aws_profile etc.
            block, key = legacy.split(".", 1)
            block_val = raw.get(block)
            if isinstance(block_val, dict) and key in block_val:
                raise LegacyFieldError(
                    f"{path}: legacy field {legacy!r} is no longer supported in "
                    f"v14.0.0 cfg. Use {new!r} instead. See "
                    f"docs/audits/de_0_cfg_redesign.md for the full migration table."
                )
        else:
            if legacy in raw:
                raise LegacyFieldError(
                    f"{path}: legacy field {legacy!r} is no longer supported in "
                    f"v14.0.0 cfg. Use {new!r} instead. See "
                    f"docs/audits/de_0_cfg_redesign.md for the full migration table."
                )


def _resolve_dialect(value: Any) -> Dialect:
    if isinstance(value, Dialect):
        return value
    if isinstance(value, str):
        return Dialect(value)
    raise CfgError(f"db.dialect must be a string or Dialect, got {type(value).__name__}")


def _build_aws(raw: dict[str, Any], path: Path) -> AwsConfig:
    block = raw.get("aws")
    if not isinstance(block, dict):
        raise MissingFieldError(f"{path}: required block 'aws:' is absent")
    required = ("account_id", "region", "deployment_name")
    for key in required:
        if key not in block:
            raise MissingFieldError(f"{path}: required field 'aws.{key}' is absent")
    ds_block = block.get("datasource", {})
    if not isinstance(ds_block, dict):
        raise CfgError(f"{path}: 'aws.datasource' must be a mapping")
    ds_mode_str = ds_block.get("mode", DatasourceMode.CREATE.value)
    try:
        ds_mode = DatasourceMode(ds_mode_str)
    except ValueError:
        raise CfgError(
            f"{path}: aws.datasource.mode must be one of "
            f"{[m.value for m in DatasourceMode]}, got {ds_mode_str!r}"
        ) from None
    ds_arn = ds_block.get("arn")
    if ds_mode == DatasourceMode.ADOPT and not ds_arn:
        raise MissingFieldError(
            f"{path}: aws.datasource.mode='adopt' requires aws.datasource.arn"
        )
    extra_tags = block.get("extra_tags", {})
    if isinstance(extra_tags, dict):
        tags_tuple = tuple(sorted(extra_tags.items()))
    else:
        raise CfgError(f"{path}: aws.extra_tags must be a mapping")
    principals = block.get("principal_arns", [])
    if not isinstance(principals, list):
        raise CfgError(f"{path}: aws.principal_arns must be a list")
    return AwsConfig(
        account_id=str(block["account_id"]),
        region=str(block["region"]),
        deployment_name=str(block["deployment_name"]),
        principal_arns=tuple(str(p) for p in principals),
        extra_tags=tags_tuple,
        tagging_enabled=bool(block.get("tagging_enabled", True)),
        qs_disable_pg_ssl=bool(block.get("qs_disable_pg_ssl", False)),
        pg_cluster_id=block.get("pg_cluster_id"),
        oracle_instance_id=block.get("oracle_instance_id"),
        datasource=DatasourceConfig(mode=ds_mode, arn=ds_arn),
    )


def _build_db(raw: dict[str, Any], aws: AwsConfig, path: Path) -> DbConfig:
    block = raw.get("db")
    if not isinstance(block, dict):
        raise MissingFieldError(f"{path}: required block 'db:' is absent")
    for key in ("dialect", "url"):
        if key not in block:
            raise MissingFieldError(f"{path}: required field 'db.{key}' is absent")
    table_prefix = block.get("table_prefix")
    if not table_prefix:
        # Derive from aws.deployment_name (`-` → `_`)
        table_prefix = aws.deployment_name.replace("-", "_")
    return DbConfig(
        dialect=_resolve_dialect(block["dialect"]),
        url=str(block["url"]),
        table_prefix=str(table_prefix),
        default_l2_instance=block.get("default_l2_instance"),
        app2_pool_size=int(block.get("app2_pool_size", 10)),
    )


def _build_auth(raw: dict[str, Any], path: Path) -> AuthConfig:
    del path  # No required fields in auth — all optional
    block = raw.get("auth", {})
    if not isinstance(block, dict):
        raise CfgError(f"auth must be a mapping when present")
    aws_block = block.get("aws", {})
    if not isinstance(aws_block, dict):
        raise CfgError("auth.aws must be a mapping")
    aws_auth = AuthAwsConfig(
        profile=aws_block.get("profile"),
        quicksight_user_arn=aws_block.get("quicksight_user_arn"),
    )
    # DD-side blocks; absent ⇒ None
    oidc_block = block.get("oidc")
    oidc = None
    if isinstance(oidc_block, dict):
        oidc = AuthOidcConfig(
            issuer_url=str(oidc_block["issuer_url"]),
            client_id=str(oidc_block["client_id"]),
            client_secret_env=str(oidc_block["client_secret_env"]),
            redirect_uri=str(oidc_block["redirect_uri"]),
            scopes=tuple(oidc_block.get("scopes", ["openid", "email", "profile"])),
        )
    session_block = block.get("session")
    session = None
    if isinstance(session_block, dict):
        session = AuthSessionConfig(
            jwt_secret_env=str(session_block["jwt_secret_env"]),
        )
    return AuthConfig(aws=aws_auth, oidc=oidc, session=session)


def _build_app2(raw: dict[str, Any]) -> App2Config:
    block = raw.get("app2", {})
    if not isinstance(block, dict):
        raise CfgError("app2 must be a mapping when present")
    tls_block = block.get("tls")
    tls = None
    if isinstance(tls_block, dict):
        tls = App2TlsConfig(
            cert_path=str(tls_block["cert_path"]),
            key_path=str(tls_block["key_path"]),
        )
    return App2Config(
        etl_hook=block.get("etl_hook"),
        banner_text=block.get("banner_text"),
        tls=tls,
    )


def _build_audit(raw: dict[str, Any]) -> AuditConfig:
    block = raw.get("audit", {})
    if not isinstance(block, dict):
        raise CfgError("audit must be a mapping when present")
    signing_block = block.get("signing")
    signing = None
    if isinstance(signing_block, dict):
        for key in ("key_path", "cert_path"):
            if key not in signing_block:
                raise MissingFieldError(f"audit.signing.{key} is required when audit.signing block present")
        signing = AuditSigningConfig(
            key_path=str(signing_block["key_path"]),
            cert_path=str(signing_block["cert_path"]),
            passphrase_env=signing_block.get("passphrase_env"),
            signer_name=signing_block.get("signer_name"),
        )
    return AuditConfig(signing=signing)


def _build_test(raw: dict[str, Any]) -> TestConfig:
    block = raw.get("test", {})
    if not isinstance(block, dict):
        raise CfgError("test must be a mapping when present")
    gen_block = block.get("generator", {})
    if not isinstance(gen_block, dict):
        raise CfgError("test.generator must be a mapping when present")
    return TestConfig(
        generator=TestGeneratorConfig(
            enabled=bool(gen_block.get("enabled", False)),
            seed=gen_block.get("seed"),
        ),
    )


def load_config(path: str | Path | None = None) -> Config:
    """Load a v14 cfg.yaml. Returns a typed Config.

    Resolution:
    1. ``path`` argument → ``RECON_GEN_CONFIG`` env → candidate list (see legacy loader).
    2. Recursively resolve ``extends:`` chain (deep-merge child over parent).
    3. Reject legacy v13 keys with migration hints.
    4. Type-check + derive-on-load (db.table_prefix from aws.deployment_name).
    """
    if path is None:
        # Mirror legacy candidate list. Bare load_config() == default.
        from recon_gen.common.env_keys import RECON_GEN_CONFIG  # noqa: PLC0415 — lazy
        env_override = RECON_GEN_CONFIG.get_or_none()
        if env_override:
            path = Path(env_override)
        else:
            for candidate in ("config.yaml", "run/config.yaml"):
                if Path(candidate).is_file():
                    path = Path(candidate)
                    break
            if path is None:
                raise CfgError(
                    "No cfg path provided + no candidate found "
                    "(config.yaml / run/config.yaml). Set RECON_GEN_CONFIG "
                    "or pass path explicitly."
                )
    if isinstance(path, str):
        path = Path(path)
    raw = _load_raw(path)
    _check_legacy_keys(raw, path)
    aws = _build_aws(raw, path)
    db = _build_db(raw, aws, path)
    return Config(
        aws=aws,
        db=db,
        auth=_build_auth(raw, path),
        app2=_build_app2(raw),
        audit=_build_audit(raw),
        test=_build_test(raw),
    )
