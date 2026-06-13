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
from datetime import date
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml

from recon_gen.common.sql import Dialect

if TYPE_CHECKING:
    from recon_gen.common.as_of_frame import AsOfFrame


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

    @property
    def partition(self) -> str:
        """AWS partition for synthesized ARNs.

        Commercial AWS = ``aws``; GovCloud = ``aws-us-gov``;
        China = ``aws-cn``. Hardcoding ``aws`` breaks deploys against
        GovCloud / China where every account-bound resource ARN must
        carry the matching partition or QS rejects the binding.

        Resolution order (mirrors pre-DE ``Config.partition``):

        1. If ``datasource.arn`` is set explicitly (operator supplied a
           pre-existing datasource, e.g. ``mode=adopt``), parse partition
           from it — that's the authoritative shape for THIS account.
        2. Else if ``principal_arns`` is non-empty, parse from the first
           ``arn:``-prefixed entry — the customer's user/role lives in
           the same partition as the resources we synthesize.
        3. Else default ``aws`` (commercial; preserves prior behavior for
           fuzz fixtures that don't carry a principal).

        Bare strings (no ``arn:`` prefix) fall through to the default.
        Note: this is a STRING-prefix parse, not a region-based derive,
        because operator can override the partition explicitly via the
        ARN they supply even if region prefix would suggest otherwise.
        Region-based partition derive is boto3's job (it picks the right
        endpoint from region prefix); cfg-level partition is for ARN
        SYNTHESIS in deploy emitters.
        """
        sources: list[str | None] = [self.datasource.arn, *self.principal_arns]
        for source in sources:
            if source and source.startswith("arn:"):
                parts = source.split(":", 2)
                if len(parts) >= 2 and parts[1]:
                    return parts[1]
        return "aws"

    def prefixed(self, name: str) -> str:
        """Return a resource ID with the configured deployment prefix.

        Z.C: single-segment prefix ``<deployment_name>-<name>``."""
        return f"{self.deployment_name}-{name}"

    def tags(self) -> list[Any] | None:
        """Return common + extra tags as the AWS Tag list format.

        Two tags always emitted (when ``tagging_enabled``):
        ``ManagedBy=recon-gen`` (cleanup eligibility gate) +
        ``Deployment=<deployment_name>`` (per-deploy scope).

        Returns ``None`` when ``tagging_enabled=False`` so the caller's
        ``Tags=cfg.aws.tags()`` goes to the AWS API as no Tags kwarg
        (no ``quicksight:TagResource`` permission needed).
        """
        if not self.tagging_enabled:
            return None
        from recon_gen.common.models import Tag  # noqa: PLC0415

        all_tags = [
            Tag(Key="ManagedBy", Value="recon-gen"),
            Tag(Key="Deployment", Value=self.deployment_name),
        ]
        for key, value in self.extra_tags:
            all_tags.append(Tag(Key=key, Value=value))
        return all_tags

    def dataset_arn(self, dataset_id: str) -> str:
        """Synthesize a QuickSight dataset ARN under this aws-block's
        partition / region / account."""
        return (
            f"arn:{self.partition}:quicksight:{self.region}"
            f":{self.account_id}:dataset/{dataset_id}"
        )

    def theme_arn(self, theme_id: str) -> str:
        """Synthesize a QuickSight theme ARN under this aws-block's
        partition / region / account."""
        return (
            f"arn:{self.partition}:quicksight:{self.region}"
            f":{self.account_id}:theme/{theme_id}"
        )


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
    db_pool_size: int = 10


