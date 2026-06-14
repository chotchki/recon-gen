"""DC.2 — ACME DNS-01 state machine for Let's Encrypt.

Drives the ``acme`` library through the DNS-01 flow with a Cloudflare-
backed TXT-record provisioner. One call produces a single SAN cert
covering N hostnames (N=2 for our env-shape: ``local<env>`` +
``<env>``).

Sequence (per spike DC.2 step 4):

  1. ACME ``new-account`` against the directory URL (idempotent;
     reuses the persistent account key under
     ``~/.local/share/recon-gen/tls/account.key``).
  2. ACME ``new-order`` with N DNS identifiers.
  3. For each authorization: extract the ``dns-01`` challenge token,
     POST the ``_acme-challenge.<host>`` TXT record via Cloudflare.
  4. Poll DNS (via dnspython against 1.1.1.1) until every TXT
     propagates.
  5. Tell ACME each challenge is ready; poll order until VALID.
  6. Mint a CSR (RSA 2048, with all SANs); finalize the order;
     download the PEM chain.
  7. DELETE every TXT record provisioned in step 3 (best-effort
     cleanup).

The ``acme`` + ``josepy`` Python packages are partially typed (acme
2.x ships only inline annotations on the public client API; the
challenge / message records flow through ``Any``-shaped attribute
access). The body below uses ``cast(Any, ...)`` at every untyped
boundary so the rest of the module stays strict-clean — same pattern
``_dev/runner.py`` uses for ``testcontainers`` / ``docker`` (both
shipped without PEP 561 stubs).
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

import josepy as jose  # pyright: ignore[reportMissingTypeStubs]: josepy 2.x ships no PEP 561 stubs
from acme import (  # pyright: ignore[reportMissingTypeStubs]: acme 5.x ships no PEP 561 stubs
    challenges,
    client,
    crypto_util,
    messages,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from recon_gen._dev.tls import storage


if TYPE_CHECKING:
    from recon_gen._dev.tls.cloudflare_api import CloudflareClient


_USER_AGENT = "recon-gen-dev-tls/1.0"  # typing-smell: ignore[recon-prefix]: HTTP User-Agent identifier, not a per-deployment AWS resource prefix

# ACME standard challenge token TTL. Cloudflare's "auto" TTL of 60s
# is plenty short, so polling rarely overshoots production TTL.
_DNS_PROPAGATION_TIMEOUT_S = 120.0
_DNS_POLL_INTERVAL_S = 5.0

# ACME order finalization can take up to ~30s on staging during
# happy-path. Prod is usually faster but we budget the same.
_ORDER_FINALIZE_TIMEOUT_S = 120.0

logger = logging.getLogger(__name__)


def run_acme_dns01(
    *,
    sans: Sequence[str],
    account_email: str,
    acme_directory_url: str,
    cloudflare: CloudflareClient,
    dns_resolver: Any | None = None,
) -> tuple[bytes, bytes]:
    """Run the full DNS-01 ACME flow; return (cert_pem, key_pem).

    Provisions one TXT challenge per SAN; cleans them up afterwards
    (best-effort; idempotent if a TXT was never created).

    Raises ``RuntimeError`` on ACME / Cloudflare failures with a
    human-readable context message.
    """
    if not sans:
        raise ValueError("run_acme_dns01 requires at least one SAN")

    # ACME account — the JWK + Network + ClientV2 trio. ``acme``'s
    # ``JWKRSA`` is exposed at runtime via ``josepy.JWKRSA`` but
    # marked "private import" by pyright (re-exported through
    # ``josepy/__init__.py`` without ``__all__`` listing); cast
    # through Any to side-step the false-positive.
    account_key = cast(Any, jose).JWKRSA(
        key=storage.load_or_create_account_key()
    )
    net = client.ClientNetwork(account_key, user_agent=_USER_AGENT)
    directory = client.ClientV2.get_directory(acme_directory_url, net)
    acme_client = client.ClientV2(directory, net=net)
    _register_account(acme_client, email=account_email)

    # Cert keypair (one per cert, separate from the long-lived
    # account key).
    cert_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert_key_pem = cert_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    csr_pem = crypto_util.make_csr(cert_key_pem, list(sans))

    order = acme_client.new_order(csr_pem)

    # Re-bind ``order`` + ``acme_client`` as Any so the dynamically-
    # typed attribute access below stays strict-clean. ``acme`` 2.x
    # exposes only inline annotations on the public client surface;
    # AuthorizationResource / ChallengeBody / OrderResource flow
    # through generic dataclass plumbing pyright can't introspect.
    order_any: Any = order
    client_any: Any = acme_client

    provisioned_record_ids: list[str] = []
    try:
        for auth in order_any.authorizations:
            chall_body = _select_dns01_challenge(auth)
            host = str(auth.body.identifier.value)
            response, validation = chall_body.chall.response_and_validation(
                client_any.net.key
            )
            txt_hostname = f"_acme-challenge.{host}"
            rec_id = cloudflare.put_txt_record(
                hostname=txt_hostname, token_value=str(validation),
            )
            provisioned_record_ids.append(rec_id)
            _wait_for_txt_propagation(
                hostname=txt_hostname,
                expected_value=str(validation),
                resolver=dns_resolver,
            )
            client_any.answer_challenge(chall_body, response)

        # Poll for VALID order; finalize.
        finalized: Any = client_any.poll_and_finalize(
            order, deadline=_deadline(_ORDER_FINALIZE_TIMEOUT_S),
        )
    finally:
        for rec_id in provisioned_record_ids:
            try:
                cloudflare.delete_dns_record(rec_id)
            except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                logger.warning(
                    "Failed to delete ACME TXT record %s: %s", rec_id, exc,
                )

    cert_pem = str(finalized.fullchain_pem).encode("utf-8")
    return cert_pem, cert_key_pem


# -- helpers ---------------------------------------------------------------


def _register_account(
    acme_client: client.ClientV2, *, email: str,
) -> None:
    """Register (or no-op re-register) the ACME account."""
    try:
        cast(Any, acme_client).new_account(
            cast(Any, messages).NewRegistration.from_data(
                email=email, terms_of_service_agreed=True,
            )
        )
    except cast(Any, messages).Error as exc:
        # ``urn:ietf:params:acme:error:accountAlreadyExists`` is the
        # benign re-register signal — swallow. Re-raise anything else.
        if "accountAlreadyExists" in str(exc) or "already" in str(exc).lower():
            return
        raise RuntimeError(f"ACME new-account failed: {exc}") from exc


def _select_dns01_challenge(auth: Any) -> Any:
    """Pick the DNS-01 challenge out of an authorization's offers.

    Returns the ChallengeBody (acme's response/answer surface); the
    inner ``chall`` is what ``response_and_validation`` runs against.
    """
    for ch in auth.body.challenges:
        if isinstance(ch.chall, cast(Any, challenges).DNS01):
            return ch
    raise RuntimeError(
        f"ACME authorization for {auth.body.identifier.value} did not "
        f"offer a DNS-01 challenge"
    )


def _wait_for_txt_propagation(
    *,
    hostname: str,
    expected_value: str,
    resolver: Any | None,
    timeout: float = _DNS_PROPAGATION_TIMEOUT_S,
    poll_interval: float = _DNS_POLL_INTERVAL_S,
) -> None:
    """Block until ``hostname``'s TXT record contains ``expected_value``.

    Queries Cloudflare's 1.1.1.1 anycast resolver so we don't wait for
    the operator's local resolver cache to expire.
    """
    if resolver is None:
        import dns.resolver  # local import — heavy module  # noqa: PLC0415
        new_resolver = cast(Any, dns.resolver).Resolver()
        new_resolver.nameservers = ["1.1.1.1"]
        new_resolver.timeout = 5.0
        new_resolver.lifetime = 5.0
        resolver = new_resolver

    deadline = time.monotonic() + timeout
    while True:
        if _txt_contains(resolver, hostname, expected_value):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"TXT record at {hostname} did not propagate "
                f"value={expected_value!r} within {timeout:.0f}s"
            )
        time.sleep(poll_interval)


def _txt_contains(resolver: Any, hostname: str, expected_value: str) -> bool:
    """Best-effort TXT lookup; swallow transient resolver errors."""
    import dns.exception  # local import — keep top of module clean  # noqa: PLC0415

    try:
        answer = resolver.resolve(hostname, "TXT")
    except cast(Any, dns.exception).DNSException:
        return False
    for rdata in answer:
        # dnspython returns TXT strings as bytes inside .strings.
        for chunk in getattr(rdata, "strings", ()):
            value = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            if value == expected_value:
                return True
    return False


def _deadline(seconds: float) -> dt.datetime:
    """Build a ``datetime`` deadline ``seconds`` from now (UTC).

    The acme library's ``poll_and_finalize`` takes a datetime; isolate
    the construction here so we own the timezone semantics.
    """
    return dt.datetime.now(dt.UTC) + dt.timedelta(seconds=seconds)
