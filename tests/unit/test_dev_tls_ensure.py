"""DC.2 — runner-internal ACME + Cloudflare DNS-01 coordinator tests.

Covers:
  * ``storage.xdg_state_dir`` + lock + cert helpers.
  * ``public_ip.discover`` parsing of the cloudflare_trace body.
  * ``cloudflare_api`` zone discovery + A/TXT record CRUD.
  * ``ensure_dev_env`` orchestration (idempotent no-op, ACME re-mint,
    missing token, expired cert, file-lock contention).

Avoids hitting the network — all ``requests`` calls + the ACME client
are mocked. Mints real PEMs via ``cryptography`` so the storage
assertions parse them like production would.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from recon_gen._dev.tls import Env, ensure_dev_env
from recon_gen._dev.tls import cloudflare_api, ensure as ensure_mod
from recon_gen._dev.tls import public_ip, storage
from recon_gen._dev.tls.ensure import _HOSTS_BY_ENV


# -- small helpers ---------------------------------------------------------


def _mint_self_signed(
    *, sans: list[str], not_after: dt.datetime,
) -> tuple[bytes, bytes]:
    """Mint a self-signed cert + key for testing."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, sans[0])]
    )
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(dt.datetime.now(dt.UTC) - dt.timedelta(days=1))
        .not_valid_after(not_after)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(n) for n in sans]
            ),
            critical=False,
        )
    )
    cert = builder.sign(key, hashes.SHA256())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return cert_pem, key_pem


