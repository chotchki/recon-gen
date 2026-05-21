"""AO.3 — Investigation getting-started institution name is persona-driven.

The welcome prose on the Investigation (AML) landing previously hardcoded
the demo institution name ("Sasquatch National Bank"), which (a) leaked
the Sasquatch persona onto persona-neutral instances like spec_example,
and (b) read as a placeholder on the examiner-facing compliance surface
(the v11.9.4 cold sweep's #2 / exec's #1 finding). The name must come from
the L2 ``persona:`` block when present and be neutral otherwise.

`tests/docs/test_docs_persona_neutral.py` scans the rendered mkdocs site,
NOT the dashboard analysis JSON — which is why this hardcode slipped. This
is the analogous gate for the dashboard analysis prose.
"""

from __future__ import annotations

import json
from pathlib import Path

from recon_gen.apps.investigation.app import build_investigation_app
from recon_gen.apps.investigation.datasets import build_all_datasets
from recon_gen.common.l2.loader import load_instance
from tests._test_helpers import make_test_config

_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


def _analysis_blob(l2_name: str) -> str:
    """Build the Investigation analysis for an L2 fixture → its JSON text."""
    inst = load_instance(_FIXTURES / l2_name)
    cfg = make_test_config(db_table_prefix="t")
    build_all_datasets(cfg, inst)
    app = build_investigation_app(cfg, l2_instance=inst)
    return json.dumps(app.emit_analysis().to_aws_json())


def test_neutral_instance_has_no_hardcoded_institution_name() -> None:
    """spec_example carries no ``persona:`` block, so the getting-started
    prose must NOT leak the Sasquatch demo name — it falls back to neutral
    language ("the shared base ledger")."""
    blob = _analysis_blob("spec_example.yaml")
    assert "Sasquatch National Bank" not in blob, (
        "spec_example (persona-neutral) leaks the Sasquatch institution "
        "name into the Investigation getting-started prose — the name "
        "must be persona-driven, not hardcoded."
    )
    assert "shared base ledger" in blob


def test_persona_instance_renders_its_institution_name() -> None:
    """sasquatch_pr declares ``persona.institution = [Sasquatch National
    Bank, SNB]``, so the getting-started prose renders that name (guards
    against over-neutralizing the persona path)."""
    blob = _analysis_blob("sasquatch_pr.yaml")
    assert "Sasquatch National Bank" in blob
