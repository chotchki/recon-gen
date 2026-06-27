"""Configuration for QuickSight resource generation.

Reads from a YAML config file or environment variables. All generated
resources reference the datasource and account specified here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Literal, cast, get_args

import yaml

from recon_gen.common.as_of_frame import LOCKED_ANCHOR, AsOfFrame
from recon_gen.common.env_keys import (
    RECON_GEN_APP2_DB_POOL_SIZE,
    RECON_GEN_AWS_ACCOUNT_ID,
    RECON_GEN_AWS_ORACLE_INSTANCE_ID,
    RECON_GEN_AWS_PG_CLUSTER_ID,
    RECON_GEN_AWS_REGION,
    RECON_GEN_DATASOURCE_ARN,
    RECON_GEN_DB_TABLE_PREFIX,
    validate_db_table_prefix,
    RECON_GEN_DEMO_DATABASE_URL,
    RECON_GEN_DEPLOYMENT_NAME,
    RECON_GEN_DIALECT,
    RECON_GEN_PRINCIPAL_ARNS,
)
from recon_gen.common.sql import Dialect

if TYPE_CHECKING:
    from recon_gen.common.models import Tag


# ---------------------------------------------------------------------------
# v14 concern-grouped Config (DE.0 lock; DE.5 phase collapsed this module
# + the prior config_v14.py into one).
# ---------------------------------------------------------------------------
#
# ``Config`` carries six nested sub-cfgs — ``aws`` / ``db`` / ``app2`` /
# ``audit`` / ``test`` / ``auth`` — each a frozen dataclass exposing the
# v14 yaml shape. The loader (``load_config`` → ``_load_nested_config``)
# parses the nested yaml directly into these dataclasses, and operators
# read e.g. ``cfg.aws.account_id`` / ``cfg.db.url`` / ``cfg.auth.aws.profile``.
#
# Methods (``partition`` / ``tags()`` / ``dataset_arn()`` / ``theme_arn()``
# / ``prefixed()``) live on the sub-cfg they conceptually belong to —
# AWS-side helpers on ``AwsConfig``, audit-signing on ``SigningConfig``,
# etc.


@dataclass(frozen=True)
class DatasourceConfig:
    """``cfg.aws.datasource`` per DE.0 lock — explicit mode enum replaces
    the implicit dispatch on ``datasource_arn`` presence."""
    mode: Literal["create", "adopt", "skip"]
    arn: str | None


@dataclass(frozen=True)
class AwsConfig:
    """``cfg.aws.*`` — AWS deploy / QS deploy / cleanup fields.
    Carries the ARN-synthesis helpers that depend solely on AWS fields
    (partition, account_id, region, deployment_name).

    DE.5 — every field has a default so partial construction
    (``AwsConfig(account_id=X)``) is legal during the strangler
    period. ``Config.__post_init__`` blends partial AwsConfigs the
    caller supplies with the legacy flat fields that are still
    backing the remaining attrs."""
    account_id: str = ""
    region: str = ""
    deployment_name: str = ""
    principal_arns: tuple[str, ...] = ()
    extra_tags: tuple[tuple[str, str], ...] = ()
    tagging_enabled: bool = True
    qs_disable_pg_ssl: bool = False
    pg_cluster_id: str | None = None
    oracle_instance_id: str | None = None
    datasource: DatasourceConfig = field(
        default_factory=lambda: DatasourceConfig(mode="create", arn=None),
    )

    @property
    def partition(self) -> str:
        """AWS partition for synthesized ARNs.

        Commercial AWS = ``aws``; GovCloud = ``aws-us-gov``;
        China = ``aws-cn``. Resolution order: ``datasource.arn`` first,
        else first ``arn:``-prefixed ``principal_arns`` entry, else
        default ``aws``. Mirrors the pre-DE ``Config.partition``
        property.
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

        Z.C: single-segment prefix ``<deployment_name>-<name>``. The
        ``deployment_name`` is the operator's per-deployment namespace,
        set explicitly in cfg.yaml (no default).
        """
        return f"{self.deployment_name}-{name}"

    def tags(self) -> "list[Tag] | None":
        """Return common + extra tags as the AWS Tag list format.

        Two tags are always emitted (when ``tagging_enabled``):

        - ``ManagedBy=recon-gen`` — gates cleanup eligibility (the
          tool-identity signal; never varies).
        - ``Deployment=<deployment_name>`` — per-deploy scope. ``json
          clean`` requires both tags to match before deleting.

        Returns ``None`` when ``tagging_enabled=False`` so the caller's
        ``Tags=cfg.aws.tags()`` field assignment goes to the dataclass's
        ``Tags: list[Tag] | None`` field as ``None`` and ``_strip_nones``
        drops it from the emitted JSON entirely. Net effect: the
        ``Create*`` boto3 call carries no ``Tags`` kwarg, so the IAM
        principal doesn't need ``quicksight:TagResource`` permission.
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
        return (
            f"arn:{self.partition}:quicksight:{self.region}"
            f":{self.account_id}:dataset/{dataset_id}"
        )

    def theme_arn(self, theme_id: str) -> str:
        return (
            f"arn:{self.partition}:quicksight:{self.region}"
            f":{self.account_id}:theme/{theme_id}"
        )


@dataclass(frozen=True)
class DbConfig:
    """``cfg.db.*`` — database connection + dialect + L2 default.
    ``url`` is the rename of pre-DE ``demo_database_url``.

    DE.5 — every field has a default so partial construction
    (``DbConfig(table_prefix=X)``) is legal during the strangler period.
    ``Config.__post_init__`` blends caller-supplied DbConfig fields
    with the remaining legacy flat fields."""
    dialect: Dialect = Dialect.POSTGRES
    url: str | None = None
    table_prefix: str = ""
    default_l2_instance: str | None = None
    app2_pool_size: int = 10


@dataclass(frozen=True)
class App2Config:
    """``cfg.app2.*`` — App2 / Studio / Dashboards server knobs.

    DE.5 step 17 — promoted to real field with defaults so partial
    construction (``App2Config(etl_hook="hook.py")``) is legal during
    the strangler period. ``Config.__post_init__`` blends caller-
    supplied App2Config fields with the remaining legacy flat fields.

    Note: ``db_pool_size`` lives at ``cfg.db.app2_pool_size`` (DE.5
    step 16) — App2Config carries only the truly App2-specific knobs.
    """
    etl_hook: str | None = None
    banner_text: str | None = None
    tls: "App2TlsConfig | None" = None


@dataclass(frozen=True)
class AuditConfig:
    """``cfg.audit.*`` — audit PDF concerns.

    DE.5 step 18 — promoted to real field with a ``signing`` slot;
    ``signing`` is None when the operator hasn't configured PDF
    auto-signing material (the default). ``SigningConfig`` itself
    is the public class (declared further down).
    """
    signing: "SigningConfig | None" = None


@dataclass(frozen=True)
class TestConfig:
    """``cfg.test.*`` — test/fuzz/synthetic-data scope.

    DE.5 step 19 — promoted to real field. ``generator`` is a
    ``TestGeneratorConfig`` (same shape + ``as_of_frame`` method
    already present); the v14 nesting is just the ``test`` field
    hop, no field-by-field copy needed.
    """
    # __test__ = False stops pytest from collecting this dataclass as a
    # test class (name starts with "Test"). It's a real config block,
    # not a test fixture.
    __test__ = False
    generator: "TestGeneratorConfig" = field(
        default_factory=lambda: TestGeneratorConfig(),
    )


@dataclass(frozen=True)
class AuthAwsConfig:
    """``cfg.auth.aws.*`` — AWS-side auth. DE.5 step 21 — promoted to
    real field with defaults so partial construction is legal."""
    profile: str | None = None
    quicksight_user_arn: str | None = None


# DE.4 — phase DC + DD cfg block carriers (OIDC + JWT session + TLS).


@dataclass(frozen=True)
class OidcConfig:
    """``auth.oidc:`` block — Phase DD's OIDC client wiring.
    ``client_secret_env`` names the env var holding the secret per
    [[feedback_no_credential_friction]]; secret never lives in cfg yaml."""
    issuer_url: str
    client_id: str
    client_secret_env: str
    redirect_uri: str
    scopes: tuple[str, ...] = ("openid", "email", "profile")


