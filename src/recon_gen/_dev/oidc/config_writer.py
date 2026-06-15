"""DD.4 — emit Dex config.yaml + cert/key into a per-run temp dir for
container mount.

The Dex container bind-mounts this directory at /etc/dex (ro), so:
  - dir_path/config.yaml ⇒ /etc/dex/config.yaml (Dex's static config)
  - dir_path/cert.pem    ⇒ /etc/dex/cert.pem    (mounted TLS cert)
  - dir_path/key.pem     ⇒ /etc/dex/key.pem     (mounted TLS key)

The Dex config uses ``secretEnv`` + ``hashFromEnv`` for the
client_secret + user password hash (real Dex yaml fields — audit-
corrected from the spike's BX.248 4-step env mirror). Dex resolves
them from container env vars at startup, so no string interpolation
of the secret into the yaml on disk is needed.

Cert + key are copied (not symlinked) into the dir so the bind-mount
is self-contained — the operator can delete the source LE cert
without breaking a running Dex container.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import yaml


def write_dex_config_dir(
    *,
    dir_path: Path,
    issuer_url: str,
    client_id: str,
    redirect_uri: str,
    user_email: str,
    cert_path: Path,
    key_path: Path,
) -> None:
    """Write Dex config.yaml + cert.pem + key.pem into ``dir_path``.

    The caller is responsible for creating ``dir_path`` (typically a
    tempdir from ``mkdtemp()``) and for passing absolute, readable
    paths for ``cert_path`` and ``key_path``. The mounted dir is
    read-only inside the container, so any post-write modification
    requires recreating the container.

    Args:
        dir_path: target directory for the three files (must exist).
        issuer_url: the OIDC issuer URL Dex will advertise — MUST
            match the URL the App2 client uses to reach Dex. Locked
            per ensure.py's _ISSUER_URL_BY_ENV.
        client_id: the OIDC client_id App2 will use; written into
            Dex's staticClients block.
        redirect_uri: the callback URL App2 expects; written into
            staticClients[*].redirectURIs.
        user_email: email for the single static test user.
        cert_path: source LE cert (PEM); copied into dir_path/cert.pem.
        key_path: source LE private key (PEM); copied into
            dir_path/key.pem.

    Raises:
        FileNotFoundError: if ``cert_path`` or ``key_path`` is absent.
        OSError: on any filesystem write error.
    """
    # 1. Copy cert + key into the dir. shutil.copyfile preserves
    # file content but NOT permissions — and on most host umasks the
    # destination lands at 0o644 (world-readable). Cert material is
    # public (LE CT logs); the private key MUST stay 0o600. Explicit
    # chmod here, not inherited from the umask of whoever invoked the
    # runner. The Dex container reads as the host UID anyway
    # (container.py user= override), so 0o600 doesn't block reads.
    target_cert = dir_path / "cert.pem"
    target_key = dir_path / "key.pem"
    shutil.copyfile(cert_path, target_cert)
    shutil.copyfile(key_path, target_key)
    os.chmod(target_cert, 0o644)
    os.chmod(target_key, 0o600)

    # 2. Compose the Dex static config. Field-by-field comments map
    # back to Dex's docs (https://dexidp.io/docs/configuration/).
    config = {
        # The issuer URL Dex advertises in /.well-known. Locked tuple
        # per ensure.py; MUST be a URL the LE cert covers + that
        # clients can reach.
        "issuer": issuer_url,
        "web": {
            # Listen on 0.0.0.0:5556 inside the container; container.py
            # binds host_port:5556 so external clients hit the locked
            # issuer URL.
            "https": "0.0.0.0:5556",
            # TLS material — bind-mounted via cfg_dir.
            "tlsCert": "/etc/dex/cert.pem",
            "tlsKey": "/etc/dex/key.pem",
        },
        # In-memory storage — test-only. State doesn't survive
        # container restart; that's a feature (clean state per run).
        "storage": {
            "type": "memory",
        },
        "staticClients": [
            {
                "id": client_id,
                "name": "recon-gen App2",
                "redirectURIs": [redirect_uri],
                # secretEnv: Dex resolves DEX_CLIENT_SECRET from the
                # container env at startup. Audit-corrected real
                # field; the spike's "$ENV_VAR" interpolation was a
                # misread.
                "secretEnv": "DEX_CLIENT_SECRET",
            },
        ],
        "enablePasswordDB": True,
        "staticPasswords": [
            {
                "email": user_email,
                # hashFromEnv: Dex resolves DEX_USER_PASSWORD_HASH
                # from the container env at startup (bcrypt hash
                # produced by secrets.bcrypt_hash).
                "hashFromEnv": "DEX_USER_PASSWORD_HASH",
                "username": "testuser",
                # Stable userID across runs — Dex requires this to be
                # a UUID so the OIDC ``sub`` claim is stable for App2
                # session bookkeeping. Hardcoded constant per LOCKS.
                "userID": "08a8684b-db88-4b73-90a9-3cd1661f5466",
            },
        ],
    }

    # 3. Write the yaml. Sort keys False keeps the layout matching
    # Dex's docs ordering (issuer, web, storage, staticClients,
    # staticPasswords) so logs / debugging line up.
    target_config = dir_path / "config.yaml"
    with target_config.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(config, fh, sort_keys=False, default_flow_style=False)
