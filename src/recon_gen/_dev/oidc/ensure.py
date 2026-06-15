"""DD.4 — top-level entry point for the runner-internal Dex IdP
coordinator.

``ensure_dev_idp(env, ...)`` does the minimum work to leave a Dex
container running at the env-locked host port, serving HTTPS via the
DC.3 LE cert + key, with a freshly-scrambled client_secret + user
password injected per run.

Pattern-symmetry with DC.3's ``ensure_dev_env``:
  1. Caller-provided cert_path + key_path are mounted into the
     container (DC.3 owns cert lifecycle; DD.4 only consumes).
  2. Per-run scrambled secret + bcrypt-hashed password (BX.248 mirror).
  3. Container adopt-or-create against the shared name.
  4. HTTPS readiness poll against the issuer URL's /.well-known
     endpoint — 30s of consistent connection-refused ⇒ fast-fail.
  5. Final issuer-URL smoke (catches stale-config drift).

Failure modes raise either ``ValueError`` (missing operator
configuration, e.g. ``RECON_GEN_OIDC_CLIENT_SECRET``) or
``RuntimeError`` (Docker daemon down, Dex crashed, network errors).
The runner (cmd_up_to, not in this commit) surfaces these as
``EXIT_NEEDS_OPERATOR``.
"""

from __future__ import annotations

import tempfile
from enum import StrEnum
from pathlib import Path
from typing import Final

from recon_gen._dev.oidc import secrets as oidc_secrets
from recon_gen._dev.oidc.config_writer import write_dex_config_dir
from recon_gen._dev.oidc.container import (
    DEX_SHARED_CONTAINER_NAME,
    get_or_start_dex_container,
    verify_dex_url,
    wait_for_dex_ready,
)


class Env(StrEnum):
    """Locked deployment-environment shapes for managed Dex.

    Mirrors ``recon_gen._dev.tls.Env`` so a single config field
    (cfg.app2.tls.env) drives both TLS and Dex env selection.
    """

    DEV = "dev"  # operator's Mac — Dex on host:5557, issuer localdev.recon-gen.hotchkiss.io:5557/dex
    CI = "ci"    # WSL2 CI runner — Dex on host:5556, issuer localci.recon-gen.hotchkiss.io:5556/dex


# Host port per env (locked) — the container internally always listens
# on 5556 but we bind to env-distinct host ports so DEV + CI can
# coexist on the same machine without conflict.
_HOST_PORT_BY_ENV: Final[dict[Env, int]] = {
    Env.DEV: 5557,
    Env.CI: 5556,
}

# Issuer URLs per env (locked tuple — the LE cert minted by DC.3
# MUST cover the hostname half). Changing these is a coordinated
# multi-record DNS event, not a code refactor.
_ISSUER_URL_BY_ENV: Final[dict[Env, str]] = {
    Env.DEV: "https://localdev.recon-gen.hotchkiss.io:5557/dex",
    Env.CI:  "https://localci.recon-gen.hotchkiss.io:5556/dex",
}


