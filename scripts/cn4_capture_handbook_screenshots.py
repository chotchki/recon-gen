#!/usr/bin/env python3
"""CN.4 Phase C — capture per-sheet screenshots for handbook-prose validation.

Spins up `recon-gen dashboards` against the `spec_example` fixture +
a fresh DuckDB demo DB, then walks every (dashboard_id, sheet_id)
combination and saves a full-page PNG to
`docs/audits/cn_4_screenshots/<dashboard>/<sheet>.png`.

The screenshots feed CN.4 Phase C — the revision pass for the 17
flagged handbook pages — so each revision agent can compare its draft
against what an operator actually sees on screen, per the
[[feedback_cold_read_iterative_screenshots]] contract.

Run from repo root:
    .venv/bin/python scripts/cn4_capture_handbook_screenshots.py
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

from playwright.sync_api import sync_playwright


REPO = Path(__file__).resolve().parents[1]
CFG_PATH = Path("/tmp/cn4_capture.yaml")
DB_PATH = Path("/tmp/cn4_capture.duckdb")
L2_PATH = REPO / "tests" / "l2" / "spec_example.yaml"
OUT_DIR = REPO / "docs" / "audits" / "cn_4_screenshots"

CFG_YAML = f"""\
deployment_name: cn4
db_table_prefix: cn4
dialect: duckdb
demo_database_url: duckdb:///{DB_PATH}
aws_account_id: "000000000000"
aws_region: us-east-1
"""

SHEETS: list[tuple[str, str]] = [
    # (dashboard_id, sheet_id)
    ("l1_dashboard", "l1-sheet-getting-started"),
    ("l1_dashboard", "l1-sheet-drift"),
    ("l1_dashboard", "l1-sheet-drift-timelines"),
    ("l1_dashboard", "l1-sheet-overdraft"),
    ("l1_dashboard", "l1-sheet-limit-breach"),
    ("l1_dashboard", "l1-sheet-pending-aging"),
    ("l1_dashboard", "l1-sheet-unbundled-aging"),
    ("l1_dashboard", "l1-sheet-supersession-audit"),
    ("l1_dashboard", "l1-sheet-exceptions"),
    ("l1_dashboard", "l1-sheet-daily-statement"),
    ("l1_dashboard", "l1-sheet-transactions"),
    ("l1_dashboard", "l1-sheet-app-info"),
    ("l2_flow_tracing", "l2ft-sheet-getting-started"),
    ("l2_flow_tracing", "l2ft-sheet-rails"),
    ("l2_flow_tracing", "l2ft-sheet-chains"),
    ("l2_flow_tracing", "l2ft-sheet-transfer-templates"),
    ("l2_flow_tracing", "l2ft-sheet-l2-exceptions"),
    ("l2_flow_tracing", "l2ft-sheet-app-info"),
    ("investigation", "inv-sheet-getting-started"),
    ("investigation", "inv-sheet-fanout"),
    ("investigation", "inv-sheet-anomalies"),
    ("investigation", "inv-sheet-money-trail"),
    ("investigation", "inv-sheet-account-network"),
    ("investigation", "inv-sheet-app-info"),
    ("executives", "exec-sheet-getting-started"),
    ("executives", "exec-sheet-program-health"),
    ("executives", "exec-sheet-account-coverage"),
    ("executives", "exec-sheet-transaction-volume"),
    ("executives", "exec-sheet-money-moved"),
    ("executives", "exec-sheet-app-info"),
]


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_port_open(host: str, port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            try:
                s.connect((host, port))
                return
            except OSError:
                time.sleep(0.2)
    raise TimeoutError(f"server at {host}:{port} did not open within {timeout}s")


def _build_demo() -> None:
    print(f"[setup] writing cfg → {CFG_PATH}")
    CFG_PATH.write_text(CFG_YAML)
    if DB_PATH.exists():
        DB_PATH.unlink()
    venv_recon = REPO / ".venv" / "bin" / "recon-gen"
    for verb in ("schema", "data"):
        print(f"[setup] recon-gen {verb} apply...")
        subprocess.run(
            [
                str(venv_recon), verb, "apply",
                "--l2", str(L2_PATH),
                "-c", str(CFG_PATH),
                "--execute",
            ],
            check=True, capture_output=True,
        )
    print("[setup] recon-gen data refresh...")
    subprocess.run(
        [
            str(venv_recon), "data", "refresh",
            "--l2", str(L2_PATH),
            "-c", str(CFG_PATH),
            "--execute",
        ],
        check=True, capture_output=True,
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _build_demo()

    port = _free_port()
    print(f"[serve] starting recon-gen dashboards on port {port}...")
    venv_recon = REPO / ".venv" / "bin" / "recon-gen"
    server = subprocess.Popen(
        [
            str(venv_recon), "dashboards",
            "--l2", str(L2_PATH),
            "-c", str(CFG_PATH),
            "--port", str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        env={**os.environ},
    )
    try:
        _wait_port_open("127.0.0.1", port, timeout=30.0)
        print(f"[serve] up at http://127.0.0.1:{port}")
        time.sleep(1.0)  # let app warm

        captured = 0
        with sync_playwright() as p:
            browser = p.webkit.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            for dash, sheet in SHEETS:
                url = f"http://127.0.0.1:{port}/dashboards/{dash}/sheets/{sheet}"
                out = OUT_DIR / dash / f"{sheet}.png"
                out.parent.mkdir(parents=True, exist_ok=True)
                try:
                    page.goto(url, wait_until="networkidle", timeout=20_000)
                    time.sleep(0.5)  # let any deferred renders settle
                    page.screenshot(path=str(out), full_page=True)
                    captured += 1
                    print(f"[capture] {dash}/{sheet} → {out.relative_to(REPO)}")
                except Exception as exc:  # pyright: ignore[reportBroadExceptionUsage]
                    print(f"[capture] FAILED {dash}/{sheet}: {exc}")
            browser.close()

        print(f"[done] captured {captured}/{len(SHEETS)} sheets to {OUT_DIR}")
        return 0 if captured == len(SHEETS) else 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    sys.exit(main())
