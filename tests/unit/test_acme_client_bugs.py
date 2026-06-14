"""DC.5 live-verify bug fixes — regression pins.

Three failure modes surfaced when ``ensure_dev_env`` first hit live
Let's Encrypt / Cloudflare against the operator's Mac:

  1. ``acme_client._deadline`` built an offset-aware datetime, but the
     acme library's ``poll_authorizations`` compares against
     ``datetime.datetime.now()`` (naive). The mismatch raised
     ``TypeError: can't compare offset-naive and offset-aware datetimes``
     mid-poll, after the cert order was already placed.
  2. ``_register_account`` caught ``messages.Error`` for the
     "account already exists" benign re-run case, but the acme
     library raises ``acme.errors.ConflictError`` instead. The
     ConflictError propagated past the handler and aborted the run.
  3. After catching the ConflictError, simply swallowing it left the
     client's ``net.account`` unset — the next JWS-signed request
     failed with "Unable to validate JWS :: No Key ID in JWS header".
     Recovery: reconstruct a ``RegistrationResource`` from the
     ConflictError's ``location`` URL and assign it to ``net.account``.

These tests pin the three fixes so the bugs can't regress silently.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest
from acme import errors as acme_errors, messages

from recon_gen._dev.tls.acme_client import _deadline, _register_account


def test_deadline_is_naive_datetime() -> None:
    """``_deadline`` must return a NAIVE datetime; offset-aware breaks
    the acme library's ``while datetime.now() < deadline`` poll."""
    d = _deadline(60)
    assert d.tzinfo is None, (
        "acme.client.poll_authorizations compares against naive "
        "datetime.now() — handing it an aware deadline raises TypeError"
    )
    # Sanity: it's in the future, by approximately the right amount.
    delta = (d - dt.datetime.now()).total_seconds()
    assert 50 < delta < 70


def test_register_account_handles_conflict_error_with_account_reload() -> None:
    """ConflictError → reconstruct RegistrationResource from
    location URL + assign to client.net.account.

    Without the assignment, downstream JWS signing fails with "No Key
    ID in JWS header" because the client doesn't know its own account.
    """
    fake_client = MagicMock()
    # Simulate "account already exists" — LE returns 200 with a
    # Location header, the acme library raises ConflictError.
    fake_client.new_account.side_effect = acme_errors.ConflictError(
        "https://acme-v02.api.letsencrypt.org/acme/acct/12345"
    )

    _register_account(fake_client, email="ops@example.com")

    # The recovery path must have set net.account to a RegistrationResource
    # whose uri matches the location from the ConflictError.
    assigned = fake_client.net.account
    assert isinstance(assigned, messages.RegistrationResource)
    assert (
        assigned.uri
        == "https://acme-v02.api.letsencrypt.org/acme/acct/12345"
    )


def test_register_account_happy_path_does_not_touch_net_account() -> None:
    """On a fresh registration (no conflict), the acme library itself
    sets ``net.account`` from the response. We must NOT overwrite it
    on the happy path."""
    fake_client = MagicMock()
    # No exception — successful registration; the library handles
    # net.account internally.
    fake_client.new_account.return_value = MagicMock()
    # Pre-stamp net.account with a sentinel so we can assert it's left alone.
    sentinel = object()
    fake_client.net.account = sentinel

    _register_account(fake_client, email="ops@example.com")

    assert fake_client.net.account is sentinel, (
        "happy path overwrote net.account; should only reconstruct on "
        "ConflictError"
    )


def test_register_account_messages_error_without_location_raises_actionable(
) -> None:
    """The older problem-doc shape (``messages.Error`` with
    accountAlreadyExists) doesn't carry a location URL — we can't
    recover. Raise with an actionable message instead of silently
    leaving the client in a broken state."""
    fake_client = MagicMock()

    class _FakeError(Exception):
        def __str__(self) -> str:
            return (
                "urn:ietf:params:acme:error:accountAlreadyExists :: "
                "Account key already in use"
            )

    fake_err = _FakeError()
    # Mock isinstance check against messages.Error
    with patch(
        "recon_gen._dev.tls.acme_client.messages",
        Error=type(fake_err),
    ):
        fake_client.new_account.side_effect = fake_err
        with pytest.raises(RuntimeError, match="accountAlreadyExists"):
            _register_account(fake_client, email="ops@example.com")
