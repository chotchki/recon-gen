"""PDF digital-signing helper backed by pyHanko (U.7.b).

Single entry point: ``sign_pdf_in_place(pdf_path, signing_config)``.
Rewrites the PDF with an incremental update carrying a CMS signature
over the entire byte range. Subsequent signers can add their own
signatures on top via Adobe / pyHanko / any compliant tool — the
generator is deliberately silent on how many signatures are required.

pyHanko is an optional runtime dep (only needed when an integrator
sets ``signing:`` in ``config.yaml``); imports happen inside the
function so the audit CLI loads cleanly without it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from recon_gen.common.config import SigningConfig


def sign_pdf_in_place(
    pdf_path: Path,
    signing: SigningConfig,
) -> None:
    """Apply a CMS digital signature to ``pdf_path`` (incremental update).

    Loads the PEM RSA private key + PEM cert, opens the PDF for
    incremental writing, signs it via pyHanko, and replaces the
    file bytes with the signed result. Field name is fixed
    (``QSGSystemSignature``) so the system signature is identifiable
    by tools that inspect signature widgets — but that's a *system*
    concern, not a "signer 1 of N" claim; the document doesn't
    advertise how many signatures are expected.

    Raises ``FileNotFoundError`` if key or cert file is missing,
    ``ValueError`` if the passphrase env var is named but unset.
    """
    from pyhanko.pdf_utils.incremental_writer import (
        IncrementalPdfFileWriter,
    )
    from pyhanko.sign import signers

    key_path = Path(signing.key_path)
    cert_path = Path(signing.cert_path)
    if not key_path.is_file():
        raise FileNotFoundError(
            f"signing.key_path does not exist: {signing.key_path!r}"
        )
    if not cert_path.is_file():
        raise FileNotFoundError(
            f"signing.cert_path does not exist: {signing.cert_path!r}"
        )

    passphrase: bytes | None = None
    if signing.passphrase_env:
        env_val = os.environ.get(signing.passphrase_env)
        if env_val is None:
            raise ValueError(
                f"signing.passphrase_env={signing.passphrase_env!r} "
                f"is set but the environment variable is missing."
            )
        passphrase = env_val.encode("utf-8")

    signer = signers.SimpleSigner.load(  # pyright: ignore[reportUnknownMemberType]: pyHanko unstubbed (no py.typed)
        key_file=str(key_path),
        cert_file=str(cert_path),
        key_passphrase=passphrase,
    )
    if signer is None:
        raise ValueError(
            f"pyHanko could not load signing material from "
            f"{signing.key_path!r} + {signing.cert_path!r}. Confirm "
            f"the key is PEM RSA + the cert is PEM."
        )

    signature_meta = signers.PdfSignatureMetadata(
        field_name="QSGSystemSignature",
        reason="Auto-signed by recon-gen audit",
        location="recon-gen",
        name=signing.signer_name,
    )

    # Incremental update: read original bytes, write signed PDF to
    # a new buffer, atomically replace. Subsequent signers see the
    # original signature + their own as separate revisions.
    with pdf_path.open("rb") as f:
        writer = IncrementalPdfFileWriter(f)
        # ``sign_pdf`` returns a ``BytesIO`` whose .getvalue() is the
        # signed PDF bytes; pyHanko is unstubbed so the return type is
        # ``Unknown``. Bind through ``Any`` so we can call .getvalue()
        # without a per-call ignore on every reference.
        signed: Any = signers.sign_pdf(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]: pyHanko unstubbed (no py.typed)
            writer, signature_meta, signer=signer,
        )

    pdf_path.write_bytes(signed.getvalue())  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]: pyHanko BytesIO unstubbed
