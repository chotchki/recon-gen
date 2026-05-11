"""X.2.q — ``App2Driver``: the ``DashboardDriver`` for the self-hosted
HTMX/d3 renderer.

App 2's DOM is deliberately simple — visuals are
``section[data-visual-kind]`` blocks with an ``<h2>`` title and a
``.visual-data`` swap target; tables are plain ``<table class="table-data">``
(no virtualization); the filter form is ``#filter-form`` with
``data-widget``-marked controls. So most verbs are a direct DOM read or
a click + ``networkidle`` wait — no QS-quirk gymnastics.

The ``.smoke()`` factory spins a local App 2 server (the smoke app +
deterministic stub fetcher — no DB, no AWS) and a WebKit page; a future
``.against(url)`` factory will point at an already-running server. The
spike (X.2.q.0) implements the verbs the ported test needs; the rest
raise ``NotImplementedError("X.2.q.2")`` until the full impl lands.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from quicksight_gen.common.browser.helpers import webkit_page
from quicksight_gen.common.config import Config
from quicksight_gen.common.html._smoke_app import (
    build_smoke_app,
    stub_money_trail_fetcher,
)
from tests._test_helpers import make_test_config
from tests.e2e._harness_html2 import html2_server


_TODO = "X.2.q.2 — App2Driver verb not implemented yet"


class App2Driver:
    """``DashboardDriver`` over a running App 2 server + a WebKit page.

    Construct via a factory (``App2Driver.smoke()``), not directly —
    the factory owns the server + browser lifecycle as a context
    manager.
    """

    dialect = "app2"

    def __init__(self, *, base_url: str, page: Any) -> None:
        self._base = base_url.rstrip("/")
        self._page = page
        self._dashboard: str | None = None
        self._sheet: str | None = None

    # -- factories -------------------------------------------------------

    @classmethod
    @contextlib.contextmanager
    def smoke(cls, cfg: Config | None = None) -> Iterator["App2Driver"]:
        """Spin a local App 2 server serving the smoke app + the stub
        fetcher, open a WebKit page, yield the driver, tear both down."""
        cfg = cfg or make_test_config()
        tree_app, sheet = build_smoke_app(cfg)
        with html2_server(
            tree_app=tree_app, sheet=sheet,
            data_fetcher=stub_money_trail_fetcher,
            dashboard_id="smoke", dashboard_title="Smoke",
        ) as url, webkit_page() as page:
            yield cls(base_url=url, page=page)

    # -- navigation ------------------------------------------------------

    def open(self, dashboard: str, sheet: str | None = None) -> None:
        self._dashboard = dashboard
        path = f"/dashboards/{dashboard}"
        if sheet is not None:
            path += f"/sheets/{sheet}"
        self._sheet = sheet
        self._page.goto(self._base + path)
        self._page.wait_for_load_state("networkidle")

    def goto_sheet(self, name: str) -> None:
        raise NotImplementedError(_TODO)

    # -- reads -----------------------------------------------------------

    def _section(self, visual_title: str) -> Any:
        """Locator for the ``section[data-visual-kind]`` whose ``<h2>``
        text is exactly ``visual_title``."""
        return self._page.locator(
            f'section[data-visual-kind]:has(h2:text-is("{visual_title}"))'
        ).first

    def visual_titles(self) -> list[str]:
        return [
            t.strip()
            for t in self._page.locator(
                "section[data-visual-kind] h2"
            ).all_inner_texts()
        ]

    def wait_loaded(
        self, visual_title: str, *, timeout_ms: int = 15_000,
    ) -> None:
        section = self._section(visual_title)
        # The .visual-data swap target is filled by HTMX with a chart /
        # table / KPI after the per-visual GET — wait for *something*
        # inside it.
        section.locator(
            ".visual-data table, .visual-data svg, .visual-data .kpi-value"
        ).first.wait_for(state="visible", timeout=timeout_ms)

    def table_rows(self, visual_title: str) -> list[dict[str, str]]:
        section = self._section(visual_title)
        table = section.locator("table.table-data").first
        table.wait_for(state="visible")
        # Header <th>s carry a sort badge (▲/▼) + a clickable <a>; the
        # column name is the leading text token.
        headers = [
            h.split("\n")[0].strip().rstrip("▲▼ ").strip()
            for h in table.locator("thead th").all_inner_texts()
        ]
        rows: list[dict[str, str]] = []
        for tr in table.locator("tbody tr").all():
            cells = [c.strip() for c in tr.locator("td").all_inner_texts()]
            rows.append(dict(zip(headers, cells, strict=False)))
        return rows

    def kpi_value(self, visual_title: str) -> str | None:
        section = self._section(visual_title)
        loc = section.locator(".kpi-value").first
        if loc.count() == 0:
            return None
        return loc.inner_text().strip()

    # -- writes ----------------------------------------------------------

    def pick_filter(self, label: str, values: Sequence[str]) -> None:
        raise NotImplementedError(_TODO)

    def set_date_range(self, from_: str | None, to: str | None) -> None:
        raise NotImplementedError(_TODO)

    def set_slider(
        self, label: str, lo: float | None, hi: float | None,
    ) -> None:
        raise NotImplementedError(_TODO)

    def clear_filters(self) -> None:
        raise NotImplementedError(_TODO)

    def cross_link(self, label: str) -> None:
        raise NotImplementedError(_TODO)

    # -- artifacts -------------------------------------------------------

    def screenshot(self, path: str | Path | None = None) -> bytes:
        png: bytes = self._page.screenshot(full_page=True)
        if path is not None:
            Path(path).write_bytes(png)
        return png

    # -- lifecycle -------------------------------------------------------

    def close(self) -> None:
        # The server + page are owned by the context manager in the
        # factory (``.smoke()``); nothing to do here.
        pass
