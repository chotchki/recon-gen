"""``python -m quicksight_gen.common.html`` — direct smoke runner.

Equivalent to ``quicksight-gen dashboards --port 8765``, but
without going through the Click CLI — no config.yaml required, no
``--l2`` resolution. Useful when iterating on the renderer / JS
without a checked-out config (the ``tests._test_helpers`` shim
fabricates one).

For anything operator-facing prefer the CLI subcommand:

    quicksight-gen dashboards -c config.yaml --l2 run/sasquatch_pr.yaml

The CLI version takes a real config + L2 instance, so the Sheet's
``app.cfg.l2_instance_prefix`` matches the prefix the X.2.a.4
DataFetcher will key the SQL off.
"""

from __future__ import annotations

import sys

import uvicorn

from quicksight_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from quicksight_gen.common.html.server import ServedDashboard, make_app
from tests._test_helpers import make_test_config


def main() -> int:
    cfg = make_test_config()
    tree_app, sheet = build_smoke_app(cfg)
    asgi_app = make_app(
        dashboards={
            "smoke": ServedDashboard(
                tree_app=tree_app,
                sheet=sheet,
                title="Smoke",
                data_fetcher=stub_money_trail_fetcher,
                filter_specs=SMOKE_FILTER_SPECS,
            ),
        },
        # Dev-log on for direct smoke runner: every HTMX event +
        # d3 click prints to stderr so the developer sees what
        # the browser is doing inline with the server log.
        dev_log=True,
    )
    print("App2 smoke server: http://127.0.0.1:8765/")
    uvicorn.run(asgi_app, host="127.0.0.1", port=8765, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