@pytest.fixture(autouse=True)
def isolate_xdg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Point ``XDG_STATE_HOME`` at a tmp dir for every test in this
    module so we never read / write the operator's real state.

    EA.2 — also clear ``RECON_GEN_TLS_SELF_SIGNED`` so the default (ACME)
    path is what these tests exercise regardless of the ambient env. The
    cloud-spike CI runner sets it globally (EA.2), which would fire the
    self-signed short-circuit and make every ACME-path assertion fail —
    POLICY-1 (CI ≡ local) says a test must not flip behavior on ambient
    env. The one self-signed test re-sets it explicitly.
    """
    state_root = tmp_path / "state"
    state_root.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state_root))
    monkeypatch.delenv("RECON_GEN_TLS_SELF_SIGNED", raising=False)  # typing-smell: ignore[envvar-bypass]: test isolation must delenv the raw name so the ACME-path tests are deterministic regardless of ambient env
    return state_root


# -- Env tuple + hostname locks --------------------------------------------


def test_env_enum_has_exactly_dev_and_ci() -> None:
    """The Env enum locks at DEV / CI — operator-visible surface."""
    assert {e.value for e in Env} == {"dev", "ci"}
    assert Env.DEV.value == "dev"
    assert Env.CI.value == "ci"


def test_hosts_by_env_dev_tuple() -> None:
    # Locked tuple per spike DC.0 §"DC.2 — runner-internal …" (line 109).
    assert _HOSTS_BY_ENV[Env.DEV] == (
        "localdev.recon-gen.hotchkiss.io",
        "dev.recon-gen.hotchkiss.io",
    )


def test_hosts_by_env_ci_tuple() -> None:
    assert _HOSTS_BY_ENV[Env.CI] == (
        "localci.recon-gen.hotchkiss.io",
        "ci.recon-gen.hotchkiss.io",
    )


# -- storage --------------------------------------------------------------


def test_xdg_state_dir_respects_env(tmp_path: Path) -> None:
    """Storage honors XDG_STATE_HOME and creates the dir."""
    state = storage.xdg_state_dir()
    assert state.exists()
    assert state.is_dir()
    # Layout: $XDG_STATE_HOME/recon-gen/tls/
    assert state.name == "tls"
    # The "recon-gen" parent dir name is the XDG application namespace,
    # by convention the package name. Matches MANAGED_TAG_VALUE only
    # incidentally (same string, unrelated semantic).
    assert state.parent.name == "recon-gen"  # typing-smell: ignore[no-inline-production-constants]: XDG application namespace, not the MANAGED_TAG_VALUE


def test_xdg_state_dir_default_when_env_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """When XDG_STATE_HOME is unset, falls back to ~/.local/share."""
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # Path.home() reads HOME on POSIX.
    assert (
        storage.xdg_state_dir()
        == fake_home / ".local" / "share" / "recon-gen" / "tls"  # typing-smell: ignore[no-inline-production-constants]: XDG application namespace, not the MANAGED_TAG_VALUE
    )
    assert storage.xdg_state_dir().exists()


def test_read_cert_not_after_parses_real_pem(tmp_path: Path) -> None:
    """``read_cert_not_after`` reads a real PEM correctly."""
    not_after = dt.datetime(2030, 1, 1, 12, 0, 0, tzinfo=dt.UTC)
    cert_pem, _ = _mint_self_signed(
        sans=["a.example.com"], not_after=not_after,
    )
    p = tmp_path / "cert.pem"
    p.write_bytes(cert_pem)
    got = storage.read_cert_not_after(p)
    assert got == not_after


def test_cert_covers_sans_matches(tmp_path: Path) -> None:
    cert_pem, _ = _mint_self_signed(
        sans=["a.example.com", "b.example.com"],
        not_after=dt.datetime.now(dt.UTC) + dt.timedelta(days=90),
    )
    p = tmp_path / "cert.pem"
    p.write_bytes(cert_pem)
    assert storage.cert_covers_sans(p, ["a.example.com", "b.example.com"])
    # Order-insensitive (set equality).
    assert storage.cert_covers_sans(p, ["b.example.com", "a.example.com"])


def test_cert_covers_sans_mismatch(tmp_path: Path) -> None:
    cert_pem, _ = _mint_self_signed(
        sans=["a.example.com"],
        not_after=dt.datetime.now(dt.UTC) + dt.timedelta(days=90),
    )
    p = tmp_path / "cert.pem"
    p.write_bytes(cert_pem)
    # Extra SAN required — fail.
    assert not storage.cert_covers_sans(p, ["a.example.com", "b.example.com"])
    # Wrong SAN — fail.
    assert not storage.cert_covers_sans(p, ["other.example.com"])


def test_cert_covers_sans_missing_file_is_false(tmp_path: Path) -> None:
    assert not storage.cert_covers_sans(
        tmp_path / "does-not-exist.pem", ["x.example.com"],
    )


def test_cert_valid_for_at_least_fresh(tmp_path: Path) -> None:
    """Fresh 90-day cert is valid for >=30 days."""
    cert_pem, _ = _mint_self_signed(
        sans=["a.example.com"],
        not_after=dt.datetime.now(dt.UTC) + dt.timedelta(days=90),
    )
    p = tmp_path / "cert.pem"
    p.write_bytes(cert_pem)
    assert storage.cert_valid_for_at_least(p, days=30)


def test_cert_valid_for_at_least_near_expiry(tmp_path: Path) -> None:
    """Cert expiring in 10 days fails the 30-day check."""
    cert_pem, _ = _mint_self_signed(
        sans=["a.example.com"],
        not_after=dt.datetime.now(dt.UTC) + dt.timedelta(days=10),
    )
    p = tmp_path / "cert.pem"
    p.write_bytes(cert_pem)
    assert not storage.cert_valid_for_at_least(p, days=30)


def test_cert_valid_for_at_least_missing(tmp_path: Path) -> None:
    assert not storage.cert_valid_for_at_least(
        tmp_path / "missing.pem", days=30,
    )


def test_load_or_create_account_key_persists(tmp_path: Path) -> None:
    """Calling twice returns equivalent keys + only writes once."""
    key1 = storage.load_or_create_account_key()
    key1_pem = key1.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key2 = storage.load_or_create_account_key()
    key2_pem = key2.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    assert key1_pem == key2_pem
    # On-disk owner-only mode.
    assert (storage.account_key_path().stat().st_mode & 0o777) == 0o600


def test_zone_id_cache_roundtrip() -> None:
    assert storage.read_cached_zone_id() is None
    storage.write_cached_zone_id("abc123")
    assert storage.read_cached_zone_id() == "abc123"


def test_acquire_renew_lock_serializes() -> None:
    """Second concurrent acquisition raises BlockingIOError fast."""
    with storage.acquire_renew_lock(timeout=0.5):
        with pytest.raises(BlockingIOError):
            with storage.acquire_renew_lock(timeout=0.2):
                pass


def test_acquire_renew_lock_releases_on_exit() -> None:
    """After the with-block, the lock is free."""
    with storage.acquire_renew_lock(timeout=0.5):
        pass
    # Re-acquire should succeed immediately.
    with storage.acquire_renew_lock(timeout=0.5):
        pass


def test_acquire_renew_lock_threadsafe_wait() -> None:
    """A thread waiting on the lock gets it after the holder releases."""
    acquired = threading.Event()
    released = threading.Event()
    second_done = threading.Event()

    def first() -> None:
        with storage.acquire_renew_lock(timeout=0.5):
            acquired.set()
            time.sleep(0.2)
        released.set()

    def second() -> None:
        acquired.wait()
        with storage.acquire_renew_lock(timeout=2.0):
            second_done.set()

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert released.is_set()
    assert second_done.is_set()


def test_write_cert_and_key_creates_parents(tmp_path: Path) -> None:
    cert_path = tmp_path / "nested" / "cert.pem"
    key_path = tmp_path / "nested" / "key.pem"
    storage.write_cert_and_key(
        cert_path=cert_path,
        key_path=key_path,
        cert_pem=b"-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n",
        key_pem=b"-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
    )
    assert cert_path.exists()
    assert key_path.exists()
    assert (key_path.stat().st_mode & 0o777) == 0o600


# -- public_ip ------------------------------------------------------------


def test_public_ip_discover_parses_synthetic_body() -> None:
    body = (
        "fl=1f2\n"
        "h=1.1.1.1\n"
        "ip=203.0.113.45\n"
        "ts=1234567890.123\n"
        "visit_scheme=https\n"
    )
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.text = body
    fake_response.raise_for_status = MagicMock()

    with patch("recon_gen._dev.tls.public_ip.requests.get", return_value=fake_response) as get_mock:
        ip = public_ip.discover()

    assert ip == "203.0.113.45"
    get_mock.assert_called_once()
    call_args = get_mock.call_args
    assert call_args.args[0] == public_ip._TRACE_URL


def test_public_ip_discover_handles_crlf() -> None:
    body = "fl=1f2\r\nip=198.51.100.7\r\nts=1\r\n"
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.text = body
    fake_response.raise_for_status = MagicMock()
    with patch("recon_gen._dev.tls.public_ip.requests.get", return_value=fake_response):
        assert public_ip.discover() == "198.51.100.7"


def test_public_ip_discover_raises_on_missing_ip_line() -> None:
    body = "fl=1f2\nh=1.1.1.1\nts=1234567890.123\n"
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.text = body
    fake_response.raise_for_status = MagicMock()
    with patch("recon_gen._dev.tls.public_ip.requests.get", return_value=fake_response):
        with pytest.raises(RuntimeError, match="ip="):
            public_ip.discover()


def test_public_ip_discover_retries_on_transient_error() -> None:
    """Three transient ConnectionErrors, then a success, all retried within budget."""
    successful = MagicMock()
    successful.status_code = 200
    successful.text = "ip=192.0.2.1\n"
    successful.raise_for_status = MagicMock()

    call_count = {"n": 0}

    def fake_get(*args: Any, **kwargs: Any) -> Any:
        call_count["n"] += 1
        if call_count["n"] < 3:
            import requests
            raise requests.ConnectionError("transient")
        return successful

    with patch("recon_gen._dev.tls.public_ip.requests.get", side_effect=fake_get):
        with patch("recon_gen._dev.tls.public_ip.time.sleep"):
            assert public_ip.discover() == "192.0.2.1"
    assert call_count["n"] == 3


def test_public_ip_discover_gives_up_after_retries() -> None:
    """Persistent failure raises RuntimeError after exhausting retries."""
    def fake_get(*args: Any, **kwargs: Any) -> Any:
        import requests
        raise requests.ConnectionError("down")

    with patch("recon_gen._dev.tls.public_ip.requests.get", side_effect=fake_get):
        with patch("recon_gen._dev.tls.public_ip.time.sleep"):
            with pytest.raises(RuntimeError, match="cloudflare_trace"):
                public_ip.discover()


# -- cloudflare_api -------------------------------------------------------


def _ok(json_body: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


def test_get_zone_id_returns_id_from_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECON_GEN_CLOUDFLARE_TOKEN", "tok")  # typing-smell: ignore[envvar-bypass]: test setup needs raw setenv to drive RECON_GEN_CLOUDFLARE_TOKEN.get_or_none()
    client = cloudflare_api.CloudflareClient(token="tok")
    response = _ok({
        "success": True,
        "result": [{"id": "zone-abc", "name": "hotchkiss.io"}],
    })
    with patch("recon_gen._dev.tls.cloudflare_api.requests.get", return_value=response) as get_mock:
        zone = client.get_zone_id("hotchkiss.io")
    assert zone == "zone-abc"
    call_args = get_mock.call_args
    assert "zones" in call_args.args[0]
    assert call_args.kwargs["params"] == {"name": "hotchkiss.io"}
    assert call_args.kwargs["headers"]["Authorization"] == "Bearer tok"


def test_get_zone_id_uses_cache_after_first_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECON_GEN_CLOUDFLARE_TOKEN", "tok")  # typing-smell: ignore[envvar-bypass]: test setup needs raw setenv to drive RECON_GEN_CLOUDFLARE_TOKEN.get_or_none()
    client = cloudflare_api.CloudflareClient(token="tok")
    response = _ok({
        "success": True,
        "result": [{"id": "zone-xyz", "name": "hotchkiss.io"}],
    })
    with patch("recon_gen._dev.tls.cloudflare_api.requests.get", return_value=response) as get_mock:
        first = client.get_zone_id("hotchkiss.io")
        second = client.get_zone_id("hotchkiss.io")
    assert first == second == "zone-xyz"
    # First call hit the API, second came from the on-disk cache.
    assert get_mock.call_count == 1


def test_get_zone_id_raises_on_unknown_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECON_GEN_CLOUDFLARE_TOKEN", "tok")  # typing-smell: ignore[envvar-bypass]: test setup needs raw setenv to drive RECON_GEN_CLOUDFLARE_TOKEN.get_or_none()
    client = cloudflare_api.CloudflareClient(token="tok")
    response = _ok({"success": True, "result": []})
    with patch("recon_gen._dev.tls.cloudflare_api.requests.get", return_value=response):
        with pytest.raises(RuntimeError, match="zone"):
            client.get_zone_id("unknown.example.com")


def test_reconcile_a_record_noop_when_matching() -> None:
    client = cloudflare_api.CloudflareClient(token="tok")
    # Force the zone cache so we don't need a second mock.
    storage.write_cached_zone_id("zone-1")
    list_response = _ok({
        "success": True,
        "result": [{
            "id": "rec-1",
            "type": "A",
            "name": "localdev.recon-gen.hotchkiss.io",
            "content": "127.0.0.1",
        }],
    })
    with patch("recon_gen._dev.tls.cloudflare_api.requests.get", return_value=list_response) as get_mock:
        with patch("recon_gen._dev.tls.cloudflare_api.requests.patch") as patch_mock:
            with patch("recon_gen._dev.tls.cloudflare_api.requests.post") as post_mock:
                result = client.reconcile_a_record(
                    hostname="localdev.recon-gen.hotchkiss.io",
                    target_ip="127.0.0.1",
                )
    assert result == "noop"
    patch_mock.assert_not_called()
    post_mock.assert_not_called()
    # Should fetch the existing records once.
    assert get_mock.call_count >= 1


def test_reconcile_a_record_patches_on_drift() -> None:
    client = cloudflare_api.CloudflareClient(token="tok")
    storage.write_cached_zone_id("zone-1")
    list_response = _ok({
        "success": True,
        "result": [{
            "id": "rec-1",
            "type": "A",
            "name": "dev.recon-gen.hotchkiss.io",
            "content": "203.0.113.1",
        }],
    })
    patch_response = _ok({"success": True, "result": {"id": "rec-1"}})
    with patch("recon_gen._dev.tls.cloudflare_api.requests.get", return_value=list_response):
        with patch("recon_gen._dev.tls.cloudflare_api.requests.patch", return_value=patch_response) as patch_mock:
            with patch("recon_gen._dev.tls.cloudflare_api.requests.post") as post_mock:
                result = client.reconcile_a_record(
                    hostname="dev.recon-gen.hotchkiss.io",
                    target_ip="203.0.113.99",
                )
    assert result == "patched"
    patch_mock.assert_called_once()
    patch_call = patch_mock.call_args
    assert patch_call.kwargs["json"]["content"] == "203.0.113.99"
    post_mock.assert_not_called()


def test_reconcile_a_record_creates_when_absent() -> None:
    client = cloudflare_api.CloudflareClient(token="tok")
    storage.write_cached_zone_id("zone-1")
    list_response = _ok({"success": True, "result": []})
    post_response = _ok({"success": True, "result": {"id": "rec-new"}})
    with patch("recon_gen._dev.tls.cloudflare_api.requests.get", return_value=list_response):
        with patch("recon_gen._dev.tls.cloudflare_api.requests.patch") as patch_mock:
            with patch("recon_gen._dev.tls.cloudflare_api.requests.post", return_value=post_response) as post_mock:
                result = client.reconcile_a_record(
                    hostname="ci.recon-gen.hotchkiss.io",
                    target_ip="198.51.100.5",
                )
    assert result == "created"
    post_mock.assert_called_once()
    post_call = post_mock.call_args
    body = post_call.kwargs["json"]
    assert body["type"] == "A"
    assert body["name"] == "ci.recon-gen.hotchkiss.io"
    assert body["content"] == "198.51.100.5"
    patch_mock.assert_not_called()


def test_put_txt_record_returns_id() -> None:
    client = cloudflare_api.CloudflareClient(token="tok")
    storage.write_cached_zone_id("zone-1")
    post_response = _ok({"success": True, "result": {"id": "txt-rec-1"}})
    with patch("recon_gen._dev.tls.cloudflare_api.requests.post", return_value=post_response) as post_mock:
        rec_id = client.put_txt_record(
            hostname="_acme-challenge.localdev.recon-gen.hotchkiss.io",
            token_value="challenge-token-xyz",
        )
    assert rec_id == "txt-rec-1"
    post_call = post_mock.call_args
    body = post_call.kwargs["json"]
    assert body["type"] == "TXT"
    assert body["name"] == "_acme-challenge.localdev.recon-gen.hotchkiss.io"
    assert body["content"] == "challenge-token-xyz"


def test_delete_dns_record_calls_delete() -> None:
    client = cloudflare_api.CloudflareClient(token="tok")
    storage.write_cached_zone_id("zone-1")
    response = _ok({"success": True, "result": {"id": "rec-x"}})
    with patch("recon_gen._dev.tls.cloudflare_api.requests.delete", return_value=response) as del_mock:
        client.delete_dns_record("rec-x")
    del_call = del_mock.call_args
    assert "rec-x" in del_call.args[0]
    assert del_call.kwargs["headers"]["Authorization"] == "Bearer tok"


# -- ensure_dev_env orchestration -----------------------------------------


def test_ensure_dev_env_raises_when_token_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RECON_GEN_CLOUDFLARE_TOKEN", raising=False)  # typing-smell: ignore[envvar-bypass]: test setup needs raw delenv to drive RECON_GEN_CLOUDFLARE_TOKEN.get_or_none()
    with pytest.raises(ValueError, match="RECON_GEN_CLOUDFLARE_TOKEN"):
        ensure_dev_env(
            Env.DEV,
            cert_path=tmp_path / "cert.pem",
            key_path=tmp_path / "key.pem",
            account_email="ops@example.com",
        )


def test_ensure_dev_env_idempotent_when_everything_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All A records current + cert covers both SANs + cert >=30d valid →
    no PATCHes, no ACME mint, no cert write."""
    monkeypatch.setenv("RECON_GEN_CLOUDFLARE_TOKEN", "tok")  # typing-smell: ignore[envvar-bypass]: test setup needs raw setenv to drive RECON_GEN_CLOUDFLARE_TOKEN.get_or_none()

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    sans = list(_HOSTS_BY_ENV[Env.DEV])
    cert_pem, key_pem = _mint_self_signed(
        sans=sans,
        not_after=dt.datetime.now(dt.UTC) + dt.timedelta(days=90),
    )
    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(key_pem)
    original_cert_bytes = cert_path.read_bytes()

    fake_client = MagicMock(spec=cloudflare_api.CloudflareClient)
    fake_client.reconcile_a_record.return_value = "noop"

    with patch.object(ensure_mod, "_make_cloudflare_client", return_value=fake_client):
        with patch.object(ensure_mod, "discover_public_ip", return_value="203.0.113.50"):
            with patch.object(ensure_mod, "run_acme_dns01") as acme_mock:
                ensure_dev_env(
                    Env.DEV,
                    cert_path=cert_path,
                    key_path=key_path,
                    account_email="ops@example.com",
                )

    # Reconciled both records (2 hostnames), no ACME.
    assert fake_client.reconcile_a_record.call_count == 2
    # All reconciles came back as no-op (the mock's return_value).
    acme_mock.assert_not_called()
    # Cert file untouched.
    assert cert_path.read_bytes() == original_cert_bytes


