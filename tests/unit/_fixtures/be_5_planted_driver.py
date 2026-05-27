"""Planted "driver-side" fixture for BE.5's no-test-e2e-driver-internals smoke test.

Simulates a ``tests/e2e/_drivers/`` module that defines an
UPPER_SNAKE constant the smoke test wants the e2e-side fixture to
"accidentally" duplicate via an inline literal.

Per BE.0 D8: planted fixtures protect against silent lint death.
If the visitor regresses, the smoke goes red even when the real
e2e corpus has 0 hits (which is the BE.5 baseline at registration).

Both fixture files (`be_5_planted_driver.py` here + the paired
`be_5_planted_e2e_test.py`) sit under ``tests/unit/_fixtures/``,
which is excluded from the BE.2 lint's normal scope via the
``fixtures_dir`` filter in ``_build_checks`` — so they never
self-trip either lint instance during a real run.
"""

# A module-level UPPER_SNAKE constant — exactly the shape BE.5's
# index scans for. Value is long enough (3-200 chars per BE.2's
# filter range) + unique enough that it won't collide with any
# real production string.
PLANTED_DRIVER_CONSTANT = "be_5_planted_driver_sentinel_value"
