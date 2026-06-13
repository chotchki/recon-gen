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

_TEST_ACCOUNT = "111122223333"
_TEST_REGION = "us-west-2"
_TEST_DATASOURCE_ARN = (
    f"arn:aws:quicksight:{_TEST_REGION}:{_TEST_ACCOUNT}:datasource/test-ds"
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
    # DE.5 steps 3-7 — translate flat aws_* / deployment_name /
    # datasource_arn / principal_arns kwargs into nested aws=AwsConfig(...).
    from recon_gen.common.config import AwsConfig, DatasourceConfig  # noqa: PLC0415
    account_id = overrides.pop("aws_account_id", _TEST_ACCOUNT)
    region = overrides.pop("aws_region", _TEST_REGION)
    deployment_name = overrides.pop("deployment_name", "recon-test")
    datasource_arn = overrides.pop("datasource_arn", None)
    principal_arns = overrides.pop("principal_arns", ())
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
            datasource=DatasourceConfig(
                mode=("adopt" if datasource_arn else "create"),
                arn=datasource_arn,
            ),
        ),
        "db_table_prefix": "test",
    }
    base.update(overrides)
    return Config(**base)