def test_ensure_dev_env_runs_acme_when_cert_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECON_GEN_CLOUDFLARE_TOKEN", "tok")  # typing-smell: ignore[envvar-bypass]: test setup needs raw setenv to drive RECON_GEN_CLOUDFLARE_TOKEN.get_or_none()
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    fake_client = MagicMock(spec=cloudflare_api.CloudflareClient)
    fake_client.reconcile_a_record.return_value = "noop"

    sans = list(_HOSTS_BY_ENV[Env.DEV])
    minted_cert, minted_key = _mint_self_signed(
        sans=sans,
        not_after=dt.datetime.now(dt.UTC) + dt.timedelta(days=90),
    )

    with patch.object(ensure_mod, "_make_cloudflare_client", return_value=fake_client):
        with patch.object(ensure_mod, "discover_public_ip", return_value="203.0.113.50"):
            with patch.object(
                ensure_mod, "run_acme_dns01",
                return_value=(minted_cert, minted_key),
            ) as acme_mock:
                ensure_dev_env(
                    Env.DEV,
                    cert_path=cert_path,
                    key_path=key_path,
                    account_email="ops@example.com",
                )

    acme_mock.assert_called_once()
    # SAN set matches the env.
    acme_call = acme_mock.call_args
    assert set(acme_call.kwargs["sans"]) == set(sans)
    assert acme_call.kwargs["account_email"] == "ops@example.com"
    # Cert + key written to caller paths.
    assert cert_path.read_bytes() == minted_cert
    assert key_path.read_bytes() == minted_key


