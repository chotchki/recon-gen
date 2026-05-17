"""E2E gate: audit PDF render + verify cycle against the live DB.

Wraps the legacy CI workflow steps (`recon-gen audit apply --execute
-o <pdf>` + `audit verify <pdf>`) as pytest so the runner's `db` layer
can dispatch them. Reads cfg + L2 from `RECON_GEN_CONFIG` /
`RECON_GEN_TEST_L2_INSTANCE`, picking up whatever prefix the variant's
synthesized yaml resolved to.

Y.2.gate.k.1.absorb-audit (Phase 2.5): the workflow's audit-PDF step
broke after k.1.absorb landed because it queried the cfg-default
`spec_example_*` prefix while the runner had created tables under
`<spec.name>_*`. Moving the render+verify cycle into the runner's `db`
layer subprocess fixes the prefix-discovery class of bug for good — the
test inherits the same env the runner's seed flow used.

Two phases:
1. ``audit apply --execute`` queries every L1 invariant matview +
   renders a regulator-ready PDF via reportlab.
2. ``audit verify`` recomputes the embedded ProvenanceFingerprint from
   current sources and asserts every per-source SHA256 matches what the
   PDF baked in.

A failure at phase 1 means render-time bug (DB query, reportlab,
provenance computation). A failure at phase 2 means provenance drift
between embed-time and verify-time — usually a code change that
re-orders something the fingerprint hashes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from recon_gen.common.env_keys import (
    EnvVarInvalid,
    RECON_GEN_CONFIG,
    RECON_GEN_TEST_L2_INSTANCE,
)


_VENV_BIN = Path(__file__).resolve().parents[2] / ".venv" / "bin"
_QS_GEN = _VENV_BIN / "recon-gen"


def _resolve_cfg_path() -> Path:
    """Same cfg-resolution shape the sibling smoke tests use — env
    override wins, else the runner's per-variant cfg-discovery path."""
    try:
        explicit = RECON_GEN_CONFIG.get_or_none()
    except EnvVarInvalid:
        explicit = None
    if explicit is not None:
        return Path(explicit)
    candidates = (
        Path("config.yaml"),
        Path("run/config.yaml"),
        Path("run/config.postgres.yaml"),
        Path("run/config.oracle.yaml"),
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    pytest.skip("no cfg discoverable (set RECON_GEN_CONFIG or place run/config.*.yaml)")


def _resolve_l2_path() -> Path | None:
    """The runner sets RECON_GEN_TEST_L2_INSTANCE on every variant
    subprocess. When unset (legacy path), fall through to the audit
    CLI's own default-L2 resolution."""
    override = RECON_GEN_TEST_L2_INSTANCE.get_or_none()
    return Path(override) if override is not None else None


def test_audit_apply_renders_pdf(tmp_path: Path) -> None:
    """``audit apply --execute -o <pdf>`` produces a non-empty PDF.

    The render path queries every L1 invariant matview that the
    variant's seed populated (via the variant's prefix). Failure at
    this step usually means a SQL bug, an empty matview, or a
    reportlab regression.
    """
    cfg = _resolve_cfg_path()
    l2 = _resolve_l2_path()
    pdf = tmp_path / "report.pdf"

    cmd = [str(_QS_GEN), "audit", "apply", "-c", str(cfg), "--execute", "-o", str(pdf)]
    if l2 is not None:
        cmd += ["--l2", str(l2)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, (
        f"audit apply failed (rc={result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
    assert pdf.exists(), f"audit apply succeeded but {pdf} not written"
    assert pdf.stat().st_size > 0, f"{pdf} written but empty"


def test_audit_verify_recomputed_fingerprint_matches(tmp_path: Path) -> None:
    """``audit verify <pdf>`` re-derives the ProvenanceFingerprint from
    current sources + asserts byte-equality with what the PDF baked in.

    Drift here = code change that re-ordered or reformatted a hashed
    input between PDF emit time and verify time. PDF render must
    happen first (this test re-runs apply rather than depending on
    the apply test's tmp_path so it can be invoked standalone)."""
    cfg = _resolve_cfg_path()
    l2 = _resolve_l2_path()
    pdf = tmp_path / "report.pdf"

    apply_cmd = [str(_QS_GEN), "audit", "apply", "-c", str(cfg), "--execute", "-o", str(pdf)]
    if l2 is not None:
        apply_cmd += ["--l2", str(l2)]
    apply_rc = subprocess.run(apply_cmd, capture_output=True, text=True, check=False)
    assert apply_rc.returncode == 0, (
        f"audit apply (verify-prerequisite) failed:\n{apply_rc.stderr}"
    )

    verify_cmd = [str(_QS_GEN), "audit", "verify", str(pdf), "-c", str(cfg)]
    if l2 is not None:
        verify_cmd += ["--l2", str(l2)]
    verify_rc = subprocess.run(verify_cmd, capture_output=True, text=True, check=False)
    assert verify_rc.returncode == 0, (
        f"audit verify failed (rc={verify_rc.returncode}):\n"
        f"--- stdout ---\n{verify_rc.stdout}\n"
        f"--- stderr ---\n{verify_rc.stderr}"
    )
