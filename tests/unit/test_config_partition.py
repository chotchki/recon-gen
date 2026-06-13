"""Hotfix v8.7.3 — synthesized ARNs honor the AWS partition.

Standard commercial AWS uses the ``aws`` partition; GovCloud uses
``aws-us-gov``; China uses ``aws-cn``. Hardcoding ``aws`` in the ARN
synthesis sites (``Config.dataset_arn``, ``Config.theme_arn``,
``Config.__post_init__`` deriving ``datasource_arn`` from
``demo_database_url``) breaks every deploy against a non-commercial
partition — QuickSight rejects the synthesized resource ARNs.

Resolution order (see ``Config.aws.partition``): ``datasource_arn`` first
(authoritative when the customer supplied a pre-existing datasource),
then the first ``principal_arns`` entry, then ``aws`` as fallback.
"""

from __future__ import annotations

from typing import Any

from recon_gen.common.config import AwsConfig, Config, DatasourceConfig


def _cfg(**overrides: Any) -> Config:
    """Minimal Config with one of every required field; override per test."""
    # DE.5 steps 3-7 — translate flat aws_* / deployment_name /
    # datasource_arn / principal_arns kwargs.
    region = overrides.pop("aws_region", "us-east-1")
    account_id = overrides.pop("aws_account_id", "111122223333")
    deployment_name = overrides.pop("deployment_name", "recon-test")
    datasource_arn = overrides.pop(
        "datasource_arn",
        f"arn:aws:quicksight:{region}:{account_id}:datasource/x",
    )
    principal_arns = overrides.pop("principal_arns", ())
    base: dict[str, Any] = dict(
        aws=AwsConfig(
            account_id=account_id, region=region,
            deployment_name=deployment_name,
            principal_arns=tuple(principal_arns),
            datasource=DatasourceConfig(
                mode=("adopt" if datasource_arn else "create"),
                arn=datasource_arn,
            ),
        ),
        db_table_prefix="test",
    )
    base.update(overrides)
    return Config(**base)


def test_partition_defaults_to_aws_with_no_arn_sources() -> None:
    """No principal_arns, no datasource_arn → "aws" default."""
    cfg = Config(
        aws=AwsConfig(
            account_id="111122223333", region="us-east-1",
            deployment_name="recon-test",
        ),
        demo_database_url="postgresql://example",
        db_table_prefix="test",
    )
    assert cfg.aws.partition == "aws"


def test_partition_from_explicit_datasource_arn() -> None:
    """Explicit datasource_arn carries the authoritative partition."""
    cfg = _cfg(
        datasource_arn=(
            "arn:aws-us-gov:quicksight:us-gov-east-1"
            ":111122223333:datasource/x"
        ),
    )
    assert cfg.aws.partition == "aws-us-gov"


def test_partition_from_principal_arn_when_no_datasource_set() -> None:
    """When demo_database_url is set (no explicit datasource_arn), the
    partition derives from the principal_arn so the synthesized
    datasource_arn lands in the right partition."""
    cfg = Config(
        aws=AwsConfig(
            account_id="111122223333", region="us-gov-east-1",
            deployment_name="recon-test",
            principal_arns=(
                "arn:aws-us-gov:iam::111122223333:user/operator",
            ),
        ),
        demo_database_url="postgresql://example",
        db_table_prefix="test",
    )
    assert cfg.aws.partition == "aws-us-gov"
    # And the synthesized datasource_arn picks it up:
    assert cfg.aws.datasource.arn is not None
    assert cfg.aws.datasource.arn.startswith("arn:aws-us-gov:quicksight:")


def test_partition_china_partition() -> None:
    cfg = _cfg(
        datasource_arn=(
            "arn:aws-cn:quicksight:cn-north-1:111122223333:datasource/x"
        ),
    )
    assert cfg.aws.partition == "aws-cn"


def test_dataset_arn_uses_partition() -> None:
    cfg = _cfg(
        aws_region="us-gov-east-1",
        datasource_arn=(
            "arn:aws-us-gov:quicksight:us-gov-east-1"
            ":111122223333:datasource/x"
        ),
    )
    arn = cfg.aws.dataset_arn("my-dataset")
    assert arn == (
        "arn:aws-us-gov:quicksight:us-gov-east-1"
        ":111122223333:dataset/my-dataset"
    )


def test_theme_arn_uses_partition() -> None:
    cfg = _cfg(
        aws_region="us-gov-east-1",
        datasource_arn=(
            "arn:aws-us-gov:quicksight:us-gov-east-1"
            ":111122223333:datasource/x"
        ),
    )
    arn = cfg.aws.theme_arn("my-theme")
    assert arn == (
        "arn:aws-us-gov:quicksight:us-gov-east-1"
        ":111122223333:theme/my-theme"
    )


def test_datasource_arn_explicit_wins_over_principal() -> None:
    """Explicit datasource_arn is authoritative; principal_arn partition
    is the fallback when datasource_arn isn't set."""
    cfg = _cfg(
        # datasource explicitly in commercial...
        datasource_arn="arn:aws:quicksight:us-east-1:111122223333:datasource/x",
        # ...but principal in GovCloud (operator-side mistake; we trust
        # the resource ARN over the principal).
        principal_arns=["arn:aws-us-gov:iam::111122223333:user/operator"],
    )
    assert cfg.aws.partition == "aws"


def test_bare_string_principal_falls_through_to_default() -> None:
    """Defensive: an empty / malformed principal_arns entry doesn't
    leak through as a partition; default ``aws`` wins."""
    cfg = Config(
        aws=AwsConfig(
            account_id="111122223333", region="us-east-1",
            deployment_name="recon-test",
            principal_arns=("not-an-arn",),
        ),
        demo_database_url="postgresql://example",
        db_table_prefix="test",
    )
    assert cfg.aws.partition == "aws"


def test_empty_partition_segment_falls_through() -> None:
    """An ARN like ``arn::quicksight:...`` (empty partition slot)
    shouldn't be honored; default ``aws`` wins."""
    cfg = Config(
        aws=AwsConfig(
            account_id="111122223333", region="us-east-1",
            deployment_name="recon-test",
            principal_arns=("arn::iam::111122223333:user/operator",),
        ),
        demo_database_url="postgresql://example",
        db_table_prefix="test",
    )
    assert cfg.aws.partition == "aws"


def test_commercial_partition_unchanged_when_explicit() -> None:
    """Round-trip: commercial AWS still works when explicit."""
    cfg = _cfg(
        datasource_arn="arn:aws:quicksight:us-east-1:111122223333:datasource/x",
    )
    assert cfg.aws.partition == "aws"
    assert cfg.aws.dataset_arn("d").startswith("arn:aws:quicksight:")
    assert cfg.aws.theme_arn("t").startswith("arn:aws:quicksight:")
