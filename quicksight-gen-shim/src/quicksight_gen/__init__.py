"""AC.F.1 — deprecation shim for the `quicksight-gen` → `recon-gen` rename.

This package has no code. It exists only to keep
``pip install quicksight-gen`` working transparently for 1-2 months
after v11.0.0 (AC.locked drop-timeline). The single side-effect
below fires once on import: a ``DeprecationWarning`` pointing the
operator at the new install name + import path.

No re-exports of `recon_gen` are provided. The shim handles the
*install path* only — code that does ``import quicksight_gen``
still fails after this warning, by design: operators must update
their imports to ``recon_gen`` as part of the migration.
"""
import warnings

warnings.warn(
    "The 'quicksight-gen' package is renamed to 'recon-gen'. "
    "This shim will be removed ~1-2 months after v11.0.0 publish "
    "(see https://github.com/chotchki/recon-gen for the exact drop "
    "date in v11.0.0 RELEASE_NOTES). "
    "Run 'pip install recon-gen' (or 'uv add recon-gen') and update "
    "your imports from 'quicksight_gen' to 'recon_gen'.",
    DeprecationWarning,
    stacklevel=2,
)
