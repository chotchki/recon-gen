"""Configuration for QuickSight resource generation.

Reads from a YAML config file or environment variables. All generated
resources reference the datasource and account specified here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast, get_args

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
    RECON_GEN_DEMO_DATABASE_URL,
    RECON_GEN_DEPLOYMENT_NAME,
    RECON_GEN_DIALECT,
    RECON_GEN_PRINCIPAL_ARNS,
)
from recon_gen.common.sql import Dialect

if TYPE_CHECKING:
    from recon_gen.common.models import Tag


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


# BS.4 (2026-05-29) removed EtlDatasourceConfig + the
# Config.etl_datasource field. The legacy upstream→demo_db copy path
# (X.4.g.2's step_2_pull) is gone; etl_hook now writes directly to
# demo_db. See docs/audits/bs_4_arch_shift_spike.md.


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

    def as_of_frame(self, *, window_days: int = 0) -> AsOfFrame:
        """Resolve this config's scenario anchor as the owned `AsOfFrame`
        (D1; see `docs/audits/date_range_model_audit.md` §5 + BD.0 spike).

        This is the call-site every `as_of` reader lands on — AQ.3 funnels
        the generator's threaded ``anchor=`` and the four ad-hoc
        ``date.today()`` fallbacks through it, and AR's views take an
        `AsOfFrame` as their anchor. Three resolution paths, one shape out:

          * ``end_date == LOCKED_ANCHOR`` → ``AsOfFrame.locked()`` (the
            canonical demo anchor; locked-seed determinism).
          * ``end_date is not None`` → explicit-anchor frame (operator
            override or trainer-pinned).
          * ``end_date is None`` → ``AsOfFrame.live()`` (production
            ends-at-now).

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
        return AsOfFrame.live(window_days=window_days)


