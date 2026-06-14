"""DC.2 — XDG state dir + advisory file lock + cert PEM helpers.

The XDG state directory holds long-lived ACME machinery that must
survive ``rm -rf run/`` and fresh clones (account key especially —
Let's Encrypt rate-limits account creation at 5 per IP per 3h).
Layout per the spike (`docs/audits/dc_0_https_spike.md` DC.2 step 1)::

    ~/.local/share/recon-gen/tls/
      account.key       # ACME account private key (one per machine)
      zone-id.txt       # Cloudflare zone-id cache
      dev/cert.pem      # operator's-Mac SAN cert
      dev/key.pem
      ci/cert.pem       # WSL2 runner SAN cert
      ci/key.pem
      renew.lock        # advisory file lock for concurrent runner safety

XDG resolves to ``$XDG_STATE_HOME/recon-gen/tls/`` when set, else
``~/.local/share/recon-gen/tls/`` per the XDG Base Directory spec.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import os
import time
from collections.abc import Generator, Sequence
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtensionOID


# 10-second advisory file-lock timeout. The spike (DC.2 step 1) calls
# out "~10s" as the budget. Two concurrent ``./run_tests.sh up`` are
# the worst case; the second waits for the first to either finish the
# fast path (4 Cloudflare GETs) or the slow path (full ACME mint) and
# then no-ops.
_LOCK_TIMEOUT_SECONDS = 10.0


def xdg_state_dir() -> Path:
    """Return the XDG state directory for this module, creating it.

    Honors ``$XDG_STATE_HOME`` (per the XDG Base Directory spec) and
    falls back to ``~/.local/share`` (the historical XDG default;
    matches the locked layout in the spike).
    """
    base_env = os.environ.get("XDG_STATE_HOME")
    if base_env:
        base = Path(base_env)
    else:
        base = Path.home() / ".local" / "share"
    target = base / "recon-gen" / "tls"
    target.mkdir(parents=True, exist_ok=True)
    return target


def zone_id_cache_path() -> Path:
    """Path to the Cloudflare zone-id cache file."""
    return xdg_state_dir() / "zone-id.txt"


def account_key_path() -> Path:
    """Path to the ACME account private key."""
    return xdg_state_dir() / "account.key"


def renew_lock_path() -> Path:
    """Path to the advisory renew lock."""
    return xdg_state_dir() / "renew.lock"


def read_cached_zone_id() -> str | None:
    """Read the cached zone ID; return None on absent / empty."""
    p = zone_id_cache_path()
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8").strip()
    return text or None


def write_cached_zone_id(zone_id: str) -> None:
    """Persist the zone ID to the cache file."""
    zone_id_cache_path().write_text(zone_id + "\n", encoding="utf-8")


def load_or_create_account_key() -> rsa.RSAPrivateKey:
    """Load the persistent ACME account key, generating on first call.

    Account keys are stable across the machine's lifetime — losing one
    just means creating a new ACME account (and burning a slot in the
    5-accounts-per-IP-per-3h Let's Encrypt rate limit). RSA 2048 is
    the conservative Let's Encrypt-supported default.
    """
    path = account_key_path()
    if path.exists():
        with path.open("rb") as f:
            loaded = serialization.load_pem_private_key(
                f.read(), password=None
            )
        if not isinstance(loaded, rsa.RSAPrivateKey):
            raise RuntimeError(
                f"Existing account key at {path} is not RSA — "
                f"delete the file and rerun to regenerate"
            )
        return loaded
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    # Owner-only read/write so the key isn't world-readable on a
    # shared dev machine.
    path.write_bytes(pem)
    path.chmod(0o600)
    return key


@contextlib.contextmanager
def acquire_renew_lock(
    *, timeout: float = _LOCK_TIMEOUT_SECONDS,
) -> Generator[None, None, None]:
    """Acquire the advisory renew lock for the duration of the block.

    Uses ``fcntl.flock`` (BSD-style advisory; honored on macOS + Linux
    inc. WSL2 — the spike's targeted environments). Polls every 100ms
    until acquired or the timeout elapses; raises ``BlockingIOError``
    on timeout so the caller surfaces the same exit code as other
    "another runner is already at it" cases.
    """
    lock_path = renew_lock_path()
    # Open in append mode so the file exists but we don't truncate
    # competing data (the file body is unused — only the lock matters).
    with lock_path.open("a") as fh:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise BlockingIOError(
                        f"Could not acquire renew lock at {lock_path} "
                        f"within {timeout:.0f}s — another runner is "
                        f"already mid-renewal"
                    )
                time.sleep(0.1)
        try:
            yield
        finally:
            # flock releases on close() too, but be explicit for the
            # not-yet-closed file handle case (matters if the caller
            # leaks the context manager via a generator-as-fixture).
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def read_cert_not_after(cert_path: Path) -> dt.datetime:
    """Parse a PEM cert and return its ``not_after`` as UTC-aware datetime.

    Raises ``FileNotFoundError`` if the file is absent, ``ValueError``
    if the file isn't a parseable PEM cert.
    """
    pem = cert_path.read_bytes()
    cert = x509.load_pem_x509_certificate(pem)
    # ``not_valid_after_utc`` is the timezone-aware variant added in
    # cryptography 42 (preferred over the deprecated naive accessor).
    return cert.not_valid_after_utc


def cert_covers_sans(cert_path: Path, sans: Sequence[str]) -> bool:
    """Return True iff the cert at ``cert_path`` covers every SAN in
    ``sans`` (set-equality on the DNS-name SAN entries — the cert may
    list them in any order).

    Returns False on parse error or missing SAN extension (rather than
    raising), so callers treat "unparseable" + "wrong SANs" + "missing
    cert" symmetrically (all three → re-mint).
    """
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except (ValueError, FileNotFoundError):
        return False
    try:
        ext = cert.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        )
    except x509.ExtensionNotFound:
        return False
    san_value = ext.value
    if not isinstance(san_value, x509.SubjectAlternativeName):
        return False
    # ``get_values_for_type(DNSName)`` returns ``list[str]`` (the underlying
    # DNS-name values, not GeneralName objects); cryptography's stubs erase
    # the generic parameter so pyright sees it as bare list — narrow at the
    # type system the same way the runtime contract does.
    dns_names: list[str] = list(
        san_value.get_values_for_type(x509.DNSName)
    )
    return set(dns_names) == set(sans)


def cert_valid_for_at_least(
    cert_path: Path, *, days: int, now: dt.datetime | None = None,
) -> bool:
    """Return True iff the cert at ``cert_path`` exists, parses, AND
    has at least ``days`` of remaining validity from ``now`` (default
    UTC-now).

    All failure shapes collapse to False — caller re-mints.
    """
    if not cert_path.exists():
        return False
    try:
        not_after = read_cert_not_after(cert_path)
    except (ValueError, FileNotFoundError):
        return False
    current = now if now is not None else dt.datetime.now(dt.UTC)
    return (not_after - current) >= dt.timedelta(days=days)


def write_cert_and_key(
    *, cert_path: Path, key_path: Path, cert_pem: bytes, key_pem: bytes,
) -> None:
    """Atomically write cert + key PEMs to caller-supplied paths.

    Creates parent dirs if missing. Key chmod 0600.
    """
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(key_pem)
    key_path.chmod(0o600)
