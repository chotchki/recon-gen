"""DD.4 — runner-internal Dex container coordinator.

Lives under ``_dev/`` (excluded from the published wheel; see
``pyproject.toml::tool.setuptools.packages.find.exclude``). Provides
``ensure_dev_idp`` — an idempotent entry point that spins/adopts a Dex
Docker container with scrambled-per-run credentials, mounts the
DC.3 LE cert + key, and returns the OIDC issuer URL.

Downstream operators bring their own IdP (Okta, Entra) and configure
``cfg.auth.oidc.issuer_url`` accordingly; this coordinator is test-only.

Pattern-symmetry: mirrors ``recon_gen._dev.tls.ensure_dev_env`` exactly
— same Env enum shape, same caller contract, same ValueError /
RuntimeError split for EXIT_NEEDS_OPERATOR routing.
"""

from __future__ import annotations

from recon_gen._dev.oidc.ensure import Env, ensure_dev_idp

__all__ = ["Env", "ensure_dev_idp"]
