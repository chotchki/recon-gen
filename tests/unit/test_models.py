"""Residual config-shape tests.

DW.8.1 deleted the QuickSight emit graph from ``common/models.py`` (the
``to_aws_json()`` serialization layer + every AWS resource / definition /
field-well / visual / filter / control dataclass). The model-serialization
tests that pinned that graph — strip-nones, Theme / DataSet / Analysis /
Visual / Filter / Tag / DataSource serialization, ``cfg.aws.tags()`` —
died with it.

What survives here are the two config derivations that don't touch any
deleted dataclass: ``cfg.aws.prefixed()`` (resource-name prefixing) and
``cfg.aws.datasource.arn`` derivation. Both are dead-config candidates
that retire in the DE-phase config redesign, not DW; until then they keep
their coverage.
"""

from recon_gen.common.config import AwsConfig, Config, DbConfig
from tests._test_helpers import make_test_config


class TestConfigPrefixed:
    """Z.C — cfg.aws.prefixed() uses deployment_name as the single prefix
    segment (replaces v8.x's <resource_prefix>-<l2_instance_prefix>-...)."""

    def test_prefixed_uses_deployment_name(self):
        cfg = make_test_config(aws_deployment_name="recon-prod")
        assert cfg.aws.prefixed("l1-dashboard") == "recon-prod-l1-dashboard"

    def test_prefixed_lets_two_deployments_coexist(self):
        """The headline use case: same dashboard kind, different deployment."""
        cfg_a = make_test_config(aws_deployment_name="recon-sasquatch")
        cfg_b = make_test_config(aws_deployment_name="recon-wonkawash")
        assert cfg_a.aws.prefixed("l1-dashboard") != cfg_b.aws.prefixed("l1-dashboard")


class TestConfigDatasourceArnDerivation:
    def test_derived_from_demo_url(self):
        cfg = Config(
            # Z.C — required cfg fields.
            aws=AwsConfig(
                account_id="111122223333", region="us-west-2",
                deployment_name="recon-derived",
            ),
            db=DbConfig(table_prefix="derived", url="postgresql://u:p@h:5432/db"),
        )
        assert cfg.aws.datasource.arn == (
            "arn:aws:quicksight:us-west-2:111122223333:datasource/"
            f"{cfg.aws.deployment_name}-demo-datasource"
        )

    def test_explicit_arn_takes_precedence(self):
        from recon_gen.common.config import DatasourceConfig
        cfg = Config(
            aws=AwsConfig(
                account_id="111122223333", region="us-west-2",
                deployment_name="recon-explicit",
                datasource=DatasourceConfig(
                    mode="adopt",
                    arn="arn:aws:quicksight:us-west-2:111122223333:datasource/custom",
                ),
            ),
            db=DbConfig(table_prefix="explicit", url="postgresql://u:p@h:5432/db"),
        )
        assert cfg.aws.datasource.arn is not None
        assert "custom" in cfg.aws.datasource.arn

    def test_raises_without_arn_or_demo_url(self):
        import pytest
        with pytest.raises(ValueError, match="aws.datasource.arn"):
            Config(
                aws=AwsConfig(account_id="123", region="us-east-1", deployment_name="recon-fail-test"),
                db=DbConfig(table_prefix="fail_test"),
            )
