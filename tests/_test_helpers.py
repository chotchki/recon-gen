"""Shared test helpers (V.1.b).

Avoids the 17-copy `Config(aws_account_id="111122223333", ...)`
boilerplate scattered across tests/json + tests/unit. The values here
are intentionally placeholder — they're syntactically valid AWS
shapes but resolve to nothing.
"""

from __future__ import annotations

from typing import Any, cast

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

_TEST_ACCOUNT = "111122223333"
_TEST_REGION = "us-west-2"
_TEST_DATASOURCE_ARN = (
    f"arn:aws:quicksight:{_TEST_REGION}:{_TEST_ACCOUNT}:datasource/test-ds"
)


def _normalize_extra_tags(raw: Any) -> tuple[tuple[str, str], ...]:
    """Translate legacy ``extra_tags={"k": "v"}`` dict kwarg to the
    ``AwsConfig.extra_tags`` tuple-of-pairs shape."""
    if isinstance(raw, dict):
        items = cast(dict[str, str], raw).items()
        return tuple(sorted(items))
    if isinstance(raw, (list, tuple)):
        items_list = cast(list[tuple[str, str]] | tuple[tuple[str, str], ...], raw)
        return tuple(items_list)
    raise TypeError(
        f"extra_tags must be dict / list / tuple; got {type(raw).__name__}"
    )


def make_test_config(**overrides: Any) -> Config:
    """Return a Config preloaded with the canonical placeholder values.

    Any field can be overridden by keyword. Common cases:

    - ``aws_region="us-east-2"`` — pin the region to match a fixture
      (e.g. tests asserting on rendered ARNs).
    - ``deployment_name="recon-spec-example"`` — pin the QS resource
      prefix (Z.C). Default ``recon-test`` works for most tests; pin
      to a real deployment name when the test asserts on rendered IDs.
    - ``db_table_prefix="spec_example"`` — pin the DB table prefix
      (Z.C). Default ``test`` works when the test doesn't touch
      generated DB DDL; pin to a real prefix when it does.
    - ``dialect=Dialect.ORACLE`` — exercise the Oracle SQL branch.
    """
    # DE.5 steps 3-19 — translate every legacy flat kwarg into the nested
    # aws/db/app2/audit/test blocks on Config.
    from recon_gen.common.config import (  # noqa: PLC0415
        App2Config, AuditConfig, AwsConfig, DatasourceConfig, DbConfig,
        Dialect, TestConfig, TestGeneratorConfig,
    )
    account_id = overrides.pop("aws_account_id", _TEST_ACCOUNT)
    region = overrides.pop("aws_region", _TEST_REGION)
    deployment_name = overrides.pop("deployment_name", "recon-test")
    datasource_arn = overrides.pop("datasource_arn", None)
    principal_arns = overrides.pop("principal_arns", ())
    extra_tags_raw = overrides.pop("extra_tags", {})
    tagging_enabled = overrides.pop("tagging_enabled", True)
    qs_disable_pg_ssl = overrides.pop("qs_disable_pg_ssl", False)
    aws_pg_cluster_id = overrides.pop("aws_pg_cluster_id", None)
    aws_oracle_instance_id = overrides.pop("aws_oracle_instance_id", None)
    # DB-block legacy kwargs
    db_table_prefix = overrides.pop("db_table_prefix", "test")
    demo_database_url = overrides.pop("demo_database_url", None)
    dialect = overrides.pop("dialect", Dialect.POSTGRES)
    default_l2_instance = overrides.pop("default_l2_instance", None)
    app2_db_pool_size = overrides.pop("app2_db_pool_size", 10)
    # App2-block legacy kwargs
    etl_hook = overrides.pop("etl_hook", None)
    banner_text = overrides.pop("banner_text", None)
    app2_tls = overrides.pop("app2_tls", None)
    # Audit-block legacy kwargs
    signing = overrides.pop("signing", None)
    # Test-block legacy kwargs
    test_generator = overrides.pop("test_generator", None)
    if test_generator is None:
        test_generator = TestGeneratorConfig()
    if datasource_arn is None:
        if region != _TEST_REGION:
            datasource_arn = (
                f"arn:aws:quicksight:{region}:{account_id}:datasource/test-ds"
            )
        else:
            datasource_arn = _TEST_DATASOURCE_ARN

    base: dict[str, Any] = {
        "aws": AwsConfig(
            account_id=account_id,
            region=region,
            deployment_name=deployment_name,
            principal_arns=tuple(principal_arns),
            extra_tags=_normalize_extra_tags(extra_tags_raw),
            tagging_enabled=tagging_enabled,
            qs_disable_pg_ssl=qs_disable_pg_ssl,
            pg_cluster_id=aws_pg_cluster_id,
            oracle_instance_id=aws_oracle_instance_id,
            datasource=DatasourceConfig(
                mode=("adopt" if datasource_arn else "create"),
                arn=datasource_arn,
            ),
        ),
        "db": DbConfig(
            dialect=dialect,
            url=demo_database_url,
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
    # If caller passed `db=DbConfig(...)` AND legacy DB-block kwargs, merge
    # them — the explicit DbConfig's set fields win, but anything it didn't
    # touch comes from the legacy-kwarg-built default. Without this, callers
    # who pass both lose their legacy values to the bare override.
    import dataclasses as _dc  # noqa: PLC0415

    def _merge(block_name: str, block_cls: type[Any]) -> None:
        override = overrides.pop(block_name, None)
        if isinstance(override, block_cls):
            merged: dict[str, Any] = {}
            for f in _dc.fields(block_cls):
                ov = getattr(override, f.name)
                default = f.default if f.default is not _dc.MISSING else (
                    f.default_factory() if f.default_factory is not _dc.MISSING else None
                )
                merged[f.name] = ov if ov != default else getattr(base[block_name], f.name)
            base[block_name] = block_cls(**merged)

    _merge("db", DbConfig)
    _merge("app2", App2Config)
    base.update(overrides)
    return Config(**base)
