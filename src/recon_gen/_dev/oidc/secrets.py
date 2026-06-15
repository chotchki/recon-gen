"""DD.4 — per-run OIDC credential scrambling (BX.248 mirror).

Three pure helpers used by ``ensure_dev_idp`` to mint a fresh
client_secret + user_password per run (BX.248 pattern — credentials
never persist across container lifetimes; even if a stale Dex container
is adopted, its env vars get re-injected with the new values on every
``ensure_dev_idp`` call).

``bcrypt_hash`` matches Dex's documented default cost (10); the hashed
form is what Dex's ``hashFromEnv`` field expects in the static-password
config block.
"""

from __future__ import annotations

import secrets as _stdlib_secrets


def generate_client_secret() -> str:
    """Return a 28-char hex string suitable for an OIDC client_secret.

    Equivalent to ``openssl rand -hex 14`` — 14 bytes of entropy
    rendered as 28 lowercase hex chars. Safe for use as a Dex
    static-client secret resolved via ``secretEnv``.
    """
    return _stdlib_secrets.token_hex(14)


def generate_user_password() -> str:
    """Return a 28-char hex string suitable for a Dex static-user
    password.

    Same entropy / shape as ``generate_client_secret`` — kept as two
    distinct helpers so caller intent is readable at the call site.
    """
    return _stdlib_secrets.token_hex(14)


def bcrypt_hash(plaintext: str) -> str:
    """Return a bcrypt hash of ``plaintext`` at cost=10 (Dex's documented
    default).

    The returned string is what Dex's static-password ``hashFromEnv``
    field expects — bcrypt's standard ``$2b$10$...`` format, decoded to
    ``str`` from the ``bytes`` that ``bcrypt.hashpw`` returns.

    Raises:
        ImportError: if the ``bcrypt`` package isn't installed. Lazy
            import keeps non-DD invocations of the runner clean.
    """
    import bcrypt  # noqa: PLC0415 — lazy: only DD.4 callers need bcrypt

    hashed = bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt(rounds=10))
    return hashed.decode("utf-8")
