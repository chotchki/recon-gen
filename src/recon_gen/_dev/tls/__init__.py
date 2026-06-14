"""DC.2 — runner-internal ACME + Cloudflare DNS-01 coordinator.

Lives under ``_dev/`` (excluded from the published wheel; see
``pyproject.toml::tool.setuptools.packages.find.exclude``). Provides
``ensure_dev_env`` — an idempotent, advisory-locked entry point that
reconciles the 4 managed A records under ``hotchkiss.io`` and (re)mints
a single SAN cert per environment via ACME DNS-01 against Let's
Encrypt.

The locked design lives at ``docs/audits/dc_0_https_spike.md``.
Downstream ``recon-gen.exe`` operators see none of this — they
supply ``cfg.app2.tls.{cert_path,key_path}`` and own their renewal
externally.
"""

from __future__ import annotations

from recon_gen._dev.tls.ensure import Env, ensure_dev_env

__all__ = ["Env", "ensure_dev_env"]