@dataclass(frozen=True)
class SessionConfig:
    """``auth.session:`` block — Phase DD's JWT session signing config."""
    jwt_secret_env: str


@dataclass(frozen=True)
class App2TlsConfig:
    """``app2.tls:`` block — Phase DC's TLS termination paths.

    ``account_email`` is the ACME registration identity (Let's Encrypt
    requires one per account); ``env`` selects which managed-DNS tuple
    the runner's ``ensure_dev_env`` reconciles (``dev`` = operator's Mac,
    ``ci`` = WSL2 CI runner). Both fields fire only when the runner
    auto-mints certs via the DC.2 coordinator; downstream consumers
    supplying their own pre-minted PEMs only need ``cert_path`` + ``key_path``.
    """
    cert_path: str
    key_path: str
    account_email: str
    env: str = "dev"

    def __post_init__(self) -> None:
        if self.env not in ("dev", "ci"):
            raise CfgError(
                f"app2.tls.env must be 'dev' or 'ci' (got {self.env!r})"
            )


@dataclass(frozen=True)
class AuthConfig:
    """Local-runner AWS auth + QS embed-signing identity.

    Combined h+i.0 spike (2026-05-08, `docs/audits/y_2_gate_h_i_combined_spike.md`):
    long-lived IAM access keys for a dedicated `recon-gen-local` user,
    referenced from `~/.aws/credentials` via a named profile. Eliminates the
    AWS-SSO-cache-miss browser flow that broke multi-hour Claude-loop sessions.
    Cfg yaml carries only the profile name; the keys themselves stay in
    `~/.aws/credentials` (out of even gitignored cfg files, standard AWS
    pattern).

    `aws_profile` — name of a profile in `~/.aws/credentials`. Runner injects
    `AWS_PROFILE=<value>` into every subprocess it spawns. None = ambient
    AWS env (env vars / default profile / SSO cache).

    `quicksight_user_arn` — explicit override for `_derive_qs_user_arn`'s
    auto-derivation. None = derive via `sts:GetCallerIdentity` + match on
    `quicksight:ListUsers`'s `PrincipalId == "federated/iam/<UserId>"`. Set
    explicitly when authed as a principal that doesn't match the desired
    QS embed user (e.g., local-root authed but want test-user; CI's per-job
    cfg with the secret value baked in).

    `oidc` / `session` — Phase DD's OIDC + JWT session blocks. Loader
    populates these when ``auth.oidc:`` / ``auth.session:`` blocks
    appear in the cfg yaml.
    """
    # DE.5 step 21 — aws_profile + quicksight_user_arn moved to aws.{profile, quicksight_user_arn}.
    aws: AuthAwsConfig = field(default_factory=AuthAwsConfig)
    oidc: OidcConfig | None = None
    session: SessionConfig | None = None


@dataclass(frozen=True)
class SigningConfig:
    """Operator-side digital-signing material for audit PDF auto-sign (U.7.b).

    When the audit `apply --execute` writes a PDF and the loaded
    config carries a ``signing:`` block, ``cli/audit`` runs it
    through pyHanko to apply a CMS signature over the entire PDF
    bytes. The system-attestation block on the sign-off page becomes
    the cryptographically-bound artifact.

    The signature is **incremental** so subsequent signers (auditor,
    second reviewer, regulator) can add their own signatures on top
    via Adobe / pyHanko / any compliant tool — the document is
    deliberately silent on how many signatures are required.

    PEM RSA key + PEM cert; ``passphrase_env`` names the env var
    holding the key passphrase if the key is encrypted (operator
    infrastructure stays out of the YAML). ``signer_name`` is the
    free-form display name shown in the signature widget; defaults
    to the cert's CN when None.
    """
    key_path: str
    cert_path: str
    passphrase_env: str | None = None
    signer_name: str | None = None

    def passphrase(self) -> bytes | None:
        """Load passphrase from ``os.environ[passphrase_env]`` lazily.

        Returns bytes for pyHanko consumption (its CMS signer takes
        bytes for the passphrase). ``None`` means the key is
        unencrypted OR the operator hasn't set the env var. pyHanko
        loads the unencrypted key when passphrase=None.
        """
        if self.passphrase_env is None:
            return None
        import os  # noqa: PLC0415 — lazy: env-touch only when audit signs
        val = os.environ.get(self.passphrase_env)  # typing-smell: ignore[envvar-bypass]: cfg-supplied env var name (audit.signing.passphrase_env) per [[feedback_no_credential_friction]]
        if not val:
            return None
        return val.encode("utf-8")


# BS.4 (2026-05-29) removed EtlDatasourceConfig + the
# Config.etl_datasource field. The legacy upstream→demo_db copy path
# (X.4.g.2's step_2_pull) is gone; etl_hook now writes directly to
# demo_db. See docs/audits/_archive/bs_4_arch_shift_spike.md.


# X.4.g.3 — Step-3 synthetic-data overlay knobs.
# X.4.i.1 added "only_template" — emit baseline restricted to a single
# TransferTemplate's leg-rails dependency closure, with the template name
# read from cfg.test_generator.only_template.
ScopeKind = Literal[
    "full", "exceptions_only", "uncovered_rails", "only_template",
]
PlantKind = Literal[
    "drift", "overdraft", "limit_breach",
    "stuck_pending", "stuck_unbundled", "supersession",
]


