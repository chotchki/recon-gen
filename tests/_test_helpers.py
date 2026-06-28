"""Shared test helpers (V.1.b).

Avoids the 17-copy `Config(aws_account_id="111122223333", ...)`
boilerplate scattered across tests/json + tests/unit. The values here
are intentionally placeholder — they're syntactically valid AWS
shapes but resolve to nothing.
"""

from __future__ import annotations

from typing import Any

from recon_gen.common.config import Config


def fetch_one(
    conn_or_cursor: Any, sql: str, params: Any = None,
) -> tuple[Any, ...]:
    """Test helper: execute `sql` and return the first row as a tuple.

    Asserts the row exists (raises AssertionError when fetchone()
    returns None). Replaces the unsafe ``conn.execute(sql).fetchone()``
    pattern that pyright flags as ``reportOptionalSubscript`` —
    DuckDB's ``fetchone()`` returns ``tuple | None`` and indexing
    ``None`` is a runtime crash. CB.11.b-followup: introduced to
    unblock the runner's sessionstart pyright gate without sprinkling
    per-line ignore comments.

    Accepts either a connection or a cursor — both support
    ``.execute(sql).fetchone()`` (DuckDB connections return a fresh
    cursor; psycopg / oracledb cursors return self).

    ``params`` is a single positional arg (tuple/list/None) — mirrors
    the dbapi shape of ``cursor.execute(sql, params)``.
    """
    if params is not None:
        cursor = conn_or_cursor.execute(sql, params)
    else:
        cursor = conn_or_cursor.execute(sql)
    row = cursor.fetchone()
    assert row is not None, f"fetch_one: query returned no rows: {sql!r}"
    return row


def fetch_scalar(
    conn_or_cursor: Any, sql: str, params: Any = None,
) -> Any:
    """Like `fetch_one` but returns just the first column (row[0]).

    The most common shape in our test code:
    ``COUNT(*)`` / ``SUM(...)`` / single-cell aggregate.
    """
    return fetch_one(conn_or_cursor, sql, params)[0]


def make_test_config(**overrides: Any) -> Config:
    """Return a Config preloaded with the canonical placeholder values.

    Any field can be overridden by keyword. Kwarg names mirror the v14
    nested cfg path, dotted-to-underscored: ``aws.deployment_name`` →
    ``aws_deployment_name``, ``db.dialect`` → ``db_dialect``,
    ``app2.etl_hook`` → ``app2_etl_hook``, etc. Any future cfg field
    rename mechanically requires the matching test-helper kwarg rename
    — no v13-flat strangler shim insulating callsites from drift.

    Common cases:

    - ``aws_region="us-east-2"`` — pin region to match a fixture (e.g.
      tests asserting on rendered ARNs). ``aws_account_id`` likewise.
    - ``aws_deployment_name="recon-spec-example"`` — pin the QS
      resource prefix (Z.C). Default ``recon-test`` works for most
      tests; pin to a real deployment name when the test asserts on
      rendered IDs.
    - ``db_table_prefix="spec_example"`` — pin DB table prefix (Z.C).
    - ``db_dialect=Dialect.ORACLE`` — exercise the Oracle SQL branch.
    - ``db_url=":memory:"`` — point at a specific DB URL.

    Block-level kwargs (``db=DbConfig(...)``, ``app2=App2Config(...)``)
    REPLACE the relevant default block entirely; passing both a block
    AND its flattened fields raises ``TypeError`` (avoids ambiguity).
    """
    from recon_gen.common.config import (  # noqa: PLC0415
        App2Config, AuditConfig, AwsConfig, DbConfig,
        Dialect, TestConfig, TestGeneratorConfig,
    )
    # AwsConfig — only ``deployment_name`` survives post-DW. The legacy
    # aws_* kwargs (account_id / region / datasource / principal_arns /
    # tags / cluster ids) are accepted-and-ignored so existing callers
    # don't break; they name nothing now that QuickSight is gone.
    deployment_name = overrides.pop("aws_deployment_name", "recon-test")
    for _dead_aws_kwarg in (
        "aws_account_id", "aws_region", "aws_datasource_arn",
        "aws_principal_arns", "aws_extra_tags", "aws_tagging_enabled",
        "aws_qs_disable_pg_ssl", "aws_pg_cluster_id", "aws_oracle_instance_id",
    ):
        overrides.pop(_dead_aws_kwarg, None)
    # DbConfig fields
    db_table_prefix = overrides.pop("db_table_prefix", "test")
    db_url = overrides.pop("db_url", None)
    dialect = overrides.pop("db_dialect", Dialect.POSTGRES)
    default_l2_instance = overrides.pop("db_default_l2_instance", None)
    app2_db_pool_size = overrides.pop("db_app2_pool_size", 10)
    # App2Config fields
    etl_hook = overrides.pop("app2_etl_hook", None)
    banner_text = overrides.pop("app2_banner_text", None)
    app2_tls = overrides.pop("app2_tls", None)
    # AuditConfig fields
    signing = overrides.pop("audit_signing", None)
    # TestConfig fields
    test_generator = overrides.pop("test_generator", None)
    if test_generator is None:
        test_generator = TestGeneratorConfig()

    base: dict[str, Any] = {
        "aws": AwsConfig(deployment_name=deployment_name),
        "db": DbConfig(
            dialect=dialect,
            url=db_url,
            table_prefix=db_table_prefix,
            default_l2_instance=default_l2_instance,
            app2_pool_size=app2_db_pool_size,
        ),
        "app2": App2Config(
            etl_hook=etl_hook,
            banner_text=banner_text,
            tls=app2_tls,
        ),
        "audit": AuditConfig(signing=signing),
        "test": TestConfig(generator=test_generator),
    }
    # Block-level kwargs (`aws=AwsConfig(...)` / `db=DbConfig(...)` /
    # etc.) REPLACE the relevant default block entirely. Passing both
    # a block AND its flattened fields is ambiguous — we raise rather
    # than silently picking one.
    for block_name in ("aws", "db", "app2", "audit", "test"):
        if block_name in overrides:
            base[block_name] = overrides.pop(block_name)
    if overrides:
        raise TypeError(
            f"make_test_config: unknown kwargs {list(overrides)!r}. "
            f"Use v14-nested-flat names (e.g. `aws_deployment_name`, "
            f"`db_dialect`, `app2_etl_hook`) or pass full blocks "
            f"(e.g. `db=DbConfig(...)`). The v13-flat strangler shim "
            f"was retired post-DE.5 v14 audit."
        )
    return Config(**base)
