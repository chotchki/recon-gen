"""DC.2 — Cloudflare REST client (zone discovery + DNS record CRUD).

The 4 verbs the rest of the coordinator needs:

* ``get_zone_id(name)`` — zone lookup by name; cached on disk after
  first call (`zone-id.txt` under the XDG state dir).
* ``reconcile_a_record(hostname, target_ip)`` — list existing A
  records under the zone; PATCH if the value drifts, POST if absent,
  no-op if equal.
* ``put_txt_record(hostname, token_value)`` — create the
  ``_acme-challenge.<host>`` TXT record needed for DNS-01; returns
  record id so the caller can DELETE after the challenge clears.
* ``delete_dns_record(record_id)`` — cleanup verb for the ACME
  challenge TXTs.

Auth: ``Authorization: Bearer <token>`` from
``RECON_GEN_CLOUDFLARE_TOKEN``. Token wants ``Zone:DNS:Edit`` on
``hotchkiss.io`` only (locked default; see spike §"Operator-confirm
questions" item 1).
"""

from __future__ import annotations

from typing import Any, Literal

import requests

from recon_gen._dev.tls import storage


_API_BASE = "https://api.cloudflare.com/client/v4"
_HTTP_TIMEOUT = 15.0


ReconcileResult = Literal["noop", "patched", "created"]


class CloudflareClient:
    """Thin wrapper over the Cloudflare REST surface.

    Single zone per process (`hotchkiss.io` per the spike); zone ID
    discovered lazily on first request and cached on disk so the next
    process boot skips the lookup.
    """

    def __init__(self, *, token: str, zone_name: str = "hotchkiss.io") -> None:
        self._token = token
        self._zone_name = zone_name
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    # -- zone discovery ----------------------------------------------------

    def get_zone_id(self, name: str | None = None) -> str:
        """Return the Cloudflare zone ID for ``name`` (default zone).

        Cached after first lookup at the XDG ``zone-id.txt``. Raises
        ``RuntimeError`` if the zone isn't visible to this token.
        """
        target = name or self._zone_name
        cached = storage.read_cached_zone_id()
        if cached:
            return cached
        url = f"{_API_BASE}/zones"
        resp = requests.get(
            url, headers=self._headers,
            params={"name": target}, timeout=_HTTP_TIMEOUT,
        )
        body = _raise_for_status(resp, ctx=f"GET /zones?name={target}")
        raw_results: list[Any] = list(body.get("result") or [])
        results: list[dict[str, Any]] = [dict(r) for r in raw_results]
        if not results:
            raise RuntimeError(
                f"Cloudflare zone {target!r} not visible to this token "
                f"(check token scope and account)"
            )
        zone_id = str(results[0]["id"])
        storage.write_cached_zone_id(zone_id)
        return zone_id

    # -- A record reconcile -----------------------------------------------

    def reconcile_a_record(
        self, *, hostname: str, target_ip: str,
    ) -> ReconcileResult:
        """Bring the ``A`` record for ``hostname`` to ``target_ip``.

        Returns one of:
          * ``"noop"`` — record already matches.
          * ``"patched"`` — record existed at a different value, PATCHed.
          * ``"created"`` — no record present, POSTed a new one.
        """
        zone_id = self.get_zone_id()
        existing = self._list_records(
            zone_id=zone_id, hostname=hostname, record_type="A",
        )
        if not existing:
            self._create_record(
                zone_id=zone_id,
                record_type="A",
                name=hostname,
                content=target_ip,
                ttl=1,  # "automatic" per Cloudflare convention
            )
            return "created"
        record = existing[0]
        if record["content"] == target_ip:
            return "noop"
        self._patch_record(
            zone_id=zone_id,
            record_id=str(record["id"]),
            content=target_ip,
        )
        return "patched"

    # -- TXT record CRUD (for ACME DNS-01) --------------------------------

    def put_txt_record(self, *, hostname: str, token_value: str) -> str:
        """Create a TXT record at ``hostname`` with value ``token_value``.

        Always POSTs (we don't reuse pre-existing TXTs — ACME tokens
        are single-use). Returns the new record's id so the caller can
        DELETE after the challenge clears.
        """
        zone_id = self.get_zone_id()
        return self._create_record(
            zone_id=zone_id,
            record_type="TXT",
            name=hostname,
            content=token_value,
            ttl=60,
        )

    def delete_dns_record(self, record_id: str) -> None:
        """DELETE ``record_id`` under the zone. Idempotent on 404."""
        zone_id = self.get_zone_id()
        url = f"{_API_BASE}/zones/{zone_id}/dns_records/{record_id}"
        resp = requests.delete(
            url, headers=self._headers, timeout=_HTTP_TIMEOUT,
        )
        # 404 is fine — retry safety.
        if resp.status_code == 404:
            return
        _raise_for_status(resp, ctx=f"DELETE {url}")

    # -- internals ---------------------------------------------------------

    def _list_records(
        self, *, zone_id: str, hostname: str, record_type: str,
    ) -> list[dict[str, Any]]:
        url = f"{_API_BASE}/zones/{zone_id}/dns_records"
        resp = requests.get(
            url, headers=self._headers,
            params={"name": hostname, "type": record_type},
            timeout=_HTTP_TIMEOUT,
        )
        body = _raise_for_status(
            resp, ctx=f"GET /dns_records?name={hostname}",
        )
        # Cloudflare's JSON body is dynamically typed (Any) — narrow to the
        # caller-facing shape at the API boundary.
        raw_results: list[Any] = list(body.get("result") or [])
        return [dict(r) for r in raw_results]

    def _create_record(
        self,
        *,
        zone_id: str,
        record_type: str,
        name: str,
        content: str,
        ttl: int,
    ) -> str:
        url = f"{_API_BASE}/zones/{zone_id}/dns_records"
        payload = {
            "type": record_type,
            "name": name,
            "content": content,
            "ttl": ttl,
        }
        # Cloudflare requires explicit "proxied: false" on A records
        # to keep the orange-cloud off — A records used as origin
        # targets for QS (us-east-1) must point straight at the IP.
        if record_type == "A":
            payload["proxied"] = False
        resp = requests.post(
            url, headers=self._headers, json=payload,
            timeout=_HTTP_TIMEOUT,
        )
        body = _raise_for_status(
            resp, ctx=f"POST /dns_records {record_type} {name}",
        )
        raw_result: dict[str, Any] = dict(body.get("result") or {})
        return str(raw_result["id"])

    def _patch_record(
        self, *, zone_id: str, record_id: str, content: str,
    ) -> None:
        url = f"{_API_BASE}/zones/{zone_id}/dns_records/{record_id}"
        resp = requests.patch(
            url, headers=self._headers,
            json={"content": content}, timeout=_HTTP_TIMEOUT,
        )
        _raise_for_status(resp, ctx=f"PATCH {url}")


def _raise_for_status(resp: requests.Response, *, ctx: str) -> dict[str, Any]:
    """Raise ``RuntimeError`` on HTTP non-2xx; return parsed JSON body."""
    if resp.status_code >= 400:
        try:
            err_body = resp.json()
        except (ValueError, requests.JSONDecodeError):
            err_body = {"text": resp.text[:500]}
        raise RuntimeError(
            f"Cloudflare API {ctx} failed: "
            f"HTTP {resp.status_code} body={err_body!r}"
        )
    try:
        return dict(resp.json())
    except (ValueError, requests.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Cloudflare API {ctx} returned non-JSON body: {resp.text[:500]!r}"
        ) from exc