# X.4.g.3 — Step 3 of the deploy pipeline (synthetic data overlay) reads
# its knobs from this block. Defaults preserve byte-identical-to-locked-
# seeds output: with `etl_datasource` unset and these knobs at defaults,
# `emit_full_seed` produces today's locked seed unchanged. The cfg-level
# `seed` is the persistent baseline; `RECON_GEN_FUZZ_SEED` env or the studio
# data-shaping panel's "Roll" button (X.4.h.4) can override per-deploy.
# `only_template` and `derive_balances` are declared here but their
# pipeline modes ship later (X.4.i.1 / X.4.i.2).
@dataclass(frozen=True)
class TestGeneratorConfig:
    # Class name starts with "Test" so pytest collection emits a
    # PytestCollectionWarning by default ("cannot collect: has
    # __init__ constructor"). The convention pytest documents is the
    # __test__ = False class attribute, which suppresses collection
    # without renaming the class.
    __test__ = False

    enabled: bool = True
    scope: ScopeKind = "full"
    end_date: date | None = None
    seed: int | None = None
    plants: tuple[PlantKind, ...] = ()
    only_template: str | None = None
    derive_balances: bool = False
    # X.4.i.2 — when derive_balances=True, this controls which account
    # roles get derived. None ⇒ the conservative default of control
    # accounts only (gl_control / concentration_master / funds_pool) —
    # bank-bookkeeping accounts where the drift invariant
    # `money = SUM(amount_money)` holds by construction. DDA / external
    # account balances come from upstream statements; deriving them
    # masks reconciliation gaps the bank wants to see. Operators can
    # override per-L2 (e.g. ('gl_control', 'dda') to also derive
    # customer DDAs) for trainer scenarios that don't depend on
    # stated-vs-derived drift. Field name matches the schema column
    # ``<prefix>_transactions.account_role`` rather than the legacy
    # "account_type" wording.
    derive_balances_account_roles: tuple[str, ...] | None = None
    # X.4.h.6.fix — Studio trainer's "up_to" cutoff. When set, deploy
    # appends DELETE statements after the generator emits to truncate
    # rows past this date. Lets the trainer scrub a cutoff inside a
    # fixed scenario window: ``end_date`` (the anchor) defines plant
    # calendar positions; ``cutoff_date`` defines how far through the
    # scenario to actually emit. Studio sets this from
    # ``cache.get_up_to()`` when up_to < window_end; CLI invocations
    # leave it None (full emission). Studio-only knob — no UI for it
    # outside the trainer panel.
    cutoff_date: date | None = None

    def as_of_frame(
        self,
        *,
        window_days: int = 0,
        db_anchor: date | None = None,
    ) -> AsOfFrame:
        """Resolve this config's scenario anchor as the owned `AsOfFrame`
        (D1; see `docs/audits/date_range_model_audit.md` §5 + BD.0 spike).

        This is the call-site every `as_of` reader lands on — AQ.3 funnels
        the generator's threaded ``anchor=`` and the four ad-hoc
        ``date.today()`` fallbacks through it, and AR's views take an
        `AsOfFrame` as their anchor. Resolution paths, one shape out:

          * ``end_date == LOCKED_ANCHOR`` → ``AsOfFrame.locked()`` (the
            canonical demo anchor; locked-seed determinism — NEVER
            routed through db_anchor even if one is supplied; locked
            binding is the gate for byte-identity tests).
          * ``end_date is not None`` → explicit-anchor frame (operator
            override or trainer-pinned). Post-DK its purpose is test-
            determinism + optional operator end-of-period freeze
            (e.g. end-of-month reconciliation snapshot).
          * ``end_date is None`` + ``db_anchor is not None`` →
            data-derived frame pinned at the DB-side anchor (queried
            from ``<prefix>_data_anchor`` matview at app-build time
            per DK.4). Operator never sees the picker default to a
            wall-clock today that hasn't received its load yet.
          * ``end_date is None`` + ``db_anchor is None`` →
            ``AsOfFrame.live()`` (production ends-at-now). **DK.3
            DEPRECATION:** this branch is reachable today only because
            DK.4 hasn't yet migrated every callsite to pass
            ``db_anchor=`` from the data_anchor matview. After DK.4 it
            should be unreachable in prod (dashboards / audit CLI) and
            the live(wall-clock) fallback removed entirely. Tests
            still set ``end_date`` directly (or via
            ``RECON_GEN_AS_OF_ANCHOR``) for determinism so this branch
            never fires there either.

        ``window_days`` is an ergonomic shortcut: 0 means a single-day
        frame, N>0 means an N-day window ending at the anchor. BD.1
        replaced the v1 ``window_days: int`` FIELD on AsOfFrame with a
        typed ``window: DateInterval`` field; this kwarg stays at the
        construction seam (construction-time ergonomics ≠ runtime
        escape hatch).
        """
        from recon_gen.common.intervals import DateInterval
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
        # DK.3 — final fallback marked for removal post-DK.4. Every
        # dashboard / audit callsite should plumb ``db_anchor=`` from
        # the ``<prefix>_data_anchor`` matview by the end of DK.4; this
        # branch becomes unreachable in prod. Until then, ``live()``
        # honours ``RECON_GEN_AS_OF_ANCHOR`` for chain-wide test
        # determinism, but a prod hit here is a footgun (renders blank
        # dashboards on stale feeds — the entire problem DK is solving).
        return AsOfFrame.live(window_days=window_days)


def _partition_from_arns(
    datasource_arn: str | None, principal_arns: list[str],
) -> str:
    """Resolve AWS partition string from any available ARN.
    See ``AwsConfig.partition`` for the resolution rationale."""
    for source in (datasource_arn, *principal_arns):
        if source and source.startswith("arn:"):
            parts = source.split(":", 2)
            if len(parts) >= 2 and parts[1]:
                return parts[1]
    return "aws"


# ---------------------------------------------------------------------------
# DE.5 — v14 nested-yaml loader. Errors / legacy-key map / build helpers
# + ``load_config`` reading the concern-grouped shape.
# ---------------------------------------------------------------------------


class CfgError(ValueError):
    """Base for v14 cfg errors. Operator-facing message format:
    short cause + actionable next step.

    Inherits from ValueError so cfg-load failures surface uniformly:
    callers that catch ValueError (legacy CLI / runner / e2e harness)
    continue to handle them, while tests asserting on specific cfg-error
    subclasses (CycleError / LegacyFieldError / MissingFieldError) stay
    precise."""


class CycleError(CfgError):
    """``extends:`` chain references a cfg already in the resolution set.
    Carries the cycle path for operator triage."""


class LegacyFieldError(CfgError):
    """A v13-shape key appeared in v14 cfg.yaml. Carries the migration
    hint with the new path."""


class MissingFieldError(CfgError):
    """Required field absent after merge + derivation. Carries the field
    path + the cfg files that contributed to the merged result."""


# Legacy → new field-path migration map (used by ``_check_legacy_keys``).
# Drives the LegacyFieldError messages so each v13 key the operator left
# in the cfg points at the new location.
_LEGACY_TO_NEW: dict[str, str] = {
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
    "auth.aws_profile": "auth.aws.profile",
    "auth.quicksight_user_arn": "auth.aws.quicksight_user_arn",
}


