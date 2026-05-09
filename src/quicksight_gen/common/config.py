"""Configuration for QuickSight resource generation.

Reads from a YAML config file or environment variables. All generated
resources reference the datasource and account specified here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml

from quicksight_gen.common.env_keys import (
    QS_GEN_APP2_DB_POOL_SIZE,
    QS_GEN_AWS_ACCOUNT_ID,
    QS_GEN_AWS_REGION,
    QS_GEN_DATASOURCE_ARN,
    QS_GEN_DEMO_DATABASE_URL,
    QS_GEN_DIALECT,
    QS_GEN_L2_INSTANCE_PREFIX,
    QS_GEN_PRINCIPAL_ARNS,
    QS_GEN_RESOURCE_PREFIX,
)
from quicksight_gen.common.sql import Dialect

if TYPE_CHECKING:
    from quicksight_gen.common.models import Tag


@dataclass(frozen=True)
class AuthConfig:
    """Local-runner AWS auth + QS embed-signing identity.

    Combined h+i.0 spike (2026-05-08, `docs/audits/y_2_gate_h_i_combined_spike.md`):
    long-lived IAM access keys for a dedicated `quicksight-gen-local` user,
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
    """
    aws_profile: str | None = None
    quicksight_user_arn: str | None = None


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


@dataclass
class Config:
    aws_account_id: str
    aws_region: str
    datasource_arn: str | None = None
    resource_prefix: str = "qs-gen"
    principal_arns: list[str] = field(default_factory=list[str])
    extra_tags: dict[str, str] = field(default_factory=dict[str, str])
    demo_database_url: str | None = None
    # Per M.2d.3: when set, the L2 instance prefix becomes the middle
    # segment of every resource ID generated via ``cfg.prefixed(name)``,
    # producing IDs like ``qs-gen-sasquatch_ar-l1-dashboard``. Lets N
    # apps (L1, PR, Exec) deploy against the same L2 instance without
    # collision, AND lets the same app deploy against N L2 instances
    # in the same QS account. Apps set this at build time (e.g.
    # ``build_l1_dashboard_app`` derives it from the L2 instance).
    # Also surfaces as an ``L2Instance`` resource tag for cleanup
    # scoping. Unset = legacy single-tenant flat-prefix behavior.
    l2_instance_prefix: str | None = None
    # P.6.a — SQL dialect for emitted DDL + dataset SQL + demo apply.
    # ``postgres`` (default, current behavior) or ``oracle`` (Phase P).
    # The dialect is tied to the datasource: a Postgres datasource_arn
    # cannot serve Oracle SQL and vice versa; in practice integrators
    # carry separate config files (config-postgres.yaml +
    # config-oracle.yaml) keyed off this field.
    dialect: Dialect = Dialect.POSTGRES
    # U.7.b — Optional digital signing material for the audit PDF.
    # When set, ``audit apply --execute`` runs the rendered PDF
    # through pyHanko to apply a CMS signature. Absent = ship the
    # PDF unsigned (current behavior).
    signing: SigningConfig | None = None
    # Y.2.gate.h+i.0 — Local-runner AWS auth + QS embed-signing identity.
    # When set, the test-layer-chain runner injects ``AWS_PROFILE`` into
    # subprocess envs (per ``cfg.auth.aws_profile``) and auto-derives
    # ``QS_E2E_USER_ARN`` from STS+ListUsers (or uses
    # ``cfg.auth.quicksight_user_arn`` when explicitly set). Absent =
    # operator manages auth via ambient env vars (legacy behavior; CI
    # also uses ambient via OIDC). See combined spike for the full
    # decision + IAM runbook.
    auth: AuthConfig | None = None
    # Y.2.gate.h.6 — Path to the L2 institution YAML the operator's external
    # DB has been seeded with. Runner injects ``QS_GEN_TEST_L2_INSTANCE=<path>``
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
    default_l2_instance: str | None = None
    # v8.6.11 — When True (default), every Create* boto3 call passes
    # ``Tags=[ManagedBy, ResourcePrefix, L2Instance, *extra_tags]`` so
    # ``json clean`` can fail-CLOSED scope deletion to ourselves. Set
    # False ONLY when the IAM principal lacks ``quicksight:TagResource``
    # / ``UntagResource`` permissions (e.g. an enterprise environment
    # where another system applies governance tags). With tagging off
    # ``json clean`` falls back to ID-prefix matching against
    # ``resource_prefix`` — significantly weaker isolation. See the
    # docs reference for the loss-of-safety details before opting in.
    tagging_enabled: bool = True
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
    app2_db_pool_size: int = 10

    def __post_init__(self) -> None:
        # If demo_database_url is set but datasource_arn is not, derive it
        if self.datasource_arn is None and self.demo_database_url is not None:
            ds_id = self.prefixed("demo-datasource")
            self.datasource_arn = (
                f"arn:{self.partition}:quicksight:{self.aws_region}"
                f":{self.aws_account_id}:datasource/{ds_id}"
            )
        if self.datasource_arn is None:
            raise ValueError(
                "datasource_arn is required unless demo_database_url is set."
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
        for source in (self.datasource_arn, *self.principal_arns):
            if source and source.startswith("arn:"):
                parts = source.split(":", 2)
                if len(parts) >= 2 and parts[1]:
                    return parts[1]
        return "aws"

    def with_l2_instance_prefix(self, prefix: str) -> "Config":
        """Return a new Config with the L2 prefix stamped in.

        When ``demo_database_url`` is set, also clears ``datasource_arn``
        so ``__post_init__`` re-derives it with the prefix in the path —
        without this, per-app builders bake the unprefixed
        ``qs-gen-demo-datasource`` ARN into dataset JSON and the deploy
        fails with ``InvalidParameterValueException: Invalid dataSourceArn``
        because the actual datasource resource carries the prefix
        (``qs-gen-<prefix>-demo-datasource``).

        When ``demo_database_url`` is unset (production deploys against
        a pre-existing customer datasource), the explicit ``datasource_arn``
        stays as-is — re-deriving would synthesize an ARN the customer's
        QS account doesn't have.

        Idempotent: callers can guard with ``if cfg.l2_instance_prefix
        is None`` to skip the re-derive when the cfg is already L2-aware.
        """
        from dataclasses import replace
        if self.demo_database_url is not None:
            return replace(
                self,
                l2_instance_prefix=prefix,
                datasource_arn=None,
            )
        return replace(self, l2_instance_prefix=prefix)

    # Derived helpers
    def tags(self) -> "list[Tag] | None":
        """Return common + extra tags as the AWS Tag list format.

        Three tags are always emitted (when ``tagging_enabled``):

        - ``ManagedBy=quicksight-gen`` — gates cleanup eligibility.
        - ``ResourcePrefix=<resource_prefix>`` — per-deploy scope. v8.4.0
          isolation: lets cleanup sweep only the deployer's own
          resources (e.g. ``qs-ci-<run_id>-pg``), so concurrent CI
          runs + local deploys don't trample each other.
        - ``L2Instance=<l2_instance_prefix>`` — only when the prefix is
          set (M.2d.3). Per-institution scope, narrower than
          ``ResourcePrefix``.

        Returns ``None`` when ``tagging_enabled=False`` so the caller's
        ``Tags=cfg.tags()`` field assignment goes to the dataclass's
        ``Tags: list[Tag] | None`` field as ``None`` and ``_strip_nones``
        drops it from the emitted JSON entirely. Net effect: the
        ``Create*`` boto3 call carries no ``Tags`` kwarg, so the IAM
        principal doesn't need ``quicksight:TagResource`` permission.
        """
        if not self.tagging_enabled:
            return None
        from quicksight_gen.common.models import Tag

        all_tags = [
            Tag(Key="ManagedBy", Value="quicksight-gen"),
            Tag(Key="ResourcePrefix", Value=self.resource_prefix),
        ]
        if self.l2_instance_prefix is not None:
            all_tags.append(Tag(Key="L2Instance", Value=self.l2_instance_prefix))
        for key, value in self.extra_tags.items():
            all_tags.append(Tag(Key=key, Value=value))
        return all_tags

    def dataset_arn(self, dataset_id: str) -> str:
        return (
            f"arn:{self.partition}:quicksight:{self.aws_region}"
            f":{self.aws_account_id}:dataset/{dataset_id}"
        )

    def theme_arn(self, theme_id: str) -> str:
        return (
            f"arn:{self.partition}:quicksight:{self.aws_region}"
            f":{self.aws_account_id}:theme/{theme_id}"
        )

    def prefixed(self, name: str) -> str:
        """Return a resource ID with the configured prefix.

        When ``l2_instance_prefix`` is set, that prefix becomes the
        middle segment so multiple L2 instances coexist in one QS
        account (M.2d.3): ``qs-gen-<l2_instance>-<name>``.
        """
        if self.l2_instance_prefix is not None:
            return f"{self.resource_prefix}-{self.l2_instance_prefix}-{name}"
        return f"{self.resource_prefix}-{name}"


# V.1.b — Strict config-key allowlist. config.yaml is environment-only:
# AWS account / region / dialect / DB connection / signing material.
# Institution-only fields (theme, persona, accounts, rails, chains,
# transfer_templates, account_templates, limit_schedules, instance,
# description) live in the L2 institution YAML — putting them in
# config.yaml is a sign the user has the wrong file open.
# ``l2_instance_prefix`` is derived from the L2 instance at runtime
# (cli/_helpers.py::resolve_l2_for_demo) and must not be hand-set here.
_CONFIG_ALLOWED_KEYS: frozenset[str] = frozenset({
    "aws_account_id", "aws_region", "datasource_arn", "resource_prefix",
    "principal_arns", "principal_arn", "extra_tags", "demo_database_url",
    "dialect", "signing", "tagging_enabled", "app2_db_pool_size", "auth",
    "default_l2_instance",
})

_CONFIG_L2_ONLY_KEYS: frozenset[str] = frozenset({
    "instance", "description", "accounts", "account_templates",
    "rails", "transfer_templates", "chains", "limit_schedules",
    "persona", "theme",
})


def _require_str(
    values: dict[str, object], key: str, *, default: str | None = None,
) -> str:
    """Extract ``key`` as a ``str``, raising if absent/wrong-type.

    Pyright sees ``dict[str, object].get(key)`` as ``object``; this
    helper does the isinstance narrowing in one place so callers get
    a properly-typed ``str``.
    """
    raw = values.get(key, default)
    if raw is None:
        raise ValueError(f"{key} is required")
    if not isinstance(raw, str):
        raise ValueError(
            f"{key} must be a string; got {type(raw).__name__} ({raw!r})"
        )
    return raw


def _opt_str(values: dict[str, object], key: str) -> str | None:
    """``_require_str`` but None when missing."""
    raw = values.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(
            f"{key} must be a string; got {type(raw).__name__} ({raw!r})"
        )
    return raw


def _reject_unknown_config_keys(raw: dict[str, object], path: Path) -> None:
    """Raise if config.yaml contains keys outside the env-only allowlist.

    V.1.b: catches the two common mis-edits — dropping an L2 institution
    block (theme, persona, rails, …) into config.yaml, and hand-setting
    ``l2_instance_prefix`` instead of letting the CLI derive it from the
    L2 instance.
    """
    leaked_l2 = sorted(set(raw) & _CONFIG_L2_ONLY_KEYS)
    if leaked_l2:
        raise ValueError(
            f"{path}: keys {leaked_l2} belong in the L2 institution YAML "
            f"(passed via --l2), not config.yaml. config.yaml holds "
            f"environment-only values (account / region / dialect / DB "
            f"connection / signing); institution shape (theme / persona / "
            f"rails / accounts / chains / transfer_templates / account_"
            f"templates / limit_schedules / instance / description) "
            f"lives in the L2 YAML."
        )
    if "l2_instance_prefix" in raw:
        raise ValueError(
            f"{path}: 'l2_instance_prefix' must not be set in config.yaml "
            f"— it is derived from the L2 institution YAML's 'instance:' "
            f"field at CLI time. Drop the key and pass --l2 <institution>."
        )
    unknown = sorted(set(raw) - _CONFIG_ALLOWED_KEYS)
    if unknown:
        raise ValueError(
            f"{path}: unknown config keys {unknown}. "
            f"Allowed: {sorted(_CONFIG_ALLOWED_KEYS)}."
        )


def load_config(path: str | Path | None = None) -> Config:
    """Load configuration from a YAML file, falling back to env vars.

    YAML keys map directly to Config fields (snake_case). ``principal_arns``
    may be a single string or a list; a legacy ``principal_arn`` key is also
    accepted as a single string.
    Environment variables use uppercase with QS_GEN_ prefix:
        QS_GEN_AWS_ACCOUNT_ID, QS_GEN_AWS_REGION, QS_GEN_DATASOURCE_ARN,
        QS_GEN_RESOURCE_PREFIX, QS_GEN_PRINCIPAL_ARNS (comma-separated)

    V.1.b: rejects unknown YAML keys and L2-only keys (theme, persona,
    rails, etc.) with a pointer to the L2 institution YAML.
    """
    values: dict[str, object] = {}

    # Try YAML first
    if path is not None:
        p = Path(path)
        if p.exists():
            with p.open() as f:
                # ``yaml.safe_load`` returns ``Any``; the isinstance
                # guard below narrows to dict[Hashable, Any] which we
                # treat as dict[str, object] (config keys are strings
                # by convention, validated against allowlists).
                raw: object = yaml.safe_load(f)
            if isinstance(raw, dict):
                # YAML dicts come back as dict[Any, Any]; coerce keys
                # to str (the rest of the loader assumes string keys)
                # and let pyright treat values as ``object`` from here.
                raw_typed = cast(dict[Any, Any], raw)
                raw_dict: dict[str, object] = {
                    str(k): v for k, v in raw_typed.items()
                }
                _reject_unknown_config_keys(raw_dict, p)
                values.update(raw_dict)

    # Env vars override YAML. The (cfg_key → EnvVar) shape goes
    # through the typed registry — get_or_none() coerces + validates
    # at the boundary; any malformed override surfaces as
    # EnvVarInvalid carrying the env-var name + description.
    env_map = {
        "aws_account_id": QS_GEN_AWS_ACCOUNT_ID,
        "aws_region": QS_GEN_AWS_REGION,
        "datasource_arn": QS_GEN_DATASOURCE_ARN,
        "resource_prefix": QS_GEN_RESOURCE_PREFIX,
        "l2_instance_prefix": QS_GEN_L2_INSTANCE_PREFIX,
        "demo_database_url": QS_GEN_DEMO_DATABASE_URL,
        "dialect": QS_GEN_DIALECT,
        "app2_db_pool_size": QS_GEN_APP2_DB_POOL_SIZE,
    }
    for cfg_key, spec in env_map.items():
        env_val = spec.get_or_none()
        if env_val is not None:
            values[cfg_key] = env_val

    env_principals = QS_GEN_PRINCIPAL_ARNS.get_or_none()
    if env_principals is not None:
        values["principal_arns"] = [
            p.strip() for p in env_principals.split(",") if p.strip()
        ]

    # Validate required fields (datasource_arn not required when demo_database_url is set)
    required = ["aws_account_id", "aws_region"]
    if "demo_database_url" not in values:
        required.append("datasource_arn")
    missing = [k for k in required if k not in values]
    if missing:
        required_env = {
            "aws_account_id": "QS_GEN_AWS_ACCOUNT_ID",
            "aws_region": "QS_GEN_AWS_REGION",
            "datasource_arn": "QS_GEN_DATASOURCE_ARN",
        }
        raise ValueError(
            f"Missing required configuration: {', '.join(missing)}. "
            f"Set them in your config YAML or via environment variables "
            f"({', '.join(required_env[k] for k in missing)})."
        )

    # Extra tags: expect a dict under "extra_tags" in the YAML
    raw_tags = values.get("extra_tags", {})
    extra_tags: dict[str, str] = {}
    if isinstance(raw_tags, dict):
        tags_typed = cast(dict[Any, Any], raw_tags)
        for k, v in tags_typed.items():
            extra_tags[str(k)] = str(v)

    # Principals: accept ``principal_arns`` (list or str) or legacy
    # ``principal_arn`` (str or list).
    principal_arns: list[str] = []
    for key in ("principal_arns", "principal_arn"):
        raw = values.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            principal_arns.append(raw)
        elif isinstance(raw, list):
            list_typed = cast(list[Any], raw)
            for item in list_typed:
                principal_arns.append(str(item))

    # Dialect parses to the enum; default Postgres for back-compat.
    raw_dialect = values.get("dialect")
    if raw_dialect is None:
        dialect = Dialect.POSTGRES
    elif isinstance(raw_dialect, Dialect):
        dialect = raw_dialect
    else:
        try:
            dialect = Dialect(str(raw_dialect).lower())
        except ValueError as exc:
            raise ValueError(
                f"dialect must be one of {[d.value for d in Dialect]}; "
                f"got {raw_dialect!r}."
            ) from exc

    # U.7.b — optional signing block.
    raw_signing = values.get("signing")
    signing: SigningConfig | None = None
    if isinstance(raw_signing, dict):
        sig_typed = cast(dict[Any, Any], raw_signing)
        sig_dict: dict[str, object] = {
            str(k): v for k, v in sig_typed.items()
        }
        try:
            signing = SigningConfig(
                key_path=str(sig_dict["key_path"]),
                cert_path=str(sig_dict["cert_path"]),
                passphrase_env=(
                    str(sig_dict["passphrase_env"])
                    if sig_dict.get("passphrase_env") is not None
                    else None
                ),
                signer_name=(
                    str(sig_dict["signer_name"])
                    if sig_dict.get("signer_name") is not None
                    else None
                ),
            )
        except KeyError as exc:
            raise ValueError(
                f"signing block is missing required field: {exc}. "
                f"Need both 'key_path' and 'cert_path'."
            ) from exc

    # Y.2.gate.h+i.0 — optional auth block.
    raw_auth = values.get("auth")
    auth: AuthConfig | None = None
    if isinstance(raw_auth, dict):
        auth_typed = cast(dict[Any, Any], raw_auth)
        auth_dict: dict[str, object] = {
            str(k): v for k, v in auth_typed.items()
        }
        unknown_auth = set(auth_dict) - {"aws_profile", "quicksight_user_arn"}
        if unknown_auth:
            raise ValueError(
                f"auth block contains unknown keys: {sorted(unknown_auth)}. "
                f"Allowed: aws_profile, quicksight_user_arn."
            )
        auth = AuthConfig(
            aws_profile=(
                str(auth_dict["aws_profile"])
                if auth_dict.get("aws_profile") is not None
                else None
            ),
            quicksight_user_arn=(
                str(auth_dict["quicksight_user_arn"])
                if auth_dict.get("quicksight_user_arn") is not None
                else None
            ),
        )

    raw_tagging = values.get("tagging_enabled", True)
    if not isinstance(raw_tagging, bool):
        raise ValueError(
            f"tagging_enabled must be a bool; got {raw_tagging!r}."
        )

    raw_pool_size = values.get("app2_db_pool_size", 10)
    if not isinstance(raw_pool_size, (int, str)):
        raise ValueError(
            f"app2_db_pool_size must be a positive integer; "
            f"got {type(raw_pool_size).__name__} ({raw_pool_size!r})."
        )
    try:
        pool_size = int(raw_pool_size)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"app2_db_pool_size must be a positive integer; "
            f"got {raw_pool_size!r}."
        ) from exc
    if pool_size < 1:
        raise ValueError(
            f"app2_db_pool_size must be ≥ 1; got {pool_size}."
        )

    return Config(
        aws_account_id=_require_str(values, "aws_account_id"),
        aws_region=_require_str(values, "aws_region"),
        datasource_arn=_opt_str(values, "datasource_arn"),
        resource_prefix=_require_str(values, "resource_prefix", default="qs-gen"),
        principal_arns=principal_arns,
        extra_tags=extra_tags,
        demo_database_url=_opt_str(values, "demo_database_url"),
        dialect=dialect,
        signing=signing,
        auth=auth,
        default_l2_instance=_opt_str(values, "default_l2_instance"),
        tagging_enabled=raw_tagging,
        app2_db_pool_size=pool_size,
    )
