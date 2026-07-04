"""DC.2 — top-level entry point for the runner-internal TLS coordinator.

``ensure_dev_env(env, ...)`` does the minimum work to leave
``cert_path + key_path`` valid for the locked hostname tuple of
``env``. Inside the advisory file lock:

  1. Reconcile the 4 managed A records (2 static loopback + 2 dynamic
     public-IP) via Cloudflare.
  2. Read the cert at ``cert_path``; if it covers BOTH locked SANs
     AND has at least 30 days of validity left, no-op.
  3. Else run ACME DNS-01 for the SAN pair; write the fresh PEMs.

Failure modes raise either ``ValueError`` (missing operator
configuration, e.g. ``RECON_GEN_CLOUDFLARE_TOKEN``) or
``RuntimeError`` (Cloudflare API error, ACME error, DNS propagation
timeout). The runner (DC.3, not in this commit) surfaces these as
``EXIT_NEEDS_OPERATOR``.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from pathlib import Path
from typing import Final

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from recon_gen._dev.tls import storage
from recon_gen._dev.tls.acme_client import run_acme_dns01
from recon_gen._dev.tls.cloudflare_api import CloudflareClient
from recon_gen._dev.tls.public_ip import discover as discover_public_ip
from recon_gen.common.env_keys import (
    RECON_GEN_CLOUDFLARE_TOKEN,
    RECON_GEN_TLS_SELF_SIGNED,
)


class Env(StrEnum):
    """Locked deployment-environment shapes for managed TLS."""

    DEV = "dev"  # operator's Mac
    CI = "ci"    # WSL2 CI runner


# Hostnames per env (locked tuple — order matters: index 0 = static
# loopback, index 1 = dynamic public-IP). Keep these in sync with the
# spike's table; changing them is a coordinated multi-record DNS event,
# not a code refactor.
_HOSTS_BY_ENV: Final[dict[Env, tuple[str, str]]] = {
    Env.DEV: ("localdev.recon-gen.hotchkiss.io", "dev.recon-gen.hotchkiss.io"),
    Env.CI:  ("localci.recon-gen.hotchkiss.io",  "ci.recon-gen.hotchkiss.io"),
}

# Static loopback target for the ``local<env>`` hostnames.
_LOOPBACK_IP: Final = "127.0.0.1"

# Renewal threshold per the spike (DC.0 §"Operator-confirm questions"
# item 4; default locked at 30d).
_RENEWAL_THRESHOLD_DAYS: Final = 30

# Prod Let's Encrypt directory by default; tests pin staging via the
# ``acme_directory_url`` arg.
_PROD_ACME_DIRECTORY = "https://acme-v02.api.letsencrypt.org/directory"
_STAGING_ACME_DIRECTORY = (
    "https://acme-staging-v02.api.letsencrypt.org/directory"
)


def ensure_dev_env(
    env: Env,
    *,
    cert_path: Path,
    key_path: Path,
    account_email: str,
    acme_directory_url: str = _PROD_ACME_DIRECTORY,
) -> None:
    """Idempotent. Does the minimum work to get ``cert_path`` +
    ``key_path`` in a valid state for ``env``, with all 4 managed DNS
    A records (across both envs' static + dynamic halves) reconciled
    against Cloudflare.

    Concretely:
      1. Reconcile the 2 A records for THIS env (static loopback +
         dynamic public-IP). The 4-record total in the spike is across
         both envs; each ``ensure_dev_env`` call reconciles the half
         that belongs to the env it was called for.
      2. Read existing cert at ``cert_path``; if it covers both SANs
         AND has >=30d of validity left, done.
      3. Else run ACME DNS-01 for the SAN pair, write fresh PEMs to
         caller paths.

    Holds the advisory file lock for steps 2-3 so two concurrent
    runner invocations don't double-write or burn through ACME rate
    limits.

    Raises:
        ValueError: when ``RECON_GEN_CLOUDFLARE_TOKEN`` is unset.
        RuntimeError: on Cloudflare API or ACME failure (with context).
        BlockingIOError: when the renew lock is held by another process
            and the timeout elapses.
    """
    # EA.2 — self-signed short-circuit for the cloud-spike CI runner, which
    # has no Cloudflare DNS control. Mint a self-signed cert for the same
    # locked SANs and skip the ACME/Cloudflare machinery entirely. Placed
    # ABOVE the token check so an unset RECON_GEN_CLOUDFLARE_TOKEN doesn't
    # raise; conversely, if the flag is UNSET the token requirement still
    # fires loud (no silent ACME fallthrough). The hostname is resolved to
    # 127.0.0.1 via /etc/hosts on the runner; trust is added out-of-band
    # (certifi append for httpx + Playwright ignore_https_errors).
    if RECON_GEN_TLS_SELF_SIGNED.get_or_none():
        sans = list(_HOSTS_BY_ENV[env])
        with storage.acquire_renew_lock():
            if not _cert_already_valid(cert_path, sans):
                cert_pem, key_pem = _mint_self_signed(sans=sans)
                storage.write_cert_and_key(
                    cert_path=cert_path, key_path=key_path,
                    cert_pem=cert_pem, key_pem=key_pem,
                )
        return

    token = RECON_GEN_CLOUDFLARE_TOKEN.get_or_none()
    if not token:
        raise ValueError(
            "Missing required configuration: RECON_GEN_CLOUDFLARE_TOKEN. "
            "Create a Cloudflare API token (Zone:DNS:Edit on hotchkiss.io) "
            "and export it; for local-dev paste into run/secrets.env and "
            "source it from your shell profile."
        )

    sans = list(_HOSTS_BY_ENV[env])
    cloudflare = _make_cloudflare_client(token=token)

    # Step 1: reconcile A records (out of the lock — the Cloudflare
    # API is the source of truth; concurrent reconciles are safe
    # because the desired state is identical across processes).
    static_host, dynamic_host = sans
    cloudflare.reconcile_a_record(
        hostname=static_host, target_ip=_LOOPBACK_IP,
    )
    public_ip = discover_public_ip()
    cloudflare.reconcile_a_record(
        hostname=dynamic_host, target_ip=public_ip,
    )

    # Steps 2-3: cert validity check + ACME mint under the lock.
    with storage.acquire_renew_lock():
        if _cert_already_valid(cert_path, sans):
            return
        cert_pem, key_pem = run_acme_dns01(
            sans=sans,
            account_email=account_email,
            acme_directory_url=acme_directory_url,
            cloudflare=cloudflare,
        )
        storage.write_cert_and_key(
            cert_path=cert_path,
            key_path=key_path,
            cert_pem=cert_pem,
            key_pem=key_pem,
        )


# -- internals -------------------------------------------------------------


# EA.2 — self-signed validity window. Long (825d, the CA/Browser Forum's
# historical max) so the cert clears the 30-day ``_RENEWAL_THRESHOLD_DAYS``
# check on every spike run without re-minting; a throwaway CI cert has no
# reason to be short-lived.
_SELF_SIGNED_DAYS: Final = 825


def _mint_self_signed(*, sans: list[str]) -> tuple[bytes, bytes]:
    """Mint a self-signed RSA-2048 cert + PKCS8 key covering ``sans``
    (EA.2). CN = first SAN; all SANs land in the SubjectAlternativeName so
    the cert validates for the locked hostname tuple. Returns
    ``(cert_pem, key_pem)`` — the same shape ``run_acme_dns01`` yields, so
    ``storage.write_cert_and_key`` consumes it unchanged."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, sans[0])])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=_SELF_SIGNED_DAYS))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(n) for n in sans]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def _make_cloudflare_client(*, token: str) -> CloudflareClient:
    """Build a Cloudflare client. Indirected for unit-test patching."""
    return CloudflareClient(token=token)


def _cert_already_valid(cert_path: Path, sans: list[str]) -> bool:
    """Return True iff the cert at ``cert_path`` exists + covers the
    exact SAN list + has at least the renewal threshold of validity left."""
    if not storage.cert_covers_sans(cert_path, sans):
        return False
    return storage.cert_valid_for_at_least(
        cert_path, days=_RENEWAL_THRESHOLD_DAYS,
    )