def _deep_merge(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:  # typing-smell: ignore[explicit-any]: heterogeneous YAML payload — yaml.safe_load returns scalar/list/dict/null per node
    """Child-wins deep merge. Dicts merge recursively; lists + scalars:
    child replaces parent (no append). Lists wanting append semantics use
    explicit ``[{{ inherited }}, new]`` in child."""
    merged: dict[str, Any] = dict(parent)  # typing-smell: ignore[explicit-any]: heterogeneous YAML payload
    for key, child_value in child.items():
        parent_value = merged.get(key)
        if isinstance(parent_value, dict) and isinstance(child_value, dict):
            merged[key] = _deep_merge(
                cast(dict[str, Any], parent_value),
                cast(dict[str, Any], child_value),
            )
        else:
            merged[key] = child_value
    return merged


def _load_raw_nested(
    path: Path, _seen: set[Path] | None = None,
) -> dict[str, Any]:  # typing-smell: ignore[explicit-any]: heterogeneous YAML payload
    """Recursively load YAML + apply ``extends:`` chain. Returns the
    merged raw dict (pre-typed)."""
    _seen = _seen if _seen is not None else set()
    abs_path = path.resolve()
    if abs_path in _seen:
        cycle = " → ".join(str(p) for p in _seen) + f" → {abs_path}"
        raise CycleError(f"extends: cycle detected: {cycle}")
    _seen.add(abs_path)
    if not path.exists():
        raise CfgError(
            f"cfg path does not exist: {abs_path}. Pass an existing yaml or "
            f"check the parent's extends: entry that points here."
        )
    raw_any: object = yaml.safe_load(path.read_text())
    raw: dict[str, Any] = cast(dict[str, Any], raw_any) if isinstance(raw_any, dict) else {}  # typing-smell: ignore[explicit-any]: heterogeneous YAML payload
    extends = raw.pop("extends", None)
    if extends is None:
        return raw
    if isinstance(extends, str):
        extends = [extends]
    if not isinstance(extends, list):
        raise CfgError(
            f"extends: must be a list of paths (or a single string), "
            f"got {type(extends).__name__} in {abs_path}"
        )
    extends_list = cast(list[Any], extends)  # typing-smell: ignore[explicit-any]: yaml-loaded list of arbitrary nodes
    merged: dict[str, Any] = {}  # typing-smell: ignore[explicit-any]: heterogeneous YAML payload
    for ext_path_str in extends_list:
        if not isinstance(ext_path_str, str):
            raise CfgError(
                f"extends: entries must be strings, got "
                f"{type(ext_path_str).__name__} in {abs_path}"
            )
        ext_path = (path.parent / ext_path_str).resolve()
        if not ext_path.exists():
            # Loud failure on missing parent. The runner used to silently
            # dispatch-skip when this happened (because a downstream
            # ``yaml.safe_load`` raised an opaque FileNotFoundError that
            # got caught by the layer-launch wrapper); operator got the
            # "deploy was skipped" symptom with no actionable trace back
            # to the broken extends:. Surface the absolute resolved path
            # + the source file's ``extends:`` entry that pointed here so
            # ``EXIT_NEEDS_OPERATOR=2`` carries a fixable message.
            raise CfgError(
                f"extends: target {ext_path_str!r} (resolved to {ext_path}) "
                f"does not exist; referenced from {abs_path}. Check the "
                f"parent path is correct, the file is committed/staged, "
                f"and the cwd matches what the cfg was authored against."
            )
        ext_raw = _load_raw_nested(ext_path, _seen=set(_seen))
        merged = _deep_merge(merged, ext_raw)
    return _deep_merge(merged, raw)


def _check_legacy_keys_nested(raw: dict[str, Any], path: Path) -> None:  # typing-smell: ignore[explicit-any]: heterogeneous YAML payload
    """Raise LegacyFieldError if any v13-shape key appears at top level
    or under the nested auth block."""
    for legacy, new in _LEGACY_TO_NEW.items():
        if "." in legacy:
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


def _resolve_dialect_nested(value: Any) -> Dialect:  # typing-smell: ignore[explicit-any]: yaml-loaded scalar — narrows via isinstance in the body
    if isinstance(value, Dialect):
        return value
    if isinstance(value, str):
        return Dialect(value)
    raise CfgError(
        f"db.dialect must be a string or Dialect, got {type(value).__name__}"
    )


def _build_aws_nested(raw: dict[str, Any], path: Path) -> AwsConfig:  # typing-smell: ignore[explicit-any]: heterogeneous YAML payload
    block_raw = raw.get("aws")
    if not isinstance(block_raw, dict):
        # DV.5 — the aws: block is OPTIONAL. A cfg with no AWS keys is valid
        # for every non-QuickSight surface (dashboards / studio / audit /
        # schema / data / docs / json emit). The QS deploy / clean / probe
        # verbs validate aws.account_id / region / deployment_name at
        # invocation (cli/json.py::_require_quicksight) and fail there with a
        # set-the-aws-cfg hint.
        return AwsConfig()
    block = cast(dict[str, Any], block_raw)
    for key in ("account_id", "region", "deployment_name"):
        if key not in block:
            raise MissingFieldError(
                f"{path}: required field 'aws.{key}' is absent"
            )
    ds_block_raw = block.get("datasource", {})
    if not isinstance(ds_block_raw, dict):
        raise CfgError(f"{path}: 'aws.datasource' must be a mapping")
    ds_block = cast(dict[str, Any], ds_block_raw)
    ds_mode_val = ds_block.get("mode", "create")
    if ds_mode_val not in ("create", "adopt", "skip"):
        raise CfgError(
            f"{path}: aws.datasource.mode must be one of "
            f"['create', 'adopt', 'skip'], got {ds_mode_val!r}"
        )
    ds_arn = ds_block.get("arn")
    if ds_mode_val == "adopt" and not ds_arn:
        raise MissingFieldError(
            f"{path}: aws.datasource.mode='adopt' requires aws.datasource.arn"
        )
    extra_tags_raw = block.get("extra_tags", {})
    if isinstance(extra_tags_raw, dict):
        tags_typed = cast(dict[Any, Any], extra_tags_raw)
        tags_tuple = tuple(sorted(
            (str(k), str(v)) for k, v in tags_typed.items()
        ))
    else:
        raise CfgError(f"{path}: aws.extra_tags must be a mapping")
    principals_raw = block.get("principal_arns", [])
    if not isinstance(principals_raw, list):
        raise CfgError(f"{path}: aws.principal_arns must be a list")
    principals_typed = cast(list[Any], principals_raw)
    return AwsConfig(
        account_id=str(block["account_id"]),
        region=str(block["region"]),
        deployment_name=str(block["deployment_name"]),
        principal_arns=tuple(str(p) for p in principals_typed),
        extra_tags=tags_tuple,
        tagging_enabled=bool(block.get("tagging_enabled", True)),
        qs_disable_pg_ssl=bool(block.get("qs_disable_pg_ssl", False)),
        pg_cluster_id=block.get("pg_cluster_id"),
        oracle_instance_id=block.get("oracle_instance_id"),
        datasource=DatasourceConfig(
            mode=ds_mode_val,
            arn=ds_arn,
        ),
    )


def _build_db_nested(
    raw: dict[str, Any], aws: AwsConfig, path: Path,  # typing-smell: ignore[explicit-any]: heterogeneous YAML payload
) -> DbConfig:
    block_raw = raw.get("db")
    if not isinstance(block_raw, dict):
        raise MissingFieldError(f"{path}: required block 'db:' is absent")
    block = cast(dict[str, Any], block_raw)
    # ``dialect`` is required (drives SQL emission across the codebase);
    # ``url`` is optional (tests that only exercise JSON emission don't
    # need a DB binding; the operator-side flat-yaml shape made it
    # optional too).
    if "dialect" not in block:
        raise MissingFieldError(
            f"{path}: required field 'db.dialect' is absent"
        )
    table_prefix = block.get("table_prefix")
    if not table_prefix:
        # Derive from aws.deployment_name (`-` → `_`)
        table_prefix = aws.deployment_name.replace("-", "_")
    validate_db_table_prefix(str(table_prefix))
    url_raw = block.get("url")
    return DbConfig(
        dialect=_resolve_dialect_nested(block["dialect"]),
        url=str(url_raw) if url_raw is not None else None,
        table_prefix=str(table_prefix),
        default_l2_instance=block.get("default_l2_instance"),
        app2_pool_size=int(block.get("app2_pool_size", 10)),
    )


def _build_auth_nested(raw: dict[str, Any], path: Path) -> "AuthConfig":  # typing-smell: ignore[explicit-any]: heterogeneous YAML payload
    del path
    block_raw = raw.get("auth", {})
    if not isinstance(block_raw, dict):
        raise CfgError("auth must be a mapping when present")
    block = cast(dict[str, Any], block_raw)
    _allowed_auth = {"aws", "oidc", "session"}
    unknown = sorted(set(block) - _allowed_auth)
    if unknown:
        raise CfgError(
            f"auth block contains unknown keys: {unknown}. "
            f"Allowed: {sorted(_allowed_auth)}."
        )
    aws_block_raw = block.get("aws", {})
    if not isinstance(aws_block_raw, dict):
        raise CfgError("auth.aws must be a mapping")
    aws_block = cast(dict[str, Any], aws_block_raw)
    aws_auth = AuthAwsConfig(
        profile=aws_block.get("profile"),
        quicksight_user_arn=aws_block.get("quicksight_user_arn"),
    )
    oidc_block_raw = block.get("oidc")
    oidc: OidcConfig | None = None
    if isinstance(oidc_block_raw, dict):
        oidc_block = cast(dict[str, Any], oidc_block_raw)
        for key in ("issuer_url", "client_id", "client_secret_env", "redirect_uri"):
            if key not in oidc_block:
                raise MissingFieldError(
                    f"auth.oidc.{key} is required when auth.oidc block present"
                )
        scopes_raw = oidc_block.get("scopes", ["openid", "email", "profile"])
        if not isinstance(scopes_raw, (list, tuple)):
            raise CfgError(
                f"auth.oidc.scopes must be a list of strings; "
                f"got {type(scopes_raw).__name__}"
            )
        scopes_typed = cast(list[Any] | tuple[Any, ...], scopes_raw)
        oidc = OidcConfig(
            issuer_url=str(oidc_block["issuer_url"]),
            client_id=str(oidc_block["client_id"]),
            client_secret_env=str(oidc_block["client_secret_env"]),
            redirect_uri=str(oidc_block["redirect_uri"]),
            scopes=tuple(str(s) for s in scopes_typed),
        )
    session_block_raw = block.get("session")
    session: SessionConfig | None = None
    if isinstance(session_block_raw, dict):
        session_block = cast(dict[str, Any], session_block_raw)
        if "jwt_secret_env" not in session_block:
            raise MissingFieldError(
                "auth.session.jwt_secret_env is required when "
                "auth.session block present"
            )
        session = SessionConfig(
            jwt_secret_env=str(session_block["jwt_secret_env"]),
        )
    return AuthConfig(aws=aws_auth, oidc=oidc, session=session)


def _build_app2_nested(raw: dict[str, Any]) -> App2Config:  # typing-smell: ignore[explicit-any]: heterogeneous YAML payload
    block_raw = raw.get("app2", {})
    if not isinstance(block_raw, dict):
        raise CfgError("app2 must be a mapping when present")
    block = cast(dict[str, Any], block_raw)
    _allowed_app2 = {"tls", "etl_hook", "banner_text"}
    unknown = sorted(set(block) - _allowed_app2)
    if unknown:
        raise CfgError(
            f"app2 block contains unknown keys: {unknown}. "
            f"Allowed: {sorted(_allowed_app2)}."
        )
    tls_block_raw = block.get("tls")
    tls: App2TlsConfig | None = None
    if isinstance(tls_block_raw, dict):
        tls_block = cast(dict[str, Any], tls_block_raw)
        for key in ("cert_path", "key_path", "account_email"):
            if key not in tls_block:
                raise MissingFieldError(
                    f"app2.tls.{key} is required when app2.tls block present"
                )
        tls = App2TlsConfig(
            cert_path=str(tls_block["cert_path"]),
            key_path=str(tls_block["key_path"]),
            account_email=str(tls_block["account_email"]),
            env=str(tls_block.get("env", "dev")),
        )
    return App2Config(
        etl_hook=block.get("etl_hook"),
        banner_text=block.get("banner_text"),
        tls=tls,
    )


def _build_audit_nested(raw: dict[str, Any]) -> AuditConfig:  # typing-smell: ignore[explicit-any]: heterogeneous YAML payload
    block_raw = raw.get("audit", {})
    if not isinstance(block_raw, dict):
        raise CfgError("audit must be a mapping when present")
    block = cast(dict[str, Any], block_raw)
    signing_block_raw = block.get("signing")
    signing: SigningConfig | None = None
    if isinstance(signing_block_raw, dict):
        signing_block = cast(dict[str, Any], signing_block_raw)
        for key in ("key_path", "cert_path"):
            if key not in signing_block:
                raise MissingFieldError(
                    f"audit.signing.{key} is required when "
                    f"audit.signing block present"
                )
        signing = SigningConfig(
            key_path=str(signing_block["key_path"]),
            cert_path=str(signing_block["cert_path"]),
            passphrase_env=signing_block.get("passphrase_env"),
            signer_name=signing_block.get("signer_name"),
        )
    return AuditConfig(signing=signing)


def _build_test_nested(raw: dict[str, Any]) -> "TestConfig":  # typing-smell: ignore[explicit-any]: heterogeneous YAML payload
    block_raw = raw.get("test", {})
    if not isinstance(block_raw, dict):
        raise CfgError("test must be a mapping when present")
    block = cast(dict[str, Any], block_raw)
    gen_block_raw = block.get("generator", {})
    if not isinstance(gen_block_raw, dict):
        raise CfgError("test.generator must be a mapping when present")
    gen_block = cast(dict[str, Any], gen_block_raw)
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
            f"test.generator.plants must be a list; "
            f"got {type(plants_raw).__name__}"
        )
    plants_typed = cast(list[Any] | tuple[Any, ...], plants_raw)
    plants_tuple = tuple(str(p) for p in plants_typed)
    drb_raw = gen_block.get("derive_balances_account_roles")
    drb_tuple: tuple[str, ...] | None = None
    if drb_raw is not None:
        if not isinstance(drb_raw, (list, tuple)):
            raise CfgError(
                f"test.generator.derive_balances_account_roles must be a "
                f"list / null; got {type(drb_raw).__name__}"
            )
        drb_typed = cast(list[Any] | tuple[Any, ...], drb_raw)
        drb_tuple = tuple(str(r) for r in drb_typed)
    scope_val = str(gen_block.get("scope", "full"))
    if scope_val not in get_args(ScopeKind):
        raise CfgError(
            f"test.generator.scope must be one of "
            f"{list(get_args(ScopeKind))}; got {scope_val!r}"
        )
    return TestConfig(
        generator=TestGeneratorConfig(
            enabled=bool(gen_block.get("enabled", True)),
            scope=cast(ScopeKind, scope_val),
            end_date=end_date_val,
            seed=gen_block.get("seed"),
            plants=cast(tuple[PlantKind, ...], plants_tuple),
            only_template=gen_block.get("only_template"),
            derive_balances=bool(gen_block.get("derive_balances", False)),
            derive_balances_account_roles=drb_tuple,
            cutoff_date=cutoff_date_val,
        ),
    )


