"""Probe a deployed QuickSight dashboard for visual datasource errors.

QuickSight surfaces datasource errors (Oracle ORA-NNNNN, Postgres syntax
errors, connection timeouts, etc.) as a generic "Your database generated
a SQL exception" banner in the visual itself — the actual driver message
is only visible in the embedded iframe's JavaScript console as a
"Stream error occurred: {...}" payload. That makes the actual cause
invisible to the user clicking through the rendered dashboard.

This module walks every sheet of a deployed dashboard via Playwright +
the embed-URL flow used by the e2e harness, captures those Stream
errors as they fire, parses the JSON payload, and returns a per-sheet
summary so a CLI can print the actual driver message ("ORA-00904:
\"table_count\": invalid identifier") alongside the sheet that produced
it.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from playwright.sync_api import ConsoleMessage


class ProbedError(NamedTuple):
    """One QS Stream error captured while a sheet was active."""
    error_class: str  # e.g. "GENERIC_SQL_EXCEPTION"
    message: str  # e.g. "ORA-00904: \"table_count\": invalid identifier"


_STREAM_ERROR_JSON = re.compile(r"\{.*\}", re.DOTALL)


def _parse_stream_error(text: str) -> ProbedError | None:
    """Extract (error_class, internalMessage) from a Stream-error console line."""
    m = _STREAM_ERROR_JSON.search(text)
    if not m:
        return None
    try:
        payload: dict[str, Any] = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    hierarchy: list[dict[str, Any]] = payload.get("errorCodeHierarchyPrimitiveModel") or []
    error_class: str = next(
        (str(h.get("name", "?")) for h in hierarchy if h.get("type") == "ERROR"),
        "UNKNOWN",
    )
    msg: str = payload.get("internalMessage") or payload.get("error") or ""
    msg = msg.replace("\\n", " ").strip()
    if msg.endswith("https://docs.oracle.com/error-help/db/ora-00904/"):
        msg = msg.rsplit("https://", 1)[0].strip()
    return ProbedError(error_class=error_class, message=msg)


def probe_dashboard(
    *,
    aws_account_id: str,
    aws_region: str,
    dashboard_id: str,
    initial_settle_ms: int = 8000,
    sheet_settle_ms: int = 8000,
) -> dict[str, list[ProbedError]]:
    """Walk every sheet tab on a deployed dashboard, capture Stream errors per sheet.

    Returns a dict keyed by sheet display name. Sheets with no errors
    map to an empty list.
    """
    from recon_gen.common.browser.helpers import (
        click_sheet_tab,
        generate_dashboard_embed_url,
        get_sheet_tab_names,
        webkit_page,
    )

    url = generate_dashboard_embed_url(
        aws_account_id=aws_account_id,
        aws_region=aws_region,
        dashboard_id=dashboard_id,
    )

    captured: list[str] = []

    def grab(message: ConsoleMessage) -> None:
        text = message.text
        if "Stream error" in text and "internalMessage" in text:
            captured.append(text)

    results: dict[str, list[ProbedError]] = {}
    with webkit_page(headless=True) as page:
        page.on("console", grab)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(initial_settle_ms)
        tabs = get_sheet_tab_names(page)
        seen = 0
        for i, name in enumerate(tabs):
            if i > 0:
                click_sheet_tab(page, name, timeout_ms=20000)
                page.wait_for_timeout(sheet_settle_ms)
            new_msgs = captured[seen:]
            seen = len(captured)
            errors: list[ProbedError] = []
            for t in new_msgs:
                e = _parse_stream_error(t)
                if e is not None:
                    errors.append(e)
            results[name] = errors

    return results


def assert_no_datasource_errors(
    console_messages: list[str], *, context: str = "",
) -> None:
    """Scan a captured-console-message list for QS Stream errors that
    indicate a per-visual datasource failure (Oracle ORA-NNNNN,
    Postgres syntax error, etc.) and raise ``AssertionError`` listing
    every distinct ``(error_class, message)`` pair found.

    Designed for the e2e harness's ``run_dashboard_check_with_retry``
    capture sink — every Playwright session that walks a dashboard
    accumulates ``[<type>] <text>`` console lines, and this turns
    "QuickSight rendered the page but visuals threw datasource
    errors" from a silent-pass into a hard failure with the actual
    driver message.

    No-op if ``console_messages`` is empty or no Stream errors were
    captured.
    """
    errors: list[ProbedError] = []
    seen: set[tuple[str, str]] = set()
    for line in console_messages:
        if "Stream error" not in line or "internalMessage" not in line:
            continue
        e = _parse_stream_error(line)
        if e is None:
            continue
        key = (e.error_class, e.message)
        if key in seen:
            continue
        seen.add(key)
        errors.append(e)
    if not errors:
        return
    header = "QuickSight surfaced datasource errors during the dashboard render"
    if context:
        header = f"{header} ({context})"
    body = "\n".join(f"  [{e.error_class}] {e.message}" for e in errors)
    raise AssertionError(f"{header}:\n{body}")


def format_report(
    dashboard_id: str, results: dict[str, list[ProbedError]],
) -> str:
    """Render a probe result as a CLI-friendly text block."""
    lines = [f"== {dashboard_id} =="]
    total = sum(len(v) for v in results.values())
    if total == 0:
        lines.append("  (no datasource errors across any sheet)")
        return "\n".join(lines)
    for sheet, errs in results.items():
        if not errs:
            lines.append(f"  {sheet}: ok")
            continue
        lines.append(f"  {sheet}: {len(errs)} error(s)")
        for e in errs:
            lines.append(f"    [{e.error_class}] {e.message}")
    return "\n".join(lines)