def test_ensure_dev_env_runs_acme_when_cert_expiring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cert exists + covers SANs but expires in <30d → re-mint."""
    monkeypatch.setenv("RECON_GEN_CLOUDFLARE_TOKEN", "tok")  # typing-smell: ignore[envvar-bypass]: test setup needs raw setenv to drive RECON_GEN_CLOUDFLARE_TOKEN.get_or_none()
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    sans = list(_HOSTS_BY_ENV[Env.DEV])
    near_expiry_cert, _ = _mint_self_signed(
        sans=sans,
        not_after=dt.datetime.now(dt.UTC) + dt.timedelta(days=10),
    )
    cert_path.write_bytes(near_expiry_cert)

    fake_client = MagicMock(spec=cloudflare_api.CloudflareClient)
    fake_client.reconcile_a_record.return_value = "noop"
    fresh_cert, fresh_key = _mint_self_signed(
        sans=sans,
        not_after=dt.datetime.now(dt.UTC) + dt.timedelta(days=90),
    )

    with patch.object(ensure_mod, "_make_cloudflare_client", return_value=fake_client):
        with patch.object(ensure_mod, "discover_public_ip", return_value="203.0.113.50"):
            with patch.object(
                ensure_mod, "run_acme_dns01",
                return_value=(fresh_cert, fresh_key),
            ) as acme_mock:
                ensure_dev_env(
                    Env.DEV,
                    cert_path=cert_path,
                    key_path=key_path,
                    account_email="ops@example.com",
                )
    acme_mock.assert_called_once()
    assert cert_path.read_bytes() == fresh_cert


def test_ensure_dev_env_runs_acme_when_sans_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cert is valid but missing one SAN → re-mint."""
    monkeypatch.setenv("RECON_GEN_CLOUDFLARE_TOKEN", "tok")  # typing-smell: ignore[envvar-bypass]: test setup needs raw setenv to drive RECON_GEN_CLOUDFLARE_TOKEN.get_or_none()
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    # Only one of the two locked SANs.
    only_one, _ = _mint_self_signed(
        sans=[_HOSTS_BY_ENV[Env.DEV][0]],
        not_after=dt.datetime.now(dt.UTC) + dt.timedelta(days=90),
    )
    cert_path.write_bytes(only_one)

    fake_client = MagicMock(spec=cloudflare_api.CloudflareClient)
    fake_client.reconcile_a_record.return_value = "noop"
    fresh_cert, fresh_key = _mint_self_signed(
        sans=list(_HOSTS_BY_ENV[Env.DEV]),
        not_after=dt.datetime.now(dt.UTC) + dt.timedelta(days=90),
    )

    with patch.object(ensure_mod, "_make_cloudflare_client", return_value=fake_client):
        with patch.object(ensure_mod, "discover_public_ip", return_value="203.0.113.50"):
            with patch.object(
                ensure_mod, "run_acme_dns01",
                return_value=(fresh_cert, fresh_key),
            ) as acme_mock:
                ensure_dev_env(
                    Env.DEV,
                    cert_path=cert_path,
                    key_path=key_path,
                    account_email="ops@example.com",
                )
    acme_mock.assert_called_once()