# ---------------------------------------------------------------------------
# Audit block
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditSigningConfig:
    """Signing material for audit PDF. Pre-DE ``signing.*`` →
    post-DE ``audit.signing.*``. ``passphrase_env`` names an env var
    holding the key passphrase per ``[[feedback_no_credential_friction]]``;
    secret never lives in cfg yaml."""
    key_path: str
    cert_path: str
    passphrase_env: str | None = None
    signer_name: str | None = None

    def passphrase(self) -> bytes | None:
        """Load passphrase from ``os.environ[passphrase_env]`` lazily.

        Returns bytes for pyHanko's CMS signer (it takes bytes).
        ``None`` ⇒ key is unencrypted OR env var unset (pyHanko loads
        the unencrypted key under None)."""
        if self.passphrase_env is None:
            return None
        import os  # noqa: PLC0415 — lazy: env-touch only when audit signs
        val = os.environ.get(self.passphrase_env)  # typing-smell: ignore[envvar-bypass]: cfg-supplied env var name (audit.signing.passphrase_env) per [[feedback_no_credential_friction]]
        if not val:
            return None
        return val.encode("utf-8")


@dataclass(frozen=True)
class AuditConfig:
    signing: AuditSigningConfig | None = None


# ---------------------------------------------------------------------------
# Test block (optional + collapsed per DE.0 lock)
# ---------------------------------------------------------------------------


ScopeKind = Literal[
    "full", "exceptions_only", "uncovered_rails", "only_template",
]
PlantKind = Literal[
    "drift", "overdraft", "limit_breach",
    "stuck_pending", "stuck_unbundled", "supersession",
]


