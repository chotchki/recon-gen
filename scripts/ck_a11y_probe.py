"""CK.0 a11y static probe — crawls every Studio surface via the
live HTTP server and reports per-page accessibility findings:

- `<main>` landmark presence + count (should be exactly 1)
- `<h1>` count (should be exactly 1)
- `<select>` elements missing aria-label / aria-labelledby
- Empty `<button>` elements without aria-label

The probe is intentionally static (regex over rendered HTML). It
does NOT replace a real axe-core run — it catches the structural
findings that the v13.1.1 audit flagged. axe-core integration is
gated on operator OK (CK.6) and would land separately.

Usage:
    .venv/bin/python scripts/ck_a11y_probe.py

Requires Studio running on http://127.0.0.1:8765/ — start with:
    .venv/bin/recon-gen studio -c run/config.duckdb.yaml \\
        --l2 run/sasquatch_pr.yaml --port 8765
"""

from __future__ import annotations

import re
from urllib.error import HTTPError
from urllib.request import urlopen

PAGES = (
    "/",
    "/diagram",
    "/data",
    "/etl/",
    "/etl/probe",
    "/etl/run",
    "/etl/triage",
    "/training/",
    "/l2_shape/account/",
    "/l2_shape/account_template/",
    "/l2_shape/rail/",
    "/l2_shape/transfer_template/",
    "/l2_shape/chain/",
    "/l2_shape/limit_schedule/",
    "/l2_shape/theme/",
    "/l2_shape/account/gl-1010-cash-due-frb/edit",
    "/l2_shape/account/new",
    "/l2_shape/rail/new",
    "/l2_shape/persona/",
)
BASE = "http://127.0.0.1:8765"


def fetch(path: str) -> str:
    """Fetch a page. Capture 4xx bodies (the CG.20 unknown-kind
    chrome lives behind a 404 status, so the plain urlopen path
    would lose its body to HTTPError)."""
    try:
        with urlopen(f"{BASE}{path}") as r:
            return r.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        return e.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return f"<!-- ERROR: {e} -->"


def scan(path: str, body: str) -> dict:
    issues: list[str] = []
    main_count = len(re.findall(r"<main\b", body))
    if main_count == 0:
        issues.append("missing <main> landmark")
    if main_count > 1:
        issues.append(f"{main_count} <main> landmarks (should be 1)")
    h1_count = len(re.findall(r"<h1\b", body))
    if h1_count == 0:
        issues.append("missing <h1>")
    if h1_count > 1:
        issues.append(f"{h1_count} <h1> elements (should be 1)")
    selects = re.findall(r"<select\b[^>]*>", body)
    unlabeled = [s for s in selects if "aria-label" not in s]
    if unlabeled:
        issues.append(
            f"{len(unlabeled)} <select> without aria-label "
            f"(first: {unlabeled[0][:120]!r})"
        )
    bare_buttons = re.findall(
        r"<button\b(?![^>]*aria-label)[^>]*>\s*</button>", body
    )
    if bare_buttons:
        issues.append(
            f"{len(bare_buttons)} empty buttons with no aria-label"
        )
    return {
        "path": path, "h1_count": h1_count,
        "main_count": main_count, "issues": issues,
    }


def main() -> None:
    rows = [scan(p, fetch(p)) for p in PAGES]
    print("# CK a11y static probe results\n")
    bad = 0
    for row in rows:
        print(f"## {row['path']}")
        print(f"- main_count: {row['main_count']}")
        print(f"- h1_count: {row['h1_count']}")
        if row["issues"]:
            bad += 1
            for i in row["issues"]:
                print(f"- WARN: {i}")
        else:
            print("- OK: no findings")
        print()
    print(f"\nPages with findings: {bad}/{len(rows)}")


if __name__ == "__main__":
    main()