def test_ensure_dev_env_reconciles_dynamic_a_record_to_public_ip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECON_GEN_CLOUDFLARE_TOKEN", "tok")  # typing-smell: ignore[envvar-bypass]: test setup needs raw setenv to drive RECON_GEN_CLOUDFLARE_TOKEN.get_or_none()
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    sans = list(_HOSTS_BY_ENV[Env.CI])
    cert_pem, key_pem = _mint_self_signed(
        sans=sans,
        not_after=dt.datetime.now(dt.UTC) + dt.timedelta(days=90),
    )
    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(key_pem)

    fake_client = MagicMock(spec=cloudflare_api.CloudflareClient)
    fake_client.reconcile_a_record.return_value = "noop"

    with patch.object(ensure_mod, "_make_cloudflare_client", return_value=fake_client):
        with patch.object(ensure_mod, "discover_public_ip", return_value="198.51.100.77") as ip_mock:
            with patch.object(ensure_mod, "run_acme_dns01"):
                ensure_dev_env(
                    Env.CI,
                    cert_path=cert_path,
                    key_path=key_path,
                    account_email="ops@example.com",
                )
    ip_mock.assert_called_once()
    # CI's static (localci) and dynamic (ci) reconciled.
    calls = {
        c.kwargs["hostname"]: c.kwargs["target_ip"]
        for c in fake_client.reconcile_a_record.call_args_list
    }
    assert calls["localci.recon-gen.hotchkiss.io"] == "127.0.0.1"
    assert calls["ci.recon-gen.hotchkiss.io"] == "198.51.100.77"


