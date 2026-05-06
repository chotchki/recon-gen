"""Shared QuickSight DataSource builder (M.4.4).

Migrated from ``apps/payment_recon/datasets.py`` when the PR app deleted —
build_datasource is app-agnostic infrastructure that the harness, the
demo CLI, and any future apps all need to construct a DataSource model
from a Postgres URL.

Lives under ``common/`` because it has no PR-specific dependencies and
all callers (harness's per-test datasource, ``quicksight-gen demo apply``,
manual deploy scripts) consume it equally.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from quicksight_gen.common.config import Config
from quicksight_gen.common.models import (
    CredentialPair,
    DataSource,
    DataSourceCredentials,
    DataSourceParameters,
    OracleParameters,
    PostgreSqlParameters,
    ResourcePermission,
    SslProperties,
)
from quicksight_gen.common.sql import Dialect


_DATASOURCE_ACTIONS = [
    "quicksight:DescribeDataSource",
    "quicksight:DescribeDataSourcePermissions",
    "quicksight:PassDataSource",
    "quicksight:UpdateDataSource",
    "quicksight:DeleteDataSource",
    "quicksight:UpdateDataSourcePermissions",
]


@dataclass(frozen=True)
class _ConnInfo:
    """Parsed connection components — host/port/database/user/password."""

    host: str
    port: int
    database: str
    user: str
    password: str


def _parse_pg_url(url: str) -> _ConnInfo:
    """Parse ``postgresql://user:pass@host:port/database`` form."""
    parsed = urlparse(url)
    return _ConnInfo(
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        database=parsed.path.lstrip("/") if parsed.path else "postgres",
        user=parsed.username or "",
        password=parsed.password or "",
    )


def _parse_oracle_url(url: str) -> _ConnInfo:
    """Parse Oracle's two URL shapes into connection components.

    Accepts either form:
    - ``oracle+oracledb://user:pass@host:port/?service_name=ORCL`` (or
      ``oracle://user:pass@host:port/SERVICE``) — SQLAlchemy-style.
    - ``user/pass@host:port/SERVICE`` — oracledb's native Easy Connect
      string.

    The database field on the QuickSight OracleParameters carries the
    service name / SID (e.g. ``ORCL``).
    """
    if url.startswith(("oracle://", "oracle+oracledb://")):
        parsed = urlparse(url)
        service = (
            parse_qs(parsed.query).get("service_name", [None])[0]
            or parsed.path.lstrip("/")
            or "FREEPDB1"
        )
        return _ConnInfo(
            host=parsed.hostname or "localhost",
            port=parsed.port or 1521,
            database=service,
            user=parsed.username or "",
            password=parsed.password or "",
        )
    # Native Easy Connect: user/pass@host:port/SERVICE
    if "@" not in url:
        raise ValueError(f"unparseable Oracle URL: {url!r}")
    creds, target = url.split("@", 1)
    if "/" not in creds:
        raise ValueError(f"Oracle URL missing user/password: {url!r}")
    user, password = creds.split("/", 1)
    if "/" in target:
        host_port, service = target.rsplit("/", 1)
    else:
        host_port, service = target, "ORCL"
    if ":" in host_port:
        host, port_str = host_port.split(":", 1)
        port = int(port_str)
    else:
        host = host_port
        port = 1521
    return _ConnInfo(
        host=host, port=port, database=service,
        user=user, password=password,
    )


def build_datasource(cfg: Config) -> DataSource:
    """Build a QuickSight DataSource from ``cfg.demo_database_url``.

    Dispatches on ``cfg.dialect``:

    - Postgres: ``Type="POSTGRESQL"`` + ``PostgreSqlParameters`` (port
      defaults 5432, database defaults ``postgres``).
    - Oracle: ``Type="ORACLE"`` + ``OracleParameters`` (port defaults
      1521, ``Database`` carries the service name / SID — accepted by
      QuickSight's create-data-source for either Easy Connect or
      SQLAlchemy-style URLs).
    - SQLite: not supported by AWS QuickSight as a datasource type.
      The SQLite dialect is the integrator's local-iteration storage
      for the X.2 self-hosted renderer (X.3.e), not a deployable
      QuickSight backend. Calling ``build_datasource`` against a SQLite
      config raises ``ValueError`` with a pointer to the local-loop
      docs.

    The DataSource ID derives from ``cfg.prefixed("demo-datasource")`` so
    when ``cfg.l2_instance_prefix`` is set (per-test harness, multi-tenant
    deploys) each gets its own unique ID. Credentials come from the
    parsed URL; SSL is enabled by default; principal_arns from cfg
    become QS Permissions.

    Raises ValueError if ``cfg.demo_database_url`` is unset, or if the
    dialect is SQLite (QuickSight has no SQLite datasource type).
    """
    if not cfg.demo_database_url:
        raise ValueError("demo_database_url is required to build a datasource")

    if cfg.dialect is Dialect.SQLITE:
        raise ValueError(
            "SQLite is not a deployable QuickSight datasource type — "
            "the SQLite dialect targets the local-iteration loop "
            "(see docs/integrator/local-loop.md). For QuickSight deploys, "
            "use 'dialect: postgres' or 'dialect: oracle' against an "
            "RDS-managed instance."
        )

    if cfg.dialect is Dialect.ORACLE:
        info = _parse_oracle_url(cfg.demo_database_url)
        ds_type = "ORACLE"
        params = DataSourceParameters(
            OracleParameters=OracleParameters(
                Host=info.host, Port=info.port, Database=info.database,
            ),
        )
        # RDS Oracle defaults to no TLS (option group needed to enable);
        # QuickSight's SSL probe closes the connection in ~2ms otherwise.
        # For Postgres, RDS forces SSL by default → DisableSsl=False
        # works.
        ssl = SslProperties(DisableSsl=True)
    else:
        info = _parse_pg_url(cfg.demo_database_url)
        ds_type = "POSTGRESQL"
        params = DataSourceParameters(
            PostgreSqlParameters=PostgreSqlParameters(
                Host=info.host, Port=info.port, Database=info.database,
            ),
        )
        ssl = SslProperties(DisableSsl=False)

    ds_id = cfg.prefixed("demo-datasource")

    permissions = None
    if cfg.principal_arns:
        permissions = [
            ResourcePermission(Principal=arn, Actions=_DATASOURCE_ACTIONS)
            for arn in cfg.principal_arns
        ]

    return DataSource(
        AwsAccountId=cfg.aws_account_id,
        DataSourceId=ds_id,
        Name=f"{cfg.resource_prefix} Demo DataSource",
        Type=ds_type,
        DataSourceParameters=params,
        Credentials=DataSourceCredentials(
            CredentialPair=CredentialPair(
                Username=info.user, Password=info.password,
            ),
        ),
        SslProperties=ssl,
        Permissions=permissions,
        Tags=cfg.tags(),
    )