@dataclass(frozen=True)
class TestGeneratorConfig:
    """Synthetic-data overlay knobs (Step 3 of deploy pipeline).

    Default ``enabled=True`` with empty plants tuple preserves
    byte-identical-to-locked-seeds output — every field's default is
    the no-op for ``emit_full_seed``.
    """
    # Class name starts with "Test" so pytest collection emits a
    # PytestCollectionWarning. ``__test__ = False`` suppresses collection
    # without renaming the class.
    __test__ = False

    enabled: bool = True
    scope: "ScopeKind" = "full"
    end_date: "date | None" = None
    seed: int | None = None
    plants: tuple["PlantKind", ...] = ()
    only_template: str | None = None
    derive_balances: bool = False
    derive_balances_account_roles: tuple[str, ...] | None = None
    cutoff_date: "date | None" = None

    def as_of_frame(
        self,
        *,
        window_days: int = 0,
        db_anchor: "date | None" = None,
    ) -> "AsOfFrame":
        """Resolve this cfg's scenario anchor as the owned ``AsOfFrame``.

        Resolution paths (D1 contract):
        - ``end_date == LOCKED_ANCHOR`` → locked frame (byte-identity
          tests gate off this).
        - ``end_date is not None`` → explicit-anchor frame.
        - ``end_date is None`` + ``db_anchor is not None`` → pin live
          frame at the DB-derived latest balance day.
        - ``end_date is None`` + ``db_anchor is None`` → live frame.
        """
        from recon_gen.common.as_of_frame import LOCKED_ANCHOR, AsOfFrame  # noqa: PLC0415
        from recon_gen.common.intervals import DateInterval  # noqa: PLC0415
        if self.end_date == LOCKED_ANCHOR:
            return AsOfFrame.locked(window_days=window_days)
        if self.end_date is not None:
            window = (
                DateInterval.single_day(self.end_date)
                if window_days <= 0
                else DateInterval.trailing_days_ending_today(
                    self.end_date, window_days + 1,
                )
            )
            return AsOfFrame(as_of=self.end_date, window=window)
        if db_anchor is not None:
            window = (
                DateInterval.single_day(db_anchor)
                if window_days <= 0
                else DateInterval.trailing_days_ending_today(
                    db_anchor, window_days + 1,
                )
            )
            return AsOfFrame(as_of=db_anchor, window=window)
        return AsOfFrame.live(window_days=window_days)


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
    # DD-side blocks; absent ⇒ None. Presence ⇒ required-field check
    # so partial blocks (operator dropped the client_id) fail loudly
    # with the field path, not a bare KeyError mid-handler.
    oidc_block = block.get("oidc")
    oidc = None
    if isinstance(oidc_block, dict):
        for key in ("issuer_url", "client_id", "client_secret_env", "redirect_uri"):
            if key not in oidc_block:
                raise MissingFieldError(
                    f"auth.oidc.{key} is required when auth.oidc block present"
                )
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
        if "jwt_secret_env" not in session_block:
            raise MissingFieldError(
                "auth.session.jwt_secret_env is required when "
                "auth.session block present"
            )
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
    # Full TestGeneratorConfig surface — parses scope / end_date /
    # plants / only_template / derive_balances / etc. for the deploy
    # pipeline's synthetic-data overlay.
    end_date_raw = gen_block.get("end_date")
    end_date_val: date | None = None
    if end_date_raw is not None:
        if isinstance(end_date_raw, date):
            end_date_val = end_date_raw
        elif isinstance(end_date_raw, str):
            try:
                end_date_val = date.fromisoformat(end_date_raw)
            except ValueError as exc:
                raise CfgError(
                    f"test.generator.end_date must be ISO 8601 (YYYY-MM-DD); "
                    f"got {end_date_raw!r}"
                ) from exc
        else:
            raise CfgError(
                f"test.generator.end_date must be a date / ISO string; "
                f"got {type(end_date_raw).__name__}"
            )
    cutoff_date_raw = gen_block.get("cutoff_date")
    cutoff_date_val: date | None = None
    if cutoff_date_raw is not None:
        if isinstance(cutoff_date_raw, date):
            cutoff_date_val = cutoff_date_raw
        elif isinstance(cutoff_date_raw, str):
            try:
                cutoff_date_val = date.fromisoformat(cutoff_date_raw)
            except ValueError as exc:
                raise CfgError(
                    f"test.generator.cutoff_date must be ISO 8601 (YYYY-MM-DD); "
                    f"got {cutoff_date_raw!r}"
                ) from exc
        else:
            raise CfgError(
                f"test.generator.cutoff_date must be a date / ISO string; "
                f"got {type(cutoff_date_raw).__name__}"
            )
    plants_raw = gen_block.get("plants", ())
    if not isinstance(plants_raw, (list, tuple)):
        raise CfgError(
            f"test.generator.plants must be a list; got {type(plants_raw).__name__}"
        )
    plants_tuple = tuple(str(p) for p in plants_raw)
    drb_raw = gen_block.get("derive_balances_account_roles")
    drb_tuple: tuple[str, ...] | None = None
    if drb_raw is not None:
        if not isinstance(drb_raw, (list, tuple)):
            raise CfgError(
                f"test.generator.derive_balances_account_roles must be a list / null; "
                f"got {type(drb_raw).__name__}"
            )
        drb_tuple = tuple(str(r) for r in drb_raw)
    scope_val = str(gen_block.get("scope", "full"))
    if scope_val not in ("full", "exceptions_only", "uncovered_rails", "only_template"):
        raise CfgError(
            f"test.generator.scope must be one of full/exceptions_only/"
            f"uncovered_rails/only_template; got {scope_val!r}"
        )
    return TestConfig(
        generator=TestGeneratorConfig(
            enabled=bool(gen_block.get("enabled", True)),
            scope=scope_val,  # pyright: ignore[reportArgumentType]: validated against literal options above
            end_date=end_date_val,
            seed=gen_block.get("seed"),
            plants=plants_tuple,  # pyright: ignore[reportArgumentType]: PlantKind validation runs at builder consumption time per docs/audits/de_0_cfg_redesign.md (legacy did the same)
            only_template=gen_block.get("only_template"),
            derive_balances=bool(gen_block.get("derive_balances", False)),
            derive_balances_account_roles=drb_tuple,
            cutoff_date=cutoff_date_val,
        ),
    )


# ---------------------------------------------------------------------------
# QuickSight user ARN — lazy, cached per (profile, account, region)
# ---------------------------------------------------------------------------


_QS_USER_ARN_CACHE: dict[tuple[str, str, str], str | None] = {}