def test_ensure_dev_env_self_signed_short_circuit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EA.2 — with ``RECON_GEN_TLS_SELF_SIGNED`` set, ``ensure_dev_env``
    mints a self-signed cert for the env's locked SANs and touches NEITHER
    Cloudflare NOR ACME NOR public-IP discovery — and needs no
    ``RECON_GEN_CLOUDFLARE_TOKEN`` (the short-circuit is above the token
    check). This is the cloud-spike path (a runner with no DNS control)."""
    monkeypatch.setenv("RECON_GEN_TLS_SELF_SIGNED", "1")  # typing-smell: ignore[envvar-bypass]: test drives RECON_GEN_TLS_SELF_SIGNED.get_or_none()
    # Deliberately NO RECON_GEN_CLOUDFLARE_TOKEN — the short-circuit must
    # not require it.
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    with patch.object(ensure_mod, "_make_cloudflare_client") as cf_mock:
        with patch.object(ensure_mod, "discover_public_ip") as ip_mock:
            with patch.object(ensure_mod, "run_acme_dns01") as acme_mock:
                ensure_dev_env(
                    Env.CI,
                    cert_path=cert_path,
                    key_path=key_path,
                    account_email="spike@localhost",
                )

    cf_mock.assert_not_called()
    ip_mock.assert_not_called()
    acme_mock.assert_not_called()

    # A real self-signed cert landed, covering the CI SANs, self-issued.
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    assert key_path.exists()
    assert cert.issuer == cert.subject
    san = cert.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value
    assert set(san.get_values_for_type(x509.DNSName)) == set(_HOSTS_BY_ENV[Env.CI])


def test_ensure_dev_env_self_signed_reuses_valid_cert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EA.2 — the self-signed branch is idempotent: a pre-placed cert that
    already covers the SANs + has validity left is NOT re-minted (so the
    spike's pre-gen step + the runner's own call don't fight)."""
    monkeypatch.setenv("RECON_GEN_TLS_SELF_SIGNED", "1")  # typing-smell: ignore[envvar-bypass]: test drives RECON_GEN_TLS_SELF_SIGNED.get_or_none()
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    sans = list(_HOSTS_BY_ENV[Env.CI])
    cert_pem, key_pem = _mint_self_signed(
        sans=sans, not_after=dt.datetime.now(dt.UTC) + dt.timedelta(days=90),
    )
    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(key_pem)

    ensure_dev_env(
        Env.CI, cert_path=cert_path, key_path=key_path,
        account_email="spike@localhost",
    )

    # Untouched — same bytes (no re-mint).
    assert cert_path.read_bytes() == cert_pem
