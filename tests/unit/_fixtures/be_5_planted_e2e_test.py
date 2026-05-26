"""Planted "e2e-test-side" fixture for BE.5's no-test-e2e-driver-internals smoke.

Asserts a literal value that's identical to ``PLANTED_DRIVER_CONSTANT``
in ``be_5_planted_driver.py`` — the smoke test invokes the lint's
visitor on this file (with src_root pointed at the fixtures dir)
and expects exactly 1 hit naming the driver-side fixture.

See ``be_5_planted_driver.py`` for the rationale.
"""


def _planted_assert() -> bool:
    """Inline-assert against the planted driver constant — should
    trip the BE.5 lint naming PLANTED_DRIVER_CONSTANT."""
    actual = "be_5_planted_driver_sentinel_value"
    assert actual == "be_5_planted_driver_sentinel_value", (
        "planted driver constant mismatch"
    )
    return True