def resolve_qs_user_arn(cfg: Config) -> str | None:
    """Lazy resolve the QuickSight user ARN for e2e tests.

    Priority (mirrors `_dev/runner.py::_resolve_qs_user_arn` against the
    v14 cfg paths; called by the runner just before qs_browser subprocess
    env injection — NOT eager-on-load, because every cfg-loading unit
    test would otherwise fire boto):

    1. **Explicit override.** ``cfg.auth.aws.quicksight_user_arn`` (set
       by CI via the ``RECON_E2E_USER_ARN`` GH secret upstream of cfg).
    2. **Derive from profile.** ``cfg.auth.aws.profile`` named boto
       profile + ``cfg.aws.account_id`` + ``cfg.aws.region`` →
       ``quicksight.list_users(Namespace='default')`` → first ADMIN
       user's ARN (falls back to first user when no ADMIN found).
    3. **None.** Caller leaves the env unset; ``qs_driver_or_none``
       skips QS-leg tests with the standard "QS user ARN unavailable".

    Boto failure (expired creds, ListUsers denied) → None + stderr
    breadcrumb; we don't want a transient AWS hiccup to abort the chain.
    Cached per ``(profile, account_id, region)`` so the runner's per-cell
    subprocess spawns share the lookup.
    """
    explicit = cfg.auth.aws.quicksight_user_arn
    if explicit:
        return explicit
    profile = cfg.auth.aws.profile
    if not profile:
        return None
    account_id = cfg.aws.account_id
    region = cfg.aws.region
    cache_key = (profile, account_id, region)
    if cache_key in _QS_USER_ARN_CACHE:
        return _QS_USER_ARN_CACHE[cache_key]
    import sys  # noqa: PLC0415 — used by stderr breadcrumb on failure paths
    try:
        import boto3  # noqa: PLC0415 — lazy: only on the derive path
        session = boto3.Session(profile_name=profile, region_name=region)
        qs: Any = session.client("quicksight")  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]: boto3-stubs overload union confuses pyright (X.2.o.5); mirrors the wrap-in-Any pattern from _dev/runner.py:_resolve_qs_user_arn
        users = qs.list_users(
            AwsAccountId=account_id, Namespace="default",
        ).get("UserList", [])
    except Exception as exc:  # noqa: BLE001 — boto-side hiccup is a breadcrumb, not a chain-abort
        print(
            f"config_v14: derive QS user ARN failed via aws_profile="
            f"{profile!r} ({type(exc).__name__}: {exc}); qs_browser will skip",
            file=sys.stderr,
        )
        _QS_USER_ARN_CACHE[cache_key] = None
        return None
    if not users:
        print(
            f"config_v14: derive QS user ARN found 0 users in "
            f"{account_id}/{region} default namespace via profile="
            f"{profile!r}; qs_browser will skip",
            file=sys.stderr,
        )
        _QS_USER_ARN_CACHE[cache_key] = None
        return None
    admins = [u for u in users if u.get("Role") == "ADMIN"]
    target = admins[0] if admins else users[0]
    arn = target.get("Arn")
    _QS_USER_ARN_CACHE[cache_key] = arn
    return arn


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
    _apply_env_overrides(raw)
    aws = _build_aws(raw, path)
    db = _build_db(raw, aws, path)
    # DE.5 — datasource.arn auto-derive when mode=create + db.url is
    # set. Mirrors pre-DE Config.__post_init__ behavior so the deploy
    # emitters get a synthesized ARN without per-callsite logic.
    if aws.datasource.mode == DatasourceMode.CREATE and aws.datasource.arn is None:
        ds_id = aws.prefixed("demo-datasource")
        derived_arn = (
            f"arn:{aws.partition}:quicksight:{aws.region}"
            f":{aws.account_id}:datasource/{ds_id}"
        )
        aws = AwsConfig(
            account_id=aws.account_id,
            region=aws.region,
            deployment_name=aws.deployment_name,
            principal_arns=aws.principal_arns,
            extra_tags=aws.extra_tags,
            tagging_enabled=aws.tagging_enabled,
            qs_disable_pg_ssl=aws.qs_disable_pg_ssl,
            pg_cluster_id=aws.pg_cluster_id,
            oracle_instance_id=aws.oracle_instance_id,
            datasource=DatasourceConfig(
                mode=DatasourceMode.CREATE, arn=derived_arn,
            ),
        )
    return Config(
        aws=aws,
        db=db,
        auth=_build_auth(raw, path),
        app2=_build_app2(raw),
        audit=_build_audit(raw),
        test=_build_test(raw),
    )