_QS_USER_ARN_CACHE: dict[tuple[str, str, str], str | None] = {}


def resolve_qs_user_arn(cfg: "Config") -> str | None:
    """Lazy resolve the QuickSight user ARN for e2e tests.

    Priority:
    1. ``cfg.auth.aws.quicksight_user_arn`` (explicit override).
    2. Derive from ``cfg.auth.aws.profile`` + ``cfg.aws.account_id`` +
       ``cfg.aws.region`` via ``quicksight.list_users(Namespace='default')``;
       first ADMIN user's ARN (falls back to first user).
    3. None (caller's qs_browser layer is skipped).

    Cached per ``(profile, account_id, region)`` so per-cell subprocesses
    share lookups. Boto failure → None + stderr breadcrumb.
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
    import sys  # noqa: PLC0415
    try:
        import boto3  # noqa: PLC0415 — lazy: only on the derive path
        session = boto3.Session(profile_name=profile, region_name=region)
        qs: Any = session.client("quicksight")  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]: boto3-stubs overload union confuses pyright  # typing-smell: ignore[explicit-any]: boto3-stubs overload union — wrap to Any per X.2.o.5 pattern
        users = qs.list_users(
            AwsAccountId=account_id, Namespace="default",
        ).get("UserList", [])
    except Exception as exc:  # noqa: BLE001
        print(
            f"config: derive QS user ARN failed via aws_profile="
            f"{profile!r} ({type(exc).__name__}: {exc}); qs_browser will skip",
            file=sys.stderr,
        )
        _QS_USER_ARN_CACHE[cache_key] = None
        return None
    if not users:
        print(
            f"config: derive QS user ARN found 0 users in "
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


def _apply_env_overrides_nested(raw: dict[str, Any]) -> None:  # typing-smell: ignore[explicit-any]: heterogeneous YAML payload
    """Apply RECON_GEN_* env var overrides to the nested raw cfg dict.

    Mutates the nested dict in place so ``_build_*`` see overrides via
    the same code path as the yaml values. Runner uses this to inject
    per-cell DB URL / account / region / dialect without rewriting cfg
    yaml per cell.
    """
    def _ensure_dict(key: str) -> dict[str, Any]:  # typing-smell: ignore[explicit-any]: heterogeneous YAML payload
        block = raw.get(key)
        if not isinstance(block, dict):
            block = {}
            raw[key] = block
        return cast(dict[str, Any], block)

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
        ds_block_raw = aws_block.setdefault("datasource", {})
        if isinstance(ds_block_raw, dict):
            ds_block = cast(dict[str, Any], ds_block_raw)
            ds_block["mode"] = "adopt"
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


def _load_nested_config(path: Path) -> "Config":
    """v14 nested-yaml loader. Path resolved by caller (``load_config``)."""
    raw = _load_raw_nested(path)
    _check_legacy_keys_nested(raw, path)
    _apply_env_overrides_nested(raw)
    aws = _build_aws_nested(raw, path)
    db = _build_db_nested(raw, aws, path)
    # Auto-derive datasource.arn when mode=create + arn is None. Mirrors
    # the pre-DE Config.__post_init__ behavior so deploy emitters get a
    # synthesized ARN without per-callsite logic.
    if aws.datasource.mode == "create" and aws.datasource.arn is None:
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
            datasource=DatasourceConfig(mode="create", arn=derived_arn),
        )
    return Config(
        aws=aws,
        db=db,
        auth=_build_auth_nested(raw, path),
        app2=_build_app2_nested(raw),
        audit=_build_audit_nested(raw),
        test=_build_test_nested(raw),
    )


@dataclass
class Config:
    # DE.5 steps 3-5 — ``aws_account_id`` + ``aws_region`` + ``deployment_name``
    # flat fields dropped. Callers pass ``aws=AwsConfig(account_id=..., region=...,
    # deployment_name=...)``; loader + make_test_config translate.
    # Z.C — Per-deploy QS namespace. Replaces v8.x's ``resource_prefix``
    # (defaulted ``qs-gen``) + ``l2_instance_prefix`` (stamped from the
    # L2 yaml's ``instance:`` field) — those were the same concept,
    # historically split because ``resource_prefix`` started life as a
    # hardcoded tool-signature. Tool identity now lives solely in the
    # ``ManagedBy=recon-gen`` tag (cleanup gate). ``deployment_name``
    # is the SINGLE QS resource-ID prefix: ``cfg.prefixed("foo")`` →
    # ``<deployment_name>-foo``, also surfaces as the ``Deployment``
    # cleanup tag value. Required (loud-fail when unset) — same pattern
    # as ``aws_account_id``. Multiple deployments of the same L2
    # (dev/staging/prod) live as multiple cfg.yaml files with distinct
    # ``deployment_name`` values pointing at the same L2 yaml. Operator
    # may encode multiple identity axes (CI run id, scenario, dialect)
    # into the value — that's fine; the cleanup gate is exact-match.
    # DE.5 step 5 — moved to aws.deployment_name.
    # Z.C — Per-deploy DB table-name prefix. Replaces direct reads of
    # ``L2Instance.instance`` in ``common/l2/schema.py`` /
    # ``common/l2/seed.py`` / ``apps/*/datasets.py``. Used in
    # ``f"{db_table_prefix}_transactions"`` etc. KEPT SEPARATE from
    # ``deployment_name`` because DB tables don't take hyphens cleanly
    # (esp. Oracle), have a 30-char limit, and integrators may have
    # established pre-existing table-prefix conventions distinct from
    # their QS naming. Required (loud-fail when unset). An advanced
    # user MAY set this equal to ``deployment_name`` (lower-case +
    # hyphens-to-underscores).
    # DE.5 step 6 — datasource_arn + datasource_arn_was_derived dropped.
    # Use ``aws=AwsConfig(datasource=DatasourceConfig(mode=..., arn=...))``;
    # ``cli/json.py`` keys "we own it" off ``cfg.aws.datasource.mode == "create"``
    # instead of the removed ``cfg.datasource_arn_was_derived`` sentinel.
    # DE.5 step 7 — principal_arns moved to aws.principal_arns.
    # DE.5 steps 8-11 — extra_tags + tagging_enabled + qs_disable_pg_ssl +
    # aws_pg_cluster_id + aws_oracle_instance_id all moved into aws.*.
    # DE.5 steps 12-13 — db_table_prefix + demo_database_url moved to db.*.
    # DE.5 step 14 — dialect moved to db.dialect.
    # DE.5 step 18 — signing moved to audit.signing.
    # Y.2.gate.h+i.0 — Local-runner AWS auth + QS embed-signing identity.
    # When set, the test-layer-chain runner injects ``AWS_PROFILE`` into
    # subprocess envs (per ``cfg.auth.aws_profile``) and auto-derives
    # ``RECON_E2E_USER_ARN`` from STS+ListUsers (or uses
    # ``cfg.auth.quicksight_user_arn`` when explicitly set). Absent =
    # operator manages auth via ambient env vars (legacy behavior; CI
    # also uses ambient via OIDC). See combined spike for the full
    # decision + IAM runbook.
    # DE.2 commit A — auth is now ALWAYS present (default-factory
    # empty AuthConfig). Legacy ``auth: AuthConfig | None = None`` required
    # callers to None-check before reading; v14 nested shape
    # (``cfg.auth.aws.profile``) needs ``cfg.auth`` to always exist.
    # The four legacy callsites that did ``cfg.auth is not None AND
    # cfg.auth.aws_profile is not None`` reduce cleanly to
    # ``cfg.auth.aws.profile is not None`` post-sweep.
    auth: AuthConfig = field(default_factory=AuthConfig)
    # DE.5 step 3 — ``aws`` is now ``init=True``. Callers pass
    # ``aws=AwsConfig(account_id="...")``; ``__post_init__`` blends in
    # the remaining flat fields (aws_region / deployment_name / etc.)
    # until those flats get dropped in subsequent steps. When the
    # caller doesn't pass ``aws=`` explicitly, the default empty
    # AwsConfig fires + __post_init__ raises (account_id="" is the
    # "not provided" sentinel).
    aws: AwsConfig = field(default_factory=AwsConfig)
    # DE.5 step 12 — ``db`` is the real DbConfig field. Same strangler
    # shape: callers may pass db=DbConfig(...) directly OR continue to
    # pass legacy flat kwargs (db_table_prefix / demo_database_url /
    # dialect / etc.). __post_init__ blends.
    db: DbConfig = field(default_factory=DbConfig)
    # DE.5 step 17 — ``app2`` is the real App2Config field. Strangler
    # period accepts both bare flat kwargs and ``app2=App2Config(...)``.
    app2: App2Config = field(default_factory=App2Config)
    # DE.5 step 18 — ``audit`` is the real AuditConfig field; carries
    # ``signing: SigningConfig | None`` (None = ship PDF unsigned).
    audit: AuditConfig = field(default_factory=AuditConfig)
    # DE.5 step 6 — ``datasource_arn_was_derived`` sentinel removed.
    # Use ``cfg.aws.datasource.mode == "create"`` (the "we own it" case
    # post-DE.0 lock 3).
    # Y.2.gate.h.6 — Path to the L2 institution YAML the operator's external
    # DB has been seeded with. Runner injects ``RECON_GEN_TEST_L2_INSTANCE=<path>``
    # into subprocess env_overrides so both the seed flow (passes ``--l2 <yaml>``
    # to schema/data CLI subcommands) and the dataset-SQL smoke test (reads
    # the env var to pick which L2's datasets to parametrize) align with the
    # operator's actual DB state. Same shape as ``cfg.auth.aws_profile``:
    # operator declares once in cfg, the runner threads it through. None =
    # subprocesses fall back to ``default_l2_instance()`` (= bundled
    # spec_example fixture); fine for greenfield local containers (local-pg
    # / local-oracle / local-sqlite) but mismatches the operator's external
    # Aurora when they've seeded a different L2 (e.g., sasquatch_pr).
    # Relative paths resolve from the repo root.
    # DE.5 step 15 — moved to db.default_l2_instance.
    # v8.6.11 — When True (default), every Create* boto3 call passes
    # ``Tags=[ManagedBy, ResourcePrefix, L2Instance, *extra_tags]`` so
    # ``json clean`` can fail-CLOSED scope deletion to ourselves. Set
    # False ONLY when the IAM principal lacks ``quicksight:TagResource``
    # / ``UntagResource`` permissions (e.g. an enterprise environment
    # where another system applies governance tags). With tagging off
    # ``json clean`` falls back to ID-prefix matching against
    # ``resource_prefix`` — significantly weaker isolation. See the
    # docs reference for the loss-of-safety details before opting in.
    # DE.5 step 9 — moved to aws.tagging_enabled.
    # DE.5 step 20 — studio_enabled flat field dropped.
    # The CLI mounts Studio unconditionally — production deployments use
    # `recon-gen dashboards` (which never mounts Studio) for the
    # dashboards-only surface. No cfg knob needed.
    # X.2.n.6 — Max concurrent DB connections in the App2 server's
    # async pool (``common/db.py::make_connection_pool``). Default 10
    # is sized for "one user opening a sheet with ~10 visuals" or
    # "10 users with single-visual refreshes" — enough for typical
    # demo + dev loads. Tune up for high-fan-in dashboards or
    # multi-tenant production.
    #
    # Relationship math, with async drivers (X.2.n.3+):
    #   max concurrent SQL ops == app2_db_pool_size
    # The asyncio loop stays free between SQL awaits, so threadpool
    # pressure is no longer a factor. Pool size IS the bottleneck —
    # set it ≤ ``PG max_connections - reserved_connections`` (PG's
    # default 100 minus 3 superuser slots = ~97 budget). Oracle's
    # connection cost is higher; integrators rarely run pools >25.
    # DE.5 step 16 — moved to db.app2_pool_size.
    # DE.5 step 17 — app2_tls moved to app2.tls.
    # Y.2.gate.l — RDS identifiers for the start/stop lifecycle.
    # `./run_tests.sh up aws` / `down aws` / `status` read these to
    # know which Aurora cluster + Oracle instance to act on. Local
    # operator's cfg points at the dev clusters (e.g. database-2 /
    # database-3); CI's per-job env injects the CI-side identifiers
    # (`recon-ci-aurora` / `recon-ci-oracle`) so the two lifecycles
    # don't step on each other (per gate.l.0 provisioning runbook).
    # DE.5 steps 10-11 — aws_pg_cluster_id / aws_oracle_instance_id /
    # qs_disable_pg_ssl all moved to aws.* (pg_cluster_id /
    # oracle_instance_id / qs_disable_pg_ssl).
    # X.4.g.1 — Optional shell command run as step 1 of the deploy
    # pipeline, BEFORE step 2 wipes the demo DB. Non-zero exit halts
    # the pipeline (the demo DB is never touched). When unset, step 1
    # is a no-op. Parsed via `shlex.split`, run with `shell=False`;
    # stdout/stderr stream to `/dev_log` (X.4.g.4 wires the runner).
    #
    # AO.1 — Money contract: the hook receives upstream rows in
    # DOLLARS and MUST convert to integer cents before INSERTing into
    # the prefixed base tables. The three money columns
    # (``<prefix>_transactions.amount_money``,
    # ``<prefix>_daily_balances.money``,
    # ``<prefix>_daily_balances.expected_eod_balance``) are BIGINT
    # integer cents on every dialect. Python ETL implementations
    # should reach for ``recon_gen.common.money.Cents`` rather than a
    # hand-rolled ``int(round(x * 100))`` — the helper rejects
    # float-init Decimals that re-introduce float dust.
    #
    # Example wrapper command (``etl_hook: ./bin/my_etl.py``) where
    # ``my_etl.py`` reads dollar amounts from upstream + writes cents::
    #
    #     from decimal import Decimal
    #     from recon_gen.common.money import Cents
    #     amount_cents = Cents.from_dollars(Decimal("75.00")).value
    #     cur.execute(
    #         "INSERT INTO myprefix_transactions (..., amount_money, ...) "
    #         "VALUES (..., %s, ...)",
    #         (..., amount_cents, ...),
    #     )
    #
    # See ``src/recon_gen/docs/Schema_v6.md`` for the full column
    # contract and ``recon-gen data etl-example`` for canonical
    # per-table INSERT patterns.
    # DE.5 step 17 — etl_hook + banner_text moved to app2.*.
    # DE.5 step 19 — test_generator moved to test.generator (TestConfig).
    test: TestConfig = field(default_factory=TestConfig)

    # -------------------------------------------------------------------
    # DE.2 commit A — v14 proxy properties. Read-only views over the
    # legacy flat fields exposing the nested ``aws / db / app2 / audit /
    # test / auth`` shape locked in DE.0. Sweep callsites:
    # ``cfg.<flat>`` → ``cfg.<nested>.<flat>``. DE.5 drops the flat
    # fields when 100% swept. Per-access cost is one frozen dataclass
    # ctor; trivial vs network / DB / pyright time.
    # -------------------------------------------------------------------

    # DE.5 — ``aws`` is now a real field (declared above), populated
    # by __post_init__ from the legacy flat fields. The @property form
    # rebuilt the AwsConfig every access; the field caches it.

    # DE.5 — ``cfg.db`` is now a real ``DbConfig`` field (declared below
    # alongside ``aws``). Populated by __post_init__ from the legacy
    # flats during the strangler; future steps drop those flats.

    # DE.5 step 17 — ``cfg.app2`` is now a real ``App2Config`` field.
    # DE.5 step 18 — ``cfg.audit`` is now a real ``AuditConfig`` field.
    # DE.5 step 19 — ``cfg.test`` is now a real ``TestConfig`` field.

    def __post_init__(self) -> None:
        # DE.5 steps 3+4 — account_id + region come from caller-supplied
        # aws=AwsConfig(account_id=..., region=...).
        if not self.aws.account_id:
            raise ValueError(
                "Config requires aws=AwsConfig(account_id=...). The legacy "
                "``aws_account_id`` flat kwarg was dropped in DE.5 step 3."
            )
        if not self.aws.region:
            raise ValueError(
                "Config requires aws=AwsConfig(region=...). The legacy "
                "``aws_region`` flat kwarg was dropped in DE.5 step 4."
            )
        if not self.aws.deployment_name:
            raise ValueError(
                "Config requires aws=AwsConfig(deployment_name=...). The legacy "
                "``deployment_name`` flat kwarg was dropped in DE.5 step 5."
            )
        account_id = self.aws.account_id
        region = self.aws.region
        deployment_name = self.aws.deployment_name
        # DE.5 step 6 — derive datasource arn into self.aws.datasource
        # when caller-supplied AwsConfig left arn=None + demo_database_url
        # is set. mode=create when we own it; mode=adopt when operator
        # provided the arn explicitly.
        ds_arn = self.aws.datasource.arn
        ds_mode = self.aws.datasource.mode
        # DE.5 step 7 — principal_arns now from caller-supplied aws.principal_arns.
        principal_arns_list = list(self.aws.principal_arns)
        # DE.5.config_v14_consolidation — auto-derive only when mode=create;
        # mode=skip means "don't touch the QS datasource API at all" so an arn
        # is not required + clobbering to mode=create would defeat the escape.
        # mode=adopt requires the operator to supply the arn explicitly (the
        # loader's _build_aws_nested raises if absent).
        if (
            ds_mode == "create"
            and ds_arn is None
            and self.db.url is not None
        ):
            ds_id = f"{deployment_name}-demo-datasource"
            partition = _partition_from_arns(ds_arn, principal_arns_list)
            ds_arn = (
                f"arn:{partition}:quicksight:{region}"
                f":{account_id}:datasource/{ds_id}"
            )
            ds_mode = "create"
        if ds_arn is None and ds_mode != "skip":
            raise ValueError(
                "aws.datasource.arn is required unless demo_database_url is "
                "set OR aws.datasource.mode='skip'."
            )
        # DE.5 — blend caller-supplied ``aws`` fields with remaining flats.
        # DE.5 steps 8-11 — extra_tags / tagging_enabled / qs_disable_pg_ssl /
        # pg_cluster_id / oracle_instance_id all from caller-supplied aws.*.
        self.aws = AwsConfig(
            account_id=account_id,
            region=region,
            deployment_name=deployment_name,
            principal_arns=tuple(principal_arns_list),
            extra_tags=self.aws.extra_tags,
            tagging_enabled=self.aws.tagging_enabled,
            qs_disable_pg_ssl=self.aws.qs_disable_pg_ssl,
            pg_cluster_id=self.aws.pg_cluster_id,
            oracle_instance_id=self.aws.oracle_instance_id,
            datasource=DatasourceConfig(mode=ds_mode, arn=ds_arn),
        )
        # DE.5 steps 12-16 — DB-block flats dropped. cfg.db is now
        # caller-supplied (loader or test helper); table_prefix is
        # required (loud-fail when empty).
        if not self.db.table_prefix:
            raise ValueError(
                "Config requires db=DbConfig(table_prefix=...). The legacy "
                "``db_table_prefix`` flat kwarg was dropped in DE.5 step 12."
            )

    @property
    def partition(self) -> str:
        """AWS partition for synthesized ARNs.

        Standard commercial AWS = ``aws``; GovCloud = ``aws-us-gov``;
        China = ``aws-cn``. Hardcoding ``aws`` breaks deploys against
        GovCloud / China where every account-bound resource ARN must
        carry the matching partition or QS rejects the binding.

        Resolution order:

        1. If ``datasource_arn`` is set explicitly (the customer
           supplied a pre-existing datasource), parse partition from
           it — that's the authoritative shape for THIS account.
        2. Else if ``principal_arns`` is non-empty, parse from the
           first principal ARN — the customer's user/role is in the
           same partition as the resources we're about to synthesize.
        3. Else default ``aws`` (commercial; preserves prior behavior
           for the spec_example / fuzz fixtures that don't carry a
           principal).

        Bare strings (no ``arn:`` prefix) fall through to the default.
        """
        for source in (self.aws.datasource.arn, *self.aws.principal_arns):
            if source and source.startswith("arn:"):
                parts = source.split(":", 2)
                if len(parts) >= 2 and parts[1]:
                    return parts[1]
        return "aws"

    # DE.5 step 5 — legacy Config.tags() / .dataset_arn(id) /
    # .theme_arn(id) / .prefixed(name) methods dropped. All callers were
    # swept to cfg.aws.X by DE.2; those are real methods on AwsConfig.

    def to_yaml_dict(self) -> dict[str, Any]:  # typing-smell: ignore[explicit-any]: heterogeneous YAML payload — every value is something safe_dump can write
        """Return a dict ``yaml.safe_dump`` can write that
        ``load_config`` can re-read. Inverse of the v14 nested loader.

        Use case: operator mutates a cfg via ``dataclasses.replace``
        (the only way through a frozen dataclass) then saves back to
        disk. Round-trip ergonomics matter; byte-identity does not.

        Traps a naive ``dataclasses.asdict(cfg)`` falls into:

        1. ``cfg.db.dialect`` is the ``Dialect`` enum — ``safe_dump``
           refuses to represent it. Coerced to ``.value`` here.
        2. ``None``-valued optional blocks (``auth.oidc`` /
           ``app2.tls`` / ``audit.signing`` / etc.) bloat the YAML.
           Dropped recursively so the emit reads like a hand-edited
           minimal cfg.
        3. Empty containers (``principal_arns: ()``, ``extra_tags:
           ()``) — same drop. Loader supplies defaults on re-read.
        4. ``tuple`` values become ``list`` for YAML cleanliness; the
           loader rebuilds tuples (extra_tags pairs, principal_arns,
           oidc.scopes, test_generator.plants).
        """
        raw: dict[str, Any] = asdict(self)  # typing-smell: ignore[explicit-any]: asdict returns dict[str, Any]
        # Dialect enum — PyYAML's safe_dump can't represent it; coerce to
        # the string Dialect(value) will accept back.
        raw["db"]["dialect"] = self.db.dialect.value
        return _compact_for_yaml(raw)

    def write_yaml(self, dest: Path | str | IO[str]) -> None:
        """Serialize via ``to_yaml_dict`` + ``yaml.safe_dump`` to
        ``dest`` (a path-like or an already-open text stream).
        Preserves field order via ``sort_keys=False`` so blocks emit
        in dataclass declaration order (``auth`` / ``aws`` / ``db`` /
        ``app2`` / ``audit`` / ``test``).
        """
        payload = self.to_yaml_dict()
        if isinstance(dest, (str, Path)):
            with Path(dest).open("w") as f:
                yaml.safe_dump(payload, f, sort_keys=False)
        else:
            yaml.safe_dump(payload, dest, sort_keys=False)


def _compact_for_yaml(value: Any) -> Any:  # typing-smell: ignore[explicit-any]: recursive YAML-payload walk
    """Recursively strip ``None`` + empty-container values from a dict
    tree + convert tuples to lists. Used by ``Config.to_yaml_dict``.

    Returned dicts contain only keys whose values survived; nested
    dicts that empty out are themselves dropped at the parent level.
    """
    if isinstance(value, dict):
        result: dict[str, Any] = {}  # typing-smell: ignore[explicit-any]: heterogeneous YAML payload — every value is something safe_dump can write
        for k, v in cast("dict[str, Any]", value).items():
            cleaned = _compact_for_yaml(v)
            if cleaned is None:
                continue
            if isinstance(cleaned, (dict, list)) and not cleaned:
                continue
            result[k] = cleaned
        return result
    if isinstance(value, tuple):
        return [_compact_for_yaml(v) for v in cast("tuple[Any, ...]", value)]
    if isinstance(value, list):
        return [_compact_for_yaml(v) for v in cast("list[Any]", value)]
    return value


# ---------------------------------------------------------------------------
# DE.5.config_v14_consolidation.C — flat-yaml loader retired.
#
# The v13 flat-yaml shape (``aws_account_id:`` / ``demo_database_url:`` /
# etc.) is no longer accepted. Operators upgrading from v13 see
# ``LegacyFieldError`` carrying the migration target. The nested-yaml
# loader above (``_load_nested_config``) is the only entry point;
# ``load_config`` here just resolves the cfg path + dispatches.
#
# Removed in this commit:
# - ``Config.to_yaml_dict`` / ``Config.write_yaml`` — no production
#   callers remained; the two test-side users have been retired or
#   rewritten.
# - ``_CONFIG_ALLOWED_KEYS`` / ``_CONFIG_L2_ONLY_KEYS`` /
#   ``_CONFIG_LEGACY_KEYS`` / ``_reject_unknown_config_keys`` — the
#   nested loader's per-block ``_build_*_nested`` functions reject
#   unknown keys with field-path errors, replacing the flat allowlist.
# - ``_require_str`` / ``_validate_and_return_db_prefix`` / ``_opt_str``
#   — flat-only helpers.
# - The legacy ``load_config`` body — replaced by a thin dispatcher.
# ---------------------------------------------------------------------------


def load_config(path: str | Path | None = None) -> Config:
    """Resolve cfg path + dispatch to the v14 nested loader.

    Resolution order:

    1. ``path`` argument (explicit).
    2. ``RECON_GEN_CONFIG`` env override.
    3. Candidate list — first existing file wins:
       ``config.yaml`` → ``run/config.yaml`` → ``run/config.postgres.yaml``
       → ``run/config.oracle.yaml`` → ``run/config.duckdb.yaml``.

    Raises ``CfgError`` when no path resolves OR the resolved file
    can't be parsed; ``LegacyFieldError`` when an upgrading-from-v13
    cfg carries flat-yaml shape (carries the migration target);
    ``MissingFieldError`` / ``CycleError`` for the structural errors
    surfaced by the nested loader.
    """
    if path is None:
        from recon_gen.common.env_keys import RECON_GEN_CONFIG  # noqa: PLC0415
        env_override = RECON_GEN_CONFIG.get_or_none()
        if env_override:
            path = Path(env_override)
        else:
            for candidate in (
                "config.yaml",
                "run/config.yaml",
                "run/config.postgres.yaml",
                "run/config.oracle.yaml",
                "run/config.duckdb.yaml",
            ):
                if Path(candidate).is_file():
                    path = Path(candidate)
                    break
            if path is None:
                raise CfgError(
                    "No cfg path provided + no candidate found "
                    "(config.yaml / run/config.yaml / "
                    "run/config.postgres.yaml / run/config.oracle.yaml / "
                    "run/config.duckdb.yaml). Set RECON_GEN_CONFIG or "
                    "pass path explicitly."
                )
    p = Path(path) if isinstance(path, str) else path
    if not p.exists():
        raise CfgError(f"cfg path does not exist: {p}")
    cfg = _load_nested_config(p)
    _apply_cfg_aws_profile_to_env(cfg)
    return cfg


def _apply_cfg_aws_profile_to_env(cfg: "Config") -> None:
    """Wire ``cfg.auth.aws.profile`` to ``AWS_PROFILE`` so downstream
    ``boto3.client(...)`` calls pick up the long-lived IAM-user credentials
    out of ``~/.aws/credentials`` instead of falling through to the
    ambient SSO-cached default (which expires + raises
    ``LoginRefreshRequired`` mid-deploy).

    Operator's explicitly-set ``AWS_PROFILE`` env var wins — only
    auto-populate when unset, so cron + CI overrides via env keep their
    precedence.

    Prior contract: the test layer runner injected ``AWS_PROFILE`` into
    every subprocess it spawned; bare ``recon-gen`` invocations (e.g.,
    ``recon-gen json apply --execute -c run/config.postgres.yaml`` outside
    the runner) relied on the operator's ambient shell having ``AWS_PROFILE``
    already exported. Picking it up from the cfg at load time closes
    that gap so both invocation shapes use the same identity.
    """
    profile = cfg.auth.aws.profile
    if not profile:
        return
    import os  # noqa: PLC0415 — lazy: only fired when cfg supplies a profile
    os.environ.setdefault("AWS_PROFILE", profile)  # typing-smell: ignore[envvar-bypass]: cfg-supplied profile name (auth.aws.profile) per [[feedback_no_credential_friction]]
