"""Browser test: L2FT metadata cascade narrows but does not empty the table.

Ported onto the ``DashboardDriver`` protocol (X.2.q.3 — no Playwright
in the test body).

Stays ``@pytest.mark.skip`` for the same reason as before: X.1.b
replaced the Metadata Value dropdown with a ``ParameterTextField``, so
the cascade-source regression class (LinkedValues + MULTI_SELECT writing
back ``__placeholder__``) is structurally unreachable on the new shape.
A rewrite would need a typing helper + a known-good metadata value
pulled from the matview at runtime — queued as X.1.g.11.
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


@pytest.mark.skip(
    reason=(
        "Test drives Metadata Value as a dropdown, but X.1.b replaced "
        "it with a text field. The cascade-source regression class is "
        "structurally unreachable on the text-field shape; rewrite (or "
        "delete) is queued as X.1.g.11."
    ),
)
def test_metadata_value_pick_does_not_empty_transactions_table(
    qs_driver, l2ft_dashboard_id,
):
    """Picking a (Key, Value) pair must leave the Transactions table
    with > 0 rows — the v8.6.5 cascade-source regression class.

    Picks a Metadata Key with at least one matching Value in the cascade-
    refreshed dropdown; skips with an informational message if no such
    pair exists for the deployed L2.
    """
    qs_driver.open(l2ft_dashboard_id, sheet="Rails")
    qs_driver.wait_loaded("Transactions")
    before = len(qs_driver.table_rows("Transactions"))
    assert before > 0, (
        f"Transactions table must have rows pre-filter, got {before}"
    )

    key_options = qs_driver.filter_options("Metadata Key")
    if not key_options:
        pytest.skip(
            "Deployed L2 instance declares no metadata keys — "
            "the cascade test has nothing to exercise."
        )
    chosen_key = key_options[0]
    qs_driver.pick_filter("Metadata Key", [chosen_key])

    value_options = qs_driver.filter_options("Metadata Value")
    if not value_options:
        pytest.skip(
            f"Metadata Value dropdown empty after picking key "
            f"{chosen_key!r} — no values declared for this key in the "
            f"deployed L2."
        )
    chosen_value = value_options[0]
    qs_driver.pick_filter("Metadata Value", [chosen_value])

    after = len(qs_driver.table_rows("Transactions"))
    qs_driver.screenshot()
    assert after > 0, (
        f"Transactions table emptied after picking "
        f"({chosen_key}={chosen_value}); regression of v8.6.5 cascade-"
        f"source write-back bug. before={before}, after={after}"
    )