@dataclass
class Config:
    aws_account_id: str
    aws_region: str
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
    deployment_name: str
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
    db_table_prefix: str
    datasource_arn: str | None = None
    principal_arns: list[str] = field(default_factory=list[str])
    extra_tags: dict[str, str] = field(default_factory=dict[str, str])
    demo_database_url: str | None = None
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
    # ``RECON_E2E_USER_ARN`` from STS+ListUsers (or uses
    # ``cfg.auth.quicksight_user_arn`` when explicitly set). Absent =
    # operator manages auth via ambient env vars (legacy behavior; CI
    # also uses ambient via OIDC). See combined spike for the full
    # decision + IAM runbook.
    auth: AuthConfig | None = None
    # Set by ``__post_init__``: True iff ``datasource_arn`` was *derived*
    # from ``demo_database_url`` (we own the QS datasource resource and
    # emit ``out/datasource.json``), False iff the operator supplied an
    # explicit ``datasource_arn`` (a pre-existing customer datasource —
    # we leave it alone and DON'T emit a datasource resource), regardless
    # of whether ``demo_database_url`` is also set. ``cli/json.py`` keys
    # the datasource-emit on this; not an init param, not in repr/eq.
    datasource_arn_was_derived: bool = field(
        default=False, init=False, repr=False, compare=False,
    )
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
    # Phase BS.2 (D1 nav contract) — toggles the Studio surface on/off
    # in the App2 binary. When False, the Studio top-nav entries (L2
    # Editor / ETL Support / Training) hide and the `/studio/*` routes
    # are not mounted. Dashboards + Docs are baseline (always mounted).
    # Default True for dev (Studio is the authoring path); production
    # cfgs that ship dashboards-only set `studio_enabled: false`.
    # See SPEC.md::D1 + PLAN.md::Phase BS BS.0 Lock 1.
    studio_enabled: bool = True
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
    # Y.2.gate.l — RDS identifiers for the start/stop lifecycle.
    # `./run_tests.sh up aws` / `down aws` / `status` read these to
    # know which Aurora cluster + Oracle instance to act on. Local
    # operator's cfg points at the dev clusters (e.g. database-2 /
    # database-3); CI's per-job env injects the CI-side identifiers
    # (`recon-ci-aurora` / `recon-ci-oracle`) so the two lifecycles
    # don't step on each other (per gate.l.0 provisioning runbook).
    # Both optional — when unset, the lifecycle commands loud-fail
    # at the dispatch site with the env-var fallback name.
    aws_pg_cluster_id: str | None = None
    aws_oracle_instance_id: str | None = None
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
    etl_hook: str | None = None
    # X.4.g.3 — Step 3 (synthetic data overlay) knobs. Non-Optional
    # default-factory so the pipeline never None-checks; an absent
    # block in the cfg yaml resolves to `TestGeneratorConfig()`
    # (byte-identical-to-locked-seeds output).
    test_generator: TestGeneratorConfig = field(
        default_factory=TestGeneratorConfig,
    )

    def __post_init__(self) -> None:
        # If demo_database_url is set but datasource_arn is not, derive it
        # — and record that we own the resulting datasource resource.
        if self.datasource_arn is None and self.demo_database_url is not None:
            ds_id = self.prefixed("demo-datasource")
            self.datasource_arn = (
                f"arn:{self.partition}:quicksight:{self.aws_region}"
                f":{self.aws_account_id}:datasource/{ds_id}"
            )
            self.datasource_arn_was_derived = True
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

    # Derived helpers
    def tags(self) -> "list[Tag] | None":
        """Return common + extra tags as the AWS Tag list format.

        Two tags are always emitted (when ``tagging_enabled``):

        - ``ManagedBy=recon-gen`` — gates cleanup eligibility (the
          tool-identity signal; never varies).
        - ``Deployment=<deployment_name>`` — per-deploy scope. ``json
          clean`` requires both tags to match before deleting, so
          concurrent deploys (CI + local, dev + staging) never trample
          each other.

        Returns ``None`` when ``tagging_enabled=False`` so the caller's
        ``Tags=cfg.tags()`` field assignment goes to the dataclass's
        ``Tags: list[Tag] | None`` field as ``None`` and ``_strip_nones``
        drops it from the emitted JSON entirely. Net effect: the
        ``Create*`` boto3 call carries no ``Tags`` kwarg, so the IAM
        principal doesn't need ``quicksight:TagResource`` permission.
        """
        if not self.tagging_enabled:
            return None
        from recon_gen.common.models import Tag

        all_tags = [
            Tag(Key="ManagedBy", Value="recon-gen"),
            Tag(Key="Deployment", Value=self.deployment_name),
        ]
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
        """Return a resource ID with the configured deployment prefix.

        Z.C: single-segment prefix replaces v8.x's
        ``<resource_prefix>-<l2_instance_prefix>-<name>``. The
        ``deployment_name`` is the operator's per-deployment
        namespace, set explicitly in cfg.yaml (no default).
        """
        return f"{self.deployment_name}-{name}"


# V.1.b — Strict config-key allowlist. config.yaml is environment-only:
# AWS account / region / dialect / DB connection / signing material /
# Z.C deployment + DB-prefix names. Institution-only fields (theme,
# persona, accounts, rails, chains, transfer_templates, account_templates,
# limit_schedules, description) live in the L2 institution YAML —
# putting them in config.yaml is a sign the user has the wrong file open.
_CONFIG_ALLOWED_KEYS: frozenset[str] = frozenset({
    "aws_account_id", "aws_region", "datasource_arn",
    "deployment_name", "db_table_prefix",
    "principal_arns", "principal_arn", "extra_tags", "demo_database_url",
    "dialect", "signing", "tagging_enabled", "studio_enabled",
    "app2_db_pool_size", "auth",
    "default_l2_instance", "aws_pg_cluster_id", "aws_oracle_instance_id",
    # X.4.g.1+3 — deploy pipeline knobs (etl_datasource removed in BS.4).
    "etl_hook", "test_generator",
})

# Z.C — `instance` removed: the L2 yaml no longer has an `instance:` field
# at all (use cfg.deployment_name + cfg.db_table_prefix instead).
_CONFIG_L2_ONLY_KEYS: frozenset[str] = frozenset({
    "description", "accounts", "account_templates",
    "rails", "transfer_templates", "chains", "limit_schedules",
    "persona", "theme",
})

