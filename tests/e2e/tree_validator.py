"""L.1.10.5 / X.2.q.3 — TreeValidator: typed walker that asserts a
deployed dashboard matches the source tree.

Walk ``(App, DashboardDriver)``; for every Sheet → Visual /
FilterControl / ParameterControl in the tree, assert the expected
element is in the rendered DOM. Per-kind dispatch extends naturally:
adding a new typed Visual subtype means adding one ``_validate_<kind>``
method — every existing test automatically gets the new check.

Replaces the per-app structural e2e boilerplate. Today's
``test_inv_dashboard_structure.py`` (and siblings) hand-list every
visual title + filter group ID + parameter name; under
``TreeValidator.validate_structure()`` the same coverage collapses to
one call because the tree IS the source of truth.

Usage:

    from tests.e2e.tree_validator import TreeValidator

    def test_investigation_dashboard_matches_tree(inv_app, qs_driver):
        qs_driver.open(inv_dashboard_id)
        TreeValidator(inv_app, qs_driver).validate_structure()

X.2.q.3 — speaks the ``DashboardDriver`` protocol (``goto_sheet`` /
``wait_loaded`` / ``visual_titles`` / ``filter_labels``), not Playwright
directly; the QS-vs-App2 mechanics are sealed in the driver.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quicksight_gen.common.tree import (
    App,
    Sheet,
    VisualLike,
)
from quicksight_gen.common.tree.actions import Drill
from tests.e2e._drivers import DashboardDriver


def enumerate_cross_sheet_left_click_drills(
    app: App,
) -> list[tuple[Sheet, VisualLike, Sheet]]:
    """Walk every visual's actions; yield each cross-sheet, left-click
    `Drill` as a `(source_sheet, source_visual, target_sheet)` tuple.

    "Cross-sheet" = `target_sheet is not source_sheet`. Same-sheet
    drills (the mutual-filter pattern) are filtered out — clicking
    doesn't change the sheet, so the "wait for tab to switch" witness
    wouldn't apply.

    "Left-click" = `trigger == "DATA_POINT_CLICK"`. Right-click menu
    drills (`DATA_POINT_MENU`) need a different DOM driver and are
    skipped here.

    Returns a list (not a generator) so `pytest.mark.parametrize` can
    consume it directly without exhausting on first call. Pure tree
    walk — no driver / DOM.
    """
    out: list[tuple[Sheet, VisualLike, Sheet]] = []
    if app.analysis is None:
        return out
    for sheet in app.analysis.sheets:
        for visual in sheet.visuals:
            for action in getattr(visual, "actions", []) or []:
                if not isinstance(action, Drill):
                    continue
                if action.trigger != "DATA_POINT_CLICK":
                    continue
                target = action.target_sheet
                if not isinstance(target, Sheet) or target is sheet:
                    continue
                out.append((sheet, visual, target))
    return out


def _control_title(control: object) -> str | None:
    """Resolve the visible title of a tree filter / parameter control.

    Direct controls carry their own ``.title``. Cross-sheet filter
    controls (`FilterCrossSheet`) inherit the title from the referenced
    filter's `default_control` (multi-sheet filters set this in the
    `FilterGroup.with_*` factories so the per-sheet widget shows the
    same label across sheets).
    """
    title = getattr(control, "title", None)
    if title:
        return str(title)
    inner_filter = getattr(control, "filter", None)
    if inner_filter is None:
        return None
    default_control = getattr(inner_filter, "default_control", None)
    if default_control is None:
        return None
    title = getattr(default_control, "title", None)
    return str(title) if title else None


@dataclass
class ValidationFailure:
    """One mismatch between tree and DOM."""
    where: str            # e.g. "Sheet 'Account Network' / Visual 'Flagged'"
    message: str          # human-readable description of the mismatch


@dataclass
class TreeValidator:
    """Walk a tree + a deployed dashboard via a ``DashboardDriver``;
    assert they match.

    All assertion methods collect into ``self.failures`` so a single
    validation run surfaces every mismatch at once, not just the first.
    Call ``.raise_if_failed()`` at the end to convert accumulated
    failures into an exception with the full list.

    Scope note (X.2.q.3): the original walker also asserted "≥
    ``len(sheet.visuals)`` visual *containers* rendered" (catching a
    visual that renders untitled). That count check is dropped here —
    `DashboardDriver` exposes titles, not raw container counts — so
    `validate_structure` is now "every *declared visual title* renders +
    every *declared control label* renders". Re-add a `visual_count()`
    verb + the count check if a regression motivates it.
    """
    app: App
    driver: DashboardDriver
    timeout_ms: int = 30_000
    failures: list[ValidationFailure] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Top-level entry points
    # ------------------------------------------------------------------

    def validate_structure(self) -> None:
        """Persona-agnostic structural check — every sheet's visual
        titles + control labels are present in the DOM. The one-call
        replacement for per-app ``test_*_dashboard_structure.py``
        boilerplate."""
        if self.app.analysis is None:
            self._fail("App", "App has no Analysis — nothing to validate.")
            self.raise_if_failed()
            return
        for sheet in self.app.analysis.sheets:
            self.validate_sheet(sheet)
        self.raise_if_failed()

    def validate_sheet(self, sheet: Sheet) -> None:
        """Navigate to ``sheet`` and assert its contents are in the DOM."""
        try:
            self.driver.goto_sheet(sheet.name)
        except Exception as e:  # noqa: BLE001
            self._fail(
                f"Sheet {sheet.name!r}",
                f"Couldn't navigate to sheet tab: {e!r}",
            )
            return

        # Visual titles — factory-wrapper visuals may not expose one.
        expected_titles = {
            v.title for v in sheet.visuals if getattr(v, "title", None)
        }
        for title in expected_titles:
            try:
                self.driver.wait_loaded(title, timeout_ms=self.timeout_ms)
            except Exception:  # noqa: BLE001 — collect via the diff below
                pass
        if expected_titles:
            rendered = set(self.driver.visual_titles())
            missing = expected_titles - rendered
            if missing:
                self._fail(
                    f"Sheet {sheet.name!r}",
                    f"Missing visual titles: {sorted(missing)} "
                    f"(rendered: {sorted(rendered)})",
                )

        for visual in sheet.visuals:
            self.validate_visual(sheet, visual)

        self.validate_sheet_controls(sheet)

    def validate_sheet_controls(self, sheet: Sheet) -> None:
        """Walk this sheet's `filter_controls` + `parameter_controls`
        and assert each control's title is in the rendered DOM."""
        filter_ctrls = getattr(sheet, "filter_controls", None) or []
        param_ctrls = getattr(sheet, "parameter_controls", None) or []
        expected = {
            t for t in (_control_title(c) for c in filter_ctrls + param_ctrls)
            if t
        }
        if not expected:
            return
        rendered = set(self.driver.filter_labels())
        if not rendered:
            self._fail(
                f"Sheet {sheet.name!r}",
                "Expected sheet controls but none rendered.",
            )
            return
        missing = expected - rendered
        if missing:
            self._fail(
                f"Sheet {sheet.name!r}",
                f"Missing sheet control titles: {sorted(missing)} "
                f"(rendered: {sorted(rendered)})",
            )

    def validate_visual(self, sheet: Sheet, visual: VisualLike) -> None:
        """Per-kind dispatch. Each typed Visual subtype has a
        corresponding ``_validate_<kind>`` method; unknown kinds fall
        back to the generic title-present check (already run at the
        sheet level)."""
        kind = getattr(visual, "_AUTO_KIND", None)
        if kind is None:
            return
        method = getattr(self, f"_validate_{kind}", None)
        if method is not None:
            method(sheet, visual)

    # ------------------------------------------------------------------
    # Per-kind checks — extension points; "the title rendered" is the
    # sheet-level check's responsibility. Add kind-specific DOM shape
    # verification (Sankey ribbon counts, KPI numeric text, …) when a
    # regression calls for it.
    # ------------------------------------------------------------------

    def _validate_kpi(self, sheet: Sheet, kpi: object) -> None:
        pass

    def _validate_table(self, sheet: Sheet, table: object) -> None:
        pass

    def _validate_bar(self, sheet: Sheet, bar: object) -> None:
        pass

    def _validate_sankey(self, sheet: Sheet, sankey: object) -> None:
        pass

    # ------------------------------------------------------------------
    # Failure handling
    # ------------------------------------------------------------------

    def _fail(self, where: str, message: str) -> None:
        self.failures.append(ValidationFailure(where=where, message=message))

    def raise_if_failed(self) -> None:
        if not self.failures:
            return
        lines = ["TreeValidator found mismatches:"]
        for f in self.failures:
            lines.append(f"  [{f.where}] {f.message}")
        raise AssertionError("\n".join(lines))
