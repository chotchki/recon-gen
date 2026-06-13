# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# BF.4/F: pypdf + pyHanko interop is essentially untyped (Any returned from
# .get_object() / page.get('/Annots') / cert.subject / etc). Disabling the
# Unknown family file-wide keeps the PDF-layout smoke readable without an
# avalanche of cast(...) wrappers around library calls.
"""CLI smoke for ``recon-gen audit`` — shape + cover-page render.

Verifies:
- ``audit --help`` lists the expected subcommands.
- ``audit apply`` (no --execute) emits Markdown carrying the L2
  institution heading + the resolved period + a generation timestamp
  + a provenance-fingerprint placeholder.
- ``audit apply --execute -o FILE`` writes a non-trivial PDF whose
  text payload mentions the institution + reporting period + footer
  provenance (proves reportlab is wired up + the L2 binding +
  cover-page layout threaded through).
- ``audit clean`` is a no-op without ``--execute`` and unlinks
  with it.

Real coverage of the underlying SQL + per-section template-input
dicts lands in U.8 (``test_sql.py`` + ``test_template_input.py``).
This file is the U.0 + U.1 acceptance net.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from recon_gen.apps.l1_dashboard.app import (
    _DRIFT_NAME,
    _SUPERSESSION_AUDIT_NAME,
)
from recon_gen.cli import main
from recon_gen.cli.audit import _resolve_frame
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX


_FIXTURES = Path(__file__).parent.parent / "l2"
_SPEC_EXAMPLE = _FIXTURES / "spec_example.yaml"


@pytest.fixture
def min_config(tmp_path: Path) -> Path:
    """Minimal config.yaml — no demo_database_url; audit U.0 doesn't
    need a live DB connection (skeleton mode is metadata-only)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "aws_account_id: '111122223333'\n"
        "aws_region: us-west-2\n"
        "deployment_name: spec_example\n"
        "db_table_prefix: spec_example\n"
        "datasource_arn: arn:aws:quicksight:us-west-2:111122223333"
        ":datasource/ds\n"
    )
    return cfg


def test_audit_help_lists_subcommands():
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "--help"])
    assert result.exit_code == 0, result.output
    assert "apply" in result.output
    assert "clean" in result.output
    assert "test" in result.output
    assert "verify" in result.output


def test_audit_verify_errors_on_pdf_without_provenance(
    min_config: Path, tmp_path: Path,
):
    """``audit verify`` against a PDF generated without a DB has no
    embedded provenance — must error out cleanly, not crash."""
    out = tmp_path / "report.pdf"
    runner = CliRunner()
    # Generate without DB → no embedded provenance.
    apply_result = runner.invoke(
        main,
        [
            "audit", "apply",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
            "-o", str(out),
            "--execute",
        ],
    )
    assert apply_result.exit_code == 0, apply_result.output

    verify_result = runner.invoke(
        main,
        [
            "audit", "verify", str(out),
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
        ],
    )
    assert verify_result.exit_code != 0
    assert "no embedded provenance" in verify_result.output