# Z.C — Legacy keys that USED to be valid in cfg.yaml but no longer are.
# Each maps to an actionable migration message pointing the operator at
# the new shape. Surfaced by `_reject_unknown_config_keys` so the loud-
# fail message is specific instead of just "unknown key".
_CONFIG_LEGACY_KEYS: dict[str, str] = {
    "resource_prefix": (
        "merged with l2_instance_prefix into 'deployment_name' (Z.C). "
        "Set 'deployment_name: <your-deployment-id>' (e.g. 'recon-prod'). "
        "Replaces both the v8.x default 'qs-gen' tool prefix AND the "
        "auto-stamped L2 segment."
    ),
    "l2_instance_prefix": (
        "merged with resource_prefix into 'deployment_name' (Z.C). "
        "Set 'deployment_name: <your-deployment-id>' in cfg.yaml. The "
        "auto-stamping from L2 yaml's 'instance:' is gone."
    ),
    "instance": (
        "the L2 yaml's top-level 'instance:' field was dropped in Z.C. "
        "Set 'deployment_name' AND 'db_table_prefix' in cfg.yaml — "
        "they replace 'instance' (which previously did double duty as "
        "QS-resource segment + DB-table prefix)."
    ),
}


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
    block (theme, persona, rails, …) into config.yaml, and the Z.C-removed
    legacy ``resource_prefix`` / ``l2_instance_prefix`` / ``instance`` keys
    (each gets a specific migration pointer).
    """
    leaked_l2 = sorted(set(raw) & _CONFIG_L2_ONLY_KEYS)
    if leaked_l2:
        raise ValueError(
            f"{path}: keys {leaked_l2} belong in the L2 institution YAML "
            f"(passed via --l2), not config.yaml. config.yaml holds "
            f"environment-only values (account / region / dialect / DB "
            f"connection / signing); institution shape (theme / persona / "
            f"rails / accounts / chains / transfer_templates / account_"
            f"templates / limit_schedules / description) "
            f"lives in the L2 YAML."
        )
    legacy_present = sorted(set(raw) & set(_CONFIG_LEGACY_KEYS))
    if legacy_present:
        msg_lines = [f"{path}: legacy config keys removed in Z.C:"]
        for key in legacy_present:
            msg_lines.append(f"  - '{key}': {_CONFIG_LEGACY_KEYS[key]}")
        raise ValueError("\n".join(msg_lines))
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
    Environment variables use uppercase with RECON_GEN_ prefix:
        RECON_GEN_AWS_ACCOUNT_ID, RECON_GEN_AWS_REGION, RECON_GEN_DATASOURCE_ARN,
        RECON_GEN_RESOURCE_PREFIX, RECON_GEN_PRINCIPAL_ARNS (comma-separated)

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
        "aws_account_id": RECON_GEN_AWS_ACCOUNT_ID,
        "aws_region": RECON_GEN_AWS_REGION,
        "datasource_arn": RECON_GEN_DATASOURCE_ARN,
        "deployment_name": RECON_GEN_DEPLOYMENT_NAME,
        "db_table_prefix": RECON_GEN_DB_TABLE_PREFIX,
        "demo_database_url": RECON_GEN_DEMO_DATABASE_URL,
        "dialect": RECON_GEN_DIALECT,
        "app2_db_pool_size": RECON_GEN_APP2_DB_POOL_SIZE,
        "aws_pg_cluster_id": RECON_GEN_AWS_PG_CLUSTER_ID,
        "aws_oracle_instance_id": RECON_GEN_AWS_ORACLE_INSTANCE_ID,
    }
    for cfg_key, spec in env_map.items():
        env_val = spec.get_or_none()
        if env_val is not None:
            values[cfg_key] = env_val

    env_principals = RECON_GEN_PRINCIPAL_ARNS.get_or_none()
    if env_principals is not None:
        values["principal_arns"] = [
            p.strip() for p in env_principals.split(",") if p.strip()
        ]

    # Validate required fields (datasource_arn not required when demo_database_url is set).
    # Z.C: deployment_name + db_table_prefix join the required-fields list.
    required = ["aws_account_id", "aws_region", "deployment_name", "db_table_prefix"]
    if "demo_database_url" not in values:
        required.append("datasource_arn")
    missing = [k for k in required if k not in values]
    if missing:
        required_env = {
            "aws_account_id": "RECON_GEN_AWS_ACCOUNT_ID",
            "aws_region": "RECON_GEN_AWS_REGION",
            "datasource_arn": "RECON_GEN_DATASOURCE_ARN",
            "deployment_name": "RECON_GEN_DEPLOYMENT_NAME",
            "db_table_prefix": "RECON_GEN_DB_TABLE_PREFIX",
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

    raw_studio_enabled = values.get("studio_enabled", True)
    if not isinstance(raw_studio_enabled, bool):
        raise ValueError(
            f"studio_enabled must be a bool; got {raw_studio_enabled!r}."
        )

    # BS.4 (2026-05-29): etl_datasource block removed from cfg — the
    # legacy upstream→demo_db copy is gone (etl_hook writes directly).
    # See docs/audits/bs_4_arch_shift_spike.md.

    # X.4.g.3 — optional test_generator block. Absent or None resolves
    # to TestGeneratorConfig() (byte-identical-to-locked-seeds output);
    # explicit dict parses + validates.
    raw_tgen = values.get("test_generator")
    test_generator = TestGeneratorConfig()
    if isinstance(raw_tgen, dict):
        tgen_typed = cast(dict[Any, Any], raw_tgen)
        tgen_dict: dict[str, object] = {
            str(k): v for k, v in tgen_typed.items()
        }
        allowed_tgen = {
            "enabled", "scope", "end_date", "seed", "plants",
            "only_template", "derive_balances",
            "derive_balances_account_roles", "cutoff_date",
        }
        unknown_tgen = set(tgen_dict) - allowed_tgen
        if unknown_tgen:
            raise ValueError(
                f"test_generator block contains unknown keys: "
                f"{sorted(unknown_tgen)}. Allowed: {sorted(allowed_tgen)}."
            )
        scope_val = tgen_dict.get("scope", "full")
        scope_allowed = get_args(ScopeKind)
        if scope_val not in scope_allowed:
            raise ValueError(
                f"test_generator.scope must be one of {list(scope_allowed)}; "
                f"got {scope_val!r}."
            )
        plants_raw = tgen_dict.get("plants", ())
        if isinstance(plants_raw, (list, tuple)):
            plants_iter = cast(list[Any] | tuple[Any, ...], plants_raw)
            plants_seq = tuple(str(p) for p in plants_iter)
        else:
            raise ValueError(
                f"test_generator.plants must be a list of strings; "
                f"got {type(plants_raw).__name__} ({plants_raw!r})."
            )
        plants_allowed = get_args(PlantKind)
        bad_plants = [p for p in plants_seq if p not in plants_allowed]
        if bad_plants:
            raise ValueError(
                f"test_generator.plants contains unknown values "
                f"{bad_plants}; allowed: {list(plants_allowed)}."
            )
        end_date_raw = tgen_dict.get("end_date")
        end_date_val: date | None
        if end_date_raw is None:
            end_date_val = None
        elif isinstance(end_date_raw, date):
            end_date_val = end_date_raw
        elif isinstance(end_date_raw, str):
            try:
                end_date_val = date.fromisoformat(end_date_raw)
            except ValueError as exc:
                raise ValueError(
                    f"test_generator.end_date must be ISO 8601 (YYYY-MM-DD); "
                    f"got {end_date_raw!r}."
                ) from exc
        else:
            raise ValueError(
                f"test_generator.end_date must be a date or ISO string; "
                f"got {type(end_date_raw).__name__} ({end_date_raw!r})."
            )
        seed_raw = tgen_dict.get("seed")
        seed_val: int | None
        if seed_raw is None:
            seed_val = None
        elif isinstance(seed_raw, int) and not isinstance(seed_raw, bool):
            seed_val = seed_raw
        else:
            raise ValueError(
                f"test_generator.seed must be an integer; "
                f"got {type(seed_raw).__name__} ({seed_raw!r})."
            )
        enabled_raw = tgen_dict.get("enabled", True)
        if not isinstance(enabled_raw, bool):
            raise ValueError(
                f"test_generator.enabled must be a bool; got {enabled_raw!r}."
            )
        derive_raw = tgen_dict.get("derive_balances", False)
        if not isinstance(derive_raw, bool):
            raise ValueError(
                f"test_generator.derive_balances must be a bool; "
                f"got {derive_raw!r}."
            )
        # X.4.i.2 — optional per-account-role narrowing for the derive pass.
        # None ⇒ default control-account set inside derive_balances.
        deriv_ar_raw = tgen_dict.get("derive_balances_account_roles")
        deriv_ar_val: tuple[str, ...] | None
        if deriv_ar_raw is None:
            deriv_ar_val = None
        elif isinstance(deriv_ar_raw, (list, tuple)):
            deriv_ar_iter = cast(
                list[Any] | tuple[Any, ...], deriv_ar_raw,
            )
            deriv_ar_val = tuple(str(t) for t in deriv_ar_iter)
        else:
            raise ValueError(
                f"test_generator.derive_balances_account_roles must be a "
                f"list of strings or null; got "
                f"{type(deriv_ar_raw).__name__} ({deriv_ar_raw!r}).",
            )
        only_template_raw = tgen_dict.get("only_template")
        only_template_val: str | None = (
            str(only_template_raw) if only_template_raw is not None else None
        )
        cutoff_date_raw = tgen_dict.get("cutoff_date")
        cutoff_date_val: date | None
        if cutoff_date_raw is None:
            cutoff_date_val = None
        elif isinstance(cutoff_date_raw, date):
            cutoff_date_val = cutoff_date_raw
        elif isinstance(cutoff_date_raw, str):
            try:
                cutoff_date_val = date.fromisoformat(cutoff_date_raw)
            except ValueError as exc:
                raise ValueError(
                    f"test_generator.cutoff_date must be ISO 8601 (YYYY-MM-DD); "
                    f"got {cutoff_date_raw!r}."
                ) from exc
        else:
            raise ValueError(
                f"test_generator.cutoff_date must be a date or ISO string; "
                f"got {type(cutoff_date_raw).__name__} ({cutoff_date_raw!r})."
            )
        # cast() narrows the Literal type from runtime-validated str values.
        test_generator = TestGeneratorConfig(
            enabled=enabled_raw,
            scope=cast(ScopeKind, scope_val),
            end_date=end_date_val,
            seed=seed_val,
            plants=cast(tuple[PlantKind, ...], plants_seq),
            only_template=only_template_val,
            derive_balances=derive_raw,
            derive_balances_account_roles=deriv_ar_val,
            cutoff_date=cutoff_date_val,
        )
    elif raw_tgen is not None:
        raise ValueError(
            f"test_generator must be a mapping; got "
            f"{type(raw_tgen).__name__} ({raw_tgen!r})."
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
        deployment_name=_require_str(values, "deployment_name"),
        db_table_prefix=_require_str(values, "db_table_prefix"),
        datasource_arn=_opt_str(values, "datasource_arn"),
        principal_arns=principal_arns,
        extra_tags=extra_tags,
        demo_database_url=_opt_str(values, "demo_database_url"),
        dialect=dialect,
        signing=signing,
        auth=auth,
        default_l2_instance=_opt_str(values, "default_l2_instance"),
        tagging_enabled=raw_tagging,
        studio_enabled=raw_studio_enabled,
        app2_db_pool_size=pool_size,
        aws_pg_cluster_id=_opt_str(values, "aws_pg_cluster_id"),
        aws_oracle_instance_id=_opt_str(values, "aws_oracle_instance_id"),
        etl_hook=_opt_str(values, "etl_hook"),
        test_generator=test_generator,
    )
