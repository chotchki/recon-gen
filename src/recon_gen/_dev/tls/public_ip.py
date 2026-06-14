"""DC.2 — public IP discovery via the ``cloudflare_trace`` pattern.

Port of the [hotchkiss-io coordinator's Rust impl](
https://github.com/chotchki/hotchkiss-io/blob/main/src/coordinator/ip/cloudflare_trace.rs):
GET ``https://1.1.1.1/cdn-cgi/trace``, parse the text body line by
line, return the ``ip=<value>`` field. ~30 LoC end to end.

Why this endpoint:
  * Cloudflare's anycast 1.1.1.1 is generally reachable from
    anywhere a dev box can reach the internet.
  * ``/cdn-cgi/trace`` is a plain-text key=value payload — no JSON
    parsing, no auth needed.
  * The pattern matches the operator's existing personal-site
    coordinator, so we share operational shape with infra they
    already debug.

Retry policy: 3 transient retries with linear back-off (1s, 2s, 3s).
4xx errors short-circuit (token / config bug, not transient). 5xx +
ConnectionError + Timeout retry.
"""

from __future__ import annotations

import time

import requests


_TRACE_URL = "https://1.1.1.1/cdn-cgi/trace"
_REQUEST_TIMEOUT_SECONDS = 5.0
_MAX_RETRIES = 3


def discover() -> str:
    """Return the caller's public IP per Cloudflare's trace endpoint.

    Raises:
        RuntimeError: on transport failure after exhausting retries,
            HTTP non-2xx, or missing/empty ``ip=`` field.
    """
    last_err: BaseException | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.get(_TRACE_URL, timeout=_REQUEST_TIMEOUT_SECONDS)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_err = exc
            if attempt < _MAX_RETRIES:
                time.sleep(float(attempt))
                continue
            raise RuntimeError(
                f"cloudflare_trace request failed after "
                f"{_MAX_RETRIES} attempts: {exc}"
            ) from exc

        if 500 <= resp.status_code < 600:
            last_err = RuntimeError(
                f"cloudflare_trace returned HTTP {resp.status_code}"
            )
            if attempt < _MAX_RETRIES:
                time.sleep(float(attempt))
                continue
            raise last_err

        if resp.status_code != 200:
            raise RuntimeError(
                f"cloudflare_trace returned HTTP {resp.status_code} "
                f"(non-retryable; check network / firewall)"
            )

        return _parse_trace_body(resp.text)

    # Unreachable — loop above either returns or raises.
    raise RuntimeError(
        f"cloudflare_trace failed unexpectedly: {last_err!r}"
    )


def _parse_trace_body(body: str) -> str:
    """Parse ``key=value`` lines; return the ``ip`` value.

    Strips CR (``\r``) so we tolerate the Cloudflare CDN occasionally
    serving CRLF-terminated bodies.
    """
    for raw_line in body.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        key, sep, value = line.partition("=")
        if sep == "" or not value:
            continue
        if key.strip() == "ip":
            return value.strip()
    raise RuntimeError(
        "cloudflare_trace body missing 'ip=' line; "
        "Cloudflare may have changed the trace format. "
        f"Body (first 200 chars): {body[:200]!r}"
    )