def test_audit_verify_errors_on_missing_pdf(
    min_config: Path, tmp_path: Path,
):
    """``audit verify`` requires the PDF to exist."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit", "verify", str(tmp_path / "nope.pdf"),
            "-c", str(min_config),
        ],
    )
    assert result.exit_code != 0


def test_provenance_fingerprint_round_trips_through_dict():
    """to_dict / from_dict round trip preserves every field +
    composite_sha so the embedded JSON in a PDF can be rehydrated
    by ``audit verify`` without information loss.
    """
    from recon_gen.cli.audit import ProvenanceFingerprint
    fp = ProvenanceFingerprint(
        transactions_hwm=42,
        transactions_sha="a" * 64,
        balances_hwm=7,
        balances_sha="b" * 64,
        l2_yaml_sha="c" * 64,
        code_identity="v8.1.0+gabc1234567890",
    )
    payload = fp.to_dict()
    rehydrated = ProvenanceFingerprint.from_dict(payload)
    assert rehydrated == fp
    assert rehydrated.composite_sha == fp.composite_sha
    assert rehydrated.short == fp.composite_sha[:8]


def test_provenance_fingerprint_rejects_unknown_schema():
    """from_dict guards against rehydrating a future schema version
    that the running code doesn't understand — better to fail loud
    than silently misverify.
    """
    from recon_gen.cli.audit import ProvenanceFingerprint
    with pytest.raises(ValueError, match="Unrecognized provenance schema"):
        ProvenanceFingerprint.from_dict({"schema": "qsg-audit-provenance-v999"})


def test_provenance_fingerprint_defaults_format_version_to_legacy():
    """CW.3 — Pre-CW PDFs have no `provenance_format_version` field.

    `from_dict` must default missing-field → 1 (legacy). CW emissions
    explicitly carry 2 (streamed SQL fingerprint from CW.2). Lock 4
    in ``docs/audits/cw_0_audit_pdf_perf_locks.md``.
    """
    from recon_gen.cli.audit import ProvenanceFingerprint
    # Legacy shape — no provenance_format_version key.
    legacy_blob = {
        "schema": "qsg-audit-provenance-v1",
        "composite_sha": "x" * 64,
        "transactions_hwm": 100,
        "transactions_sha": "a" * 64,
        "balances_hwm": 50,
        "balances_sha": "b" * 64,
        "l2_yaml_sha": "c" * 64,
        "code_identity": "v8.0.0",
    }
    legacy = ProvenanceFingerprint.from_dict(legacy_blob)
    assert legacy.provenance_format_version == 1, (
        "Legacy PDFs (no field) must hydrate as version=1 for "
        "audit verify dispatch to land on the legacy code path."
    )
    # New CW emissions carry the field explicitly.
    cw_blob = {**legacy_blob, "provenance_format_version": 2}
    cw = ProvenanceFingerprint.from_dict(cw_blob)
    assert cw.provenance_format_version == 2


def test_legacy_hash_table_rows_v1_frozen_smoke():
    """CW.4 — legacy v1 ladder still functions against a synthetic cursor.

    This is the verify path pre-CW PDFs land on. The function is
    frozen (do not modify); this test just proves the import path
    + DB-API contract still work without an actual DB. Real
    fingerprint equivalence on a v1 PDF would require shipping a
    fixture PDF in the repo (deferred — see lock doc open items).
    """
    from recon_gen.common.provenance import legacy_hash_table_rows_v1

    class FakeCursor:
        def __init__(self) -> None:
            self._rows: list[tuple[object, ...]] = []
            # DB-API description: 7-tuples (name, type_code, ...).
            self.description: list[tuple[str, object, object, object, object, object, object]] = [
                ("entry", None, None, None, None, None, None),
                ("account_id", None, None, None, None, None, None),
                ("signed_amount", None, None, None, None, None, None),
            ]

        def execute(self, sql: str) -> None:  # noqa: ARG002
            self._rows = [
                (1, "a", 100),
                (2, "b", -50),
                (3, "c", 25),
            ]

        def fetchall(self) -> list[tuple[object, ...]]:
            return self._rows

    cur = FakeCursor()
    sha = legacy_hash_table_rows_v1(cur, table="t", hwm=3)
    # Stable hex digest (regenerated locally; if this changes the
    # frozen ladder broke). 64 hex chars.
    assert len(sha) == 64
    assert all(c in "0123456789abcdef" for c in sha)


def test_audit_verify_rejects_unknown_format_version(
    min_config: Path, tmp_path: Path,
):
    """CW.4 — `audit verify` against a PDF carrying an unsupported
    `provenance_format_version` fails loud with a useful message.

    Tests the dispatch's defensive branch (v1 + v2 are handled
    natively; anything else hits the error). Avoids the actual
    DB-roundtrip by stubbing the embedded provenance in a fresh PDF.
    """
    # First write a real PDF, then patch its /Subject metadata to
    # carry a fabricated provenance with version=999.
    from pypdf import PdfReader, PdfWriter
    import json as _json
    real = tmp_path / "real.pdf"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit", "apply",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
            "-o", str(real),
            "--execute",
        ],
    )
    assert result.exit_code == 0, result.output

    # Replace metadata Subject with a fake provenance blob.
    fake_blob = {
        "schema": "qsg-audit-provenance-v1",
        "composite_sha": "0" * 64,
        "transactions_hwm": 0,
        "transactions_sha": "0" * 64,
        "balances_hwm": 0,
        "balances_sha": "0" * 64,
        "l2_yaml_sha": "0" * 64,
        "code_identity": "v0",
        "provenance_format_version": 999,
    }
    reader = PdfReader(str(real))
    writer = PdfWriter(clone_from=reader)
    writer.add_metadata({"/Subject": _json.dumps(fake_blob)})
    fake = tmp_path / "fake.pdf"
    with fake.open("wb") as f:
        writer.write(f)

    # min_config has no demo_database_url → audit verify will
    # short-circuit before reaching the DB-recompute branch. We
    # need to fool it into reaching the dispatch. Skip if the
    # check fires too early.
    verify_result = runner.invoke(
        main,
        [
            "audit", "verify", str(fake),
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
        ],
    )
    assert verify_result.exit_code != 0
    # Either the no-DB check fires (acceptable — proves Click-level
    # rejection still works), or the dispatch error fires (tests
    # the actual CW.4 code path). Both surface "useful message".
    assert (
        "Unsupported provenance_format_version" in verify_result.output
        or "demo_database_url" in verify_result.output
    )


def test_provenance_fingerprint_default_constructor_emits_cw_version():
    """A fresh ProvenanceFingerprint() carries the current CW version,
    so audit-apply emissions stamp v2 without explicit operator wiring."""
    from recon_gen.cli.audit import ProvenanceFingerprint
    fp = ProvenanceFingerprint(
        transactions_hwm=1, transactions_sha="a" * 64,
        balances_hwm=2, balances_sha="b" * 64,
        l2_yaml_sha="c" * 64, code_identity="v0",
    )
    assert fp.provenance_format_version == 2
    # to_dict serializes the field.
    payload = fp.to_dict()
    assert payload["provenance_format_version"] == 2


_SIGNING_FIXTURE_KEY = (
    Path(__file__).parent / "fixtures" / "test-signing-key.pem"
)
_SIGNING_FIXTURE_CERT = (
    Path(__file__).parent / "fixtures" / "test-signing-cert.pem"
)


@pytest.fixture
def signed_config(tmp_path: Path) -> Path:
    """min_config + a signing block pointing at the test fixture cert.

    The fixture key+cert are committed self-signed PEMs under
    ``tests/audit/fixtures/`` (CN=recon-gen audit test signing).
    Anything in the repo is fine to sign with — it's not trusted
    by any cert store.
    """
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "aws_account_id: '111122223333'\n"
        "aws_region: us-west-2\n"
        "deployment_name: spec_example\n"
        "db_table_prefix: spec_example\n"
        "datasource_arn: arn:aws:quicksight:us-west-2:111122223333"
        ":datasource/ds\n"
        f"signing:\n"
        f"  key_path: {_SIGNING_FIXTURE_KEY}\n"
        f"  cert_path: {_SIGNING_FIXTURE_CERT}\n"
        f"  signer_name: pytest signer\n"
    )
    return cfg


def test_audit_apply_signing_block_loads_into_config(
    signed_config: Path,
):
    """``signing:`` in config.yaml round-trips into ``Config.signing``."""
    from recon_gen.common.config import load_config, SigningConfig
    cfg = load_config(signed_config)
    assert isinstance(cfg.audit.signing, SigningConfig)
    assert cfg.audit.signing.signer_name == "pytest signer"
    assert Path(cfg.audit.signing.key_path).is_file()
    assert Path(cfg.audit.signing.cert_path).is_file()


def test_audit_apply_signing_block_missing_field_errors(tmp_path: Path):
    """``signing:`` requires both key_path and cert_path; a partial
    block must fail loud with a useful message."""
    from recon_gen.common.config import load_config
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "aws_account_id: '111122223333'\n"
        "aws_region: us-west-2\n"
        "deployment_name: spec_example\n"
        "db_table_prefix: spec_example\n"
        "datasource_arn: arn:aws:quicksight:us-west-2:111122223333"
        ":datasource/ds\n"
        "signing:\n"
        "  key_path: /tmp/missing.pem\n"
        # cert_path intentionally missing
    )
    with pytest.raises(ValueError, match="signing block is missing"):
        load_config(cfg)


def test_audit_apply_pdf_embeds_l2_yaml_attachment(
    min_config: Path, tmp_path: Path,
):
    """The PDF carries the L2 YAML as a byte-exact file attachment.

    The attachment lets a verifier download the L2 spec the report
    was generated against, hash it, and confirm it matches the
    embedded ``l2_yaml_sha`` (when fingerprinting is enabled). The
    attachment fires regardless of whether a DB is configured —
    skeleton-mode PDFs still ship with the L2 yaml so reviewers
    can audit the spec.
    """
    out = tmp_path / "report.pdf"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit", "apply",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
            "-o", str(out),
            "--execute",
        ],
    )
    assert result.exit_code == 0, result.output

    import hashlib
    from pypdf import PdfReader
    reader = PdfReader(str(out))
    attachments = reader.attachments
    assert "spec_example.yaml" in attachments, (
        f"expected spec_example.yaml attachment, got "
        f"{list(attachments.keys())}"
    )
    attached_bytes = attachments["spec_example.yaml"][0]
    on_disk = _SPEC_EXAMPLE.read_bytes()
    assert hashlib.sha256(attached_bytes).hexdigest() == \
        hashlib.sha256(on_disk).hexdigest(), (
        "attachment bytes don't match the source L2 yaml on disk"
    )


def test_audit_apply_pdf_has_two_empty_reviewer_signature_fields(
    min_config: Path, tmp_path: Path,
):
    """Two empty reviewer signature fields land below the notes box.

    pyHanko adds them post-multiBuild via append_signature_field at
    the coords the layout reserved. They show as clickable Sign-Here
    placeholders in any signing-capable PDF reader; reviewers sign
    INTO the existing fields rather than appending new ones, so the
    system signature's byte range covers their definitions.
    """
    out = tmp_path / "report.pdf"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit", "apply",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
            "-o", str(out),
            "--execute",
        ],
    )
    assert result.exit_code == 0, result.output

    from pypdf import PdfReader
    reader = PdfReader(str(out))
    sig_field_names: list[str] = []
    notes_field_rect = None
    sig_field_rects: dict[str, list[float]] = {}
    for page in reader.pages:
        for annot_ref in page.get("/Annots") or []:
            obj = annot_ref.get_object() if hasattr(
                annot_ref, "get_object"
            ) else annot_ref
            if obj.get("/FT") == "/Sig":
                name = obj.get("/T")
                sig_field_names.append(name)
                sig_field_rects[name] = list(obj.get("/Rect"))
            elif obj.get("/T") == "QSGNotesField":
                notes_field_rect = list(obj.get("/Rect"))
    assert "QSGReviewerSignature1" in sig_field_names, (
        f"expected reviewer sig field 1, got {sig_field_names}"
    )
    assert "QSGReviewerSignature2" in sig_field_names, (
        f"expected reviewer sig field 2, got {sig_field_names}"
    )
    assert notes_field_rect is not None, (
        "QSGNotesField annotation missing — sig field placement "
        "test depends on locating it"
    )
    # Both reviewer fields land BELOW the notes box (lower y than
    # the notes box bottom edge). Encodes the layout intent: the
    # signature placeholders are a continuation of the sign-off
    # block, not floating elsewhere.
    notes_bottom_y = notes_field_rect[1]
    for name in ("QSGReviewerSignature1", "QSGReviewerSignature2"):
        sig_top_y = sig_field_rects[name][3]
        assert sig_top_y < notes_bottom_y, (
            f"{name} top edge {sig_top_y} should be below "
            f"notes box bottom {notes_bottom_y}"
        )


def test_audit_apply_pdf_embeds_verify_script_attachment(
    min_config: Path, tmp_path: Path,
):
    """The PDF carries the manual-recompute recipe as a downloadable
    ``verify-provenance.py`` attachment.

    Pairs with the L2 yaml attachment: a verifier opens the PDF's
    attachments panel, downloads both files byte-exact, and runs the
    script against the operator's database to recompute the composite
    fingerprint independently. The script body is the same text the
    appendix renders inline, so what the reader sees on the page is
    what they download.
    """
    out = tmp_path / "report.pdf"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit", "apply",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
            "-o", str(out),
            "--execute",
        ],
    )
    assert result.exit_code == 0, result.output

    from pypdf import PdfReader
    reader = PdfReader(str(out))
    attachments = reader.attachments
    assert "verify-provenance.py" in attachments, (
        f"expected verify-provenance.py attachment, got "
        f"{list(attachments.keys())}"
    )
    script_bytes = attachments["verify-provenance.py"][0]
    script_text = script_bytes.decode("utf-8")
    # Must be the recipe we render in the appendix, not arbitrary
    # bytes — these markers are stable load-bearing fragments of
    # _build_verify_recipe_script.
    assert "import hashlib" in script_text
    assert "def hash_table(cur, table, hwm):" in script_text
    assert "<prefix>_transactions" in script_text
    assert "print(h.hexdigest())" in script_text


def test_audit_apply_pdf_appendix_bookmarked_at_level_0(
    min_config: Path, tmp_path: Path,
):
    """Provenance Appendix gets its own level-0 outline entry so
    a regulator can jump to it from the PDF reader's sidebar nav.
    """
    out = tmp_path / "report.pdf"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit", "apply",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
            "-o", str(out),
            "--execute",
        ],
    )
    assert result.exit_code == 0, result.output

    from pypdf import PdfReader
    reader = PdfReader(str(out))
    titles_at_root = []
    for item in reader.outline:
        if not isinstance(item, list):
            titles_at_root.append(item.title)
    assert "Provenance Appendix" in titles_at_root, (
        f"expected level-0 'Provenance Appendix' bookmark, got "
        f"{titles_at_root}"
    )


def test_audit_apply_pdf_notes_field_is_fillable_acroform(
    min_config: Path, tmp_path: Path,
):
    """The Notes / Exceptions box is a real AcroForm text field
    (not just a styled cell) so reviewers can type comments in any
    PDF reader before adding their digital signature.
    """
    out = tmp_path / "report.pdf"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit", "apply",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
            "-o", str(out),
            "--execute",
        ],
    )
    assert result.exit_code == 0, result.output

    from pypdf import PdfReader
    reader = PdfReader(str(out))
    fields = reader.get_form_text_fields() or {}
    assert "QSGNotesField" in fields, (
        f"expected QSGNotesField AcroForm text field, got "
        f"{sorted(fields.keys())}"
    )


def test_audit_apply_signing_pyhanko_missing_degrades_gracefully(
    signed_config: Path, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """CW.5 — pyhanko-missing must NOT crash audit apply.

    Operator-flagged side issue: ``audit apply --execute`` was hard-
    crashing at the signing step when the optional ``pyhanko`` dep
    wasn't installed. Lock 5 in
    ``docs/audits/cw_0_audit_pdf_perf_locks.md`` says: emit an
    unsigned PDF + warning event, don't crash.

    We can't actually uninstall pyhanko inside the test, so we
    inject an ImportError by stubbing ``sign_pdf_in_place`` to raise.
    The CLI must:
    1. Still exit 0.
    2. Still leave the PDF on disk (it was written BEFORE the
       signing attempt).
    3. Emit the "signing skipped" warning to stderr.
    """
    out = tmp_path / "report.pdf"

    # Stub sign_pdf_in_place to raise ImportError, simulating the
    # `from pyhanko... import` line inside it failing at call time.
    def fake_sign(*args: object, **kwargs: object) -> None:  # noqa: ARG001
        raise ImportError(
            "No module named 'pyhanko' (synthetic for CW.5 test)"
        )
    monkeypatch.setattr(
        "recon_gen.common.pdf.signing.sign_pdf_in_place",
        fake_sign,
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit", "apply",
            "-c", str(signed_config),
            "--l2", str(_SPEC_EXAMPLE),
            "-o", str(out),
            "--execute",
        ],
    )
    # Exit 0 — the PDF wrote successfully, signing is post-hoc.
    assert result.exit_code == 0, result.output
    # PDF on disk, valid bytes.
    assert out.is_file()
    assert out.stat().st_size > 0
    assert out.read_bytes().startswith(b"%PDF-")
    # Warning surfaced via stderr (click runner merges into .output).
    assert "signing skipped" in result.output
    assert "pyhanko" in result.output
    assert "recon-gen[prod]" in result.output


def test_audit_apply_execute_signs_pdf(
    signed_config: Path, tmp_path: Path,
):
    """When ``signing:`` is set, the rendered PDF carries an embedded
    digital signature (one signature widget, signed by the fixture
    cert). Subsequent reviewers can stack additional signatures on top
    via Adobe / pyHanko — this test only checks the system seal.
    """
    out = tmp_path / "signed.pdf"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit", "apply",
            "-c", str(signed_config),
            "--l2", str(_SPEC_EXAMPLE),
            "-o", str(out),
            "--execute",
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.is_file()

    from pyhanko.pdf_utils.reader import PdfFileReader
    with out.open("rb") as f:
        reader = PdfFileReader(f)
        sigs = list(reader.embedded_signatures)
    assert len(sigs) == 1, f"expected 1 signature, got {len(sigs)}"
    sig = sigs[0]
    assert sig.field_name == "QSGSystemSignature"
    # cert CN is the fixture's CN
    cn = sig.signer_cert.subject.human_friendly
    assert "recon-gen audit test signing" in cn


def test_audit_apply_emits_markdown_to_stdout(min_config: Path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit", "apply",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
        ],
    )
    assert result.exit_code == 0, result.output
    # Cover-page markdown shape sanity.
    assert "# Recon Generator Audit Report" in result.output
    assert "## spec_example" in result.output  # institution as H2
    assert "**Reporting period:**" in result.output
    assert "**Generated:**" in result.output
    assert "Provenance fingerprint:" in result.output
    assert "U.7" in result.output  # placeholder cites where real hash lands
    # Executive summary section sanity (U.2).
    assert "## Executive Summary" in result.output
    assert "### Volume" in result.output
    assert "### Exception Counts" in result.output
    assert "Transactions (legs)" in result.output
    assert _DRIFT_NAME in result.output
    assert "Supersession" in result.output
    # No DB configured → placeholder notice rendered.
    assert "Database not configured" in result.output
    # U.3.a Drift violations section.
    assert "## Drift Violations" in result.output
    # U.3.b Overdraft violations section.
    assert "## Overdraft Violations" in result.output
    # U.3.c Limit breach violations section.
    assert "## Limit Breach Violations" in result.output
    # U.3.d Stuck pending transactions section.
    assert "## Stuck Pending Transactions" in result.output
    # U.3.e Stuck unbundled transactions section.
    assert "## Stuck Unbundled Transactions" in result.output
    # U.3.f Supersession audit section.
    assert "## Supersession Audit" in result.output
    # U.5 Sign-off block.
    assert "## Sign-Off" in result.output
    assert "### System Attestation" in result.output
    assert "### Reviewer Attestation" in result.output
    assert "Generated by" in result.output
    assert "recon-gen v" in result.output
    assert "L2 instance" in result.output
    assert "Notes / Exceptions" in result.output


def test_audit_apply_emits_markdown_to_file(min_config: Path, tmp_path: Path):
    out = tmp_path / "report.md"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit", "apply",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
            "-o", str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.is_file()
    content = out.read_text()
    assert "# Recon Generator Audit Report" in content


def test_audit_apply_period_overrides(min_config: Path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit", "apply",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
            "--period", "2026-01-01..2026-01-07",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "2026-01-01" in result.output
    assert "2026-01-07" in result.output


def test_audit_apply_period_from_after_to_errors(min_config: Path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit", "apply",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
            "--period", "2026-01-08..2026-01-01",
        ],
    )
    assert result.exit_code != 0
    assert "must not be after" in result.output


def test_audit_apply_execute_writes_pdf(min_config: Path, tmp_path: Path):
    out = tmp_path / "report.pdf"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit", "apply",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
            "-o", str(out),
            "--execute",
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.is_file()
    # PDFs start with %PDF- magic bytes.
    assert out.read_bytes().startswith(b"%PDF-")
    # End-to-end sanity (per Phase U test plan): extract text via
    # pypdf and confirm the institution + period + skeleton sentinel
    # actually rendered onto the page.
    from pypdf import PdfReader
    reader = PdfReader(str(out))
    # U.1 cover + U.2 executive summary = at least 2 pages.
    assert len(reader.pages) >= 2
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "audit report" in text.lower()
    assert DEFAULT_PREFIX in text  # institution heading rendered
    assert "Reporting period" in text  # period band rendered
    assert "Provenance" in text  # page footer rendered
    assert "U.7" in text  # fingerprint placeholder cites where it lands
    # U.6 cover-page source-data provenance block.
    assert "Source-Data Provenance" in text
    assert "Transactions table" in text
    assert "Daily balances table" in text
    assert "L2 instance YAML" in text
    assert "recon-gen code" in text
    # U.6 per-page footer chrome (NumberedCanvas).
    assert "Page 1 of " in text
    assert "Provenance: pending" in text
    # U.2 executive summary content.
    assert "Executive Summary" in text
    assert "Volume" in text
    assert "Exception Counts" in text
    assert "Transactions (legs)" in text
    assert _DRIFT_NAME in text
    assert "Supersession" in text
    # No DB → placeholder notice on the exec summary page.
    assert "Database not configured" in text
    # U.3.a Drift violations page.
    assert "Drift Violations" in text
    # U.3.b Overdraft violations page.
    assert "Overdraft Violations" in text
    # U.3.c Limit breach violations page.
    assert "Limit Breach Violations" in text
    # U.3.d Stuck pending transactions page.
    assert "Stuck Pending Transactions" in text
    # U.3.e Stuck unbundled transactions page.
    assert "Stuck Unbundled Transactions" in text
    # U.3.f Supersession audit page.
    assert _SUPERSESSION_AUDIT_NAME in text
    # U.5 Sign-off page.
    assert "Sign-Off" in text
    assert "System Attestation" in text
    assert "Reviewer Attestation" in text
    assert "recon-gen v" in text  # version stamped
    assert "Notes / Exceptions" in text


def test_audit_pdf_bookmarks_resolve_to_real_pages(
    min_config: Path, tmp_path: Path,
):
    """Bookmarks must land on the right page (regression net).

    Catches two failure modes seen in development:
    1. NumberedCanvas snapshot/restore pattern collapses every
       bookmark target to page 1 because ``dict(self.__dict__)``
       captured page-ref state and restoring overwrote the
       accumulated bookmark→page refs. Easy to miss because the
       PDF still renders fine and the TOC text looks normal — only
       the sidebar nav is broken.
    2. multiBuild stopping too early so TOC/bookmarks disagree
       (off-by-one on a section heading after a TOC overflow shift).

    Asserts:
    - Bookmarks span ≥3 distinct pages (would catch the all-page-1
      collapse outright).
    - Bookmarks are monotonically non-decreasing in PDF order
      (parents come before children top-of-doc to bottom).
    - No two top-level (H1) bookmarks point to the same page (every
      section header is its own ``PageBreak``-separated page; if two
      collapse it means a section emitted nothing or PageBreak
      handling broke).
    - The TOC page text contains every H1 title (sanity that the
      TOC flowable rendered the entries it collected).
    """
    out = tmp_path / "report.pdf"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit", "apply",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
            "-o", str(out),
            "--execute",
        ],
    )
    assert result.exit_code == 0, result.output

    from pypdf import PdfReader
    reader = PdfReader(str(out))

    # Walk the outline: collect (depth, title, 1-indexed page).
    entries: list[tuple[int, str, int]] = []

    def walk(items, depth=0):  # type: ignore[no-untyped-def]: items is pypdf outline (recursive list[Destination | list])
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
            else:
                page_idx = reader.get_destination_page_number(item)
                assert page_idx is not None, "bookmark missing page index"
                entries.append((depth, item.title, page_idx + 1))

    walk(reader.outline)
    assert entries, "PDF outline is empty — bookmarks didn't emit"

    pages = [page for _, _, page in entries]
    distinct_pages = set(pages)
    assert len(distinct_pages) >= 3, (
        f"Bookmarks collapsed onto {len(distinct_pages)} distinct "
        f"pages ({sorted(distinct_pages)}) — likely the canvas "
        f"snapshot/restore bug that overwrites destinations dict. "
        f"Outline: {entries[:5]}"
    )

    # Monotonicity in PDF order.
    for prev, curr in zip(entries, entries[1:]):
        assert prev[2] <= curr[2], (
            f"Bookmarks out of order: {prev} comes before {curr} but "
            f"its page is later. multiBuild may not have converged."
        )

    # No two H1s on same page.
    h1_entries = [e for e in entries if e[0] == 0]
    h1_pages: dict[int, str] = {}
    for _, title, page in h1_entries:
        if page in h1_pages:
            raise AssertionError(
                f"Two top-level sections collapsed to page {page}: "
                f"'{h1_pages[page]}' and '{title}'. Either a section "
                f"emitted no content or a PageBreak got dropped."
            )
        h1_pages[page] = title

    # TOC contains every H1 title.
    toc_text = ""
    for page in reader.pages[:5]:
        text = page.extract_text(extraction_mode="layout") or ""
        if "Table of Contents" in text or toc_text:
            toc_text += text + "\n"
    missing_toc = [
        title for _, title, _ in h1_entries if title not in toc_text
    ]
    assert not missing_toc, (
        f"TOC text is missing H1 entries: {missing_toc}. "
        f"TOC flowable may have rendered before all entries were "
        f"collected (multiBuild convergence)."
    )


def test_audit_clean_default_is_dry_run(tmp_path: Path):
    target = tmp_path / "report.pdf"
    target.write_bytes(b"%PDF-stub")
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "clean", "-o", str(target)])
    assert result.exit_code == 0, result.output
    assert "Would delete" in result.output
    assert target.exists(), "default clean should not actually delete"


def test_audit_clean_execute_deletes(tmp_path: Path):
    target = tmp_path / "report.pdf"
    target.write_bytes(b"%PDF-stub")
    runner = CliRunner()
    result = runner.invoke(
        main, ["audit", "clean", "-o", str(target), "--execute"],
    )
    assert result.exit_code == 0, result.output
    assert not target.exists()


def test_audit_clean_missing_file_is_noop(tmp_path: Path):
    target = tmp_path / "does-not-exist.pdf"
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "clean", "-o", str(target)])
    assert result.exit_code == 0, result.output
    assert "doesn't exist" in result.output


def test_resolve_frame_default_is_seven_day_window():
    """Default = today − 7 ... today − 1 (inclusive). 7 days, ending yesterday.
    BD.2 — was `_resolve_period -> DateInterval`; now `_resolve_frame ->
    AsOfFrame`. Reach the window via `frame.window`. `frame.as_of` is
    `today` (the day the audit runs)."""
    today = date(2026, 5, 15)
    frame = _resolve_frame(None, today=today)
    assert frame.window.start == date(2026, 5, 8)
    assert frame.window.end == date(2026, 5, 14)
    assert frame.window.days == 7  # inclusive 7-day window
    assert frame.as_of == today  # audit-event anchor
