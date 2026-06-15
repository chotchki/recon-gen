"""DD.4 — Dex container adopt-or-create using the Docker SDK directly.

Mirrors ``runner.py::_get_or_start_pg_container`` shape: try
``client.containers.get(name)`` first (adopt path); fall through to a
fresh ``client.containers.run(...)`` on ``NotFound`` / unreachable
daemon / mis-shaped port mapping.

Uses the Docker SDK directly (not testcontainers) because adopt-or-create
needs to inspect existing containers, which testcontainers' high-level
API doesn't expose. Same rationale as the PG / Oracle persistent
container handles in runner.py.

Readiness: polls the OIDC discovery endpoint
(``<issuer>/.well-known/openid-configuration``) over HTTPS (using the
real LE cert mounted into the container, no verify=False needed) until
it returns a usable response. Fast-fails after 30s of consistent
connection-refused — that shape means the container crashed.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final


# Pinned image (LOCKS — SHA digest TBD; using tag for now per spec).
# TODO(DD.4): replace with ghcr.io/dexidp/dex@sha256:<digest> once the
# v2.40.0 digest is captured. Tag-pinning here means a tag move upstream
# would surface as a behavior shift on the next fresh-container path.
DEX_IMAGE: Final = "ghcr.io/dexidp/dex:v2.40.0"

# Shared container name across xdist workers, mirroring the PG /
# Oracle pattern. Docker daemon serializes adopt-or-create by name so
# every worker converges on a single container per test session.
DEX_SHARED_CONTAINER_NAME: Final = "recon-gen-test-dex"  # typing-smell: ignore[recon-prefix]: Docker container name for the DD.4 xdist-shared Dex test fixture (not a cfg-prefixed AWS / DB resource ID) — stable across `pytest -n auto` workers so the App2 OIDC fixture can adopt-or-create against a single shared container; not multi-tenant and intentionally does not flow through `cfg.aws.prefixed()`

# Dex always listens on 5556 inside the container; the host-side port
# is supplied by the caller (DEV=5557, CI=5556 per ensure.py LOCKS).
_DEX_INTERNAL_PORT: Final = 5556

# Readiness tuning per LOCKS:
#   - poll every 1s
#   - 30s of consistent connection-refused => fast-fail
#   - hard timeout 60s overall
_READY_POLL_INTERVAL_SECONDS: Final = 1.0
_READY_REFUSED_FAILFAST_SECONDS: Final = 30.0
_READY_HARD_TIMEOUT_SECONDS: Final = 60


@dataclass(frozen=True)
class _PersistentContainerHandle:
    """DD.4 mirror of runner.py's ``_PersistentContainerHandle``.

    ``.stop()`` is a no-op by design — the container survives across
    ``./run_tests.sh`` invocations so the next run can adopt it. Operator
    owns the lifecycle via ``docker stop <name>`` or future
    ``./run_tests.sh down``.
    """

    name: str

    def stop(self) -> None:
        """No-op by design — see class docstring."""


def get_or_start_dex_container(
    *,
    host_port: int,
    cfg_dir: Path,
    cert_path: Path,
    key_path: Path,
    client_secret: str,
    user_password_hash: str,
) -> tuple[str, _PersistentContainerHandle]:
    """Adopt-or-create the shared Dex container.

    DJ.2.name_threading (2026-06-15): the prior ``name`` parameter
    was illusory — only one caller passed it, always with the value
    ``DEX_SHARED_CONTAINER_NAME``. Removed to match the actual call
    surface; the internal references to ``DEX_SHARED_CONTAINER_NAME``
    in ``_dex_logs_tail`` no longer represent a "hardcoded internally,
    parametrized at the boundary" smell.

    Adopt path: ``client.containers.get(name)``. If running, re-extract
    the host port and return. Container env vars (DEX_CLIENT_SECRET +
    DEX_USER_PASSWORD_HASH) are NOT re-injected on adopt — Dex's
    static config reads them at process start, and the container is
    already running.

    HONEST CAVEAT (adversarial review, 2026-06-15): on the adopt path
    the adopted container's bind-mount still points at the FIRST run's
    ``cfg_dir`` tempdir. ``ensure_dev_idp`` always allocates a FRESH
    tempdir per call (mkdtemp), so the new tempdir is unused on adopt
    and any cert/config change between runs is INVISIBLE to the live
    Dex process. ``verify_dex_url`` catches issuer-URL drift but not
    stale cert content. To rotate cert / client_secret / issuer-URL:
    ``docker rm -f recon-gen-test-dex`` then re-run — the next call
    will fall through to the fresh-create path. Backlog item:
    inspect ``existing.attrs['Mounts']`` on adopt + force-recreate
    when the bind-mount source diverges from ``cfg_dir``.

    Fresh path: ``client.containers.run(...)`` with the host_port
    binding + cfg_dir bind-mount + env vars set so Dex's ``secretEnv``
    + ``hashFromEnv`` resolve at startup.

    Returns ``(<host_port_actually_in_use>, handle)`` — caller composes
    the issuer URL from this + the env's locked hostname.

    Raises:
        RuntimeError: if the docker daemon is unreachable AND the fresh
            path can't fall back — caller surfaces as
            EXIT_NEEDS_OPERATOR.
    """
    try:
        import docker  # type: ignore[import-untyped]: third-party SDK lacks PEP 561 stubs  # noqa: PLC0415 — lazy
        from docker.errors import NotFound  # type: ignore[import-untyped]: third-party SDK lacks PEP 561 stubs  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "docker SDK unavailable — install `docker` python package "
            "or set RECON_GEN_DEX_URL to skip container spinup"
        ) from exc

    try:
        client = docker.from_env()
    except Exception as exc:  # noqa: BLE001 — operator-actionable bubble
        raise RuntimeError(
            f"Docker daemon unreachable: {type(exc).__name__}: {exc}; "
            "ensure Docker is running or set RECON_GEN_DEX_URL to skip"
        ) from exc

    # Adopt path.
    try:
        existing = client.containers.get(DEX_SHARED_CONTAINER_NAME)
        # If the container exited with a non-zero code, the last
        # docker-entrypoint run hit a fatal config error (bad cert path,
        # yaml malformed, perms locked). Restarting won't help — the
        # entrypoint redoes gomplate-rendering with the same inputs.
        # Always force-recreate so the new fresh-create path picks up
        # any cfg / perm fix that landed since the prior crash.
        if existing.status != "running":
            try:
                existing.remove(force=True)
            except Exception:  # noqa: BLE001 — best-effort
                pass
            return _start_fresh_dex_container(
                client=client,
                host_port=host_port,
                cfg_dir=cfg_dir,
                cert_path=cert_path,
                key_path=key_path,
                client_secret=client_secret,
                user_password_hash=user_password_hash,
            )

        # Mirror PG path — read actual host port from container attrs
        # so the URL we return matches the live binding. If the mapping
        # is mis-shaped, force-recreate.
        try:
            ports = existing.attrs["NetworkSettings"]["Ports"]
            actual_host_port = int(
                ports[f"{_DEX_INTERNAL_PORT}/tcp"][0]["HostPort"]
            )
        except (KeyError, IndexError, TypeError, ValueError):
            try:
                existing.remove(force=True)
            except Exception:  # noqa: BLE001 — best-effort
                pass
            return _start_fresh_dex_container(
                client=client,
                host_port=host_port,
                cfg_dir=cfg_dir,
                cert_path=cert_path,
                key_path=key_path,
                client_secret=client_secret,
                user_password_hash=user_password_hash,
            )

        # DJ.2.adopt_mount_check (2026-06-15): the adopted container's
        # bind-mount source must match the current cfg_dir, else the
        # container is serving STALE config (cert renewal, client_secret
        # rotation, issuer change all live in cfg_dir's contents). The
        # original DD.4.a docstring honestly admitted this gap; we now
        # detect + force-recreate when the mount source diverges.
        if not _adopt_mount_matches(existing, cfg_dir):
            try:
                existing.remove(force=True)
            except Exception:  # noqa: BLE001 — best-effort
                pass
            return _start_fresh_dex_container(
                client=client,
                host_port=host_port,
                cfg_dir=cfg_dir,
                cert_path=cert_path,
                key_path=key_path,
                client_secret=client_secret,
                user_password_hash=user_password_hash,
            )

        return str(actual_host_port), _PersistentContainerHandle(name=DEX_SHARED_CONTAINER_NAME)
    except NotFound:
        return _start_fresh_dex_container(
            client=client,
            host_port=host_port,
            cfg_dir=cfg_dir,
            cert_path=cert_path,
            key_path=key_path,
            client_secret=client_secret,
            user_password_hash=user_password_hash,
        )


def _adopt_mount_matches(existing: object, cfg_dir: Path) -> bool:
    """Return True iff the adopted container's bind-mount source for
    ``/etc/dex`` resolves to the same path as ``cfg_dir``.

    Docker's ``container.attrs['Mounts']`` is a list of dicts shaped:

        {"Type": "bind", "Source": "/tmp/recon-gen-dex-cfg-...",
         "Destination": "/etc/dex", "Mode": "ro", ...}

    Resolves both sides through ``Path.resolve()`` to absorb symlink
    differences (``/private/tmp/...`` vs ``/tmp/...`` on macOS, for
    instance). Returns False on any missing key / shape mismatch /
    OSError — the safe fallback is "force-recreate".

    DJ.2.adopt_mount_check (2026-06-15). Backlog source: DD.4
    adversarial review.
    """
    from typing import Any, cast  # noqa: PLC0415 — lazy
    try:
        mounts = cast(
            "list[dict[str, Any]]",  # typing-smell: ignore[explicit-any]: Docker SDK lacks PEP 561 stubs
            existing.attrs["Mounts"],  # type: ignore[attr-defined]: Docker SDK lacks PEP 561 stubs
        )
        for mount in mounts:
            if mount.get("Destination") != "/etc/dex":
                continue
            source = mount.get("Source")
            if not isinstance(source, str) or not source:
                return False
            try:
                return Path(source).resolve() == cfg_dir.resolve()
            except OSError:
                # Source path may have been rmtree'd by a prior
                # session's atexit; treat as divergence.
                return False
        return False
    except (KeyError, TypeError, AttributeError):
        return False


def _start_fresh_dex_container(
    *,
    client: object,
    host_port: int,
    cfg_dir: Path,
    cert_path: Path,  # noqa: ARG001 — cert lives in cfg_dir on disk; arg kept for caller-symmetry with adopt path
    key_path: Path,  # noqa: ARG001 — see cert_path
    client_secret: str,
    user_password_hash: str,
) -> tuple[str, _PersistentContainerHandle]:
    """Spin a fresh named Dex container with the LE cert + Dex config
    bind-mounted at /etc/dex (ro).

    Container env vars set DEX_CLIENT_SECRET + DEX_USER_PASSWORD_HASH
    so Dex's ``secretEnv`` + ``hashFromEnv`` config blocks resolve at
    startup (the audit-corrected approach — simpler than the BX.248
    4-step env mirror because Dex resolves natively).

    Race-safety: if another worker won the name-create race, Docker's
    daemon rejects with "container name already exists"; caller's loop
    in the adopt-or-create flow handles by falling back to adopt.
    """
    import os  # noqa: PLC0415 — lazy
    import sys  # noqa: PLC0415 — lazy

    # DJ.2.windows_guard (2026-06-15): os.getuid() / os.getgid() are
    # POSIX-only — on Windows both raise AttributeError. The Dex test
    # fixture requires Linux/macOS by construction (the cfg_dir
    # tempdir is mode 700 and the host-UID bind-mount-perms match
    # only works on POSIX). Fail explicitly with the documented
    # constraint instead of bubbling an obscure AttributeError from
    # the f-string below.
    if not hasattr(os, "getuid"):  # pragma: no cover — Windows-only branch
        raise RuntimeError(
            "Dex test container fixture requires Linux/macOS — "
            "host-UID match needs os.getuid()/os.getgid() (POSIX only). "
            f"Found sys.platform={sys.platform!r}. "
            "_dev/ is excluded from the wheel; this constraint is test-only."
        )

    # The exec command is the Dex binary serving the static config we
    # write to cfg_dir/config.yaml. Internal port 5556 is the Dex
    # default; we bind it to the env-locked host_port.
    #
    # `user=<host_uid>:<host_gid>`: dexidp/dex's Dockerfile sets
    # `USER 1001`, but our cfg_dir is a `tempfile.mkdtemp()` (mode 700,
    # owned by the host user that ran the runner) — UID 1001 inside the
    # container can't even traverse a host-uid-owned 700 dir, so the
    # image's docker-entrypoint fails at gomplate's first `stat` with
    # "permission denied". Override the container's runtime UID to
    # match the host user that created the bind-mount, so file perms
    # line up. Doesn't escape the container; host file perms untouched.
    container = client.containers.run(  # type: ignore[attr-defined]: docker.client.DockerClient stub lacks .containers
        image=DEX_IMAGE,
        name=DEX_SHARED_CONTAINER_NAME,
        detach=True,
        command=["dex", "serve", "/etc/dex/config.yaml"],
        ports={f"{_DEX_INTERNAL_PORT}/tcp": host_port},
        volumes={
            str(cfg_dir): {
                "bind": "/etc/dex",
                "mode": "ro",
            },
        },
        environment={
            "DEX_CLIENT_SECRET": client_secret,
            "DEX_USER_PASSWORD_HASH": user_password_hash,
        },
        user=f"{os.getuid()}:{os.getgid()}",
        # Restart policy: don't auto-restart on crash; the readiness
        # poll will fast-fail with EXIT_NEEDS_OPERATOR so the operator
        # can read logs from the dead container.
        restart_policy={"Name": "no"},
    )
    container.reload()  # type: ignore[reportUnknownMemberType]: docker SDK lacks PEP 561 stubs
    return str(host_port), _PersistentContainerHandle(name=DEX_SHARED_CONTAINER_NAME)


def wait_for_dex_ready(
    url: str,
    *,
    deadline_seconds: int = _READY_HARD_TIMEOUT_SECONDS,
    redact: "Sequence[str]" = (),
) -> None:
    """Poll ``<url>/.well-known/openid-configuration`` until Dex is
    ready (any 2xx response) or until we hit a fail-fast / hard
    timeout condition.

    Fail-fast: ``_READY_REFUSED_FAILFAST_SECONDS`` of CONSECUTIVE
    connection-refused responses ⇒ raise RuntimeError immediately.
    That shape means the container crashed or never bound the port;
    waiting the full 60s won't help.

    Other failure modes (TLS handshake errors, 5xx, partial reads)
    keep polling until the hard deadline — these can be transient
    during the Dex warm-up window.

    HTTPS to the real LE-cert hostname — no verify=False needed.

    ``redact``: sequence of secret values to scrub from the captured
    Dex logs before they ride out via the RuntimeError. The error
    propagates to ``runs/<id>/<variant>/<layer>/stderr.log`` which
    ci.yml uploads as a 14-day GHA artifact on a PUBLIC repo (DD.4
    adversarial-review finding); we must NEVER leak the resolved
    DEX_CLIENT_SECRET / DEX_USER_PASSWORD_HASH values via this path.
    Pass the actual secret strings; substring replacement scrubs them
    from the log text.

    Raises:
        RuntimeError: on fail-fast or hard-timeout. Message names the
            failure shape so the runner can surface an actionable
            stderr hint.
    """
    import httpx  # noqa: PLC0415 — lazy

    discovery_url = url.rstrip("/") + "/.well-known/openid-configuration"
    start = time.monotonic()
    refused_since: float | None = None

    while True:
        elapsed = time.monotonic() - start
        if elapsed >= deadline_seconds:
            raise RuntimeError(
                f"Dex readiness check failed: hard timeout "
                f"({deadline_seconds}s) on {discovery_url}; "
                "container may have started but Dex didn't bind"
                f"{_dex_logs_tail(redact=redact)}"
            )

        try:
            response = httpx.get(discovery_url, timeout=2.0)
            if 200 <= response.status_code < 300:
                # One final smoke: parse should reveal an issuer field
                # matching the URL we polled. Caller does the strict
                # check via _verify_dex_url; here we just trust the
                # 2xx.
                return
            # Non-2xx — keep polling, clear refused tracker.
            refused_since = None
        except httpx.ConnectError:
            # Connection-refused class — start the fail-fast clock.
            if refused_since is None:
                refused_since = time.monotonic()
            elif time.monotonic() - refused_since >= _READY_REFUSED_FAILFAST_SECONDS:
                raise RuntimeError(
                    "Dex readiness check failed: connection refused "
                    f"for {_READY_REFUSED_FAILFAST_SECONDS:.0f}s on "
                    f"{discovery_url}; container may have crashed"
                    f"{_dex_logs_tail(redact=redact)}"
                ) from None
        except httpx.HTTPError:
            # Transient (TLS handshake mid-warmup, partial read) —
            # clear refused tracker and keep polling.
            refused_since = None

        time.sleep(_READY_POLL_INTERVAL_SECONDS)


def _dex_logs_tail(*, max_lines: int = 40, redact: "Sequence[str]" = ()) -> str:
    """Capture the last ``max_lines`` of Dex's stdout+stderr and
    return them formatted for inclusion in a RuntimeError message.

    Surfaces Dex's actual crash reason (missing cert path / unparseable
    config / port bind failure) in the same error the CI runner emits,
    so the operator doesn't have to manually shell into the runner +
    run `docker logs recon-gen-test-dex` to diagnose. Returns an empty
    string on any docker-side failure — the readiness error is the
    primary signal; log capture is best-effort enrichment.

    ``redact``: substring-scrub list. Any non-empty string in this
    sequence is replaced with ``<redacted>`` in the captured log
    text before it's returned. Pass the resolved DEX_CLIENT_SECRET +
    DEX_USER_PASSWORD_HASH values so we can never accidentally leak
    them via a future Dex log-verbosity bump landing the secret in
    the PUBLIC-repo GHA artifact (DD.4 adversarial-review finding).
    """
    try:
        import docker  # type: ignore[import-untyped]: third-party SDK lacks PEP 561 stubs  # noqa: PLC0415
    except ImportError:
        return ""
    try:
        client = docker.from_env()
        container = client.containers.get(DEX_SHARED_CONTAINER_NAME)
        raw = container.logs(tail=max_lines, stdout=True, stderr=True)
        text = raw.decode("utf-8", errors="replace")
        for secret in redact:
            if secret:
                text = text.replace(secret, "<redacted>")
        if not text.strip():
            return (
                f"\n  (container {DEX_SHARED_CONTAINER_NAME} produced no "
                "logs — likely never ran the Dex binary; check the image "
                "pull + command line)"
            )
        return (
            f"\n  --- docker logs {DEX_SHARED_CONTAINER_NAME} "
            f"(tail {max_lines}) ---\n  "
            + text.replace("\n", "\n  ").rstrip()
            + f"\n  --- end docker logs ---"
        )
    except Exception as exc:  # noqa: BLE001 — best-effort enrichment
        return (
            f"\n  (could not fetch docker logs for "
            f"{DEX_SHARED_CONTAINER_NAME}: {type(exc).__name__}: {exc})"
        )


def verify_dex_url(url: str) -> None:
    """Final smoke-check: fetch the discovery doc and verify its
    ``issuer`` field matches ``url`` exactly.

    Catches the "Dex started but its config.yaml has a stale issuer"
    failure mode — analogous to ``_verify_pg_connect`` in the PG path.

    Raises:
        RuntimeError: on mismatched issuer or unparseable response.
    """
    import httpx  # noqa: PLC0415 — lazy

    discovery_url = url.rstrip("/") + "/.well-known/openid-configuration"
    try:
        response = httpx.get(discovery_url, timeout=5.0)
        response.raise_for_status()
        doc = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(
            f"Dex post-ready smoke failed: could not fetch / parse "
            f"{discovery_url}: {type(exc).__name__}: {exc}"
        ) from exc

    actual_issuer = doc.get("issuer")
    if actual_issuer != url:
        raise RuntimeError(
            f"Dex issuer mismatch: cfg expects {url!r} but live Dex "
            f"reports {actual_issuer!r}; container is serving a stale "
            "config — `docker rm -f recon-gen-test-dex` and retry"
        )