def _apply_env_overrides(raw: dict[str, Any]) -> None:
    """Apply RECON_GEN_* env var overrides to the nested raw cfg dict.

    Env-var overrides exist so the runner can inject per-cell values
    (DB URL / account / region / dialect) for the test layer chain
    without rewriting cfg.yaml per cell. Each override mutates the
    nested raw dict in place; _build_* then sees the overridden values.
    """
    from recon_gen.common.env_keys import (  # noqa: PLC0415
        RECON_GEN_APP2_DB_POOL_SIZE,
        RECON_GEN_AWS_ACCOUNT_ID,
        RECON_GEN_AWS_ORACLE_INSTANCE_ID,
        RECON_GEN_AWS_PG_CLUSTER_ID,
        RECON_GEN_AWS_REGION,
        RECON_GEN_DATASOURCE_ARN,
        RECON_GEN_DB_TABLE_PREFIX,
        RECON_GEN_DEMO_DATABASE_URL,
        RECON_GEN_DEPLOYMENT_NAME,
        RECON_GEN_DIALECT,
        RECON_GEN_PRINCIPAL_ARNS,
    )

    def _ensure_dict(key: str) -> dict[str, Any]:
        block = raw.get(key)
        if not isinstance(block, dict):
            block = {}
            raw[key] = block
        return block

    aws_block = _ensure_dict("aws")
    if (v := RECON_GEN_AWS_ACCOUNT_ID.get_or_none()) is not None:
        aws_block["account_id"] = v
    if (v := RECON_GEN_AWS_REGION.get_or_none()) is not None:
        aws_block["region"] = v
    if (v := RECON_GEN_DEPLOYMENT_NAME.get_or_none()) is not None:
        aws_block["deployment_name"] = v
    if (v := RECON_GEN_AWS_PG_CLUSTER_ID.get_or_none()) is not None:
        aws_block["pg_cluster_id"] = v
    if (v := RECON_GEN_AWS_ORACLE_INSTANCE_ID.get_or_none()) is not None:
        aws_block["oracle_instance_id"] = v
    if (v := RECON_GEN_PRINCIPAL_ARNS.get_or_none()) is not None:
        aws_block["principal_arns"] = [
            s.strip() for s in v.split(",") if s.strip()
        ]
    if (v := RECON_GEN_DATASOURCE_ARN.get_or_none()) is not None:
        ds_block = aws_block.setdefault("datasource", {})
        if isinstance(ds_block, dict):
            ds_block["mode"] = DatasourceMode.ADOPT.value
            ds_block["arn"] = v

    db_block = _ensure_dict("db")
    if (v := RECON_GEN_DIALECT.get_or_none()) is not None:
        db_block["dialect"] = v
    if (v := RECON_GEN_DEMO_DATABASE_URL.get_or_none()) is not None:
        db_block["url"] = v
    if (v := RECON_GEN_DB_TABLE_PREFIX.get_or_none()) is not None:
        db_block["table_prefix"] = v
    pool_raw = RECON_GEN_APP2_DB_POOL_SIZE.get_or_none()
    if pool_raw is not None:
        try:
            db_block["app2_pool_size"] = int(pool_raw)
        except (TypeError, ValueError) as exc:
            raise CfgError(
                f"RECON_GEN_APP2_DB_POOL_SIZE must be int; got {pool_raw!r}"
            ) from exc
