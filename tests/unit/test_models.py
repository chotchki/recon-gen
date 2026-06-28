"""Residual config-shape tests.

DW.8.1 deleted the QuickSight emit graph from ``common/models.py`` (the
``to_aws_json()`` serialization layer + every AWS resource / definition /
field-well / visual / filter / control dataclass). The model-serialization
tests that pinned that graph — strip-nones, Theme / DataSet / Analysis /
Visual / Filter / Tag / DataSource serialization, ``cfg.aws.tags()`` —
died with it.

What survives here is the one config derivation that doesn't touch any
deleted dataclass: ``cfg.aws.prefixed()`` (resource-name prefixing). The
datasource-ARN-synthesis derivations died with the QuickSight deploy path
in the dead-config sweep — ``AwsConfig`` keeps only ``deployment_name``.
"""

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
