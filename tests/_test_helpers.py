"""Shared test helpers (V.1.b).

Avoids the 17-copy `Config(aws_account_id="111122223333", ...)`
boilerplate scattered across tests/json + tests/unit. The values here
are intentionally placeholder — they're syntactically valid AWS
shapes but resolve to nothing.
"""

from __future__ import annotations

from typing import Any

from quicksight_gen.common.config import Config

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
    - ``deployment_name="qsgen-spec-example"`` — pin the QS resource
      prefix (Z.C). Default ``qsgen-test`` works for most tests; pin
      to a real deployment name when the test asserts on rendered IDs.
    - ``db_table_prefix="spec_example"`` — pin the DB table prefix
      (Z.C). Default ``test`` works when the test doesn't touch
      generated DB DDL; pin to a real prefix when it does.
    - ``dialect=Dialect.ORACLE`` — exercise the Oracle SQL branch.
    """
    base: dict[str, Any] = {
        "aws_account_id": _TEST_ACCOUNT,
        "aws_region": _TEST_REGION,
        # Z.C: required Config fields. Tests that don't assert on
        # rendered resource IDs / DB DDL accept these as no-op defaults.
        "deployment_name": "qsgen-test",
        "db_table_prefix": "test",
        "datasource_arn": _TEST_DATASOURCE_ARN,
    }
    # Region overrides cascade into the ARN unless the caller also
    # supplies datasource_arn explicitly.
    if "aws_region" in overrides and "datasource_arn" not in overrides:
        region = overrides["aws_region"]
        base["datasource_arn"] = (
            f"arn:aws:quicksight:{region}:{_TEST_ACCOUNT}:datasource/test-ds"
        )
    base.update(overrides)
    return Config(**base)