def ensure_dev_idp(
    env: Env,
    *,
    cfg: object,  # noqa: ARG001 — kept in signature per caller-contract; future runner integration may key off cfg.auth.oidc fields directly
    cert_path: Path,
    key_path: Path,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    user_email: str,
    user_password: str,
    container_host_port: int | None = None,
) -> str:
    """Idempotent. Does the minimum work to leave a Dex IdP container
    running at the env-locked URL, returning the issuer URL string.

    Concretely:
      1. Compute issuer_url from env (override host_port via
         ``container_host_port`` for tests that pin a free port).
      2. Allocate a tempdir; write Dex config.yaml + cert.pem + key.pem
         into it. The container bind-mounts this tempdir at /etc/dex
         (ro).
      3. Bcrypt the supplied user_password — Dex's static-password
         config reads the hash via ``hashFromEnv``.
      4. Adopt-or-create the Dex container with the shared name,
         injecting DEX_CLIENT_SECRET + DEX_USER_PASSWORD_HASH into the
         container env so Dex's ``secretEnv`` + ``hashFromEnv`` resolve.
      5. Poll the issuer URL's /.well-known/openid-configuration
         endpoint until Dex answers. 30s of connection-refused ⇒
         raise RuntimeError (container crashed).
      6. Smoke the issuer URL one more time (catches stale-config
         drift on the adopt path).
      7. Return the issuer URL.

    Args:
        env: ``Env.DEV`` or ``Env.CI``; selects host port + issuer URL.
        cfg: the loaded ``Config`` object (kept in signature for
            future runner integration; not consumed today).
        cert_path: PEM cert path (DC.3-managed); MUST cover the
            issuer URL's hostname.
        key_path: PEM private-key path (DC.3-managed).
        client_id: OIDC client_id App2 will use.
        client_secret: per-run scrambled client_secret; gets injected
            as the DEX_CLIENT_SECRET container env var.
        redirect_uri: App2 callback URL written into Dex's
            staticClients block.
        user_email: email of the single static test user.
        user_password: plaintext password for the test user; gets
            bcrypt-hashed before injection as DEX_USER_PASSWORD_HASH.
        container_host_port: optional override for the env-locked
            host port (rare; tests use this).

    Returns:
        The OIDC issuer URL (str) that App2 / pytest can hand to its
        OIDC client.

    Raises:
        ValueError: on missing env-supplied secrets / mis-shaped
            inputs (operator-actionable).
        RuntimeError: on Docker daemon failures, Dex crash, network
            errors. Runner surfaces these as EXIT_NEEDS_OPERATOR.
    """
    # 1. Resolve env-keyed URL + port.
    host_port = container_host_port or _HOST_PORT_BY_ENV[env]
    issuer_url = _ISSUER_URL_BY_ENV[env]
    # When the caller overrides the host port, we still want the
    # issuer URL to match the live binding for the readiness poll to
    # work. Re-derive by swapping the port in the locked URL.
    if container_host_port is not None and container_host_port != _HOST_PORT_BY_ENV[env]:
        issuer_url = _swap_port_in_url(
            _ISSUER_URL_BY_ENV[env], container_host_port,
        )

    # Operator-actionable validation: empty secrets here mean the
    # operator's env-var pipe is broken (run/secrets.env not sourced
    # / CI GitHub secret not threaded). Catch before docker calls so
    # the failure message points at the right thing.
    if not client_secret:
        raise ValueError(
            "Missing required configuration: client_secret is empty; "
            "ensure RECON_GEN_OIDC_CLIENT_SECRET is set (run/secrets.env "
            "on dev; OIDC_CLIENT_SECRET GitHub secret on CI)"
        )
    if not user_password:
        raise ValueError(
            "Missing required configuration: user_password is empty; "
            "ensure RECON_GEN_DEX_USER_PASSWORD is set (run/secrets.env "
            "on dev; DEX_USER_PASSWORD GitHub secret on CI)"
        )

    # 2. Allocate cfg tempdir + write the three mounted files.
    # tempfile.mkdtemp leaves the dir on disk — that's deliberate, the
    # Dex container needs it live for the container lifetime. The
    # adopt path re-writes the same paths so adopted containers see
    # fresh config every call.
    cfg_dir = Path(tempfile.mkdtemp(prefix="recon-gen-dex-cfg-"))  # typing-smell: ignore[recon-prefix]: tempfile prefix for the per-run mount dir; not a cfg-prefixed AWS / DB resource ID and intentionally does not flow through `cfg.aws.prefixed()`
    write_dex_config_dir(
        dir_path=cfg_dir,
        issuer_url=issuer_url,
        client_id=client_id,
        redirect_uri=redirect_uri,
        user_email=user_email,
        cert_path=cert_path,
        key_path=key_path,
    )

    # 3. Bcrypt the user password — Dex's static-password block reads
    # this via hashFromEnv (DEX_USER_PASSWORD_HASH).
    user_password_hash = oidc_secrets.bcrypt_hash(user_password)

    # 4. Container adopt-or-create.
    get_or_start_dex_container(
        name=DEX_SHARED_CONTAINER_NAME,
        host_port=host_port,
        cfg_dir=cfg_dir,
        cert_path=cert_path,
        key_path=key_path,
        client_secret=client_secret,
        user_password_hash=user_password_hash,
    )

    # 5. Readiness poll — fast-fail on connection-refused.
    wait_for_dex_ready(issuer_url, deadline_seconds=60)

    # 6. Smoke the issuer URL to catch stale-config drift on the adopt
    # path (the adopt path doesn't re-create the container, so if a
    # prior run's Dex is alive with the wrong issuer in its in-memory
    # state, the readiness poll passes but App2's OIDC flow fails
    # opaquely later).
    verify_dex_url(issuer_url)

    return issuer_url


# -- internals -------------------------------------------------------------


def _swap_port_in_url(url: str, new_port: int) -> str:
    """Return ``url`` with its host port replaced by ``new_port``.

    Used by tests that pin a free port via ``container_host_port`` —
    keeps the issuer URL consistent with the live binding so the
    readiness poll talks to the right address.

    Indirected so unit tests can patch + we don't pull in a full URL
    parser for a one-field swap.
    """
    from urllib.parse import urlsplit, urlunsplit  # noqa: PLC0415 — lazy

    parts = urlsplit(url)
    # netloc shape is "host:port" — split + rebuild.
    host = parts.hostname or ""
    new_netloc = f"{host}:{new_port}"
    return urlunsplit(
        (parts.scheme, new_netloc, parts.path, parts.query, parts.fragment),
    )
